from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
import json
import joblib

import pandas as pd

from config import PROJECT_ROOT, default_model_path
from modeling import (
    TrainingResult,
    load_or_build_model_frames,
    load_training_result,
    save_training_result,
    train_model,
    update_prediction_history,
)
from prediction import predict_matchup, predict_matchup_details
from data import load_game_logs
from provenance import SCHEMA_VERSION, implementation_id, runtime_versions, cutoff_timestamp, digest


CONFIG_PATH = PROJECT_ROOT / "production_config.txt"


@dataclass(frozen=True)
class ProductionConfig:
    seasons: list[str]
    season_types: list[str]
    rolling_window: int
    min_periods: int
    rolling_history: str
    use_prior_season_features: bool
    prior_decay_games: int
    feature_set: str
    feature_mode: str
    use_elo: bool
    elo_k: float
    elo_playoff_k: float | None
    elo_home_advantage: float
    elo_carryover: float
    upcoming_days: int
    retrain: bool
    refresh: bool
    betting_lines_csv: Path | None


@dataclass(frozen=True)
class UpcomingGame:
    game_date: date
    away_team: str
    home_team: str
    is_playoffs: bool = False
    away_score: float | None = None
    home_score: float | None = None
    game_id: str | None = None
    tipoff_at: str | None = None


def nba_season_label(start_year: int) -> str:
    return f"{start_year}-{str(start_year + 1)[-2:]}"


def current_nba_season_start(today: date | None = None) -> int:
    today = today or date.today()
    return today.year if today.month >= 10 else today.year - 1


def auto_recent_seasons(count: int = 3, today: date | None = None) -> list[str]:
    current_start = current_nba_season_start(today)
    start_years = range(current_start - count + 1, current_start + 1)
    return [nba_season_label(year) for year in start_years]


def parse_bool(value: str, default: bool = False) -> bool:
    if value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def parse_optional_float(value: str) -> float | None:
    value = value.strip()
    return float(value) if value else None


def parse_list(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def read_key_value_config(path: Path = CONFIG_PATH) -> dict[str, str]:
    if not path.exists():
        raise FileNotFoundError(f"Missing production config: {path}")

    settings: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"Invalid config line, expected key=value: {raw_line}")
        key, value = line.split("=", 1)
        settings[key.strip()] = value.strip()
    return settings


def load_production_config(path: Path = CONFIG_PATH, today: date | None = None) -> ProductionConfig:
    settings = read_key_value_config(path)
    seasons_value = settings.get("seasons", "auto_last_3")
    seasons = auto_recent_seasons(today=today) if seasons_value == "auto_last_3" else parse_list(seasons_value)
    betting_lines_value = settings.get("betting_lines_csv", "").strip()

    return ProductionConfig(
        seasons=seasons,
        season_types=parse_list(settings.get("season_types", "Regular Season,Playoffs")),
        rolling_window=int(settings.get("rolling_window", 10)),
        min_periods=int(settings.get("min_periods", 1)),
        rolling_history=settings.get("rolling_history", "same-season"),
        use_prior_season_features=parse_bool(settings.get("use_prior_season_features", "false")),
        prior_decay_games=int(settings.get("prior_decay_games", 30)),
        feature_set=settings.get("feature_set", "deltas"),
        feature_mode=settings.get("feature_mode", "lean"),
        use_elo=parse_bool(settings.get("use_elo", "true")),
        elo_k=float(settings.get("elo_k", 15)),
        elo_playoff_k=parse_optional_float(settings.get("elo_playoff_k", "15")),
        elo_home_advantage=float(settings.get("elo_home_advantage", 65)),
        elo_carryover=float(settings.get("elo_carryover", 0.65)),
        upcoming_days=int(settings.get("upcoming_days", 90)),
        retrain=parse_bool(settings.get("retrain", "false")),
        refresh=parse_bool(settings.get("refresh", "false")),
        betting_lines_csv=PROJECT_ROOT / betting_lines_value if betting_lines_value else None,
    )


