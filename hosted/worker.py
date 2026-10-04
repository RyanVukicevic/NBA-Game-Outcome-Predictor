"""One durable, bounded Courtside collection and publication cycle."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dashboard.export_static import export_snapshot
from dashboard.service import Dashboard
from production import load_production_config, production_model_path
from scheduler import plan, refresh_tracking, tick
from tracking import Ledger, utc

from hosted.notifications import EmailNotifier, deliver_locked_alerts
from hosted.state import StateStore


CORE_TABLES = ("schedules", "forecasts", "odds", "results", "decisions", "eligibility", "scheduler_slots")


def core_fingerprint(ledger) -> str:
    state = {}
    for table in CORE_TABLES:
        row = ledger.rows(f"SELECT COUNT(*) AS count,MAX(rowid) AS latest FROM {table}")[0]
        state[table] = row
    return hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()


def next_wake(plans, now=None) -> str:
    current = pd.Timestamp(utc(now))
    future = [pd.Timestamp(item["due"]) for item in plans
              if item["status"] == "scheduled" and pd.Timestamp(item["due"]) > current]
    return utc(min(future) if future else current + pd.Timedelta(hours=6))


def should_publish(changed: bool, last_published, now=None, maximum_age_hours=6) -> bool:
    if changed or not last_published:
        return True
    return pd.Timestamp(utc(now)) - pd.Timestamp(last_published) >= pd.Timedelta(hours=maximum_age_hours)


def publish_pages(output: Path) -> None:
    project = os.environ.get("CLOUDFLARE_PAGES_PROJECT", "courtside-nba")
    required = ("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise RuntimeError("Missing Pages publication configuration: " + ", ".join(missing))
    subprocess.run([os.environ.get("WRANGLER_BIN", "wrangler"), "pages", "deploy", str(output),
                    "--project-name", project, "--branch", "main", "--commit-dirty=true"],
                   check=True, timeout=300, capture_output=True, text=True)


class HostedWorker:
    def __init__(self, store=None, publisher=publish_pages, notifier=None,
                 root=ROOT, work_dir=Path("/tmp/courtside")):
        self.store = store or StateStore.from_env()
        self.publisher = publisher
        self.notifier = notifier if notifier is not None else EmailNotifier.from_env()
        self.root = Path(root)
        self.work_dir = Path(work_dir)

    def run(self) -> dict:
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.store.restore_runtime(self.root)
        db_path = self.work_dir / "predictions.sqlite3"
        output = self.work_dir / "public"
        self.store.restore_ledger(db_path)
        config_path = self.root / "production_config.txt"
        model_path = production_model_path(load_production_config(config_path))
        revision = os.environ.get("K_REVISION", os.environ.get("COURTSIDE_BUILD", "local-build"))
        now = utc()
        run_id = None
        publish_due = False
        try:
            with Ledger(db_path) as ledger:
                previous = ledger.rows("SELECT MAX(published) AS stamp FROM worker_runs")[0]["stamp"]
                before = core_fingerprint(ledger)
                run_id = ledger.start_worker_run(revision, now)
                import joblib
                model_id = joblib.load(model_path).metadata["model_id"]
                outcome = tick(ledger, model_id, refresh=lambda: refresh_tracking(
                    ledger, model_path, config_path, expected_model_id=model_id))
                alerts = deliver_locked_alerts(ledger, self.notifier)
                plans = plan(ledger, model_id)
                wake = next_wake(plans)
                changed = before != core_fingerprint(ledger)
                publish_due = should_publish(changed, previous)
                summary = json.dumps({"tick": outcome["status"], "requests": outcome["requests"],
                                      "decisions": outcome["decisions"], "alerts": alerts["sent"]},
                                     sort_keys=True, separators=(",", ":"))
                ledger.finish_worker_run(run_id, "success", summary, next_wake=wake)

            # Persist collection before attempting publication; a failed deploy cannot lose quota history.
            self.store.save_ledger(db_path)
            published = None
            if publish_due and os.environ.get("PUBLISH_PAGES", "true").lower() != "false":
                export_snapshot(output, Dashboard(db_path, model_path))
                self.publisher(output)
                published = utc()
                with Ledger(db_path) as ledger:
                    ledger.mark_worker_published(run_id, published)
                self.store.save_ledger(db_path)
            return {"status": "success", "run_id": run_id, "published": published,
                    "publish_due": publish_due, "next_wake": wake, "outcome": outcome,
                    "alerts": alerts}
        except Exception as exc:
            if run_id and db_path.exists():
                try:
                    with Ledger(db_path) as ledger:
                        row = ledger.rows("SELECT status FROM worker_runs WHERE id=?", (run_id,))
                        if row and row[0]["status"] == "running":
                            ledger.finish_worker_run(run_id, "failed", type(exc).__name__)
                        elif row and row[0]["status"] == "success":
                            ledger.degrade_worker_run(run_id, type(exc).__name__)
                    self.store.save_ledger(db_path)
                except Exception:
                    pass
            raise RuntimeError(f"Hosted cycle failed during {type(exc).__name__}.") from None
