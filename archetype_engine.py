"""
Archetype + comp-engine + trajectory module for statfuel.online.

Ports the design built and validated in /Downloads/archive/nba_model/ onto
this site's existing data: the archive_advanced table already has
usg_percent, ast_percent, blk_percent, drb_percent, stl_percent, x3p_ar,
f_tr, dbpm and real age for every season 1947-2026 (including Wemby/Chet/
Luka), so unlike the prototype this needs NO era-blending or derived-stat
estimation -- it queries one already-correct table.

Public entry point: build_player_report(conn, player_id, season) -> dict,
used by the /api/archetype endpoint in server.py.

Everything here is read-only against the existing `q()` connection pattern
used elsewhere in server.py -- no new tables, no writes.
"""
from __future__ import annotations

import math
from collections import defaultdict

MIN_GAMES_SEASON = 20  # garbage-time/injury-shortened-season filter (per_game table has no MP total column, g is the available volume signal)

A_KEYS = ["heliocentric_engine", "secondary_playmaker", "off_ball_scorer", "non_creator_finisher"]
B_KEYS = ["rim_protector", "versatile_defender"]
C_KEYS = ["three_pt_pressure", "interior_pressure"]

WEIGHT_A, WEIGHT_DE, WEIGHT_B, WEIGHT_C, WEIGHT_AGE = 0.45, 0.25, 0.15, 0.10, 0.05
USAGE_ORDER = {"low": 0, "medium": 1, "high": 2, "extreme": 3}
PLAYMAKING_ORDER = {"low": 0, "medium": 1, "high": 2}


# ── data loading ─────────────────────────────────────────────────────────

def _to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load_pool(conn, q):
    """One row per (player_id, season): merges archive_advanced with games
    played from archive_player_per_game (multi-team trade seasons collapse
    to the row with the most games played, a reasonable single-season
    representative when an aggregate "TOT"-style row isn't guaranteed to
    exist in this dataset)."""
    adv_rows = q(conn, """
        SELECT player, player_id, season, age, usg_percent, ast_percent,
               blk_percent, drb_percent, stl_percent, x3p_ar, f_tr, dbpm
        FROM archive_advanced
    """)
    games_rows = q(conn, "SELECT player_id, season, g FROM archive_player_per_game")

    games_by_key = {}
    for r in games_rows:
        key = (r["player_id"], r["season"])
        g = _to_float(r["g"]) or 0
        if key not in games_by_key or g > games_by_key[key]:
            games_by_key[key] = g

    pool = []
    for r in adv_rows:
        key = (r["player_id"], r["season"])
        games = games_by_key.get(key)
        usg, ast, blk, drb, stl = (
            _to_float(r["usg_percent"]), _to_float(r["ast_percent"]), _to_float(r["blk_percent"]),
            _to_float(r["drb_percent"]), _to_float(r["stl_percent"]),
        )
        if None in (usg, ast, blk, drb, stl) or games is None or games < MIN_GAMES_SEASON:
            continue
        pool.append({
            "player": r["player"], "player_id": r["player_id"],
            "season": int(r["season"]), "age": _to_float(r["age"]), "games": games,
            "usg_pct": usg, "ast_pct": ast, "blk_pct": blk, "drb_pct": drb, "stl_pct": stl,
            "fg3a_rate": _to_float(r["x3p_ar"]) or 0.0, "ft_rate": _to_float(r["f_tr"]) or 0.0,
            "dbpm": _to_float(r["dbpm"]),
        })

    pool.sort(key=lambda p: (p["player_id"], p["season"]))
    exp_counter = defaultdict(int)
    for p in pool:
        p["experience"] = exp_counter[p["player_id"]]
        exp_counter[p["player_id"]] += 1

    return pool


# ── percentile ranks (computed per season across the qualified pool) ──────

def add_percentiles(pool):
    by_season = defaultdict(list)
    for p in pool:
        by_season[p["season"]].append(p)

    for season_rows in by_season.values():
        n = len(season_rows)
        for col in ["usg_pct", "ast_pct", "blk_pct", "drb_pct", "stl_pct", "fg3a_rate", "ft_rate"]:
            ordered = sorted(season_rows, key=lambda p: p[col])
            for i, p in enumerate(ordered):
                p[f"{col}_pr"] = (i + 1) / n
    return pool


# ── archetype dimensions (same math as the prototype) ─────────────────────

def _softmax(scores: dict) -> dict:
    exps = {k: math.exp(1.5 * v) for k, v in scores.items()}
    total = sum(exps.values()) or 1.0
    return {k: round(100 * v / total, 1) for k, v in exps.items()}


def creation_burden(p):
    usg, ast = p["usg_pct_pr"], p["ast_pct_pr"]
    return _softmax({
        "heliocentric_engine": min(usg, ast) if usg > 0.85 and ast > 0.75 else 0.3 * usg + 0.3 * ast,
        "secondary_playmaker": ast if 0.55 <= usg < 0.85 else 0.4 * ast,
        "off_ball_scorer": (1 - ast) * usg if usg >= 0.4 else 0.3,
        "non_creator_finisher": (1 - usg) * (1 - ast),
    })


