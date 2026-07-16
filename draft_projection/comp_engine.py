"""
Historical Comparable Engine: finds the most similar historically-drafted
players to a given prospect, weighted by category, with the same
graceful-degradation behavior archetype_engine._composite_similarity already
uses for missing data (drop a category and renormalize remaining weights,
rather than scoring missing data as a hard 0).

Draft context is capped at 5% of the similarity score and represented only
by the coarse draft_slot_tier bucket (see features.py) -- per design
direction, this engine should find players who *played* like the prospect,
not players who were *drafted* like the prospect.

Category weights reflect the explicit priority order set for this system
(2026-06-21): college production first, then real advanced metrics (PER/
Win-Shares/BPM-style, only meaningful once a source that actually publishes
them -- sports-reference.com/cbb -- replaced stats.ncaa.org, which
structurally could not provide them), then physical profile/age, then
role/efficiency, with draft position always last and capped low.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from draft_projection.features import (
    FEATURE_CATEGORY, FEATURE_NAMES, build_feature_vector, bulk_build_feature_vectors,
)
from draft_projection.labels import build_career_labels

log = logging.getLogger("draft_projection.comp_engine")

CATEGORY_WEIGHTS = {
    "production": 0.32,
    "advanced": 0.22,
    "efficiency": 0.15,
    "role": 0.18,
    "physical": 0.12,
    "draft_context": 0.01,
}
CATEGORIES = list(CATEGORY_WEIGHTS.keys())
FEATURES_BY_CATEGORY = {
    cat: [f for f in FEATURE_NAMES if FEATURE_CATEGORY[f] == cat] for cat in CATEGORIES
}


@dataclass
class PoolMember:
    player: str
    player_id: str
    draft_season: float
    overall_pick: Optional[float]
    college: Optional[str]
    tier: str
    tier_label: str
    raw_row: dict = field(default_factory=dict)
    z_row: dict = field(default_factory=dict)
    missing: dict = field(default_factory=dict)


def _age_at_draft(birth_date, draft_season) -> Optional[float]:
    if not birth_date or draft_season is None or (isinstance(draft_season, float) and draft_season != draft_season):
        return None
    try:
        birth_year = int(str(birth_date)[:4])
    except (TypeError, ValueError):
        return None
    # Draft happens mid-year (June); approximate age at draft as season - birth_year - 0.5
    # to roughly center the unknown birth-month error rather than bias it.
    return float(draft_season) - birth_year - 0.5


@dataclass
class HistoricalPool:
    members: list[PoolMember]
    pool_stats: dict  # {"means": {...}, "stds": {...}} -- needed to standardize query vectors consistently

    def __len__(self) -> int:
        return len(self.members)

    def __bool__(self) -> bool:
        return bool(self.members)


def build_historical_pool(conn, q, current_season: int = 2026, min_draft_season: int = 1984) -> HistoricalPool:
    """One feature-vector pass over every labeled historical draft pick.
    Uses bulk_build_feature_vectors (one query per provider total, not one
    per player) -- callers should still cache the result for the lifetime
    of a server process, this isn't free, just no longer minutes-slow.

    min_draft_season limits the pool to the draft-lottery era (1984+) by
    default -- pre-lottery NBA is a poor comp baseline for modern prospects
    and including it more than triples pool size with low signal-density."""
    labels = build_career_labels(conn, q, current_season=current_season)
    if min_draft_season:
        labels = labels[labels["season"] >= min_draft_season]

    birth_rows = q(conn, "SELECT player_id, birth_date FROM archive_player_career_info")
    birth_by_id = {r["player_id"]: r["birth_date"] for r in birth_rows}

    label_rows = labels.to_dict("records")
    requests = [
        {
            "player_name": row["player"], "college": row.get("college"),
            "season": int(row["season"]) if pd.notna(row["season"]) else None,
            "age_at_draft": _age_at_draft(birth_by_id.get(row["player_id"]), row["season"]),
            "overall_pick": row.get("overall_pick"), "player_id": row["player_id"],
        }
        for row in label_rows
    ]
    from server import normalize_name_for_match
    pool_name_keys = {normalize_name_for_match(row["player"]) for row in label_rows}
    vectors = bulk_build_feature_vectors(conn, q, requests, allowed_name_keys=pool_name_keys)

    members: list[PoolMember] = []
    for row, fv in zip(label_rows, vectors):
        members.append(PoolMember(
            player=row["player"], player_id=row["player_id"], draft_season=row["season"],
            overall_pick=row.get("overall_pick"), college=row.get("college"),
            tier=row["tier"], tier_label=row["tier_label"],
            raw_row=fv.values, missing=fv.missing,
        ))
    log.info("Built historical comp pool: %d players", len(members))
    pool_stats = _standardize_pool(members)
    return HistoricalPool(members=members, pool_stats=pool_stats)


def _standardize_pool(members: list[PoolMember]) -> dict:
    """Z-score each feature across the pool (using only non-missing values
    for the mean/std), then store standardized values on every member,
    including the originally-missing ones (at the pool mean, i.e. z=0 --
    consistent with how they're already treated as "default", not penalized
    twice). Returns the {means, stds} used, so query vectors can be
    standardized the same way at lookup time."""
    if not members:
        return {"means": {}, "stds": {}}
    means, stds = {}, {}
    for name in FEATURE_NAMES:
        vals = [m.raw_row[name] for m in members if not m.missing.get(name, True)]
        if len(vals) >= 2:
            s = pd.Series(vals)
            means[name], stds[name] = float(s.mean()), float(s.std() or 1.0)
        else:
            means[name], stds[name] = 0.0, 1.0
    for m in members:
        for name in FEATURE_NAMES:
            if m.missing.get(name, True):
                m.z_row[name] = 0.0
            else:
                m.z_row[name] = (m.raw_row[name] - means[name]) / (stds[name] or 1.0)
    return {"means": means, "stds": stds}


def _standardize_query(fv_values: dict, fv_missing: dict, pool_stats: dict) -> dict:
    z = {}
    for name in FEATURE_NAMES:
        if fv_missing.get(name, True):
            z[name] = 0.0
        else:
            mean, std = pool_stats["means"][name], pool_stats["stds"][name]
            z[name] = (fv_values[name] - mean) / (std or 1.0)
    return z


def _category_similarity(za: dict, ma: dict, zb: dict, mb: dict, category: str) -> Optional[float]:
    """Gaussian-kernel similarity on standardized (z-scored) feature
    distance, scaled to [0, 1]. Deliberately NOT cosine similarity: cosine
    is scale-invariant, so for a category with only one usable feature
    (which happens constantly here -- draft_context always has exactly one
    feature, and physical degrades to just age_at_draft when height/weight
    are missing) cosine of two scalars is always +1 or -1 regardless of how
    far apart they actually are. That bug was caught by testing: pick #1 and
    pick #30 were scoring 100% "draft context similarity" because both
    z-scores were simply positive. Euclidean distance in z-space doesn't
    have that degenerate case and is the standard approach for this kind of
    comp/similarity score."""
    names = FEATURES_BY_CATEGORY[category]
    if category in _STAT_CATEGORIES:
        # For college stat categories: require BOTH sides to have real data.
        # A high-school draftee (no college career) gets z=0 (pool mean) for
        # every college feature, which would create artificial distance vs. an
        # above-average college prospect. Excluding the category entirely lets
        # the comparison fall back to draft context + physical only — a fair
        # basis when one player simply had no college career.
        usable = [n for n in names if not ma.get(n, True) and not mb.get(n, True)]
    else:
        # For non-statistical categories (draft context, physical): keep the
        # original "at least one side has data" logic.
        usable = [n for n in names if not (ma.get(n, True) and mb.get(n, True))]
    if not usable:
        return None
    sq_dists = [(za[n] - zb[n]) ** 2 for n in usable]
    mean_sq_dist = sum(sq_dists) / len(sq_dists)
    return math.exp(-mean_sq_dist / 2.0)  # 1.0 when identical, decays smoothly with z-distance


def composite_similarity(query_z: dict, query_missing: dict, member: PoolMember) -> tuple[float, dict]:
    """Weighted category similarity with graceful degradation -- mirrors
    archetype_engine._composite_similarity's missing-category
    renormalization rather than scoring missing data as zero similarity."""
    breakdown = {}
    terms = []
    for cat, weight in CATEGORY_WEIGHTS.items():
        sim = _category_similarity(query_z, query_missing, member.z_row, member.missing, cat)
        breakdown[cat] = round(100 * sim, 1) if sim is not None else None
        if sim is not None:
            terms.append((weight, sim))
    if not terms:
        return 0.0, breakdown
    total_w = sum(w for w, _ in terms)
    score = sum(w * s for w, s in terms) / total_w
    return max(0.0, min(100.0, 100 * score)), breakdown


_STAT_CATEGORIES = {"production", "advanced", "efficiency"}


def find_top_comps(conn, q, pool: HistoricalPool, *, player_name: str, college: Optional[str] = None,
                    age_at_draft: Optional[float] = None, overall_pick: Optional[float] = None,
                    top_n: int = 50) -> list[dict]:
    if not pool:
        return []
    fv = build_feature_vector(
        conn, q, player_name=player_name, college=college,
        age_at_draft=age_at_draft, overall_pick=overall_pick,
    )
    # Require at least one real statistical signal (production/advanced/efficiency).
    # Without it the score is driven only by physical + draft slot — too coarse
    # to be meaningful and produces artificially inflated similarities (~95-99%)
    # for international players who have no CBB data.
    stat_features = [f for f in FEATURE_NAMES if FEATURE_CATEGORY[f] in _STAT_CATEGORIES]
    has_stats = any(not fv.missing.get(f, True) for f in stat_features)
    if not has_stats:
        return []

    query_z = _standardize_query(fv.values, fv.missing, pool.pool_stats)

    results = []
    for member in pool.members:
        score, breakdown = composite_similarity(query_z, fv.missing, member)
        results.append({
            "player": member.player, "player_id": member.player_id,
            "draft_season": int(member.draft_season) if member.draft_season == member.draft_season else None,
            "overall_pick": member.overall_pick, "college": member.college,
            "tier": member.tier, "tier_label": member.tier_label,
            "similarity": round(score, 1), "breakdown": breakdown,
        })
    results.sort(key=lambda r: -r["similarity"])
    return results[:top_n]


def explain_comp(prospect_name: str, comp: dict) -> str:
    """Plain-language explanation, built from whichever similarity
    categories actually had data to compare -- not a fixed template."""
    breakdown = comp["breakdown"]
    available = {k: v for k, v in breakdown.items() if v is not None}
    if not available:
        return (
            f"Limited basis for comparison -- {comp['player']} is included primarily as a "
            f"draft-context comp (similar slot) since college statistical data wasn't available "
            f"for one or both players."
        )
    best_cat = max(available, key=available.get)
    parts = [
        f"{comp['player']} (pick #{comp['overall_pick']:.0f}, {comp['draft_season']}) "
        f"is most similar on {best_cat.replace('_', ' ')} ({available[best_cat]}% match)."
    ]
    parts.append(f"Actual NBA outcome: {comp['tier_label']}.")
    return " ".join(parts)
