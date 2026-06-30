#!/usr/bin/env python3
import datetime
import decimal
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
import traceback
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import contextlib

import psycopg2
import psycopg2.extras
import psycopg2.pool

import archetype_engine

ROOT = Path(__file__).resolve().parent

PBKDF2_ITERATIONS = 200_000


def hash_password(password: str, salt: bytes = None) -> tuple[str, str]:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return digest.hex(), salt.hex()


def verify_password(password: str, salt_hex: str, hash_hex: str) -> bool:
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), PBKDF2_ITERATIONS)
    return hmac.compare_digest(digest.hex(), hash_hex)

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

# ── Draft career projection historical pool (built once, kept for process
# lifetime -- rebuilding takes real time since it scans every drafted
# player; only changes when build_career_labels.py / load_ncaa_stats.py are
# re-run, which means restarting the server, matching the model-loading
# pattern above rather than the time-based _SEASONS_CACHE below) ───────────
#
# Lock is load-bearing, not defensive boilerplate: ThreadingHTTPServer spawns
# a thread per request, and this build now takes 45+ seconds against the
# full 1950-2026 dataset (was much faster against the smaller original
# range). Without the lock, every concurrent request that arrives before the
# first build finishes sees _draft_projection_pool as None and starts its
# own redundant 45-second build, each holding its own DB connection open the
# whole time -- confirmed live (2026-06-25) as the actual cause of
# production connection-pool exhaustion (Supabase's pool here is only 15
# connections), not a crash in any specific route.
_draft_projection_pool = None
_draft_projection_pool_lock = threading.Lock()
_draft_projection_cache = {}   # name -> (result_dict, timestamp)
_DRAFT_PROJECTION_CACHE_TTL = 600  # 10 minutes
def _get_draft_projection_pool():
    global _draft_projection_pool
    if _draft_projection_pool is not None:
        return _draft_projection_pool
    with _draft_projection_pool_lock:
        if _draft_projection_pool is None:  # re-check: another thread may have finished while we waited for the lock
            import draft_projection.comp_engine as comp_engine
            with get_conn() as conn:
                _draft_projection_pool = comp_engine.build_historical_pool(conn, q, current_season=2026)
            print(f"Draft projection historical pool built: {len(_draft_projection_pool)} players.")
    return _draft_projection_pool

SALARY_CAPS_M = {
    2015: 70.00, 2016: 94.143, 2017: 99.093, 2018: 101.869, 2019: 109.14,
    2020: 109.14, 2021: 112.414, 2022: 123.655, 2023: 136.021, 2024: 140.588,
    2025: 155.00, 2026: 170.00,
}
DATABASE_URL = os.environ["DATABASE_URL"]

# Connection pool: reuses TCP connections and hard-caps concurrent DB usage at
# maxconn=5, well under Supabase free tier's 15-connection limit. Previously
# every request opened a fresh psycopg2.connect(), so N concurrent requests =
# N simultaneous connections; 16+ requests exhausted the pool with cryptic SSL
# errors. PoolError (pool full) is caught at the do_GET/do_POST level → 503.
_db_pool = None
_db_pool_lock = threading.Lock()


def _init_pool():
    global _db_pool
    if _db_pool is not None:
        return _db_pool
    with _db_pool_lock:
        if _db_pool is None:
            _db_pool = psycopg2.pool.ThreadedConnectionPool(
                minconn=1, maxconn=5,
                dsn=DATABASE_URL,
                connect_timeout=10,
            )
    return _db_pool


@contextlib.contextmanager
def get_conn():
    pool = _init_pool()
    conn = pool.getconn()
    discard = False
    try:
        yield conn
    except (psycopg2.OperationalError, psycopg2.InterfaceError):
        # Connection-level failure — discard instead of returning to pool.
        discard = True
        raise
    finally:
        if not discard:
            try:
                conn.rollback()  # reset any open transaction before reuse
            except Exception:
                discard = True
        try:
            pool.putconn(conn, close=discard)
        except Exception:
            pass


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


def normalize_name_for_match(name) -> str:
    """'Doe, John' -> 'john doe'; 'John Doe' -> 'john doe'. Must stay in sync
    with the identically-named function in load_ncaa_stats.py, which builds
    the name_key column this is matched against."""
    name = str(name or "").strip()
    if "," in name:
        last, first = name.split(",", 1)
        name = f"{first.strip()} {last.strip()}"
    return re.sub(r"[^a-z ]", "", name.lower()).strip()


