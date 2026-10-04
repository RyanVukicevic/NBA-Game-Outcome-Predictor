import unittest

import pandas as pd

from test_forecast_foundation import fixture_logs
from data import reconcile_schedule_roles, completed_game_logs
from elo import add_elo_features
from modeling import load_or_build_model_frames


class NeutralGamesTests(unittest.TestCase):
    def setUp(self):
        self.logs = fixture_logs(3)
        self.logs.loc[self.logs.GAME_ID == '1', 'MATCHUP'] = ['BOS @ NYK', 'NYK @ BOS']
        self.schedule = pd.DataFrame([dict(gameId=str(i), homeTeam_teamTricode='BOS',
                                           awayTeam_teamTricode='NYK', isNeutral=i == 1) for i in range(3)])

    def test_preserves_source_and_keeps_completed_neutral_rows(self):
        resolved = reconcile_schedule_roles(self.logs, self.schedule)
        self.assertEqual(resolved.MATCHUP.tolist(), self.logs.MATCHUP.tolist())
        self.assertEqual(len(completed_game_logs(resolved, '2025-01-01')), 6)
        self.assertEqual(resolved.IS_NEUTRAL.sum(), 2)
        self.assertEqual(resolved.groupby('GAME_ID').IS_HOME.sum().tolist(), [1, 1, 1])

    def test_rejects_wrong_teams_and_unconfirmed_neutral(self):
        for column, value in [('homeTeam_teamTricode', 'LAL'), ('isNeutral', False)]:
            schedule = self.schedule.copy()
            schedule.loc[1, column] = value
            with self.assertRaises(ValueError):
                reconcile_schedule_roles(self.logs, schedule)

    def test_neutral_elo_has_no_home_bonus(self):
        frame = pd.DataFrame([dict(GAME_ID='1', GAME_DATE='2024-11-01', HOME_TEAM='BOS',
                                   AWAY_TEAM='NYK', HOME_WIN=1, IS_NEUTRAL=True)])
        games, ratings = add_elo_features(frame, home_advantage=100, k_factor=20)
        self.assertEqual(games.iloc[0].elo_expected_home_win, .5)
        self.assertEqual(ratings.loc['BOS', 'ELO'], 1510)

    def test_settlement_uses_designated_home_not_matchup_text(self):
        from tracking import Ledger
        resolved = reconcile_schedule_roles(self.logs, self.schedule)
        with Ledger(':memory:') as ledger:
            ledger.game('0000000001', 'BOS', 'NYK')
            self.assertEqual(ledger.settle_logs(resolved, '2025-01-01T00:00:00Z'), 1)
            result = ledger.rows('SELECT * FROM results')[0]
            self.assertEqual(result['home_score'], 102)
            self.assertEqual(result['away_score'], 103)

    def test_neutral_history_retained_but_not_training_target(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        resolved = reconcile_schedule_roles(self.logs, self.schedule)
        with tempfile.TemporaryDirectory() as directory, patch('modeling.PROCESSED_CACHE_DIR', Path(directory)):
            teams, matchups, _ = load_or_build_model_frames(
                seasons=['2024-25'], rolling_window=1, min_periods=1, feature_set='deltas',
                game_logs=resolved, as_of='2025-01-01', use_elo=True)
        self.assertEqual(len(teams.attrs['history']), 6)
        self.assertNotIn('0000000001', matchups.full.GAME_ID.tolist())
        self.assertIn('0000000002', matchups.full.GAME_ID.tolist())


if __name__ == '__main__':
    unittest.main()
