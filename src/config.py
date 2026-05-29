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
) -> Path:
    elo_part = "_elo" if use_elo else ""
    type_part = ""
    if season_type_slug(season_types) != "regularseason":
        type_part = f"_{season_type_slug(season_types)}"
    return PROJECT_ROOT / "models" / f"game_predictor_{feature_set}{elo_part}{type_part}_rw{rolling_window}_min{min_periods}.joblib"
