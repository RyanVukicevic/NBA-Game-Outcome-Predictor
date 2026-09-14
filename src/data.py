from __future__ import annotations

import pandas as pd
from nba_api.stats.endpoints import leaguegamelog

from config import RAW_DIR
from provenance import local_day


def completed_game_logs(logs: pd.DataFrame, as_of=None) -> pd.DataFrame:
    """Use paired final W/L rows strictly before the Eastern calendar cutoff.

    Legacy logs do not record result publication times; this is an event-date
    replay, not a claim of historical point-in-time ingestion completeness.
    """
    df = logs.copy()
    df["GAME_DATE"] = pd.to_datetime(df["GAME_DATE"]).dt.normalize()
    df = df.loc[(df["GAME_DATE"] < local_day(as_of)) & df["WL"].isin(["W", "L"])].copy()
    if "GAME_STATUS_ID" in df:
        df = df.loc[pd.to_numeric(df["GAME_STATUS_ID"], errors="coerce") == 3]
    df["GAME_ID"] = df["GAME_ID"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(10)
    if df.duplicated(["GAME_ID", "TEAM_ID"]).any():
        raise ValueError("Duplicate team rows for one game; reconcile the source before forecasting.")
    groups = df.groupby("GAME_ID")
    df["_HOME_ROW"] = df["MATCHUP"].str.contains(" vs. ", regex=False).astype(int)
    groups = df.groupby("GAME_ID")
    valid = (groups.size().eq(2) & groups["WL"].nunique().eq(2) & groups["GAME_DATE"].nunique().eq(1)
             & groups["TEAM_ID"].nunique().eq(2) & groups["_HOME_ROW"].sum().eq(1))
    df = df.loc[df["GAME_ID"].isin(valid.index[valid])]
    df = df.drop(columns="_HOME_ROW")
    if df.duplicated(["TEAM_ID", "GAME_DATE"]).any():
        raise ValueError("Daily logs cannot order multiple games for one team on the same date.")
    return df.sort_values(["GAME_DATE", "GAME_ID", "TEAM_ID"]).reset_index(drop=True)


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
