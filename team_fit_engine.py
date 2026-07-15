"""
Team fit analysis: given a player (NBA or prospect), score how well they'd
complement each NBA team's current roster archetype composition.

Logic:
  1. Load the most recent season's archetype data for all NBA players with a team.
  2. For each team, compute the average archetype mix (% of each of the 9 archetypes).
  3. For the target player, find which archetypes they bring most strongly.
  4. Score each team by how much the player fills a gap in their roster.
  5. Apply a complementarity bonus/penalty (e.g. a team already stacked with HEs
     gets penalized for adding another HE).
  6. Return the top 5 teams with a plain-English reason.
"""

import math

_ARCH_ORDER = [
    "Heliocentric Engine", "Secondary Playmaker", "Off-Ball Scorer",
    "Scoring Big", "Playmaking Big", "Rim Protector",
    "3&D Wing", "Defensive Wing", "Hybrid Offensive Big",
]

# Multi-team rows (trade seasons) — skip these for team composition
_MULTI_TEAM = {"2TM", "3TM", "4TM", "TOT"}

# BBRef abbrev → display name + city
_TEAM_NAMES = {
    "ATL": ("Atlanta", "Hawks"),
    "BOS": ("Boston", "Celtics"),
    "BRK": ("Brooklyn", "Nets"),
    "CHI": ("Chicago", "Bulls"),
    "CHO": ("Charlotte", "Hornets"),
    "CLE": ("Cleveland", "Cavaliers"),
    "DAL": ("Dallas", "Mavericks"),
    "DEN": ("Denver", "Nuggets"),
    "DET": ("Detroit", "Pistons"),
    "GSW": ("Golden State", "Warriors"),
    "HOU": ("Houston", "Rockets"),
    "IND": ("Indiana", "Pacers"),
    "LAC": ("LA", "Clippers"),
    "LAL": ("LA", "Lakers"),
    "MEM": ("Memphis", "Grizzlies"),
    "MIA": ("Miami", "Heat"),
    "MIL": ("Milwaukee", "Bucks"),
    "MIN": ("Minnesota", "Timberwolves"),
    "NOP": ("New Orleans", "Pelicans"),
    "NYK": ("New York", "Knicks"),
    "OKC": ("Oklahoma City", "Thunder"),
    "ORL": ("Orlando", "Magic"),
    "PHI": ("Philadelphia", "76ers"),
    "PHO": ("Phoenix", "Suns"),
    "POR": ("Portland", "Trail Blazers"),
    "SAC": ("Sacramento", "Kings"),
    "SAS": ("San Antonio", "Spurs"),
    "TOR": ("Toronto", "Raptors"),
    "UTA": ("Utah", "Jazz"),
    "WAS": ("Washington", "Wizards"),
}

# Archetypes that conflict (having too many hurts the team)
# Maps archetype → penalty factor applied when the team already has excess of it
_SATURATION_THRESHOLD = 25.0  # team average % above which adding more hurts
_SATURATION_PENALTY = 0.55    # multiply gap score by this if team is saturated

