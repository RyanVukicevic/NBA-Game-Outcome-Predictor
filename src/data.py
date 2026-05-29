from __future__ import annotations

import pandas as pd
from nba_api.stats.endpoints import leaguegamelog

from config import RAW_DIR


def season_type_cache_slug(season_type: str) -> str:
    return season_type.lower().replace(" ", "_")


def fetch_season_game_log(season: str, season_type: str = "Regular Season", refresh: bool = False) -> pd.DataFrame:
    """Fetch one season of team game logs, with a CSV cache for repeat runs."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    season_slug = season.replace("-", "_")
    season_type_slug = season_type_cache_slug(season_type)
    cache_path = RAW_DIR / f"league_game_log_{season_slug}_{season_type_slug}.csv"
    legacy_cache_path = RAW_DIR / f"league_game_log_{season_slug}.csv"

    if season_type == "Regular Season" and legacy_cache_path.exists() and not cache_path.exists() and not refresh:
        cache_path = legacy_cache_path

    if cache_path.exists() and not refresh:
        df = pd.read_csv(cache_path)
        df["SEASON_TYPE"] = season_type
        return df

    response = leaguegamelog.LeagueGameLog(
        season=season,
        season_type_all_star=season_type,
        player_or_team_abbreviation="T",
        direction="ASC",
        sorter="DATE",
        league_id="00",
        timeout=60,
    )
    df = response.get_data_frames()[0]
    df["SEASON_TYPE"] = season_type
    df.to_csv(cache_path, index=False)
    return df


def load_game_logs(
    seasons: list[str],
    season_types: list[str] | None = None,
    refresh: bool = False,
) -> pd.DataFrame:
    """Load multiple seasons and combine them into one DataFrame."""
    season_types = season_types or ["Regular Season"]
    frames = [
        fetch_season_game_log(season, season_type=season_type, refresh=refresh)
        for season in seasons
        for season_type in season_types
    ]
    if not frames:
        raise ValueError("Pass at least one season, for example: --seasons 2023-24")
    return pd.concat(frames, ignore_index=True)
