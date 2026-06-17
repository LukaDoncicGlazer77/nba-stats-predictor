#!/usr/bin/env python3
"""
Train the StatFuel salary prediction model.
Run once: python train_salary_model.py
Saves: salary_model.pkl  (used by server.py at runtime)
"""
import os
import re
import sqlite3
import numpy as np
import pandas as pd
import joblib
from sklearn.ensemble import RandomForestRegressor
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_squared_error

ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT, "nba.db")
SALARY_CSV = os.path.join(ROOT, "..", "salary predicter", "archive (3)", "NBA Salaries(1990-2023).csv")
SALARY_CSV_2526 = os.path.join(ROOT, "..", "salary predicter", "nba_2025_26_salaries.csv")
MODEL_OUT = os.path.join(ROOT, "salary_model.pkl")

# Salary cap in MILLIONS, keyed by season START year (e.g. 2022 = 2022-23 season)
SALARY_CAPS_M = {
    1990: 11.87, 1991: 12.50, 1992: 14.00, 1993: 15.175, 1994: 15.964,
    1995: 15.964, 1996: 23.16, 1997: 26.90, 1998: 26.90, 1999: 14.00,
    2000: 19.00, 2001: 35.50, 2002: 40.27, 2003: 40.27, 2004: 43.87,
    2005: 43.87, 2006: 49.50, 2007: 53.135, 2008: 55.63, 2009: 57.70,
    2010: 57.70, 2011: 58.044, 2012: 58.044, 2013: 58.679, 2014: 63.065,
    2015: 70.00, 2016: 94.143, 2017: 99.093, 2018: 101.869, 2019: 109.14,
    2020: 109.14, 2021: 112.414, 2022: 123.655, 2023: 136.021, 2024: 140.588,
    2025: 155.00,
}

POSITIONS = ["C", "PF", "PG", "SF", "SG"]


def clean_name(name):
    name = str(name).strip()
    name = re.sub(r'\s+(Jr\.?|Sr\.?|II|III|IV|V)$', '', name, flags=re.IGNORECASE)
    # normalise accented chars
    import unicodedata
    name = unicodedata.normalize("NFD", name)
    name = "".join(c for c in name if unicodedata.category(c) != "Mn")
    return name.lower()


def parse_salary(s):
    if pd.isna(s):
        return None
    s = str(s).replace("$", "").replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def safe_float(v, default=0.0):
    try:
        f = float(v)
        return f if not np.isnan(f) else default
    except (TypeError, ValueError):
        return default


# ── 1. Load salary data ─────────────────────────────────────────────────────
print("Loading salary CSV…")
sal = pd.read_csv(SALARY_CSV)
sal.columns = ["idx", "playerName", "seasonStartYear", "salary", "inflationSalary"]
sal["salary_m"] = sal["salary"].apply(parse_salary) / 1_000_000
sal["seasonStartYear"] = pd.to_numeric(sal["seasonStartYear"], errors="coerce")
sal = sal.dropna(subset=["salary_m", "seasonStartYear"])
sal = sal[sal["salary_m"] > 0]
sal["cap_m"] = sal["seasonStartYear"].map(SALARY_CAPS_M)
sal = sal.dropna(subset=["cap_m"])
sal["salary_pct"] = sal["salary_m"] / sal["cap_m"]
# DB season = seasonStartYear + 1
sal["season"] = (sal["seasonStartYear"] + 1).astype(int).astype(str)
sal["name_key"] = sal["playerName"].apply(clean_name)
print(f"  {len(sal)} historical salary rows loaded")

# ── 1b. Add 2025-26 salary data ──────────────────────────────────────────────
print("Loading 2025-26 salary CSV…")
sal2526 = pd.read_csv(SALARY_CSV_2526, skiprows=1)
sal2526 = sal2526[["Player", "2025-26"]].copy()
sal2526.columns = ["playerName", "salary_str"]
sal2526 = sal2526.dropna(subset=["salary_str"])
sal2526 = sal2526[sal2526["salary_str"].str.strip() != ""]
sal2526["salary_m"] = sal2526["salary_str"].apply(parse_salary) / 1_000_000
sal2526 = sal2526[sal2526["salary_m"] > 0]
sal2526["seasonStartYear"] = 2025
sal2526["cap_m"] = SALARY_CAPS_M[2025]  # $155M
sal2526["salary_pct"] = sal2526["salary_m"] / sal2526["cap_m"]
sal2526["season"] = "2026"  # DB season = seasonStartYear + 1
sal2526["name_key"] = sal2526["playerName"].apply(clean_name)
sal2526 = sal2526[["name_key", "season", "salary_pct"]]
print(f"  {len(sal2526)} 2025-26 salary rows loaded")

