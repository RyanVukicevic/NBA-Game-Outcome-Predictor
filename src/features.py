from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import numpy as np

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
    rolling_history: str = "same-season",
    use_prior_season_features: bool = False,
    prior_decay_games: int = 30,
) -> pd.DataFrame:
    """
    Create team-level features before each game:
    IS_HOME, WIN, REST_DAYS, and ROLLING_* recent averages.
    """
    if rolling_history not in {"same-season", "carryover"}:
        raise ValueError("rolling_history must be either 'same-season' or 'carryover'")

    df = game_logs.copy()
    df["GAME_DATE"] = pd.to_datetime(df["GAME_DATE"])
    if "SEASON_ID" in df.columns:
        df["SEASON_YEAR"] = df["SEASON_ID"].astype(str).str[-4:]
    else:
        df["SEASON_YEAR"] = ""
    df["IS_HOME"] = df["MATCHUP"].str.contains(" vs. ", regex=False).astype(int)
    if "SEASON_TYPE" in df.columns:
        df["IS_PLAYOFFS"] = (df["SEASON_TYPE"] == "Playoffs").astype(int)
    else:
        df["IS_PLAYOFFS"] = 0
    df["WIN"] = (df["WL"] == "W").astype(int)

    for column in BOX_SCORE_COLUMNS:
        df[column] = pd.to_numeric(df[column], errors="coerce")

    if use_prior_season_features:
        df = add_prior_season_features(df, prior_decay_games=prior_decay_games)

    group_columns = ["TEAM_ID", "SEASON_YEAR"] if rolling_history == "same-season" else ["TEAM_ID"]

    df = df.sort_values(["TEAM_ID", "GAME_DATE", "GAME_ID"]).reset_index(drop=True)
    df["REST_DAYS"] = (
        df.groupby("TEAM_ID")["GAME_DATE"].diff().dt.days.clip(lower=0, upper=5).fillna(3)
    )

    grouped = df.groupby(group_columns, group_keys=False)
    for column in BOX_SCORE_COLUMNS:
        feature_name = f"ROLLING_{rolling_window}_{column}"
        df[feature_name] = grouped[column].transform(
            lambda values: values.shift(1).rolling(rolling_window, min_periods=min_periods).mean()
        )

    return df


def add_prior_season_features(team_games: pd.DataFrame, prior_decay_games: int = 30) -> pd.DataFrame:
    if "SEASON_YEAR" not in team_games.columns:
        return team_games

    summaries = (
        team_games.groupby(["TEAM_ID", "SEASON_YEAR"], as_index=False)
        .agg(
            PRIOR_SEASON_WIN=("WIN", "mean"),
            PRIOR_SEASON_PLUS_MINUS=("PLUS_MINUS", "mean"),
        )
        .sort_values(["TEAM_ID", "SEASON_YEAR"])
    )
    summaries[["PRIOR_SEASON_WIN", "PRIOR_SEASON_PLUS_MINUS"]] = summaries.groupby("TEAM_ID")[
        ["PRIOR_SEASON_WIN", "PRIOR_SEASON_PLUS_MINUS"]
    ].shift(1)
    previous_year = summaries.groupby("TEAM_ID")["SEASON_YEAR"].shift(1)
    consecutive = pd.to_numeric(summaries["SEASON_YEAR"]) - pd.to_numeric(previous_year) == 1
    summaries.loc[~consecutive, ["PRIOR_SEASON_WIN", "PRIOR_SEASON_PLUS_MINUS"]] = np.nan
    summaries = summaries.dropna(subset=["PRIOR_SEASON_WIN", "PRIOR_SEASON_PLUS_MINUS"])

    df = team_games.merge(
        summaries,
        on=["TEAM_ID", "SEASON_YEAR"],
        how="left",
    )
    df = df.sort_values(["TEAM_ID", "SEASON_YEAR", "GAME_DATE", "GAME_ID"]).reset_index(drop=True)
    df["TEAM_GAMES_PLAYED_THIS_SEASON"] = df.groupby(["TEAM_ID", "SEASON_YEAR"]).cumcount()
    if prior_decay_games <= 0:
        df["PRIOR_SEASON_WEIGHT"] = 0.0
    else:
        df["PRIOR_SEASON_WEIGHT"] = (1 - df["TEAM_GAMES_PLAYED_THIS_SEASON"] / prior_decay_games).clip(lower=0)
    for column in ["PRIOR_SEASON_WIN", "PRIOR_SEASON_PLUS_MINUS"]:
        df[f"DECAYED_{column}"] = df[column] * df["PRIOR_SEASON_WEIGHT"]

    return df


