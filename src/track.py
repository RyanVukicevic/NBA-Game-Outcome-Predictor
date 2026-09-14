"""CLI for pregame tracking and paper research. Does not place bets."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import os
import time

from tracking import DB_PATH, Ledger, Policy, export_report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DB_PATH)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    quota = commands.add_parser("quota-init", help="Configure the free monthly allowance once; resets automatically at 00:00 UTC on the 1st.")
    quota.add_argument("--used", type=int, required=True, help="Current credits used, from your dashboard.")
    eligibility = commands.add_parser("eligibility")
    eligibility.add_argument("--model-id", required=True)
    schedule_run = commands.add_parser("schedule", help="Preview due work; --execute runs one pass, --watch repeats it.")
    schedule_run.add_argument("--model-path", type=Path, required=True)
    schedule_run.add_argument("--config", type=Path, default=Path(__file__).resolve().parents[1] / "production_config.txt")
    schedule_run.add_argument("--execute", action="store_true")
    schedule_run.add_argument("--watch", action="store_true")
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
            from budget import status
            print("Quota:", status(ledger))
        elif args.command == "quota-init":
            from budget import configure, next_month, status
            configure(ledger, next_month(), args.used, calendar_monthly=True)
            print(status(ledger))
        elif args.command == "eligibility":
            from eligibility import EligibilityContext
            print(json.dumps(EligibilityContext(ledger, args.model_id).record(), indent=2))
        elif args.command == "schedule":
            from scheduler import plan, tick, refresh_tracking
            from budget import status
            model_id = json.loads(args.model_path.with_suffix(".json").read_text())["model_id"]
            if args.watch and not args.execute:
                parser.error("--watch requires --execute; preview makes no API calls.")
            if not args.execute:
                print(json.dumps(dict(quota=status(ledger), plan=plan(ledger, model_id)), indent=2))
                return
            if not os.environ.get("ODDS_API_KEY"):
                parser.error("Set ODDS_API_KEY locally before executing the scheduler.")
            if not status(ledger)["ready"]:
                parser.error("Configure quota with quota-init before executing the scheduler.")
            while True:
                try:
                    force = any(p["status"] == "due" for p in plan(ledger, model_id))
                    result = tick(ledger, model_id, refresh=lambda: refresh_tracking(ledger, args.model_path, args.config,
                                                                                   force=force, expected_model_id=model_id))
                    print(json.dumps(result), flush=True)
                except (ValueError, RuntimeError) as exc:
                    print(f"Scheduler stopped safely: {exc}", flush=True)
                    return
                if not args.watch:
                    return
                try:
                    time.sleep(60)
                except KeyboardInterrupt:
                    print("Scheduler stopped.")
                    return
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
