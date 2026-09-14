"""Append-only pregame research ledger. No wagering or live-game inference."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
import sqlite3

import numpy as np
import pandas as pd

from config import PROJECT_ROOT
from provenance import digest

DB_PATH = PROJECT_ROOT / "data" / "tracking" / "predictions.sqlite3"
STRATEGIES = ("favorite", "home", "model_winner", "model_value")


def utc(value=None) -> str:
    t = pd.Timestamp.now(tz="UTC") if value is None else pd.Timestamp(value)
    if pd.isna(t) or t.tzinfo is None:
        raise ValueError("Use an explicit timezone, e.g. 2026-10-20T23:00:00Z.")
    return t.tz_convert("UTC").floor("us").to_pydatetime().isoformat(timespec="microseconds")


def encoded(value) -> str:
    return json.dumps(value, sort_keys=True, allow_nan=False, default=str)


def decimal_odds(value: float, american=False) -> float:
    value = float(value)
    if american:
        if not math.isfinite(value) or abs(value) < 100:
            raise ValueError("American odds must be <= -100 or >= 100.")
        value = 1 + (value / 100 if value > 0 else 100 / -value)
    if not math.isfinite(value) or value <= 1:
        raise ValueError("Decimal odds must be finite and greater than 1.")
    return value


@dataclass(frozen=True)
class Policy:
    model_id: str
    bookmaker: str = "draftkings"
    horizon_minutes: int = 60
    odds_max_age_minutes: int = 10
    prediction_max_age_hours: int = 24
    stake: float = 10.0
    minimum_ev: float = 0.03
    version: int = 1

    def __post_init__(self):
        if not self.model_id or not self.bookmaker:
            raise ValueError("Pin a model and bookmaker for each policy.")
        if min(self.horizon_minutes, self.odds_max_age_minutes, self.prediction_max_age_hours) <= 0:
            raise ValueError("Horizon and freshness limits must be positive.")
        if not math.isfinite(self.stake) or self.stake <= 0 or not math.isfinite(self.minimum_ev) or self.minimum_ev < 0:
            raise ValueError("Stake must be positive and minimum EV nonnegative.")

    @property
    def id(self):
        return digest(asdict(self))


class Ledger:
    def __init__(self, path=DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            raise ValueError(f"Unsupported tracking schema {version}")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS games(id TEXT PRIMARY KEY, home TEXT NOT NULL, away TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS schedules(
            seq INTEGER PRIMARY KEY, game_id TEXT REFERENCES games(id), tipoff TEXT NOT NULL,
            status TEXT NOT NULL, observed TEXT NOT NULL, source TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS schedule_game ON schedules(game_id, observed);
        CREATE TABLE IF NOT EXISTS models(id TEXT PRIMARY KEY, metadata TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS forecasts(
            id TEXT PRIMARY KEY, game_id TEXT REFERENCES games(id), model_id TEXT REFERENCES models(id),
            issued TEXT NOT NULL, received TEXT NOT NULL, probability REAL NOT NULL, payload TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS forecast_game ON forecasts(game_id, model_id, issued);
        CREATE TABLE IF NOT EXISTS odds(
            id TEXT PRIMARY KEY, game_id TEXT REFERENCES games(id), bookmaker TEXT NOT NULL,
            updated TEXT NOT NULL, received TEXT NOT NULL, tipoff TEXT NOT NULL,
            home REAL, away REAL, status TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS odds_game ON odds(game_id, bookmaker, received);
        CREATE TABLE IF NOT EXISTS results(
            seq INTEGER PRIMARY KEY, game_id TEXT REFERENCES games(id), observed TEXT NOT NULL,
            status TEXT NOT NULL, home_score INTEGER, away_score INTEGER, source TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS policies(id TEXT PRIMARY KEY, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS decisions(
            policy_id TEXT REFERENCES policies(id), game_id TEXT REFERENCES games(id),
            cutoff TEXT NOT NULL, created TEXT NOT NULL, payload TEXT NOT NULL,
            PRIMARY KEY(policy_id, game_id));
        CREATE TABLE IF NOT EXISTS actual_wagers(
            id TEXT PRIMARY KEY, game_id TEXT REFERENCES games(id), bookmaker TEXT NOT NULL,
            accepted TEXT NOT NULL, received TEXT NOT NULL, selection TEXT NOT NULL,
            odds REAL NOT NULL, stake REAL NOT NULL, reference TEXT NOT NULL,
            UNIQUE(bookmaker, reference));
        CREATE TABLE IF NOT EXISTS actual_settlements(
            seq INTEGER PRIMARY KEY, wager_id TEXT REFERENCES actual_wagers(id), observed TEXT NOT NULL,
            status TEXT NOT NULL, payout REAL NOT NULL, reference TEXT NOT NULL);
        PRAGMA user_version=1;
        """)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.db.close()

    def rows(self, sql, params=()):
        return [dict(r) for r in self.db.execute(sql, params)]

    def game(self, game_id, home, away):
        if not game_id or not home or not away or home == away:
            raise ValueError("A game requires a stable ID and two different teams.")
        old = self.rows("SELECT * FROM games WHERE id=?", (str(game_id),))
        if old and (old[0]["home"], old[0]["away"]) != (home, away):
            raise ValueError("Game ID reused for different teams.")
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO games VALUES(?,?,?)", (str(game_id), home, away))

    def schedule(self, game_id, tipoff, observed=None, status="scheduled", source="nba"):
        tipoff, observed = utc(tipoff), utc(observed)
        if status not in ("scheduled", "postponed", "canceled", "started", "final"):
            raise ValueError("Unknown schedule status.")
        old = self.rows("SELECT * FROM schedules WHERE game_id=? ORDER BY seq DESC LIMIT 1", (game_id,))
        if old and observed < old[0]["observed"]:
            raise ValueError("Cannot backdate schedule observations.")
        if old and (old[0]["tipoff"], old[0]["status"]) == (tipoff, status):
            return
        with self.db:
            self.db.execute("INSERT INTO schedules(game_id,tipoff,status,observed,source) VALUES(?,?,?,?,?)",
                            (game_id, tipoff, status, observed, source))

    def forecast(self, details, metadata, received=None):
        received, issued = utc(received), utc(details["issued_at"])
        tipoff = utc(details["tipoff_at"])
        if issued >= tipoff or issued > received:
            raise ValueError("Forecast must be issued before tipoff and before receipt.")
        p = float(details["home_win_probability"])
        if not math.isfinite(p) or not 0 <= p <= 1:
            raise ValueError("Invalid probability.")
        if details["model_id"] != metadata["model_id"]:
            raise ValueError("Model metadata mismatch.")
        game_id = str(details["game_id"])
        self.game(game_id, details["home"], details["away"])
        # Old archives remain auditable but their availability is never backdated.
        schedules = self.rows("SELECT * FROM schedules WHERE game_id=? ORDER BY seq DESC LIMIT 1", (game_id,))
        if not schedules or issued >= schedules[0]["observed"]:
            self.schedule(game_id, tipoff, received)
        payload = encoded(details)
        key = digest(details)
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO models VALUES(?,?)", (metadata["model_id"], encoded(metadata)))
            self.db.execute("INSERT OR IGNORE INTO forecasts VALUES(?,?,?,?,?,?,?)",
                            (key, game_id, details["model_id"], issued, received, p, payload))
        return key

    def import_forecasts(self, artifact, received=None):
        received = utc(received)
        count = 0
        for row in artifact["predictions"]:
            d = row["provenance"]
            if not d.get("game_id") or not d.get("tipoff_at"):
                continue
            self.forecast(d, artifact["model"], received)
            count += 1
        return count

    def quote(self, game_id, bookmaker, updated, tipoff, home=None, away=None,
              received=None, status="quoted", payload=None):
        updated, received, tipoff = utc(updated), utc(received), utc(tipoff)
        if updated > received or received >= tipoff:
            raise ValueError("Odds must be received before tipoff; source cannot be in the future.")
        if status not in ("quoted", "unavailable"):
            raise ValueError("Unknown odds status.")
        if status == "quoted":
            home, away = decimal_odds(home), decimal_odds(away)
        else:
            home = away = None
        values = dict(game_id=game_id, bookmaker=bookmaker, updated=updated, received=received,
                      tipoff=tipoff, home=home, away=away, status=status, payload=payload or {})
        key = digest(values)
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO odds VALUES(?,?,?,?,?,?,?,?,?,?)",
                            (key, game_id, bookmaker, updated, received, tipoff, home, away, status, encoded(payload or {})))
        return key

    def result(self, game_id, home_score=None, away_score=None, status="final", observed=None, source="nba"):
        observed = utc(observed)
        if status not in ("final", "void", "pending"):
            raise ValueError("Unknown result status.")
        if status == "final":
            if any(v is None or not math.isfinite(float(v)) or float(v) < 0 or int(v) != float(v)
                   for v in (home_score, away_score)) or home_score == away_score:
                raise ValueError("Final NBA scores must be nonnegative integers with no tie.")
            home_score, away_score = int(home_score), int(away_score)
        else:
            home_score = away_score = None
        old = self.rows("SELECT * FROM results WHERE game_id=? ORDER BY seq DESC LIMIT 1", (game_id,))
        if old and observed < old[0]["observed"]:
            raise ValueError("Cannot backdate result observations.")
        if old and (old[0]["status"], old[0]["home_score"], old[0]["away_score"], old[0]["source"]) == (status, home_score, away_score, source):
            return
        with self.db:
            self.db.execute("INSERT INTO results(game_id,observed,status,home_score,away_score,source) VALUES(?,?,?,?,?,?)",
                            (game_id, observed, status, home_score, away_score, source))

    def settle_logs(self, logs, observed=None):
        from data import completed_game_logs
        observed = utc(observed)
        logs = completed_game_logs(logs, observed)
        known = {g["id"]: g for g in self.rows("SELECT * FROM games")}
        count = 0
        for game_id, group in logs.groupby("GAME_ID"):
            if game_id not in known:
                continue
            home = group[group["MATCHUP"].str.contains(" vs. ", regex=False)].iloc[0]
            away = group[group["TEAM_ID"] != home["TEAM_ID"]].iloc[0]
            if (home["TEAM_ABBREVIATION"], away["TEAM_ABBREVIATION"]) != (known[game_id]["home"], known[game_id]["away"]):
                raise ValueError("Result team mismatch.")
            self.result(game_id, home["PTS"], away["PTS"], observed=observed)
            count += 1
        return count

    def decide(self, policy: Policy, as_of=None):
        now = utc(as_of)
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO policies VALUES(?,?)", (policy.id, encoded(asdict(policy))))
        count = 0
        for game in self.rows("SELECT * FROM games ORDER BY id"):
            gid = game["id"]
            if self.rows("SELECT 1 FROM decisions WHERE policy_id=? AND game_id=?", (policy.id, gid)):
                continue
            schedules = self.rows("SELECT * FROM schedules WHERE game_id=? AND observed<=? ORDER BY observed,seq", (gid, now))
            if not schedules:
                continue
            s = schedules[-1]
            cutoff = utc(pd.Timestamp(s["tipoff"]) - pd.Timedelta(minutes=policy.horizon_minutes))
            if cutoff > now:
                continue
            reason = None
            if s["status"] != "scheduled":
                reason = "schedule_not_open"
            if s["observed"] > cutoff:
                reason = "schedule_unavailable_at_cutoff"
            preds = self.rows("SELECT * FROM forecasts WHERE game_id=? AND model_id=? AND issued<=? AND received<=? ORDER BY issued DESC, id DESC LIMIT 1",
                              (gid, policy.model_id, cutoff, cutoff))
            odds = self.rows("SELECT * FROM odds WHERE game_id=? AND bookmaker=? AND received<=? AND updated<=? ORDER BY received DESC, id DESC LIMIT 1",
                             (gid, policy.bookmaker, cutoff, cutoff))
            pred, quote = preds[0] if preds else None, odds[0] if odds else None
            if not reason and not pred:
                reason = "missing_prediction"
            if not reason and (pd.Timestamp(cutoff) - pd.Timestamp(pred["issued"])).total_seconds() > policy.prediction_max_age_hours * 3600:
                reason = "stale_prediction"
            if not reason and utc(json.loads(pred["payload"])["tipoff_at"]) != s["tipoff"]:
                reason = "prediction_schedule_mismatch"
            if not reason and not quote:
                reason = "missing_odds"
            if not reason and quote["status"] != "quoted":
                reason = "odds_unavailable"
            if not reason and quote["tipoff"] != s["tipoff"]:
                reason = "odds_schedule_mismatch"
            if not reason and (pd.Timestamp(cutoff) - pd.Timestamp(quote["updated"])).total_seconds() > policy.odds_max_age_minutes * 60:
                reason = "stale_odds"
            bets = {}
            if not reason:
                p, h, a = pred["probability"], quote["home"], quote["away"]
                evs = {"home": p * h - 1, "away": (1 - p) * a - 1}
                value_side = max(evs, key=evs.get)
                selections = dict(favorite="home" if h < a else "away" if a < h else None,
                                  home="home", model_winner="home" if p > .5 else "away" if p < .5 else None,
                                  model_value=value_side if evs[value_side] > policy.minimum_ev else None)
                for strategy, side in selections.items():
                    bets[strategy] = dict(selection=side, odds=(h if side == "home" else a) if side else None,
                                          stake=policy.stake if side else 0,
                                          reason=None if side else "tie_or_below_threshold")
            payload = dict(tipoff=s["tipoff"], schedule_seq=s["seq"], reason=reason,
                           prediction_id=pred["id"] if pred else None, odds_id=quote["id"] if quote else None,
                           probability=pred["probability"] if pred else None,
                           market_probability=(1 / quote["home"]) / (1 / quote["home"] + 1 / quote["away"])
                           if quote and quote["status"] == "quoted" else None, bets=bets)
            with self.db:
                cur = self.db.execute("INSERT OR IGNORE INTO decisions VALUES(?,?,?,?,?)",
                                      (policy.id, gid, cutoff, now, encoded(payload)))
                count += cur.rowcount
        return count

    def paper_rows(self, policy_id, as_of=None):
        now = utc(as_of)
        output = []
        for d in self.rows("SELECT d.*,g.home,g.away FROM decisions d JOIN games g ON g.id=d.game_id WHERE policy_id=? AND created<=? ORDER BY cutoff,game_id", (policy_id, now)):
            p = json.loads(d["payload"])
            changes = self.rows("SELECT * FROM schedules WHERE game_id=? AND seq>? AND observed<=?", (d["game_id"], p["schedule_seq"], now))
            invalidated = any(s["tipoff"] != p["tipoff"] or s["status"] in ("postponed", "canceled") for s in changes)
            results = self.rows("SELECT * FROM results WHERE game_id=? AND observed<=? ORDER BY observed DESC,seq DESC LIMIT 1", (d["game_id"], now))
            r = results[0] if results else None
            winner = ("home" if r["home_score"] > r["away_score"] else "away") if r and r["status"] == "final" else None
            for strategy in STRATEGIES:
                bet = p["bets"].get(strategy, {})
                side = bet.get("selection")
                status, profit = "skipped", None
                reason = p["reason"] or bet.get("reason")
                if side:
                    status = "pending"
                    if invalidated or (r and r["status"] == "void"):
                        status, profit, reason = "void", 0.0, "schedule_changed" if invalidated else "result_void"
                    elif winner:
                        status = "win" if side == winner else "loss"
                        profit = bet["stake"] * (bet["odds"] - 1) if status == "win" else -bet["stake"]
                output.append(dict(game_id=d["game_id"], home=d["home"], away=d["away"], cutoff=d["cutoff"],
                                   tipoff=p["tipoff"], strategy=strategy, status=status, reason=reason,
                                   selection=side, odds=bet.get("odds"), stake=bet.get("stake", 0), profit=profit,
                                   probability=p["probability"], market_probability=p["market_probability"],
                                   home_win=int(winner == "home") if winner and not invalidated and not p["reason"] else None,
                                   settled_at=r["observed"] if r else None))
        return pd.DataFrame(output)

    def record_actual(self, game_id, bookmaker, reference, selection, odds, stake, accepted, received=None):
        accepted, received = utc(accepted), utc(received)
        if selection not in ("home", "away") or not math.isfinite(stake) or stake <= 0 or accepted > received:
            raise ValueError("Invalid accepted wager receipt.")
        odds = decimal_odds(odds)
        if not reference:
            raise ValueError("A bookmaker receipt reference is required.")
        schedules = self.rows("SELECT * FROM schedules WHERE game_id=? AND observed<=? ORDER BY observed DESC,seq DESC LIMIT 1",
                              (game_id, accepted))
        if not schedules or schedules[0]["status"] != "scheduled" or accepted >= schedules[0]["tipoff"]:
            raise ValueError("Receipt is not pregame against the recorded schedule at acceptance.")
        key = digest([bookmaker, reference])
        values = (key, game_id, bookmaker, accepted, received, selection, odds, stake, reference)
        old = self.rows("SELECT * FROM actual_wagers WHERE id=?", (key,))
        if old and any(old[0][k] != v for k, v in zip(("id", "game_id", "bookmaker", "accepted", "selection", "odds", "stake", "reference"),
                                                     (key, game_id, bookmaker, accepted, selection, odds, stake, reference))):
            raise ValueError("Conflicting receipt; original wager is immutable.")
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO actual_wagers VALUES(?,?,?,?,?,?,?,?,?)", values)
        return key

    def settle_actual(self, wager_id, status, payout, reference, observed=None):
        if status not in ("win", "loss", "void", "push") or not math.isfinite(payout) or payout < 0 or not reference:
            raise ValueError("Record the bookmaker's actual gross payout and settlement reference.")
        observed = utc(observed)
        wagers = self.rows("SELECT * FROM actual_wagers WHERE id=?", (wager_id,))
        if not wagers or observed < wagers[0]["accepted"]:
            raise ValueError("Settlement requires an existing, previously accepted wager.")
        stake = wagers[0]["stake"]
        if ((status == "loss" and payout != 0) or (status in ("void", "push") and payout != stake)
                or (status == "win" and payout <= stake)):
            raise ValueError("Payout conflicts with settlement status (gross payout includes returned stake).")
        old = self.rows("SELECT * FROM actual_settlements WHERE wager_id=? ORDER BY seq DESC LIMIT 1", (wager_id,))
        if old and observed < old[0]["observed"]:
            raise ValueError("Cannot backdate settlements.")
        if old and (old[0]["status"], old[0]["payout"], old[0]["reference"]) == (status, payout, reference):
            return
        with self.db:
            self.db.execute("INSERT INTO actual_settlements(wager_id,observed,status,payout,reference) VALUES(?,?,?,?,?)",
                            (wager_id, observed, status, payout, reference))


