"""
Pull NBA referee data for seasons 2015-2025 using nba_api.

Step 1 (fast, ~10 API calls): LeagueGameLog per season → get PF, FTA, PTS per team per game.
Step 2 (slow, ~12,300 calls): BoxScoreSummaryV2 per game → get 3 officials per game.

Output: referee_games.csv — one row per game with ref names + team stats.

Usage:
    pip install nba_api pandas
    python pull_referee_data.py              # full run (resumes if CSV already exists)
    python pull_referee_data.py --step1      # only run step 1 (game logs)
    python pull_referee_data.py --step2      # only run step 2 (officials)
    python pull_referee_data.py --seasons 2024 2025  # specific seasons only
"""

import time
import csv
import os
import sys
import argparse

try:
    from nba_api.stats.endpoints import LeagueGameLog, BoxScoreSummaryV2
except ImportError:
    print("ERROR: nba_api not installed. Run: pip install nba_api")
    sys.exit(1)

# ── Config ────────────────────────────────────────────────────────────────────
SEASONS = [f"{y}-{str(y+1)[2:]}" for y in range(2014, 2025)]  # 2014-15 to 2024-25
OUTPUT = os.path.join(os.path.dirname(__file__), "referee_games.csv")
OFFICIALS_CACHE = os.path.join(os.path.dirname(__file__), "referee_officials_cache.csv")
HEADERS = [
    "game_id", "season", "game_date", "home_team", "away_team",
    "ref1", "ref2", "ref3",
    "home_pf", "away_pf", "home_fta", "away_fta", "home_pts", "away_pts",
]
SLEEP_BETWEEN_CALLS = 0.65


def get_game_logs(season: str) -> dict:
    """Fetch LeagueGameLog for one season. Returns dict: game_id -> partial row."""
    print(f"  Fetching game log for {season}...")
    log = LeagueGameLog(season=season, season_type_all_star="Regular Season")
    df = log.get_data_frames()[0]
    season_year = int(season[:4]) + 1  # "2014-15" → 2015
    games = {}
    for _, row in df.iterrows():
        gid = str(row["GAME_ID"]).strip()
        is_home = " vs. " in str(row["MATCHUP"])
        side = "home" if is_home else "away"
        if gid not in games:
            games[gid] = {"season": season_year, "game_date": str(row["GAME_DATE"])[:10]}
        games[gid][f"{side}_team"] = row["TEAM_ABBREVIATION"]
        games[gid][f"{side}_pf"] = int(row["PF"])
        games[gid][f"{side}_fta"] = int(row["FTA"])
        games[gid][f"{side}_pts"] = int(row["PTS"])
    time.sleep(SLEEP_BETWEEN_CALLS)
    return games


def get_officials(game_id: str) -> list:
    """Return list of up to 3 referee full names for a game."""
    summary = BoxScoreSummaryV2(game_id=game_id)
    officials_df = summary.get_data_frames()[2]  # index 2 = Officials
    names = (officials_df["FIRST_NAME"].str.strip() + " " + officials_df["LAST_NAME"].str.strip()).tolist()
    while len(names) < 3:
        names.append("")
    return names[:3]


def load_existing_officials() -> dict:
    """Load already-fetched officials from cache file. Returns dict: game_id -> [r1,r2,r3]."""
    if not os.path.exists(OFFICIALS_CACHE):
        return {}
    result = {}
    with open(OFFICIALS_CACHE, newline="") as f:
        for row in csv.DictReader(f):
            result[row["game_id"]] = [row["ref1"], row["ref2"], row["ref3"]]
    return result


def step1(target_seasons):
    """Collect team stats per game for all target seasons."""
    all_games = {}
    for season in target_seasons:
        games = get_game_logs(season)
        all_games.update(games)
        print(f"    → {len(games)} games (total so far: {len(all_games)})")
    complete = {
        gid: d for gid, d in all_games.items()
        if all(k in d for k in ["home_team", "away_team", "home_pf", "away_pf"])
    }
    print(f"\nStep 1 complete: {len(complete)} full games across {len(target_seasons)} season(s).")
    return complete


def step2(game_data: dict):
    """Fetch officials for each game and write output CSV."""
    existing = load_existing_officials()
    print(f"\nStep 2: Fetching officials per game...")
    print(f"  {len(existing)} games already cached, {len(game_data) - len(existing)} remaining.")

    # Append new officials to cache file
    cache_exists = os.path.exists(OFFICIALS_CACHE)
    cache_f = open(OFFICIALS_CACHE, "a", newline="")
    cache_writer = csv.DictWriter(cache_f, fieldnames=["game_id", "ref1", "ref2", "ref3"])
    if not cache_exists:
        cache_writer.writeheader()

    todo = [(gid, d) for gid, d in game_data.items() if gid not in existing]
    errors = 0
    for i, (gid, _) in enumerate(todo):
        if i % 200 == 0:
            print(f"  {i}/{len(todo)} ({errors} errors)...")
        try:
            refs = get_officials(gid)
            existing[gid] = refs
            cache_writer.writerow({"game_id": gid, "ref1": refs[0], "ref2": refs[1], "ref3": refs[2]})
            cache_f.flush()
        except Exception as exc:
            errors += 1
            if errors <= 20:
                print(f"    Error on {gid}: {exc}")
        time.sleep(SLEEP_BETWEEN_CALLS)

    cache_f.close()
    print(f"\nStep 2 complete: {len(existing)} officials fetched ({errors} errors).")
    return existing


def write_output(game_data: dict, officials: dict):
    """Merge game data + officials and write final CSV."""
    with open(OUTPUT, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=HEADERS)
        writer.writeheader()
        skipped = 0
        for gid, d in game_data.items():
            refs = officials.get(gid, ["", "", ""])
            if not refs[0]:
                skipped += 1
                continue
            writer.writerow({
                "game_id": gid,
                "season": d.get("season", ""),
                "game_date": d.get("game_date", ""),
                "home_team": d.get("home_team", ""),
                "away_team": d.get("away_team", ""),
                "ref1": refs[0], "ref2": refs[1], "ref3": refs[2],
                "home_pf": d.get("home_pf", 0),
                "away_pf": d.get("away_pf", 0),
                "home_fta": d.get("home_fta", 0),
                "away_fta": d.get("away_fta", 0),
                "home_pts": d.get("home_pts", 0),
                "away_pts": d.get("away_pts", 0),
            })
        print(f"\nOutput written to {OUTPUT} ({skipped} games skipped — no officials found).")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--step1", action="store_true", help="Only run step 1 (game logs)")
    parser.add_argument("--step2", action="store_true", help="Only run step 2 (officials fetch)")
    parser.add_argument("--seasons", nargs="+", type=int, help="Specific end years e.g. 2024 2025")
    args = parser.parse_args()

    if args.seasons:
        target = [f"{y-1}-{str(y)[2:]}" for y in args.seasons]
    else:
        target = SEASONS

    if args.step1:
        step1(target)
        return
    if args.step2:
        # Load game data from cache if available, otherwise re-run step 1
        game_data = step1(target)
        officials = step2(game_data)
        write_output(game_data, officials)
        return

    print("=== NBA Referee Data Pull ===")
    print(f"Seasons: {target[0]} → {target[-1]}")
    print(f"Estimated time: ~{len(target) * 1230 * SLEEP_BETWEEN_CALLS / 3600:.1f} hours\n")

    game_data = step1(target)
    officials = step2(game_data)
    write_output(game_data, officials)

    print("\nNext step: load into Supabase with:")
    print(f"  DATABASE_URL=... python load_referee_data.py {OUTPUT}")


if __name__ == "__main__":
    main()
