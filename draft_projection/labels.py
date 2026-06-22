"""
Career-outcome label generation for the NBA Draft Career Projection system.

Computes one ordinal career-tier label per historical drafted player from
their *resolved* NBA career (archive_advanced, archive_player_per_game,
archive_all_star_selections, archive_end_of_season_teams,
archive_player_award_shares), joined from archive_draft_pick_history.

All source columns in this DB are stored as TEXT (confirmed via
information_schema) and safe_float()/safe_int() are slow per-call PL/pgSQL
functions -- so this module fetches raw text once and casts with pandas,
matching the pattern already used to fix the /api/seasons slowdown.

Tier definitions (highest tier a player qualifies for wins):

  Superstar           all_nba_1st_count >= 2, OR
                       (all_nba_count >= 3 AND peak_bpm >= 6)
  All-NBA              all_nba_count >= 1
  All-Star             all_star_count >= 1
  High-Level Starter   starter_seasons >= 3 AND peak_ws48 >= 0.15
  Starter              starter_seasons >= 3
  Rotation Player      seasons_played >= 4 AND career_total_minutes >= 4000
  End-of-Bench Player  seasons_played >= 4 (but below the Rotation minutes bar)
  Bust                 seasons_played < 4, OR
                       career_total_minutes < 1500 despite sticking around

A player is only labeled once they've had >= MIN_SEASONS_TO_LABEL NBA
seasons since being drafted (or their career has clearly ended) --
too-early-to-judge recent draftees are excluded from training, not
mislabeled as busts.

IMPORTANT, deliberate design property: these tiers measure *absolute* career
outcome (did they stick in the league, start, make an All-Star/All-NBA team)
-- NOT "disappointment relative to draft slot." Confirmed against real data:
Darko Miličić (#2, 2003) lands as Rotation Player and Kwame Brown (#1, 2001)
as Starter, even though both are famous "draft bust" narratives, because
both had long, real NBA careers (9 and 11 seasons with real rotation/starter
minutes) -- they just badly underperformed where they were picked. That's
correct here, not a bug: conflating "bad pick" with "bad career" would mean
the labels themselves are anchored to draft slot, which directly undermines
the system's purpose (finding prospects the league undervalues). Surfacing
"this prospect projects well above their consensus mock rank" is handled
downstream by comparing the model's predicted tier distribution to draft
position context -- never by baking slot into the label.
"""
from __future__ import annotations

import logging

import pandas as pd

log = logging.getLogger("draft_projection.labels")

MIN_SEASONS_TO_LABEL = 4
MIN_GAMES_FOR_QUALIFYING_SEASON = 10  # filters out two-game call-ups
STARTER_SEASON_MIN_GAMES = 41  # half a season, so a 3-game stint at gs/g=1.0 can't count
STARTER_GS_RATIO = 0.5
ROTATION_MIN_MINUTES = 4000.0
BUST_MIN_MINUTES = 1500.0
HIGH_LEVEL_STARTER_MIN_WS48 = 0.15
SUPERSTAR_ALL_NBA_1ST_COUNT = 2
SUPERSTAR_ALL_NBA_COUNT = 3
SUPERSTAR_PEAK_BPM = 6.0

# Award-share rows that corroborate genuine superstar-level recognition.
# Deliberately excludes ROY/MIP/SMOY/Clutch -- those reflect a specific
# role or moment, not sustained superstar caliber.
ELITE_AWARD_TYPES = {"nba mvp", "nba dpoy", "nba finals_mvp"}

TIERS = [
    "bust", "end_of_bench", "rotation", "starter", "high_level_starter",
    "all_star", "all_nba", "superstar",
]
TIER_RANK = {t: i for i, t in enumerate(TIERS)}
TIER_LABEL = {
    "bust": "Bust", "end_of_bench": "End-of-Bench Player", "rotation": "Rotation Player",
    "starter": "Starter", "high_level_starter": "High-Level Starter", "all_star": "All-Star",
    "all_nba": "All-NBA", "superstar": "Superstar",
}


def _to_num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def load_raw_tables(conn, q) -> dict[str, pd.DataFrame]:
    """One query per source table, no SQL-side casting."""
    picks = q(conn, """
        SELECT season, overall_pick, round, tm, player, player_id, college
        FROM archive_draft_pick_history
    """)
    per_game = q(conn, "SELECT player_id, season, g, gs, mp_per_game FROM archive_player_per_game")
    advanced = q(conn, "SELECT player_id, season, per, ws, ws_48, bpm, obpm, dbpm, vorp FROM archive_advanced")
    all_star = q(conn, "SELECT player_id, season FROM archive_all_star_selections")
    end_season = q(conn, "SELECT player_id, season, type, number_tm FROM archive_end_of_season_teams")
    award_shares = q(conn, "SELECT player_id, award, share FROM archive_player_award_shares")
    career_info = q(conn, """
        SELECT player_id, ht_in_in, wt, pos, "from" AS career_from, "to" AS career_to, colleges
        FROM archive_player_career_info
    """)
    return {
        "picks": pd.DataFrame(picks),
        "per_game": pd.DataFrame(per_game),
        "advanced": pd.DataFrame(advanced),
        "all_star": pd.DataFrame(all_star),
        "end_season": pd.DataFrame(end_season),
        "award_shares": pd.DataFrame(award_shares),
        "career_info": pd.DataFrame(career_info),
    }


