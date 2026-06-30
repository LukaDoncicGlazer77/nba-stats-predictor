#!/usr/bin/env python3
"""
Scrapes Basketball-Reference for the current NBA season's advanced and
per-game stats, then upserts them into the Supabase Postgres DB.

Run daily via GitHub Actions during the NBA season (Oct–Jun).
Off-season (Jul–Sep): exits immediately with no DB writes.

Usage:
    DATABASE_URL=... python update_current_season.py [--season YEAR] [--dry-run]
"""
import argparse
import logging
import os
import re
import sys
import time
from datetime import date

import psycopg2
import psycopg2.extras
import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("update_current_season")

BR_BASE = "https://www.basketball-reference.com"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
REQUEST_DELAY = 4  # seconds between BR requests to avoid Akamai blocks


def current_season_year() -> int | None:
    """Returns the BR season end-year for the current NBA season.
    Returns None during the off-season (July–September)."""
    today = date.today()
    m = today.month
    if 7 <= m <= 9:
        return None  # off-season
    if m >= 10:
        return today.year + 1
    return today.year


def fetch_br_table(season_year: int, table_type: str) -> list[dict]:
    """Scrapes one BR stats table and returns a list of row dicts.

    table_type: "advanced" → /leagues/NBA_YEAR_advanced.html
                "per_game" → /leagues/NBA_YEAR_per_game.html
    """
    suffix = {"advanced": "advanced", "per_game": "per_game"}[table_type]
    table_id = {"advanced": "advanced", "per_game": "per_game_stats"}[table_type]
    url = f"{BR_BASE}/leagues/NBA_{season_year}_{suffix}.html"

    log.info("Fetching %s", url)
    resp = requests.get(url, headers=HEADERS, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"BR returned HTTP {resp.status_code} for {url}")
    resp.encoding = "utf-8"

    soup = BeautifulSoup(resp.text, "html.parser")
    table = soup.find("table", {"id": table_id})
    if table is None:
        # BR sometimes wraps tables in HTML comments for non-JS clients
        raw = resp.text
        start = raw.find(f'id="{table_id}"')
        if start == -1:
            raise RuntimeError(f"Could not find table #{table_id} on {url}")
        # Uncomment the comment block containing the table
        uncommented = re.sub(r"<!--(.*?)-->", r"\1", raw, flags=re.DOTALL)
        soup = BeautifulSoup(uncommented, "html.parser")
        table = soup.find("table", {"id": table_id})
        if table is None:
            raise RuntimeError(f"Table #{table_id} still missing after comment removal")

    rows = []
    for tr in table.find("tbody").find_all("tr"):
        # Skip repeated header rows and empty separators
        if tr.get("class") and "thead" in tr["class"]:
            continue
        cells = tr.find_all(["td", "th"])
        if not cells:
            continue

        row: dict[str, str | None] = {}
        for td in cells:
            stat = td.get("data-stat")
            if not stat:
                continue
            if stat == "name_display":
                # Player ID is in data-append-csv on the <td> itself
                row["player_id"] = td.get("data-append-csv")
                row["player"] = td.get_text(strip=True)
            else:
                text = td.get_text(strip=True)
                row[stat] = text if text not in ("", "\xa0") else None

        # Skip rows without a player_id (header separators, etc.)
        if not row.get("player_id"):
            continue

        rows.append(row)

    log.info("Parsed %d rows from %s", len(rows), url)
    return rows


