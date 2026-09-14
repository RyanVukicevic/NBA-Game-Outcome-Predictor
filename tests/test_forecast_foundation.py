import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from config import BOX_SCORE_COLUMNS
from data import completed_game_logs
from evaluation import holdout_split_index
from features import forecast_team_features
from modeling import train_model, load_or_build_model_frames, save_training_result, load_training_result, update_prediction_history
from prediction import predict_matchup_details, prediction_inputs
from dataclasses import replace
from production import load_production_config, load_or_train_production_model


def fixture_logs(count=80):
    rows = []
    for i in range(count):
        home_wins = i % 3 != 0
        for team, other, ident, home in [("BOS", "NYK", 1, True), ("NYK", "BOS", 2, False)]:
            win = home_wins if home else not home_wins
            row = {c: float(10 + i % 7) for c in BOX_SCORE_COLUMNS}
            row.update(GAME_ID=str(i), TEAM_ID=ident, TEAM_ABBREVIATION=team,
                       GAME_DATE=pd.Timestamp("2024-11-01") + pd.Timedelta(days=i),
                       SEASON_ID=22024, SEASON_TYPE="Regular Season",
                       MATCHUP=f"{team} {'vs.' if home else '@'} {other}",
                       WL="W" if win else "L", WIN=float(win), PTS=100.0 + i + ident,
                       PLUS_MINUS=10.0 if win else -10.0)
            rows.append(row)
    return pd.DataFrame(rows)


class FoundationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cache = Path(self.temp.name)
        self.patcher = patch("modeling.PROCESSED_CACHE_DIR", self.cache)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.logs = fixture_logs()
        self.settings = dict(seasons=["2024-25"], rolling_window=3, min_periods=1,
                             use_elo=True, as_of="2025-04-01", game_logs=self.logs)

    def test_cutoff_incomplete_and_duplicate_rows(self):
        cutoff = "2024-11-10"
        history = completed_game_logs(self.logs, cutoff)
        self.assertEqual(len(history), 18)
        self.assertLess(history.GAME_DATE.max(), pd.Timestamp(cutoff))
        changed = self.logs.copy()
        changed.loc[changed.GAME_ID == "0", "WL"] = None
        self.assertEqual(len(completed_game_logs(changed, cutoff)), 16)
        with self.assertRaises(ValueError):
            completed_game_logs(pd.concat([self.logs, self.logs.iloc[:1]]), cutoff)

    def test_latest_completed_game_enters_window_and_schedule_rest(self):
        result = train_model(**self.settings)
        x, context = prediction_inputs(result, "BOS", "NYK", game_date="2025-04-02", as_of="2025-04-01",
                                       rest_dates={"BOS": "2025-04-01", "NYK": "2025-03-30"})
        post = forecast_team_features(result.history, 3, "2025-04-02")
        expected = self.logs.loc[self.logs.TEAM_ABBREVIATION == "BOS", "PTS"].tail(3).mean()
        self.assertEqual(post.loc["BOS", "ROLLING_3_PTS"], expected)
        self.assertEqual(x.iloc[0]["HOME_REST_DAYS"], 1)
        self.assertEqual(x.iloc[0]["AWAY_REST_DAYS"], 3)
        self.assertEqual(context["rest_reference_dates"]["BOS"], "2025-04-01")

    def test_deployment_refit_and_roundtrip_reproduction(self):
        result = train_model(**self.settings)
        self.assertEqual(result.model.named_steps["scale"].n_samples_seen_, result.metrics["train_games"])
        self.assertEqual(result.deployment_model.named_steps["scale"].n_samples_seen_, result.metadata["deployment_games"])
        self.assertGreater(result.metadata["deployment_games"], result.metrics["train_games"])
        self.assertLess(result.metadata["evaluation_train_end"], result.metadata["holdout_start"])
        before = predict_matchup_details(result, "BOS", "NYK", as_of="2025-04-01")
        path = self.cache / "saved.joblib"
        save_training_result(result, path)
        self.assertTrue(path.with_suffix(".json").exists())
        after = predict_matchup_details(load_training_result(path), "BOS", "NYK", as_of="2025-04-01")
        self.assertEqual(before, after)

    def test_future_results_cannot_affect_training(self):
        options = {**self.settings, "as_of": "2025-01-01"}
        before = train_model(**options)
        changed = self.logs.copy()
        changed.loc[changed.GAME_DATE >= pd.Timestamp("2025-01-01"), "PTS"] = 9999
        after = train_model(**{**options, "game_logs": changed})
        self.assertEqual(before.metadata["model_id"], after.metadata["model_id"])
        np.testing.assert_array_equal(before.deployment_model.named_steps["logistic"].coef_, after.deployment_model.named_steps["logistic"].coef_)
        with self.assertRaisesRegex(ValueError, "cutoff"):
            predict_matchup_details(before, "BOS", "NYK", as_of="2024-12-15")

    def test_elo_sees_games_removed_by_min_periods(self):
        _, first, elo1 = load_or_build_model_frames(**self.settings, feature_set="deltas")
        _, second, elo2 = load_or_build_model_frames(**{**self.settings, "min_periods": 3}, feature_set="deltas")
        pd.testing.assert_frame_equal(elo1, elo2)
        self.assertGreater(len(first.x), len(second.x))
        self.assertNotEqual(second.full.iloc[0]["home_elo_pre"], 1500)

    def test_live_inputs_match_historical_pregame_row(self):
        target = pd.Timestamp("2024-11-01") + pd.Timedelta(days=70)
        result = train_model(**{**self.settings, "as_of": target})
        live, _ = prediction_inputs(result, "BOS", "NYK", as_of=target, game_date=target)
        _, frames, _ = load_or_build_model_frames(**self.settings, feature_set="deltas")
        historical = frames.full.loc[frames.full.GAME_DATE == target, result.feature_names].fillna(0)
        np.testing.assert_allclose(live.to_numpy(), historical.to_numpy())

    def test_changed_data_invalidates_cache_and_snapshot_not_weights(self):
        result = train_model(**self.settings)
        old_id = result.metadata["model_id"]
        updated = update_prediction_history(result, fixture_logs(81), "2025-04-01")
        self.assertEqual(updated.metadata["model_id"], old_id)
        self.assertIs(updated.deployment_model, result.deployment_model)
        self.assertNotEqual(updated.metadata["snapshot_hash"], result.metadata["snapshot_hash"])
        changed = self.logs.copy()
        changed.loc[0, "PTS"] += 1
        other = train_model(**{**self.settings, "game_logs": changed})
        self.assertNotEqual(other.metadata["model_id"], old_id)
        self.assertEqual(len(list(self.cache.glob("frames_*.joblib"))), 2)

    def test_season_rollover_fallback_and_elo_regression(self):
        result = train_model(**self.settings)
        x, context = prediction_inputs(result, "BOS", "NYK", game_date="2025-10-20", as_of="2025-09-01")
        self.assertTrue(context["history_fallback"])
        expected = result.elo_carryover * (result.latest_elos.loc["BOS", "ELO"] - result.latest_elos.loc["NYK", "ELO"])
        self.assertAlmostEqual(x.iloc[0]["diff_elo_pre"], expected)
        self.assertEqual(x.iloc[0]["diff_elo_change_last_3"], 0)

    def test_holdout_keeps_whole_dates(self):
        self.assertEqual(holdout_split_index(pd.to_datetime(["2025-01-01"] * 4 + ["2025-01-02"] * 6)), 4)

    def test_production_advances_snapshot_without_retraining(self):
        config = replace(load_production_config(), seasons=["2024-25"], season_types=["Regular Season"],
                         rolling_window=3, min_periods=1)
        path = self.cache / "production.joblib"
        with patch("production.production_model_path", return_value=path), patch("production.load_game_logs", return_value=self.logs):
            first, _, trained = load_or_train_production_model(config)
        self.assertTrue(trained)
        with patch("production.production_model_path", return_value=path), patch("production.load_game_logs", return_value=fixture_logs(81)), patch("production.train_model") as fit:
            second, _, trained = load_or_train_production_model(config)
        self.assertFalse(trained)
        fit.assert_not_called()
        self.assertEqual(first.metadata["model_id"], second.metadata["model_id"])
        self.assertNotEqual(first.metadata["snapshot_hash"], second.metadata["snapshot_hash"])
        np.testing.assert_array_equal(first.deployment_model.named_steps["logistic"].coef_, second.deployment_model.named_steps["logistic"].coef_)


if __name__ == "__main__":
    unittest.main()