def summarize(rows):
    output = []
    for strategy in STRATEGIES:
        r = rows[rows.strategy == strategy] if not rows.empty else pd.DataFrame()
        settled = r[r.status.isin(["win", "loss"])] if not r.empty else r
        stake = float(settled.stake.sum()) if not settled.empty else 0.0
        profit = float(settled.profit.sum()) if not settled.empty else 0.0
        curve = np.r_[0, settled.sort_values(["settled_at", "game_id"]).profit.to_numpy(dtype=float).cumsum()] if not settled.empty else np.array([0.])
        low = high = None
        # Resample whole game-days, retaining within-day correlation.
        if not settled.empty:
            days = settled.assign(day=settled.tipoff.str[:10]).groupby("day")[["profit", "stake"]].sum().to_numpy(dtype=float)
            if len(days) >= 10:
                rng = np.random.default_rng(42)
                samples = days[rng.integers(0, len(days), (2000, len(days)))].sum(axis=1)
                low, high = np.quantile(samples[:, 0] / samples[:, 1], [.025, .975])
        output.append(dict(strategy=strategy, decisions=len(r), bets=int(r.selection.notna().sum()) if not r.empty else 0,
                           wins=int((r.status == "win").sum()) if not r.empty else 0,
                           losses=int((r.status == "loss").sum()) if not r.empty else 0,
                           pending=int((r.status == "pending").sum()) if not r.empty else 0,
                           voids=int((r.status == "void").sum()) if not r.empty else 0,
                           skipped=int((r.status == "skipped").sum()) if not r.empty else 0,
                           total_stake=float(r.stake.sum()) if not r.empty else 0.0,
                           pending_stake=float(r.loc[r.status == "pending", "stake"].sum()) if not r.empty else 0.0,
                           settled_stake=stake, profit=profit, roi=profit / stake if stake else None,
                           win_rate=float((settled.status == "win").mean()) if not settled.empty else None,
                           average_odds=float(settled.odds.mean()) if not settled.empty else None,
                           max_drawdown=float((np.maximum.accumulate(curve) - curve).max()),
                           roi_ci_low=low, roi_ci_high=high))
    return pd.DataFrame(output)


