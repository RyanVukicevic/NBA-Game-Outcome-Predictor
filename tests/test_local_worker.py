import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hosted.local_worker import run_cycle
from tracking import Ledger


class LocalWorkerTests(unittest.TestCase):
    def test_missing_key_records_degraded_without_attempting_odds_or_decisions(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / 'ledger.sqlite3'
            model = Path(directory) / 'model.joblib'
            model.with_suffix('.json').write_text(json.dumps({'model_id': 'm'}))
            with patch.dict('os.environ', {}, clear=True), patch('hosted.local_worker.plan', return_value=[]), \
                 patch('hosted.local_worker.refresh_tracking') as refresh, patch('hosted.local_worker.tick') as tick:
                outcome = run_cycle(db, model)
                refresh.assert_called_once()
                tick.assert_not_called()
                self.assertEqual(outcome['status'], 'degraded')
            with Ledger(db) as ledger:
                self.assertEqual(ledger.rows('SELECT status FROM worker_runs')[0]['status'], 'degraded')
                self.assertEqual(ledger.rows('SELECT COUNT(*) AS n FROM api_requests')[0]['n'], 0)

    def test_configured_key_runs_existing_quota_guarded_scheduler(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / 'ledger.sqlite3'
            model = Path(directory) / 'model.joblib'
            model.with_suffix('.json').write_text(json.dumps({'model_id': 'm'}))
            with patch.dict('os.environ', {'ODDS_API_KEY': 'fixture'}), \
                 patch('hosted.local_worker.plan', return_value=[]), patch('hosted.local_worker.refresh_tracking'), \
                 patch('hosted.local_worker.tick', return_value={'status': 'ok', 'budget': {'ready': True}}) as tick:
                run_cycle(db, model)
                tick.assert_called_once()
            with Ledger(db) as ledger:
                self.assertEqual(ledger.rows('SELECT status FROM worker_runs')[0]['status'], 'success')
