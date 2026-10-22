"""Seed R2 with the local ledger and verified production runtime bundle."""
from __future__ import annotations

import argparse
import gzip
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from production import load_production_config, production_model_path
from tracking import DB_PATH
from hosted.state import StateStore, build_runtime_bundle, sha256


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--config", type=Path, default=ROOT / "production_config.txt")
    parser.add_argument('--prepare-only', type=Path, help='Build upload files locally without cloud credentials.')
    args = parser.parse_args()
    model_path = production_model_path(load_production_config(args.config))
    runtime = build_runtime_bundle(ROOT, model_path)
    if args.prepare_only:
        output = args.prepare_only
        output.mkdir(parents=True, exist_ok=True)
        source = sqlite3.connect(args.db)
        snapshot_path = output / 'predictions.sqlite3'
        snapshot = sqlite3.connect(snapshot_path)
        try:
            source.backup(snapshot)
            if snapshot.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('Prepared ledger failed integrity check.')
        finally:
            snapshot.close()
            source.close()
        (output / 'predictions.sqlite3.gz').write_bytes(gzip.compress(snapshot_path.read_bytes(), mtime=0))
        (output / 'runtime.tar.gz').write_bytes(runtime)
        print(f'Prepared verified ledger and pinned runtime at {output.resolve()}. No cloud objects were changed.')
        return
    store = StateStore.from_env()
    store.save_ledger(args.db)
    store.put("state/runtime.tar.gz", runtime, "application/gzip")
    print(f"Seeded verified ledger and runtime bundle ({len(runtime):,} bytes, sha256 {sha256(runtime)}).")


if __name__ == "__main__":
    main()
