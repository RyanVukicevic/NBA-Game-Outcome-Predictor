from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from config import BOX_SCORE_COLUMNS
from elo import ELO_COLUMNS


@dataclass(frozen=True)
class MatchupData:
    full: pd.DataFrame
    x: pd.DataFrame
    y: pd.Series
    feature_names: list[str]


def add_team_features(
    game_logs: pd.DataFrame,
    rolling_window: int,
    min_periods: int = 5,
    reset_each_season: bool = True,
) -> pd.DataFrame:
    """
    Create team-level features before each game:
    IS_HOME, WIN, REST_DAYS, and ROLLING_* recent averages.
    """
    df = game_logs.copy()
    df["GAME_DATE"] = pd.to_datetime(df["GAME_DATE"])
    df["IS_HOME"] = df["MATCHUP"].str.contains(" vs. ", regex=False).astype(int)
    if "SEASON_TYPE" in df.columns:
        df["IS_PLAYOFFS"] = (df["SEASON_TYPE"] == "Playoffs").astype(int)
    else:
        df["IS_PLAYOFFS"] = 0
    df["WIN"] = (df["WL"] == "W").astype(int)

    for column in BOX_SCORE_COLUMNS:
        df[column] = pd.to_numeric(df[column], errors="coerce")

    group_columns = ["TEAM_ID"]
    if reset_each_season and "SEASON_ID" in df.columns:
        group_columns = ["TEAM_ID", "SEASON_ID"]

    df = df.sort_values(group_columns + ["GAME_DATE", "GAME_ID"]).reset_index(drop=True)
    df["REST_DAYS"] = (
        df.groupby(group_columns)["GAME_DATE"].diff().dt.days.clip(lower=0, upper=5).fillna(3)
    )

    grouped = df.groupby(group_columns, group_keys=False)
    for column in BOX_SCORE_COLUMNS:
        feature_name = f"ROLLING_{rolling_window}_{column}"
        df[feature_name] = grouped[column].transform(
            lambda values: values.shift(1).rolling(rolling_window, min_periods=min_periods).mean()
        )

    return df


def build_matchup_frame(team_games: pd.DataFrame, rolling_window: int) -> pd.DataFrame:
    """Combine the two team rows for each NBA game into one home-vs-away row."""
    rolling_columns = [f"ROLLING_{rolling_window}_{column}" for column in BOX_SCORE_COLUMNS]
    rows: list[dict[str, float | str | pd.Timestamp]] = []

    for game_id, game in team_games.groupby("GAME_ID", sort=False):
        if len(game) != 2:
            continue

        home = game[game["IS_HOME"] == 1]
        away = game[game["IS_HOME"] == 0]
        if len(home) != 1 or len(away) != 1:
            continue

        home_row = home.iloc[0]
        away_row = away.iloc[0]
        if home_row[rolling_columns].isna().any() or away_row[rolling_columns].isna().any():
            continue

        row: dict[str, float | str | pd.Timestamp] = {
            "GAME_ID": game_id,
            "GAME_DATE": home_row["GAME_DATE"],
            "SEASON_ID": home_row.get("SEASON_ID"),
            "HOME_TEAM": home_row["TEAM_ABBREVIATION"],
            "AWAY_TEAM": away_row["TEAM_ABBREVIATION"],
            "HOME_WIN": int(home_row["WIN"]),
            "IS_PLAYOFFS": int(home_row.get("IS_PLAYOFFS", 0)),
            "HOME_REST_DAYS": float(home_row["REST_DAYS"]),
            "AWAY_REST_DAYS": float(away_row["REST_DAYS"]),
        }

        for column in rolling_columns:
            clean_name = column.lower()
            home_value = float(home_row[column])
            away_value = float(away_row[column])
            row[f"home_{clean_name}"] = home_value
            row[f"away_{clean_name}"] = away_value
            row[f"diff_{clean_name}"] = home_value - away_value

        rows.append(row)

    return pd.DataFrame(rows).sort_values("GAME_DATE").reset_index(drop=True)


