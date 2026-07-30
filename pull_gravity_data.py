"""
Pull per-game season stats for all NBA players using LeagueDashPlayerStats.
Run locally (stats.nba.com blocks datacenter IPs — cannot run on Railway).

~11 API calls total, completes in under 60 seconds.

Usage:
    python3 pull_gravity_data.py              # all seasons 2014-15 to 2024-25
    python3 pull_gravity_data.py --season 2025  # single season (integer year)
"""

import os, sys, time
import psycopg2, psycopg2.extras
from nba_api.stats.endpoints import LeagueDashPlayerStats

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres.ovgnihzycxdjzouurpfz:statfuel.online@aws-1-us-west-2.pooler.supabase.com:6543/postgres?sslmode=require"
)

SEASONS = [f"{y}-{str(y+1)[2:]}" for y in range(2014, 2026)]  # 2014-15 → 2025-26
SLEEP = 1.2


def get_conn():
    return psycopg2.connect(DATABASE_URL)


def ensure_table(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS archive_player_season_stats (
                player_id   TEXT NOT NULL,
                player_name TEXT NOT NULL,
                team_abbr   TEXT NOT NULL DEFAULT '',
                season      INTEGER NOT NULL,
                gp          INTEGER,
                min_per_g   FLOAT,
                pts_per_g   FLOAT,
                fgm_per_g   FLOAT,
                fga_per_g   FLOAT,
                fg3m_per_g  FLOAT,
                fg3a_per_g  FLOAT,
                ftm_per_g   FLOAT,
                fta_per_g   FLOAT,
                fg3_pct     FLOAT,
                ft_pct      FLOAT,
                ast_per_g   FLOAT,
                tov_per_g   FLOAT,
                PRIMARY KEY (player_id, team_abbr, season)
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_apss_season ON archive_player_season_stats(season)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_apss_player ON archive_player_season_stats(player_id)")
        conn.commit()
    print("Table ready.")


def pull_season(season_str, conn):
    year = int(season_str.split("-")[0]) + 1  # "2024-25" → 2025
    print(f"  Fetching {season_str} (season={year})…", end=" ", flush=True)
    try:
        df = LeagueDashPlayerStats(
            season=season_str,
            per_mode_detailed="PerGame",
            measure_type_detailed_defense="Base",
            season_type_all_star="Regular Season",
        ).get_data_frames()[0]
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 0

    rows = []
    for _, r in df.iterrows():
        if (r.get("GP") or 0) < 5:
            continue
        fg3_pct = float(r["FG3_PCT"]) if r.get("FG3_PCT") is not None else 0.0
        ft_pct  = float(r["FT_PCT"])  if r.get("FT_PCT")  is not None else 0.0
        rows.append((
            str(int(r["PLAYER_ID"])),
            str(r["PLAYER_NAME"]),
            str(r.get("TEAM_ABBREVIATION") or ""),
            year,
            int(r["GP"]),
            round(float(r.get("MIN") or 0), 1),
            round(float(r.get("PTS") or 0), 1),
            round(float(r.get("FGM") or 0), 2),
            round(float(r.get("FGA") or 0), 1),
            round(float(r.get("FG3M") or 0), 2),
            round(float(r.get("FG3A") or 0), 1),
            round(float(r.get("FTM") or 0), 2),
            round(float(r.get("FTA") or 0), 1),
            fg3_pct,
            ft_pct,
            round(float(r.get("AST") or 0), 1),
            round(float(r.get("TOV") or 0), 1),
        ))

    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, """
            INSERT INTO archive_player_season_stats
                (player_id, player_name, team_abbr, season, gp, min_per_g,
                 pts_per_g, fgm_per_g, fga_per_g, fg3m_per_g, fg3a_per_g,
                 ftm_per_g, fta_per_g, fg3_pct, ft_pct, ast_per_g, tov_per_g)
            VALUES %s
            ON CONFLICT (player_id, team_abbr, season) DO UPDATE SET
                player_name = EXCLUDED.player_name,
                gp = EXCLUDED.gp, min_per_g = EXCLUDED.min_per_g,
                pts_per_g = EXCLUDED.pts_per_g, fgm_per_g = EXCLUDED.fgm_per_g,
                fga_per_g = EXCLUDED.fga_per_g, fg3m_per_g = EXCLUDED.fg3m_per_g,
                fg3a_per_g = EXCLUDED.fg3a_per_g, ftm_per_g = EXCLUDED.ftm_per_g,
                fta_per_g = EXCLUDED.fta_per_g, fg3_pct = EXCLUDED.fg3_pct,
                ft_pct = EXCLUDED.ft_pct, ast_per_g = EXCLUDED.ast_per_g,
                tov_per_g = EXCLUDED.tov_per_g
        """, rows)
        conn.commit()
    print(f"{len(rows)} players stored.")
    return len(rows)


def main():
    conn = get_conn()
    ensure_table(conn)

    if "--season" in sys.argv:
        idx = sys.argv.index("--season")
        year = int(sys.argv[idx + 1])
        season_str = f"{year-1}-{str(year)[2:]}"
        pull_season(season_str, conn)
    else:
        total = 0
        for s in SEASONS:
            total += pull_season(s, conn)
            time.sleep(SLEEP)
        print(f"\nDone. {total} total player-season rows stored.")

    conn.close()


if __name__ == "__main__":
    main()
