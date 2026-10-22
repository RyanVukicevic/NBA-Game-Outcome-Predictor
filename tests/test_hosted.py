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
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from hosted.notifications import deliver_locked_alerts, qualifying_decisions
from hosted.state import StateStore, build_runtime_bundle
from hosted.state import GoogleStateStore
from hosted.worker import next_wake, should_publish
from hosted.worker import HostedWorker
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
    def test_cloud_cycle_refreshes_due_inputs_and_persists_failed_collection_health(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'original.sqlite3'
            with Ledger(source) as ledger:
                ledger.game('preserved', 'BOS', 'NYK')
            store = StateStore(MemoryS3(), 'bucket')
            store.save_ledger(source)
            due = [{'status': 'due', 'due': '2026-10-20T20:00:00Z'}]
            def cycle(ledger, model_id, refresh):
                refresh()
                return dict(status='ok', requests=1, decisions=0, error='RuntimeError', budget={'ready': True})
            with patch.object(store, 'restore_runtime'), \
                 patch('hosted.worker.load_production_config'), \
                 patch('hosted.worker.production_model_path', return_value=root/'model.joblib'), \
                 patch('joblib.load', return_value=SimpleNamespace(metadata={'model_id': 'm'})), \
                 patch('hosted.worker.plan', return_value=due), \
                 patch('hosted.worker.tick', side_effect=cycle), \
                 patch('hosted.worker.refresh_tracking') as refresh, \
                 patch.dict('os.environ', {'PUBLISH_PAGES': 'false'}):
                result = HostedWorker(store=store, root=root, work_dir=root/'work').run()
            self.assertEqual(result['status'], 'degraded')
            self.assertTrue(refresh.call_args.kwargs['force'])
            restored = root/'restored.sqlite3'
            store.restore_ledger(restored)
            with Ledger(restored) as ledger:
                self.assertEqual(ledger.rows('SELECT id FROM games')[0]['id'], 'preserved')
                self.assertEqual(ledger.rows('SELECT status FROM worker_runs')[0]['status'], 'degraded')

    def test_google_store_rejects_stale_overwrite(self):
        from google.api_core.exceptions import PreconditionFailed
        class Bucket:
            def __init__(self): self.data = {}; self.next = 1
            def blob(self, name): return Blob(self, name)
        class Blob:
            def __init__(self, bucket, name): self.bucket, self.name = bucket, name
            def reload(self): self.generation = self.bucket.data[self.name][0]
            def download_as_bytes(self, if_generation_match):
                generation, data = self.bucket.data[self.name]
                if generation != if_generation_match: raise PreconditionFailed('stale')
                return data
            def upload_from_string(self, data, if_generation_match, **kwargs):
                generation = self.bucket.data.get(self.name, (0, None))[0]
                if generation != if_generation_match: raise PreconditionFailed('stale')
                self.generation = self.bucket.next
                self.bucket.next += 1
                self.bucket.data[self.name] = self.generation, data
            def delete(self, if_generation_match):
                if self.bucket.data[self.name][0] != if_generation_match: raise PreconditionFailed('stale')
                del self.bucket.data[self.name]
        bucket = Bucket()
        first, second = GoogleStateStore(bucket), GoogleStateStore(bucket)
        first.put('ledger', b'original', 'application/octet-stream')
        self.assertEqual(second.get('ledger'), b'original')
        first.put('ledger', b'new', 'application/octet-stream')
        with self.assertRaises(PreconditionFailed):
            second.put('ledger', b'stale', 'application/octet-stream')
        with first.lease() as acquired:
            self.assertTrue(acquired)
            with second.lease() as other:
                self.assertFalse(other)
        with second.lease() as acquired:
            self.assertTrue(acquired)

    def test_sqlite_backup_includes_committed_wal_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            source, restored = Path(directory) / 'wal.sqlite3', Path(directory) / 'restored.sqlite3'
            db = sqlite3.connect(source)
            try:
                db.execute('PRAGMA journal_mode=WAL')
                db.execute('CREATE TABLE sample(value TEXT)')
                db.execute("INSERT INTO sample VALUES('committed')")
                db.commit()
                store = StateStore(MemoryS3(), 'bucket')
                store.save_ledger(source)
                store.restore_ledger(restored)
                check = sqlite3.connect(restored)
                try:
                    self.assertEqual(check.execute('SELECT value FROM sample').fetchone()[0], 'committed')
                finally:
                    check.close()
            finally:
                db.close()

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
