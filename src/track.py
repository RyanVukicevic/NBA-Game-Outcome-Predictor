"""CLI for pregame tracking and paper research. Does not place bets."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path

from tracking import DB_PATH, Ledger, Policy, export_report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DB_PATH)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    imp = commands.add_parser("import-forecasts")
    imp.add_argument("paths", nargs="+", type=Path)
    collect = commands.add_parser("collect-odds")
    collect.add_argument("--bookmakers", nargs="+", default=["draftkings", "fanduel"])
    decide = commands.add_parser("decide")
    decide.add_argument("--model-id", required=True)
    decide.add_argument("--bookmaker", default="draftkings")
    decide.add_argument("--horizon-minutes", type=int, default=60)
    decide.add_argument("--odds-max-age-minutes", type=int, default=10)
    decide.add_argument("--prediction-max-age-hours", type=int, default=24)
    decide.add_argument("--stake", type=float, default=10)
    decide.add_argument("--minimum-ev", type=float, default=.03)
    settle = commands.add_parser("settle")
    settle.add_argument("--seasons", nargs="+", required=True)
    settle.add_argument("--refresh", action="store_true")
    report = commands.add_parser("report")
    report.add_argument("--policy-id", required=True)
    report.add_argument("--output", type=Path, default=Path("reports/tracking"))
    actual = commands.add_parser("import-actual", help="Import accepted receipts, never execute wagers.")
    actual.add_argument("path", type=Path)
    schedule = commands.add_parser("schedule-status", help="Record a verified postponement/cancellation or revised tipoff.")
    schedule.add_argument("--game-id", required=True)
    schedule.add_argument("--tipoff", required=True)
    schedule.add_argument("--status", required=True, choices=["scheduled", "postponed", "canceled", "started", "final"])
    schedule.add_argument("--source", required=True)
    args = parser.parse_args()
    with Ledger(args.db) as ledger:
        if args.command == "status":
            for table in ("games", "schedules", "models", "forecasts", "odds", "results", "decisions", "actual_wagers"):
                print(f"{table}: {ledger.db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]}")
            print("Models:", ledger.rows("SELECT id FROM models"))
            print("Policies:", ledger.rows("SELECT * FROM policies"))
        elif args.command == "import-forecasts":
            for path in args.paths:
                print(path, ledger.import_forecasts(json.loads(path.read_text(encoding="utf-8"))))
        elif args.command == "collect-odds":
            from odds import collect_odds
            print(collect_odds(ledger, args.bookmakers))
        elif args.command == "decide":
            settings = {k: getattr(args, k) for k in asdict(Policy(args.model_id)) if hasattr(args, k)}
            policy = Policy(**settings)
            print("New decisions:", ledger.decide(policy))
            print("Policy ID:", policy.id)
        elif args.command == "settle":
            from data import load_game_logs
            logs = load_game_logs(args.seasons, ["Regular Season", "Playoffs"], refresh=args.refresh)
            print("Tracked final games reconciled:", ledger.settle_logs(logs))
        elif args.command == "report":
            print(export_report(ledger, args.policy_id, args.output).to_string(index=False))
            print("Paper report:", args.output.resolve())
        elif args.command == "schedule-status":
            ledger.schedule(args.game_id, args.tipoff, status=args.status, source=args.source)
        elif args.command == "import-actual":
            payload = json.loads(args.path.read_text(encoding="utf-8"))
            for receipt in payload.get("wagers", []):
                print("Receipt:", ledger.record_actual(**receipt))
            for settlement in payload.get("settlements", []):
                ledger.settle_actual(**settlement)


if __name__ == "__main__":
    main()
