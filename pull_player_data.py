"""
Pull player game logs from NBA Stats API and store in archive_player_game_logs.
Run locally (not on Railway — stats.nba.com blocks datacenter IPs).

Usage:
  python pull_player_data.py "Luka Doncic"
  python pull_player_data.py --bulk   # loads the preset star-player list
"""

import sys
import time
import os
import psycopg2
import psycopg2.extras
from nba_api.stats.endpoints import playergamelog, commonallplayers

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres.ovgnihzycxdjzouurpfz:statfuel.online@aws-1-us-west-2.pooler.supabase.com:6543/postgres?sslmode=require"
)

BULK_PLAYERS = [
    "LeBron James", "Stephen Curry", "Kevin Durant", "Giannis Antetokounmpo",
    "Luka Doncic", "Jayson Tatum", "Joel Embiid", "Nikola Jokic",
    "Damian Lillard", "James Harden", "Kawhi Leonard", "Paul George",
    "Anthony Davis", "Russell Westbrook", "Kyrie Irving", "Devin Booker",
    "Trae Young", "Donovan Mitchell", "Zion Williamson", "Ja Morant",
    "Jimmy Butler", "Karl-Anthony Towns", "Bam Adebayo", "Chris Paul",
    "Draymond Green", "Klay Thompson", "Bradley Beal", "DeMar DeRozan",
]

SEASONS = [f"{y}-{str(y+1)[2:]}" for y in range(2014, 2025)]  # 2014-15 to 2024-25


def get_conn():
    return psycopg2.connect(DATABASE_URL)


def ensure_table(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS archive_player_game_logs (
                player_id   TEXT NOT NULL,
                player_name TEXT NOT NULL,
                game_id     TEXT NOT NULL,
                season      TEXT,
                pf          INTEGER,
                fta         INTEGER,
                pts         INTEGER,
                PRIMARY KEY (player_id, game_id)
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_apgl_player_id ON archive_player_game_logs(player_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_apgl_game_id   ON archive_player_game_logs(game_id)")
        conn.commit()


def find_player(name):
    print(f"  Looking up player: {name}")
    all_players = commonallplayers.CommonAllPlayers(
        is_only_current_season=0, league_id="00", season="2024-25"
    ).get_data_frames()[0]
    name_lower = name.lower()
    # exact match
    match = all_players[all_players["DISPLAY_FIRST_LAST"].str.lower() == name_lower]
    if match.empty:
        # all-words match
        words = name_lower.split()
        mask = all_players["DISPLAY_FIRST_LAST"].str.lower().apply(
            lambda n: all(w in n for w in words)
        )
        match = all_players[mask]
    if match.empty:
        return None
    row = match.iloc[0]
    return {
        "id": str(row["PERSON_ID"]),
        "name": row["DISPLAY_FIRST_LAST"],
        "from_year": int(row["FROM_YEAR"]) if row["FROM_YEAR"] else 0,
        "to_year":   int(row["TO_YEAR"])   if row["TO_YEAR"]   else 0,
    }


def pull_player(player_name, conn):
    player = find_player(player_name)
    if not player:
        print(f"  !! Player not found: {player_name}")
        return 0

    print(f"  Found: {player['name']} (ID {player['id']}, {player['from_year']}–{player['to_year']})")

    rows = []
    for season in SEASONS:
        year = int(season.split("-")[0])
        if not (player["from_year"] <= year + 1 <= player["to_year"]):
            continue
        try:
            logs = playergamelog.PlayerGameLog(
                player_id=player["id"], season=season, season_type_all_star="Regular Season"
            ).get_data_frames()[0]
            for _, r in logs.iterrows():
                rows.append((
                    player["id"], player["name"], str(r["Game_ID"]),
                    season, int(r["PF"]), int(r["FTA"]), int(r["PTS"])
                ))
            print(f"    {season}: {len(logs)} games")
            time.sleep(0.7)
        except Exception as exc:
            print(f"    {season}: error — {exc}")
            time.sleep(1)

    if not rows:
        print(f"  No rows for {player['name']}")
        return 0

    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, """
            INSERT INTO archive_player_game_logs (player_id, player_name, game_id, season, pf, fta, pts)
            VALUES %s
            ON CONFLICT (player_id, game_id) DO UPDATE SET
                pf = EXCLUDED.pf, fta = EXCLUDED.fta, pts = EXCLUDED.pts
        """, rows)
        conn.commit()

    print(f"  -> Stored {len(rows)} game rows for {player['name']}")
    return len(rows)


def main():
    conn = get_conn()
    ensure_table(conn)

    if "--bulk" in sys.argv:
        players = BULK_PLAYERS
        print(f"Bulk loading {len(players)} players...\n")
    else:
        args = [a for a in sys.argv[1:] if not a.startswith("--")]
        if not args:
            print("Usage: python pull_player_data.py \"Player Name\"")
            print("       python pull_player_data.py --bulk")
            sys.exit(1)
        players = [" ".join(args)]

    total = 0
    for name in players:
        print(f"\n=== {name} ===")
        total += pull_player(name, conn)

    conn.close()
    print(f"\nDone. {total} total game rows stored.")


if __name__ == "__main__":
    main()
