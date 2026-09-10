import contextlib
import io
import sys
import unittest
from dataclasses import replace
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from production import load_production_config, run_production_predictions, upcoming_games


class ScheduleTests(unittest.TestCase):
    def test_cross_season_window_filters_and_deduplicates(self):
        config = replace(load_production_config(), upcoming_days=90)
        rows = pd.DataFrame([
            [date(2026, 9, 8), "BOS", "NYK", "", ""],
            [date(2026, 9, 9), "BOS", "NYK", "", ""],
            [date(2026, 10, 4), "BOS", "NYK", "Preseason", ""],
            [date(2026, 10, 5), "BOS", "LON", "", ""],
            [date(2026, 10, 20), "BOS", "NYK", "", ""],
            [date(2026, 10, 21), "BOS", "NYK", "", "Final"],
            [date(2026, 12, 8), "BOS", "NYK", "", ""],
            [date(2026, 12, 9), "BOS", "NYK", "", ""],
        ], columns=["GAME_DATE", "HOME_TEAM", "AWAY_TEAM", "GAME_LABEL", "STATUS"])
        with patch("production.fetch_schedule_games", return_value=rows) as fetch:
            games = upcoming_games(config, today=date(2026, 9, 9))
        self.assertEqual([call.args[0] for call in fetch.call_args_list], ["2025-26", "2026-27"])
        self.assertEqual([g.game_date for g in games], [date(2026, 9, 9), date(2026, 10, 20), date(2026, 12, 8)])

    def test_playoff_context_and_zero_day_window(self):
        config = replace(load_production_config(), upcoming_days=0)
        rows = pd.DataFrame([dict(GAME_DATE=date(2026, 5, 1), HOME_TEAM="BOS", AWAY_TEAM="NYK", GAME_LABEL="Playoffs")])
        with patch("production.fetch_schedule_games", return_value=rows):
            games = upcoming_games(config, today=date(2026, 5, 1))
            self.assertTrue(games[0].is_playoffs)
            self.assertEqual(upcoming_games(replace(config, season_types=["Regular Season"]), today=date(2026, 5, 1)), [])

    def test_negative_window(self):
        with self.assertRaises(ValueError):
            upcoming_games(replace(load_production_config(), upcoming_days=-1))

    def test_empty_search_prints_window_without_loading_model(self):
        output = io.StringIO()
        with patch("production.date") as today, patch("production.upcoming_games", return_value=[]), patch("production.load_or_train_production_model") as model:
            today.today.return_value = date(2026, 9, 9)
            with contextlib.redirect_stdout(output):
                rows = run_production_predictions()
        self.assertTrue(rows.empty)
        model.assert_not_called()
        self.assertIn("90 days ahead of September 9, 2026 (through December 8, 2026)", output.getvalue())


if __name__ == "__main__":
    unittest.main()
