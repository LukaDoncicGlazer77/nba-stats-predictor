#!/usr/bin/env python3
"""
Updates current team for every active NBA player using NBA.com roster data
via the nba_api package, with ESPN as a fallback for trades NBA.com is slow
to reflect. Designed for the offseason (July–September) when Basketball-
Reference has no current-season stats page to scrape.

Run daily via GitHub Actions alongside update_current_season.py.
During the NBA season (Oct–Jun) this script exits immediately — the BR
scraper already keeps teams current via full stat rewrites.

Usage:
    DATABASE_URL=... python update_offseason_rosters.py [--dry-run]
"""
import argparse
import json
import logging
import os
import sys
import time
import unicodedata
import urllib.request
from datetime import date

import psycopg2
import psycopg2.extras

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("update_offseason_rosters")

# Only run during the offseason months — the BR scraper owns Oct–Jun.
OFFSEASON_MONTHS = {7, 8, 9}

# NBA.com abbreviations that differ from Basketball-Reference
NBADOTCOM_TO_BR = {
    "BKN": "BRK",
    "CHA": "CHO",
    "PHX": "PHO",
}

# ESPN abbreviations that differ from Basketball-Reference
ESPN_TO_BR = {
    "BKN": "BRK",
    "CHA": "CHO",
    "PHX": "PHO",
    "GS": "GSW",
    "SA": "SAS",
    "NY": "NYK",
    "NO": "NOP",
    "UTAH": "UTA",
}


def is_offseason() -> bool:
    return date.today().month in OFFSEASON_MONTHS


def normalize(name: str) -> str:
    """Lowercase, strip accents, collapse whitespace — for fuzzy name matching."""
    nfkd = unicodedata.normalize("NFKD", name or "")
    ascii_only = "".join(c for c in nfkd if not unicodedata.combining(c))
    return " ".join(ascii_only.lower().split())


def fetch_nba_rosters() -> dict[str, str]:
    """Returns {normalized_player_name: br_team_abbreviation} for all active
    NBA players using a single PlayerIndex call."""
    from nba_api.stats.endpoints import PlayerIndex

    log.info("Fetching current rosters from NBA.com...")
    time.sleep(1)  # be polite before the request
    df = PlayerIndex(league_id="00").get_data_frames()[0]

    active = df[df["ROSTER_STATUS"] == 1.0]
    log.info("NBA.com returned %d active roster players", len(active))

    result = {}
    for _, row in active.iterrows():
        full_name = f"{row['PLAYER_FIRST_NAME']} {row['PLAYER_LAST_NAME']}"
        nba_abbr = str(row["TEAM_ABBREVIATION"] or "").strip()
        br_abbr = NBADOTCOM_TO_BR.get(nba_abbr, nba_abbr)
        if br_abbr:
            result[normalize(full_name)] = br_abbr

    return result


def fetch_espn_rosters() -> dict[str, str]:
    """Returns {normalized_player_name: br_team_abbreviation} by scraping all
    30 ESPN team rosters. Used as a fallback/override over NBA.com, which is
    often slow to reflect offseason trades."""
    teams_url = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams?limit=35"
    with urllib.request.urlopen(teams_url, timeout=15) as r:
        teams_data = json.loads(r.read())

    teams = teams_data["sports"][0]["leagues"][0]["teams"]
    log.info("ESPN: fetching rosters for %d teams...", len(teams))

    result = {}
    for t in teams:
        tid = t["team"]["id"]
        espn_abbr = t["team"]["abbreviation"]
        br_abbr = ESPN_TO_BR.get(espn_abbr, espn_abbr)
        roster_url = (
            f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
            f"/teams/{tid}/roster"
        )
        try:
            with urllib.request.urlopen(roster_url, timeout=15) as r:
                roster_data = json.loads(r.read())
            for athlete in roster_data.get("athletes", []):
                name = athlete.get("fullName", "")
                if name:
                    result[normalize(name)] = br_abbr
        except Exception as exc:
            log.warning("ESPN roster fetch failed for team %s: %s", espn_abbr, exc)
        time.sleep(0.1)

    log.info("ESPN: found %d active roster players", len(result))
    return result


def merge_roster_maps(nba_map: dict[str, str], espn_map: dict[str, str]) -> dict[str, str]:
    """Merges NBA.com and ESPN roster maps. ESPN takes precedence — it tends to
    reflect trades faster during the offseason. Logs any disagreements."""
    merged = dict(nba_map)
    disagreements = 0
    for name, espn_team in espn_map.items():
        nba_team = nba_map.get(name)
        if nba_team and nba_team != espn_team:
            log.info("  Source disagreement %-25s  NBA.com=%s  ESPN=%s  → using ESPN",
                     name, nba_team, espn_team)
            disagreements += 1
        merged[name] = espn_team
    if disagreements:
        log.info("%d player(s) where ESPN overrode NBA.com", disagreements)
    return merged


def most_recent_season(conn) -> str:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT MAX(season::int) FROM archive_player_dashboard
            WHERE season ~ '^[0-9]+$'
        """)
        return str(cur.fetchone()[0])


def update_teams(conn, roster_map: dict[str, str], season: str, dry_run: bool) -> None:
    """Updates the team column in archive_player_dashboard for all players
    whose current team differs from what NBA.com reports."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT player_id, player, team FROM archive_player_dashboard WHERE season = %s",
            (season,),
        )
        rows = cur.fetchall()

    updates = []
    unmatched = []
    for player_id, player_name, current_team in rows:
        key = normalize(player_name or "")
        new_team = roster_map.get(key)
        if new_team is None:
            unmatched.append(player_name)
            continue
        if new_team != current_team:
            updates.append((new_team, player_id, season))
            log.info("  %-25s  %s → %s", player_name, current_team, new_team)

    log.info("%d team changes found, %d players unmatched (retired/two-way/G-League)",
             len(updates), len(unmatched))
    if unmatched:
        log.debug("Unmatched: %s", unmatched[:20])

    if not updates:
        log.info("Nothing to update.")
        return

    if dry_run:
        log.info("[DRY RUN] Would apply %d team updates to archive_player_dashboard", len(updates))
        return

    with conn.cursor() as cur:
        psycopg2.extras.execute_batch(
            cur,
            "UPDATE archive_player_dashboard SET team = %s WHERE player_id = %s AND season = %s",
            updates,
            page_size=200,
        )
    conn.commit()
    log.info("Applied %d team updates.", len(updates))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Fetch but don't write to DB")
    parser.add_argument("--force", action="store_true",
                        help="Run even outside the offseason window (for testing)")
    args = parser.parse_args()

    if not args.force and not is_offseason():
        log.info("In-season (Oct–Jun) — BR scraper owns team updates. Exiting.")
        sys.exit(0)

    db_url = os.environ.get("DATABASE_URL")
    if not db_url and not args.dry_run:
        log.error("DATABASE_URL not set")
        sys.exit(1)

    nba_map = fetch_nba_rosters()
    try:
        espn_map = fetch_espn_rosters()
        roster_map = merge_roster_maps(nba_map, espn_map)
    except Exception as exc:
        log.warning("ESPN fallback failed (%s) — using NBA.com only", exc)
        roster_map = nba_map

    conn = psycopg2.connect(db_url, connect_timeout=10) if db_url else None
    try:
        season = most_recent_season(conn)
        log.info("Updating team column for season %s", season)
        update_teams(conn, roster_map, season, dry_run=args.dry_run)
    finally:
        if conn:
            conn.close()

    log.info("Done.")


if __name__ == "__main__":
    main()
