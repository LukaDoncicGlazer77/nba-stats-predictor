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


def _build_team_compositions(pool, season: int) -> dict:
    """Returns {team_abbrev: avg_mix_dict} for all 30 teams using the
    most recent season's pool data. Filters to MIN_GAMES threshold."""
    MIN_GAMES = 20
    by_team: dict[str, list[dict]] = {}
    for p in pool:
        if p.get("season") != season:
            continue
        team = p.get("team")
        if not team or team in _MULTI_TEAM:
            continue
        if (p.get("games") or 0) < MIN_GAMES:
            continue
        if not p.get("named_mix"):
            continue
        by_team.setdefault(team, []).append(p["named_mix"])

    compositions = {}
    for team, mixes in by_team.items():
        avg = {}
        for arch in _ARCH_ORDER:
            avg[arch] = sum(m.get(arch, 0) for m in mixes) / len(mixes)
        compositions[team] = avg
    return compositions


def _league_avg(compositions: dict) -> dict:
    """Average archetype distribution across all teams."""
    avg = {a: 0.0 for a in _ARCH_ORDER}
    n = len(compositions)
    if n == 0:
        return avg
    for team_mix in compositions.values():
        for arch in _ARCH_ORDER:
            avg[arch] += team_mix.get(arch, 0)
    return {a: v / n for a, v in avg.items()}


def _fit_score(player_mix: dict, team_mix: dict, league_avg: dict) -> tuple[float, str]:
    """
    Score how well a player fits a team (0-100) and produce a reason string.

    Approach:
      - For each archetype, compute player's contribution weighted by how much
        the team is below league average for that archetype (gap filling).
      - Apply saturation penalty when team already has too much of that archetype.
      - Apply complementarity bonus/penalty based on roster composition.
      - Normalize to 0-100.
    """
    player_primary = _dominant_archetype(player_mix)
    player_pct = player_mix.get(player_primary, 0)

    raw = 0.0
    gap_contributions: list[tuple[float, str]] = []

    for arch in _ARCH_ORDER:
        p_pct = player_mix.get(arch, 0)
        t_pct = team_mix.get(arch, 0)
        la_pct = league_avg.get(arch, 0)

        # How much below league average is the team for this archetype?
        gap = la_pct - t_pct  # positive = team is weak here

        # Player contributes their % weighted by how much the team needs it
        contribution = p_pct * max(gap, 0) / 100

        # Saturation penalty: if team already has way above average, adding more hurts
        if t_pct > _SATURATION_THRESHOLD and gap < 0:
            contribution *= _SATURATION_PENALTY

        gap_contributions.append((contribution, arch))
        raw += contribution

    # Complementarity: look at what the team already has strong and adjust
    comp_bonus = 0.0
    for arch, t_pct in team_mix.items():
        if t_pct < 10:
            continue  # team doesn't really have this archetype, skip
        rules = _COMPLEMENTARITY.get(player_primary, {})
        if arch in rules:
            comp_bonus += rules[arch] * (t_pct / 100)

    raw = max(0.0, raw + comp_bonus * 20)

    # Build reason: top archetype gap filled
    gap_contributions.sort(key=lambda x: -x[0])
    top_arch = gap_contributions[0][1] if gap_contributions else player_primary

    team_weak_on = [
        arch for arch in _ARCH_ORDER
        if (league_avg.get(arch, 0) - team_mix.get(arch, 0)) > 3
    ]

    if player_primary in team_weak_on:
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


def score_team_fit(player_mix: dict, pool, season: int, top_n: int = 5) -> list[dict]:
    """
    Main entry point. Returns a list of top_n team fit dicts:
      {team, city, name, fit_score, reason, team_archetype_gaps}
    """
    compositions = _build_team_compositions(pool, season)
    if not compositions:
        return []

    la = _league_avg(compositions)
    raw_results = []
    for abbrev, team_mix in compositions.items():
        raw_score, reason = _fit_score(player_mix, team_mix, la)
        city, name = _TEAM_NAMES.get(abbrev, (abbrev, ""))
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
            "player_primary": _dominant_archetype(player_mix),
        })

    raw_results.sort(key=lambda r: -r["_raw"])

    # Normalize so top team = 95, rest scaled linearly, floor at 40
    max_raw = raw_results[0]["_raw"] if raw_results else 1.0
    min_raw = raw_results[-1]["_raw"] if raw_results else 0.0
    span = max(max_raw - min_raw, 0.001)

    results = []
    for r in raw_results[:top_n]:
        normalized = 40 + 55 * (r["_raw"] - min_raw) / span
        r["fit_score"] = round(min(95, normalized), 1)
        del r["_raw"]
        results.append(r)

    return results
