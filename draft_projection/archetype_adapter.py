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

import csv
import logging
import os
import re
from collections import defaultdict
from typing import Optional

import archetype_engine as ae

log = logging.getLogger("draft_projection.archetype_adapter")

PERCENTILE_COLS = ["usg_pct", "ast_pct", "blk_pct", "dreb_pct", "stl_pct", "fg3a_rate", "ft_rate"]
# Shot-zone cols are ranked separately — they stay None when absent so scoring_profile()
# can fall back to fg3a_rate_pr / ft_rate_pr for players without Barttorvik coverage.
SHOT_ZONE_COLS = ["rim_att_rate", "three_att_rate"]


def _normalize_btv_name(name: str) -> str:
    name = str(name or "").strip()
    if "," in name:
        last, first = name.split(",", 1)
        name = f"{first.strip()} {last.strip()}"
    return re.sub(r"[^a-z ]", "", name.lower()).strip()


def _to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# (normalized_name, year) -> {rim_att_rate, three_att_rate}
_SHOT_ZONES: dict[tuple, dict] = {}

def _load_shot_zones() -> None:
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "shot_zones.csv")
    if not os.path.exists(path):
        log.warning("shot_zones.csv not found at %s — shot-zone scoring signals disabled", path)
        return
    loaded = 0
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            rim_a = _to_float(row.get("rim_a"))
            mid_a = _to_float(row.get("mid_a"))
            three_a = _to_float(row.get("three_a"))
            if rim_a is None or mid_a is None or three_a is None:
                continue
            total = rim_a + mid_a + three_a
            if total <= 0:
                continue
            try:
                year = int(row["year"])
            except (KeyError, ValueError):
                continue
            key = (_normalize_btv_name(row.get("player", "")), year)
            _SHOT_ZONES[key] = {
                "rim_att_rate": rim_a / total,
                "three_att_rate": three_a / total,
            }
            loaded += 1
    log.info("Loaded %d shot-zone records from shot_zones.csv", loaded)

_load_shot_zones()


def _add_shot_zone_percentiles(rows: list[dict]) -> None:
    """Ranks rim_att_rate / three_att_rate only among rows that HAVE the data.
    Players without coverage keep None so scoring_profile() can fall back."""
    groups = defaultdict(list)
    for r in rows:
        groups[r.get("academic_year")].append(r)
    for group_rows in groups.values():
        for col in SHOT_ZONE_COLS:
            present = [r for r in group_rows if r.get(col) is not None]
            n = len(present)
            if n == 0:
                continue
            ordered = sorted(present, key=lambda r: r[col])
            for i, r in enumerate(ordered):
                r[f"{col}_pr"] = (i + 1) / n


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
        "rim_att_rate_pr": r.get("rim_att_rate_pr"),
        "three_att_rate_pr": r.get("three_att_rate_pr"),
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

    # Merge Barttorvik shot-zone rates into each row before percentile ranking.
    # Key: (normalized player name, academic_year). Missing → None, falls back
    # to ft_rate_pr / fg3a_rate_pr inside scoring_profile().
    for r in rows:
        sz = _SHOT_ZONES.get((r.get("name_key", ""), r.get("academic_year")))
        if sz:
            r["rim_att_rate"] = sz["rim_att_rate"]
            r["three_att_rate"] = sz["three_att_rate"]
        else:
            r["rim_att_rate"] = None
            r["three_att_rate"] = None

    _add_college_percentiles(rows)
    _add_shot_zone_percentiles(rows)

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
