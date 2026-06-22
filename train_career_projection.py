#!/usr/bin/env python3
"""
Trains the NBA Draft Career Projection multi-class model: given a prospect's
feature vector (physical profile, age, college production/efficiency/role
when available, capped draft context), predicts probabilities across the 8
career-outcome tiers (Bust .. Superstar) defined in draft_projection.labels.

Deliberate deviation from train_stats_model.py/train_salary_model.py: those
scripts train against the local nba.db SQLite snapshot. This script reads
directly from production Postgres instead, because archive_ncaa_player_stats
and archive_draft_career_labels are actively iterated on (re-scraped,
re-labeled) right now -- training against a stale local snapshot of those
specific two tables would be a real bug class. The stable, rarely-changing
NBA-side tables (advanced/per_game/etc.) are queried the same way either
script would.

Run: python train_career_projection.py
Saves: career_projection_model.pkl
"""
import logging

import joblib
import numpy as np
from sklearn.metrics import classification_report
from sklearn.model_selection import train_test_split
from xgboost import XGBClassifier

from draft_projection.comp_engine import build_historical_pool
from draft_projection.features import FEATURE_NAMES
from draft_projection.labels import TIERS
from server import connect, q

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("train_career_projection")

MODEL_OUT = "career_projection_model.pkl"
FEATURE_COLS = FEATURE_NAMES + [f"{n}_missing" for n in FEATURE_NAMES]


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

    n_with_college_production = sum(
        1 for m in pool.members if not m.missing.get("pts_per40", True)
    )
    log.info(
        "%d historical players. %d (%.1f%%) have real college production data; the rest train "
        "on physical profile + age + capped draft context only (college features default to 0 "
        "with their _missing flag set to 1, which the model can learn to discount). Re-run this "
        "script after the NCAA scraper has been run and load_ncaa_stats.py loaded its output --"
        " that's a one-command retrain, not a refactor.",
        len(pool), n_with_college_production,
        100 * n_with_college_production / len(pool) if len(pool) else 0,
    )

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.15, random_state=42, stratify=y
    )

    model = XGBClassifier(
        objective="multi:softprob",
        num_class=len(TIERS),
        n_estimators=400,
        max_depth=5,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.7,
        min_child_weight=5,
        reg_alpha=0.1,
        reg_lambda=1.0,
        random_state=42,
        n_jobs=-1,
        verbosity=0,
    )
    model.fit(X_train, y_train)

    y_pred = model.predict(X_test)
    log.info("Per-class precision/recall (held-out 15%%):\n%s",
              classification_report(y_test, y_pred, target_names=TIERS, zero_division=0))

    # Verify draft position isn't dominating, per design direction -- log
    # feature importance so this is checked, not assumed.
    importances = sorted(zip(FEATURE_COLS, model.feature_importances_), key=lambda t: -t[1])
    log.info("Top 10 features by gain importance:\n%s",
              "\n".join(f"  {name:<28} {imp:.4f}" for name, imp in importances[:10]))
    draft_rank = next((i for i, (name, _) in enumerate(importances) if name == "draft_slot_tier"), None)
    draft_importance = next((imp for name, imp in importances if name == "draft_slot_tier"), 0.0)
    log.info(
        "draft_slot_tier ranks #%s of %d features by importance (%.4f) -- %s",
        draft_rank + 1 if draft_rank is not None else "?", len(FEATURE_COLS), draft_importance,
        "OK, not dominating" if (draft_rank or 0) > 2 else "WARNING: unexpectedly high, investigate before shipping",
    )

    joblib.dump({
        "model": model,
        "features": FEATURE_COLS,
        "tiers": TIERS,
    }, MODEL_OUT)
    log.info("Saved -> %s", MODEL_OUT)


if __name__ == "__main__":
    main()
