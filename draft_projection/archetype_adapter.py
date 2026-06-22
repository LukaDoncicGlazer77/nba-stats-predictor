"""
College-side adapter for the existing NBA archetype engine
(archetype_engine.py) -- computes the same _pr-percentile inputs that
creation_burden()/defensive_role()/scoring_profile()/named_archetype_mix()
already expect, ranked within a college-season pool instead of an NBA-season
pool, then calls those NBA functions UNCHANGED. A prospect's college
archetype mix therefore uses the exact same 9 named archetypes (Heliocentric
Engine, 3&D Wing, Rim Protector, etc.) an NBA player already gets from
/api/archetype -- "this prospect's college profile reads most like X" is a
same-units comparison, not a parallel system.

Percentile-ranking happens within (academic_year, division) groups, mirroring
archetype_engine.add_percentiles()'s per-NBA-season grouping -- a prospect's
usage/rebounding/etc. percentile should be computed against their actual
peer group (same season, same division), not a different competitive level
or era.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Optional

import archetype_engine as ae

log = logging.getLogger("draft_projection.archetype_adapter")

# NCAA column -> the _pr key archetype_engine's pure functions expect.
PERCENTILE_COLS = ["usg_pct", "ast_pct", "blk_pct", "dreb_pct", "stl_pct", "fg3a_rate", "ft_rate"]


def _add_derived_rates(rows: list[dict]) -> None:
    """fg3a_rate = 3PA/FGA, ft_rate = FTA/FGA -- the same definitions
    archetype_engine expects from NBA's x3p_ar/f_tr, computed here since the
    NCAA scraper stores raw fg3a/fga/fta rather than the pre-divided rate."""
    for r in rows:
        fga = r.get("fga")
        r["fg3a_rate"] = (r["fg3a"] / fga) if fga and r.get("fg3a") is not None else None
        r["ft_rate"] = (r["fta"] / fga) if fga and r.get("fta") is not None else None


def _add_college_percentiles(rows: list[dict]) -> None:
    """Mutates rows in place, adding f"{col}_pr" keys, grouped by
    (academic_year, division). Missing values default to the neutral 0.5
    percentile -- matches archetype_engine.add_percentiles' own convention
    for missing data rather than dropping the player."""
    groups = defaultdict(list)
    for r in rows:
        groups[(r.get("academic_year"), r.get("division"))].append(r)

    for group_rows in groups.values():
        for col in PERCENTILE_COLS:
            present = [r for r in group_rows if r.get(col) is not None]
            n = len(present)
            if n == 0:
                continue
            ordered = sorted(present, key=lambda r: r[col])
            for i, r in enumerate(ordered):
                r[f"{col}_pr"] = (i + 1) / n
        for r in group_rows:
            for col in PERCENTILE_COLS:
                r.setdefault(f"{col}_pr", 0.5)


def _row_to_archetype_input(r: dict) -> dict:
    return {
        "usg_pct_pr": r["usg_pct_pr"], "ast_pct_pr": r["ast_pct_pr"],
        "blk_pct_pr": r["blk_pct_pr"], "drb_pct_pr": r["dreb_pct_pr"],
        "stl_pct_pr": r["stl_pct_pr"], "fg3a_rate_pr": r["fg3a_rate_pr"],
        "ft_rate_pr": r["ft_rate_pr"],
        "dbpm": None,  # no NBA-style defensive box plus-minus at the college level
    }


def compute_archetype_mix(conn, q, *, player_name: str) -> Optional[dict]:
    """Returns the same named_mix shape archetype_engine.build_player_report
    already returns for NBA players (9 archetype names -> percentage),
    computed from this prospect's most recent college season's peer pool.
    Returns None if no college stats are available (scraper not run yet, or
    no name match) -- callers must treat that as "unknown", not "zero"."""
    from server import normalize_name_for_match
    key = normalize_name_for_match(player_name)

    season_rows = q(conn, """
        SELECT academic_year, division FROM archive_ncaa_player_stats
        WHERE name_key = ? ORDER BY academic_year DESC LIMIT 1
    """, (key,))
    if not season_rows:
        return None
    academic_year, division = season_rows[0]["academic_year"], season_rows[0]["division"]

    pool_rows = q(conn, """
        SELECT name_key, player_name, academic_year, division,
               usg_pct, ast_pct, blk_pct, dreb_pct, stl_pct, fg3a, fga, fta
        FROM archive_ncaa_player_stats
        WHERE academic_year = ? AND division = ?
    """, (academic_year, division))
    if not pool_rows:
        return None

    rows = [dict(r) for r in pool_rows]
    _add_derived_rates(rows)
    _add_college_percentiles(rows)

    target_rows = [r for r in rows if r["name_key"] == key]
    if not target_rows:
        return None
    target = target_rows[0]

    p = _row_to_archetype_input(target)
    creation = ae.creation_burden(p)
    defense = ae.defensive_role(p)
    scoring = ae.scoring_profile(p)
    usage = ae.usage_level(p)
    return ae.named_archetype_mix(p, creation, defense, scoring, usage)


def archetype_match_strength(mix_a: Optional[dict], mix_b: Optional[dict]) -> Optional[float]:
    """0-100 similarity between two named_mix dicts (cosine over the 9
    archetype percentages -- these are compositional proportions summing to
    100%, which is exactly the case cosine similarity is well-suited for,
    unlike the scalar features in comp_engine). None if either side is
    unavailable -- never silently scored as dissimilar."""
    if not mix_a or not mix_b:
        return None
    keys = set(mix_a) | set(mix_b)
    va = [mix_a.get(k, 0.0) for k in keys]
    vb = [mix_b.get(k, 0.0) for k in keys]
    dot = sum(x * y for x, y in zip(va, vb))
    na = sum(x * x for x in va) ** 0.5
    nb = sum(y * y for y in vb) ** 0.5
    if na == 0 or nb == 0:
        return None
    return round(100 * max(0.0, dot / (na * nb)), 1)
