#!/usr/bin/env python3
"""
Loads ncaa_stats.csv (produced by ../ncaa_scraper/ncaa_scraper.py) into the
archive_ncaa_player_stats table in the site's Postgres DB, replacing whatever
was there before.

Usage:
    python load_ncaa_stats.py path/to/ncaa_stats.csv
"""
import logging
import re
import sys

import pandas as pd
import psycopg2.extras

from server import connect

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("load_ncaa_stats")

TABLE = "archive_ncaa_player_stats"

# Mirrors ncaa_scraper.OUTPUT_COLUMNS. Kept as a literal dict (not imported
# from the scraper package) since this script and the scraper live in
# separate repos/deploy units.
SCHEMA = {
    "player_name": "TEXT", "team": "TEXT", "conference": "TEXT", "division": "SMALLINT",
    "position": "TEXT", "class_year": "TEXT", "height_in": "SMALLINT", "weight_lb": "SMALLINT",
    "season": "TEXT", "academic_year": "SMALLINT",
    "gp": "SMALLINT", "gs": "SMALLINT", "min": "REAL",
    "pts": "REAL", "reb": "REAL", "oreb": "REAL", "dreb": "REAL", "ast": "REAL",
    "stl": "REAL", "blk": "REAL", "tov": "REAL", "pf": "REAL",
    "fgm": "REAL", "fga": "REAL", "fg_pct": "REAL",
    "fg3m": "REAL", "fg3a": "REAL", "fg3_pct": "REAL",
    "ftm": "REAL", "fta": "REAL", "ft_pct": "REAL",
    "ts_pct": "REAL", "efg_pct": "REAL",
    "ast_pct": "REAL", "oreb_pct": "REAL", "dreb_pct": "REAL",
    "stl_pct": "REAL", "blk_pct": "REAL", "tov_pct": "REAL", "usg_pct": "REAL",
    "pts_per_game": "REAL", "reb_per_game": "REAL", "oreb_per_game": "REAL", "dreb_per_game": "REAL",
    "ast_per_game": "REAL", "stl_per_game": "REAL", "blk_per_game": "REAL", "tov_per_game": "REAL",
    "pf_per_game": "REAL", "fgm_per_game": "REAL", "fga_per_game": "REAL",
    "fg3m_per_game": "REAL", "fg3a_per_game": "REAL", "ftm_per_game": "REAL", "fta_per_game": "REAL",
    "min_per_game": "REAL",
    "pts_per40": "REAL", "reb_per40": "REAL", "ast_per40": "REAL",
    "stl_per40": "REAL", "blk_per40": "REAL", "tov_per40": "REAL",
    "data_quality_flag": "TEXT",
}


def normalize_name_for_match(name) -> str:
    """'Doe, John' -> 'john doe'; 'John Doe' -> 'john doe'. Used only as a
    join key against other sources (e.g. draft prospect names) -- the
    original player_name column is kept as-is for display. Must stay in sync
    with the matching function of the same name in server.py."""
    name = str(name or "").strip()
    if "," in name:
        last, first = name.split(",", 1)
        name = f"{first.strip()} {last.strip()}"
    return re.sub(r"[^a-z ]", "", name.lower()).strip()


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("Usage: python load_ncaa_stats.py path/to/ncaa_stats.csv")
    csv_path = sys.argv[1]

    log.info("Reading %s", csv_path)
    df = pd.read_csv(csv_path)
    log.info("Read %d rows, %d columns", len(df), len(df.columns))

    unknown_cols = set(df.columns) - set(SCHEMA)
    if unknown_cols:
        log.warning("Dropping columns not in the known schema: %s", sorted(unknown_cols))
        df = df.drop(columns=list(unknown_cols))
    for col in SCHEMA:
        if col not in df.columns:
            df[col] = None

    df["name_key"] = df["player_name"].map(normalize_name_for_match)

    conn = connect()
    try:
        cur = conn.cursor()
        columns_sql = ",\n  ".join(f'"{c}" {t}' for c, t in SCHEMA.items())
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {TABLE} (
              id SERIAL PRIMARY KEY,
              {columns_sql},
              name_key TEXT
            )
        """)
        cur.execute(f"TRUNCATE TABLE {TABLE}")

        cols = list(SCHEMA.keys()) + ["name_key"]
        values = [
            tuple(row[c] if pd.notna(row[c]) else None for c in cols)
            for _, row in df.iterrows()
        ]
        psycopg2.extras.execute_values(
            cur, f'INSERT INTO {TABLE} ({", ".join(cols)}) VALUES %s', values, page_size=1000,
        )

        cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_name_key ON {TABLE} (name_key)")
        cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_team_season ON {TABLE} (team, season)")
        conn.commit()
        log.info("Loaded %d rows into %s", len(df), TABLE)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
