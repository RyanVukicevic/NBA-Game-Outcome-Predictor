"""Event-driven, paper-only collection. Dry-run by default in the CLI."""
from dataclasses import replace
from pathlib import Path
import uuid

import pandas as pd

from budget import status as budget_status
from eligibility import EligibilityContext
from provenance import digest
from tracking import Policy, utc

HORIZONS = (60, 360)


def plan(ledger, model_id, as_of=None):
    now = utc(as_of)
    context = EligibilityContext(ledger, model_id, now)
    plans = []
    for gid, g in context.games.items():
        start = pd.Timestamp(g["tipoff"])
        if g["first_observed"] >= g["tipoff"]:
            continue
        if start < pd.Timestamp(now)-pd.Timedelta(days=2) or start > pd.Timestamp(now)+pd.Timedelta(days=1):
            continue
        state = context.assess(gid)
        for horizon in HORIZONS:
            cutoff = start - pd.Timedelta(minutes=horizon)
            due = cutoff - pd.Timedelta(minutes=5)
            key = digest(["pregame-scheduler-v1", model_id, gid, g["tipoff"], horizon])
            saved = ledger.rows("SELECT status,reason FROM scheduler_slots WHERE id=?", (key,))
            status = saved[0]["status"] if saved else "missed" if pd.Timestamp(now) > cutoff else "due" if pd.Timestamp(now) >= due else "scheduled"
            plans.append(dict(id=key, game_id=gid, model_id=model_id, home=g["home"], away=g["away"],
                              tipoff=g["tipoff"], horizon=horizon, due=utc(due), cutoff=utc(cutoff),
                              status=status, eligibility=state["status"],
                              reason=saved[0]["reason"] if saved else None, recorded=bool(saved)))
    return sorted(plans, key=lambda p: (p["horizon"], p["due"], p["game_id"]))


def _slot(ledger, item, status, reason, now):
    with ledger.db:
        ledger.db.execute("INSERT OR IGNORE INTO scheduler_slots VALUES(?,?,?,?,?,?,?,?)",
                          (item["id"], item["game_id"], item["model_id"], item["tipoff"], item["horizon"], now, status, reason))


def _has_quote(ledger, item, now):
    quotes = ledger.rows("""SELECT * FROM odds WHERE game_id=? AND bookmaker='draftkings' AND received<=?
        ORDER BY received DESC,id DESC LIMIT 1""", (item["game_id"], now))
    if not quotes:
        return False
    q = quotes[0]
    return (q["status"] == "quoted" and q["tipoff"] == item["tipoff"] and q["received"] <= item["cutoff"]
            and pd.Timedelta(0) <= pd.Timestamp(item["cutoff"])-pd.Timestamp(q["updated"]) <= pd.Timedelta(minutes=10))


