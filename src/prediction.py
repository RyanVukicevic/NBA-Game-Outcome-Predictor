from __future__ import annotations

import pandas as pd

from config import BOX_SCORE_COLUMNS
from modeling import TrainingResult


def predict_matchup(result: TrainingResult, home: str, away: str) -> float:
    home = home.upper()
    away = away.upper()
    latest = result.latest_team_features

    if home not in latest.index:
        raise ValueError(f"Unknown home team abbreviation or no features available: {home}")
    if away not in latest.index:
        raise ValueError(f"Unknown away team abbreviation or no features available: {away}")

    home_row = latest.loc[home]
    away_row = latest.loc[away]
    sample = {
        "HOME_REST_DAYS": 3.0,
        "AWAY_REST_DAYS": 3.0,
    }

    for column in BOX_SCORE_COLUMNS:
        rolling_column = f"ROLLING_{result.rolling_window}_{column}"
        clean_name = rolling_column.lower()
        home_value = float(home_row[rolling_column])
        away_value = float(away_row[rolling_column])
        sample[f"home_{clean_name}"] = home_value
        sample[f"away_{clean_name}"] = away_value
        sample[f"diff_{clean_name}"] = home_value - away_value

    if getattr(result, "use_elo", False):
        if getattr(result, "latest_elos", None) is None:
            raise ValueError("This model expects Elo features, but no latest Elo ratings were saved.")
        if home not in result.latest_elos.index:
            raise ValueError(f"No Elo rating available for home team: {home}")
        if away not in result.latest_elos.index:
            raise ValueError(f"No Elo rating available for away team: {away}")

        home_elo = float(result.latest_elos.loc[home, "ELO"])
        away_elo = float(result.latest_elos.loc[away, "ELO"])
        sample["home_elo_pre"] = home_elo
        sample["away_elo_pre"] = away_elo
        sample["diff_elo_pre"] = home_elo - away_elo
        for window in [3, 5]:
            home_change = float(result.latest_elos.loc[home].get(f"elo_change_last_{window}", 0.0))
            away_change = float(result.latest_elos.loc[away].get(f"elo_change_last_{window}", 0.0))
            sample[f"home_elo_change_last_{window}"] = home_change
            sample[f"away_elo_change_last_{window}"] = away_change
            sample[f"diff_elo_change_last_{window}"] = home_change - away_change

    x_pred = pd.DataFrame([sample], columns=result.feature_names)
    return float(result.model.predict_proba(x_pred)[0, 1])