def build_matchup_frame(team_games: pd.DataFrame, rolling_window: int, require_features: bool = True) -> pd.DataFrame:
    """Combine the two team rows for each NBA game into one home-vs-away row."""
    rolling_columns = [f"ROLLING_{rolling_window}_{column}" for column in BOX_SCORE_COLUMNS]
    prior_columns = [
        "PRIOR_SEASON_WIN",
        "PRIOR_SEASON_PLUS_MINUS",
        "PRIOR_SEASON_WEIGHT",
        "DECAYED_PRIOR_SEASON_WIN",
        "DECAYED_PRIOR_SEASON_PLUS_MINUS",
    ]
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
        if require_features and (home_row[rolling_columns].isna().any() or away_row[rolling_columns].isna().any()):
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
        if "TEAM_GAMES_PLAYED_THIS_SEASON" in team_games.columns:
            row["HOME_GAMES_PLAYED_THIS_SEASON"] = float(home_row["TEAM_GAMES_PLAYED_THIS_SEASON"])
            row["AWAY_GAMES_PLAYED_THIS_SEASON"] = float(away_row["TEAM_GAMES_PLAYED_THIS_SEASON"])
            row["DIFF_GAMES_PLAYED_THIS_SEASON"] = (
                row["HOME_GAMES_PLAYED_THIS_SEASON"] - row["AWAY_GAMES_PLAYED_THIS_SEASON"]
            )

        for column in rolling_columns:
            clean_name = column.lower()
            home_value = float(home_row[column])
            away_value = float(away_row[column])
            row[f"home_{clean_name}"] = home_value
            row[f"away_{clean_name}"] = away_value
            row[f"diff_{clean_name}"] = home_value - away_value

        for column in prior_columns:
            if column in team_games.columns and pd.notna(home_row.get(column)) and pd.notna(away_row.get(column)):
                clean_name = column.lower()
                home_value = float(home_row[column])
                away_value = float(away_row[column])
                row[f"home_{clean_name}"] = home_value
                row[f"away_{clean_name}"] = away_value
                row[f"diff_{clean_name}"] = home_value - away_value

        rows.append(row)

    if not rows:
        raise ValueError("No eligible paired matchups. Check history and minimum periods.")
    return pd.DataFrame(rows).sort_values(["GAME_DATE", "GAME_ID"]).reset_index(drop=True)


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


def select_feature_names(
    matchup_frame: pd.DataFrame,
    feature_set: str,
    use_elo: bool = False,
    feature_mode: str = "full",
) -> list[str]:
    if feature_set == "deltas":
        prefixes = ("diff_",)
    elif feature_set == "full":
        prefixes = ("home_", "away_", "diff_")
    else:
        raise ValueError("feature_set must be either 'deltas' or 'full'")

    if feature_mode not in {"base", "full", "lean"}:
        raise ValueError("feature_mode must be one of: base, full, lean")

    interaction_columns = {
        column
        for column in matchup_frame.columns
        if column.startswith("playoff_x_")
        or column
        in {
            "diff_rest_days",
            "home_rest_advantage",
            "away_rest_advantage",
            "diff_elo_pre_x_diff_plus_minus",
        }
        or column.endswith("_possession_control")
    }

    feature_names = [
        column
        for column in matchup_frame.columns
        if column.startswith(prefixes)
        or column
        in {
            "HOME_REST_DAYS",
            "AWAY_REST_DAYS",
            "IS_PLAYOFFS",
        }
    ]
    if feature_mode == "base":
        feature_names = [column for column in feature_names if column not in interaction_columns]

    if use_elo:
        if feature_set == "deltas":
            feature_names.extend(["diff_elo_pre", "diff_elo_change_last_3", "diff_elo_change_last_5"])
        else:
            feature_names.extend(ELO_COLUMNS)

    if feature_mode in {"full", "lean"}:
        feature_names.extend(
            [
                column
                for column in matchup_frame.columns
                if column.startswith("playoff_x_")
                or column in {"home_rest_advantage", "away_rest_advantage"}
            ]
        )

    if feature_mode == "lean":
        keep_exact = {
            "diff_elo_pre",
            "diff_elo_change_last_5",
            "diff_rest_days",
            "HOME_REST_DAYS",
            "AWAY_REST_DAYS",
            "home_rest_advantage",
            "away_rest_advantage",
            "IS_PLAYOFFS",
            "playoff_x_diff_elo_pre",
            "playoff_x_diff_rest_days",
            "diff_elo_pre_x_diff_plus_minus",
            "diff_prior_season_win",
            "diff_prior_season_plus_minus",
            "diff_decayed_prior_season_win",
            "diff_decayed_prior_season_plus_minus",
            "home_prior_season_weight",
            "away_prior_season_weight",
            "diff_prior_season_weight",
        }
        keep_suffixes = {
            "_plus_minus",
            "_fg_pct",
            "_fg3_pct",
            "_tov",
        }
        feature_names = [
            column
            for column in feature_names
            if column in keep_exact
            or (column.startswith("playoff_x_diff_rolling_") and column.endswith("_plus_minus"))
            or (column.startswith("diff_rolling_") and any(column.endswith(suffix) for suffix in keep_suffixes))
        ]

    return list(dict.fromkeys(column for column in feature_names if column in matchup_frame.columns))