# How much each archetype "needs" certain other archetypes on the roster
# Positive = complementary (having X on team helps Y fit)
# Negative = conflicting (having too much X on team hurts Y's fit)
_COMPLEMENTARITY = {
    "Heliocentric Engine": {
        "Off-Ball Scorer": +0.15, "3&D Wing": +0.15, "Rim Protector": +0.10,
        "Heliocentric Engine": -0.30,
    },
    "Secondary Playmaker": {
        "Scoring Big": +0.10, "Off-Ball Scorer": +0.10, "Rim Protector": +0.08,
        "Secondary Playmaker": -0.20, "Heliocentric Engine": -0.10,
    },
    "Off-Ball Scorer": {
        "Heliocentric Engine": +0.12, "Secondary Playmaker": +0.08,
        "Off-Ball Scorer": -0.15,
    },
    "Scoring Big": {
        "Heliocentric Engine": +0.12, "Secondary Playmaker": +0.08,
        "Scoring Big": -0.20,
    },
    "Playmaking Big": {
        "Off-Ball Scorer": +0.10, "3&D Wing": +0.12, "Defensive Wing": +0.08,
        "Playmaking Big": -0.25, "Heliocentric Engine": -0.08,
    },
    "Rim Protector": {
        "Heliocentric Engine": +0.10, "Secondary Playmaker": +0.08, "3&D Wing": +0.08,
        "Rim Protector": -0.30,
    },
    "3&D Wing": {
        "Heliocentric Engine": +0.12, "Secondary Playmaker": +0.10, "Playmaking Big": +0.08,
        "3&D Wing": -0.12,
    },
    "Defensive Wing": {
        "Heliocentric Engine": +0.10, "Secondary Playmaker": +0.08,
        "Defensive Wing": -0.15,
    },
    "Hybrid Offensive Big": {
        "Heliocentric Engine": +0.08, "3&D Wing": +0.10,
        "Hybrid Offensive Big": -0.20,
    },
}


def _dominant_archetype(mix: dict) -> str:
    return max(mix, key=mix.get)


# Position groups for crowding detection. BBRef pos strings like "PG", "SG-SF", "C" etc.
_GUARD_POS   = {"PG", "SG"}
_WING_POS    = {"SF", "SG-SF", "SF-SG", "SF-PF", "PF-SF"}
_BIG_POS     = {"PF", "C", "PF-C", "C-PF"}

def _pos_group(pos: str | None) -> str:
    """Coerce a BBRef position string to 'guard', 'wing', or 'big'."""
    if not pos:
        return "wing"
    primary = pos.split("-")[0].strip().upper()
    if primary in ("PG", "SG"):
        return "guard"
    if primary in ("PF", "C"):
        return "big"
    return "wing"


def _build_team_compositions(pool, season: int, exclude_player_id: str | None = None) -> dict:
    """Returns {team_abbrev: team_info_dict} for all 30 teams.

    team_info_dict keys:
      mix         — games-weighted average archetype %s (top-9 rotation)
      avg_usg_pr  — games-weighted average usg_pct_pr (creation-demand signal)
      pos_counts  — {'guard': N, 'wing': N, 'big': N} among rotation
      total_vorp  — sum of vorp for all qualifying players (contender signal)

    exclude_player_id: omit this player from their own team's composition so
    the team isn't artificially satisfied by the player already being on it.
    """
    MIN_GAMES = 20
    TOP_ROTATION = 9

    by_team: dict[str, list[dict]] = {}
    vorp_by_team: dict[str, float] = {}

    for p in pool:
        if p.get("season") != season:
            continue
        if exclude_player_id and p.get("player_id") == exclude_player_id:
            continue
        team = p.get("team")
        if not team or team in _MULTI_TEAM:
            continue
        if (p.get("games") or 0) < MIN_GAMES:
            continue
        if not p.get("named_mix"):
            continue
        vorp_by_team[team] = vorp_by_team.get(team, 0.0) + (p.get("vorp") or 0.0)
        by_team.setdefault(team, []).append({
            "mix": p["named_mix"],
            "games": float(p.get("games") or 1),
            "usg_pr": p.get("usg_pct_pr") or 0.5,
            "pos_group": _pos_group(p.get("pos")),
        })

    compositions = {}
    for team, players in by_team.items():
        players.sort(key=lambda x: -x["games"])
        rotation = players[:TOP_ROTATION]

        total_games = sum(pl["games"] for pl in rotation)
        if total_games == 0:
            continue

        avg = {}
        for arch in _ARCH_ORDER:
            avg[arch] = sum(pl["mix"].get(arch, 0) * pl["games"] for pl in rotation) / total_games

        avg_usg_pr = sum(pl["usg_pr"] * pl["games"] for pl in rotation) / total_games

        pos_counts: dict[str, int] = {"guard": 0, "wing": 0, "big": 0}
        for pl in rotation:
            pos_counts[pl["pos_group"]] += 1

        compositions[team] = {
            "mix": avg,
            "avg_usg_pr": avg_usg_pr,
            "pos_counts": pos_counts,
            "total_vorp": vorp_by_team.get(team, 0.0),
        }
    return compositions


