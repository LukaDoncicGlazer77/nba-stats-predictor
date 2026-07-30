"""
Pull L2M (Last Two Minute) report data from the public atlhawksfanatic/L2M
GitHub repository and upsert into archive_l2m.

The repo is actively maintained through the current NBA season.
Run this locally whenever you want to refresh L2M data.

Usage:
    python3 pull_l2m_data.py
"""

import os, io, csv, sys, time
import psycopg2, psycopg2.extras

try:
    import requests
except ImportError:
    sys.exit("pip install requests")

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise SystemExit("Set DATABASE_URL environment variable before running.")

L2M_URL = "https://raw.githubusercontent.com/atlhawksfanatic/L2M/master/1-tidy/L2M/L2M.csv"

DECISIONS = {"CC", "CNC", "INC", "IC"}


def get_conn():
    return psycopg2.connect(DATABASE_URL)


def ensure_table(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS archive_l2m (
                id                  SERIAL PRIMARY KEY,
                game_id             TEXT,
                game_date           DATE,
                season              TEXT,
                playoff             BOOLEAN,
                period              TEXT,
                time_remaining      TEXT,
                call_type           TEXT,
                committing          TEXT,
                committing_team     TEXT,
                disadvantaged       TEXT,
                disadvantaged_team  TEXT,
                decision            TEXT,
                comments            TEXT,
                home_team           TEXT,
                away_team           TEXT,
                ref_1               TEXT,
                ref_2               TEXT,
                ref_3               TEXT
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_l2m_disadvantaged ON archive_l2m(lower(disadvantaged))")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_l2m_committing    ON archive_l2m(lower(committing))")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_l2m_game_id       ON archive_l2m(game_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_l2m_decision      ON archive_l2m(decision)")
        conn.commit()
    print("Table ready.")


def download_csv():
    print(f"Downloading L2M data from GitHub…", end=" ", flush=True)
    r = requests.get(L2M_URL, timeout=60)
    r.raise_for_status()
    print(f"{len(r.content)//1024} KB")
    return io.StringIO(r.text)


def parse_rows(f):
    rows = []
    reader = csv.DictReader(f)
    for r in reader:
        decision = (r.get("decision") or "").strip()
        if decision not in DECISIONS:
            continue

        game_date_raw = (r.get("game_date") or r.get("date") or "").strip()
        try:
            from datetime import datetime
            # Try common formats
            for fmt in ("%Y-%m-%d", "%b %d, %Y", "%m/%d/%Y"):
                try:
                    game_date = datetime.strptime(game_date_raw, fmt).date().isoformat()
                    break
                except ValueError:
                    game_date = None
        except Exception:
            game_date = None

        playoff_raw = (r.get("playoff") or "").strip().upper()
        playoff = playoff_raw in ("TRUE", "1", "YES")

        rows.append((
            (r.get("nba_game_id") or r.get("game_id") or "").strip() or None,
            game_date or None,
            (r.get("season") or "").strip() or None,
            playoff,
            (r.get("period") or "").strip() or None,
            (r.get("time") or "").strip() or None,
            (r.get("call_type") or "").strip() or None,
            (r.get("committing") or "").strip() or None,
            (r.get("committing_team") or "").strip() or None,
            (r.get("disadvantaged") or "").strip() or None,
            (r.get("disadvantaged_team") or "").strip() or None,
            decision,
            (r.get("comments") or "").strip() or None,
            (r.get("home") or "").strip() or None,
            (r.get("away") or "").strip() or None,
            (r.get("ref_1") or "").strip() or None,
            (r.get("ref_2") or "").strip() or None,
            (r.get("ref_3") or "").strip() or None,
        ))
    return rows


def load(conn, rows):
    print(f"Truncating and reloading {len(rows):,} rows…", end=" ", flush=True)
    with conn.cursor() as cur:
        cur.execute("TRUNCATE archive_l2m RESTART IDENTITY")
        CHUNK = 1000
        for i in range(0, len(rows), CHUNK):
            psycopg2.extras.execute_values(cur, """
                INSERT INTO archive_l2m
                  (game_id, game_date, season, playoff, period, time_remaining,
                   call_type, committing, committing_team, disadvantaged, disadvantaged_team,
                   decision, comments, home_team, away_team, ref_1, ref_2, ref_3)
                VALUES %s
            """, rows[i:i+CHUNK])
        conn.commit()
    print("done.")


def main():
    conn = get_conn()
    ensure_table(conn)
    f = download_csv()
    rows = parse_rows(f)
    print(f"Parsed {len(rows):,} valid rows (CC/CNC/INC/IC decisions).")
    load(conn, rows)
    conn.close()
    print("L2M data refresh complete.")


if __name__ == "__main__":
    main()