def build_matchup_dataset(
    team_games: pd.DataFrame,
    rolling_window: int,
    feature_set: str = "deltas",
    use_elo: bool = False,
    feature_mode: str = "full",
) -> MatchupData:
    matchup_frame = build_matchup_frame(team_games, rolling_window=rolling_window)
    feature_names = select_feature_names(
        matchup_frame,
        feature_set=feature_set,
        use_elo=use_elo,
        feature_mode=feature_mode,
    )
    return MatchupData(
        full=matchup_frame,
        x=matchup_frame[feature_names],
        y=matchup_frame["HOME_WIN"],
        feature_names=feature_names,
    )


def latest_features_by_team(team_games: pd.DataFrame, rolling_window: int) -> pd.DataFrame:
    return forecast_team_features(team_games, rolling_window,
                                  pd.to_datetime(team_games["GAME_DATE"]).max() + pd.Timedelta(days=1))


def forecast_team_features(history: pd.DataFrame, rolling_window: int, game_date,
                           min_periods: int = 1, rolling_history: str = "same-season",
                           prior_decay_games: int = 30) -> pd.DataFrame:
    """Post-result snapshots for a future matchup, using only supplied history.

    Insufficient current-season history uses the last available rolling window,
    explicitly marked as a fallback; unknown teams have no synthetic forecast.
    """
    target = pd.Timestamp(game_date).normalize()
    season = target.year if target.month >= 10 else target.year - 1
    df = history.copy()
    df["GAME_DATE"] = pd.to_datetime(df["GAME_DATE"])
    df = df.loc[df["GAME_DATE"] < target].sort_values(["GAME_DATE", "GAME_ID"])
    df["WIN"] = (df["WL"] == "W").astype(float)
    df["SEASON_YEAR"] = df["SEASON_ID"].astype(str).str[-4:]
    rows = []
    for team, games in df.groupby("TEAM_ABBREVIATION"):
        current = games.loc[games["SEASON_YEAR"] == str(season)]
        selected = current if rolling_history == "same-season" else games
        fallback = len(selected) < min_periods
        if fallback:
            selected = games
        window = selected.tail(rolling_window)
        row = games.iloc[-1].to_dict()
        row["TEAM_ABBREVIATION"] = team
        row["HISTORY_FALLBACK"] = fallback
        row["HISTORY_GAMES"] = len(window)
        row["REST_DAYS"] = float(np.clip((target - games["GAME_DATE"].max()).days, 0, 5))
        for column in BOX_SCORE_COLUMNS:
            row[f"ROLLING_{rolling_window}_{column}"] = pd.to_numeric(window[column], errors="raise").mean()
        previous = games.loc[games["SEASON_YEAR"] == str(season - 1)]
        weight = max(0.0, 1 - len(current) / prior_decay_games) if prior_decay_games > 0 else 0.0
        row["PRIOR_SEASON_WEIGHT"] = weight
        for stat in ("WIN", "PLUS_MINUS"):
            prior = float(previous[stat].mean()) if not previous.empty else 0.0
            row[f"PRIOR_SEASON_{stat}"] = prior
            row[f"DECAYED_PRIOR_SEASON_{stat}"] = prior * weight
        rows.append(row)
    if not rows:
        raise ValueError("No completed history before the prediction game date.")
    return pd.DataFrame(rows).set_index("TEAM_ABBREVIATION")