def tick(ledger, model_id, as_of=None, collector=None, refresh=None):
    """One bounded pass. Caller owns the clock and may inject offline fixtures."""
    now = utc(as_of)
    owner = uuid.uuid4().hex
    ledger.db.execute("BEGIN IMMEDIATE")
    try:
        lease = ledger.rows("SELECT * FROM scheduler_leases WHERE name='tick'")
        if lease and lease[0]["expires"] > now:
            ledger.db.rollback()
            return dict(status="busy", requests=0)
        ledger.db.execute("INSERT OR REPLACE INTO scheduler_leases VALUES('tick',?,?)",
                          (owner, utc(pd.Timestamp(now)+pd.Timedelta(minutes=30))))
        ledger.db.commit()
    except Exception:
        ledger.db.rollback()
        raise
    try:
        if refresh:
            refresh()
            if as_of is None:
                now = utc()
        EligibilityContext(ledger, model_id, now).record()
        items = plan(ledger, model_id, now)
        for item in items:
            if item["status"] == "missed" and not item["recorded"]:
                _slot(ledger, item, "missed", "collection_window_missed", now)
        due = [p for p in items if p["status"] == "due" and p["eligibility"] == "eligible"]
        fetch = []
        for item in due:
            if _has_quote(ledger, item, now):
                _slot(ledger, item, "covered", "existing_fresh_quote", now)
            else:
                fetch.append(item)
        info = budget_status(ledger, now)
        primary_left = len({p["cutoff"] for p in items if p["horizon"] == 60
                            and p["status"] in ("scheduled", "due")
                            and pd.Timestamp(p["cutoff"]).tz_convert("America/New_York").date()
                            == pd.Timestamp(now).tz_convert("America/New_York").date()})
        primary = [p for p in fetch if p["horizon"] == 60]
        if not primary and info["daily_left"] <= primary_left:
            fetch = []
        attempted, error = 0, None
        if fetch and info["ready"] and min(info["daily_left"], info["cycle_left"]) > 0:
            # A slot gets at most one automatic attempt; failures remain auditable.
            for item in fetch:
                _slot(ledger, item, "attempted", None, now)
            if collector is None:
                from odds import collect_odds
                collector = lambda: collect_odds(ledger, purpose="scheduler")
            attempted = 1
            try:
                collector()
                finished = utc() if as_of is None else now
                with ledger.db:
                    for item in fetch:
                        ok = finished <= item["cutoff"] and _has_quote(ledger, item, finished)
                        ledger.db.execute("UPDATE scheduler_slots SET status=?,reason=? WHERE id=?",
                                          ("collected" if ok else "unavailable", None if ok else "no_timely_fresh_quote", item["id"]))
            except (ValueError, RuntimeError) as exc:
                error = type(exc).__name__
                with ledger.db:
                    for item in fetch:
                        ledger.db.execute("UPDATE scheduler_slots SET status='failed',reason=? WHERE id=?", (error, item["id"]))
        current = now if as_of is not None else utc()
        decisions = sum(ledger.decide(Policy(model_id, horizon_minutes=h), current) for h in HORIZONS)
        return dict(status="ok", requests=attempted, decisions=decisions, error=error,
                    budget=budget_status(ledger, current), due=len(due))
    finally:
        with ledger.db:
            ledger.db.execute("DELETE FROM scheduler_leases WHERE name='tick' AND owner=?", (owner,))


def refresh_tracking(ledger, model_path, config_path, force=False, expected_model_id=None):
    """Refresh once per Eastern day, using pinned weights and new completed logs."""
    from data import load_game_logs
    from modeling import load_training_result, update_prediction_history
    from production import load_production_config, upcoming_games, prediction_rows, current_nba_season_start, nba_season_label
    from provenance import implementation_id, runtime_versions
    result = load_training_result(Path(model_path))
    if expected_model_id is not None and result.metadata["model_id"] != expected_model_id:
        raise ValueError("Pinned model file was replaced. Stop and deliberately select a new policy/model.")
    if result.metadata["implementation_id"] != implementation_id() or result.metadata["runtime"] != runtime_versions():
        raise ValueError("Pinned model source/runtime changed. Train and deliberately select a new model before scheduling.")
    now = utc()
    today = pd.Timestamp(now).tz_convert("America/New_York").date()
    marker = ledger.rows("SELECT expires FROM scheduler_leases WHERE name=?", ("refresh:"+result.metadata["model_id"],))
    if marker:
        recent = pd.Timestamp(now)-pd.Timestamp(marker[0]["expires"]) < pd.Timedelta(minutes=15)
        same_day = pd.Timestamp(marker[0]["expires"]).tz_convert("America/New_York").date() == today
        if recent or (same_day and not force):
            return
    current = nba_season_label(current_nba_season_start(today))
    seasons = list(dict.fromkeys(result.seasons + result.warmup_seasons + [current]))
    logs = pd.concat([load_game_logs([s], result.season_types, refresh=s == current) for s in seasons], ignore_index=True)
    result = update_prediction_history(result, logs, now)
    import joblib
    from config import PROCESSED_DIR
    snapshots = PROCESSED_DIR / "snapshots"
    snapshots.mkdir(parents=True, exist_ok=True)
    snapshot_path = snapshots / (result.metadata["snapshot_hash"] + ".joblib")
    if not snapshot_path.exists():
        joblib.dump(result.history, snapshot_path)
    config = load_production_config(config_path, today)
    config = replace(config, season_types=result.season_types, betting_lines_csv=None)
    games = upcoming_games(config, today, ledger=ledger)
    ledger.record_history(result.history, result.metadata["snapshot_hash"])
    ledger.settle_logs(logs)
    if games:
        rows = prediction_rows(config, result, games)
        ledger.import_forecasts({"model": result.metadata, "predictions": rows.to_dict(orient="records")})
    with ledger.db:
        ledger.db.execute("INSERT OR REPLACE INTO scheduler_leases VALUES(?,?,?)",
                          ("refresh:"+result.metadata["model_id"], "daily-data", now))
