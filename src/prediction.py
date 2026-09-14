from __future__ import annotations

import pandas as pd

from config import BOX_SCORE_COLUMNS
from features import add_interaction_features, forecast_team_features
from modeling import TrainingResult
from provenance import SCHEMA_VERSION, cutoff_timestamp, local_day, digest, implementation_id, runtime_versions


def prediction_inputs(result: TrainingResult, home: str, away: str, is_playoffs: bool = False,
                      game_date=None, as_of=None, rest_dates: dict | None = None) -> tuple[pd.DataFrame, dict]:
    home = home.upper()
    away = away.upper()
    if home == away:
        raise ValueError("Home and away teams must differ.")
    if getattr(result, "metadata", {}).get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Legacy model has no cutoff provenance. Retrain before predicting.")
    if (result.metadata.get("implementation_id") != implementation_id()
            or result.metadata.get("runtime") != runtime_versions()):
        raise ValueError("Model source/runtime differs from this installation. Retrain before predicting.")
    issued_at = cutoff_timestamp(as_of)
    target = local_day(issued_at) if game_date is None else pd.Timestamp(game_date).normalize()
    if target < local_day(issued_at):
        raise ValueError("A forecast game date cannot precede its issue date.")
    for key in ("training_end", "snapshot_end"):
        if pd.Timestamp(result.metadata[key]) >= local_day(issued_at):
            raise ValueError("Model/history contains games on or after the requested cutoff. Rebuild as of that date.")
    matchup_history = result.history.loc[result.history["TEAM_ABBREVIATION"].isin([home, away])]
    latest = forecast_team_features(matchup_history, result.rolling_window, target,
                                    min_periods=result.min_periods, rolling_history=result.rolling_history,
                                    prior_decay_games=result.prior_decay_games)

    if home not in latest.index:
        raise ValueError(f"Unknown home team abbreviation or no features available: {home}")
    if away not in latest.index:
        raise ValueError(f"Unknown away team abbreviation or no features available: {away}")

    home_row = latest.loc[home]
    away_row = latest.loc[away]
    sample = {
        "IS_PLAYOFFS": int(is_playoffs),
        "HOME_REST_DAYS": float(home_row["REST_DAYS"]),
        "AWAY_REST_DAYS": float(away_row["REST_DAYS"]),
    }
    rest_sources = {}
    for side, team, row in [("HOME", home, home_row), ("AWAY", away, away_row)]:
        previous = pd.Timestamp(row["GAME_DATE"])
        if rest_dates and team in rest_dates:
            scheduled = pd.Timestamp(rest_dates[team]).normalize()
            if scheduled >= target:
                raise ValueError("Previous scheduled game must precede the target game.")
            previous = max(previous, scheduled)
        sample[f"{side}_REST_DAYS"] = float(min(5, max(0, (target - previous).days)))
        rest_sources[team] = str(previous.date())

    for column in BOX_SCORE_COLUMNS:
        rolling_column = f"ROLLING_{result.rolling_window}_{column}"
        clean_name = rolling_column.lower()
        home_value = float(home_row[rolling_column])
        away_value = float(away_row[rolling_column])
        sample[f"home_{clean_name}"] = home_value
        sample[f"away_{clean_name}"] = away_value
        sample[f"diff_{clean_name}"] = home_value - away_value

    for column in [
        "PRIOR_SEASON_WIN",
        "PRIOR_SEASON_PLUS_MINUS",
        "PRIOR_SEASON_WEIGHT",
        "DECAYED_PRIOR_SEASON_WIN",
        "DECAYED_PRIOR_SEASON_PLUS_MINUS",
    ]:
        if column in latest.columns:
            clean_name = column.lower()
            home_value = float(home_row[column]) if pd.notna(home_row[column]) else 0.0
            away_value = float(away_row[column]) if pd.notna(away_row[column]) else 0.0
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
        last_season = int(result.history["SEASON_ID"].astype(str).str[-4:].max())
        target_season = target.year if target.month >= 10 else target.year - 1
        transitions = max(0, target_season - last_season)
        if transitions:
            home_elo = 1500 + result.elo_carryover ** transitions * (home_elo - 1500)
            away_elo = 1500 + result.elo_carryover ** transitions * (away_elo - 1500)
        sample["home_elo_pre"] = home_elo
        sample["away_elo_pre"] = away_elo
        sample["diff_elo_pre"] = home_elo - away_elo
        for window in [3, 5]:
            home_change = float(result.latest_elos.loc[home].get(f"elo_change_last_{window}", 0.0))
            away_change = float(result.latest_elos.loc[away].get(f"elo_change_last_{window}", 0.0))
            if transitions:
                home_change = away_change = 0.0
            sample[f"home_elo_change_last_{window}"] = home_change
            sample[f"away_elo_change_last_{window}"] = away_change
            sample[f"diff_elo_change_last_{window}"] = home_change - away_change

    x_pred = add_interaction_features(pd.DataFrame([sample]), rolling_window=result.rolling_window)
    x_pred = x_pred.reindex(columns=result.feature_names, fill_value=0.0)
    if x_pred.isna().any().any():
        raise ValueError("Prediction features contain missing values.")
    inputs = {key: float(value) for key, value in x_pred.iloc[0].items()}
    context = dict(model_id=result.metadata["model_id"], snapshot_hash=result.metadata["snapshot_hash"],
                   training_end=result.metadata["training_end"], snapshot_end=result.metadata["snapshot_end"],
                   issued_at=issued_at.isoformat(), game_date=str(target.date()), home=home, away=away,
                   availability_policy=result.metadata["availability_policy"], input_features=inputs,
                   history_fallback=bool(home_row["HISTORY_FALLBACK"] or away_row["HISTORY_FALLBACK"]),
                   rest_reference_dates=rest_sources)
    context["input_hash"] = digest(inputs)
    return x_pred, context


def predict_matchup_details(result: TrainingResult, home: str, away: str, is_playoffs: bool = False,
                            game_date=None, as_of=None, rest_dates: dict | None = None) -> dict:
    sample, context = prediction_inputs(result, home, away, is_playoffs, game_date, as_of, rest_dates)
    if result.deployment_model is None:
        raise ValueError("No deployment estimator; retrain this model.")
    context["home_win_probability"] = float(result.deployment_model.predict_proba(sample)[0, 1])
    context["prediction_id"] = digest(context)
    return context


def predict_matchup(result: TrainingResult, home: str, away: str, is_playoffs: bool = False,
                    game_date=None, as_of=None, rest_dates: dict | None = None) -> float:
    return predict_matchup_details(result, home, away, is_playoffs, game_date, as_of, rest_dates)["home_win_probability"]
