#!/usr/bin/env python3
import json
import re
import sqlite3
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "nba.db"


def connect():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def rows_to_json(rows):
    return json.dumps([dict(row) for row in rows]).encode("utf-8")


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
        body = rows_to_json(rows)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if parsed.path == "/api/health":
            return self.send_json({"ok": True, "database": DB_PATH.name})

        if parsed.path == "/api/seasons":
            with connect() as db:
                rows = db.execute(
                    """
                    SELECT
                      player AS player_name,
                      player_id,
                      team AS team_abbreviation,
                      pos,
                      CAST(age AS REAL) AS age,
                      CAST(g AS INTEGER) AS gp,
                      CAST(mp_per_game AS REAL) AS min,
                      CAST(pts_per_game AS REAL) AS pts,
                      CAST(trb_per_game AS REAL) AS reb,
                      CAST(ast_per_game AS REAL) AS ast,
                      CAST(x3p_per_game AS REAL) AS three,
                      CAST(stl_per_game AS REAL) AS stl,
                      CAST(blk_per_game AS REAL) AS blk,
                      CAST(tov_per_game AS REAL) AS tov,
                      CAST(fg_percent AS REAL) * 100 AS fg,
                      CAST(x3p_percent AS REAL) * 100 AS three_pct,
                      CAST(ft_percent AS REAL) * 100 AS ft_pct,
                      CAST(bpm AS REAL) AS net_rating,
                      CAST(usg_percent AS REAL) / 100 AS usg_pct,
                      CAST(ts_percent AS REAL) AS ts_pct,
                      season,
                      CAST(season AS INTEGER) AS season_start
                    FROM archive_player_dashboard
                    ORDER BY player, CAST(season AS INTEGER)
                    """
                ).fetchall()
            return self.send_json_rows(rows)

        if parsed.path == "/api/players":
            search = (params.get("search", [""])[0] or "").strip()
            sql = """
                SELECT
                  player AS player_name,
                  player_id,
                  COUNT(*) AS seasons,
                  MIN(CAST(season AS INTEGER)) AS first_season_start,
                  MAX(CAST(season AS INTEGER)) AS latest_season_start,
                  ROUND(AVG(CAST(pts_per_game AS REAL)), 1) AS career_pts,
                  ROUND(AVG(CAST(trb_per_game AS REAL)), 1) AS career_reb,
                  ROUND(AVG(CAST(ast_per_game AS REAL)), 1) AS career_ast,
                  ROUND(AVG(CAST(ts_percent AS REAL)) * 100, 1) AS career_ts_pct
                FROM archive_player_dashboard
            """
            args = []
            if search:
                sql += " WHERE player LIKE ?"
                args.append(f"%{search}%")
            sql += " GROUP BY player_id, player ORDER BY seasons DESC, career_pts DESC, player LIMIT 200"
            with connect() as db:
                rows = db.execute(sql, args).fetchall()
            return self.send_json_rows(rows)

        if parsed.path == "/api/dashboard":
            with connect() as db:
                latest_season = db.execute(
                    "SELECT MAX(CAST(season AS INTEGER)) FROM archive_player_per_game"
                ).fetchone()[0]
                season = int(params.get("season", [latest_season])[0] or latest_season)

                seasons_available = [
                    r[0] for r in db.execute(
                        "SELECT DISTINCT season FROM archive_team_summaries ORDER BY CAST(season AS INTEGER) DESC"
                    ).fetchall()
                ]

                top_scorers = db.execute("""
                    SELECT player, player_id, team, CAST(pts_per_game AS REAL) AS pts,
                           CAST(trb_per_game AS REAL) AS reb,
                           CAST(ast_per_game AS REAL) AS ast,
                           CAST(fg_percent AS REAL)*100 AS fg_pct
                    FROM archive_player_per_game
                    WHERE season = ? AND pts_per_game != '' AND CAST(g AS INTEGER) >= 20
                    ORDER BY CAST(pts_per_game AS REAL) DESC LIMIT 10
                """, (str(season),)).fetchall()

                top_assisters = db.execute("""
                    SELECT player, player_id, team, CAST(ast_per_game AS REAL) AS ast,
                           CAST(pts_per_game AS REAL) AS pts
                    FROM archive_player_per_game
                    WHERE season = ? AND ast_per_game != '' AND CAST(g AS INTEGER) >= 20
                    ORDER BY CAST(ast_per_game AS REAL) DESC LIMIT 5
                """, (str(season),)).fetchall()

                top_rebounders = db.execute("""
                    SELECT player, player_id, team, CAST(trb_per_game AS REAL) AS reb,
                           CAST(pts_per_game AS REAL) AS pts
                    FROM archive_player_per_game
                    WHERE season = ? AND trb_per_game != '' AND CAST(g AS INTEGER) >= 20
                    ORDER BY CAST(trb_per_game AS REAL) DESC LIMIT 5
                """, (str(season),)).fetchall()

                awards = db.execute("""
                    SELECT a.award, a.player, a.player_id, a.winner, a.share
                    FROM archive_player_award_shares a
                    WHERE a.season = ? AND UPPER(a.winner) = 'TRUE'
                    ORDER BY a.award
                """, (str(season),)).fetchall()

                team_standings = db.execute("""
                    SELECT team, abbreviation, w, l,
                           ROUND(CAST(w AS REAL)/(CAST(w AS REAL)+CAST(l AS REAL)),3) AS win_pct,
                           CAST(n_rtg AS REAL) AS net_rtg,
                           playoffs
                    FROM archive_team_summaries
                    WHERE season = ? AND abbreviation != 'NA'
                    ORDER BY CAST(w AS REAL) DESC
                """, (str(season),)).fetchall()

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
            with connect() as db:
                rows = db.execute("""
                    SELECT d.season, d.overall_pick, d.round, d.tm AS team,
                           d.player, d.player_id, d.college,
                           ROUND(AVG(CAST(p.pts_per_game AS REAL)), 1) AS career_pts,
                           ROUND(AVG(CAST(p.trb_per_game AS REAL)), 1) AS career_reb,
                           ROUND(AVG(CAST(p.ast_per_game AS REAL)), 1) AS career_ast,
                           COUNT(p.season) AS seasons_played
                    FROM archive_draft_pick_history d
                    LEFT JOIN archive_player_per_game p ON p.player_id = d.player_id
                    WHERE d.season = ?
                    GROUP BY d.player_id, d.overall_pick
                    ORDER BY CAST(d.overall_pick AS INTEGER)
                """, (season,)).fetchall()
                seasons_available = db.execute("""
                    SELECT DISTINCT season FROM archive_draft_pick_history
                    ORDER BY CAST(season AS INTEGER) DESC
                """).fetchall()
            return self.send_json({
                "season": season,
                "picks": [dict(r) for r in rows],
                "seasons": [r[0] for r in seasons_available],
            })

        if parsed.path == "/api/prospects":
            with connect() as db:
                rows = db.execute("""
                    SELECT rank, name, pos, age, school, height, weight, status, country
                    FROM archive_draft_prospects_2026
                    ORDER BY rank
                """).fetchall()
            return self.send_json_rows(rows)

        if parsed.path == "/api/allstars":
            with connect() as db:
                rows = db.execute("""
                    SELECT player, player_id, team, season, lg, replaced
                    FROM archive_all_star_selections
                    ORDER BY CAST(season AS INTEGER) DESC, player
                    LIMIT 100
                """).fetchall()
            return self.send_json_rows(rows)

        # Photo proxy — fetches Basketball-Reference headshot server-side to avoid hotlink blocking
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
    if not DB_PATH.exists():
        raise SystemExit(f"Missing database: {DB_PATH}")

    server = ThreadingHTTPServer(("localhost", 8000), Handler)
    print("Serving NBA predictor at http://localhost:8000")
    print("API health check: http://localhost:8000/api/health")
    server.serve_forever()


if __name__ == "__main__":
    main()
