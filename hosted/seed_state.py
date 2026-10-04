"""Seed R2 with the local ledger and verified production runtime bundle."""
from __future__ import annotations

import argparse
from pathlib import Path
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
    args = parser.parse_args()
    model_path = production_model_path(load_production_config(args.config))
    store = StateStore.from_env()
    runtime = build_runtime_bundle(ROOT, model_path)
    store.save_ledger(args.db)
    store.put("state/runtime.tar.gz", runtime, "application/gzip")
    print(f"Seeded verified ledger and runtime bundle ({len(runtime):,} bytes, sha256 {sha256(runtime)}).")


if __name__ == "__main__":
    main()
