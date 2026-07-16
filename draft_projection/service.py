"""
Response assembly layer for the draft career projection system: the single
function server.py's /api/draft-projection route calls. Combines

  - comp_engine's top historical comps (+ explanations)
  - predict.py's tier probabilities (comp-based today, ML-blended once
    career_projection_model.pkl exists) and Expected/Floor/Ceiling outcome
  - archetype_adapter's college archetype mix
  - explain.py's strengths/weaknesses/development/risk indicators

into one dict, with an explicit `data_quality` block so callers (the API
response, the frontend) can honestly represent how much real signal backed
a given projection rather than presenting a placeholder-built number with
false confidence.
"""
from __future__ import annotations

import logging
from typing import Optional

from draft_projection.archetype_adapter import archetype_match_strength, compute_archetype_mix, get_shot_creation_data
from draft_projection.comp_engine import HistoricalPool, find_top_comps, explain_comp
from draft_projection.explain import build_explainability
from draft_projection.features import FEATURE_NAMES, build_feature_vector
from draft_projection.labels import TIERS, TIER_LABEL
from draft_projection.predict import (
    blend_tier_probabilities, comp_based_tier_probabilities, expected_floor_ceiling,
    load_trained_hierarchical_model, load_trained_model, ml_tier_probabilities,
    ml_tier_probabilities_hierarchical,
)

log = logging.getLogger("draft_projection.service")

TOP_N_COMPS_FOR_POOL_SCORING = 50  # comp-based probability estimator wants a wide pool
TOP_N_COMPS_TO_RETURN = 10         # final displayed comps
TOP_N_FOR_ARCH_BLEND = 30          # compute arch_match on this many before re-sorting

ARCH_BLEND_WEIGHT = 0.40           # weight of arch_match in final sim; rest goes to raw comp sim

# Nickname / alias → canonical name as used in archive_cbb_player_stats.
# Keys are lowercased; values must match the exact CBB name (also lowercased at lookup).
# Covers: legal-name vs. nickname, Jr./Sr. suffix mismatches, common misspellings.
_NICKNAME_MAP: dict[str, str] = {
    # Nickname → legal first name
    "ace bailey":          "airious bailey",
    "ace bailey jr.":      "airious bailey",   # typed with suffix
    "airious bailey jr.":  "airious bailey",   # suffix not in CBB
    "cam boozer":          "cameron boozer",
    "cam carr":            "cameron carr",
    "ced coward":          "cedric coward",

    # Jr. present in CBB but omitted by user
    "mikel brown":         "mikel brown jr.",

    # Jr. absent in CBB but user types it
    "labaron philon jr.":  "labaron philon",
    "derik queen jr.":     "derik queen",
    "kevin zabo jr.":      "kevin zabo",
    "michael ajayi jr.":   "michael ajayi",

    # Nickname → full legal name in CBB
    "nate bittle":         "nathan bittle",

    # Common misspelling
    "isaiah harwell":      "isiah harwell",
}

# Draft context for players already picked in previous classes — they're not in
# archive_draft_prospects_2026 so age/pick/college would all be None without this.
# Add a row whenever a queryable player was drafted in a past class.
_KNOWN_PICKS: dict[str, dict] = {
    # 2025 NBA Draft
    "cooper flagg":    {"overall_pick": 1.0,  "age_at_draft": 19.0, "college": "Duke"},
    "dylan harper":    {"overall_pick": 2.0,  "age_at_draft": 19.0, "college": "Rutgers"},
    "airious bailey":  {"overall_pick": 3.0,  "age_at_draft": 19.0, "college": "Rutgers"},
    "vj edgecombe":    {"overall_pick": 5.0,  "age_at_draft": 20.0, "college": "Baylor"},
    "derik queen":     {"overall_pick": 4.0,  "age_at_draft": 20.0, "college": "Maryland"},
    "kon knueppel":    {"overall_pick": 6.0,  "age_at_draft": 21.0, "college": "Duke"},
    "khaman maluach":  {"overall_pick": 9.0,  "age_at_draft": 19.0, "college": "Duke"},
    # 2026 NBA Draft
    "cameron carr":    {"overall_pick": 24.0, "age_at_draft": 21.0, "college": "Baylor"},
    "caleb wilson":    {"overall_pick": 4.0,  "age_at_draft": 19.0, "college": "North Carolina"},
}

USE_HIERARCHICAL_MODEL = True

_model_bundle_cache = {"loaded": False, "bundle": None}


def _get_model_bundle() -> Optional[dict]:
    """Cached per-process -- checked once, not on every request. Re-running
    the server picks up a newly-trained model file; this isn't a hot reload,
    matching how other model .pkl files are loaded elsewhere in this repo."""
    if not _model_bundle_cache["loaded"]:
        if USE_HIERARCHICAL_MODEL:
            _model_bundle_cache["bundle"] = load_trained_hierarchical_model()
        else:
            _model_bundle_cache["bundle"] = load_trained_model()
        _model_bundle_cache["loaded"] = True
    return _model_bundle_cache["bundle"]


