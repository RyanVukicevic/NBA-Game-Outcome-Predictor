from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from config import MODEL_PATH, PROCESSED_CACHE_DIR
from config import season_type_slug
from data import load_game_logs
from elo import add_elo_features
from evaluation import calibration_table, evaluate_probabilities, temporal_cv_scores
from features import (
    MatchupData,
    add_interaction_features,
    add_team_features,
    build_matchup_frame,
    latest_features_by_team,
    select_feature_names,
)


@dataclass(frozen=True)
class TrainingResult:
    model: Pipeline
    feature_names: list[str]
    latest_team_features: pd.DataFrame
    rolling_window: int
    min_periods: int
    feature_set: str
    feature_mode: str
    use_elo: bool
    season_types: list[str]
    latest_elos: pd.DataFrame | None
    elo_k: float
    elo_playoff_k: float | None
    elo_home_advantage: float
    elo_carryover: float
    metrics: dict[str, float]
    calibration: pd.DataFrame
    feature_importance: pd.DataFrame
    cv_scores: pd.DataFrame | None = None


def make_model() -> Pipeline:
    return Pipeline(
        steps=[
            ("scale", StandardScaler()),
            ("logistic", LogisticRegression(max_iter=1000, class_weight="balanced")),
        ]
    )


def cache_slug(
    seasons: list[str],
    rolling_window: int,
    min_periods: int,
    season_types: list[str] | None = None,
    use_elo: bool = False,
    elo_k: float = 20,
    elo_playoff_k: float | None = None,
    elo_home_advantage: float = 65,
    elo_carryover: float = 0.75,
) -> str:
    season_part = "_".join(season.replace("-", "_") for season in seasons)
    slug = f"{season_part}_rw{rolling_window}_min{min_periods}"
    type_slug = season_type_slug(season_types)
    if type_slug != "regularseason":
        slug += f"_{type_slug}"
    if use_elo:
        slug += f"_elo_k{elo_k:g}_ha{elo_home_advantage:g}_co{elo_carryover:g}_mom3_5"
        if elo_playoff_k is not None:
            slug += f"_pk{elo_playoff_k:g}"
    return slug


def load_or_build_model_frames(
    seasons: list[str],
    rolling_window: int,
    min_periods: int,
    feature_set: str,
    feature_mode: str = "full",
    season_types: list[str] | None = None,
    use_elo: bool = False,
    elo_k: float = 20,
    elo_playoff_k: float | None = None,
    elo_home_advantage: float = 65,
    elo_carryover: float = 0.75,
    refresh: bool = False,
) -> tuple[pd.DataFrame, MatchupData, pd.DataFrame | None]:
    PROCESSED_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    slug = cache_slug(
        seasons,
        rolling_window=rolling_window,
        min_periods=min_periods,
        season_types=season_types,
        use_elo=use_elo,
        elo_k=elo_k,
        elo_playoff_k=elo_playoff_k,
        elo_home_advantage=elo_home_advantage,
        elo_carryover=elo_carryover,
    )
    team_features_path = PROCESSED_CACHE_DIR / f"team_features_{slug}.csv"
    matchup_path = PROCESSED_CACHE_DIR / f"matchups_{slug}.csv"
    latest_elos_path = PROCESSED_CACHE_DIR / f"latest_elos_{slug}.csv"

    if team_features_path.exists() and matchup_path.exists() and not refresh:
        team_games = pd.read_csv(team_features_path, parse_dates=["GAME_DATE"])
        matchup_frame = pd.read_csv(matchup_path, parse_dates=["GAME_DATE"])
        latest_elos = (
            pd.read_csv(latest_elos_path).set_index("TEAM_ABBREVIATION")
            if use_elo and latest_elos_path.exists()
            else None
        )
    else:
        logs = load_game_logs(seasons, season_types=season_types, refresh=refresh)
        team_games = add_team_features(logs, rolling_window=rolling_window, min_periods=min_periods)
        matchup_frame = build_matchup_frame(team_games, rolling_window=rolling_window)
        latest_elos = None
        if use_elo:
            matchup_frame, latest_elos = add_elo_features(
                matchup_frame,
                k_factor=elo_k,
                playoff_k_factor=elo_playoff_k,
                home_advantage=elo_home_advantage,
                carryover=elo_carryover,
            )
            latest_elos.to_csv(latest_elos_path)
        team_games.to_csv(team_features_path, index=False)
        matchup_frame.to_csv(matchup_path, index=False)

    matchup_frame = add_interaction_features(matchup_frame, rolling_window=rolling_window)
    feature_names = select_feature_names(
        matchup_frame,
        feature_set=feature_set,
        use_elo=use_elo,
        feature_mode=feature_mode,
    )
    return team_games, MatchupData(
        full=matchup_frame,
        x=matchup_frame[feature_names],
        y=matchup_frame["HOME_WIN"],
        feature_names=feature_names,
    ), latest_elos