def safe_int_py(val):
    try:
        return int(str(val).strip())
    except (TypeError, ValueError):
        return None


def safe_float_py(val):
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


_SEASONS_CACHE = {"data": None, "ts": 0}
_SEASONS_CACHE_TTL = 600  # seconds


def _seasons_cache_get():
    if _SEASONS_CACHE["data"] is not None and (time.time() - _SEASONS_CACHE["ts"]) < _SEASONS_CACHE_TTL:
        return _SEASONS_CACHE["data"]
    return None


def _seasons_cache_set(rows):
    _SEASONS_CACHE["data"] = rows
    _SEASONS_CACHE["ts"] = time.time()


_DASHBOARD_CACHE = {}  # season_int -> (data_dict, timestamp)
_DASHBOARD_CACHE_TTL = 600


def _dashboard_cache_get(season):
    entry = _DASHBOARD_CACHE.get(season)
    if entry and (time.time() - entry[1]) < _DASHBOARD_CACHE_TTL:
        return entry[0]
    return None


def _dashboard_cache_set(season, data):
    _DASHBOARD_CACHE[season] = (data, time.time())


# ── Heartbeat buffer: accumulate in memory, flush to DB every 5 min ─────────
# Each heartbeat request previously opened a new psycopg2 connection, which
# exhausted Supabase's 15-connection pool under any real user load.
_heartbeat_buffer = {}  # email -> {"last_seen": datetime, "delta_seconds": int}
_heartbeat_lock = threading.Lock()
_HEARTBEAT_FLUSH_INTERVAL = 300  # seconds


def _flush_heartbeats():
    while True:
        time.sleep(_HEARTBEAT_FLUSH_INTERVAL)
        with _heartbeat_lock:
            if not _heartbeat_buffer:
                continue
            snapshot = dict(_heartbeat_buffer)
            _heartbeat_buffer.clear()
        try:
            with get_conn() as conn:
                cur = conn.cursor()
                for email, data in snapshot.items():
                    cur.execute(
                        "UPDATE archive_users SET last_seen_at = %s, total_active_seconds = total_active_seconds + %s WHERE email = %s",
                        (data["last_seen"], data["delta_seconds"], email),
                    )
                conn.commit()
        except Exception as exc:
            print(f"Heartbeat flush failed (non-fatal): {exc}")


def _cast_season_row(row):
    fg = safe_float_py(row.get("fg"))
    usg = safe_float_py(row.get("usg_pct"))
    return {
        "player_name": row.get("player_name"),
        "player_id": row.get("player_id"),
        "team_abbreviation": row.get("team_abbreviation"),
        "pos": row.get("pos"),
        "age": safe_float_py(row.get("age")),
        "gp": safe_int_py(row.get("gp")),
        "min": safe_float_py(row.get("min")),
        "pts": safe_float_py(row.get("pts")),
        "reb": safe_float_py(row.get("reb")),
        "ast": safe_float_py(row.get("ast")),
        "three": safe_float_py(row.get("three")),
        "stl": safe_float_py(row.get("stl")),
        "blk": safe_float_py(row.get("blk")),
        "tov": safe_float_py(row.get("tov")),
        "fg": fg * 100 if fg is not None else None,
        "three_pct": (lambda v: v * 100 if v is not None else None)(safe_float_py(row.get("three_pct"))),
        "ft_pct": (lambda v: v * 100 if v is not None else None)(safe_float_py(row.get("ft_pct"))),
        "net_rating": safe_float_py(row.get("net_rating")),
        "usg_pct": usg / 100 if usg is not None else None,
        "ts_pct": safe_float_py(row.get("ts_pct")),
        "per": safe_float_py(row.get("per")),
        "vorp": safe_float_py(row.get("vorp")),
        "ws": safe_float_py(row.get("ws")),
        "ows": safe_float_py(row.get("ows")),
        "dws": safe_float_py(row.get("dws")),
        "season": row.get("season"),
        "season_start": safe_int_py(row.get("season")),
    }


