"""One local prediction cycle, suitable for a Windows scheduled task."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from production import CONFIG_PATH, load_production_config, production_model_path
from scheduler import plan, refresh_tracking, tick
from tracking import DB_PATH, Ledger


def run_cycle(db_path=DB_PATH, model_path=None, config_path=CONFIG_PATH):
    model_path = Path(model_path or production_model_path(load_production_config(config_path)))
    model_id = json.loads(model_path.with_suffix('.json').read_text())['model_id']
    with Ledger(db_path) as ledger:
        run_id = ledger.start_worker_run('local-windows-task')
        try:
            force = any(item['status'] == 'due' for item in plan(ledger, model_id))
            refresh_tracking(ledger, model_path, config_path, force=force, expected_model_id=model_id)
            if not os.environ.get('ODDS_API_KEY'):
                outcome = dict(status='degraded', reason='ODDS_API_KEY is missing; forecasts refresh but odds collection and paper decisions are paused')
            else:
                outcome = tick(ledger, model_id)
                if outcome.get('error') or not outcome.get('budget', {}).get('ready', False):
                    outcome['status'] = 'degraded'
            ledger.finish_worker_run(run_id, 'degraded' if outcome['status'] == 'degraded' else 'success',
                                     json.dumps(outcome, sort_keys=True))
            return outcome
        except Exception as exc:
            ledger.finish_worker_run(run_id, 'failed', type(exc).__name__)
            raise RuntimeError(f'Local prediction cycle failed: {type(exc).__name__}.') from None


if __name__ == '__main__':
    print(json.dumps(run_cycle()), flush=True)
