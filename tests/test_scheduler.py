import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from budget import BudgetError, configure, finish, next_month, reserve, status
from eligibility import EligibilityContext, record_schedule
from scheduler import plan, tick, refresh_tracking
from tracking import Ledger, Policy, american_odds, utc

START = "2026-10-20T23:00:00Z"
ISSUE = "2026-10-20T17:00:00Z"
DUE = "2026-10-20T21:55:00Z"
CUTOFF = "2026-10-20T22:00:00Z"


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "ledger.sqlite3"
        self.l = Ledger(self.path)
        configure(self.l, "2026-11-01T00:00:00Z", 0, "2026-10-20T00:00:00Z", calendar_monthly=True)
        self.history = pd.DataFrame([
            dict(GAME_ID="0022600000", TEAM_ABBREVIATION="BOS", GAME_DATE="2026-10-18", PTS=105),
            dict(GAME_ID="0022600000", TEAM_ABBREVIATION="NYK", GAME_DATE="2026-10-18", PTS=100)])
        self.l.record_history(self.history, "snapshot0")
        self.details = dict(game_id="next", home="BOS", away="NYK", tipoff_at=START, issued_at=ISSUE,
                            model_id="m", snapshot_hash="snapshot0", home_win_probability=.65)
        self.l.forecast(self.details, {"model_id": "m"}, ISSUE)

    def tearDown(self):
        self.l.db.close()
        self.temp.cleanup()

    def assess(self, at=DUE):
        return EligibilityContext(self.l, "m", at).assess("next")

    def quote(self, at=DUE):
        self.l.quote("next", "draftkings", at, START, 1.8, 2.1, at)

    def previous(self, tipoff="2026-10-19T23:00:00Z"):
        self.l.game("0022600001", "NYK", "PHI")
        self.l.schedule("0022600001", tipoff, "2026-10-18T10:00:00Z")

    def test_american_conversion(self):
        for decimal, american in [(1.25,-400),(1.5,-200),(1.8,-125),(2,100),(2.5,150),(3,200)]:
            self.assertEqual(american_odds(decimal), american)
        with self.assertRaises(ValueError):
            american_odds(1)

    def test_ready_requires_history(self):
        self.assertEqual(self.assess()["status"], "eligible")
        self.l.forecast({**self.details, "snapshot_hash": "missing", "issued_at": DUE}, {"model_id": "m"}, DUE)
        self.assertEqual(self.assess()["status"], "awaiting_data")

    def test_wait_for_opponents_intervening_game(self):
        self.previous("2026-10-20T22:00:00Z")
        state = self.assess()
        self.assertEqual(state["status"], "waiting")
        self.assertEqual(state["blockers"][0]["team"], "NYK")

    def test_final_result_must_enter_forecast_snapshot(self):
        self.previous()
        self.assertEqual(self.assess()["status"], "awaiting_data")
        self.l.result("0022600001", 110, 90, observed="2026-10-20T14:00:00Z")
        self.assertEqual(self.assess()["status"], "awaiting_data")
        updated = pd.concat([self.history, pd.DataFrame([
            dict(GAME_ID="0022600001", TEAM_ABBREVIATION="NYK", GAME_DATE="2026-10-19", PTS=110)])])
        self.l.record_history(updated, "snapshot1")
        self.l.forecast({**self.details, "snapshot_hash":"snapshot1", "issued_at":"2026-10-20T18:00:00Z"}, {"model_id":"m"}, "2026-10-20T18:00:00Z")
        self.assertEqual(self.assess()["status"], "eligible")
        self.assertEqual(self.assess()["latest_incorporated"]["NYK"]["game_id"], "0022600001")
        self.l.result("0022600001", 111, 90, observed="2026-10-20T20:00:00Z")
        self.assertEqual(self.assess()["status"], "awaiting_data")

    def test_cancellation_and_postponement(self):
        self.previous()
        self.l.schedule("0022600001", "2026-10-19T23:00:00Z", ISSUE, "postponed")
        self.assertEqual(self.assess()["status"], "waiting")
        self.l.schedule("0022600001", "2026-10-19T23:00:00Z", DUE, "canceled")
        self.assertEqual(self.assess()["status"], "eligible")

    def test_first_eligible_is_observed_not_invented(self):
        first = EligibilityContext(self.l,"m",DUE).record()[0]
        second = EligibilityContext(self.l,"m",CUTOFF).record()[0]
        self.assertEqual(first["first_eligible_at"], utc(DUE))
        self.assertEqual(second["first_eligible_at"], utc(DUE))
        self.assertEqual(len(self.l.rows("SELECT * FROM eligibility")), 1)

    def test_new_policy_gates_and_preserves_legacy(self):
        self.previous()
        self.quote()
        new, old = Policy("m"), Policy("m", version=1)
        self.l.decide(new,CUTOFF)
        self.l.decide(old,CUTOFF)
        decisions = {r["policy_id"]:json.loads(r["payload"]) for r in self.l.rows("SELECT * FROM decisions WHERE game_id='next'")}
        self.assertEqual(decisions[new.id]["reason"], "eligibility_awaiting_data")
        self.assertIsNone(decisions[old.id]["reason"])

    def test_future_ingestion_does_not_fix_past_eligibility(self):
        self.previous()
        self.l.result("0022600001",110,90,observed="2026-10-20T22:01:00Z")
        self.assertEqual(self.assess(CUTOFF)["status"],"awaiting_data")

    def test_daily_cap_shared_across_connections(self):
        for i in range(12):
            key=reserve(self.l,"manual",DUE)
            finish(self.l,key,success=True,as_of=DUE)
        with Ledger(self.path) as other:
            with self.assertRaises(BudgetError):
                reserve(other,"scheduler",DUE)
        self.assertEqual(status(self.l,DUE)["daily_left"],0)

    def test_inflight_and_timeout_reserved_cost(self):
        key=reserve(self.l,"test",DUE)
        with self.assertRaises(BudgetError):
            reserve(self.l,"duplicate",DUE)
        finish(self.l,key,success=False,as_of=DUE)
        self.assertEqual(status(self.l,DUE)["accounted_used"],1)

    def test_headers_reconcile_external_use_and_zero_cost(self):
        key=reserve(self.l,"test",DUE)
        finish(self.l,key,{"x-requests-used":"449","x-requests-remaining":"51","x-requests-last":"1"},True,DUE)
        self.assertEqual(status(self.l,DUE)["cycle_left"],1)
        key=reserve(self.l,"test",DUE)
        finish(self.l,key,success=False,as_of=DUE)
        with self.assertRaises(BudgetError):
            reserve(self.l,"test",DUE)

    def test_empty_response_refunds_reservation(self):
        key=reserve(self.l,"test",DUE)
        finish(self.l,key,{"x-requests-last":"0","x-requests-used":"0","x-requests-remaining":"500"},True,DUE)
        self.assertEqual(status(self.l,DUE)["accounted_used"],0)

    def test_month_rollover_not_daily_budget_reset(self):
        key=reserve(self.l,"test",DUE)
        finish(self.l,key,success=True,as_of=DUE)
        self.assertEqual(status(self.l,"2026-10-21T15:00:00Z")["daily_used"],0)
        self.assertEqual(status(self.l,"2026-10-21T15:00:00Z")["accounted_used"],1)
        reserve(self.l,"new-month","2026-11-01T00:00:00Z")
        self.assertEqual(len(self.l.rows("SELECT * FROM quota_cycles")),2)
        self.assertEqual(status(self.l,"2026-11-01T00:00:00Z")["accounted_used"],1)
        self.assertEqual(next_month("2026-12-31T23:59:00Z"),utc("2027-01-01T00:00:00Z"))

    def test_cannot_reinitialize_active_quota(self):
        with self.assertRaises(BudgetError):
            configure(self.l,"2026-11-01T00:00:00Z",0,DUE)

    def test_primary_one_request_then_decision(self):
        calls=[]
        def collect():
            calls.append(1)
            key=reserve(self.l,"scheduler",DUE)
            self.quote()
            finish(self.l,key,success=True,as_of=DUE)
        self.assertEqual(tick(self.l,"m",DUE,collector=collect)["requests"],1)
        tick(self.l,"m","2026-10-20T21:56:00Z",collector=collect)
        tick(self.l,"m",CUTOFF,collector=collect)
        self.assertEqual(len(calls),1)
        p=Policy("m")
        self.assertTrue((self.l.paper_rows(p.id,CUTOFF).status=="pending").all())

    def test_existing_quote_avoids_credit(self):
        self.quote()
        def fail():
            self.fail("Should reuse a qualifying stored quote")
        tick(self.l,"m",DUE,collector=fail)
        self.assertEqual(status(self.l,DUE)["accounted_used"],0)

    def test_missed_window_never_backfilled(self):
        def fail():
            self.fail("Missed window must not trigger late collection")
        tick(self.l,"m","2026-10-20T22:01:00Z",collector=fail)
        slots=self.l.rows("SELECT * FROM scheduler_slots WHERE horizon=60")
        self.assertEqual(slots[0]["status"],"missed")

    def test_failure_no_automatic_retry(self):
        calls=[]
        def fail():
            calls.append(1)
            raise RuntimeError("network unavailable")
        tick(self.l,"m",DUE,collector=fail)
        tick(self.l,"m","2026-10-20T21:56:00Z",collector=fail)
        self.assertEqual(len(calls),1)

    def test_waiting_game_not_collected(self):
        self.previous("2026-10-20T22:00:00Z")
        def fail():
            self.fail("Waiting game should not trigger collection")
        tick(self.l,"m",DUE,collector=fail)

    def test_dry_plan_never_reserves_credits(self):
        self.assertTrue(any(p["status"]=="due" for p in plan(self.l,"m",DUE)))
        self.assertEqual(len(self.l.rows("SELECT * FROM api_requests")),0)
        self.assertEqual(len(self.l.rows("SELECT * FROM scheduler_slots")),0)

    def test_migration_preserves_existing_records(self):
        self.l.db.execute("PRAGMA user_version=1")
        self.l.db.commit()
        with Ledger(self.path) as other:
            self.assertEqual(other.db.execute("PRAGMA user_version").fetchone()[0],2)
            self.assertEqual(len(other.rows("SELECT * FROM forecasts")),1)

    def test_full_schedule_import_retains_prior_final_blocker(self):
        frame=pd.DataFrame([dict(GAME_ID="0022600001",HOME_TEAM="NYK",AWAY_TEAM="PHI",
            TIPOFF_AT="2026-10-19T23:00:00Z",STATUS_ID=3,STATUS="Final")])
        record_schedule(self.l,frame,["Regular Season"],ISSUE)
        self.assertEqual(self.assess()["status"],"awaiting_data")
        self.assertEqual(self.l.rows("SELECT status FROM schedules WHERE game_id='0022600001'")[0]["status"],"final")

    def test_primary_batch_for_two_games(self):
        # Use a distinct team pair and snapshot to avoid an artificial same-time blocker.
        extra=pd.DataFrame([dict(GAME_ID="old",TEAM_ABBREVIATION=t,GAME_DATE="2026-10-18",PTS=100) for t in ["LAL","GSW"]])
        self.l.record_history(extra,"other-history")
        d={**self.details,"game_id":"other","home":"LAL","away":"GSW","snapshot_hash":"other-history"}
        self.l.forecast(d,{"model_id":"m"},ISSUE)
        calls=[]
        def collect():
            calls.append(1)
            self.quote()
            self.l.quote("other","draftkings",DUE,START,1.8,2.1,DUE)
        tick(self.l,"m",DUE,collector=collect)
        self.assertEqual(len(calls),1)
        self.assertEqual(len(self.l.rows("SELECT * FROM scheduler_slots WHERE horizon=60 AND status='collected'")),2)

    def test_secondary_reserves_primary_daily_capacity(self):
        when="2026-10-20T16:55:00Z"
        self.l.forecast({**self.details,"game_id":"early","issued_at":"2026-10-20T15:00:00Z"},{"model_id":"m"},"2026-10-20T15:00:00Z")
        self.assertTrue(any(p["status"]=="due" and p["eligibility"]=="eligible" for p in plan(self.l,"m",when)))
        for _ in range(11):
            key=reserve(self.l,"manual",when)
            finish(self.l,key,success=True,as_of=when)
        def fail():
            self.fail("Last daily credit is reserved for the primary horizon")
        tick(self.l,"m",when,collector=fail)
        self.assertEqual(status(self.l,when)["daily_left"],1)

    def test_daily_refresh_pins_weights_and_includes_new_season(self):
        from types import SimpleNamespace
        from provenance import implementation_id, runtime_versions
        result=SimpleNamespace(metadata=dict(model_id="m",implementation_id=implementation_id(),runtime=runtime_versions(),snapshot_hash="s"),
            seasons=["2025-26"],warmup_seasons=[],season_types=["Regular Season"],history=self.history)
        config=__import__('production').load_production_config()
        when="2026-10-20T12:00:00Z"
        with patch("modeling.load_training_result",return_value=result), patch("modeling.update_prediction_history",return_value=result) as update, \
             patch("data.load_game_logs",return_value=self.history) as logs, patch("production.upcoming_games",return_value=[]), \
             patch("production.load_production_config",return_value=config), patch("scheduler.utc",side_effect=lambda value=None:utc(value or when)), \
             patch.object(self.l,"record_history"),patch.object(self.l,"settle_logs"), \
             patch("config.PROCESSED_DIR",Path(self.temp.name)):
            refresh_tracking(self.l,"fake.joblib","fake.txt")
            refresh_tracking(self.l,"fake.joblib","fake.txt")
        self.assertEqual(update.call_count,1)
        self.assertEqual(logs.call_count,2)
        self.assertEqual(logs.call_args_list[0].kwargs["refresh"],False)
        self.assertEqual(logs.call_args_list[1].args[0],["2026-27"])
        self.assertEqual(logs.call_args_list[1].kwargs["refresh"],True)

    def test_month_reset_does_not_reset_same_eastern_day(self):
        when="2026-10-31T23:30:00Z"
        for _ in range(12):
            key=reserve(self.l,"test",when)
            finish(self.l,key,success=True,as_of=when)
        self.assertEqual(status(self.l,"2026-11-01T00:00:00Z")["daily_left"],0)
        with self.assertRaises(BudgetError):
            reserve(self.l,"new_month_same_day","2026-11-01T00:00:00Z")

    def test_scheduler_lease_prevents_duplicate_worker(self):
        with self.l.db:
            self.l.db.execute("INSERT INTO scheduler_leases VALUES('tick','another-worker',?)",(utc(CUTOFF),))
        self.assertEqual(tick(self.l,"m",DUE)["status"],"busy")

    def test_replaced_model_file_stops_refresh(self):
        from types import SimpleNamespace
        with patch("modeling.load_training_result",return_value=SimpleNamespace(metadata={"model_id":"replaced"})):
            with self.assertRaisesRegex(ValueError,"replaced"):
                refresh_tracking(self.l,"fake.joblib","fake.txt",expected_model_id="m")

    def test_late_historical_schedule_is_not_a_prospective_opportunity(self):
        self.l.game("historical","LAL","GSW")
        self.l.schedule("historical","2026-10-19T23:00:00Z",ISSUE,"final")
        self.assertFalse(any(p["game_id"]=="historical" for p in plan(self.l,"m",DUE)))
        self.l.decide(Policy("m"),CUTOFF)
        self.assertEqual(self.l.rows("SELECT * FROM decisions WHERE game_id='historical'"),[])


if __name__ == "__main__":
    unittest.main()
