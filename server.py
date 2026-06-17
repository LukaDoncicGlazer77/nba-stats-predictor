#!/usr/bin/env python3
import decimal
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


def to_json(obj):
    """JSON serializer that handles Decimal and other PostgreSQL types."""
    if isinstance(obj, decimal.Decimal):
        return float(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)

    def send_json(self, payload, status=200):
        body = json.dumps(payload, default=to_json).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_json_rows(self, rows):
        body = json.dumps([dict(r) for r in rows], default=to_json).encode("utf-8")
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
                      safe_float(age) AS age,
                      safe_int(g) AS gp,
                      safe_float(mp_per_game) AS min,
                      safe_float(pts_per_game) AS pts,
                      safe_float(trb_per_game) AS reb,
                      safe_float(ast_per_game) AS ast,
                      safe_float(x3p_per_game) AS three,
                      safe_float(stl_per_game) AS stl,
                      safe_float(blk_per_game) AS blk,
                      safe_float(tov_per_game) AS tov,
                      safe_float(fg_percent) * 100 AS fg,
                      safe_float(x3p_percent) * 100 AS three_pct,
                      safe_float(ft_percent) * 100 AS ft_pct,
                      safe_float(bpm) AS net_rating,
                      safe_float(usg_percent) / 100 AS usg_pct,
                      safe_float(ts_percent) AS ts_pct,
                      season,
                      safe_int(season) AS season_start
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
                  MIN(safe_int(season)) AS first_season_start,
                  MAX(safe_int(season)) AS latest_season_start,
                  ROUND(AVG(safe_float(pts_per_game))::numeric, 1) AS career_pts,
                  ROUND(AVG(safe_float(trb_per_game))::numeric, 1) AS career_reb,
                  ROUND(AVG(safe_float(ast_per_game))::numeric, 1) AS career_ast,
                  ROUND((AVG(safe_float(ts_percent)) * 100)::numeric, 1) AS career_ts_pct
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
                    "SELECT MAX(safe_int(season)) FROM archive_player_per_game"
                )[0]
                season = int(params.get("season", [latest_season])[0] or latest_season)

                seasons_available = [r["season"] for r in q(conn,
                    "SELECT season FROM (SELECT DISTINCT season FROM archive_team_summaries) t ORDER BY safe_int(season) DESC"
                )]

                top_scorers = q(conn, """
                    SELECT player, player_id, team,
                           safe_float(pts_per_game) AS pts,
                           safe_float(trb_per_game) AS reb,
                           safe_float(ast_per_game) AS ast,
                           safe_float(fg_percent)*100 AS fg_pct
                    FROM archive_player_per_game
                    WHERE season = ?
                      AND pts_per_game != '' AND g != ''
                      AND safe_int(g) >= 20
                    ORDER BY safe_float(pts_per_game) DESC NULLS LAST LIMIT 10
                """, (str(season),))

                top_assisters = q(conn, """
                    SELECT player, player_id, team,
                           safe_float(ast_per_game) AS ast,
                           safe_float(pts_per_game) AS pts
                    FROM archive_player_per_game
                    WHERE season = ?
                      AND ast_per_game != '' AND g != ''
                      AND safe_int(g) >= 20
                    ORDER BY safe_float(ast_per_game) DESC NULLS LAST LIMIT 5
                """, (str(season),))

                top_rebounders = q(conn, """
                    SELECT player, player_id, team,
                           safe_float(trb_per_game) AS reb,
                           safe_float(pts_per_game) AS pts
                    FROM archive_player_per_game
                    WHERE season = ?
                      AND trb_per_game != '' AND g != ''
                      AND safe_int(g) >= 20
                    ORDER BY safe_float(trb_per_game) DESC NULLS LAST LIMIT 5
                """, (str(season),))

                awards = q(conn, """
                    SELECT a.award, a.player, a.player_id, a.winner, a.share
                    FROM archive_player_award_shares a
                    WHERE a.season = ? AND UPPER(a.winner) = 'TRUE'
                    ORDER BY a.award
                """, (str(season),))

                team_standings = q(conn, """
                    SELECT team, abbreviation, w, l,
                           CASE WHEN safe_float(w) IS NOT NULL AND safe_float(l) IS NOT NULL
                                AND (safe_float(w) + safe_float(l)) > 0
                                THEN ROUND((safe_float(w)/(safe_float(w)+safe_float(l)))::numeric, 3)
                                ELSE NULL END AS win_pct,
                           safe_float(n_rtg) AS net_rtg,
                           playoffs
                    FROM archive_team_summaries
                    WHERE season = ? AND abbreviation != 'NA'
                    ORDER BY safe_float(w) DESC NULLS LAST
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
                           ROUND(AVG(safe_float(p.pts_per_game))::numeric, 1) AS career_pts,
                           ROUND(AVG(safe_float(p.trb_per_game))::numeric, 1) AS career_reb,
                           ROUND(AVG(safe_float(p.ast_per_game))::numeric, 1) AS career_ast,
                           COUNT(p.season) AS seasons_played
                    FROM archive_draft_pick_history d
                    LEFT JOIN archive_player_per_game p ON p.player_id = d.player_id
                    WHERE d.season = ?
                    GROUP BY d.player_id, d.overall_pick, d.season, d.round, d.tm, d.player, d.college
                    ORDER BY safe_int(d.overall_pick)
                """, (season,))
                seasons_available = q(conn, """
                    SELECT season FROM (SELECT DISTINCT season FROM archive_draft_pick_history) t
                    ORDER BY safe_int(season) DESC
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
                    ORDER BY safe_int(rank)
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
                    ORDER BY safe_int(season) DESC, player
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
