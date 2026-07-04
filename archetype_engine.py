"""
Archetype + comp-engine + trajectory module for statfuel.online.

Ports the design built and validated in /Downloads/archive/nba_model/ onto
this site's existing data: the archive_advanced table already has
usg_percent, ast_percent, blk_percent, drb_percent, stl_percent, x3p_ar,
f_tr, dbpm and real age for every season 1947-2026 (including Wemby/Chet/
Luka), so unlike the prototype this needs NO era-blending or derived-stat
estimation -- it queries one already-correct table.

Comp scoring (_composite_similarity) is a weighted blend of four embeddings
-- playstyle (how offense is generated), efficiency-adjusted stats (how
much, discounted for empty volume), advanced impact metrics, and physical
profile -- specifically so two players with similar raw usage/assist
volume but different efficiency and turnover profiles don't read as
comps just because their box scores rhyme.

Public entry point: build_player_report(conn, player_id, season) -> dict,
used by the /api/archetype endpoint in server.py.

Everything here is read-only against the existing `q()` connection pattern
used elsewhere in server.py -- no new tables, no writes.
"""
from __future__ import annotations

import math
from collections import defaultdict

MIN_GAMES_SEASON = 20  # garbage-time/injury-shortened-season filter (per_game table has no MP total column, g is the available volume signal)
HIGH_USAGE_THRESHOLD = 30.0  # absolute usg_percent cutoff for "efficiency under load"

A_KEYS = ["heliocentric_engine", "secondary_playmaker", "off_ball_scorer", "non_creator_finisher"]
B_KEYS = ["rim_protector", "versatile_defender"]
C_KEYS = ["three_pt_pressure", "interior_pressure"]

# Per-season percentile columns. The "_pr" suffix on each is added by add_percentiles().
PERCENTILE_COLS = [
    "usg_pct", "ast_pct", "blk_pct", "drb_pct", "stl_pct", "fg3a_rate", "ft_rate",
    "ts_pct", "tov_pct", "bpm", "obpm", "vorp", "pts_pg", "trb_pg", "ht_in", "wt",
]