def _league_avg(compositions: dict) -> dict:
    """Average archetype distribution across all teams."""
    avg = {a: 0.0 for a in _ARCH_ORDER}
    n = len(compositions)
    if n == 0:
        return avg
    for info in compositions.values():
        for arch in _ARCH_ORDER:
            avg[arch] += info["mix"].get(arch, 0)
    return {a: v / n for a, v in avg.items()}


# Position slots considered crowded (out of a 9-man rotation)
_POS_CROWDED_THRESHOLD = 4   # ≥4 players at the same position group = crowded
_POS_CROWDED_PENALTY   = 0.75  # multiply raw score by this when position is crowded

# Creation-clash: when both player and team are high ball-demand, penalize.
# usg_pct_pr > 0.70 = high-usage player / team.
_CREATION_CLASH_THRESHOLD = 0.68
_CREATION_CLASH_PENALTY   = 0.80


def _fit_score(
    player_mix: dict,
    team_info: dict,
    league_avg: dict,
    player_usg_pr: float = 0.5,
    player_pos_group: str = "wing",
) -> tuple[float, str]:
    """
    Score how well a player fits a team and produce a reason string.

    New signals vs. the original:
      - Creation clash: penalises adding a high-usage player to a team already
        carrying a high average usage load (two ball-dominant players clash).
      - Position crowding: penalises adding a player when the team already has
        ≥4 rotation spots at the same position group (guard/wing/big).
    """
    team_mix      = team_info["mix"]
    team_usg_pr   = team_info.get("avg_usg_pr", 0.5)
    pos_counts    = team_info.get("pos_counts", {})

    player_primary = _dominant_archetype(player_mix)

    raw = 0.0
    gap_contributions: list[tuple[float, str]] = []

    for arch in _ARCH_ORDER:
        p_pct = player_mix.get(arch, 0)
        t_pct = team_mix.get(arch, 0)
        la_pct = league_avg.get(arch, 0)

        gap = la_pct - t_pct  # positive = team is weak here
        contribution = p_pct * max(gap, 0) / 100

        if t_pct > _SATURATION_THRESHOLD and gap < 0:
            contribution *= _SATURATION_PENALTY

        gap_contributions.append((contribution, arch))
        raw += contribution

    # Complementarity bonus/penalty
    comp_bonus = 0.0
    for arch, t_pct in team_mix.items():
        if t_pct < 10:
            continue
        for p_arch, p_weight in player_mix.items():
            rules = _COMPLEMENTARITY.get(p_arch, {})
            if arch in rules:
                comp_bonus += rules[arch] * (t_pct / 100) * (p_weight / 100)

    raw = max(0.0, raw + comp_bonus * 50)

    # Creation clash: both player and team are high ball-demand
    creation_clash = (
        player_usg_pr > _CREATION_CLASH_THRESHOLD
        and team_usg_pr > _CREATION_CLASH_THRESHOLD
    )
    if creation_clash:
        raw *= _CREATION_CLASH_PENALTY

    # Position crowding: team already has too many players at this position group
    pos_crowded = pos_counts.get(player_pos_group, 0) >= _POS_CROWDED_THRESHOLD
    if pos_crowded:
        raw *= _POS_CROWDED_PENALTY

    # Build reason string
    gap_contributions.sort(key=lambda x: -x[0])
    top_arch = gap_contributions[0][1] if gap_contributions else player_primary

    team_weak_on = [
        arch for arch in _ARCH_ORDER
        if (league_avg.get(arch, 0) - team_mix.get(arch, 0)) > 3
    ]

    if creation_clash:
        reason = f"Ball-dominant roster — may clash with {player_primary} role"
    elif pos_crowded:
        reason = f"Crowded at {player_pos_group} — fewer minutes available"
    elif player_primary in team_weak_on:
        reason = f"Fills a gap — team is thin on {player_primary}s"
    elif top_arch != player_primary and top_arch in team_weak_on:
        reason = f"Addresses team's need for {top_arch} while contributing as a {player_primary}"
    else:
        comp_rules = _COMPLEMENTARITY.get(player_primary, {})
        strong_on_team = [
            a for a in _ARCH_ORDER
            if team_mix.get(a, 0) > _SATURATION_THRESHOLD and a in comp_rules and comp_rules[a] > 0
        ]
        if strong_on_team:
            reason = f"{player_primary} pairs well with team's {strong_on_team[0]} core"
        elif team_weak_on:
            reason = f"Roster versatility — covers {team_weak_on[0]} need as a {player_primary}"
        else:
            reason = f"Well-rounded fit as a {player_primary} on a balanced roster"

    return raw, reason


