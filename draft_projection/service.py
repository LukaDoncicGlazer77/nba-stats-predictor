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

from draft_projection.archetype_adapter import archetype_match_strength, compute_archetype_mix
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
TOP_N_COMPS_TO_RETURN = 10         # but the API/UI only show the closest 10

# Flip to True to serve the experimental two-stage hierarchical model
# (career_projection_model_hierarchical.pkl) instead of the single 8-way
# model -- see train_career_projection_hierarchical.py's module docstring
# for what it does differently and its known limitations. False is the
# default/production behavior; this is a one-line, fully reversible switch
# specifically so trying it doesn't require touching anything else.
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
    """Dispatches to the right prediction function based on the bundle's
    own model_type marker, so build_draft_projection() doesn't need to know
    which architecture is currently loaded."""
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
    comparables = []
    for c in top_comps:
        comp_mix = compute_archetype_mix(conn, q, player_name=c["player"])
        comparables.append({
            "player": c["player"],
            "draft_season": c["draft_season"],
            "overall_pick": c["overall_pick"],
            "college": c["college"],
            "similarity": c["similarity"],
            "actual_outcome": c["tier_label"],
            "archetype_match": archetype_match_strength(prospect_mix, comp_mix),
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
