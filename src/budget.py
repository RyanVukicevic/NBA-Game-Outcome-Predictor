"""Conservative shared quota guard for all local odds requests."""
import pandas as pd

from tracking import encoded, utc

DAILY_CAP = 12
CYCLE_CAP = 450
RESERVE = 50


class BudgetError(ValueError):
    pass


def next_month(as_of=None):
    now = pd.Timestamp(utc(as_of))
    return utc(pd.Timestamp(year=now.year + (now.month == 12), month=now.month % 12 + 1, day=1, tz="UTC"))


def configure(ledger, reset_at, used, as_of=None, calendar_monthly=False):
    now, reset = utc(as_of), utc(reset_at)
    if not isinstance(used, int) or not 0 <= used <= 500 or reset <= now:
        raise ValueError("Supply current used credits (0-500) and a future actual billing reset time.")
    if calendar_monthly and reset != next_month(now):
        raise ValueError("Calendar-month reset must be next month's first day at 00:00 UTC.")
    previous = ledger.rows("SELECT * FROM quota_cycles ORDER BY id DESC LIMIT 1")
    if previous and previous[0]["reset_at"] > now:
        raise BudgetError("Active cycle already configured; it cannot be reset early to bypass the cap.")
    with ledger.db:
        ledger.db.execute("INSERT INTO quota_cycles(started,reset_at,baseline_used) VALUES(?,?,?)", (now, reset, used))
        ledger.db.execute("INSERT OR REPLACE INTO quota_settings VALUES('calendar_monthly',?)", (str(int(calendar_monthly)),))


def status(ledger, as_of=None):
    now = utc(as_of)
    day_start = utc(pd.Timestamp(now).tz_convert("America/New_York").normalize())
    day_spent = ledger.db.execute("SELECT COALESCE(SUM(cost),0) FROM api_requests WHERE started>=? AND started<=?",
                                 (day_start, now)).fetchone()[0]
    cycles = ledger.rows("SELECT * FROM quota_cycles ORDER BY id DESC LIMIT 1")
    if not cycles:
        return dict(ready=False, reason="quota_not_configured", daily_left=0, cycle_left=0)
    cycle = cycles[0]
    monthly = ledger.rows("SELECT value FROM quota_settings WHERE name='calendar_monthly'")
    if now >= cycle["reset_at"] and monthly and monthly[0]["value"] == "1":
        return dict(ready=True, reason=None, cycle_id=None, reset_at=next_month(now), accounted_used=0,
                    daily_used=day_spent, daily_left=max(0, DAILY_CAP-day_spent), cycle_left=CYCLE_CAP)
    requests = ledger.rows("SELECT * FROM api_requests WHERE cycle_id=?", (cycle["id"],))
    spent = cycle["baseline_used"] + sum(r["cost"] for r in requests)
    left = min(CYCLE_CAP - spent, cycle["allowance"] - spent - RESERVE)
    return dict(ready=now < cycle["reset_at"], reason=None if now < cycle["reset_at"] else "confirm_new_billing_cycle",
                cycle_id=cycle["id"], reset_at=cycle["reset_at"], accounted_used=spent,
                daily_used=day_spent, daily_left=max(0, DAILY_CAP-day_spent), cycle_left=max(0, left))


def reserve(ledger, purpose, as_of=None, cost=1):
    now = utc(as_of)
    if not isinstance(cost, int) or cost <= 0:
        raise ValueError("Expected positive integer credit cost.")
    ledger.db.execute("BEGIN IMMEDIATE")
    try:
        info = status(ledger, now)
        if not info["ready"]:
            raise BudgetError(info["reason"])
        if cost > min(info["daily_left"], info["cycle_left"]):
            raise BudgetError("Daily or billing-cycle quota cap reached.")
        if info["cycle_id"] is None:
            cur = ledger.db.execute("INSERT INTO quota_cycles(started,reset_at,baseline_used) VALUES(?,?,0)", (now, info["reset_at"]))
            info["cycle_id"] = cur.lastrowid
        pending = ledger.rows("SELECT started FROM api_requests WHERE status='reserved'")
        if any(pd.Timestamp(now)-pd.Timestamp(r["started"]) < pd.Timedelta(minutes=2) for r in pending):
            raise BudgetError("Another odds request is in progress.")
        cur = ledger.db.execute("INSERT INTO api_requests(cycle_id,started,cost,status,purpose) VALUES(?,?,?,?,?)",
                                (info["cycle_id"], now, cost, "reserved", purpose))
        ledger.db.commit()
        return cur.lastrowid
    except Exception:
        ledger.db.rollback()
        raise


def finish(ledger, request_id, headers=None, success=False, as_of=None):
    safe = {key: str(value) for key, value in (headers or {}).items()
            if key.lower() in ("x-requests-used", "x-requests-remaining", "x-requests-last")}
    safe = {k.lower(): v for k, v in safe.items()}
    def number(key):
        try:
            n = int(safe[key])
            return n if n >= 0 else None
        except (KeyError, ValueError, TypeError):
            return None
    with ledger.db:
        ledger.db.execute("BEGIN IMMEDIATE")
        request = ledger.rows("SELECT * FROM api_requests WHERE id=?", (request_id,))[0]
        if request["status"] != "reserved":
            return
        actual_cost = number("x-requests-last")
        # Unknown outcomes (including timeouts/crashes) consume the reserved cost.
        cost = actual_cost if actual_cost is not None else request["cost"]
        ledger.db.execute("UPDATE api_requests SET finished=?,cost=?,status=?,headers=? WHERE id=?",
                          (utc(as_of), cost, "success" if success else "error", encoded(safe), request_id))
        cycle = ledger.rows("SELECT * FROM quota_cycles WHERE id=?", (request["cycle_id"],))[0]
        total = ledger.db.execute("SELECT SUM(cost) FROM api_requests WHERE cycle_id=?", (cycle["id"],)).fetchone()[0]
        used, remaining = number("x-requests-used"), number("x-requests-remaining")
        reported = max(used or 0, max(0, 500-remaining) if remaining is not None else 0)
        if reported > cycle["baseline_used"] + total:
            ledger.db.execute("UPDATE quota_cycles SET baseline_used=? WHERE id=?", (reported-total, cycle["id"]))
