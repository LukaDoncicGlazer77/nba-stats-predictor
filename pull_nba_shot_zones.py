#!/usr/bin/env python3
"""
Pulls per-player per-season NBA shot zone attempt data from stats.nba.com
via nba_api and writes/updates nba_shot_zones.csv.

Zones mapped to match our college shot_zones.csv convention:
  rim   = Restricted Area + In The Paint (Non-RA)
  mid   = Mid-Range
  three = Left Corner 3 + Right Corner 3 + Above the Break 3

Run:
  python pull_nba_shot_zones.py               # all missing seasons
  python pull_nba_shot_zones.py --season 2024 # single season
  python pull_nba_shot_zones.py --full        # rebuild from scratch
"""
import argparse
import csv
import os
import re
import time
from pathlib import Path

from nba_api.stats.endpoints import LeagueDashPlayerShotLocations

OUT = Path(__file__).parent / "nba_shot_zones.csv"
HEADERS = ["player", "season", "rim_a", "mid_a", "three_a"]
# nba_api season IDs go back to 1996-97; shot location data starts ~1997
FIRST_SEASON = 1997
# Current season end-year
CURRENT_SEASON = 2026


def normalize(name: str) -> str:
    name = str(name or "").strip()
    if "," in name:
        last, first = name.split(",", 1)
        name = f"{first.strip()} {last.strip()}"
    return re.sub(r"[^a-z ]", "", name.lower()).strip()


def season_str(end_year: int) -> str:
    return f"{end_year - 1}-{str(end_year)[2:]}"


def load_existing() -> set:
    """Returns set of (normalized_name, season) already in the CSV."""
    if not OUT.exists():
        return set()
    with open(OUT, newline="") as f:
        return {(r["player"], int(r["season"])) for r in csv.DictReader(f)}


def pull_season(end_year: int, existing: set) -> list[dict]:
    """Returns rows for a season, skipping players already present."""
    print(f"  Pulling {season_str(end_year)}...", flush=True)
    try:
        resp = LeagueDashPlayerShotLocations(
            season=season_str(end_year),
            season_type_all_star="Regular Season",
            per_mode_simple="Totals",
            timeout=60,
        )
        df = resp.get_data_frames()[0]
    except Exception as e:
        print(f"    ERROR: {e}")
        return []

    rows = []
    for _, row in df.iterrows():
        name = normalize(str(row.get("PLAYER_NAME", "")))
        if not name:
            continue
        if (name, end_year) in existing:
            continue

        # Aggregate zones
        def g(col, default=0):
            v = row.get(col)
            try:
                return int(float(v)) if v is not None else default
            except (TypeError, ValueError):
                return default

        rim_a   = g("RESTRICTED_AREA_FGA") + g("IN_THE_PAINT_NON_RA_FGA")
        mid_a   = g("MID_RANGE_FGA")
        three_a = g("LEFT_CORNER_3_FGA") + g("RIGHT_CORNER_3_FGA") + g("ABOVE_THE_BREAK_3_FGA")

        if rim_a + mid_a + three_a == 0:
            continue
        rows.append({"player": name, "season": end_year,
                     "rim_a": rim_a, "mid_a": mid_a, "three_a": three_a})
    print(f"    {len(rows)} new rows")
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--season", type=int, help="Single season end-year to pull")
    parser.add_argument("--full", action="store_true", help="Rebuild from scratch")
    args = parser.parse_args()

    existing = set() if args.full else load_existing()
    print(f"Existing rows: {len(existing)}")

    if args.full and OUT.exists():
        OUT.unlink()

    if args.season:
        seasons = [args.season]
    else:
        seasons = list(range(FIRST_SEASON, CURRENT_SEASON + 1))

    write_header = not OUT.exists()
    with open(OUT, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=HEADERS)
        if write_header:
            writer.writeheader()

        for yr in seasons:
            rows = pull_season(yr, existing)
            writer.writerows(rows)
            f.flush()
            existing.update((r["player"], r["season"]) for r in rows)
            time.sleep(0.6)  # respect stats.nba.com rate limit

    total = sum(1 for _ in open(OUT)) - 1
    print(f"\nDone — {total} total rows in {OUT}")


if __name__ == "__main__":
    main()
