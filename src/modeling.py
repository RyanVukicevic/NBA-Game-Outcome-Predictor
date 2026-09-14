from __future__ import annotations

from dataclasses import dataclass, field, replace
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from config import MODEL_PATH, PROCESSED_CACHE_DIR
from config import season_type_slug
from data import load_game_logs, completed_game_logs
from provenance import SCHEMA_VERSION, cutoff_timestamp, digest, frame_digest, implementation_id, runtime_versions, git_revision
from elo import add_elo_features
from evaluation import calibration_table, evaluate_probabilities, temporal_cv_scores
from evaluation import walk_forward_cv_scores, holdout_split_index
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
    seasons: list[str]
    rolling_window: int
    min_periods: int
    feature_set: str
    feature_mode: str
    rolling_history: str
    use_prior_season_features: bool
    prior_decay_games: int
    warmup_seasons: list[str]
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
    deployment_model: Pipeline | None = None
    history: pd.DataFrame = field(default_factory=pd.DataFrame)
    metadata: dict = field(default_factory=dict)


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
    warmup_seasons: list[str] | None = None,
    rolling_history: str = "same-season",
    use_prior_season_features: bool = False,
    prior_decay_games: int = 30,
    use_elo: bool = False,
    elo_k: float = 20,
    elo_playoff_k: float | None = None,
    elo_home_advantage: float = 65,
    elo_carryover: float = 0.75,
) -> str:
    season_part = "_".join(season.replace("-", "_") for season in seasons)
    slug = f"{season_part}_rw{rolling_window}_min{min_periods}"
    if warmup_seasons:
        warmup_part = "_".join(season.replace("-", "_") for season in warmup_seasons)
        slug += f"_warmup_{warmup_part}"
    if rolling_history != "same-season":
        slug += f"_{rolling_history}"
    if use_prior_season_features:
        slug += f"_prior{prior_decay_games}"
    type_slug = season_type_slug(season_types)
    if type_slug != "regularseason":
        slug += f"_{type_slug}"
    if use_elo:
        slug += f"_elo_k{elo_k:g}_ha{elo_home_advantage:g}_co{elo_carryover:g}_mom3_5"
        if elo_playoff_k is not None:
            slug += f"_pk{elo_playoff_k:g}"
    return slug


def season_start_years(seasons: list[str]) -> set[str]:
    return {season.split("-")[0] for season in seasons}