def score_team_fit(
    player_mix: dict,
    pool,
    season: int,
    top_n: int = 5,
    player_id: str | None = None,
    player_usg_pr: float = 0.5,
    player_pos: str | None = None,
) -> list[dict]:
    """
    Main entry point. Returns a list of top_n team fit dicts:
      {team, city, name, fit_score, reason, team_needs, contender, player_primary}

    player_id:     BBRef slug — excluded from their own team's composition.
    player_usg_pr: player's usage percentile (0-1) for creation-clash detection.
    player_pos:    BBRef position string for position-crowding detection.
    """
    compositions = _build_team_compositions(pool, season, exclude_player_id=player_id)
    if not compositions:
        return []

    la = _league_avg(compositions)
    player_pos_group = _pos_group(player_pos)

    # Determine contender threshold: top third of teams by total VORP.
    all_vorps = sorted((info["total_vorp"] for info in compositions.values()), reverse=True)
    contender_cutoff = all_vorps[len(all_vorps) // 3] if all_vorps else 0.0

    raw_results = []
    for abbrev, team_info in compositions.items():
        raw_score, reason = _fit_score(
            player_mix, team_info, la,
            player_usg_pr=player_usg_pr,
            player_pos_group=player_pos_group,
        )
        city, name = _TEAM_NAMES.get(abbrev, (abbrev, ""))
        team_mix = team_info["mix"]
        gaps = sorted(
            [(la.get(a, 0) - team_mix.get(a, 0), a) for a in _ARCH_ORDER],
            reverse=True,
        )
        top_gaps = [a for gap, a in gaps if gap > 2][:3]
        raw_results.append({
            "team": abbrev,
            "city": city,
            "name": name,
            "_raw": raw_score,
            "reason": reason,
            "team_needs": top_gaps,
            "contender": team_info["total_vorp"] >= contender_cutoff,
            "player_primary": _dominant_archetype(player_mix),
        })

    raw_results.sort(key=lambda r: -r["_raw"])

    # Anchor: the median raw score across all 30 teams maps to 50.
    # This gives absolute meaning (top fits score 70-90+, poor fits 20-40)
    # without being sensitive to near-zero league-average self-scores.
    all_raws = sorted(r["_raw"] for r in raw_results)
    mid = len(all_raws) // 2
    median_raw = (all_raws[mid - 1] + all_raws[mid]) / 2 if len(all_raws) >= 2 else all_raws[0]
    scale_anchor = max(median_raw, 0.001)

    results = []
    for r in raw_results[:top_n]:
        absolute = 50 * r["_raw"] / scale_anchor
        r["fit_score"] = round(max(20, min(97, absolute)), 1)
        del r["_raw"]
        results.append(r)

    return results
