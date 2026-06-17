#!/usr/bin/env python3
import json
import os
import re
import traceback
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import psycopg2
import psycopg2.extras

ROOT = Path(__file__).resolve().parent
DATABASE_URL = os.environ.get("DATABASE_URL") or \
    "postgresql://postgres:LukaDoncic77@db.ovgnihzycxdjzouurpfz.supabase.co:5432/postgres"


def connect():
    return psycopg2.connect(DATABASE_URL)


def q(conn, sql, params=()):
    pg_sql = sql.replace("?", "%s")
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(pg_sql, params)
    return cur.fetchall()


def q1(conn, sql, params=()):
    pg_sql = sql.replace("?", "%s")
    cur = conn.cursor()
    cur.execute(pg_sql, params)
    return cur.fetchone()


# Safe cast helpers for text columns that may contain empty strings
def _n(col):
    """Wrap a column name in NULLIF(col, '') so empty strings cast safely."""
    return f"NULLIF({col}, '')"


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)

    def send_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_json_rows(self, rows):
        body = json.dumps([dict(r) for r in rows]).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        try:
            self._handle()
        except Exception as e:
            traceback.print_exc()
            try:
                self.send_json({"error": str(e)}, status=500)
            except Exception:
                pass

    def _handle(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if parsed.path == "/api/health":
            return self.send_json({"ok": True, "database": "supabase"})

        if parsed.path == "/api/seasons":
            conn = connect()
            try:
                rows = q(conn, """
                    SELECT
                      player AS player_name,
                      player_id,
                      team AS team_abbreviation,
                      pos,
                      CAST(NULLIF(age, '') AS REAL) AS age,
                      CAST(NULLIF(g, '') AS INTEGER) AS gp,
                      CAST(NULLIF(mp_per_game, '') AS REAL) AS min,
                      CAST(NULLIF(pts_per_game, '') AS REAL) AS pts,
                      CAST(NULLIF(trb_per_game, '') AS REAL) AS reb,
                      CAST(NULLIF(ast_per_game, '') AS REAL) AS ast,
                      CAST(NULLIF(x3p_per_game, '') AS REAL) AS three,
                      CAST(NULLIF(stl_per_game, '') AS REAL) AS stl,
                      CAST(NULLIF(blk_per_game, '') AS REAL) AS blk,
                      CAST(NULLIF(tov_per_game, '') AS REAL) AS tov,
                      CAST(NULLIF(fg_percent, '') AS REAL) * 100 AS fg,
                      CAST(NULLIF(x3p_percent, '') AS REAL) * 100 AS three_pct,
                      CAST(NULLIF(ft_percent, '') AS REAL) * 100 AS ft_pct,
                      CAST(NULLIF(bpm, '') AS REAL) AS net_rating,
                      CAST(NULLIF(usg_percent, '') AS REAL) / 100 AS usg_pct,
                      CAST(NULLIF(ts_percent, '') AS REAL) AS ts_pct,
                      season,
                      CAST(NULLIF(season, '') AS INTEGER) AS season_start
                    FROM archive_player_dashboard
                    ORDER BY player, season
                """)
            finally:
                conn.close()
            return self.send_json_rows(rows)

        if parsed.path == "/api/players":
            search = (params.get("search", [""])[0] or "").strip()
            sql = """
                SELECT
                  player AS player_name,
                  player_id,
                  COUNT(*) AS seasons,
                  MIN(CAST(NULLIF(season, '') AS INTEGER)) AS first_season_start,
                  MAX(CAST(NULLIF(season, '') AS INTEGER)) AS latest_season_start,
                  ROUND(AVG(CAST(NULLIF(pts_per_game, '') AS REAL))::numeric, 1) AS career_pts,
                  ROUND(AVG(CAST(NULLIF(trb_per_game, '') AS REAL))::numeric, 1) AS career_reb,
                  ROUND(AVG(CAST(NULLIF(ast_per_game, '') AS REAL))::numeric, 1) AS career_ast,
                  ROUND((AVG(CAST(NULLIF(ts_percent, '') AS REAL)) * 100)::numeric, 1) AS career_ts_pct
                FROM archive_player_dashboard
            """
            args = []
            if search:
                sql += " WHERE player ILIKE ?"
                args.append(f"%{search}%")
            sql += " GROUP BY player_id, player ORDER BY seasons DESC, career_pts DESC, player LIMIT 200"
            conn = connect()
            try:
                rows = q(conn, sql, args)
            finally:
                conn.close()
            return self.send_json_rows(rows)

        if parsed.path == "/api/dashboard":
            conn = connect()
            try:
                latest_season = q1(conn,
                    "SELECT MAX(CAST(NULLIF(season, '') AS INTEGER)) FROM archive_player_per_game"
                )[0]
                season = int(params.get("season", [latest_season])[0] or latest_season)

                seasons_available = [r["season"] for r in q(conn,
                    "SELECT season FROM (SELECT DISTINCT season FROM archive_team_summaries) t ORDER BY CAST(NULLIF(season, '') AS INTEGER) DESC"
                )]

                top_scorers = q(conn, """
                    SELECT player, player_id, team,
                           CAST(NULLIF(pts_per_game, '') AS REAL) AS pts,
                           CAST(NULLIF(trb_per_game, '') AS REAL) AS reb,
                           CAST(NULLIF(ast_per_game, '') AS REAL) AS ast,
                           CAST(NULLIF(fg_percent, '') AS REAL)*100 AS fg_pct
                    FROM archive_player_per_game
                    WHERE season = ?
                      AND pts_per_game != '' AND g != ''
                      AND CAST(NULLIF(g, '') AS INTEGER) >= 20
                    ORDER BY CAST(NULLIF(pts_per_game, '') AS REAL) DESC LIMIT 10
                """, (str(season),))

                top_assisters = q(conn, """
                    SELECT player, player_id, team,
                           CAST(NULLIF(ast_per_game, '') AS REAL) AS ast,
                           CAST(NULLIF(pts_per_game, '') AS REAL) AS pts
                    FROM archive_player_per_game
                    WHERE season = ?
                      AND ast_per_game != '' AND g != ''
                      AND CAST(NULLIF(g, '') AS INTEGER) >= 20
                    ORDER BY CAST(NULLIF(ast_per_game, '') AS REAL) DESC LIMIT 5
                """, (str(season),))

                top_rebounders = q(conn, """
                    SELECT player, player_id, team,
                           CAST(NULLIF(trb_per_game, '') AS REAL) AS reb,
                           CAST(NULLIF(pts_per_game, '') AS REAL) AS pts
                    FROM archive_player_per_game
                    WHERE season = ?
                      AND trb_per_game != '' AND g != ''
                      AND CAST(NULLIF(g, '') AS INTEGER) >= 20
                    ORDER BY CAST(NULLIF(trb_per_game, '') AS REAL) DESC LIMIT 5
                """, (str(season),))

                awards = q(conn, """
                    SELECT a.award, a.player, a.player_id, a.winner, a.share
                    FROM archive_player_award_shares a
                    WHERE a.season = ? AND UPPER(a.winner) = 'TRUE'
                    ORDER BY a.award
                """, (str(season),))

                team_standings = q(conn, """
                    SELECT team, abbreviation, w, l,
                           CASE WHEN NULLIF(w, '') IS NOT NULL AND NULLIF(l, '') IS NOT NULL
                                THEN ROUND((CAST(NULLIF(w,'') AS REAL)/(CAST(NULLIF(w,'') AS REAL)+CAST(NULLIF(l,'') AS REAL)))::numeric,3)
                                ELSE NULL END AS win_pct,
                           CAST(NULLIF(n_rtg, '') AS REAL) AS net_rtg,
                           playoffs
                    FROM archive_team_summaries
                    WHERE season = ? AND abbreviation != 'NA'
                    ORDER BY CAST(NULLIF(w, '') AS REAL) DESC NULLS LAST
                """, (str(season),))
            finally:
                conn.close()

            return self.send_json({
                "season": season,
                "seasons_available": seasons_available,
                "top_scorers": [dict(r) for r in top_scorers],
                "top_assisters": [dict(r) for r in top_assisters],
                "top_rebounders": [dict(r) for r in top_rebounders],
                "awards": [dict(r) for r in awards],
                "team_standings": [dict(r) for r in team_standings],
            })

        if parsed.path == "/api/draft":
            season = (params.get("season", ["2025"])[0] or "2025").strip()
            conn = connect()
            try:
                rows = q(conn, """
                    SELECT d.season, d.overall_pick, d.round, d.tm AS team,
                           d.player, d.player_id, d.college,
                           ROUND(AVG(CAST(NULLIF(p.pts_per_game, '') AS REAL))::numeric, 1) AS career_pts,
                           ROUND(AVG(CAST(NULLIF(p.trb_per_game, '') AS REAL))::numeric, 1) AS career_reb,
                           ROUND(AVG(CAST(NULLIF(p.ast_per_game, '') AS REAL))::numeric, 1) AS career_ast,
                           COUNT(p.season) AS seasons_played
                    FROM archive_draft_pick_history d
                    LEFT JOIN archive_player_per_game p ON p.player_id = d.player_id
                    WHERE d.season = ?
                    GROUP BY d.player_id, d.overall_pick, d.season, d.round, d.tm, d.player, d.college
                    ORDER BY CAST(NULLIF(d.overall_pick, '') AS INTEGER)
                """, (season,))
                seasons_available = q(conn, """
                    SELECT season FROM (SELECT DISTINCT season FROM archive_draft_pick_history) t
                    ORDER BY CAST(NULLIF(season, '') AS INTEGER) DESC
                """)
            finally:
                conn.close()
            return self.send_json({
                "season": season,
                "picks": [dict(r) for r in rows],
                "seasons": [r["season"] for r in seasons_available],
            })

        if parsed.path == "/api/prospects":
            conn = connect()
            try:
                rows = q(conn, """
                    SELECT rank, name, pos, age, school, height, weight, status, country
                    FROM archive_draft_prospects_2026
                    ORDER BY CAST(NULLIF(rank, '') AS INTEGER)
                """)
            finally:
                conn.close()
            return self.send_json_rows(rows)

        if parsed.path == "/api/allstars":
            conn = connect()
            try:
                rows = q(conn, """
                    SELECT player, player_id, team, season, lg, replaced
                    FROM archive_all_star_selections
                    ORDER BY CAST(NULLIF(season, '') AS INTEGER) DESC, player
                    LIMIT 100
                """)
            finally:
                conn.close()
            return self.send_json_rows(rows)

        # Photo proxy
        m = re.match(r"^/api/player-photo/([a-z0-9]+)$", parsed.path)
        if m:
            player_id = m.group(1)
            url = f"https://www.basketball-reference.com/req/202106291/images/players/{player_id}.jpg"
            try:
                req = urllib.request.Request(url, headers={
                    "User-Agent": "Mozilla/5.0",
                    "Referer": "https://www.basketball-reference.com/",
                })
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = resp.read()
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers()
                self.wfile.write(data)
            except Exception:
                self.send_response(404)
                self.end_headers()
            return

        if parsed.path.startswith("/api/"):
            return self.send_json({"error": "Not found"}, status=404)

        return super().do_GET()

    def log_message(self, format, *args):
        pass


def main():
    port = int(os.environ.get("PORT", 8000))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"Serving NBA predictor at http://0.0.0.0:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
