"""Deterministic synthetic accounting example, isolated from real forecasts."""
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from tracking import Ledger, Policy, export_report, utc


def run_tracking_demo(output_dir):
    policy = Policy("synthetic-accounting-example", version=1)
    with TemporaryDirectory() as temp, Ledger(Path(temp) / "demo.sqlite3") as ledger:
        for i in range(30):
            tipoff = pd.Timestamp("2026-01-01T23:00:00Z") + pd.Timedelta(days=i)
            issued = tipoff - pd.Timedelta(hours=3)
            quote_time = tipoff - pd.Timedelta(minutes=65)
            cutoff = tipoff - pd.Timedelta(minutes=60)
            game_id = f"synthetic-{i:03}"
            probability = [.65, .45, .55, .72, .38][i % 5]
            home_odds, away_odds = [(1.8, 2.1), (2.2, 1.7), (1.6, 2.5)][i % 3]
            details = dict(game_id=game_id, home="BOS", away="NYK", model_id=policy.model_id,
                           home_win_probability=probability, issued_at=utc(issued), tipoff_at=utc(tipoff),
                           input_features={"synthetic": 1})
            ledger.forecast(details, {"model_id": policy.model_id, "synthetic": True}, issued)
            ledger.quote(game_id, policy.bookmaker, quote_time, tipoff, home_odds, away_odds, quote_time)
            ledger.decide(policy, cutoff)
            ledger.result(game_id, 110 if i % 4 else 95, 100, observed=tipoff + pd.Timedelta(hours=4), source="synthetic")
        summary = export_report(ledger, policy.id, output_dir, "2026-02-01T00:00:00Z", synthetic=True)
        rows = ledger.paper_rows(policy.id, "2026-02-01T00:00:00Z")
    return summary, rows