def probability_report(rows):
    if rows.empty:
        return pd.DataFrame(), pd.DataFrame()
    eligible = rows[(rows.strategy == "home") & rows.home_win.notna()]
    metrics, calibration = [], []
    for label, column in (("model", "probability"), ("market_no_vig", "market_probability")):
        p = eligible[column].to_numpy(dtype=float)
        y = eligible.home_win.to_numpy(dtype=float)
        if not len(p):
            continue
        clipped = np.clip(p, 1e-15, 1 - 1e-15)
        metrics.append(dict(forecaster=label, games=len(p), accuracy=float(((p >= .5) == y).mean()),
                            brier=float(((p-y)**2).mean()),
                            log_loss=float(-(y*np.log(clipped)+(1-y)*np.log(1-clipped)).mean())))
        binned = pd.DataFrame(dict(p=p, y=y, bin=np.minimum((p * 10).astype(int), 9)))
        for b, group in binned.groupby("bin"):
            calibration.append(dict(forecaster=label, bin=int(b), games=len(group),
                                    mean_prediction=group.p.mean(), observed_win_rate=group.y.mean()))
    return pd.DataFrame(metrics), pd.DataFrame(calibration)


def export_report(ledger, policy_id, directory, as_of=None, synthetic=False):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    now = utc(as_of)
    policies = ledger.rows("SELECT payload FROM policies WHERE id=?", (policy_id,))
    if not policies:
        raise ValueError("Unknown policy ID.")
    rows = ledger.paper_rows(policy_id, now)
    summary = summarize(rows)
    metrics, calibration = probability_report(rows)
    rows.to_csv(directory / "paper_bets.csv", index=False)
    summary.to_csv(directory / "strategy_summary.csv", index=False)
    metrics.to_csv(directory / "probability_metrics.csv", index=False)
    calibration.to_csv(directory / "calibration.csv", index=False)
    periods = []
    if not rows.empty:
        dates = pd.to_datetime(rows.tipoff, utc=True).dt.tz_convert("America/New_York")
        for kind, labels in (("month", dates.dt.strftime("%Y-%m")),
                             ("season_start", (dates.dt.year - (dates.dt.month < 10).astype(int)).astype(str))):
            for label in sorted(labels.unique()):
                periods.append(summarize(rows[labels == label]).assign(period_type=kind, period=label))
    (pd.concat(periods, ignore_index=True) if periods else pd.DataFrame()).to_csv(directory / "period_summary.csv", index=False)
    actual = ledger.rows("""SELECT w.*,s.status,s.payout,s.payout-w.stake AS profit FROM actual_wagers w
        LEFT JOIN actual_settlements s ON s.seq=(SELECT MAX(seq) FROM actual_settlements WHERE wager_id=w.id AND observed<=?)
        WHERE w.received<=?""", (now, now))
    actual = pd.DataFrame(actual)
    actual.to_csv(directory / "actual_wagers.csv", index=False)
    actual_summaries = []
    if not actual.empty:
        for bookmaker, group in actual.groupby("bookmaker"):
            settled = group[group.status.isin(["win", "loss"])]
            amount = float(settled.stake.sum())
            profit = float(group.profit.sum())
            actual_summaries.append(dict(bookmaker=bookmaker, bets=len(group), pending=int(group.status.isna().sum()),
                settled_stake=amount, net_profit=profit, roi=profit / amount if amount else None))
    pd.DataFrame(actual_summaries).to_csv(directory / "actual_summary.csv", index=False)
    (directory / "report.json").write_text(encoded(dict(policy_id=policy_id, policy=json.loads(policies[0]["payload"]),
        as_of=now, synthetic=synthetic, kind="paper; quoted prices are not guaranteed fills", roi_denominator="settled nonvoid stake",
        schedule_rule="any subsequent postponement/cancellation/tipoff change voids paper bet; no re-entry",
        uncertainty="95% game-day bootstrap, >=10 settled days; descriptive, not proof of an edge")), encoding="utf-8")
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    fig = Figure(figsize=(10, 4))
    FigureCanvasAgg(fig)
    ax = fig.subplots()
    for strategy in STRATEGIES:
        r = rows[(rows.strategy == strategy) & rows.status.isin(["win", "loss"])] if not rows.empty else rows
        if not r.empty:
            r = r.sort_values(["settled_at", "game_id"])
            ax.plot(np.arange(len(r)+1), np.r_[0, r.profit.cumsum()], label=strategy)
    ax.set(title="Synthetic accounting example - not real returns" if synthetic else "Paper strategy profit",
           xlabel="Settled bets", ylabel="Net profit ($)")
    if ax.lines:
        ax.legend()
    else:
        ax.text(.5, .5, "No settled paper bets yet", transform=ax.transAxes, ha="center")
    fig.tight_layout()
    fig.savefig(directory / "paper_profit.png", dpi=140)
    return summary