def load_or_build_model_frames(
    seasons: list[str],
    rolling_window: int,
    min_periods: int,
    feature_set: str,
    feature_mode: str = "full",
    season_types: list[str] | None = None,
    warmup_seasons: list[str] | None = None,
    rolling_history: str = "same-season",
    use_prior_season_features: bool = False,
    prior_decay_games: int = 30,
    use_elo: bool = False,
    elo_k: float = 20,
    elo_playoff_k: float | None = None,
    elo_home_advantage: float = 65,
    elo_carryover: float = 0.75,
    refresh: bool = False,
    as_of=None,
    game_logs: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, MatchupData, pd.DataFrame | None]:
    PROCESSED_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    all_seasons = list(dict.fromkeys([*(warmup_seasons or []), *seasons]))
    logs = completed_game_logs(
        game_logs if game_logs is not None else load_game_logs(all_seasons, season_types=season_types, refresh=refresh), as_of)
    if logs.empty:
        raise ValueError("No completed games before the data cutoff.")
    settings = dict(seasons=seasons, rolling_window=rolling_window, min_periods=min_periods,
                    season_types=season_types, warmup_seasons=warmup_seasons, rolling_history=rolling_history,
                    use_prior_season_features=use_prior_season_features, prior_decay_games=prior_decay_games,
                    use_elo=use_elo, elo_k=elo_k, elo_playoff_k=elo_playoff_k,
                    elo_home_advantage=elo_home_advantage, elo_carryover=elo_carryover)
    identity = dict(settings=settings, source=frame_digest(logs), code=implementation_id(),
                    schema=SCHEMA_VERSION, runtime=runtime_versions())
    cache_path = PROCESSED_CACHE_DIR / f"frames_{digest(identity)}.joblib"
    if cache_path.exists() and not refresh:
        team_games, matchup_frame, latest_elos = joblib.load(cache_path)
    else:
        team_games = add_team_features(
            logs,
            rolling_window=rolling_window,
            min_periods=min_periods,
            rolling_history=rolling_history,
            use_prior_season_features=use_prior_season_features,
            prior_decay_games=prior_decay_games,
        )
        matchup_frame = build_matchup_frame(team_games, rolling_window=rolling_window, require_features=False)
        latest_elos = None
        if use_elo:
            matchup_frame, latest_elos = add_elo_features(
                matchup_frame,
                k_factor=elo_k,
                playoff_k_factor=elo_playoff_k,
                home_advantage=elo_home_advantage,
                carryover=elo_carryover,
            )
        # Elo sees the complete game sequence before rolling warmup rows are removed.
        rolling_columns = [c for c in matchup_frame if c.startswith(("home_rolling_", "away_rolling_"))]
        matchup_frame = matchup_frame.dropna(subset=rolling_columns).reset_index(drop=True)
        joblib.dump((team_games, matchup_frame, latest_elos), cache_path)

    target_years = season_start_years(seasons)
    if warmup_seasons and "SEASON_ID" in matchup_frame.columns:
        matchup_frame = matchup_frame[
            matchup_frame["SEASON_ID"].astype(str).str[-4:].isin(target_years)
        ].reset_index(drop=True)

    matchup_frame = add_interaction_features(matchup_frame, rolling_window=rolling_window)
    feature_names = select_feature_names(
        matchup_frame,
        feature_set=feature_set,
        use_elo=use_elo,
        feature_mode=feature_mode,
    )
    team_games.attrs["source_hash"] = identity["source"]
    team_games.attrs["history"] = logs
    return team_games, MatchupData(
        full=matchup_frame,
        x=matchup_frame[feature_names].fillna(0.0),
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
    warmup_seasons: list[str] | None = None,
    rolling_history: str = "same-season",
    use_prior_season_features: bool = False,
    prior_decay_games: int = 30,
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
        warmup_seasons=warmup_seasons,
        rolling_history=rolling_history,
        use_prior_season_features=use_prior_season_features,
        prior_decay_games=prior_decay_games,
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
    warmup_seasons: list[str] | None = None,
    rolling_history: str = "same-season",
    use_prior_season_features: bool = False,
    prior_decay_games: int = 30,
    use_elo: bool = False,
    elo_k: float = 20,
    elo_playoff_k: float | None = None,
    elo_home_advantage: float = 65,
    elo_carryover: float = 0.75,
    cv_splits: int = 0,
    cv_method: str = "timeseries",
    refresh: bool = False,
    as_of=None,
    game_logs: pd.DataFrame | None = None,
) -> TrainingResult:
    issued_at = cutoff_timestamp(as_of)
    team_games, matchup_data, latest_elos = load_or_build_model_frames(
        seasons=seasons,
        rolling_window=rolling_window,
        min_periods=min_periods,
        feature_set=feature_set,
        feature_mode=feature_mode,
        season_types=season_types,
        warmup_seasons=warmup_seasons,
        rolling_history=rolling_history,
        use_prior_season_features=use_prior_season_features,
        prior_decay_games=prior_decay_games,
        use_elo=use_elo,
        elo_k=elo_k,
        elo_playoff_k=elo_playoff_k,
        elo_home_advantage=elo_home_advantage,
        elo_carryover=elo_carryover,
        refresh=refresh,
        as_of=issued_at,
        game_logs=game_logs,
    )
    x, y = matchup_data.x, matchup_data.y

    if len(x) < 50:
        raise ValueError("Not enough games after feature engineering. Add more seasons.")

    split_index = holdout_split_index(matchup_data.full["GAME_DATE"], test_fraction)
    x_train, x_test = x.iloc[:split_index], x.iloc[split_index:]
    y_train, y_test = y.iloc[:split_index], y.iloc[split_index:]

    model = make_model()
    if cv_splits and cv_method == "walk-forward":
        cv_scores = walk_forward_cv_scores(model, x, y, matchup_data.full["GAME_DATE"], splits=cv_splits)
    elif cv_splits:
        cv_scores = temporal_cv_scores(model, x, y, splits=cv_splits, dates=matchup_data.full["GAME_DATE"])
    else:
        cv_scores = None
    model.fit(x_train, y_train)

    probabilities = model.predict_proba(x_test)[:, 1]
    metrics = evaluate_probabilities(y_test, probabilities)
    metrics["train_games"] = float(len(x_train))
    metrics["test_games"] = float(len(x_test))

    deployment_model = make_model()
    deployment_model.fit(x, y)
    metadata = dict(
        schema_version=SCHEMA_VERSION, implementation_id=implementation_id(), runtime=runtime_versions(),
        code_commit=git_revision(),
        data_cutoff=issued_at.isoformat(), training_data_hash=team_games.attrs["source_hash"],
        snapshot_hash=team_games.attrs["source_hash"],
        availability_policy="completed paired W/L games strictly before Eastern cutoff date; no historical ingestion timestamps",
        training_end=str(matchup_data.full["GAME_DATE"].max().date()),
        snapshot_end=str(team_games["GAME_DATE"].max().date()),
        evaluation_train_end=str(matchup_data.full["GAME_DATE"].iloc[split_index - 1].date()),
        holdout_start=str(matchup_data.full["GAME_DATE"].iloc[split_index].date()),
        holdout_split_index=split_index, deployment_games=len(x), evaluation_train_games=len(x_train),
        feature_names=matchup_data.feature_names,
        settings=dict(seasons=seasons, season_types=season_types or ["Regular Season"],
                      warmup_seasons=warmup_seasons or [], rolling_window=rolling_window,
                      min_periods=min_periods, feature_set=feature_set, feature_mode=feature_mode,
                      rolling_history=rolling_history, use_prior_season_features=use_prior_season_features,
                      prior_decay_games=prior_decay_games, use_elo=use_elo, elo_k=elo_k,
                      elo_playoff_k=elo_playoff_k, elo_home_advantage=elo_home_advantage,
                      elo_carryover=elo_carryover),
    )
    metadata["model_id"] = digest({"source": metadata["training_data_hash"], "settings": metadata["settings"],
                                    "code": metadata["implementation_id"], "runtime": metadata["runtime"],
                                    "features": matchup_data.feature_names})

    return TrainingResult(
        model=model,
        feature_names=matchup_data.feature_names,
        latest_team_features=latest_features_by_team(team_games, rolling_window=rolling_window),
        seasons=seasons,
        rolling_window=rolling_window,
        min_periods=min_periods,
        feature_set=feature_set,
        feature_mode=feature_mode,
        rolling_history=rolling_history,
        use_prior_season_features=use_prior_season_features,
        prior_decay_games=prior_decay_games,
        warmup_seasons=warmup_seasons or [],
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
        deployment_model=deployment_model,
        history=team_games.attrs["history"],
        metadata=metadata,
    )


