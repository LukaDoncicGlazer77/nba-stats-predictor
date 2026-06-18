#!/usr/bin/env python3
"""
Train XGBoost models to predict next-season per-game stats.
Run: python train_stats_model.py
Saves: stats_model.pkl
"""
import os, sqlite3, numpy as np, pandas as pd, joblib
from xgboost import XGBRegressor
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_absolute_error

ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT, "nba.db")
MODEL_OUT = os.path.join(ROOT, "stats_model.pkl")

TARGETS = ["pts_per_game", "trb_per_game", "ast_per_game",
           "stl_per_game", "blk_per_game", "fg_percent",
           "x3p_per_game", "ts_percent", "per", "vorp", "ws"]

POSITIONS = ["C", "PF", "PG", "SF", "SG"]

# ── 1. Load data ──────────────────────────────────────────────────────────────
print("Loading data from nba.db…")
conn = sqlite3.connect(DB_PATH)

pg = pd.read_sql_query("""
    SELECT player_id, player, season, pos, age, g, mp_per_game,
           pts_per_game, trb_per_game, ast_per_game, stl_per_game, blk_per_game,
           tov_per_game, fg_percent, x3p_per_game, ft_percent,
           fga_per_game, x3pa_per_game
    FROM archive_player_per_game
    WHERE team IN ('TOT','2TM','3TM')
       OR player NOT IN (
           SELECT player FROM archive_player_per_game p2
           WHERE p2.season = archive_player_per_game.season
           GROUP BY p2.player HAVING COUNT(*) > 1
       )
""", conn)

adv = pd.read_sql_query("""
    SELECT player_id, season, per, ts_percent, usg_percent,
           ows, dws, ws, ws_48, obpm, dbpm, bpm, vorp
    FROM archive_advanced
    WHERE team IN ('TOT','2TM','3TM')
       OR player NOT IN (
           SELECT player FROM archive_advanced a2
           WHERE a2.season = archive_advanced.season
           GROUP BY a2.player HAVING COUNT(*) > 1
       )
""", conn)
conn.close()

# Numeric conversion
for col in pg.columns:
    if col not in ("player_id", "player", "season", "pos"):
        pg[col] = pd.to_numeric(pg[col], errors="coerce")
for col in adv.columns:
    if col not in ("player_id", "season"):
        adv[col] = pd.to_numeric(adv[col], errors="coerce")

pg["season"] = pd.to_numeric(pg["season"], errors="coerce")
adv["season"] = pd.to_numeric(adv["season"], errors="coerce")

stats = pd.merge(pg, adv, on=["player_id", "season"], how="inner")
print(f"  {len(stats)} player-season rows")

# ── 2. Build lag features ─────────────────────────────────────────────────────
# Sort so we can shift within each player
stats = stats.sort_values(["player_id", "season"]).reset_index(drop=True)

LAG_COLS = ["pts_per_game", "trb_per_game", "ast_per_game", "stl_per_game",
            "blk_per_game", "tov_per_game", "fg_percent", "x3p_per_game",
            "ft_percent", "mp_per_game", "g", "fga_per_game", "x3pa_per_game",
            "per", "ts_percent", "usg_percent", "ws", "ws_48", "bpm", "vorp",
            "obpm", "dbpm", "ows", "dws"]

for col in LAG_COLS:
    stats[f"lag1_{col}"] = stats.groupby("player_id")[col].shift(1)
    stats[f"lag2_{col}"] = stats.groupby("player_id")[col].shift(2)
    stats[f"delta_{col}"] = stats[f"lag1_{col}"] - stats[f"lag2_{col}"]

# Age of the PREVIOUS season (feature), current season is what we're predicting for
stats["prev_age"] = stats.groupby("player_id")["age"].shift(1)

# Position one-hot (from current row)
stats["pos_primary"] = stats["pos"].str.split("-").str[0].str.strip()
stats["pos_primary"] = stats["pos_primary"].where(stats["pos_primary"].isin(POSITIONS), "SF")
for pos in POSITIONS:
    stats[f"Pos_{pos}"] = (stats["pos_primary"] == pos).astype(float)

# ── 3. Feature columns ────────────────────────────────────────────────────────
FEATURE_COLS = (
    ["prev_age"] +
    [f"lag1_{c}" for c in LAG_COLS] +
    [f"lag2_{c}" for c in LAG_COLS] +
    [f"delta_{c}" for c in LAG_COLS] +
    [f"Pos_{p}" for p in POSITIONS]
)

# Drop rows where lag1 is missing (first season per player)
df = stats.dropna(subset=["prev_age"] + [f"lag1_{c}" for c in LAG_COLS[:5]])
df = df.copy()
df[FEATURE_COLS] = df[FEATURE_COLS].fillna(0)

print(f"  {len(df)} training samples, {len(FEATURE_COLS)} features")

# ── 4. Train one XGBoost per target ──────────────────────────────────────────
models = {}
for target in TARGETS:
    sub = df.dropna(subset=[target])
    X = sub[FEATURE_COLS].values
    y = sub[target].values

    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.15, random_state=42)

    xgb = XGBRegressor(
        n_estimators=300,
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
    xgb.fit(X_train, y_train)
    mae = mean_absolute_error(y_test, xgb.predict(X_test))
    print(f"  {target:<22} MAE={mae:.3f}")
    models[target] = xgb

# ── 5. Save ───────────────────────────────────────────────────────────────────
joblib.dump({
    "models": models,
    "features": FEATURE_COLS,
    "lag_cols": LAG_COLS,
    "positions": POSITIONS,
    "targets": TARGETS,
}, MODEL_OUT)
print(f"\nSaved → {MODEL_OUT}")