def _ml_tier_probabilities_any(model_bundle: Optional[dict], row: dict) -> Optional[dict]:
    if model_bundle is None:
        return None
    if model_bundle.get("model_type") == "hierarchical":
        return ml_tier_probabilities_hierarchical(model_bundle, row)
    return ml_tier_probabilities(model_bundle, row)


def _model_feature_names(model_bundle: dict) -> list:
    if model_bundle.get("model_type") == "hierarchical":
        return model_bundle["stage1_features"]
    return model_bundle["features"]


def build_draft_projection(conn, q, pool: HistoricalPool, *, player_name: str,
                            college: Optional[str] = None, age_at_draft: Optional[float] = None,
                            overall_pick: Optional[float] = None) -> dict:
    # Resolve nickname → official name before any DB lookups
    player_name = _NICKNAME_MAP.get(player_name.lower(), player_name)

    # Fill in draft context for players from past classes not in archive_draft_prospects_2026
    known = _KNOWN_PICKS.get(player_name.lower(), {})
    if age_at_draft is None and "age_at_draft" in known:
        age_at_draft = known["age_at_draft"]
    if overall_pick is None and "overall_pick" in known:
        overall_pick = known["overall_pick"]
    if college is None and "college" in known:
        college = known["college"]

    fv = build_feature_vector(
        conn, q, player_name=player_name, college=college,
        age_at_draft=age_at_draft, overall_pick=overall_pick,
    )

    all_comps = find_top_comps(
        conn, q, pool, player_name=player_name, college=college,
        age_at_draft=age_at_draft, overall_pick=overall_pick, top_n=TOP_N_COMPS_FOR_POOL_SCORING,
    )
    top_comps = all_comps[:TOP_N_COMPS_TO_RETURN]

    comp_probs = comp_based_tier_probabilities(all_comps)
    model_bundle = _get_model_bundle()
    ml_probs = None
    if model_bundle is not None:
        row = fv.as_row()
        if all(c in row for c in _model_feature_names(model_bundle)):
            ml_probs = _ml_tier_probabilities_any(model_bundle, row)
        else:
            log.warning("Trained model's feature schema doesn't match current FEATURE_NAMES -- "
                        "skipping ML layer until retrained.")
    final_probs = blend_tier_probabilities(comp_probs, ml_probs)
    outcome_summary = expected_floor_ceiling(final_probs)

    prospect_mix = compute_archetype_mix(conn, q, player_name=player_name)
    shot_creation = get_shot_creation_data(conn, q, player_name=player_name)

    # Compute arch_match for a wider candidate pool, then re-sort by a blended score
    # so role-similar players (e.g. Mobley for Flagg) rank above pick-slot-only matches.
    arch_candidates = all_comps[:TOP_N_FOR_ARCH_BLEND]
    candidates_with_arch = []
    for c in arch_candidates:
        comp_mix = compute_archetype_mix(conn, q, player_name=c["player"])
        am = archetype_match_strength(prospect_mix, comp_mix)
        blended = (1 - ARCH_BLEND_WEIGHT) * c["similarity"] + ARCH_BLEND_WEIGHT * am if am is not None else c["similarity"]
        candidates_with_arch.append((blended, c, am))
    candidates_with_arch.sort(key=lambda x: -x[0])

    comparables = []
    for blended_sim, c, am in candidates_with_arch[:TOP_N_COMPS_TO_RETURN]:
        comparables.append({
            "player": c["player"],
            "draft_season": c["draft_season"],
            "overall_pick": c["overall_pick"],
            "college": c["college"],
            "similarity": round(blended_sim, 1),
            "actual_outcome": c["tier_label"],
            "archetype_match": am,
            "explanation": explain_comp(player_name, c),
        })

    n_missing = sum(1 for v in fv.missing.values() if v)
    college_data_available = not fv.missing.get("pts_per40", True)
    confidence_note = (
        "Full college statistical profile available; this projection reflects production, "
        "efficiency, and role signal, not just physical/draft context."
        if college_data_available else
        "No college statistical data is loaded for this prospect yet -- this projection is "
        "currently based on physical profile, age, and draft context only. Treat it as "
        "low-confidence until archive_ncaa_player_stats has real data and the model is retrained."
    )

    return {
        "prospect": {
            "name": player_name, "college": college,
            "age_at_draft": age_at_draft, "overall_pick": overall_pick,
        },
        "outcome_probabilities": {TIER_LABEL[t]: round(final_probs.get(t, 0.0), 4) for t in TIERS},
        **outcome_summary,
        "comparables": comparables,
        "archetype": {
            "mix": prospect_mix,
            "available": prospect_mix is not None,
            "shot_creation": shot_creation,
        },
        "explainability": build_explainability(fv),
        "data_quality": {
            "ml_model_loaded": model_bundle is not None,
            "college_data_available": college_data_available,
            "missing_feature_count": n_missing,
            "total_feature_count": len(FEATURE_NAMES),
            "confidence_note": confidence_note,
        },
    }
