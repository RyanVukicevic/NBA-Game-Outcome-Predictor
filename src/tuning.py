from __future__ import annotations

from pathlib import Path

import pandas as pd

from modeling import train_model


def result_row(
    result,
    feature_set: str,
    feature_mode: str,
    season_types: list[str] | None,
    use_elo: bool,
    elo_k: float | None,
    elo_playoff_k: float | None,
    elo_home_advantage: float | None,
    elo_carryover: float | None,
    rolling_window: int,
    min_periods: int,
    warmup_seasons: list[str] | None = None,
    rolling_history: str = "same-season",
    use_prior_season_features: bool = False,
) -> dict[str, float | str | bool | None]:
    row = {
        "feature_set": feature_set,
        "feature_mode": feature_mode,
        "season_types": ",".join(season_types or ["Regular Season"]),
        "warmup_seasons": ",".join(warmup_seasons or []),
        "rolling_history": rolling_history,
        "use_prior_season_features": use_prior_season_features,
        "use_elo": use_elo,
        "elo_k": elo_k if use_elo else None,
        "elo_playoff_k": elo_playoff_k if use_elo else None,
        "elo_home_advantage": elo_home_advantage if use_elo else None,
        "elo_carryover": elo_carryover if use_elo else None,
        "rolling_window": rolling_window,
        "min_periods": min_periods,
        **result.metrics,
    }

    if result.cv_scores is not None:
        for metric in ["accuracy", "log_loss", "brier_score", "roc_auc"]:
            row[f"cv_mean_{metric}"] = result.cv_scores[metric].mean()
            row[f"cv_std_{metric}"] = result.cv_scores[metric].std()

    return row


def tune_model_grid(
    seasons: list[str],
    feature_sets: list[str],
    feature_modes: list[str],
    rolling_windows: list[int],
    min_periods_values: list[int],
    elo_k_values: list[float],
    elo_playoff_k_values: list[float | None],
    elo_carryover_values: list[float] | None = None,
    season_types: list[str] | None = None,
    warmup_seasons: list[str] | None = None,
    rolling_histories: list[str] | None = None,
    use_prior_season_features_values: list[bool] | None = None,
    elo_home_advantage: float = 65,
    cv_splits: int = 0,
    refresh: bool = False,
) -> pd.DataFrame:
    rows = []
    elo_carryover_values = elo_carryover_values or [0.75]
    rolling_histories = rolling_histories or ["same-season"]
    use_prior_season_features_values = use_prior_season_features_values or [False]

    for feature_set in feature_sets:
        for feature_mode in feature_modes:
            for rolling_history in rolling_histories:
                for use_prior_season_features in use_prior_season_features_values:
                    for rolling_window in rolling_windows:
                        for min_periods in min_periods_values:
                            if min_periods > rolling_window:
                                continue
                            for elo_k in elo_k_values:
                                for elo_playoff_k in elo_playoff_k_values:
                                    for elo_carryover in elo_carryover_values:
                                        result = train_model(
                                            seasons=seasons,
                                            rolling_window=rolling_window,
                                            min_periods=min_periods,
                                            feature_set=feature_set,
                                            feature_mode=feature_mode,
                                            season_types=season_types,
                                            warmup_seasons=warmup_seasons,
                                            rolling_history=rolling_history,
                                            use_prior_season_features=use_prior_season_features,
                                            use_elo=True,
                                            elo_k=elo_k,
                                            elo_playoff_k=elo_playoff_k,
                                            elo_home_advantage=elo_home_advantage,
                                            elo_carryover=elo_carryover,
                                            cv_splits=cv_splits,
                                            refresh=refresh,
                                        )
                                        rows.append(
                                            result_row(
                                                result=result,
                                                feature_set=feature_set,
                                                feature_mode=feature_mode,
                                                season_types=season_types,
                                                warmup_seasons=warmup_seasons,
                                                rolling_history=rolling_history,
                                                use_prior_season_features=use_prior_season_features,
                                                use_elo=True,
                                                elo_k=elo_k,
                                                elo_playoff_k=elo_playoff_k,
                                                elo_home_advantage=elo_home_advantage,
                                                elo_carryover=elo_carryover,
                                                rolling_window=rolling_window,
                                                min_periods=min_periods,
                                            )
                                        )

    results = pd.DataFrame(rows)
    if not results.empty:
        sort_columns = [
            column
            for column in ["cv_mean_log_loss", "log_loss", "cv_mean_brier_score", "brier_score"]
            if column in results.columns
        ]
        results = results.sort_values(sort_columns).reset_index(drop=True)
    return results


def tune_rolling_settings(
    seasons: list[str],
    rolling_windows: list[int],
    min_periods_values: list[int],
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
) -> pd.DataFrame:
    rows = []

    for rolling_window in rolling_windows:
        for min_periods in min_periods_values:
            if min_periods > rolling_window:
                continue

            result = train_model(
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
                cv_splits=cv_splits,
                refresh=refresh,
            )

            rows.append(
                result_row(
                    result=result,
                    feature_set=feature_set,
                    feature_mode=feature_mode,
                    season_types=season_types,
                    use_elo=use_elo,
                    elo_k=elo_k,
                    elo_playoff_k=elo_playoff_k,
                    elo_home_advantage=elo_home_advantage,
                    elo_carryover=elo_carryover,
                    rolling_window=rolling_window,
                    min_periods=min_periods,
                )
            )

    results = pd.DataFrame(rows)
    if not results.empty:
        results = results.sort_values(["rolling_window", "min_periods"]).reset_index(drop=True)
    return results


def tune_elo_settings(
    seasons: list[str],
    rolling_window: int,
    min_periods: int,
    elo_k_values: list[float],
    elo_home_advantages: list[float],
    elo_carryovers: list[float],
    elo_playoff_k: float | None = None,
    feature_set: str = "deltas",
    feature_mode: str = "full",
    season_types: list[str] | None = None,
    cv_splits: int = 0,
    refresh: bool = False,
) -> pd.DataFrame:
    rows = []

    for elo_k in elo_k_values:
        for elo_home_advantage in elo_home_advantages:
            for elo_carryover in elo_carryovers:
                result = train_model(
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
                    cv_splits=cv_splits,
                    refresh=refresh,
                )

                rows.append(
                    result_row(
                        result=result,
                        feature_set=feature_set,
                        feature_mode=feature_mode,
                        season_types=season_types,
                        use_elo=True,
                        elo_k=elo_k,
                        elo_playoff_k=elo_playoff_k,
                        elo_home_advantage=elo_home_advantage,
                        elo_carryover=elo_carryover,
                        rolling_window=rolling_window,
                        min_periods=min_periods,
                    )
                )

    results = pd.DataFrame(rows)
    if not results.empty:
        results = results.sort_values(
            ["elo_k", "elo_home_advantage", "elo_carryover"]
        ).reset_index(drop=True)
    return results


def save_tuning_results(results: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    results.to_csv(path, index=False)
