"""Load L2M (Last Two Minute) report data into Supabase."""
import csv, os, psycopg2, psycopg2.extras

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres.ovgnihzycxdjzouurpfz:statfuel.online@aws-1-us-west-2.pooler.supabase.com:6543/postgres?sslmode=require"
)

conn = psycopg2.connect(DATABASE_URL)

with conn.cursor() as cur:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS archive_l2m (
            id              SERIAL PRIMARY KEY,
            game_id         TEXT,
            game_date       DATE,
            season          TEXT,
            playoff         BOOLEAN,
            period          TEXT,
            time_remaining  TEXT,
            call_type       TEXT,
            committing      TEXT,
            committing_team TEXT,
            disadvantaged   TEXT,
            disadvantaged_team TEXT,
            decision        TEXT,
            comments        TEXT,
            home_team       TEXT,
            away_team       TEXT,
            ref_1           TEXT,
            ref_2           TEXT,
            ref_3           TEXT
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_l2m_disadvantaged ON archive_l2m(lower(disadvantaged))")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_l2m_committing    ON archive_l2m(lower(committing))")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_l2m_game_id       ON archive_l2m(game_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_l2m_decision      ON archive_l2m(decision)")
    conn.commit()
    print("Table ready.")

rows = []
with open("l2m_raw.csv") as f:
    for r in csv.DictReader(f):
        decision = r.get("decision","").strip()
        if decision not in ("CC","CNC","INC","IC"):
            continue
        try:
            game_date = r.get("date","").strip() or r.get("game_date","").strip() or None
        except:
            game_date = None
        rows.append((
            r.get("nba_game_id","").strip() or None,
            game_date or None,
            r.get("season","").strip() or None,
            r.get("playoff","").strip() == "TRUE",
            r.get("period","").strip() or None,
            r.get("time","").strip() or None,
            r.get("call_type","").strip() or None,
            r.get("committing","").strip() or None,
            r.get("committing_team","").strip() or None,
            r.get("disadvantaged","").strip() or None,
            r.get("disadvantaged_team","").strip() or None,
            decision,
            r.get("comments","").strip() or None,
            r.get("home","").strip() or None,
            r.get("away","").strip() or None,
            r.get("ref_1","").strip() or None,
            r.get("ref_2","").strip() or None,
            r.get("ref_3","").strip() or None,
        ))

print(f"Inserting {len(rows)} rows...")
CHUNK = 500
with conn.cursor() as cur:
    for i in range(0, len(rows), CHUNK):
        psycopg2.extras.execute_values(cur, """
            INSERT INTO archive_l2m
              (game_id, game_date, season, playoff, period, time_remaining,
               call_type, committing, committing_team, disadvantaged, disadvantaged_team,
               decision, comments, home_team, away_team, ref_1, ref_2, ref_3)
            VALUES %s
        """, rows[i:i+CHUNK])
        conn.commit()
        print(f"  {min(i+CHUNK, len(rows))}/{len(rows)}...")

print(f"Done. {len(rows)} rows loaded.")
conn.close()