def get_table_columns(conn, table_name: str) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT column_name FROM information_schema.columns
               WHERE table_schema = 'public' AND table_name = %s""",
            (table_name,),
        )
        return {row[0] for row in cur.fetchall()}


def to_float(v) -> float | None:
    if v is None:
        return None
    try:
        return float(str(v).replace(",", ""))
    except (ValueError, TypeError):
        return None


def build_advanced_rows(raw_rows: list[dict], season_year: int) -> list[dict]:
    """Maps BR data-stat keys to archive_advanced DB column names."""
    # BR data-stat → DB column
    STAT_MAP = {
        "age": "age",
        "team_name_abbr": "tm",
        "pos": "pos",
        "games": "g",
        "mp": "mp",
        "per": "per",
        "ts_pct": "ts_percent",
        "fg3a_per_fga_pct": "x3p_ar",
        "fta_per_fga_pct": "f_tr",
        "orb_pct": "orb_percent",
        "drb_pct": "drb_percent",
        "trb_pct": "trb_percent",
        "ast_pct": "ast_percent",
        "stl_pct": "stl_percent",
        "blk_pct": "blk_percent",
        "tov_pct": "tov_percent",
        "usg_pct": "usg_percent",
        "ows": "ows",
        "dws": "dws",
        "ws": "ws",
        "ws_per_48": "ws_48",
        "obpm": "obpm",
        "dbpm": "dbpm",
        "bpm": "bpm",
        "vorp": "vorp",
    }
    INT_COLS = {"g"}
    FLOAT_COLS = {v for v in STAT_MAP.values() if v not in ("tm", "pos", "g")}

    out = []
    for r in raw_rows:
        row = {
            "player_id": r["player_id"],
            "player": r.get("player"),
            "season": str(season_year),
        }
        for br_key, db_col in STAT_MAP.items():
            val = r.get(br_key)
            if db_col in FLOAT_COLS:
                row[db_col] = to_float(val)
            elif db_col in INT_COLS:
                row[db_col] = int(float(val)) if val is not None else None
            else:
                row[db_col] = val
        out.append(row)
    return out


def build_per_game_rows(raw_rows: list[dict], season_year: int) -> list[dict]:
    """Maps BR data-stat keys to archive_player_per_game DB column names."""
    STAT_MAP = {
        "pos": "pos",
        "age": "age",
        "team_name_abbr": "tm",
        "games": "g",
        "games_started": "gs",
        "mp_per_g": "mp_per_game",
        "fg_per_g": "fg_per_game",
        "fga_per_g": "fga_per_game",
        "fg_pct": "fg_percent",
        "fg3_per_g": "x3p_per_game",
        "fg3a_per_g": "x3pa_per_game",
        "fg3_pct": "x3p_percent",
        "fg2_per_g": "x2p_per_game",
        "fg2a_per_g": "x2pa_per_game",
        "fg2_pct": "x2p_percent",
        "efg_pct": "efg_percent",
        "ft_per_g": "ft_per_game",
        "fta_per_g": "fta_per_game",
        "ft_pct": "ft_percent",
        "orb_per_g": "orb_per_game",
        "drb_per_g": "drb_per_game",
        "trb_per_g": "trb_per_game",
        "ast_per_g": "ast_per_game",
        "stl_per_g": "stl_per_game",
        "blk_per_g": "blk_per_game",
        "tov_per_g": "tov_per_game",
        "pf_per_g": "pf_per_game",
        "pts_per_g": "pts_per_game",
    }
    STR_COLS = {"pos", "tm"}
    INT_COLS = {"g", "gs"}

    out = []
    for r in raw_rows:
        row = {
            "player_id": r["player_id"],
            "player": r.get("player"),
            "season": str(season_year),
        }
        for br_key, db_col in STAT_MAP.items():
            val = r.get(br_key)
            if db_col in STR_COLS:
                row[db_col] = val
            elif db_col in INT_COLS:
                row[db_col] = int(float(val)) if val is not None else None
            else:
                row[db_col] = to_float(val)
        out.append(row)
    return out


def _resolve_team_col(db_cols: set[str]) -> str:
    """Returns the actual team column name in the DB (handles tm vs team)."""
    if "team" in db_cols:
        return "team"
    return "tm"


def upsert_table(
    conn,
    table_name: str,
    rows: list[dict],
    season_year: int,
    dry_run: bool,
) -> None:
    """Deletes all rows for the season then bulk-inserts fresh data."""
    if not rows:
        log.warning("No rows to insert for %s season %s", table_name, season_year)
        return

    db_cols = get_table_columns(conn, table_name)
    if not db_cols:
        raise RuntimeError(f"Table {table_name} not found in DB or has no columns")

    # Resolve the team column name from the actual schema
    team_col = _resolve_team_col(db_cols)

    # Remap "tm" key in rows to whatever the DB actually calls it
    remapped = []
    for r in rows:
        nr = dict(r)
        if "tm" in nr and team_col != "tm":
            nr[team_col] = nr.pop("tm")
        elif team_col == "tm" and "team" in nr:
            nr["tm"] = nr.pop("team")
        remapped.append(nr)

    # Only insert columns that exist in the DB
    candidate_cols = [k for k in remapped[0].keys() if k in db_cols]
    missing_from_db = [k for k in remapped[0].keys() if k not in db_cols]
    if missing_from_db:
        log.debug("Skipping columns absent from %s: %s", table_name, missing_from_db)

    if dry_run:
        log.info("[DRY RUN] Would delete season=%s from %s and insert %d rows (cols: %s)",
                 season_year, table_name, len(remapped), candidate_cols)
        return

    with conn.cursor() as cur:
        cur.execute(
            f"DELETE FROM {table_name} WHERE season = %s",
            (str(season_year),),
        )
        deleted = cur.rowcount
        log.info("Deleted %d existing rows for season %s from %s", deleted, season_year, table_name)

        insert_sql = (
            f"INSERT INTO {table_name} ({', '.join(candidate_cols)}) "
            f"VALUES ({', '.join(['%s'] * len(candidate_cols))})"
        )
        batch = [[r.get(col) for col in candidate_cols] for r in remapped]
        psycopg2.extras.execute_batch(cur, insert_sql, batch, page_size=500)
        log.info("Inserted %d rows into %s", len(batch), table_name)

    conn.commit()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--season", type=int, help="Override season year (e.g. 2026)")
    parser.add_argument("--dry-run", action="store_true", help="Scrape but don't write to DB")
    args = parser.parse_args()

    season_year = args.season or current_season_year()
    if season_year is None:
        log.info("Off-season (Jul–Sep) — nothing to update.")
        sys.exit(0)

    log.info("Updating season %s stats", season_year)

    db_url = os.environ.get("DATABASE_URL")
    if not db_url and not args.dry_run:
        log.error("DATABASE_URL not set")
        sys.exit(1)

    # Scrape both tables (delay between requests)
    adv_raw = fetch_br_table(season_year, "advanced")
    time.sleep(REQUEST_DELAY)
    pg_raw = fetch_br_table(season_year, "per_game")

    adv_rows = build_advanced_rows(adv_raw, season_year)
    pg_rows = build_per_game_rows(pg_raw, season_year)

    log.info("Built %d advanced rows, %d per-game rows", len(adv_rows), len(pg_rows))

    if args.dry_run:
        log.info("[DRY RUN] Sample advanced row: %s", adv_rows[0] if adv_rows else None)
        log.info("[DRY RUN] Sample per-game row: %s", pg_rows[0] if pg_rows else None)
        return

    conn = psycopg2.connect(db_url, connect_timeout=10)
    try:
        upsert_table(conn, "archive_advanced", adv_rows, season_year, dry_run=False)
        upsert_table(conn, "archive_player_per_game", pg_rows, season_year, dry_run=False)
    finally:
        conn.close()

    log.info("Done.")


if __name__ == "__main__":
    main()
