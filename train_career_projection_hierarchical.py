#!/usr/bin/env python3
"""
EXPERIMENT: two-stage hierarchical version of the career projection model.

Stage 1 (binary): Bust vs. Non-Bust, trained on all 8,208 labeled players
(74.3%/25.7% split -- still imbalanced, but nowhere near as lopsided as the
8-way problem where Bust drowns out 7 other classes at once).

Stage 2 (7-way multiclass): tier among Non-Bust players only (End-of-Bench
through Superstar), trained on just the 2,112 non-Bust players. Within this
subset the largest class (Rotation, 834) is 39.5% -- a far more tractable
class balance than Bust's 74% share of the full population.

Composition is SOFT, not a hard gate: P(tier=k) = P_stage1(non-bust) *
P_stage2(tier=k | non-bust) for every non-Bust tier, P(bust) =
P_stage1(bust). A player stage 1 only gives a modest non-bust chance can
still carry a meaningfully elevated probability for a specific tier if
stage 2 is confident -- there's no point where a player is irreversibly
routed away from consideration before stage 2 runs.

Known, expected limitation (not a bug): this targets the "real contributor,
but which tier" confusion. It does NOT fix the "is this person a bust at
all" gate for players with literally no data (e.g. Steve Nash, Mark Price)
-- stage 1 is trained on the same confounded missing-data signal as the
single-model version. See draft_projection/experiments/ for the full
diagnostic history this design responds to.

Saved as career_projection_model_hierarchical.pkl -- NOT the production
model (career_projection_model.pkl is untouched). Run this script any time
to retrain; it never overwrites anything else.
"""
import logging

import joblib
import numpy as np
from sklearn.metrics import classification_report, confusion_matrix, accuracy_score, f1_score
from sklearn.model_selection import train_test_split
from xgboost import XGBClassifier

from draft_projection.comp_engine import build_historical_pool
from draft_projection.features import FEATURE_NAMES
from draft_projection.labels import TIERS
from server import connect, q

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("train_career_projection_hierarchical")

MODEL_OUT = "career_projection_model_hierarchical.pkl"
FEATURE_COLS = FEATURE_NAMES + [f"{n}_missing" for n in FEATURE_NAMES]

# Same hyperparameters as the production single-model script, for both
# stages -- isolating the architecture change from any retuning, per this
# project's "change one variable at a time" discipline this session.
XGB_PARAMS = dict(
    n_estimators=400, max_depth=5, learning_rate=0.05, subsample=0.8,
    colsample_bytree=0.7, min_child_weight=5, reg_alpha=0.1, reg_lambda=1.0,
    random_state=42, n_jobs=-1, verbosity=0,
)


def main() -> None:
    conn = connect()
    try:
        pool = build_historical_pool(conn, q, current_season=2026)
    finally:
        conn.close()

    if not pool:
        log.error("Historical pool is empty -- nothing to train on.")
        return

    rows, tiers = [], []
    for m in pool.members:
        row = dict(m.raw_row)
        for name in FEATURE_NAMES:
            row[f"{name}_missing"] = 1.0 if m.missing.get(name, True) else 0.0
        rows.append(row)
        tiers.append(TIERS.index(m.tier))

    X = np.array([[row[c] for c in FEATURE_COLS] for row in rows])
    y = np.array(tiers)
    n = len(pool.members)
    indices = np.arange(n)

    idx_train, idx_test = train_test_split(indices, test_size=0.15, random_state=42, stratify=y)
    X_train, X_test = X[idx_train], X[idx_test]
    y_train, y_test = y[idx_train], y[idx_test]

    BUST_IDX = TIERS.index("bust")
    y_train_bust = (y_train != BUST_IDX).astype(int)  # 1 = non-bust (the positive class)

    log.info("Stage 1 (Bust vs Non-Bust) training distribution: %d bust, %d non-bust",
              (y_train_bust == 0).sum(), (y_train_bust == 1).sum())

    stage1 = XGBClassifier(objective="binary:logistic", **XGB_PARAMS)
    stage1.fit(X_train, y_train_bust)

    nonbust_train_mask = y_train != BUST_IDX
    X_train_s2 = X_train[nonbust_train_mask]
    y_train_s2 = y_train[nonbust_train_mask] - 1  # shift down: bust(0) removed, end_of_bench..superstar -> 0..6
    STAGE2_TIERS = TIERS[1:]

    log.info("Stage 2 (tier | Non-Bust) training distribution: %s",
              {STAGE2_TIERS[i]: int((y_train_s2 == i).sum()) for i in range(len(STAGE2_TIERS))})

    stage2 = XGBClassifier(objective="multi:softprob", num_class=len(STAGE2_TIERS), **XGB_PARAMS)
    stage2.fit(X_train_s2, y_train_s2)

    # ── Compose into a full 8-tier distribution for evaluation ──────────────
    p_nonbust = stage1.predict_proba(X_test)[:, 1]
    p_bust = 1.0 - p_nonbust
    p_stage2 = stage2.predict_proba(X_test)  # (n, 7)

    full_proba = np.zeros((len(X_test), len(TIERS)))
    full_proba[:, BUST_IDX] = p_bust
    full_proba[:, 1:] = p_stage2 * p_nonbust[:, None]

    y_pred = np.argmax(full_proba, axis=1)

    acc = accuracy_score(y_test, y_pred)
    macro_f1 = f1_score(y_test, y_pred, average="macro", labels=list(range(len(TIERS))), zero_division=0)
    weighted_f1 = f1_score(y_test, y_pred, average="weighted", labels=list(range(len(TIERS))), zero_division=0)
    log.info("Composed accuracy: %.4f  Macro F1: %.4f  Weighted F1: %.4f", acc, macro_f1, weighted_f1)
    log.info("Per-class precision/recall (held-out 15%%):\n%s",
              classification_report(y_test, y_pred, target_names=TIERS, zero_division=0))

    cm = confusion_matrix(y_test, y_pred, labels=list(range(len(TIERS))))
    log.info("Confusion matrix (rows=actual, cols=predicted), order %s:\n%s", TIERS, cm)

    joblib.dump({
        "model_type": "hierarchical",
        "stage1_model": stage1,
        "stage2_model": stage2,
        "stage1_features": FEATURE_COLS,
        "stage2_features": FEATURE_COLS,
        "tiers": TIERS,
    }, MODEL_OUT)
    log.info("Saved -> %s (experiment only, production model untouched)", MODEL_OUT)


if __name__ == "__main__":
    main()
