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

Percentile-ranking happens within academic_year groups against
archive_cbb_player_stats (sports-reference.com/cbb), which has fg3a_rate
and ft_rate pre-computed. archive_ncaa_player_stats is empty and not used.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Optional

import archetype_engine as ae

log = logging.getLogger("draft_projection.archetype_adapter")

PERCENTILE_COLS = ["usg_pct", "ast_pct", "blk_pct", "dreb_pct", "stl_pct", "fg3a_rate", "ft_rate"]


def _add_college_percentiles(rows: list[dict]) -> None:
    """Mutates rows in place, adding f"{col}_pr" keys, grouped by academic_year.
    Missing values default to 0.5 -- matches archetype_engine.add_percentiles'
    convention for missing data."""
    groups = defaultdict(list)
    for r in rows:
        groups[r.get("academic_year")].append(r)

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
        "dbpm": None,  # no NBA-style defensive BPM at the college level
        "ht_in": r.get("height_in"),  # feeds _size_factor() in named_archetype_mix
    }


def compute_archetype_mix(conn, q, *, player_name: str) -> Optional[dict]:
    """Returns the same named_mix shape archetype_engine.build_player_report
    already returns for NBA players (9 archetype names -> percentage),
    computed from this prospect's most recent college season's peer pool.
    Returns None if no college stats are available."""
    from server import normalize_name_for_match
    key = normalize_name_for_match(player_name)

    season_rows = q(conn, """
        SELECT academic_year FROM archive_cbb_player_stats
        WHERE name_key = ? ORDER BY academic_year DESC LIMIT 1
    """, (key,))
    if not season_rows:
        return None
    academic_year = season_rows[0]["academic_year"]

    pool_rows = q(conn, """
        SELECT name_key, academic_year, height_in,
               usg_pct, ast_pct, blk_pct, dreb_pct, stl_pct, fg3a_rate, ft_rate
        FROM archive_cbb_player_stats
        WHERE academic_year = ?
    """, (academic_year,))
    if not pool_rows:
        return None

    rows = [dict(r) for r in pool_rows]
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
    """0-100 cosine similarity between two named_mix dicts. None if either
    side is unavailable."""
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