sal = pd.concat([sal[["name_key", "season", "salary_pct"]], sal2526], ignore_index=True)

# ── 2. Load per-game stats from SQLite ───────────────────────────────────────
print("Loading per-game stats from DB…")
conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

pg = pd.read_sql_query("""
    SELECT player, season, pos, age, g, gs, mp_per_game,
           fg_per_game, fga_per_game, fg_percent,
           x3p_per_game, x3pa_per_game, x3p_percent,
           ft_per_game, fta_per_game, ft_percent,
           orb_per_game, drb_per_game, trb_per_game,
           ast_per_game, stl_per_game, blk_per_game, tov_per_game, pts_per_game
    FROM archive_player_per_game
    WHERE team IN ('TOT','2TM','3TM')
       OR player NOT IN (
           SELECT player FROM archive_player_per_game p2
           WHERE p2.season = archive_player_per_game.season
           GROUP BY p2.player HAVING COUNT(*) > 1
       )
""", conn)

adv = pd.read_sql_query("""
    SELECT player, season, per, ts_percent, usg_percent,
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
print(f"  {len(pg)} per-game rows, {len(adv)} advanced rows")

# Convert all numeric cols
num_pg = [c for c in pg.columns if c not in ("player", "season", "pos")]
for c in num_pg:
    pg[c] = pd.to_numeric(pg[c], errors="coerce")

num_adv = [c for c in adv.columns if c not in ("player", "season")]
for c in num_adv:
    adv[c] = pd.to_numeric(adv[c], errors="coerce")

# ── 3. Merge stats ────────────────────────────────────────────────────────────
stats = pd.merge(pg, adv, on=["player", "season"], how="inner")
stats["name_key"] = stats["player"].apply(clean_name)
print(f"  {len(stats)} stat rows after merge")

# ── 4. Join with salary data ──────────────────────────────────────────────────
df = pd.merge(stats, sal, on=["name_key", "season"], how="inner")
print(f"  {len(df)} rows after joining salary data")

# ── 5. Feature engineering ────────────────────────────────────────────────────
# One-hot encode position (primary position only)
df["pos_primary"] = df["pos"].str.split("-").str[0].str.strip()
df["pos_primary"] = df["pos_primary"].where(df["pos_primary"].isin(POSITIONS), "SF")

for pos in POSITIONS:
    df[f"Pos_{pos}"] = (df["pos_primary"] == pos).astype(float)

FEATURE_COLS = [
    "age", "g", "gs", "mp_per_game",
    "fg_per_game", "fga_per_game", "fg_percent",
    "x3p_per_game", "x3pa_per_game", "x3p_percent",
    "ft_per_game", "fta_per_game", "ft_percent",
    "orb_per_game", "drb_per_game", "trb_per_game",
    "ast_per_game", "stl_per_game", "blk_per_game", "tov_per_game", "pts_per_game",
    "per", "ts_percent", "usg_percent", "ows", "dws", "ws", "ws_48",
    "obpm", "dbpm", "bpm", "vorp",
    "Pos_C", "Pos_PF", "Pos_PG", "Pos_SF", "Pos_SG",
]

df = df.dropna(subset=["salary_pct"])
df[FEATURE_COLS] = df[FEATURE_COLS].fillna(0)

X = df[FEATURE_COLS].values
y = df["salary_pct"].values

# Cap outliers (max contracts ~35% of cap)
y = np.clip(y, 0, 0.40)

print(f"\nTraining on {len(X)} samples, {len(FEATURE_COLS)} features")

# ── 6. Train ──────────────────────────────────────────────────────────────────
X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

rfr = RandomForestRegressor(
    n_estimators=30, max_depth=6, min_samples_split=8,
    max_features=28, random_state=42
)
rfr.fit(X_train, y_train)

y_pred = rfr.predict(X_test)
r2 = rfr.score(X_test, y_test)
rmse = np.sqrt(mean_squared_error(y_test, y_pred)) * 100

print(f"R² score : {r2:.4f}")
print(f"RMSE     : {rmse:.2f}%")

# ── 7. Save model ─────────────────────────────────────────────────────────────
joblib.dump({"model": rfr, "features": FEATURE_COLS, "positions": POSITIONS}, MODEL_OUT)
print(f"\nModel saved → {MODEL_OUT}")