def save_training_result(result: TrainingResult, path: Path = MODEL_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(result, path)
    path.with_suffix(".json").write_text(json.dumps(result.metadata, indent=2), encoding="utf-8")
    if path.parent.name != "versions":
        archive = path.parent / "versions" / f"{result.metadata['model_id']}.joblib"
        if not archive.exists():
            save_training_result(result, archive)


def load_training_result(path: Path = MODEL_PATH) -> TrainingResult:
    return joblib.load(path)


def update_prediction_history(result: TrainingResult, logs: pd.DataFrame, as_of=None) -> TrainingResult:
    """Advance data snapshots independently of estimator fitting."""
    history = completed_game_logs(logs, as_of)
    if history.empty:
        raise ValueError("No completed history available for prediction.")
    if history["GAME_DATE"].max() < pd.Timestamp(result.metadata["training_end"]):
        raise ValueError("Prediction history cannot precede model training history.")
    team_games = add_team_features(history, rolling_window=result.rolling_window, min_periods=result.min_periods,
                                   rolling_history=result.rolling_history,
                                   use_prior_season_features=result.use_prior_season_features,
                                   prior_decay_games=result.prior_decay_games)
    latest_elos = None
    if result.use_elo:
        _, latest_elos = add_elo_features(
            build_matchup_frame(team_games, result.rolling_window, require_features=False),
            k_factor=result.elo_k, playoff_k_factor=result.elo_playoff_k,
            home_advantage=result.elo_home_advantage, carryover=result.elo_carryover)
    metadata = {**result.metadata, "data_cutoff": cutoff_timestamp(as_of).isoformat(),
                "snapshot_hash": frame_digest(history), "snapshot_end": str(history["GAME_DATE"].max().date())}
    return replace(result, history=history, latest_elos=latest_elos,
                   latest_team_features=latest_features_by_team(team_games, result.rolling_window), metadata=metadata)
