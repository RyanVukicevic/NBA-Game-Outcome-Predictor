from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_ROOT / "data" / "raw"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
PROCESSED_CACHE_DIR = PROCESSED_DIR / "cache"
REPORTS_DIR = PROJECT_ROOT / "reports"
MODEL_PATH = PROJECT_ROOT / "models" / "game_predictor.joblib"


BOX_SCORE_COLUMNS = [
    "PTS",
    "FG_PCT",
    "FG3_PCT",
    "FT_PCT",
    "OREB",
    "DREB",
    "REB",
    "AST",
    "STL",
    "BLK",
    "TOV",
    "PF",
    "PLUS_MINUS",
    "WIN",
]


ID_COLUMNS = [
    "GAME_ID",
    "GAME_DATE",
    "HOME_TEAM",
    "AWAY_TEAM",
    "HOME_WIN",
]


def season_type_slug(season_types: list[str] | None = None) -> str:
    season_types = season_types or ["Regular Season"]
    return "_".join(season_type.lower().replace(" ", "") for season_type in season_types)


def default_model_path(
    feature_set: str,
    rolling_window: int,
    min_periods: int,
    use_elo: bool = False,
    season_types: list[str] | None = None,
    feature_mode: str = "full",
    rolling_history: str = "same-season",
    use_prior_season_features: bool = False,
    prior_decay_games: int = 30,
) -> Path:
    elo_part = "_elo" if use_elo else ""
    type_part = ""
    if season_type_slug(season_types) != "regularseason":
        type_part = f"_{season_type_slug(season_types)}"
    mode_part = "" if feature_mode == "full" else f"_{feature_mode}"
    history_part = "" if rolling_history == "same-season" else f"_{rolling_history}"
    prior_part = f"_prior{prior_decay_games}" if use_prior_season_features else ""
    return PROJECT_ROOT / "models" / f"game_predictor_{feature_set}{elo_part}{type_part}{mode_part}{history_part}{prior_part}_rw{rolling_window}_min{min_periods}.joblib"
