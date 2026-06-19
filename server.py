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

import archetype_engine

ROOT = Path(__file__).resolve().parent

# ── Salary model (loaded once at startup) ──────────────────────────────────
_salary_model = None
def _load_salary_model():
    global _salary_model
    if _salary_model is not None:
        return _salary_model
    model_path = ROOT / "salary_model.pkl"
    if not model_path.exists():
        return None
    try:
        import joblib
        _salary_model = joblib.load(model_path)
        print("Salary model loaded.")
    except Exception as e:
        print(f"Could not load salary model: {e}")
    return _salary_model

# ── Stats prediction model (loaded once) ───────────────────────────────────
_stats_model = None
def _load_stats_model():
    global _stats_model
    if _stats_model is not None:
        return _stats_model
    model_path = ROOT / "stats_model.pkl"
    if not model_path.exists():
        return None
    try:
        import joblib
        _stats_model = joblib.load(model_path)
        print("Stats model loaded.")
    except Exception as e:
        print(f"Could not load stats model: {e}")
    return _stats_model

SALARY_CAPS_M = {
    2015: 70.00, 2016: 94.143, 2017: 99.093, 2018: 101.869, 2019: 109.14,
    2020: 109.14, 2021: 112.414, 2022: 123.655, 2023: 136.021, 2024: 140.588,
    2025: 155.00, 2026: 170.00,
}
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


