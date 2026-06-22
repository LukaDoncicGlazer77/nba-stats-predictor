#!/usr/bin/env python3
"""
Builds the historical career-outcome label set and persists it to
archive_draft_career_labels so label logic changes don't require
re-deriving from scratch every time, and so the labels can be audited
directly (e.g. via psql) independent of the training pipeline.

Usage:
    python build_career_labels.py [--current-season 2026]
"""
import argparse
import logging

import psycopg2.extras

from draft_projection.labels import build_career_labels
from server import connect, q

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("build_career_labels")

TABLE = "archive_draft_career_labels"

COLUMNS = [
    "player", "player_id", "season", "overall_pick", "round", "tm", "college",
    "seasons_played", "career_total_minutes", "starter_seasons",
    "peak_per", "peak_ws48", "peak_bpm",
    "all_star_count", "all_nba_count", "all_nba_1st_count", "max_award_share",
    "tier", "tier_label", "tier_rank",
]

SCHEMA = {
    "player": "TEXT", "player_id": "TEXT", "season": "SMALLINT", "overall_pick": "SMALLINT",
    "round": "TEXT", "tm": "TEXT", "college": "TEXT",
    "seasons_played": "SMALLINT", "career_total_minutes": "REAL", "starter_seasons": "SMALLINT",
    "peak_per": "REAL", "peak_ws48": "REAL", "peak_bpm": "REAL",
    "all_star_count": "SMALLINT", "all_nba_count": "SMALLINT", "all_nba_1st_count": "SMALLINT",
    "max_award_share": "REAL",
    "tier": "TEXT", "tier_label": "TEXT", "tier_rank": "SMALLINT",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--current-season", type=int, default=2026)
    args = parser.parse_args()

    conn = connect()
    try:
        labels = build_career_labels(conn, q, current_season=args.current_season)

        for col in COLUMNS:
            if col not in labels.columns:
                labels[col] = None
        labels = labels[COLUMNS]

        cur = conn.cursor()
        columns_sql = ",\n  ".join(f'"{c}" {t}' for c, t in SCHEMA.items())
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {TABLE} (
              id SERIAL PRIMARY KEY,
              {columns_sql}
            )
        """)
        cur.execute(f"TRUNCATE TABLE {TABLE}")

        values = [
            tuple(row[c] if row[c] == row[c] and row[c] is not None else None for c in COLUMNS)
            for row in labels.to_dict("records")
        ]
        psycopg2.extras.execute_values(
            cur, f'INSERT INTO {TABLE} ({", ".join(COLUMNS)}) VALUES %s', values, page_size=1000,
        )
        cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_player_id ON {TABLE} (player_id)")
        cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_tier ON {TABLE} (tier)")
        conn.commit()
        log.info("Wrote %d labeled players to %s", len(labels), TABLE)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