def model_matches_config(result: TrainingResult, config: ProductionConfig) -> bool:
    checks = {
        "seasons": config.seasons,
        "season_types": config.season_types,
        "rolling_window": config.rolling_window,
        "min_periods": config.min_periods,
        "rolling_history": config.rolling_history,
        "use_prior_season_features": config.use_prior_season_features,
        "prior_decay_games": config.prior_decay_games,
        "feature_set": config.feature_set,
        "feature_mode": config.feature_mode,
        "use_elo": config.use_elo,
        "elo_k": config.elo_k,
        "elo_playoff_k": config.elo_playoff_k,
        "elo_home_advantage": config.elo_home_advantage,
        "elo_carryover": config.elo_carryover,
    }
    return all(getattr(result, key, None) == value for key, value in checks.items())


def production_model_path(config: ProductionConfig) -> Path:
    legacy = default_model_path(
        config.feature_set,
        config.rolling_window,
        config.min_periods,
        use_elo=config.use_elo,
        season_types=config.season_types,
        feature_mode=config.feature_mode,
        rolling_history=config.rolling_history,
        use_prior_season_features=config.use_prior_season_features,
        prior_decay_games=config.prior_decay_games,
    )
    return legacy.with_name(f"{legacy.stem}_v{SCHEMA_VERSION}{legacy.suffix}")


def load_or_train_production_model(config: ProductionConfig) -> tuple[TrainingResult, Path, bool]:
    path = production_model_path(config)
    issued_at = cutoff_timestamp()
    logs = load_game_logs(config.seasons, season_types=config.season_types, refresh=config.refresh)
    manifest = path.with_suffix(".json")
    compatible = False
    if manifest.exists():
        metadata = json.loads(manifest.read_text(encoding="utf-8"))
        compatible = (metadata.get("schema_version") == SCHEMA_VERSION
                      and metadata.get("implementation_id") == implementation_id()
                      and metadata.get("runtime") == runtime_versions())
    if path.exists() and compatible and not config.retrain:
        result = load_training_result(path)
        if model_matches_config(result, config):
            return update_prediction_history(result, logs, issued_at), path, False

    result = train_model(
        seasons=config.seasons,
        rolling_window=config.rolling_window,
        min_periods=config.min_periods,
        feature_set=config.feature_set,
        feature_mode=config.feature_mode,
        season_types=config.season_types,
        rolling_history=config.rolling_history,
        use_prior_season_features=config.use_prior_season_features,
        prior_decay_games=config.prior_decay_games,
        use_elo=config.use_elo,
        elo_k=config.elo_k,
        elo_playoff_k=config.elo_playoff_k,
        elo_home_advantage=config.elo_home_advantage,
        elo_carryover=config.elo_carryover,
        game_logs=logs,
        as_of=issued_at,
    )
    save_training_result(result, path)
    return result, path, True


def team_id_to_abbreviation() -> dict[int, str]:
    from nba_api.stats.static import teams

    return {team["id"]: team["abbreviation"] for team in teams.get_teams()}


def team_name_to_abbreviation() -> dict[str, str]:
    from nba_api.stats.static import teams

    mapping = {}
    for team in teams.get_teams():
        abbreviation = team["abbreviation"]
        for key in ["abbreviation", "nickname", "city", "full_name"]:
            value = team.get(key)
            if value:
                mapping[str(value).upper()] = abbreviation
    return mapping


def team_value_to_abbreviation(value, id_map: dict[int, str], name_map: dict[str, str]) -> str | None:
    if isinstance(value, dict):
        for key in ["teamTricode", "teamAbbreviation", "abbreviation", "TEAM_ABBREVIATION"]:
            if value.get(key):
                return str(value[key]).upper()
        for key in ["teamId", "TEAM_ID", "id"]:
            if value.get(key):
                return id_map.get(int(value[key]))
        for key in ["teamName", "teamCity", "teamSlug", "fullName"]:
            if value.get(key):
                return name_map.get(str(value[key]).upper())
    if pd.isna(value):
        return None
    if isinstance(value, (int, float)) and not pd.isna(value):
        return id_map.get(int(value))

    text = str(value).strip().upper()
    if len(text) <= 3:
        return text
    return name_map.get(text)


