from __future__ import annotations

import gzip
import io
import json
from pathlib import Path
import sqlite3
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from hosted.notifications import deliver_locked_alerts, qualifying_decisions
from hosted.state import StateStore, build_runtime_bundle
from hosted.worker import next_wake, should_publish
from tracking import Ledger, Policy


class Body:
    def __init__(self, value): self.value = value
    def read(self): return self.value


class MemoryS3:
    def __init__(self): self.objects = {}
    def put_object(self, Bucket, Key, Body, **kwargs): self.objects[(Bucket, Key)] = bytes(Body)
    def get_object(self, Bucket, Key): return {"Body": Body(self.objects[(Bucket, Key)])}


class FakeNotifier:
    recipient = "owner@example.com"
    def __init__(self): self.alerts = []
    def send(self, alert, event_key):
        self.alerts.append((alert, event_key))
        return "provider-id"


class HostedTests(unittest.TestCase):
    def test_ledger_round_trip_is_compressed_and_integrity_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            source, restored = Path(directory) / "source.sqlite3", Path(directory) / "restored.sqlite3"
            db = sqlite3.connect(source)
            try:
                db.execute("CREATE TABLE sample(value TEXT)")
                db.execute("INSERT INTO sample VALUES('ok')")
                db.commit()
            finally:
                db.close()
            client = MemoryS3()
            store = StateStore(client, "bucket", "test")
            store.save_ledger(source)
            self.assertLess(len(client.objects[("bucket", "test/state/predictions.sqlite3.gz")]), source.stat().st_size)
            store.restore_ledger(restored)
            db = sqlite3.connect(restored)
            try:
                self.assertEqual(db.execute("SELECT value FROM sample").fetchone()[0], "ok")
            finally:
                db.close()

    def test_runtime_bundle_is_verified_after_restore(self):
        with tempfile.TemporaryDirectory() as directory:
            root, target = Path(directory) / "source", Path(directory) / "target"
            model = root / "models" / "production.joblib"
            raw = root / "data" / "raw" / "history.csv"
            model.parent.mkdir(parents=True)
            raw.parent.mkdir(parents=True)
            model.write_bytes(b"model")
            model.with_suffix(".json").write_text('{"model_id":"m"}', encoding="utf-8")
            raw.write_text("a,b\n1,2\n", encoding="ascii")
            payload = build_runtime_bundle(root, model)
            client = MemoryS3()
            client.objects[("bucket", "test/state/runtime.tar.gz")] = payload
            manifest = StateStore(client, "bucket", "test").restore_runtime(target)
            self.assertEqual(len(manifest["files"]), 3)
            self.assertEqual((target / "models" / "production.joblib").read_bytes(), b"model")

    def test_runtime_restore_rejects_path_traversal(self):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            info = tarfile.TarInfo("../outside")
            info.size = 1
            archive.addfile(info, io.BytesIO(b"x"))
        client = MemoryS3()
        client.objects[("bucket", "test/state/runtime.tar.gz")] = buffer.getvalue()
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(RuntimeError):
            StateStore(client, "bucket", "test").restore_runtime(Path(directory))

    def test_only_qualifying_locked_decisions_send_once(self):
        with tempfile.TemporaryDirectory() as directory, Ledger(Path(directory) / "ledger.sqlite3") as ledger:
            issued, cutoff, tipoff = "2026-10-20T20:00:00Z", "2026-10-20T22:00:00Z", "2026-10-20T23:00:00Z"
            ledger.forecast(dict(game_id="g", home="BOS", away="NYK", model_id="m", issued_at=issued,
                tipoff_at=tipoff, home_win_probability=.70, snapshot_hash="s"), {"model_id": "m"}, issued)
            ledger.quote("g", "draftkings", cutoff, tipoff, 1.8, 2.1, cutoff)
            ledger.decide(Policy("m", version=1), cutoff)
            self.assertEqual(qualifying_decisions(ledger)[0]["team"], "BOS")
            notifier = FakeNotifier()
            first = deliver_locked_alerts(ledger, notifier, cutoff)
            second = deliver_locked_alerts(ledger, notifier, cutoff)
            self.assertEqual(first["sent"], 1)
            self.assertEqual(second["sent"], 0)
            self.assertEqual(len(notifier.alerts), 1)

    def test_publish_cadence_and_next_wake(self):
        now = "2026-10-20T20:00:00Z"
        plans = [{"status": "scheduled", "due": "2026-10-20T22:00:00Z"},
                 {"status": "scheduled", "due": "2026-10-20T21:00:00Z"}]
        self.assertEqual(next_wake(plans, now), "2026-10-20T21:00:00.000000+00:00")
        self.assertFalse(should_publish(False, "2026-10-20T18:00:00Z", now))
        self.assertTrue(should_publish(False, "2026-10-20T12:00:00Z", now))
        self.assertTrue(should_publish(True, "2026-10-20T19:59:00Z", now))


if __name__ == "__main__":
    unittest.main()
