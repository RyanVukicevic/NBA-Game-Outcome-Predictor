from __future__ import annotations

import pandas as pd


ELO_COLUMNS = [
    "home_elo_pre",
    "away_elo_pre",
    "diff_elo_pre",
    "home_elo_change_last_3",
    "away_elo_change_last_3",
    "diff_elo_change_last_3",
    "home_elo_change_last_5",
    "away_elo_change_last_5",
    "diff_elo_change_last_5",
]


def expected_score(rating_a: float, rating_b: float) -> float:
    return 1 / (1 + 10 ** (-(rating_a - rating_b) / 400))


def regress_ratings_to_mean(
    ratings: dict[str, float],
    initial_elo: float,
    carryover: float,
) -> dict[str, float]:
    return {
        team: initial_elo + carryover * (rating - initial_elo)
        for team, rating in ratings.items()
    }


def season_year_key(season_id) -> str | None:
    if pd.isna(season_id):
        return None
    season_text = str(int(season_id)) if isinstance(season_id, float) else str(season_id)
    return season_text[-4:]


def add_elo_features(
    matchup_frame: pd.DataFrame,
    initial_elo: float = 1500,
    k_factor: float = 20,
    playoff_k_factor: float | None = None,
    home_advantage: float = 65,
    carryover: float = 0.75,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Add pre-game Elo features and return final team ratings."""
    playoff_k_factor = playoff_k_factor if playoff_k_factor is not None else k_factor
    df = matchup_frame.sort_values(["GAME_DATE", "GAME_ID"]).reset_index(drop=True).copy()
    ratings: dict[str, float] = {}
    rating_history: dict[str, list[float]] = {}
    rows = []
    current_season = None

    for _, row in df.iterrows():
        season_key = season_year_key(row.get("SEASON_ID"))
        if season_key is not None and season_key != current_season:
            if current_season is not None:
                ratings = regress_ratings_to_mean(ratings, initial_elo=initial_elo, carryover=carryover)
                rating_history = {team: [rating] for team, rating in ratings.items()}
            current_season = season_key

        home = row["HOME_TEAM"]
        away = row["AWAY_TEAM"]
        home_elo = ratings.get(home, initial_elo)
        away_elo = ratings.get(away, initial_elo)
        home_history = rating_history.setdefault(home, [home_elo])
        away_history = rating_history.setdefault(away, [away_elo])
        expected_home = expected_score(home_elo + home_advantage, away_elo)
        home_win = float(row["HOME_WIN"])

        updated = row.to_dict()
        updated["home_elo_pre"] = home_elo
        updated["away_elo_pre"] = away_elo
        updated["diff_elo_pre"] = home_elo - away_elo
        updated["elo_expected_home_win"] = expected_home
        for window in [3, 5]:
            home_anchor = home_history[-window - 1] if len(home_history) > window else home_history[0]
            away_anchor = away_history[-window - 1] if len(away_history) > window else away_history[0]
            home_change = home_elo - home_anchor
            away_change = away_elo - away_anchor
            updated[f"home_elo_change_last_{window}"] = home_change
            updated[f"away_elo_change_last_{window}"] = away_change
            updated[f"diff_elo_change_last_{window}"] = home_change - away_change
        rows.append(updated)

        active_k = playoff_k_factor if int(row.get("IS_PLAYOFFS", 0)) else k_factor
        elo_delta = active_k * (home_win - expected_home)
        ratings[home] = home_elo + elo_delta
        ratings[away] = away_elo - elo_delta
        home_history.append(ratings[home])
        away_history.append(ratings[away])

    latest_rows = []
    for team, rating in ratings.items():
        history = rating_history.get(team, [initial_elo])
        latest_row = {"TEAM_ABBREVIATION": team, "ELO": rating}
        for window in [3, 5]:
            anchor = history[-window - 1] if len(history) > window else history[0]
            latest_row[f"elo_change_last_{window}"] = rating - anchor
        latest_rows.append(latest_row)

    latest_elos = pd.DataFrame(latest_rows).sort_values("ELO", ascending=False).reset_index(drop=True)
    latest_elos.insert(0, "RANK", latest_elos.index + 1)
    latest_elos = latest_elos.set_index("TEAM_ABBREVIATION")
    return pd.DataFrame(rows), latest_elos
