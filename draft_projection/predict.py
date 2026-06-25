"""
Tier-probability prediction and Expected/Floor/Ceiling derivation for the
draft career projection system.

Two probability sources, designed to blend:

1. `comp_based_tier_probabilities` -- a similarity-weighted vote across a
   prospect's top historical comps (a kNN-style estimator). Works today,
   with zero college data, off whatever signal comp_engine actually has
   (currently physical profile + capped draft context -- see comp_engine.py).
2. `ml_tier_probabilities` -- loads a trained XGBoost model (saved by
   train_career_projection.py) and predicts directly from the feature
   vector. Returns None when no trained model file exists yet, which is the
   current state: deliberately NOT trained until archive_ncaa_player_stats
   has real data (see draft_projection package docstring / project notes).

`blend_tier_probabilities` is the seam joining them -- once a real model
exists, prediction quality improves without any caller (service.py, the API
route) needing to change.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import numpy as np

from draft_projection.labels import TIER_RANK, TIERS, TIER_LABEL

log = logging.getLogger("draft_projection.predict")

MODEL_PATH = os.path.join(os.path.dirname(__file__), "..", "career_projection_model.pkl")
HIERARCHICAL_MODEL_PATH = os.path.join(os.path.dirname(__file__), "..", "career_projection_model_hierarchical.pkl")

# Comp-based estimator: weight by similarity^POWER so the most-similar comps
# dominate the vote rather than every one of the top_n comps counting almost
# equally -- a flat average across e.g. 50 comps would wash out the signal
# from the handful that actually look like the prospect.
SIMILARITY_WEIGHT_POWER = 3.0
MIN_SIMILARITY_TO_COUNT = 1.0  # comps at ~0% similarity contribute noise, not signal

# Floor = realistic downside (~15th percentile of the outcome distribution).
# Ceiling = realistic upside (~85th percentile). Not min/max -- those would
# be dominated by single-comp noise in a thin pool.
FLOOR_PERCENTILE = 0.15
CEILING_PERCENTILE = 0.85


def comp_based_tier_probabilities(comps: list[dict]) -> dict:
    """Similarity-weighted vote across a prospect's historical comps' actual
    tier outcomes. Returns a dict of tier -> probability (sums to 1.0), or a
    uniform distribution if no comp has usable similarity (e.g. an entirely
    unknown prospect with no pool to compare against)."""
    weights = {t: 0.0 for t in TIERS}
    total = 0.0
    for c in comps:
        sim = c.get("similarity") or 0.0
        if sim < MIN_SIMILARITY_TO_COUNT:
            continue
        w = (sim / 100.0) ** SIMILARITY_WEIGHT_POWER
        weights[c["tier"]] += w
        total += w

    if total <= 0:
        uniform = 1.0 / len(TIERS)
        return {t: uniform for t in TIERS}
    return {t: w / total for t, w in weights.items()}


def load_trained_model() -> Optional[dict]:
    """Returns the joblib-saved {"model", "features", "tiers"} bundle, or
    None if career_projection_model.pkl doesn't exist yet -- which is the
    current, deliberate state (see module docstring). Callers must treat
    None as "ML layer unavailable", not an error."""
    if not os.path.exists(MODEL_PATH):
        return None
    import joblib
    try:
        return joblib.load(MODEL_PATH)
    except Exception as exc:
        log.warning("Failed to load %s: %s", MODEL_PATH, exc)
        return None


def ml_tier_probabilities(model_bundle: Optional[dict], feature_row: dict) -> Optional[dict]:
    """feature_row must have every key in model_bundle['features']. Returns
    tier -> probability, or None if no model is loaded."""
    if model_bundle is None:
        return None
    x = np.array([[feature_row[c] for c in model_bundle["features"]]])
    probs = model_bundle["model"].predict_proba(x)[0]
    tiers = model_bundle["tiers"]
    return {t: float(p) for t, p in zip(tiers, probs)}


def load_trained_hierarchical_model() -> Optional[dict]:
    if not os.path.exists(HIERARCHICAL_MODEL_PATH):
        return None
    import joblib
    try:
        return joblib.load(HIERARCHICAL_MODEL_PATH)
    except Exception as exc:
        log.warning("Failed to load %s: %s", HIERARCHICAL_MODEL_PATH, exc)
        return None


def ml_tier_probabilities_hierarchical(model_bundle: Optional[dict], feature_row: dict) -> Optional[dict]:
    """Soft composition: P(tier=k) = P_stage1(non-bust) * P_stage2(tier=k|non-bust)."""
    if model_bundle is None:
        return None
    x1 = np.array([[feature_row[c] for c in model_bundle["stage1_features"]]])
    x2 = np.array([[feature_row[c] for c in model_bundle["stage2_features"]]])
    p_nonbust = float(model_bundle["stage1_model"].predict_proba(x1)[0][1])
    p_bust = 1.0 - p_nonbust
    p_stage2 = model_bundle["stage2_model"].predict_proba(x2)[0]
    tiers = model_bundle["tiers"]
    out = {tiers[0]: p_bust}
    for t, p in zip(tiers[1:], p_stage2):
        out[t] = p_nonbust * float(p)
    return out


def blend_tier_probabilities(comp_probs: dict, ml_probs: Optional[dict], ml_weight: float = 0.7) -> dict:
    """If no ML model is available, comp-based probabilities are returned
    unchanged -- the system is fully functional pre-training, just less
    accurate. Once a model exists, it dominates the blend (ml_weight=0.7)
    but the comp engine still contributes, since it encodes real historical
    outcomes a pure feature-based model can't see (e.g. an unusual
    statistical profile that nonetheless resembles a specific real career)."""
    if ml_probs is None:
        return dict(comp_probs)
    return {
        t: ml_weight * ml_probs.get(t, 0.0) + (1 - ml_weight) * comp_probs.get(t, 0.0)
        for t in TIERS
    }


def _percentile_tier(sorted_probs: list[tuple], target: float) -> str:
    """sorted_probs: [(tier, prob), ...] ordered by TIER_RANK ascending
    (bust -> superstar). Walks the cumulative distribution bottom-up and
    returns the first tier where cumulative probability reaches `target`."""
    cum = 0.0
    for tier, prob in sorted_probs:
        cum += prob
        if cum >= target:
            return tier
    return sorted_probs[-1][0] if sorted_probs else TIERS[0]


def expected_floor_ceiling(tier_probs: dict) -> dict:
    """Expected = probability-weighted mean tier rank, rounded to the
    nearest real tier (a statistical center of mass, not just the mode --
    so a prospect split evenly between Starter and All-Star reads as
    High-Level Starter, not whichever of the two has one more vote).
    Floor/Ceiling = ~15th/85th percentile of the same distribution -- a
    realistic downside/upside band, not literal min/max outcomes."""
    sorted_probs = sorted(tier_probs.items(), key=lambda kv: TIER_RANK[kv[0]])

    mean_rank = sum(TIER_RANK[t] * p for t, p in tier_probs.items())
    expected_tier = TIERS[int(round(min(max(mean_rank, 0), len(TIERS) - 1)))]

    floor_tier = _percentile_tier(sorted_probs, FLOOR_PERCENTILE)
    ceiling_tier = _percentile_tier(sorted_probs, CEILING_PERCENTILE)

    return {
        "expected_outcome": TIER_LABEL[expected_tier],
        "expected_outcome_tier": expected_tier,
        "floor_outcome": TIER_LABEL[floor_tier],
        "floor_outcome_tier": floor_tier,
        "ceiling_outcome": TIER_LABEL[ceiling_tier],
        "ceiling_outcome_tier": ceiling_tier,
    }
