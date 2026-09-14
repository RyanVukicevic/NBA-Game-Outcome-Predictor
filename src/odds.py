"""Read-only The Odds API v4 NBA moneyline adapter; no sportsbook credentials."""
from __future__ import annotations

import os

import pandas as pd
import requests
from nba_api.stats.static import teams

from tracking import utc

API_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds"
START_TIME_TOLERANCE = pd.Timedelta(minutes=15)


def ingest_odds(ledger, events, received=None, bookmakers=("draftkings", "fanduel")):
    received = utc(received)
    names = {t["full_name"]: t["abbreviation"] for t in teams.get_teams()}
    names["LA Clippers"] = "LAC"
    stats = dict(matched=0, time_offset_matches=0, unmatched=0, started=0, quotes=0, unavailable=0, invalid=0)
    matched_ids = set()
    if not isinstance(events, list):
        raise ValueError("Expected The Odds API event list.")
    for event in events:
        if event.get("sport_key") != "basketball_nba":
            stats["invalid"] += 1
            continue
        tipoff = utc(event["commence_time"])
        if tipoff <= received:
            stats["started"] += 1
            continue
        home, away = names.get(event["home_team"]), names.get(event["away_team"])
        candidates = ledger.rows("""SELECT g.id,s.tipoff,s.status FROM games g JOIN schedules s
            ON s.seq=(SELECT MAX(seq) FROM schedules WHERE game_id=g.id AND observed<=?)
            WHERE g.home=? AND g.away=?""", (received, home, away))
        # Books can list expected starts minutes after the official schedule.
        # Require a unique team-pair match; never replace the NBA cutoff time.
        matches = [g for g in candidates if g["status"] == "scheduled"
                   and abs(pd.Timestamp(g["tipoff"]) - pd.Timestamp(tipoff)) <= START_TIME_TOLERANCE]
        if len(matches) != 1:
            stats["unmatched"] += 1
            continue
        gid = matches[0]["id"]
        nba_tipoff = matches[0]["tipoff"]
        if nba_tipoff <= received:
            stats["started"] += 1
            continue
        matched_ids.add(gid)
        stats["matched"] += 1
        stats["time_offset_matches"] += int(nba_tipoff != tipoff)
        books = {b["key"]: b for b in event.get("bookmakers", [])}
        for bookmaker in bookmakers:
            book = books.get(bookmaker, {})
            markets = [m for m in book.get("markets", []) if m["key"] == "h2h"]
            market = markets[0] if len(markets) == 1 else {}
            outcomes = market.get("outcomes", [])
            prices = {o["name"]: o["price"] for o in outcomes}
            valid = len(outcomes) == 2 and set(prices) == {event["home_team"], event["away_team"]}
            updated = market.get("last_update") or book.get("last_update")
            payload = dict(provider="the-odds-api", event_id=event["id"], book=book,
                           provider_tipoff=tipoff, nba_tipoff=nba_tipoff,
                           start_offset_seconds=(pd.Timestamp(tipoff) - pd.Timestamp(nba_tipoff)).total_seconds())
            if valid and updated:
                try:
                    ledger.quote(gid, bookmaker, updated, nba_tipoff, prices[event["home_team"]],
                                 prices[event["away_team"]], received, payload=payload)
                    stats["quotes"] += 1
                    continue
                except (ValueError, TypeError):
                    stats["invalid"] += 1
            ledger.quote(gid, bookmaker, received, nba_tipoff, received=received,
                         status="unavailable", payload=payload)
            stats["unavailable"] += 1
    # A successful full-feed response that omits an event must not leave an old
    # quote eligible. API/network failures never reach this ingestion function.
    for game in ledger.rows("""SELECT s.* FROM schedules s WHERE s.seq=(
        SELECT MAX(seq) FROM schedules WHERE game_id=s.game_id AND observed<=?)
        AND s.status='scheduled' AND s.tipoff>?""", (received, received)):
        if game["game_id"] not in matched_ids:
            for bookmaker in bookmakers:
                ledger.quote(game["game_id"], bookmaker, received, game["tipoff"], received=received,
                             status="unavailable", payload={"provider": "the-odds-api", "reason": "event_absent_from_full_feed"})
                stats["unavailable"] += 1
    return stats


def collect_odds(ledger, bookmakers=("draftkings", "fanduel"), session=None, purpose="manual"):
    key = os.environ.get("ODDS_API_KEY")
    if not key:
        raise ValueError("Set ODDS_API_KEY locally. No request was made.")
    if not bookmakers or any(not b.replace("_", "").isalnum() for b in bookmakers):
        raise ValueError("Provide valid bookmaker keys.")
    session = session or requests
    from budget import reserve, finish
    request_id = reserve(ledger, purpose, cost=(len(bookmakers) + 9) // 10)
    try:
        response = session.get(API_URL, params=dict(apiKey=key, bookmakers=",".join(bookmakers),
                               markets="h2h", oddsFormat="decimal", dateFormat="iso"), timeout=30)
    except requests.RequestException:
        finish(ledger, request_id)
        # requests exceptions can include the URL with its secret query parameter.
        raise RuntimeError("Odds request failed; check connectivity. Credentials omitted.") from None
    if response.status_code != 200:
        finish(ledger, request_id, response.headers)
        raise RuntimeError(f"Odds API returned HTTP {response.status_code}; no data ingested. Check key/quota.")
    try:
        events = response.json()
    except ValueError:
        finish(ledger, request_id, response.headers)
        raise RuntimeError("Odds API returned invalid JSON.") from None
    finish(ledger, request_id, response.headers, success=True)
    received = utc()
    stats = ingest_odds(ledger, events, received, bookmakers)
    stats["credits_remaining"] = response.headers.get("x-requests-remaining")
    stats["credits_used"] = response.headers.get("x-requests-used")
    return stats
