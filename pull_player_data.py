"""
Pull player game logs from NBA Stats API and store in archive_player_game_logs.
Run locally (not on Railway — stats.nba.com blocks datacenter IPs).

Usage:
  python3 pull_player_data.py "Luka Doncic"
  python3 pull_player_data.py --bulk   # loads the preset star-player list
  python3 pull_player_data.py --all    # loads every player active 2014-2025 (resumable)
"""

import sys, time, os, unicodedata
import psycopg2, psycopg2.extras
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


def _norm(s):
    nfkd = unicodedata.normalize("NFKD", s.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def get_all_players_in_window():
    """Return all players with any activity between 2015 and 2025."""
    print("Fetching full player list from NBA API...")
    df = commonallplayers.CommonAllPlayers(
        is_only_current_season=0, league_id="00", season="2024-25"
    ).get_data_frames()[0]
    players = []
    for _, row in df.iterrows():
        try:
            from_year = int(row["FROM_YEAR"]) if row["FROM_YEAR"] else 0
            to_year   = int(row["TO_YEAR"])   if row["TO_YEAR"]   else 0
        except:
            continue
        # Active in any season from 2014-15 (year=2015) to 2024-25 (year=2025)
        if to_year >= 2015 and from_year <= 2025:
            players.append({
                "id":        str(row["PERSON_ID"]),
                "name":      row["DISPLAY_FIRST_LAST"],
                "from_year": from_year,
                "to_year":   to_year,
            })
    return players


def find_player(name):
    print(f"  Looking up player: {name}")
    df = commonallplayers.CommonAllPlayers(
        is_only_current_season=0, league_id="00", season="2024-25"
    ).get_data_frames()[0]
    q = _norm(name)
    match = df[df["DISPLAY_FIRST_LAST"].apply(_norm) == q]
    if match.empty:
        words = q.split()
        mask = df["DISPLAY_FIRST_LAST"].apply(_norm).apply(lambda n: all(w in n for w in words))
        match = df[mask]
    if match.empty:
        return None
    row = match.iloc[0]
    return {
        "id":        str(row["PERSON_ID"]),
        "name":      row["DISPLAY_FIRST_LAST"],
        "from_year": int(row["FROM_YEAR"]) if row["FROM_YEAR"] else 0,
        "to_year":   int(row["TO_YEAR"])   if row["TO_YEAR"]   else 0,
    }


def get_already_loaded_ids(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT player_id FROM archive_player_game_logs")
        return {row[0] for row in cur.fetchall()}


def pull_player_by_obj(player, conn, verbose=True):
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
            if verbose:
                print(f"    {season}: {len(logs)} games")
            time.sleep(0.6)
        except Exception as exc:
            if verbose:
                print(f"    {season}: error — {exc}")
            time.sleep(1)

    if not rows:
        return 0

    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, """
            INSERT INTO archive_player_game_logs (player_id, player_name, game_id, season, pf, fta, pts)
            VALUES %s
            ON CONFLICT (player_id, game_id) DO UPDATE SET
                pf = EXCLUDED.pf, fta = EXCLUDED.fta, pts = EXCLUDED.pts
        """, rows)
        conn.commit()
    return len(rows)


def pull_player(player_name, conn):
    player = find_player(player_name)
    if not player:
        print(f"  !! Player not found: {player_name}")
        return 0
    print(f"  Found: {player['name']} (ID {player['id']}, {player['from_year']}–{player['to_year']})")
    n = pull_player_by_obj(player, conn)
    print(f"  -> Stored {n} game rows for {player['name']}")
    return n


def main():
    conn = get_conn()
    ensure_table(conn)

    if "--all" in sys.argv:
        all_players = get_all_players_in_window()
        already_done = get_already_loaded_ids(conn)
        remaining = [p for p in all_players if p["id"] not in already_done]
        total_players = len(all_players)
        print(f"\n{total_players} players in window. {len(already_done)} already loaded. {len(remaining)} to fetch.\n")

        total_rows = 0
        for i, player in enumerate(remaining):
            pct = ((i + len(already_done)) / total_players * 100)
            print(f"[{i+1}/{len(remaining)} | {pct:.0f}%] {player['name']} ({player['from_year']}–{player['to_year']})")
            n = pull_player_by_obj(player, conn, verbose=False)
            if n:
                print(f"  -> {n} rows")
            total_rows += n

        conn.close()
        print(f"\nDone. {total_rows} new rows stored. All {total_players} players complete.")

    elif "--bulk" in sys.argv:
        print(f"Bulk loading {len(BULK_PLAYERS)} players...\n")
        total = 0
        for name in BULK_PLAYERS:
            print(f"\n=== {name} ===")
            total += pull_player(name, conn)
        conn.close()
        print(f"\nDone. {total} total game rows stored.")

    else:
        args = [a for a in sys.argv[1:] if not a.startswith("--")]
        if not args:
            print("Usage: python3 pull_player_data.py \"Player Name\"")
            print("       python3 pull_player_data.py --bulk")
            print("       python3 pull_player_data.py --all")
            sys.exit(1)
        player_name = " ".join(args)
        print(f"\n=== {player_name} ===")
        total = pull_player(player_name, conn)
        conn.close()
        print(f"\nDone. {total} total game rows stored.")


if __name__ == "__main__":
    main()