def _career_aggregates(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """One row per player_id with every aggregate the tier rules need."""
    pg = tables["per_game"].copy()
    pg["season"] = _to_num(pg["season"])
    pg["g"] = _to_num(pg["g"])
    pg["gs"] = _to_num(pg["gs"])
    pg["mp_per_game"] = _to_num(pg["mp_per_game"])
    pg = pg.dropna(subset=["player_id", "season"])

    qualifying = pg[pg["g"] >= MIN_GAMES_FOR_QUALIFYING_SEASON].copy()
    qualifying["season_minutes"] = qualifying["mp_per_game"].fillna(0) * qualifying["g"]
    qualifying["is_starter_season"] = (
        (qualifying["g"] >= STARTER_SEASON_MIN_GAMES)
        & ((qualifying["gs"].fillna(0) / qualifying["g"]) >= STARTER_GS_RATIO)
    )

    agg = qualifying.groupby("player_id").agg(
        seasons_played=("season", "nunique"),
        career_total_minutes=("season_minutes", "sum"),
        starter_seasons=("is_starter_season", "sum"),
        last_qualifying_season=("season", "max"),
    )

    adv = tables["advanced"].copy()
    for c in ["per", "ws", "ws_48", "bpm", "obpm", "dbpm", "vorp"]:
        adv[c] = _to_num(adv[c])
    peak = adv.groupby("player_id").agg(
        peak_per=("per", "max"), peak_ws48=("ws_48", "max"), peak_bpm=("bpm", "max"),
    )
    agg = agg.join(peak, how="left")

    all_star_count = tables["all_star"].groupby("player_id").size().rename("all_star_count")
    agg = agg.join(all_star_count, how="left")

    es = tables["end_season"]
    all_nba = es[es["type"] == "All-NBA"]
    all_nba_count = all_nba.groupby("player_id").size().rename("all_nba_count")
    all_nba_1st_count = (
        all_nba[all_nba["number_tm"] == "1st"].groupby("player_id").size().rename("all_nba_1st_count")
    )
    agg = agg.join(all_nba_count, how="left").join(all_nba_1st_count, how="left")

    aw = tables["award_shares"].copy()
    aw["share"] = _to_num(aw["share"])
    elite = aw[aw["award"].isin(ELITE_AWARD_TYPES)]
    max_award_share = elite.groupby("player_id")["share"].max().rename("max_award_share")
    agg = agg.join(max_award_share, how="left")

    fill_zero = [
        "all_star_count", "all_nba_count", "all_nba_1st_count", "starter_seasons",
        "seasons_played", "career_total_minutes",
    ]
    agg[fill_zero] = agg[fill_zero].fillna(0)
    return agg.reset_index()


def _assign_tier(row: pd.Series) -> str:
    all_nba_1st = row["all_nba_1st_count"] or 0
    all_nba = row["all_nba_count"] or 0
    peak_bpm = row["peak_bpm"] if pd.notna(row["peak_bpm"]) else float("-inf")
    peak_ws48 = row["peak_ws48"] if pd.notna(row["peak_ws48"]) else float("-inf")
    starter_seasons = row["starter_seasons"] or 0
    seasons_played = row["seasons_played"] or 0
    career_minutes = row["career_total_minutes"] or 0
    all_star_count = row["all_star_count"] or 0

    if all_nba_1st >= SUPERSTAR_ALL_NBA_1ST_COUNT or (all_nba >= SUPERSTAR_ALL_NBA_COUNT and peak_bpm >= SUPERSTAR_PEAK_BPM):
        return "superstar"
    if all_nba >= 1:
        return "all_nba"
    if all_star_count >= 1:
        return "all_star"
    if starter_seasons >= 3 and peak_ws48 >= HIGH_LEVEL_STARTER_MIN_WS48:
        return "high_level_starter"
    if starter_seasons >= 3:
        return "starter"
    if seasons_played >= MIN_SEASONS_TO_LABEL and career_minutes >= ROTATION_MIN_MINUTES:
        return "rotation"
    if seasons_played >= MIN_SEASONS_TO_LABEL and career_minutes >= BUST_MIN_MINUTES:
        return "end_of_bench"
    return "bust"


def build_career_labels(conn, q, current_season: int) -> pd.DataFrame:
    """Returns one row per drafted player who has enough resolved career to
    label, with their tier and every aggregate that produced it. Players
    drafted too recently (fewer than MIN_SEASONS_TO_LABEL seasons could have
    elapsed since their draft season) are excluded -- not mislabeled."""
    tables = load_raw_tables(conn, q)
    picks = tables["picks"].copy()
    picks["season"] = _to_num(picks["season"])
    picks["overall_pick"] = _to_num(picks["overall_pick"])
    picks = picks.dropna(subset=["player_id", "season"])

    aggregates = _career_aggregates(tables)
    merged = picks.merge(aggregates, on="player_id", how="left")

    fill_zero = [
        "all_star_count", "all_nba_count", "all_nba_1st_count", "starter_seasons",
        "seasons_played", "career_total_minutes",
    ]
    merged[fill_zero] = merged[fill_zero].fillna(0)

    seasons_since_draft = current_season - merged["season"]
    too_recent = seasons_since_draft < MIN_SEASONS_TO_LABEL
    n_excluded = int(too_recent.sum())
    if n_excluded:
        log.info("Excluding %d too-recently-drafted players from labeling (drafted within the last %d seasons)",
                  n_excluded, MIN_SEASONS_TO_LABEL)
    labeled = merged[~too_recent].copy()

    labeled["tier"] = labeled.apply(_assign_tier, axis=1)
    labeled["tier_label"] = labeled["tier"].map(TIER_LABEL)
    labeled["tier_rank"] = labeled["tier"].map(TIER_RANK)

    log.info("Built labels for %d drafted players. Tier distribution:\n%s",
              len(labeled), labeled["tier"].value_counts().to_string())
    return labeled
