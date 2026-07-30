"""
Pull per-game season stats for all NBA players using LeagueLeaders (Totals).
Supports seasons back to 1975-76. Run locally (stats.nba.com blocked on Railway).

~51 API calls, completes in ~2 minutes.

Usage:
    python3 pull_gravity_data.py              # all seasons 1975-76 to 2025-26
    python3 pull_gravity_data.py --season 2026  # single season (integer year)
"""

import os, sys, time
import psycopg2, psycopg2.extras
from nba_api.stats.endpoints import LeagueLeaders

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise SystemExit("Set DATABASE_URL environment variable before running.")

# 1975-76 → 2025-26  (season integer = ending year)
SEASONS = [f"{y}-{str(y+1)[2:]}" for y in range(1975, 2026)]
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
        df = LeagueLeaders(
            season=season_str,
            stat_category_abbreviation="PTS",
            per_mode48="Totals",
            season_type_all_star="Regular Season",
            league_id="00",
        ).get_data_frames()[0]
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 0

    rows = []
    for _, r in df.iterrows():
        gp = int(r.get("GP") or 0)
        if gp < 5:
            continue
        mn  = float(r.get("MIN") or 0)
        pts = float(r.get("PTS") or 0)
        fgm = float(r.get("FGM") or 0)
        fga = float(r.get("FGA") or 0)
        fg3m = float(r.get("FG3M") or 0)
        fg3a = float(r.get("FG3A") or 0)
        ftm  = float(r.get("FTM") or 0)
        fta  = float(r.get("FTA") or 0)
        ast  = float(r.get("AST") or 0)
        tov  = float(r.get("TOV") or 0)

        fg3_pct = (fg3m / fg3a) if fg3a > 0 else 0.0
        ft_pct  = (ftm  / fta)  if fta  > 0 else 0.0

        rows.append((
            str(int(r["PLAYER_ID"])),
            str(r["PLAYER"]),
            str(r.get("TEAM") or ""),
            year,
            gp,
            round(mn  / gp, 1),
            round(pts / gp, 1),
            round(fgm / gp, 2),
            round(fga / gp, 1),
            round(fg3m / gp, 2),
            round(fg3a / gp, 1),
            round(ftm  / gp, 2),
            round(fta  / gp, 1),
            round(fg3_pct, 3),
            round(ft_pct,  3),
            round(ast  / gp, 1),
            round(tov  / gp, 1),
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