def normalize_schedule_frame(frame: pd.DataFrame) -> pd.DataFrame:
    rename_candidates = {
        "GAME_ID": ["GAME_ID", "gameId"],
        "STATUS_ID": ["GAME_STATUS_ID", "gameStatus"],
        "TIPOFF_AT": ["gameDateTimeUTC", "GAME_DATE_TIME_UTC"],
        "GAME_DATE": ["GAME_DATE", "GAME_DATE_EST", "GAME_DATE_TIME_EST", "gameDate", "gameDateEst", "gameDateTimeEst"],
        "HOME_TEAM": ["HOME_TEAM_ABBREVIATION", "HOME_TEAM_ABBREVIATION_NAME", "HOME_TEAM", "homeTeam", "homeTeam_teamTricode"],
        "AWAY_TEAM": ["VISITOR_TEAM_ABBREVIATION", "AWAY_TEAM_ABBREVIATION", "VISITOR_TEAM", "AWAY_TEAM", "awayTeam", "awayTeam_teamTricode"],
        "HOME_TEAM_ID": ["HOME_TEAM_ID", "homeTeamId", "homeTeam_teamId"],
        "AWAY_TEAM_ID": ["VISITOR_TEAM_ID", "AWAY_TEAM_ID", "awayTeamId", "awayTeam_teamId"],
        "STATUS": ["GAME_STATUS_TEXT", "GAME_STATUS_NAME", "STATUS_TEXT", "gameStatusText"],
        "HOME_SCORE": ["HOME_PTS", "HOME_SCORE", "homeTeam_score"],
        "AWAY_SCORE": ["VISITOR_PTS", "AWAY_PTS", "AWAY_SCORE", "awayTeam_score"],
        "GAME_SUBTYPE": ["gameSubtype"],
        "GAME_LABEL": ["gameLabel"],
        "SERIES_TEXT": ["seriesText"],
    }
    renamed: dict[str, str] = {}
    for target, candidates in rename_candidates.items():
        for candidate in candidates:
            if candidate in frame.columns:
                renamed[candidate] = target
                break

    df = frame.rename(columns=renamed).copy()
    id_map = team_id_to_abbreviation()
    name_map = team_name_to_abbreviation()
    if "HOME_TEAM" in df.columns:
        df["HOME_TEAM"] = df["HOME_TEAM"].map(lambda value: team_value_to_abbreviation(value, id_map, name_map))
    elif "HOME_TEAM_ID" in df.columns:
        df["HOME_TEAM"] = pd.to_numeric(df["HOME_TEAM_ID"], errors="coerce").map(id_map)
    if "AWAY_TEAM" in df.columns:
        df["AWAY_TEAM"] = df["AWAY_TEAM"].map(lambda value: team_value_to_abbreviation(value, id_map, name_map))
    elif "AWAY_TEAM_ID" in df.columns:
        df["AWAY_TEAM"] = pd.to_numeric(df["AWAY_TEAM_ID"], errors="coerce").map(id_map)

    required = {"GAME_DATE", "HOME_TEAM", "AWAY_TEAM"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Schedule data is missing columns: {', '.join(sorted(missing))}")

    df["GAME_DATE"] = pd.to_datetime(df["GAME_DATE"], errors="coerce").dt.date
    df = df.dropna(subset=["HOME_TEAM", "AWAY_TEAM"])
    df["HOME_TEAM"] = df["HOME_TEAM"].astype(str).str.upper()
    df["AWAY_TEAM"] = df["AWAY_TEAM"].astype(str).str.upper()
    for score_column in ["HOME_SCORE", "AWAY_SCORE"]:
        if score_column in df.columns:
            df[score_column] = pd.to_numeric(df[score_column], errors="coerce")
    return df.dropna(subset=["GAME_DATE", "HOME_TEAM", "AWAY_TEAM"])


def fetch_schedule_games(season: str) -> pd.DataFrame:
    from nba_api.stats.endpoints.scheduleleaguev2 import ScheduleLeagueV2

    response = ScheduleLeagueV2(league_id="00", season=season, timeout=60)
    frames = response.get_data_frames()
    errors = []
    for frame in frames:
        try:
            normalized = normalize_schedule_frame(frame)
        except ValueError as error:
            errors.append(str(error))
            continue
        if not normalized.empty:
            return normalized
    detail = "; ".join(errors) if errors else "no data frames returned"
    raise ValueError(f"NBA schedule endpoint returned no usable schedule data: {detail}")


def upcoming_games(config: ProductionConfig, today: date | None = None) -> list[UpcomingGame]:
    today = today or date.today()
    if config.upcoming_days < 0:
        raise ValueError("upcoming_days must be zero or greater.")
    end_date = today + timedelta(days=config.upcoming_days)
    season_years = range(current_nba_season_start(today), current_nba_season_start(end_date) + 1)
    schedule = pd.concat(
        [fetch_schedule_games(nba_season_label(year)) for year in season_years],
        ignore_index=True,
    )
    mask = (schedule["GAME_DATE"] >= today) & (schedule["GAME_DATE"] <= end_date)
    labels = schedule.reindex(columns=["GAME_LABEL", "GAME_SUBTYPE", "SERIES_TEXT"]).fillna("").astype(str).agg(" ".join, axis=1)
    is_playoffs = labels.str.contains("playoff|finals|first round|conference", case=False, regex=True)
    phase = pd.Series(None, index=schedule.index, dtype=object)
    if "GAME_ID" in schedule:
        prefixes = schedule["GAME_ID"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(10).str[:3]
        phase = prefixes.map({"001": "Preseason", "002": "Regular Season", "003": "All Star",
                              "004": "Playoffs", "005": "Play-In"})
        is_playoffs = is_playoffs.where(phase.isna(), phase.eq("Playoffs"))
    supported = schedule["HOME_TEAM"].isin(team_id_to_abbreviation().values()) & schedule["AWAY_TEAM"].isin(team_id_to_abbreviation().values())
    supported &= ~labels.str.contains("preseason|all.star", case=False, regex=True)
    supported &= phase.isna() | phase.isin(config.season_types)
    if "STATUS" in schedule:
        supported &= ~schedule["STATUS"].fillna("").str.contains("final", case=False)
    if "STATUS_ID" in schedule:
        supported &= pd.to_numeric(schedule["STATUS_ID"], errors="coerce").eq(1)
    if "TIPOFF_AT" in schedule:
        tipoffs = pd.to_datetime(schedule["TIPOFF_AT"], utc=True, errors="coerce")
        supported &= tipoffs.isna() | (tipoffs > cutoff_timestamp())
    if "Playoffs" not in config.season_types:
        supported &= ~is_playoffs
    if "Regular Season" not in config.season_types:
        supported &= is_playoffs
    excluded = int((mask & ~supported).sum())
    if excluded:
        print(f"Excluded {excluded} completed, exhibition, or out-of-scope games.", flush=True)
    schedule["IS_PLAYOFFS"] = is_playoffs
    mask &= supported
    future_games = (
        schedule.loc[mask]
        .drop_duplicates(subset=["GAME_DATE", "AWAY_TEAM", "HOME_TEAM"])
        .sort_values(["GAME_DATE", "AWAY_TEAM", "HOME_TEAM"])
    )

    games = []
    for row in future_games.itertuples(index=False):
        games.append(
            UpcomingGame(
                game_date=row.GAME_DATE,
                away_team=row.AWAY_TEAM,
                home_team=row.HOME_TEAM,
                is_playoffs=bool(row.IS_PLAYOFFS),
                game_id=str(row.GAME_ID) if hasattr(row, "GAME_ID") else None,
                tipoff_at=str(row.TIPOFF_AT) if hasattr(row, "TIPOFF_AT") else None,
            )
        )
    return games


def historical_games(
    season: str,
    season_type: str = "Playoffs",
    limit: int | None = None,
) -> list[UpcomingGame]:
    schedule = fetch_schedule_games(season)
    played = schedule.dropna(subset=["HOME_SCORE", "AWAY_SCORE"]).copy()

    if season_type == "Playoffs":
        playoff_mask = pd.Series(False, index=played.index)
        for column in ["GAME_SUBTYPE", "GAME_LABEL", "SERIES_TEXT"]:
            if column in played.columns:
                playoff_mask = playoff_mask | played[column].astype(str).str.contains(
                    "playoff|finals|semifinals|first round|conference|game ",
                    case=False,
                    regex=True,
                    na=False,
                )
        played = played.loc[playoff_mask]
    elif season_type == "Regular Season":
        if "GAME_SUBTYPE" in played.columns:
            played = played.loc[played["GAME_SUBTYPE"].isna() | (played["GAME_SUBTYPE"].astype(str).str.strip() == "")]

    played = played.sort_values(["GAME_DATE", "AWAY_TEAM", "HOME_TEAM"])
    if limit is not None:
        played = played.tail(limit)

    games = []
    for row in played.itertuples(index=False):
        games.append(
            UpcomingGame(
                game_date=row.GAME_DATE,
                away_team=row.AWAY_TEAM,
                home_team=row.HOME_TEAM,
                is_playoffs=season_type == "Playoffs",
                away_score=float(row.AWAY_SCORE),
                home_score=float(row.HOME_SCORE),
            )
        )
    return games


def american_odds_to_implied_probability(odds: float) -> float:
    if odds < 0:
        return abs(odds) / (abs(odds) + 100)
    return 100 / (odds + 100)


def load_betting_lines(path: Path | None) -> dict[tuple[str, str], dict[str, float]]:
    if path is None:
        return {}
    if not path.exists():
        raise FileNotFoundError(f"Betting lines file does not exist: {path}")

    df = pd.read_csv(path)
    required = {"home_team", "away_team", "home_odds", "away_odds"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Betting lines CSV is missing columns: {', '.join(sorted(missing))}")

    lines = {}
    for row in df.itertuples(index=False):
        home = str(row.home_team).upper()
        away = str(row.away_team).upper()
        home_raw = american_odds_to_implied_probability(float(row.home_odds))
        away_raw = american_odds_to_implied_probability(float(row.away_odds))
        total = home_raw + away_raw
        lines[(home, away)] = {
            "home_market_prob": home_raw / total,
            "away_market_prob": away_raw / total,
        }
    return lines


def prediction_rows(config: ProductionConfig, result: TrainingResult, games: list[UpcomingGame]) -> pd.DataFrame:
    lines = load_betting_lines(config.betting_lines_csv)
    rows = []
    issued_at = cutoff_timestamp()
    previous_dates = {}
    for game in sorted(games, key=lambda g: (g.game_date, g.away_team, g.home_team)):
        details = predict_matchup_details(
            result,
            home=game.home_team,
            away=game.away_team,
            is_playoffs=game.is_playoffs,
            game_date=game.game_date,
            as_of=issued_at,
            rest_dates=previous_dates,
        )
        home_probability = details["home_win_probability"]
        details["game_id"] = game.game_id
        details["tipoff_at"] = game.tipoff_at
        details["prediction_id"] = digest({k: v for k, v in details.items() if k != "prediction_id"})
        previous_dates.update({game.home_team: game.game_date, game.away_team: game.game_date})
        away_probability = 1 - home_probability
        predicted_winner = game.home_team if home_probability >= 0.5 else game.away_team
        confidence = max(home_probability, away_probability)
        line = lines.get((game.home_team, game.away_team), {})
        market_probability = line.get(
            "home_market_prob" if predicted_winner == game.home_team else "away_market_prob"
        )
        edge = confidence - market_probability if market_probability is not None else None

        rows.append(
            {
                "date": game.game_date.isoformat(),
                "game_id": game.game_id,
                "model_id": details["model_id"],
                "input_hash": details["input_hash"],
                "history_fallback": details["history_fallback"],
                "provenance": details,
                "away": game.away_team,
                "home": game.home_team,
                "away_score": game.away_score,
                "home_score": game.home_score,
                "predicted_winner": predicted_winner,
                "confidence": confidence,
                "home_win_probability": home_probability,
                "market_probability": market_probability,
                "edge": edge,
                "actual_winner": (
                    game.home_team
                    if game.home_score is not None and game.away_score is not None and game.home_score > game.away_score
                    else game.away_team
                    if game.home_score is not None and game.away_score is not None and game.away_score > game.home_score
                    else None
                ),
            }
        )
    frame = pd.DataFrame(rows)
    if not frame.empty and "actual_winner" in frame.columns:
        frame["correct"] = frame["actual_winner"].notna() & (frame["predicted_winner"] == frame["actual_winner"])
    return frame


def true_historical_prediction_rows(
    config: ProductionConfig,
    result: TrainingResult,
    season: str,
    season_type: str = "Playoffs",
    limit: int | None = 25,
) -> pd.DataFrame:
    score_games = historical_games(season=season, season_type=season_type)

    def load_frames(refresh: bool):
        return load_or_build_model_frames(
            seasons=config.seasons,
            rolling_window=config.rolling_window,
            min_periods=config.min_periods,
            feature_set=config.feature_set,
            feature_mode=config.feature_mode,
            season_types=config.season_types,
            rolling_history=config.rolling_history,
            use_prior_season_features=config.use_prior_season_features,
            prior_decay_games=config.prior_decay_games,
            use_elo=config.use_elo,
            elo_k=config.elo_k,
            elo_playoff_k=config.elo_playoff_k,
            elo_home_advantage=config.elo_home_advantage,
            elo_carryover=config.elo_carryover,
            refresh=refresh,
        )

    _, matchup_data, _ = load_frames(refresh=config.refresh)
    frame = matchup_data.full.copy()
    if "SEASON_ID" in frame.columns:
        frame = frame[frame["SEASON_ID"].astype(str).str[-4:] == season.split("-")[0]]
    if season_type == "Playoffs":
        frame = frame[frame["IS_PLAYOFFS"] == 1]
    elif season_type == "Regular Season":
        frame = frame[frame["IS_PLAYOFFS"] == 0]

    frame = frame.sort_values(["GAME_DATE", "AWAY_TEAM", "HOME_TEAM"])
    if score_games and not frame.empty:
        schedule_latest = max(game.game_date for game in score_games)
        frame_latest = pd.to_datetime(frame["GAME_DATE"]).dt.date.max()
        if schedule_latest > frame_latest and not config.refresh:
            _, matchup_data, _ = load_frames(refresh=True)
            frame = matchup_data.full.copy()
            if "SEASON_ID" in frame.columns:
                frame = frame[frame["SEASON_ID"].astype(str).str[-4:] == season.split("-")[0]]
            if season_type == "Playoffs":
                frame = frame[frame["IS_PLAYOFFS"] == 1]
            elif season_type == "Regular Season":
                frame = frame[frame["IS_PLAYOFFS"] == 0]
            frame = frame.sort_values(["GAME_DATE", "AWAY_TEAM", "HOME_TEAM"])

    if limit is not None:
        frame = frame.tail(limit)

    scores = {
        (game.game_date, game.away_team, game.home_team): game
        for game in score_games
    }
    x = frame.reindex(columns=result.feature_names, fill_value=0.0).fillna(0.0)
    probabilities = result.model.predict_proba(x)[:, 1]

    rows = []
    for probability, row in zip(probabilities, frame.itertuples(index=False)):
        home_probability = float(probability)
        away_probability = 1 - home_probability
        predicted_winner = row.HOME_TEAM if home_probability >= 0.5 else row.AWAY_TEAM
        confidence = max(home_probability, away_probability)
        game_date = pd.to_datetime(row.GAME_DATE).date()
        score_game = scores.get((game_date, row.AWAY_TEAM, row.HOME_TEAM))
        away_score = score_game.away_score if score_game else None
        home_score = score_game.home_score if score_game else None
        actual_winner = row.HOME_TEAM if int(row.HOME_WIN) == 1 else row.AWAY_TEAM

        rows.append(
            {
                "date": game_date.isoformat(),
                "away": row.AWAY_TEAM,
                "home": row.HOME_TEAM,
                "away_score": away_score,
                "home_score": home_score,
                "predicted_winner": predicted_winner,
                "confidence": confidence,
                "home_win_probability": home_probability,
                "market_probability": None,
                "edge": None,
                "actual_winner": actual_winner,
                "correct": predicted_winner == actual_winner,
            }
        )
    return pd.DataFrame(rows)


def format_probability(value) -> str:
    if pd.isna(value):
        return ""
    return f"{float(value):.1%}"


def print_prediction_table(rows: pd.DataFrame) -> None:
    if rows.empty:
        print("No upcoming NBA games found for the configured window.")
        return

    display = rows.copy()
    display = display.drop(columns=["provenance", "model_id", "input_hash"], errors="ignore")
    for column in ["confidence", "home_win_probability", "market_probability", "edge"]:
        display[column] = display[column].map(format_probability)
    print(display.to_string(index=False))


def run_production_predictions(config_path: Path = CONFIG_PATH) -> pd.DataFrame:
    today = date.today()
    config = load_production_config(config_path, today=today)
    end_date = today + timedelta(days=config.upcoming_days)
    print(
        f"Searching for games {config.upcoming_days} days ahead of "
        f"{today:%B} {today.day}, {today.year} "
        f"(through {end_date:%B} {end_date.day}, {end_date.year}).",
        flush=True,
    )
    games = upcoming_games(config, today=today)
    if not games:
        rows = pd.DataFrame()
        print_prediction_table(rows)
        return rows
    print(f"Found {len(games)} games. Loading prediction model...", flush=True)
    result, model_path, trained = load_or_train_production_model(config)
    rows = prediction_rows(config, result, games)
    output_dir = PROJECT_ROOT / "reports" / "forecasts"
    output_dir.mkdir(parents=True, exist_ok=True)
    run_stamp = cutoff_timestamp().strftime("%Y%m%dT%H%M%S%fZ")
    artifact = output_dir / f"{run_stamp}.json"
    snapshot_dir = PROJECT_ROOT / "data" / "processed" / "snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = snapshot_dir / f"{result.metadata['snapshot_hash']}.joblib"
    if not snapshot_path.exists():
        joblib.dump(result.history, snapshot_path)
    artifact.write_text(json.dumps({"model": result.metadata, "snapshot_path": str(snapshot_path),
                                   "predictions": rows.to_dict(orient="records")},
                                   indent=2, default=str), encoding="utf-8")

    print(f"Seasons: {', '.join(config.seasons)}")
    print(f"Model: {model_path}")
    print(f"Model trained this run: {'yes' if trained else 'no'}")
    print(f"Model training through: {result.metadata['training_end']}; data snapshot through: {result.metadata['snapshot_end']}")
    print(f"Forecast inputs and provenance: {artifact}")
    print()
    print_prediction_table(rows)
    return rows


def run_sample_predictions(
    config_path: Path = CONFIG_PATH,
    season: str | None = None,
    season_type: str = "Playoffs",
    limit: int | None = 25,
) -> pd.DataFrame:
    config = load_production_config(config_path)
    result, model_path, trained = load_or_train_production_model(config)
    season = season or nba_season_label(current_nba_season_start())
    rows = true_historical_prediction_rows(
        config=config,
        result=result,
        season=season,
        season_type=season_type,
        limit=limit,
    )

    print(f"Sample season: {season} ({season_type})")
    print(f"Model seasons: {', '.join(config.seasons)}")
    print(f"Model: {model_path}")
    print(f"Model trained this run: {'yes' if trained else 'no'}")
    print("Note: rows use each game's pre-game rolling stats/Elo, but this is not leakage-free if the model was trained on the same games.")
    print()
    print_prediction_table(rows)
    return rows