def feature_importance_table(model: Pipeline, feature_names: list[str]) -> pd.DataFrame:
    coefficients = model.named_steps["logistic"].coef_[0]
    table = pd.DataFrame(
        {
            "feature": feature_names,
            "coefficient": coefficients,
            "abs_coefficient": np.abs(coefficients),
            "odds_ratio_per_1_std": np.exp(coefficients),
        }
    )
    table["direction"] = table["coefficient"].map(lambda value: "home" if value > 0 else "away")
    return table.sort_values("abs_coefficient", ascending=False).reset_index(drop=True)


def build_elo_leaderboard(
    seasons: list[str],
    rolling_window: int = 20,
    min_periods: int = 7,
    feature_set: str = "deltas",
    feature_mode: str = "full",
    season_types: list[str] | None = None,
    elo_k: float = 20,
    elo_playoff_k: float | None = None,
    elo_home_advantage: float = 65,
    elo_carryover: float = 0.75,
    refresh: bool = False,
) -> pd.DataFrame:
    _, _, latest_elos = load_or_build_model_frames(
        seasons=seasons,
        rolling_window=rolling_window,
        min_periods=min_periods,
        feature_set=feature_set,
        feature_mode=feature_mode,
        season_types=season_types,
        use_elo=True,
        elo_k=elo_k,
        elo_playoff_k=elo_playoff_k,
        elo_home_advantage=elo_home_advantage,
        elo_carryover=elo_carryover,
        refresh=refresh,
    )
    if latest_elos is None:
        raise ValueError("Elo leaderboard could not be built.")
    return latest_elos.sort_values("ELO", ascending=False)


def train_model(
    seasons: list[str],
    rolling_window: int = 10,
    min_periods: int = 5,
    test_fraction: float = 0.2,
    feature_set: str = "deltas",
    feature_mode: str = "full",
    season_types: list[str] | None = None,
    use_elo: bool = False,
    elo_k: float = 20,
    elo_playoff_k: float | None = None,
    elo_home_advantage: float = 65,
    elo_carryover: float = 0.75,
    cv_splits: int = 0,
    refresh: bool = False,
) -> TrainingResult:
    team_games, matchup_data, latest_elos = load_or_build_model_frames(
        seasons=seasons,
        rolling_window=rolling_window,
        min_periods=min_periods,
        feature_set=feature_set,
        feature_mode=feature_mode,
        season_types=season_types,
        use_elo=use_elo,
        elo_k=elo_k,
        elo_playoff_k=elo_playoff_k,
        elo_home_advantage=elo_home_advantage,
        elo_carryover=elo_carryover,
        refresh=refresh,
    )
    x, y = matchup_data.x, matchup_data.y

    if len(x) < 50:
        raise ValueError("Not enough games after feature engineering. Add more seasons.")

    split_index = int(len(x) * (1 - test_fraction))
    x_train, x_test = x.iloc[:split_index], x.iloc[split_index:]
    y_train, y_test = y.iloc[:split_index], y.iloc[split_index:]

    model = make_model()
    cv_scores = temporal_cv_scores(model, x, y, splits=cv_splits) if cv_splits else None
    model.fit(x_train, y_train)

    probabilities = model.predict_proba(x_test)[:, 1]
    metrics = evaluate_probabilities(y_test, probabilities)
    metrics["train_games"] = float(len(x_train))
    metrics["test_games"] = float(len(x_test))

    return TrainingResult(
        model=model,
        feature_names=matchup_data.feature_names,
        latest_team_features=latest_features_by_team(team_games, rolling_window=rolling_window),
        rolling_window=rolling_window,
        min_periods=min_periods,
        feature_set=feature_set,
        feature_mode=feature_mode,
        use_elo=use_elo,
        season_types=season_types or ["Regular Season"],
        latest_elos=latest_elos,
        elo_k=elo_k,
        elo_playoff_k=elo_playoff_k,
        elo_home_advantage=elo_home_advantage,
        elo_carryover=elo_carryover,
        metrics=metrics,
        calibration=calibration_table(y_test, probabilities),
        feature_importance=feature_importance_table(model, matchup_data.feature_names),
        cv_scores=cv_scores,
    )


def save_training_result(result: TrainingResult, path: Path = MODEL_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(result, path)


def load_training_result(path: Path = MODEL_PATH) -> TrainingResult:
    return joblib.load(path)