def defensive_role(p):
    rim = 0.6 * p["blk_pct_pr"] + 0.4 * p["drb_pct_pr"]
    versatile = p["stl_pct_pr"] if p["dbpm"] is None else 0.5 * p["stl_pct_pr"] + 0.5 * max(p["dbpm"], 0) / 5
    return _softmax({"rim_protector": rim, "versatile_defender": versatile})


def scoring_profile(p):
    return _softmax({"three_pt_pressure": p["fg3a_rate_pr"], "interior_pressure": p["ft_rate_pr"]})


def usage_level(p):
    u = p["usg_pct_pr"]
    return "extreme" if u >= 0.90 else "high" if u >= 0.70 else "medium" if u >= 0.40 else "low"


def playmaking_level(p):
    a = p["ast_pct_pr"]
    return "high" if a >= 0.75 else "medium" if a >= 0.40 else "low"


def hybrid_offensive_big_score(p):
    return p["drb_pct_pr"] * (0.5 * p["usg_pct_pr"] + 0.5 * p["ast_pct_pr"])


def named_archetype_mix(p, creation, defense, scoring, usage):
    low_creation = creation["non_creator_finisher"] + creation["off_ball_scorer"]
    hybrid = hybrid_offensive_big_score(p)
    raw = {
        "Heliocentric Engine": creation["heliocentric_engine"],
        "Secondary Playmaker": creation["secondary_playmaker"],
        "Off-Ball Scorer": creation["off_ball_scorer"] * (scoring["three_pt_pressure"] / 100) * 2,
        "Scoring Big": creation["off_ball_scorer"] * (scoring["interior_pressure"] / 100) * 2
        + creation["non_creator_finisher"] * (scoring["interior_pressure"] / 100) * 1.5,
        "Playmaking Big": creation["secondary_playmaker"] * (defense["rim_protector"] / 100) * 1.5
        + creation["heliocentric_engine"] * (defense["rim_protector"] / 100) * 1.5,
        "Rim Protector": defense["rim_protector"] * (low_creation / 100) * 2,
        "3&D Wing": defense["versatile_defender"] * (scoring["three_pt_pressure"] / 100) * (low_creation / 100) * 3,
        "Defensive Wing": defense["versatile_defender"] * (low_creation / 100) * 2 * (1 if usage == "low" else 0.5),
        "Hybrid Offensive Big": hybrid * 4,
    }
    total = sum(raw.values()) or 1.0
    return {k: round(100 * v / total, 1) for k, v in raw.items()}


def development_stage(experience):
    if experience <= 0:
        return "rookie"
    if experience <= 2:
        return "early"
    if experience <= 5:
        return "prime_projection"
    return "established"


def annotate(pool):
    add_percentiles(pool)
    for p in pool:
        creation = creation_burden(p)
        defense = defensive_role(p)
        scoring = scoring_profile(p)
        usage = usage_level(p)
        p["A"] = creation
        p["B"] = defense
        p["C"] = scoring
        p["D_usage_level"] = usage
        p["E_playmaking_level"] = playmaking_level(p)
        p["named_mix"] = named_archetype_mix(p, creation, defense, scoring, usage)
        p["dominant_engine"] = max(creation, key=creation.get)
        p["dev_stage"] = development_stage(p["experience"])
    return pool


# ── comp engine: two separate, never-mixed layers ──────────────────────────

def _cosine(va, vb):
    dot = sum(a * b for a, b in zip(va, vb))
    na, nb = math.sqrt(sum(a * a for a in va)), math.sqrt(sum(b * b for b in vb))
    return dot / (na * nb) if na and nb else 0.0


def _ordinal_sim(a, b, order):
    span = max(order.values()) or 1
    return 1 - abs(order[a] - order[b]) / span


def _age_band_ok(a, b, band=2):
    if a["age"] is not None and b["age"] is not None:
        return abs(a["age"] - b["age"]) <= band
    return abs(a["experience"] - b["experience"]) <= band


def _same_stage_similarity(a, b):
    if not _age_band_ok(a, b):
        return None
    a_sim = _cosine([a["A"][k] for k in A_KEYS], [b["A"][k] for k in A_KEYS])
    de_sim = (
        _ordinal_sim(a["D_usage_level"], b["D_usage_level"], USAGE_ORDER)
        + _ordinal_sim(a["E_playmaking_level"], b["E_playmaking_level"], PLAYMAKING_ORDER)
    ) / 2
    b_sim = _cosine([a["B"][k] for k in B_KEYS], [b["B"][k] for k in B_KEYS])
    c_sim = _cosine([a["C"][k] for k in C_KEYS], [b["C"][k] for k in C_KEYS])
    if a["age"] is not None and b["age"] is not None:
        age_sim = max(0.0, 1 - abs(a["age"] - b["age"]) / 5)
    else:
        age_sim = max(0.0, 1 - abs(a["experience"] - b["experience"]) / 5)

    score = 100 * (WEIGHT_A * a_sim + WEIGHT_DE * de_sim + WEIGHT_B * b_sim + WEIGHT_C * c_sim + WEIGHT_AGE * age_sim)
    if a_sim < 0.85:
        score = min(score, 60.0)
    return round(score, 1)