# Final comp score = weighted combination of four embeddings: how a player generates
# offense (playstyle), how much they produce once you discount empty volume
# (efficiency-adjusted stats), broader two-way impact (advanced metrics), and body
# profile (physical). This replaces a single raw usage/assist vector specifically so
# that two high-usage, high-assist players with very different efficiency and
# turnover profiles don't read as comps just because their box scores rhyme.
COMP_WEIGHTS = {"playstyle": 0.40, "stats": 0.30, "advanced": 0.20, "physical": 0.10}


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
               blk_percent, drb_percent, stl_percent, x3p_ar, f_tr, dbpm,
               ts_percent, tov_percent, bpm, obpm, vorp
        FROM archive_advanced
    """)
    games_rows = q(conn, """
        SELECT player_id, season, g, pts_per_game, trb_per_game
        FROM archive_player_per_game
    """)
    physical_rows = q(conn, "SELECT player_id, ht_in_in, wt FROM archive_player_career_info")
    shooting_rows = q(conn, """
        SELECT player_id, season, g,
               percent_fga_from_x2p_range, percent_fga_from_x3p_range,
               percent_assisted_x2p_fg, percent_assisted_x3p_fg
        FROM archive_player_shooting
    """)

    games_by_key = {}
    for r in games_rows:
        key = (r["player_id"], r["season"])
        g = _to_float(r["g"]) or 0
        if key not in games_by_key or g > games_by_key[key]["games"]:
            games_by_key[key] = {
                "games": g,
                "pts_pg": _to_float(r["pts_per_game"]),
                "trb_pg": _to_float(r["trb_per_game"]),
            }

    shooting_by_key = {}
    for r in shooting_rows:
        key = (r["player_id"], r["season"])
        g = _to_float(r["g"]) or 0
        x2p  = _to_float(r["percent_fga_from_x2p_range"])
        x3p  = _to_float(r["percent_fga_from_x3p_range"])
        ast2 = _to_float(r["percent_assisted_x2p_fg"])
        ast3 = _to_float(r["percent_assisted_x3p_fg"])
        if None in (x2p, x3p, ast2, ast3):
            continue
        sc = (1 - ast2) * x2p + (1 - ast3) * x3p
        if key not in shooting_by_key or g > shooting_by_key[key]["games"]:
            shooting_by_key[key] = {"games": g, "self_creation": sc}

    physical_by_player = {
        r["player_id"]: (_to_float(r["ht_in_in"]), _to_float(r["wt"])) for r in physical_rows
    }

    pool = []
    for r in adv_rows:
        key = (r["player_id"], r["season"])
        per_game = games_by_key.get(key)
        games = per_game["games"] if per_game else None
        usg, ast, blk, drb, stl = (
            _to_float(r["usg_percent"]), _to_float(r["ast_percent"]), _to_float(r["blk_percent"]),
            _to_float(r["drb_percent"]), _to_float(r["stl_percent"]),
        )
        if None in (usg, ast, blk, drb, stl) or games is None or games < MIN_GAMES_SEASON:
            continue
        ht_in, wt = physical_by_player.get(r["player_id"], (None, None))
        shooting = shooting_by_key.get(key)
        pool.append({
            "player": r["player"], "player_id": r["player_id"],
            "season": int(r["season"]), "age": _to_float(r["age"]), "games": games,
            "usg_pct": usg, "ast_pct": ast, "blk_pct": blk, "drb_pct": drb, "stl_pct": stl,
            "fg3a_rate": _to_float(r["x3p_ar"]) or 0.0, "ft_rate": _to_float(r["f_tr"]) or 0.0,
            "dbpm": _to_float(r["dbpm"]),
            # efficiency / impact signals -- absent for some early-era seasons (e.g.
            # turnovers weren't tracked league-wide before 1973-74), handled as
            # missing-but-neutral in add_percentiles() rather than dropped.
            "ts_pct": _to_float(r["ts_percent"]), "tov_pct": _to_float(r["tov_percent"]),
            "bpm": _to_float(r["bpm"]), "obpm": _to_float(r["obpm"]), "vorp": _to_float(r["vorp"]),
            "pts_pg": per_game["pts_pg"], "trb_pg": per_game["trb_pg"],
            "ht_in": ht_in, "wt": wt,
            "self_creation": shooting["self_creation"] if shooting else None,
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
        for col in PERCENTILE_COLS:
            present = [p for p in season_rows if p.get(col) is not None]
            n = len(present)
            if n == 0:
                continue
            ordered = sorted(present, key=lambda p: p[col])
            for i, p in enumerate(ordered):
                p[f"{col}_pr"] = (i + 1) / n
        for p in season_rows:
            for col in PERCENTILE_COLS:
                p.setdefault(f"{col}_pr", 0.5)  # missing data -> neutral, not dropped
    return pool


def add_efficiency_under_load(pool):
    """Ranks TS% only among genuinely high-usage seasons (usg_pct > 30) within that
    season -- isolates how well a player scores once an offense is actually run
    through them, rather than letting raw usage volume stand in for efficiency.
    Seasons below the threshold get None here and fall back to plain ts_pct_pr."""
    by_season = defaultdict(list)
    for p in pool:
        if p["usg_pct"] > HIGH_USAGE_THRESHOLD and p["ts_pct"] is not None:
            by_season[p["season"]].append(p)

    for rows in by_season.values():
        n = len(rows)
        ordered = sorted(rows, key=lambda p: p["ts_pct"])
        for i, p in enumerate(ordered):
            p["efficiency_under_load_pr"] = (i + 1) / n

    for p in pool:
        p.setdefault("efficiency_under_load_pr", None)
    return pool


def add_self_creation_percentile(pool):
    """Ranks self_creation (unassisted-FGA fraction) per season among player-seasons
    with shooting data (1997+). Pre-1997 players have no _pr key; creation_burden()
    falls back to plain usg*ast for them."""
    by_season = defaultdict(list)
    for p in pool:
        if p.get("self_creation") is not None:
            by_season[p["season"]].append(p)
    for rows in by_season.values():
        n = len(rows)
        for i, p in enumerate(sorted(rows, key=lambda x: x["self_creation"])):
            p["self_creation_pr"] = (i + 1) / n
    return pool


# ── archetype dimensions (same math as the prototype) ─────────────────────

def _size_factor(ht_in) -> float:
    """Logistic gate for 'Big' archetypes. Returns ~1.0 at 6'9"+ (81 in),
    ~0.5 at 6'6" (78 in), ~0.18 at 6'5", ~0.01 at 6'3" and below.
    Missing height → 1.0 so an unknown-height player is never penalised."""
    if ht_in is None:
        return 1.0
    return 1.0 / (1.0 + math.exp(-1.5 * (ht_in - 78)))


def _softmax(scores: dict) -> dict:
    exps = {k: math.exp(1.5 * v) for k, v in scores.items()}
    total = sum(exps.values()) or 1.0
    return {k: round(100 * v / total, 1) for k, v in exps.items()}


def creation_burden(p):
    usg, ast = p["usg_pct_pr"], p["ast_pct_pr"]
    sc = p.get("self_creation_pr")
    he_score = usg * ast * (sc ** 0.5) if sc is not None else usg * ast
    return _softmax({
        "heliocentric_engine": he_score,
        "secondary_playmaker": ast if 0.55 <= usg < 0.85 else 0.4 * ast,
        "off_ball_scorer": (1 - ast) * usg if usg >= 0.4 else 0.3,
        "non_creator_finisher": (1 - usg) * (1 - ast),
    })


def defensive_role(p):
    rim = 0.6 * p["blk_pct_pr"] + 0.4 * p["drb_pct_pr"]
    versatile = p["stl_pct_pr"] if p["dbpm"] is None else 0.5 * p["stl_pct_pr"] + 0.5 * max(p["dbpm"], 0) / 5
    softmaxed = _softmax({"rim_protector": rim, "versatile_defender": versatile})
    return {
        "rim_protector": softmaxed["rim_protector"],
        "versatile_defender": softmaxed["versatile_defender"],
        "rim_protector_raw": rim,
        "versatile_defender_raw": versatile,
    }


def scoring_profile(p):
    rim_pr = p.get("rim_att_rate_pr")
    three_pr = p.get("three_att_rate_pr")
    rim_ast_pr = p.get("rim_ast_pct_pr")      # high = finishes off others' creation
    three_unast_pr = p.get("three_unast_pct_pr")  # high = self-created 3PT threat

    if rim_pr is not None and three_pr is not None:
        interior = rim_pr
        three_pt = three_pr
        # Blend in self-creation signal when available: unassisted 3s lift three_pt
        # pressure; high rim assisted% slightly dampens interior (finisher, not creator).
        if three_unast_pr is not None:
            three_pt = 0.7 * three_pr + 0.3 * three_unast_pr
        if rim_ast_pr is not None:
            interior = 0.8 * rim_pr + 0.2 * (1.0 - rim_ast_pr)
        return _softmax({"three_pt_pressure": three_pt, "interior_pressure": interior})
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
    rim_raw = defense["rim_protector_raw"]
    versatile_raw = defense["versatile_defender_raw"]
    sf = _size_factor(p.get("ht_in"))

    raw = {
        "Heliocentric Engine": creation["heliocentric_engine"],
        "Secondary Playmaker": creation["secondary_playmaker"],
        "Off-Ball Scorer": creation["off_ball_scorer"] * scoring["three_pt_pressure"] / 100 * 2,
        "Scoring Big": sf * (creation["off_ball_scorer"] * scoring["interior_pressure"] / 100 * 2
            + creation["non_creator_finisher"] * scoring["interior_pressure"] / 100 * 1.5),
        "Playmaking Big": sf * (creation["secondary_playmaker"] * rim_raw * 1.5
            + creation["heliocentric_engine"] * rim_raw * 1.5),
        "Rim Protector": sf * (rim_raw * low_creation * 2),
        "3&D Wing": versatile_raw * scoring["three_pt_pressure"] * (low_creation / 100) * 3,
        "Defensive Wing": versatile_raw * low_creation * 2 * (1 if usage == "low" else 0.5),
        "Hybrid Offensive Big": sf * (hybrid * 4),
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
    add_efficiency_under_load(pool)
    add_self_creation_percentile(pool)
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

        # efficiency_signal: TS% ranked specifically among high-usage seasons when this
        # was one (genuine "engine" efficiency), else plain TS% percentile.
        p["efficiency_signal"] = (
            p["efficiency_under_load_pr"] if p["efficiency_under_load_pr"] is not None else p["ts_pct_pr"]
        )
        p["ball_security"] = 1 - p["tov_pct_pr"]
        # usage weighted by how well that usage is converted, not the raw usage itself
        p["usage_efficiency"] = p["usg_pct_pr"] * p["efficiency_signal"]

        p["playstyle_vec"] = [
            creation["heliocentric_engine"] / 100, creation["secondary_playmaker"] / 100,
            creation["off_ball_scorer"] / 100, creation["non_creator_finisher"] / 100,
            scoring["three_pt_pressure"] / 100, scoring["interior_pressure"] / 100,
            p["ball_security"], p["efficiency_signal"], p["usage_efficiency"],
        ]
        # efficiency-adjusted production volume: raw counting stats discounted (not
        # zeroed) by how efficiently they were produced, so a high-usage/low-efficiency
        # stat line no longer reads as equivalent to a high-usage/high-efficiency one.
        eff_mult = 0.5 + 0.5 * p["efficiency_signal"]
        p["stats_vec"] = [
            p["pts_pg_pr"] * eff_mult, p["ast_pct_pr"] * eff_mult,
            p["trb_pg_pr"], p["stl_pct_pr"], p["blk_pct_pr"],
        ]
        p["advanced_vec"] = [p["bpm_pr"], p["obpm_pr"], p["vorp_pr"]]
        p["physical_vec"] = [p["ht_in_pr"], p["wt_pr"]] if p["ht_in"] is not None and p["wt"] is not None else None
    return pool


# ── comp engine: two separate, never-mixed layers ──────────────────────────

def _cosine(va, vb):
    dot = sum(a * b for a, b in zip(va, vb))
    na, nb = math.sqrt(sum(a * a for a in va)), math.sqrt(sum(b * b for b in vb))
    return dot / (na * nb) if na and nb else 0.0


def _age_band_ok(a, b, band=2):
    if a["age"] is not None and b["age"] is not None:
        return abs(a["age"] - b["age"]) <= band
    return abs(a["experience"] - b["experience"]) <= band


def _composite_similarity(a, b):
    """The shared scoring core for both comp layers: a weighted combination of
    playstyle (how offense is generated), efficiency-adjusted stats (how much,
    discounted for empty volume), advanced two-way impact, and physical profile.
    Falls back to dropping the physical term (renormalizing the rest) when height/
    weight is missing for either player-season, rather than scoring it as 0."""
    playstyle_sim = _cosine(a["playstyle_vec"], b["playstyle_vec"])
    stats_sim = _cosine(a["stats_vec"], b["stats_vec"])
    advanced_sim = _cosine(a["advanced_vec"], b["advanced_vec"])
    physical_sim = (
        _cosine(a["physical_vec"], b["physical_vec"])
        if a["physical_vec"] is not None and b["physical_vec"] is not None else None
    )

    terms = [(COMP_WEIGHTS["playstyle"], playstyle_sim), (COMP_WEIGHTS["stats"], stats_sim),
             (COMP_WEIGHTS["advanced"], advanced_sim)]
    if physical_sim is not None:
        terms.append((COMP_WEIGHTS["physical"], physical_sim))
    total_w = sum(w for w, _ in terms)
    score = sum(w * s for w, s in terms) / total_w

    # Penalty for false equivalence: near-identical box-score volume but a clearly
    # different creation mechanism/efficiency profile (e.g. two high-usage,
    # high-assist guards who diverge sharply on scoring efficiency and ball
    # security) should not score as a strong comp just because the stat line rhymes.
    if stats_sim > 0.85 and playstyle_sim < 0.55:
        score = min(score, 0.55)

    # Direct efficiency-divergence penalty: cosine similarity on mostly-positive,
    # role-aligned vectors can stay high even when efficiency_signal diverges sharply
    # (e.g. two similar-usage, similar-role engines where one converts that load far
    # more efficiently than the other), since the role dims dominate the dot product.
    # This compares the scalar gap directly so that divergence isn't diluted away.
    efficiency_divergence = abs(a["efficiency_signal"] - b["efficiency_signal"])
    if efficiency_divergence > 0.25:
        score *= max(0.4, 1 - efficiency_divergence)

    breakdown = {
        "playstyle_similarity": round(100 * playstyle_sim, 1),
        "efficiency_adjusted_stats_similarity": round(100 * stats_sim, 1),
        "advanced_metrics_similarity": round(100 * advanced_sim, 1),
        "physical_similarity": round(100 * physical_sim, 1) if physical_sim is not None else None,
        "efficiency_divergence": round(100 * efficiency_divergence, 1),
    }
    # Unrounded: callers sort on this so near-ties aren't collapsed to the same
    # value before ranking (that was producing arbitrary-order ties in the top 5).
    return 100 * score, breakdown


def _efficiency_label(pr):
    if pr is None:
        return "unknown efficiency"
    return ("elite efficiency" if pr >= 0.85 else "strong efficiency" if pr >= 0.65
            else "average efficiency" if pr >= 0.35 else "below-average efficiency")


def _usage_label(level):
    return {"extreme": "primary, ball-dominant", "high": "high-usage",
            "medium": "moderate-usage", "low": "low-usage"}[level]


def _seed(*parts):
    """Stable (non-randomized, process-independent) hash for picking template
    variants -- Python's built-in hash() of strings is salted per-process, which
    would make explanation text change between requests for the same pair."""
    h = 0
    for s in parts:
        for ch in str(s):
            h = (h * 131 + ord(ch)) % 1000003
    return h


def _pick(options, *seed_parts):
    return options[_seed(*seed_parts) % len(options)]


def _scoring_lean_label(c_dict):
    if c_dict["three_pt_pressure"] >= 60:
        return "primarily beyond the arc"
    if c_dict["interior_pressure"] >= 60:
        return "primarily at the rim/free-throw line"
    return "a balanced inside-outside mix"


def _defense_lean_label(b_dict):
    if b_dict["rim_protector"] >= 60:
        return "rim protection"
    if b_dict["versatile_defender"] >= 60:
        return "versatile, ball-pressure defense"
    return "a blended defensive role"


def _role_clause(target, cand):
    t_role, c_role = target["dominant_engine"].replace("_", " "), cand["dominant_engine"].replace("_", " ")
    t_usage, c_usage = _usage_label(target["D_usage_level"]), _usage_label(cand["D_usage_level"])
    if t_role == c_role:
        options = [
            f"Both project primarily as a {t_role} ({t_usage} usage vs {c_usage} usage).",
            f"{target['player']} and {cand['player']} share a {t_role} foundation -- {t_usage} usage vs {c_usage} usage.",
            f"Same primary engine for both: {t_role}, with {target['player']} carrying {t_usage} usage against {cand['player']}'s {c_usage} usage.",
        ]
        return _pick(options, target["player"], cand["player"], "role")
    options = [
        f"{target['player']} reads as a {t_role}; {cand['player']} reads as a {c_role} -- different primary offensive roles.",
        f"Different offensive identities: {target['player']} projects as a {t_role} while {cand['player']} leans {c_role}.",
        f"The two diverge on offensive role -- {t_role} for {target['player']} vs {c_role} for {cand['player']}.",
    ]
    return _pick(options, target["player"], cand["player"], "role")


def _efficiency_clause(target, cand, breakdown):
    t_eff_pr = target["efficiency_under_load_pr"] if target["efficiency_under_load_pr"] is not None else target["ts_pct_pr"]
    c_eff_pr = cand["efficiency_under_load_pr"] if cand["efficiency_under_load_pr"] is not None else cand["ts_pct_pr"]
    t_label, c_label = _efficiency_label(t_eff_pr), _efficiency_label(c_eff_pr)
    options = [
        f"Scoring efficiency under offensive load: {t_label} vs {c_label}.",
        f"Efficiency under load reads as {t_label} for {target['player']}, {c_label} for {cand['player']}.",
        f"On converting that workload into points, {target['player']} grades as {t_label} and {cand['player']} as {c_label}.",
    ]
    clause = _pick(options, target["player"], cand["player"], "eff")
    if breakdown["efficiency_divergence"] > 25:
        clause += (
            f" Efficiency profiles diverge by {breakdown['efficiency_divergence']} percentile points despite "
            f"similar roles/volume -- score is penalized for this, treat as a partial comp, not a true one."
        )
    elif breakdown["efficiency_adjusted_stats_similarity"] - breakdown["playstyle_similarity"] > 25:
        clause += " Box-score volume looks similar, but the underlying creation/efficiency profile diverges -- treat this as a partial comp, not a true one."
    return clause


def _scoring_profile_clause(target, cand):
    t_lean, c_lean = _scoring_lean_label(target["C"]), _scoring_lean_label(cand["C"])
    if t_lean == c_lean and t_lean != "a balanced inside-outside mix":
        options = [
            f"Both generate scoring pressure {t_lean}, the same shot-pressure profile.",
            f"Shot-pressure profiles match -- {target['player']} and {cand['player']} both lean {t_lean}.",
        ]
        return _pick(options, target["player"], cand["player"], "scoring")
    if t_lean != c_lean and "balanced" not in (t_lean, c_lean):
        return f"Shot-pressure profiles diverge: {target['player']} leans {t_lean}, {cand['player']} {c_lean}."
    return None


def _defensive_role_clause(target, cand):
    t_lean, c_lean = _defense_lean_label(target["B"]), _defense_lean_label(cand["B"])
    if t_lean == c_lean and "blended" not in t_lean:
        return f"Defensively, both lean toward {t_lean}."
    if t_lean != c_lean and "blended" not in (t_lean, c_lean):
        return f"Defensive roles differ: {t_lean} for {target['player']} vs {c_lean} for {cand['player']}."
    return None


def _physical_clause(target, cand):
    if target["ht_in"] is None or cand["ht_in"] is None:
        return None
    h_diff = abs(target["ht_in"] - cand["ht_in"])
    w_diff = abs((target["wt"] or 0) - (cand["wt"] or 0))
    if h_diff <= 1 and w_diff <= 15:
        return _pick([
            "Near-identical physical profiles for the two.",
            f"{target['player']} and {cand['player']} carry essentially the same frame.",
        ], target["player"], cand["player"], "phys")
    if h_diff >= 4:
        taller = target["player"] if target["ht_in"] > cand["ht_in"] else cand["player"]
        shorter = cand["player"] if taller == target["player"] else target["player"]
        return f"Notably different frames -- {taller} is sized up significantly versus {shorter}."
    return None


def _era_clause(target, cand):
    gap = abs(target["season"] - cand["season"])
    if gap == 0:
        return f"Same-season snapshot: both from {target['season']}."
    if gap >= 20:
        return f"Cross-era comp spanning {gap} seasons ({min(target['season'], cand['season'])} vs {max(target['season'], cand['season'])})."
    if gap >= 10:
        return f"A {gap}-season gap separates these two -- different eras of the league."
    return None


def explain_comp(target, cand, breakdown):
    """Plain-language basketball explanation for a single comp result. Builds
    from several independent signals (role, efficiency, shot profile, defensive
    role, physical build, era gap) and only includes the ones that are actually
    notable for this specific pair, so the explanation text varies pair-to-pair
    rather than reading as a fixed template repeated for every comp."""
    core = [_role_clause(target, cand), _efficiency_clause(target, cand, breakdown)]
    optional = [
        _scoring_profile_clause(target, cand),
        _defensive_role_clause(target, cand),
        _physical_clause(target, cand),
        _era_clause(target, cand),
    ]
    optional = [c for c in optional if c]
    # Cap how many optional clauses get appended so the explanation stays
    # readable. Rotate the starting point by a stable per-pair seed (rather than
    # always taking the list in scoring/defense/physical/era order) so which
    # signals surface first varies across different comps, not just whether
    # they're present.
    if optional:
        rot = _seed(target["player"], cand["player"], "rot") % len(optional)
        optional = optional[rot:] + optional[:rot]
    max_optional = _pick([1, 2], target["player"], cand["player"], "count")
    parts = core + optional[:max_optional]
    return " ".join(parts)


def same_stage_comps(target, pool, top_n=5):
    """SAME-STAGE COMPS: strict +/-2 age/experience band, full composite score."""
    results = []
    for cand in pool:
        if cand["player_id"] == target["player_id"]:
            continue
        if not _age_band_ok(target, cand):
            continue
        raw_score, breakdown = _composite_similarity(target, cand)
        results.append({
            "player": cand["player"], "season": cand["season"],
            "similarity": round(raw_score, 1), "_raw_score": raw_score,
            "dominant_engine": cand["dominant_engine"], "breakdown": breakdown,
            "explanation": explain_comp(target, cand, breakdown),
        })
    results.sort(key=lambda r: -r["_raw_score"])
    seen, out = set(), []
    for r in results:
        if r["player"] in seen:
            continue
        seen.add(r["player"])
        del r["_raw_score"]
        out.append(r)
        if len(out) == top_n:
            break
    return out


def projected_engine_comps(target, pool, top_n=5):
    """PROJECTED ENGINE COMPS (scouting layer): no age band, same composite score."""
    results = []
    for cand in pool:
        if cand["player_id"] == target["player_id"]:
            continue
        raw_score, breakdown = _composite_similarity(target, cand)
        results.append({
            "player": cand["player"], "season": cand["season"],
            "engine_similarity": round(raw_score, 1), "_raw_score": raw_score,
            "dominant_engine": cand["dominant_engine"], "breakdown": breakdown,
            "explanation": explain_comp(target, cand, breakdown),
        })
    results.sort(key=lambda r: -r["_raw_score"])
    seen, out = set(), []
    for r in results:
        if r["player"] in seen:
            continue
        seen.add(r["player"])
        del r["_raw_score"]
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