def add_interaction_features(matchup_frame: pd.DataFrame, rolling_window: int) -> pd.DataFrame:
    df = matchup_frame.copy()
    plus_minus = f"diff_rolling_{rolling_window}_plus_minus"
    rebounds = f"diff_rolling_{rolling_window}_reb"
    turnovers = f"diff_rolling_{rolling_window}_tov"

    if "HOME_REST_DAYS" in df.columns and "AWAY_REST_DAYS" in df.columns:
        df["diff_rest_days"] = df["HOME_REST_DAYS"] - df["AWAY_REST_DAYS"]
        df["home_rest_advantage"] = df["diff_rest_days"].clip(lower=0)
        df["away_rest_advantage"] = (-df["diff_rest_days"]).clip(lower=0)
        if "IS_PLAYOFFS" in df.columns:
            df["playoff_x_diff_rest_days"] = df["IS_PLAYOFFS"] * df["diff_rest_days"]

    if rebounds in df.columns and turnovers in df.columns:
        df[f"diff_rolling_{rolling_window}_possession_control"] = df[rebounds] - df[turnovers]

    if "IS_PLAYOFFS" in df.columns and plus_minus in df.columns:
        df[f"playoff_x_{plus_minus}"] = df["IS_PLAYOFFS"] * df[plus_minus]

    if "diff_elo_pre" in df.columns:
        if "IS_PLAYOFFS" in df.columns:
            df["playoff_x_diff_elo_pre"] = df["IS_PLAYOFFS"] * df["diff_elo_pre"]
        if plus_minus in df.columns:
            df["diff_elo_pre_x_diff_plus_minus"] = df["diff_elo_pre"] * df[plus_minus]

    for window in [3, 5]:
        column = f"diff_elo_change_last_{window}"
        if "IS_PLAYOFFS" in df.columns and column in df.columns:
            df[f"playoff_x_{column}"] = df["IS_PLAYOFFS"] * df[column]

    return df


def select_feature_names(matchup_frame: pd.DataFrame, feature_set: str, use_elo: bool = False) -> list[str]:
    if feature_set == "deltas":
        prefixes = ("diff_",)
    elif feature_set == "full":
        prefixes = ("home_", "away_", "diff_")
    else:
        raise ValueError("feature_set must be either 'deltas' or 'full'")

    feature_names = [
        column
        for column in matchup_frame.columns
        if column.startswith(prefixes)
        or column.startswith("playoff_x_")
        or column
        in {
            "HOME_REST_DAYS",
            "AWAY_REST_DAYS",
            "IS_PLAYOFFS",
            "home_rest_advantage",
            "away_rest_advantage",
        }
    ]
    if use_elo:
        if feature_set == "deltas":
            feature_names.extend(["diff_elo_pre", "diff_elo_change_last_3", "diff_elo_change_last_5"])
        else:
            feature_names.extend(ELO_COLUMNS)

    return list(dict.fromkeys(column for column in feature_names if column in matchup_frame.columns))


def build_matchup_dataset(
    team_games: pd.DataFrame,
    rolling_window: int,
    feature_set: str = "deltas",
    use_elo: bool = False,
) -> MatchupData:
    matchup_frame = build_matchup_frame(team_games, rolling_window=rolling_window)
    feature_names = select_feature_names(matchup_frame, feature_set=feature_set, use_elo=use_elo)
    return MatchupData(
        full=matchup_frame,
        x=matchup_frame[feature_names],
        y=matchup_frame["HOME_WIN"],
        feature_names=feature_names,
    )


def latest_features_by_team(team_games: pd.DataFrame, rolling_window: int) -> pd.DataFrame:
    rolling_columns = [f"ROLLING_{rolling_window}_{column}" for column in BOX_SCORE_COLUMNS]
    return (
        team_games.dropna(subset=rolling_columns)
        .sort_values(["TEAM_ID", "GAME_DATE", "GAME_ID"])
        .groupby("TEAM_ABBREVIATION", as_index=False)
        .tail(1)
        .set_index("TEAM_ABBREVIATION")
    )
