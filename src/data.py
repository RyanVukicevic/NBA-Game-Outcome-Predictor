from __future__ import annotations

import pandas as pd
from nba_api.stats.endpoints import leaguegamelog

from config import RAW_DIR


def fetch_season_game_log(season: str, refresh: bool = False) -> pd.DataFrame:
    """Fetch one season of team game logs, with a CSV cache for repeat runs."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = RAW_DIR / f"league_game_log_{season.replace('-', '_')}.csv"

    if cache_path.exists() and not refresh:
        return pd.read_csv(cache_path)

    response = leaguegamelog.LeagueGameLog(
        season=season,
        season_type_all_star="Regular Season",
        player_or_team_abbreviation="T",
        direction="ASC",
        sorter="DATE",
        league_id="00",
        timeout=60,
    )
    df = response.get_data_frames()[0]
    df.to_csv(cache_path, index=False)
    return df


def load_game_logs(seasons: list[str], refresh: bool = False) -> pd.DataFrame:
    """Load multiple seasons and combine them into one DataFrame."""
    frames = [fetch_season_game_log(season, refresh=refresh) for season in seasons]
    if not frames:
        raise ValueError("Pass at least one season, for example: --seasons 2023-24")
    return pd.concat(frames, ignore_index=True)