def safe_int_py(val):
    try:
        return int(str(val).strip())
    except (TypeError, ValueError):
        return None


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
                      safe_float(per) AS per,
                      safe_float(vorp) AS vorp,
                      safe_float(ws) AS ws,
                      safe_float(ows) AS ows,
                      safe_float(dws) AS dws,
                      season,
                      safe_int(season) AS season_start
                    FROM archive_player_dashboard
                    ORDER BY player, season
                """, ())
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
                    FROM archive_player_per_game p
                    WHERE season = ?
                      AND pts_per_game != '' AND g != ''
                      AND safe_int(g) >= 20
                      AND NOT (team NOT IN ('TOT','2TM','3TM') AND player IN (
                          SELECT player FROM archive_player_per_game
                          WHERE season = ? GROUP BY player HAVING COUNT(*) > 1
                      ))
                    ORDER BY safe_float(pts_per_game) DESC NULLS LAST LIMIT 10
                """, (str(season), str(season)))

                top_assisters = q(conn, """
                    SELECT player, player_id, team,
                           safe_float(ast_per_game) AS ast,
                           safe_float(pts_per_game) AS pts
                    FROM archive_player_per_game
                    WHERE season = ?
                      AND ast_per_game != '' AND g != ''
                      AND safe_int(g) >= 20
                      AND NOT (team NOT IN ('TOT','2TM','3TM') AND player IN (
                          SELECT player FROM archive_player_per_game
                          WHERE season = ? GROUP BY player HAVING COUNT(*) > 1
                      ))
                    ORDER BY safe_float(ast_per_game) DESC NULLS LAST LIMIT 5
                """, (str(season), str(season)))

                top_rebounders = q(conn, """
                    SELECT player, player_id, team,
                           safe_float(trb_per_game) AS reb,
                           safe_float(pts_per_game) AS pts
                    FROM archive_player_per_game
                    WHERE season = ?
                      AND trb_per_game != '' AND g != ''
                      AND safe_int(g) >= 20
                      AND NOT (team NOT IN ('TOT','2TM','3TM') AND player IN (
                          SELECT player FROM archive_player_per_game
                          WHERE season = ? GROUP BY player HAVING COUNT(*) > 1
                      ))
                    ORDER BY safe_float(trb_per_game) DESC NULLS LAST LIMIT 5
                """, (str(season), str(season)))

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

        if parsed.path == "/api/playoffs":
            season = (params.get("season", ["2026"])[0] or "2026").strip()
            conn = connect()
            try:
                rows = q(conn, """
                    SELECT season, conference, round,
                           team1, team1_abbrev, team1_seed, team1_wins,
                           team2, team2_abbrev, team2_seed, team2_wins,
                           winner_abbrev
                    FROM archive_playoff_series
                    WHERE season = ?
                    ORDER BY conference, round, team1_seed
                """, (season,))
            finally:
                conn.close()
            return self.send_json_rows(rows)

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

        if parsed.path == "/api/prospect-outcome":
            name = (params.get("name", [""])[0] or "").strip()
            if not name:
                return self.send_json({"error": "name required"}, status=400)
            conn = connect()
            try:
                prospect_rows = q(conn, """
                    SELECT rank, name, pos, age, school, height, weight, status, country
                    FROM archive_draft_prospects_2026 WHERE name = ?
                """, (name,))
                if not prospect_rows:
                    return self.send_json({"error": "Prospect not found"}, status=404)
                prospect = dict(prospect_rows[0])
                rank = safe_int_py(prospect.get("rank")) or 30

                picks = q(conn, """
                    WITH rookie_pos AS (
                        SELECT DISTINCT ON (player_id) player_id, pos
                        FROM archive_player_per_game
                        WHERE safe_int(season) IS NOT NULL
                        ORDER BY player_id, safe_int(season) ASC
                    ),
                    career AS (
                        SELECT player_id,
                               ROUND(AVG(safe_float(pts_per_game))::numeric, 1) AS career_pts,
                               ROUND(AVG(safe_float(trb_per_game))::numeric, 1) AS career_reb,
                               ROUND(AVG(safe_float(ast_per_game))::numeric, 1) AS career_ast,
                               COUNT(season) AS seasons_played,
                               ROUND(MAX(safe_float(pts_per_game))::numeric, 1) AS peak_pts
                        FROM archive_player_per_game
                        GROUP BY player_id
                    )
                    SELECT d.player, d.player_id, d.season AS draft_season, d.overall_pick, d.college,
                           rp.pos AS rookie_pos,
                           c.career_pts, c.career_reb, c.career_ast, c.seasons_played, c.peak_pts
                    FROM archive_draft_pick_history d
                    JOIN career c ON c.player_id = d.player_id
                    LEFT JOIN rookie_pos rp ON rp.player_id = d.player_id
                    WHERE safe_int(d.overall_pick) IS NOT NULL
                """)
            finally:
                conn.close()

            def pos_bucket(pos):
                p = (pos or "").upper()
                bucket = set()
                if "G" in p:
                    bucket.add("G")
                if "F" in p:
                    bucket.add("F")
                if "C" in p:
                    bucket.add("C")
                return bucket

            prospect_buckets = pos_bucket(prospect.get("pos"))
            scored = []
            for row in picks:
                r = dict(row)
                pick = safe_int_py(r.get("overall_pick"))
                if pick is None:
                    continue
                position_match = bool(prospect_buckets & pos_bucket(r.get("rookie_pos")))
                pick_distance = abs(pick - rank)
                draft_season = safe_int_py(r.get("draft_season")) or 0
                r["pick_distance"] = pick_distance
                r["position_match"] = position_match
                scored.append((pick_distance - (8 if position_match else 0), -draft_season, r))

            scored.sort(key=lambda triple: triple[:2])
            comps = [r for *_, r in scored[:8]]

            def avg(key):
                vals = [float(c[key]) for c in comps if c.get(key) is not None]
                return round(sum(vals) / len(vals), 1) if vals else None

            summary = {
                "avg_career_pts": avg("career_pts"),
                "avg_career_reb": avg("career_reb"),
                "avg_career_ast": avg("career_ast"),
                "avg_seasons_played": avg("seasons_played"),
                "comp_count": len(comps),
            }

            return self.send_json({"prospect": prospect, "comps": comps, "summary": summary})

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

        if parsed.path == "/api/archetype":
            player_id = (params.get("player_id", [""])[0] or "").strip()
            season = (params.get("season", [""])[0] or "").strip()
            if not player_id or not season:
                return self.send_json({"error": "player_id and season required"}, status=400)
            conn = connect()
            try:
                report = archetype_engine.build_player_report(conn, q, player_id, season)
            finally:
                conn.close()
            if report is None:
                return self.send_json({"error": "No qualified season found for that player_id/season"}, status=404)
            return self.send_json(report)

        if parsed.path == "/api/salary-predict":
            player_id = (params.get("player_id", [""])[0] or "").strip()
            if not player_id:
                return self.send_json({"error": "player_id required"}, status=400)
            bundle = _load_salary_model()
            if bundle is None:
                return self.send_json({"error": "Model not available"}, status=503)
            conn = connect()
            try:
                # Grab the player's most recent season stats
                row = q(conn, """
                    SELECT
                        safe_float(p.age) AS age, safe_float(p.g) AS g,
                        safe_float(p.gs) AS gs, safe_float(p.mp_per_game) AS mp_per_game,
                        safe_float(p.fg_per_game) AS fg_per_game,
                        safe_float(p.fga_per_game) AS fga_per_game,
                        safe_float(p.fg_percent) AS fg_percent,
                        safe_float(p.x3p_per_game) AS x3p_per_game,
                        safe_float(p.x3pa_per_game) AS x3pa_per_game,
                        safe_float(p.x3p_percent) AS x3p_percent,
                        safe_float(p.ft_per_game) AS ft_per_game,
                        safe_float(p.fta_per_game) AS fta_per_game,
                        safe_float(p.ft_percent) AS ft_percent,
                        safe_float(p.orb_per_game) AS orb_per_game,
                        safe_float(p.drb_per_game) AS drb_per_game,
                        safe_float(p.trb_per_game) AS trb_per_game,
                        safe_float(p.ast_per_game) AS ast_per_game,
                        safe_float(p.stl_per_game) AS stl_per_game,
                        safe_float(p.blk_per_game) AS blk_per_game,
                        safe_float(p.tov_per_game) AS tov_per_game,
                        safe_float(p.pts_per_game) AS pts_per_game,
                        safe_float(a.per) AS per,
                        safe_float(a.ts_percent) AS ts_percent,
                        safe_float(a.usg_percent) AS usg_percent,
                        safe_float(a.ows) AS ows, safe_float(a.dws) AS dws,
                        safe_float(a.ws) AS ws, safe_float(a.ws_48) AS ws_48,
                        safe_float(a.obpm) AS obpm, safe_float(a.dbpm) AS dbpm,
                        safe_float(a.bpm) AS bpm, safe_float(a.vorp) AS vorp,
                        p.pos, safe_int(p.season) AS season
                    FROM archive_player_per_game p
                    JOIN archive_advanced a
                      ON a.player_id = p.player_id AND a.season = p.season
                    WHERE p.player_id = ?
                    ORDER BY safe_int(p.season) DESC
                    LIMIT 1
                """, (player_id,))
            finally:
                conn.close()

            if not row:
                return self.send_json({"error": "Player not found"}, status=404)

            r = dict(row[0])
            model = bundle["model"]
            features = bundle["features"]
            positions = bundle["positions"]
            POSITIONS = ["C", "PF", "PG", "SF", "SG"]

            pos_primary = (r.get("pos") or "SF").split("-")[0].strip()
            if pos_primary not in POSITIONS:
                pos_primary = "SF"

            feat_vals = {}
            for f in features:
                if f.startswith("Pos_"):
                    pos = f.split("_", 1)[1]
                    feat_vals[f] = 1.0 if pos_primary == pos else 0.0
                else:
                    v = r.get(f)
                    feat_vals[f] = float(v) if v is not None else 0.0

            import numpy as np
            X = np.array([[feat_vals[f] for f in features]])
            salary_pct = float(model.predict(X)[0])
            salary_pct = max(0.005, min(salary_pct, 0.40))

            # Use next-season cap for valuation
            current_season = int(r.get("season") or 2026)
            next_season_start = current_season  # DB season = end year; next contract starts this summer
            cap_m = SALARY_CAPS_M.get(next_season_start, 155.0)
            predicted_m = round(salary_pct * cap_m, 2)

            return self.send_json({
                "player_id": player_id,
                "season": current_season,
                "salary_pct": round(salary_pct * 100, 1),
                "predicted_salary_m": predicted_m,
                "cap_m": cap_m,
            })

        if parsed.path == "/api/stats-predict":
            player_id = (params.get("player_id", [""])[0] or "").strip()
            if not player_id:
                return self.send_json({"error": "player_id required"}, status=400)
            bundle = _load_stats_model()
            if bundle is None:
                return self.send_json({"error": "Stats model not available"}, status=503)

            conn = connect()
            try:
                rows = q(conn, """
                    SELECT
                        safe_float(p.age) AS age,
                        safe_float(p.g) AS g,
                        safe_float(p.mp_per_game) AS mp_per_game,
                        safe_float(p.pts_per_game) AS pts_per_game,
                        safe_float(p.trb_per_game) AS trb_per_game,
                        safe_float(p.ast_per_game) AS ast_per_game,
                        safe_float(p.stl_per_game) AS stl_per_game,
                        safe_float(p.blk_per_game) AS blk_per_game,
                        safe_float(p.tov_per_game) AS tov_per_game,
                        safe_float(p.fg_percent) AS fg_percent,
                        safe_float(p.x3p_per_game) AS x3p_per_game,
                        safe_float(p.ft_percent) AS ft_percent,
                        safe_float(p.fga_per_game) AS fga_per_game,
                        safe_float(p.x3pa_per_game) AS x3pa_per_game,
                        safe_float(a.per) AS per,
                        safe_float(a.ts_percent) AS ts_percent,
                        safe_float(a.usg_percent) AS usg_percent,
                        safe_float(a.ws) AS ws,
                        safe_float(a.ws_48) AS ws_48,
                        safe_float(a.bpm) AS bpm,
                        safe_float(a.vorp) AS vorp,
                        safe_float(a.obpm) AS obpm,
                        safe_float(a.dbpm) AS dbpm,
                        safe_float(a.ows) AS ows,
                        safe_float(a.dws) AS dws,
                        p.pos,
                        safe_int(p.season) AS season
                    FROM archive_player_per_game p
                    JOIN archive_advanced a
                      ON a.player_id = p.player_id AND a.season = p.season
                    WHERE p.player_id = ?
                    ORDER BY safe_int(p.season) DESC
                    LIMIT 2
                """, (player_id,))
            finally:
                conn.close()

            if not rows:
                return self.send_json({"error": "Player not found"}, status=404)

            import numpy as np

            models   = bundle["models"]
            features = bundle["features"]
            lag_cols = bundle["lag_cols"]
            POSITIONS = bundle["positions"]
            targets  = bundle["targets"]

            s0 = dict(rows[0])  # most recent season
            s1 = dict(rows[1]) if len(rows) > 1 else {}

            def sf(d, k): return float(d.get(k) or 0)

            feat_vals = {}
            feat_vals["prev_age"] = sf(s0, "age")

            for col in lag_cols:
                feat_vals[f"lag1_{col}"] = sf(s0, col)
                feat_vals[f"lag2_{col}"] = sf(s1, col) if s1 else 0.0
                feat_vals[f"delta_{col}"] = feat_vals[f"lag1_{col}"] - feat_vals[f"lag2_{col}"]

            pos_primary = (s0.get("pos") or "SF").split("-")[0].strip()
            if pos_primary not in POSITIONS:
                pos_primary = "SF"
            for pos in POSITIONS:
                feat_vals[f"Pos_{pos}"] = 1.0 if pos_primary == pos else 0.0

            X = np.array([[feat_vals.get(f, 0.0) for f in features]])

            preds = {}
            for target, model in models.items():
                val = float(model.predict(X)[0])
                preds[target] = round(max(0, val), 2)

            # Cap sensible ranges
            preds["fg_percent"]  = round(min(preds["fg_percent"],  0.75), 3)
            preds["ts_percent"]  = round(min(preds["ts_percent"],  0.85), 3)
            preds["x3p_per_game"] = round(min(preds["x3p_per_game"], 15), 2)

            current_season = int(s0.get("season") or 2026)
            return self.send_json({
                "player_id": player_id,
                "current_season": current_season,
                "next_season": current_season + 1,
                "predictions": preds,
            })

        # Photo proxy
        m = re.match(r"^/api/player-photo/([a-z0-9]+)$", parsed.path)
        if m:
            player_id = m.group(1)
            urls = [
                f"https://www.basketball-reference.com/req/202106291/images/players/{player_id}.jpg",
                f"https://www.basketball-reference.com/req/202106291/images/players/{player_id}_200x200.jpg",
                f"https://cdn.nba.com/headshots/nba/latest/1040x760/{player_id}.png",
            ]
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Referer": "https://www.basketball-reference.com/",
                "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
            }
            data = None
            content_type = "image/jpeg"
            for url in urls:
                try:
                    req = urllib.request.Request(url, headers=headers)
                    with urllib.request.urlopen(req, timeout=6) as resp:
                        if resp.status == 200:
                            data = resp.read()
                            if url.endswith(".png"):
                                content_type = "image/png"
                            break
                except Exception:
                    continue
            if data:
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers()
                self.wfile.write(data)
            else:
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