def to_json(obj):
    """JSON serializer that handles Decimal and other PostgreSQL types."""
    if isinstance(obj, decimal.Decimal):
        return float(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


STATIC_DIR = ROOT / "static"


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        # Only the static/ subdirectory is served over HTTP -- previously this
        # was ROOT, which meant server.py, nba.db, the trained .pkl models,
        # and every training/scraper script were directly downloadable from
        # production (confirmed live: curl .../server.py returned 200).
        super().__init__(*args, directory=STATIC_DIR, **kwargs)

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

    def do_POST(self):
        try:
            self._handle_post()
        except psycopg2.pool.PoolError:
            try:
                self.send_json({"error": "Server busy, please retry"}, status=503)
            except Exception:
                pass
        except Exception as e:
            traceback.print_exc()
            try:
                self.send_json({"error": str(e)}, status=500)
            except Exception:
                pass

    def _handle_post(self):
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        if self.headers.get("Content-Encoding") == "gzip":
            import gzip as _gzip
            raw = _gzip.decompress(raw)
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return self.send_json({"error": "Invalid JSON body"}, status=400)

        if parsed.path == "/api/signup":
            email = (body.get("email") or "").strip().lower()
            password = body.get("password") or ""
            if not email or "@" not in email:
                return self.send_json({"error": "Valid email required"}, status=400)
            if len(password) < 6:
                return self.send_json({"error": "Password must be at least 6 characters"}, status=400)
            with get_conn() as conn:
                if q1(conn, "SELECT 1 FROM archive_users WHERE email = ?", (email,)):
                    return self.send_json({"error": "An account with this email already exists"}, status=409)
                pw_hash, salt = hash_password(password)
                cur = conn.cursor()
                cur.execute(
                    "INSERT INTO archive_users (email, password_hash, password_salt) VALUES (%s, %s, %s)",
                    (email, pw_hash, salt),
                )
                conn.commit()
            return self.send_json({"ok": True, "email": email})

        if parsed.path == "/api/login":
            email = (body.get("email") or "").strip().lower()
            password = body.get("password") or ""
            with get_conn() as conn:
                row = q1(conn, "SELECT password_hash, password_salt FROM archive_users WHERE email = ?", (email,))
            if not row or not verify_password(password, row[1], row[0]):
                return self.send_json({"error": "Incorrect email or password"}, status=401)
            return self.send_json({"ok": True, "email": email})

        if parsed.path == "/api/heartbeat":
            email = (body.get("email") or "").strip().lower()
            if not email or "@" not in email:
                return self.send_json({"ok": False}, status=400)
            now = datetime.datetime.now(datetime.timezone.utc)
            with _heartbeat_lock:
                prev = _heartbeat_buffer.get(email)
                if prev:
                    gap = (now - prev["last_seen"]).total_seconds()
                    delta = int(gap) if gap <= 90 else 0
                    _heartbeat_buffer[email] = {
                        "last_seen": now,
                        "delta_seconds": prev["delta_seconds"] + delta,
                    }
                else:
                    _heartbeat_buffer[email] = {"last_seen": now, "delta_seconds": 0}
            return self.send_json({"ok": True})

        if parsed.path == "/api/admin/delete-users":
            admin_key = os.environ.get("ADMIN_KEY")
            if not admin_key or not hmac.compare_digest(body.get("key", ""), admin_key):
                return self.send_json({"error": "Not found"}, status=404)
            emails = [e.strip().lower() for e in body.get("emails", []) if e.strip()]
            if not emails:
                return self.send_json({"error": "emails list required"}, status=400)
            with get_conn() as conn:
                cur = conn.cursor()
                cur.execute("DELETE FROM archive_users WHERE email = ANY(%s)", (emails,))
                deleted = cur.rowcount
                conn.commit()
            return self.send_json({"ok": True, "deleted": deleted})

        return self.send_json({"error": "Not found"}, status=404)

    def do_GET(self):
        try:
            self._handle()
        except psycopg2.pool.PoolError:
            try:
                self.send_json({"error": "Server busy, please retry"}, status=503)
            except Exception:
                pass
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

        if parsed.path == "/api/admin/user-count":
            admin_key = os.environ.get("ADMIN_KEY")
            if not admin_key or not hmac.compare_digest(params.get("key", [""])[0], admin_key):
                return self.send_json({"error": "Not found"}, status=404)
            with get_conn() as conn:
                row = q1(conn, "SELECT COUNT(*) FROM archive_users")
            return self.send_json({"user_count": row[0]})

        if parsed.path == "/api/admin/user-emails":
            admin_key = os.environ.get("ADMIN_KEY")
            if not admin_key or not hmac.compare_digest(params.get("key", [""])[0], admin_key):
                return self.send_json({"error": "Not found"}, status=404)
            with get_conn() as conn:
                rows = q(conn, "SELECT email, created_at, total_active_seconds FROM archive_users ORDER BY created_at")
            now = datetime.datetime.now(datetime.timezone.utc)
            def member_for(ts):
                if ts is None:
                    return "unknown"
                delta = now - ts.replace(tzinfo=datetime.timezone.utc) if ts.tzinfo is None else now - ts
                days = delta.days
                if days == 0:
                    return "today"
                if days == 1:
                    return "1 day"
                if days < 7:
                    return f"{days} days"
                if days < 14:
                    return "1 week"
                if days < 30:
                    return f"{days // 7} weeks"
                if days < 60:
                    return "1 month"
                if days < 365:
                    return f"{days // 30} months"
                return f"{days // 365}y {(days % 365) // 30}m"
            def fmt_time(secs):
                if not secs:
                    return "0 min"
                secs = int(secs)
                h, m = divmod(secs // 60, 60)
                if h >= 1:
                    return f"{h}h {m}m" if m else f"{h}h"
                return f"{secs // 60}m" if secs >= 60 else f"{secs}s"
            return self.send_json([{
                "email": r["email"],
                "created_at": r["created_at"].isoformat(),
                "member_for": member_for(r["created_at"]),
                "time_on_site": fmt_time(r["total_active_seconds"]),
            } for r in rows])

        if parsed.path == "/api/seasons":
            cached = _seasons_cache_get()
            if cached is not None:
                return self.send_json_rows(cached)
            with get_conn() as conn:
                # Cast in Python rather than via the SQL safe_float()/safe_int()
                # helpers -- those are PL/pgSQL functions with an EXCEPTION
                # handler, and Postgres opens a subtransaction per call, which
                # made this ~530k-call query take 9+ seconds. Plain text
                # columns + Python casting is functionally identical and
                # orders of magnitude faster.
                rows = q(conn, """
                    SELECT
                      player AS player_name,
                      player_id,
                      team AS team_abbreviation,
                      pos,
                      age,
                      g AS gp,
                      mp_per_game AS min,
                      pts_per_game AS pts,
                      trb_per_game AS reb,
                      ast_per_game AS ast,
                      x3p_per_game AS three,
                      stl_per_game AS stl,
                      blk_per_game AS blk,
                      tov_per_game AS tov,
                      fg_percent AS fg,
                      x3p_percent AS three_pct,
                      ft_percent AS ft_pct,
                      bpm AS net_rating,
                      usg_percent AS usg_pct,
                      ts_percent AS ts_pct,
                      per,
                      vorp,
                      ws,
                      ows,
                      dws,
                      season
                    FROM archive_player_dashboard
                    ORDER BY player, season
                """, ())
            result = [_cast_season_row(dict(r)) for r in rows]
            _seasons_cache_set(result)
            return self.send_json_rows(result)

        if parsed.path == "/api/players":
            search = (params.get("search", [""])[0] or "").strip()
            # Use NULLIF(col,'')::type instead of safe_float()/safe_int() to avoid
            # PL/pgSQL subtransaction overhead on every row.
            sql = """
                SELECT
                  player AS player_name,
                  player_id,
                  COUNT(*) AS seasons,
                  MIN(NULLIF(NULLIF(season, ''), 'NA')::int) AS first_season_start,
                  MAX(NULLIF(NULLIF(season, ''), 'NA')::int) AS latest_season_start,
                  ROUND(AVG(NULLIF(NULLIF(pts_per_game, ''), 'NA')::float)::numeric, 1) AS career_pts,
                  ROUND(AVG(NULLIF(NULLIF(trb_per_game, ''), 'NA')::float)::numeric, 1) AS career_reb,
                  ROUND(AVG(NULLIF(NULLIF(ast_per_game, ''), 'NA')::float)::numeric, 1) AS career_ast,
                  ROUND((AVG(NULLIF(NULLIF(ts_percent, ''), 'NA')::float) * 100)::numeric, 1) AS career_ts_pct
                FROM archive_player_dashboard
            """
            args = []
            if search:
                sql += " WHERE player ILIKE ?"
                args.append(f"%{search}%")
            sql += " GROUP BY player_id, player ORDER BY seasons DESC, career_pts DESC, player LIMIT 200"
            with get_conn() as conn:
                rows = q(conn, sql, args)
            return self.send_json_rows(rows)

        if parsed.path == "/api/dashboard":
            # Check cache before any DB work. Default to 2026 if no param;
            # the cached result includes the real seasons_available list.
            _season_param = params.get("season", [""])[0] or ""
            season = int(_season_param) if _season_param.isdigit() else 2026
            cached = _dashboard_cache_get(season)
            if cached is not None:
                return self.send_json(cached)

            with get_conn() as conn:
                # Cache miss: discover available seasons from archive_team_summaries
                # (small table, ~30 rows/season) and re-resolve the latest season.
                ts_season_rows = q(conn, "SELECT DISTINCT season FROM archive_team_summaries WHERE season != ''", ())
                all_seasons_sorted = sorted(
                    [r["season"] for r in ts_season_rows if str(r["season"]).isdigit()],
                    key=lambda s: int(s), reverse=True
                )
                if not _season_param.isdigit():
                    latest_season = int(all_seasons_sorted[0]) if all_seasons_sorted else 2026
                    season = latest_season
                    # Check cache again now that we know the real latest season
                    cached = _dashboard_cache_get(season)
                    if cached is not None:
                        return self.send_json(cached)

                seasons_available = all_seasons_sorted

                # Reuse the seasons cache (populated by /api/seasons) if warm;
                # avoids a slow full-table scan of archive_player_per_game.
                seasons_cache = _seasons_cache_get()
                if seasons_cache is not None:
                    per_game_rows = [
                        {
                            "player": r["player_name"],
                            "player_id": r["player_id"],
                            "team": r["team_abbreviation"],
                            "pts_per_game": r.get("pts"),
                            "trb_per_game": r.get("reb"),
                            "ast_per_game": r.get("ast"),
                            "fg_percent": (r["fg"] / 100) if r.get("fg") is not None else None,
                            "g": r.get("gp"),
                        }
                        for r in seasons_cache if str(r.get("season_start")) == str(season)
                    ]
                else:
                    # Cold start: fetch directly. archive_player_per_game can be
                    # slow without an index on season; results are cached so only
                    # the first cold request pays this cost.
                    raw_pg = q(conn, """
                        SELECT player, player_id, team,
                               pts_per_game, trb_per_game, ast_per_game, fg_percent, g
                        FROM archive_player_per_game
                        WHERE season = ?
                    """, (str(season),))
                    per_game_rows = [dict(r) for r in raw_pg]

                # Dedup: prefer TOT/2TM/3TM rows for traded players
                traded = set()
                for r in per_game_rows:
                    if r["team"] in ("TOT", "2TM", "3TM"):
                        traded.add(r["player"])

                def _keep(r):
                    if r["player"] in traded and r["team"] not in ("TOT", "2TM", "3TM"):
                        return False
                    g = safe_float_py(r["g"])
                    return g is not None and g >= 20

                eligible = [r for r in per_game_rows if _keep(r)]

                def _cast_pg(r):
                    return {
                        "player": r["player"],
                        "player_id": r["player_id"],
                        "team": r["team"],
                        "pts": safe_float_py(r["pts_per_game"]),
                        "reb": safe_float_py(r["trb_per_game"]),
                        "ast": safe_float_py(r["ast_per_game"]),
                        "fg_pct": (lambda v: round(v * 100, 1) if v is not None else None)(safe_float_py(r["fg_percent"])),
                    }

                cast_eligible = [_cast_pg(r) for r in eligible]

                top_scorers = sorted(
                    [r for r in cast_eligible if r["pts"] is not None],
                    key=lambda r: r["pts"], reverse=True
                )[:10]

                top_assisters = sorted(
                    [r for r in cast_eligible if r["ast"] is not None],
                    key=lambda r: r["ast"], reverse=True
                )[:5]
                top_assisters = [{"player": r["player"], "player_id": r["player_id"], "team": r["team"],
                                   "ast": r["ast"], "pts": r["pts"]} for r in top_assisters]

                top_rebounders = sorted(
                    [r for r in cast_eligible if r["reb"] is not None],
                    key=lambda r: r["reb"], reverse=True
                )[:5]
                top_rebounders = [{"player": r["player"], "player_id": r["player_id"], "team": r["team"],
                                    "reb": r["reb"], "pts": r["pts"]} for r in top_rebounders]

                awards = q(conn, """
                    SELECT a.award, a.player, a.player_id, a.winner, a.share
                    FROM archive_player_award_shares a
                    WHERE a.season = ? AND UPPER(a.winner) = 'TRUE'
                    ORDER BY a.award
                """, (str(season),))

                raw_standings = q(conn, """
                    SELECT team, abbreviation, w, l, n_rtg, playoffs
                    FROM archive_team_summaries
                    WHERE season = ? AND abbreviation != 'NA'
                """, (str(season),))

                def _cast_standing(r):
                    w = safe_float_py(r["w"])
                    l = safe_float_py(r["l"])
                    total = (w or 0) + (l or 0)
                    return {
                        "team": r["team"],
                        "abbreviation": r["abbreviation"],
                        "w": w,
                        "l": l,
                        "win_pct": round(w / total, 3) if w is not None and total > 0 else None,
                        "net_rtg": safe_float_py(r["n_rtg"]),
                        "playoffs": r["playoffs"],
                    }

                team_standings = sorted(
                    [_cast_standing(r) for r in raw_standings],
                    key=lambda r: r["w"] or 0, reverse=True
                )

            result = {
                "season": season,
                "seasons_available": seasons_available,
                "top_scorers": top_scorers,
                "top_assisters": top_assisters,
                "top_rebounders": top_rebounders,
                "awards": [dict(r) for r in awards],
                "team_standings": team_standings,
            }
            _dashboard_cache_set(season, result)
            return self.send_json(result)

        if parsed.path == "/api/playoffs":
            season = (params.get("season", ["2026"])[0] or "2026").strip()
            with get_conn() as conn:
                rows = q(conn, """
                    SELECT season, conference, round,
                           team1, team1_abbrev, team1_seed, team1_wins,
                           team2, team2_abbrev, team2_seed, team2_wins,
                           winner_abbrev
                    FROM archive_playoff_series
                    WHERE season = ?
                    ORDER BY conference, round, team1_seed
                """, (season,))
            return self.send_json_rows(rows)

        if parsed.path == "/api/draft":
            season = (params.get("season", ["2025"])[0] or "2025").strip()
            with get_conn() as conn:
                rows = q(conn, """
                    SELECT d.season, d.overall_pick, d.round, d.tm AS team,
                           d.player, d.player_id, d.college,
                           ROUND(AVG(NULLIF(NULLIF(p.pts_per_game, ''), 'NA')::float)::numeric, 1) AS career_pts,
                           ROUND(AVG(NULLIF(NULLIF(p.trb_per_game, ''), 'NA')::float)::numeric, 1) AS career_reb,
                           ROUND(AVG(NULLIF(NULLIF(p.ast_per_game, ''), 'NA')::float)::numeric, 1) AS career_ast,
                           COUNT(p.season) AS seasons_played
                    FROM archive_draft_pick_history d
                    LEFT JOIN archive_player_per_game p ON p.player_id = d.player_id
                    WHERE d.season = ?
                    GROUP BY d.player_id, d.overall_pick, d.season, d.round, d.tm, d.player, d.college
                    ORDER BY NULLIF(NULLIF(d.overall_pick, ''), 'NA')::int NULLS LAST
                """, (season,))
                seasons_available = q(conn, """
                    SELECT season FROM (SELECT DISTINCT season FROM archive_draft_pick_history WHERE season != '') t
                    ORDER BY season::int DESC
                """)
            return self.send_json({
                "season": season,
                "picks": [dict(r) for r in rows],
                "seasons": [r["season"] for r in seasons_available],
            })

        if parsed.path == "/api/prospects":
            with get_conn() as conn:
                rows = q(conn, """
                    SELECT rank, name, pos, age, school, height, weight, status, country
                    FROM archive_draft_prospects_2026
                    ORDER BY NULLIF(NULLIF(rank, ''), 'NA')::int NULLS LAST
                """)
            return self.send_json_rows(rows)

        if parsed.path == "/api/prospect-outcome":
            name = (params.get("name", [""])[0] or "").strip()
            if not name:
                return self.send_json({"error": "name required"}, status=400)
            with get_conn() as conn:
                prospect_rows = q(conn, """
                    SELECT rank, name, pos, age, school, height, weight, status, country
                    FROM archive_draft_prospects_2026 WHERE name = ?
                """, (name,))
                if not prospect_rows:
                    return self.send_json({"error": "Prospect not found"}, status=404)
                prospect = dict(prospect_rows[0])
                rank = safe_int_py(prospect.get("rank")) or 30

                picks = q(conn, """
                    WITH season_rows AS (
                        SELECT DISTINCT ON (player_id, season)
                               player_id, season, pos, pts_per_game, trb_per_game, ast_per_game
                        FROM archive_player_per_game
                        WHERE season != ''
                        ORDER BY player_id, season,
                                 CASE WHEN team ~ 'TM$' THEN 0 ELSE 1 END
                    ),
                    rookie_pos AS (
                        SELECT DISTINCT ON (player_id) player_id, pos
                        FROM season_rows
                        ORDER BY player_id, season::int ASC
                    ),
                    career AS (
                        SELECT player_id,
                               ROUND(AVG(NULLIF(NULLIF(pts_per_game, ''), 'NA')::float)::numeric, 1) AS career_pts,
                               ROUND(AVG(NULLIF(NULLIF(trb_per_game, ''), 'NA')::float)::numeric, 1) AS career_reb,
                               ROUND(AVG(NULLIF(NULLIF(ast_per_game, ''), 'NA')::float)::numeric, 1) AS career_ast,
                               COUNT(season) AS seasons_played,
                               ROUND(MAX(NULLIF(NULLIF(pts_per_game, ''), 'NA')::float)::numeric, 1) AS peak_pts
                        FROM season_rows
                        GROUP BY player_id
                    )
                    SELECT d.player, d.player_id, d.season AS draft_season, d.overall_pick, d.college,
                           rp.pos AS rookie_pos,
                           c.career_pts, c.career_reb, c.career_ast, c.seasons_played, c.peak_pts
                    FROM archive_draft_pick_history d
                    JOIN career c ON c.player_id = d.player_id
                    LEFT JOIN rookie_pos rp ON rp.player_id = d.player_id
                    WHERE d.overall_pick != ''
                """)

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

        if parsed.path == "/api/draft-projection":
            import time as _time
            name = (params.get("name", [""])[0] or "").strip()
            if not name:
                return self.send_json({"error": "name required"}, status=400)
            college = (params.get("college", [""])[0] or "").strip() or None
            age_param = (params.get("age_at_draft", [""])[0] or "").strip()
            pick_param = (params.get("overall_pick", [""])[0] or "").strip()
            age_at_draft = safe_float_py(age_param) if age_param else None
            overall_pick = safe_float_py(pick_param) if pick_param else None

            cache_key = f"{name}|{college}|{age_at_draft}|{overall_pick}"
            cached = _draft_projection_cache.get(cache_key)
            if cached and (_time.time() - cached[1]) < _DRAFT_PROJECTION_CACHE_TTL:
                return self.send_json(cached[0])

            with get_conn() as conn:
                if age_at_draft is None or overall_pick is None or college is None:
                    prospect_rows = q(conn, """
                        SELECT rank, age, school FROM archive_draft_prospects_2026 WHERE name = ?
                    """, (name,))
                    if prospect_rows:
                        p = dict(prospect_rows[0])
                        if college is None:
                            college = p.get("school")
                        if age_at_draft is None:
                            age_at_draft = safe_float_py(p.get("age"))
                        if overall_pick is None:
                            # Pre-draft prospects have no real pick yet -- their
                            # consensus mock rank is the best available proxy,
                            # and draft_slot_tier is coarse enough (top-5 /
                            # lottery / first-round / second-round-or-UDFA) that
                            # a mock-rank approximation doesn't overstate
                            # precision.
                            overall_pick = safe_float_py(p.get("rank"))
                    cache_key = f"{name}|{college}|{age_at_draft}|{overall_pick}"
                    cached = _draft_projection_cache.get(cache_key)
                    if cached and (_time.time() - cached[1]) < _DRAFT_PROJECTION_CACHE_TTL:
                        return self.send_json(cached[0])

                import draft_projection.service as draft_service
                pool = _get_draft_projection_pool()
                result = draft_service.build_draft_projection(
                    conn, q, pool, player_name=name, college=college,
                    age_at_draft=age_at_draft, overall_pick=overall_pick,
                )
            _draft_projection_cache[cache_key] = (result, _time.time())
            return self.send_json(result)

        if parsed.path == "/api/ncaa-stats":
            name = (params.get("name", [""])[0] or "").strip()
            if not name:
                return self.send_json({"error": "name required"}, status=400)
            with get_conn() as conn:
                key = normalize_name_for_match(name)
                try:
                    rows = q(conn, """
                        SELECT player_name, team, conference, division, position, class_year,
                               height_in, weight_lb, season, academic_year, gp, gs, min,
                               pts_per_game, reb_per_game, ast_per_game, stl_per_game, blk_per_game,
                               tov_per_game, fg_pct, fg3_pct, ft_pct, ts_pct, efg_pct,
                               ast_pct, oreb_pct, dreb_pct, usg_pct, data_quality_flag
                        FROM archive_ncaa_player_stats
                        WHERE name_key = ?
                        ORDER BY academic_year
                    """, (key,))
                except psycopg2.Error:
                    # Table may not exist yet if load_ncaa_stats.py hasn't been
                    # run -- this is an optional, best-effort enrichment, not a
                    # hard dependency, so degrade to an empty result rather
                    # than a 500.
                    conn.rollback()
                    rows = []
            return self.send_json_rows(rows)

        if parsed.path == "/api/allstars":
            with get_conn() as conn:
                rows = q(conn, """
                    SELECT player, player_id, team, season, lg, replaced
                    FROM archive_all_star_selections
                    WHERE season != ''
                    ORDER BY season::int DESC, player
                    LIMIT 100
                """)
            return self.send_json_rows(rows)

        if parsed.path == "/api/archetype":
            player_id = (params.get("player_id", [""])[0] or "").strip()
            season = (params.get("season", [""])[0] or "").strip()
            if not player_id or not season:
                return self.send_json({"error": "player_id and season required"}, status=400)
            with get_conn() as conn:
                report = archetype_engine.build_player_report(conn, q, player_id, season)
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
            with get_conn() as conn:
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

            with get_conn() as conn:
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


def ensure_users_table(max_attempts=5, retry_delay_seconds=2):
    """Idempotent (CREATE TABLE IF NOT EXISTS) and only needs to succeed once
    ever -- the table persists across restarts. Retries with a short backoff
    to ride out transient connection hiccups at cold boot (seen in
    production: psycopg2.OperationalError "server didn't return client
    encoding" against the Supabase pooler), and does NOT crash the whole
    server on final failure -- a brand-new deploy where this table somehow
    still doesn't exist would just mean signups fail until the next
    successful run, rather than the entire site going down for every visitor
    over a one-time startup step unrelated to most requests."""
    for attempt in range(1, max_attempts + 1):
        try:
            with get_conn() as conn:
                cur = conn.cursor()
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS archive_users (
                        email TEXT PRIMARY KEY,
                        password_hash TEXT NOT NULL,
                        password_salt TEXT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                """)
                cur.execute("""
                    ALTER TABLE archive_users
                        ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ,
                        ADD COLUMN IF NOT EXISTS total_active_seconds INTEGER NOT NULL DEFAULT 0
                """)
                conn.commit()
            return
        except Exception as exc:
            print(f"ensure_users_table attempt {attempt}/{max_attempts} failed: {exc}")
            if attempt == max_attempts:
                print("ensure_users_table giving up after max attempts -- continuing startup anyway "
                      "(the table almost certainly already exists from a prior successful run).")
                return
            time.sleep(retry_delay_seconds)


def _prewarm_pool():
    try:
        _get_draft_projection_pool()
    except Exception as exc:
        print(f"Pre-warm failed (non-fatal): {exc}")


def main():
    ensure_users_table()
    threading.Thread(target=_prewarm_pool, daemon=True).start()
    threading.Thread(target=_flush_heartbeats, daemon=True).start()
    port = int(os.environ.get("PORT", 8000))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"Serving NBA predictor at http://0.0.0.0:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
