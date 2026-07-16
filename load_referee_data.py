"""
Load referee_games.csv into Supabase archive_referee_games table.
Usage: DATABASE_URL=... python load_referee_data.py referee_games.csv
"""
import csv
import os
import sys
import psycopg2

if len(sys.argv) < 2:
    print("Usage: DATABASE_URL=... python load_referee_data.py referee_games.csv")
    sys.exit(1)

CSV_FILE = sys.argv[1]
DATABASE_URL = os.environ["DATABASE_URL"]

conn = psycopg2.connect(DATABASE_URL, connect_timeout=15)
cur = conn.cursor()

cur.execute("""
    CREATE TABLE IF NOT EXISTS archive_referee_games (
        game_id TEXT PRIMARY KEY,
        season SMALLINT,
        game_date TEXT,
        home_team TEXT,
        away_team TEXT,
        ref1 TEXT,
        ref2 TEXT,
        ref3 TEXT,
        home_pf SMALLINT,
        away_pf SMALLINT,
        home_fta SMALLINT,
        away_fta SMALLINT,
        home_pts SMALLINT,
        away_pts SMALLINT
    )
""")
conn.commit()

with open(CSV_FILE, newline="") as f:
    rows = list(csv.DictReader(f))

print(f"Loading {len(rows)} rows...")
CHUNK = 500
inserted = 0
for i in range(0, len(rows), CHUNK):
    chunk = rows[i:i+CHUNK]
    cur.executemany("""
        INSERT INTO archive_referee_games
            (game_id, season, game_date, home_team, away_team,
             ref1, ref2, ref3,
             home_pf, away_pf, home_fta, away_fta, home_pts, away_pts)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (game_id) DO UPDATE SET
            ref1=EXCLUDED.ref1, ref2=EXCLUDED.ref2, ref3=EXCLUDED.ref3,
            home_pf=EXCLUDED.home_pf, away_pf=EXCLUDED.away_pf,
            home_fta=EXCLUDED.home_fta, away_fta=EXCLUDED.away_fta,
            home_pts=EXCLUDED.home_pts, away_pts=EXCLUDED.away_pts
    """, [(
        r["game_id"], int(r["season"]) if r["season"] else None, r["game_date"],
        r["home_team"], r["away_team"], r["ref1"], r["ref2"], r["ref3"],
        int(r["home_pf"] or 0), int(r["away_pf"] or 0),
        int(r["home_fta"] or 0), int(r["away_fta"] or 0),
        int(r["home_pts"] or 0), int(r["away_pts"] or 0),
    ) for r in chunk])
    conn.commit()
    inserted += len(chunk)
    print(f"  {inserted}/{len(rows)}...")

print(f"Done. {len(rows)} rows loaded into archive_referee_games.")
cur.execute("SELECT COUNT(*) FROM archive_referee_games")
print(f"Table now has {cur.fetchone()[0]} rows.")
conn.close()
