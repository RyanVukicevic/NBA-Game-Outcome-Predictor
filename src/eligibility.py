"""Point-in-time next-game readiness for tracked regular-season/playoff games."""
import json

import pandas as pd

from tracking import encoded, utc


def record_schedule(ledger, frame, season_types, observed=None):
    """Keep completed/ongoing games too, so a fresh installation cannot miss a blocker."""
    observed = utc(observed)
    phases = {"002": "Regular Season", "004": "Playoffs"}
    for row in frame.to_dict(orient="records"):
        gid = str(row.get("GAME_ID", "")).removesuffix(".0").zfill(10)
        if phases.get(gid[:3]) not in season_types or pd.isna(row.get("TIPOFF_AT")):
            continue
        label = str(row.get("STATUS", "")).lower()
        status_id = row.get("STATUS_ID")
        status = ("canceled" if "cancel" in label else "postponed" if "postpon" in label
                  else "final" if status_id == 3 or "final" in label
                  else "started" if status_id == 2 else "scheduled")
        ledger.game(gid, row["HOME_TEAM"], row["AWAY_TEAM"])
        ledger.schedule(gid, row["TIPOFF_AT"], observed, status)


class EligibilityContext:
    def __init__(self, ledger, model_id, as_of=None):
        self.ledger, self.model_id, self.now = ledger, model_id, utc(as_of)
        self.games = {r["id"]: r for r in ledger.rows("""SELECT g.*,s.tipoff,s.status,s.seq AS schedule_seq,s.observed,
            (SELECT MIN(observed) FROM schedules WHERE game_id=g.id) AS first_observed
            FROM games g JOIN schedules s ON s.seq=(SELECT seq FROM schedules WHERE game_id=g.id AND observed<=?
            ORDER BY observed DESC,seq DESC LIMIT 1)""", (self.now,))}
        self.results = {r["game_id"]: r for r in ledger.rows("""SELECT r.* FROM results r WHERE r.seq=(SELECT seq FROM results
            WHERE game_id=r.game_id AND observed<=? ORDER BY observed DESC,seq DESC LIMIT 1)""", (self.now,))}
        self.forecasts = {r["game_id"]: r for r in ledger.rows("""SELECT f.* FROM forecasts f WHERE f.model_id=?
            AND f.id=(SELECT id FROM forecasts WHERE game_id=f.game_id AND model_id=? AND issued<=? AND received<=?
            ORDER BY issued DESC,id DESC LIMIT 1)""", (model_id, model_id, self.now, self.now))}
        self.snapshots = {}

    def assess(self, game_id):
        g = self.games.get(game_id)
        out = dict(game_id=game_id, model_id=self.model_id, status="closed", blockers=[], latest_incorporated={},
                   schedule_seq=g["schedule_seq"] if g else None, forecast_id=None)
        if not g or g["status"] in ("canceled", "postponed", "started", "final") or g["tipoff"] <= self.now:
            return out
        forecast = self.forecasts.get(game_id)
        details = json.loads(forecast["payload"]) if forecast else {}
        out["forecast_id"] = forecast["id"] if forecast else None
        snapshot = details.get("snapshot_hash")
        if snapshot not in self.snapshots:
            self.snapshots[snapshot] = self.ledger.rows("SELECT * FROM snapshot_games WHERE snapshot_hash=?", (snapshot,))
        history = self.snapshots[snapshot]
        members = {(r["game_id"], r["team"]): r for r in history}
        for team in (g["home"], g["away"]):
            team_history = [r for r in history if r["team"] == team]
            out["latest_incorporated"][team] = max(team_history, key=lambda r: (r["game_date"], r["game_id"])) if team_history else None
            for previous in self.games.values():
                if previous["id"] == game_id or team not in (previous["home"], previous["away"]):
                    continue
                if previous["tipoff"] > g["tipoff"] or previous["status"] == "canceled":
                    continue
                result = self.results.get(previous["id"])
                state = None
                if previous["status"] == "postponed":
                    state = "waiting"
                elif not result or result["status"] != "final":
                    state = "waiting" if previous["tipoff"] >= self.now else "awaiting_data"
                else:
                    member = members.get((previous["id"], team))
                    score = result["home_score"] if team == previous["home"] else result["away_score"]
                    if not member or member["points"] != score:
                        state = "awaiting_data"
                if state:
                    out["blockers"].append(dict(team=team, game_id=previous["id"], tipoff=previous["tipoff"], status=state))
        if any(b["status"] == "waiting" for b in out["blockers"]):
            out["status"] = "waiting"
        elif (out["blockers"] or not forecast or utc(details["tipoff_at"]) != g["tipoff"]
              or any(v is None for v in out["latest_incorporated"].values())):
            out["status"] = "awaiting_data"
        else:
            out["status"] = "eligible"
        return out

    def record(self):
        output = []
        for gid, game in self.games.items():
            if game["tipoff"] <= self.now:
                continue
            state = self.assess(gid)
            previous = self.ledger.rows("SELECT * FROM eligibility WHERE game_id=? AND model_id=? ORDER BY seq DESC LIMIT 1",
                                        (gid, self.model_id))
            payload = encoded(state)
            if not previous or previous[0]["payload"] != payload:
                if previous and previous[0]["observed"] > self.now:
                    raise ValueError("Cannot backdate eligibility observations.")
                with self.ledger.db:
                    self.ledger.db.execute("INSERT INTO eligibility(game_id,model_id,observed,status,payload) VALUES(?,?,?,?,?)",
                                           (gid, self.model_id, self.now, state["status"], payload))
            eligible = self.ledger.rows("SELECT observed,payload FROM eligibility WHERE game_id=? AND model_id=? AND status='eligible' AND observed<=? ORDER BY observed,seq",
                                       (gid, self.model_id, self.now))
            state["first_eligible_at"] = next((r["observed"] for r in eligible
                                               if json.loads(r["payload"])["schedule_seq"] == state["schedule_seq"]), None)
            output.append({**state, "home": game["home"], "away": game["away"], "tipoff": game["tipoff"]})
        return output
