import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tracking import Ledger, Policy, decimal_odds, export_report, probability_report, summarize, utc
from odds import collect_odds, ingest_odds

START = "2026-10-20T23:00:00Z"
ISSUED = "2026-10-20T20:00:00Z"
CUTOFF = "2026-10-20T22:00:00Z"
QUOTE = "2026-10-20T21:55:00Z"
FINAL = "2026-10-21T03:00:00Z"


class TrackingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = Ledger(Path(self.tmp.name) / "test.sqlite3")
        self.policy = Policy("model-1")
        self.metadata = {"model_id": "model-1"}
        self.details = dict(game_id="0022600001", home="BOS", away="NYK", model_id="model-1",
                            home_win_probability=.65, issued_at=ISSUED, tipoff_at=START,
                            input_features={"diff_elo": 50})
        self.ledger.forecast(self.details, self.metadata, ISSUED)

    def tearDown(self):
        self.ledger.db.close()
        self.tmp.cleanup()

    def quote(self, **kwargs):
        params = dict(game_id="0022600001", bookmaker="draftkings", updated=QUOTE, tipoff=START,
                      home=1.8, away=2.1, received=QUOTE)
        params.update(kwargs)
        return self.ledger.quote(**params)

    def decision(self):
        return json.loads(self.ledger.rows("SELECT payload FROM decisions")[0]["payload"])

    def test_idempotency_and_immutable_predictions(self):
        self.ledger.forecast(self.details, self.metadata, ISSUED)
        self.assertEqual(len(self.ledger.rows("SELECT * FROM forecasts")), 1)
        self.ledger.forecast({**self.details, "home_win_probability": .7, "issued_at": QUOTE}, self.metadata, QUOTE)
        self.assertEqual(len(self.ledger.rows("SELECT * FROM forecasts")), 2)
        self.quote()
        self.quote()
        self.assertEqual(len(self.ledger.rows("SELECT * FROM odds")), 1)
        self.assertEqual(self.ledger.decide(self.policy, CUTOFF), 1)
        self.assertEqual(self.ledger.decide(self.policy, FINAL), 0)
        self.assertEqual(self.decision()["probability"], .7)

    def test_no_decision_before_cutoff_and_no_future_inputs(self):
        self.quote()
        self.ledger.forecast({**self.details, "home_win_probability": .99, "issued_at": "2026-10-20T22:05:00Z"},
                             self.metadata, "2026-10-20T22:05:00Z")
        self.quote(home=1.01, away=20, updated="2026-10-20T22:10:00Z", received="2026-10-20T22:10:00Z")
        self.assertEqual(self.ledger.decide(self.policy, QUOTE), 0)
        self.ledger.decide(self.policy, FINAL)
        self.assertEqual(self.decision()["probability"], .65)
        self.assertEqual(self.decision()["bets"]["favorite"]["odds"], 1.8)

    def test_late_import_is_not_available_at_cutoff(self):
        self.quote(updated=QUOTE, received="2026-10-20T22:01:00Z")
        self.ledger.decide(self.policy, FINAL)
        self.assertEqual(self.decision()["reason"], "missing_odds")

    def test_stale_and_unavailable_odds(self):
        self.quote(updated=ISSUED)
        self.ledger.decide(self.policy, CUTOFF)
        self.assertEqual(self.decision()["reason"], "stale_odds")
        other = Policy("model-1", minimum_ev=.04)
        self.quote(updated=QUOTE, status="unavailable")
        self.ledger.decide(other, CUTOFF)
        self.assertEqual(json.loads(self.ledger.rows("SELECT payload FROM decisions WHERE policy_id=?", (other.id,))[0]["payload"])["reason"], "odds_unavailable")

    def test_payout_and_shared_cohort(self):
        self.quote()
        self.ledger.decide(self.policy, CUTOFF)
        pending = self.ledger.paper_rows(self.policy.id, CUTOFF)
        self.assertTrue((pending.status == "pending").all())
        self.assertTrue(pending.profit.isna().all())
        self.ledger.result("0022600001", 110, 100, observed=FINAL)
        rows = self.ledger.paper_rows(self.policy.id, FINAL)
        self.assertTrue((rows.status == "win").all())
        self.assertTrue((rows.profit == 8).all())
        summary = summarize(rows)
        self.assertTrue((summary.roi == .8).all())
        metrics, _ = probability_report(rows)
        self.assertAlmostEqual(metrics.iloc[0].brier, .35**2)

    def test_result_corrections_keep_history_and_asof(self):
        self.quote()
        self.ledger.decide(self.policy, CUTOFF)
        self.ledger.result("0022600001", 110, 100, observed=FINAL)
        self.ledger.result("0022600001", 110, 100, observed=FINAL)
        self.ledger.result("0022600001", 100, 110, observed="2026-10-22T03:00:00Z")
        self.assertEqual(len(self.ledger.rows("SELECT * FROM results")), 2)
        self.assertTrue((self.ledger.paper_rows(self.policy.id, FINAL).status == "win").all())
        self.assertTrue((self.ledger.paper_rows(self.policy.id, "2026-10-23T00:00:00Z").profit == -10).all())

    def test_postponement_voids_without_reentry(self):
        self.quote()
        self.ledger.decide(self.policy, CUTOFF)
        self.ledger.schedule("0022600001", "2026-10-22T23:00:00Z", "2026-10-20T22:30:00Z", "postponed")
        self.assertTrue((self.ledger.paper_rows(self.policy.id, FINAL).status == "void").all())
        self.assertEqual(self.ledger.decide(self.policy, "2026-10-23T00:00:00Z"), 0)

    def test_changed_schedule_requires_new_forecast_and_quote(self):
        self.quote()
        self.ledger.schedule("0022600001", "2026-10-21T00:00:00Z", "2026-10-20T21:00:00Z")
        self.ledger.decide(self.policy, "2026-10-20T23:00:00Z")
        self.assertEqual(self.decision()["reason"], "prediction_schedule_mismatch")

    def test_ties_and_value_threshold(self):
        self.quote(home=1.9, away=1.9)
        self.ledger.decide(Policy("model-1", minimum_ev=.5), CUTOFF)
        bets = self.decision()["bets"]
        self.assertIsNone(bets["favorite"]["selection"])
        self.assertIsNone(bets["model_value"]["selection"])
        self.assertEqual(bets["model_winner"]["selection"], "home")

    def test_validation(self):
        for odds in (1, 0, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                decimal_odds(odds)
        self.assertEqual(decimal_odds(-200, True), 1.5)
        self.assertEqual(decimal_odds(150, True), 2.5)
        with self.assertRaises(ValueError):
            utc("2026-10-20")
        with self.assertRaises(ValueError):
            self.quote(received=START)
        with self.assertRaises(ValueError):
            self.ledger.forecast({**self.details, "issued_at": START}, self.metadata, START)
        with self.assertRaises(ValueError):
            self.ledger.result("0022600001", 100, 100, observed=FINAL)
        with self.assertRaises(ValueError):
            Policy("model-1", stake=-10)

    def event(self):
        return dict(id="provider-1", sport_key="basketball_nba", commence_time=START,
                    home_team="Boston Celtics", away_team="New York Knicks", bookmakers=[dict(key="draftkings",
                    last_update=QUOTE, markets=[dict(key="h2h", outcomes=[dict(name="Boston Celtics", price=1.8),
                                                                            dict(name="New York Knicks", price=2.1)])])])

    def test_adapter_mapping_and_missing_book(self):
        stats = ingest_odds(self.ledger, [self.event()], QUOTE)
        self.assertEqual(stats["matched"], 1)
        self.assertEqual(stats["quotes"], 1)
        self.assertEqual(stats["unavailable"], 1)
        stats = ingest_odds(self.ledger, [{**self.event(), "commence_time": "2026-10-21T00:00:00Z"}], QUOTE)
        self.assertEqual(stats["unmatched"], 1)
        self.assertEqual(ingest_odds(self.ledger, [self.event()], START)["started"], 1)

    def test_api_key_absent_and_errors_redacted(self):
        session = Mock()
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "ODDS_API_KEY"):
                collect_odds(self.ledger, session=session)
        session.get.assert_not_called()
        session.get.side_effect = requests.ConnectionError("https://example.com?apiKey=SECRET")
        with patch.dict(os.environ, {"ODDS_API_KEY": "SECRET"}):
            with self.assertRaises(RuntimeError) as error:
                collect_odds(self.ledger, session=session)
        self.assertNotIn("SECRET", str(error.exception))

    def test_actual_receipts_are_separate_and_immutable(self):
        key = self.ledger.record_actual("0022600001", "draftkings", "receipt-1", "home", 1.8, 10, QUOTE, QUOTE)
        self.ledger.record_actual("0022600001", "draftkings", "receipt-1", "home", 1.8, 10, QUOTE, FINAL)
        with self.assertRaises(ValueError):
            self.ledger.record_actual("0022600001", "draftkings", "receipt-1", "home", 2.0, 10, QUOTE, FINAL)
        self.ledger.settle_actual(key, "win", 18, "settlement-1", FINAL)
        self.assertEqual(len(self.ledger.rows("SELECT * FROM actual_wagers")), 1)
        self.assertEqual(len(self.ledger.rows("SELECT * FROM decisions")), 0)
        with self.assertRaises(ValueError):
            self.ledger.record_actual("0022600001", "draftkings", "late", "home", 1.8, 10, START, FINAL)
        with self.assertRaises(ValueError):
            self.ledger.settle_actual(key, "loss", 18, "bad", FINAL)

    def test_empty_report_exports(self):
        self.ledger.decide(self.policy, ISSUED)
        result = export_report(self.ledger, self.policy.id, Path(self.tmp.name) / "report", CUTOFF)
        self.assertTrue((result.bets == 0).all())
        self.assertTrue((Path(self.tmp.name) / "report" / "paper_profit.png").exists())

    def test_missing_event_invalidates_previous_quote(self):
        self.quote()
        ingest_odds(self.ledger, [], "2026-10-20T21:59:00Z", ("draftkings",))
        self.ledger.decide(self.policy, CUTOFF)
        self.assertEqual(self.decision()["reason"], "odds_unavailable")

    def test_archived_forecast_cannot_restore_old_schedule(self):
        self.ledger.schedule("0022600001", "2026-10-22T23:00:00Z", QUOTE)
        self.ledger.forecast(self.details, self.metadata, FINAL)
        self.assertEqual(self.ledger.rows("SELECT * FROM schedules ORDER BY seq DESC LIMIT 1")[0]["tipoff"], utc("2026-10-22T23:00:00Z"))

    def test_pin_model_and_prediction_freshness(self):
        self.quote()
        self.ledger.decide(Policy("different-model"), CUTOFF)
        self.assertEqual(self.decision()["reason"], "missing_prediction")
        policy = Policy("model-1", prediction_max_age_hours=1)
        self.ledger.decide(policy, CUTOFF)
        d = json.loads(self.ledger.rows("SELECT payload FROM decisions WHERE policy_id=?", (policy.id,))[0]["payload"])
        self.assertEqual(d["reason"], "stale_prediction")

    def test_fractional_timestamp_ordering(self):
        self.assertLess(utc("2026-01-01T00:00:00Z"), utc("2026-01-01T00:00:00.1Z"))
        self.assertEqual(utc("2026-01-01T01:00:00+01:00"), utc("2026-01-01T00:00:00Z"))

    def test_settlement_from_completed_nba_logs(self):
        logs = pd.DataFrame([
            dict(GAME_ID="0022600001", GAME_DATE="2026-10-20", TEAM_ID=1, TEAM_ABBREVIATION="BOS", MATCHUP="BOS vs. NYK", WL="W", PTS=110),
            dict(GAME_ID="0022600001", GAME_DATE="2026-10-20", TEAM_ID=2, TEAM_ABBREVIATION="NYK", MATCHUP="NYK @ BOS", WL="L", PTS=100)])
        self.assertEqual(self.ledger.settle_logs(logs, "2026-10-22T03:00:00Z"), 1)
        self.assertEqual(self.ledger.rows("SELECT * FROM results")[0]["home_score"], 110)

    def test_synthetic_report_and_actual_exports(self):
        from tracking_demo import run_tracking_demo
        output = Path(self.tmp.name) / "synthetic"
        summary, rows = run_tracking_demo(output)
        self.assertEqual(len(rows), 120)
        self.assertEqual(len(summary), 4)
        self.assertTrue(summary.roi_ci_low.notna().all())
        self.assertEqual(len(pd.read_csv(output / "probability_metrics.csv")), 2)
        self.assertGreater(len(pd.read_csv(output / "period_summary.csv")), 0)
        self.assertTrue(json.loads((output / "report.json").read_text())["synthetic"])

    def test_mocked_successful_api_contract(self):
        response = Mock(status_code=200, headers={"x-requests-remaining": "499"})
        response.json.return_value = [self.event()]
        session = Mock()
        session.get.return_value = response
        with patch.dict(os.environ, {"ODDS_API_KEY": "TEST"}), patch("odds.utc", side_effect=lambda value=None: utc(value or QUOTE)):
            stats = collect_odds(self.ledger, session=session)
        self.assertEqual(stats["credits_remaining"], "499")
        self.assertEqual(stats["quotes"], 1)
        self.assertEqual(session.get.call_args.kwargs["params"]["markets"], "h2h")
        self.assertEqual(session.get.call_args.kwargs["params"]["oddsFormat"], "decimal")


if __name__ == "__main__":
    unittest.main()
