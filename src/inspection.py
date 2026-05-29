from __future__ import annotations

from pathlib import Path

from config import ID_COLUMNS
from data import load_game_logs
from elo import add_elo_features
from features import add_interaction_features, add_team_features, build_matchup_dataset, select_feature_names


def export_model_stages(
    seasons: list[str],
    output_dir: Path,
    rolling_window: int = 10,
    min_periods: int = 5,
    feature_set: str = "deltas",
    feature_mode: str = "full",
    season_types: list[str] | None = None,
    warmup_seasons: list[str] | None = None,
    rolling_history: str = "same-season",
    use_prior_season_features: bool = False,
    use_elo: bool = False,
    elo_k: float = 20,
    elo_playoff_k: float | None = None,
    elo_home_advantage: float = 65,
    elo_carryover: float = 0.75,
    refresh: bool = False,
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)

    all_seasons = list(dict.fromkeys([*(warmup_seasons or []), *seasons]))
    raw_logs = load_game_logs(all_seasons, season_types=season_types, refresh=refresh)
    team_features = add_team_features(
        raw_logs,
        rolling_window=rolling_window,
        min_periods=min_periods,
        rolling_history=rolling_history,
        use_prior_season_features=use_prior_season_features,
    )
    deltas = build_matchup_dataset(team_features, rolling_window=rolling_window, feature_set="deltas")
    full = build_matchup_dataset(team_features, rolling_window=rolling_window, feature_set="full")

    if use_elo:
        deltas_frame = add_elo_features(
            deltas.full,
            k_factor=elo_k,
            playoff_k_factor=elo_playoff_k,
            home_advantage=elo_home_advantage,
            carryover=elo_carryover,
        )[0]
        full_frame = add_elo_features(
            full.full,
            k_factor=elo_k,
            playoff_k_factor=elo_playoff_k,
            home_advantage=elo_home_advantage,
            carryover=elo_carryover,
        )[0]
    else:
        deltas_frame = deltas.full
        full_frame = full.full

    deltas_frame = add_interaction_features(deltas_frame, rolling_window=rolling_window)
    full_frame = add_interaction_features(full_frame, rolling_window=rolling_window)
    deltas_feature_names = select_feature_names(
        deltas_frame,
        feature_set="deltas",
        use_elo=use_elo,
        feature_mode=feature_mode,
    )
    selected_frame = deltas_frame if feature_set == "deltas" else full_frame
    selected_feature_names = select_feature_names(
        selected_frame,
        feature_set=feature_set,
        use_elo=use_elo,
        feature_mode=feature_mode,
    )

    paths = {
        "raw_game_logs": output_dir / "01_raw_game_logs.csv",
        "team_rolling_features": output_dir / "02_team_rolling_features.csv",
        "matchups_deltas_only": output_dir / "03_matchups_deltas_only.csv",
        "matchups_full_home_away_diff": output_dir / "04_matchups_full_home_away_diff.csv",
        "training_features": output_dir / "05_training_features.csv",
    }

    raw_logs.to_csv(paths["raw_game_logs"], index=False)
    team_features.to_csv(paths["team_rolling_features"], index=False)
    deltas_frame[["GAME_ID", "GAME_DATE", "HOME_TEAM", "AWAY_TEAM", "HOME_WIN"] + deltas_feature_names].to_csv(
        paths["matchups_deltas_only"], index=False
    )
    full_frame.to_csv(paths["matchups_full_home_away_diff"], index=False)
    training_export = selected_frame[ID_COLUMNS + selected_feature_names]
    training_export.to_csv(paths["training_features"], index=False)

    return paths