def same_stage_comps(target, pool, top_n=5):
    """SAME-STAGE COMPS: strict +/-2 age/experience band, full weighted score."""
    results = []
    for cand in pool:
        if cand["player_id"] == target["player_id"]:
            continue
        score = _same_stage_similarity(target, cand)
        if score is None:
            continue
        results.append({
            "player": cand["player"], "season": cand["season"], "similarity": score,
            "dominant_engine": cand["dominant_engine"],
        })
    results.sort(key=lambda r: -r["similarity"])
    seen, out = set(), []
    for r in results:
        if r["player"] in seen:
            continue
        seen.add(r["player"])
        out.append(r)
        if len(out) == top_n:
            break
    return out


def projected_engine_comps(target, pool, top_n=5):
    """PROJECTED ENGINE COMPS (scouting layer): no age band, A-vector cosine only."""
    target_vec = [target["A"][k] for k in A_KEYS]
    results = []
    for cand in pool:
        if cand["player_id"] == target["player_id"]:
            continue
        sim = _cosine(target_vec, [cand["A"][k] for k in A_KEYS])
        results.append({
            "player": cand["player"], "season": cand["season"],
            "engine_similarity": round(sim * 100, 1), "dominant_engine": cand["dominant_engine"],
        })
    results.sort(key=lambda r: -r["engine_similarity"])
    seen, out = set(), []
    for r in results:
        if r["player"] in seen:
            continue
        seen.add(r["player"])
        out.append(r)
        if len(out) == top_n:
            break
    return out


# ── trajectory: simple age-curve delta projection, no ML ──────────────────

TRACKED = ["usg_pct", "ast_pct", "blk_pct", "drb_pct", "stl_pct"]


def build_age_curves(pool):
    by_arch_exp = defaultdict(lambda: defaultdict(list))
    for p in pool:
        dominant_named = max(p["named_mix"], key=p["named_mix"].get)
        by_arch_exp[dominant_named][p["experience"]].append(p)

    curves = {}
    for arch, by_exp in by_arch_exp.items():
        points = {}
        for exp, rows in by_exp.items():
            points[exp] = {stat: sum(r[stat] for r in rows) / len(rows) for stat in TRACKED}
        # 3-point centered smoothing over experience axis
        exps = sorted(points)
        smoothed = {}
        for e in exps:
            window = [points[x] for x in (e - 1, e, e + 1) if x in points]
            smoothed[e] = {stat: sum(w[stat] for w in window) / len(window) for stat in TRACKED}
        curves[arch] = smoothed
    return curves


def project_next_season(p, curves):
    arch = max(p["named_mix"], key=p["named_mix"].get)
    arch_curve = curves.get(arch, {})
    cur_exp = p["experience"]
    if cur_exp not in arch_curve or cur_exp + 1 not in arch_curve:
        return {stat: p[stat] for stat in TRACKED}
    return {
        stat: round(p[stat] + (arch_curve[cur_exp + 1][stat] - arch_curve[cur_exp][stat]), 2)
        for stat in TRACKED
    }


def ceiling_floor(p, curves, years=3):
    arch = max(p["named_mix"], key=p["named_mix"].get)
    arch_curve = curves.get(arch, {})
    cur_exp = p["experience"]
    future = [arch_curve[e] for e in range(cur_exp, cur_exp + years + 1) if e in arch_curve]
    if not future:
        return {"ceiling_usg_pct": None, "floor_usg_pct": None, "years_projected": years}
    usg_path = [f["usg_pct"] for f in future]
    spread = max(usg_path) - min(usg_path)
    return {
        "ceiling_usg_pct": round(p["usg_pct"] + spread, 1),
        "floor_usg_pct": round(max(p["usg_pct"] - spread * 0.5, 0), 1),
        "years_projected": years,
    }


# ── public entry point used by server.py ──────────────────────────────────

def build_player_report(conn, q, player_id, season):
    pool = annotate(load_pool(conn, q))
    target = next((p for p in pool if p["player_id"] == player_id and p["season"] == int(season)), None)
    if target is None:
        return None

    curves = build_age_curves(pool)
    return {
        "player": target["player"],
        "season": target["season"],
        "development_stage": target["dev_stage"],
        "experience": target["experience"],
        "age": target["age"],
        "dominant_engine": target["dominant_engine"],
        "archetype_weights": target["named_mix"],
        "same_stage_comps": same_stage_comps(target, pool),
        "projected_engine_comps": projected_engine_comps(target, pool),
        "next_season_projection": project_next_season(target, curves),
        "ceiling_floor": ceiling_floor(target, curves),
    }
