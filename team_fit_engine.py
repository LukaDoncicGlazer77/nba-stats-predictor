"""
Team fit analysis: given a player, score how well they complement each NBA team.

Signals (in order of application):
  1. Gap filling    — player covers archetypes the team is below league average on
  2. Complementarity — hand-coded archetype synergy/conflict rules (team → player)
  3. Effectiveness  — what team archetypes make THIS player better (bidirectional)
  4. Star pairing   — how well player pairs with the team's highest-VORP anchor
  5. Creation clash — penalty when both player and team are high ball-demand
  6. Minutes factor — continuous crowding penalty as position group fills up
"""

import math

_ARCH_ORDER = [
    "Heliocentric Engine", "Secondary Playmaker", "Off-Ball Scorer",
    "Scoring Big", "Playmaking Big", "Rim Protector",
    "3&D Wing", "Defensive Wing", "Hybrid Offensive Big",
]

_MULTI_TEAM = {"2TM", "3TM", "4TM", "TOT"}

_TEAM_NAMES = {
    "ATL": ("Atlanta", "Hawks"),       "BOS": ("Boston", "Celtics"),
    "BRK": ("Brooklyn", "Nets"),       "CHI": ("Chicago", "Bulls"),
    "CHO": ("Charlotte", "Hornets"),   "CLE": ("Cleveland", "Cavaliers"),
    "DAL": ("Dallas", "Mavericks"),    "DEN": ("Denver", "Nuggets"),
    "DET": ("Detroit", "Pistons"),     "GSW": ("Golden State", "Warriors"),
    "HOU": ("Houston", "Rockets"),     "IND": ("Indiana", "Pacers"),
    "LAC": ("LA", "Clippers"),         "LAL": ("LA", "Lakers"),
    "MEM": ("Memphis", "Grizzlies"),   "MIA": ("Miami", "Heat"),
    "MIL": ("Milwaukee", "Bucks"),     "MIN": ("Minnesota", "Timberwolves"),
    "NOP": ("New Orleans", "Pelicans"),"NYK": ("New York", "Knicks"),
    "OKC": ("Oklahoma City", "Thunder"),"ORL": ("Orlando", "Magic"),
    "PHI": ("Philadelphia", "76ers"),  "PHO": ("Phoenix", "Suns"),
    "POR": ("Portland", "Trail Blazers"),"SAC": ("Sacramento", "Kings"),
    "SAS": ("San Antonio", "Spurs"),   "TOR": ("Toronto", "Raptors"),
    "UTA": ("Utah", "Jazz"),           "WAS": ("Washington", "Wizards"),
}

_SATURATION_THRESHOLD = 25.0
_SATURATION_PENALTY   = 0.55

# Team has X → player archetype Y benefits/clashes
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

# Team has X → player archetype Y becomes MORE effective at their role
# (bidirectional: complements the player, not just the team)
_PLAYER_EFFECTIVENESS = {
    "Heliocentric Engine":  {"Off-Ball Scorer": 0.10, "3&D Wing": 0.10, "Rim Protector": 0.08},
    "Secondary Playmaker":  {"Heliocentric Engine": 0.08, "Off-Ball Scorer": 0.08, "Scoring Big": 0.06},
    "Off-Ball Scorer":      {"Heliocentric Engine": 0.15, "Secondary Playmaker": 0.10, "Playmaking Big": 0.08},
    "3&D Wing":             {"Heliocentric Engine": 0.12, "Secondary Playmaker": 0.10, "Playmaking Big": 0.08},
    "Rim Protector":        {"Heliocentric Engine": 0.10, "Secondary Playmaker": 0.08, "3&D Wing": 0.06},
    "Playmaking Big":       {"Off-Ball Scorer": 0.10, "3&D Wing": 0.10, "Defensive Wing": 0.08},
    "Scoring Big":          {"Heliocentric Engine": 0.10, "Secondary Playmaker": 0.08, "3&D Wing": 0.06},
    "Defensive Wing":       {"Heliocentric Engine": 0.10, "Off-Ball Scorer": 0.06},
    "Hybrid Offensive Big": {"3&D Wing": 0.10, "Secondary Playmaker": 0.08},
}

_CREATION_CLASH_THRESHOLD = 0.68
_CREATION_CLASH_PENALTY   = 0.80


def _dominant_archetype(mix: dict) -> str:
    return max(mix, key=mix.get)


def _pos_group(pos: str | None) -> str:
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
      mix           — games-weighted average archetype %s (top-9 rotation)
      avg_usg_pr    — games-weighted average usg_pct_pr
      pos_counts    — {'guard': N, 'wing': N, 'big': N} in rotation
      total_vorp    — sum of vorp across all qualifying players
      star_mix      — named_mix of the highest-VORP player in rotation
      star_dominant — dominant archetype of the star
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
            "mix":      p["named_mix"],
            "games":    float(p.get("games") or 1),
            "usg_pr":   p.get("usg_pct_pr") or 0.5,
            "pos_group": _pos_group(p.get("pos")),
            "vorp":     p.get("vorp") or 0.0,
        })

    compositions = {}
    for team, players in by_team.items():
        players.sort(key=lambda x: -x["games"])
        rotation = players[:TOP_ROTATION]

        total_games = sum(pl["games"] for pl in rotation)
        if total_games == 0:
            continue

        avg = {
            arch: sum(pl["mix"].get(arch, 0) * pl["games"] for pl in rotation) / total_games
            for arch in _ARCH_ORDER
        }
        avg_usg_pr = sum(pl["usg_pr"] * pl["games"] for pl in rotation) / total_games

        pos_counts: dict[str, int] = {"guard": 0, "wing": 0, "big": 0}
        for pl in rotation:
            pos_counts[pl["pos_group"]] += 1

        # Star = highest VORP in rotation (team identity anchor for pairing score)
        star = max(rotation, key=lambda x: x["vorp"])
        star_mix = star["mix"]

        compositions[team] = {
            "mix":          avg,
            "avg_usg_pr":   avg_usg_pr,
            "pos_counts":   pos_counts,
            "total_vorp":   vorp_by_team.get(team, 0.0),
            "star_mix":     star_mix,
            "star_dominant": _dominant_archetype(star_mix) if star_mix else None,
        }
    return compositions


def _league_avg(compositions: dict) -> dict:
    avg = {a: 0.0 for a in _ARCH_ORDER}
    n = len(compositions)
    if n == 0:
        return avg
    for info in compositions.values():
        for arch in _ARCH_ORDER:
            avg[arch] += info["mix"].get(arch, 0)
    return {a: v / n for a, v in avg.items()}


def _fit_score(
    player_mix: dict,
    team_info: dict,
    league_avg: dict,
    player_usg_pr: float = 0.5,
    player_pos_group: str = "wing",
) -> tuple[float, str]:
    team_mix      = team_info["mix"]
    team_usg_pr   = team_info.get("avg_usg_pr", 0.5)
    pos_counts    = team_info.get("pos_counts", {})
    star_mix      = team_info.get("star_mix", {})
    star_dominant = team_info.get("star_dominant")

    player_primary = _dominant_archetype(player_mix)

    # 1. Gap filling
    raw = 0.0
    gap_contributions: list[tuple[float, str]] = []
    for arch in _ARCH_ORDER:
        p_pct  = player_mix.get(arch, 0)
        t_pct  = team_mix.get(arch, 0)
        la_pct = league_avg.get(arch, 0)
        gap = la_pct - t_pct
        contribution = p_pct * max(gap, 0) / 100
        if t_pct > _SATURATION_THRESHOLD and gap < 0:
            contribution *= _SATURATION_PENALTY
        gap_contributions.append((contribution, arch))
        raw += contribution

    # 2. Complementarity: team composition → player fit (one-directional)
    comp_bonus = 0.0
    for arch, t_pct in team_mix.items():
        if t_pct < 10:
            continue
        for p_arch, p_weight in player_mix.items():
            rules = _COMPLEMENTARITY.get(p_arch, {})
            if arch in rules:
                comp_bonus += rules[arch] * (t_pct / 100) * (p_weight / 100)
    raw += comp_bonus * 50

    # 3. Bidirectional effectiveness: team composition → player performs better
    eff_bonus = 0.0
    for p_arch, p_weight in player_mix.items():
        effectiveness = _PLAYER_EFFECTIVENESS.get(p_arch, {})
        for team_arch, team_pct in team_mix.items():
            if team_arch in effectiveness:
                eff_bonus += effectiveness[team_arch] * (team_pct / 100) * (p_weight / 100)
    raw += eff_bonus * 40

    # 4. Star pairing: player vs the team's primary anchor specifically
    if star_dominant and star_mix:
        star_bonus = 0.0
        star_pct = star_mix.get(star_dominant, 0)
        for p_arch, p_weight in player_mix.items():
            rules = _COMPLEMENTARITY.get(p_arch, {})
            if star_dominant in rules:
                star_bonus += rules[star_dominant] * (star_pct / 100) * (p_weight / 100)
        raw += star_bonus * 35

    # 5. Creation clash penalty
    creation_clash = (
        player_usg_pr > _CREATION_CLASH_THRESHOLD
        and team_usg_pr > _CREATION_CLASH_THRESHOLD
    )
    if creation_clash:
        raw *= _CREATION_CLASH_PENALTY

    # 6. Continuous minutes/crowding factor (replaces binary threshold)
    # 0-2 players at position: no effect; each additional player above 2 costs 12%
    pos_count = pos_counts.get(player_pos_group, 0)
    minutes_factor = max(0.55, 1.0 - max(0, pos_count - 2) * 0.12)
    raw *= minutes_factor

    # Reason string
    gap_contributions.sort(key=lambda x: -x[0])
    top_arch = gap_contributions[0][1] if gap_contributions else player_primary
    team_weak_on = [
        arch for arch in _ARCH_ORDER
        if (league_avg.get(arch, 0) - team_mix.get(arch, 0)) > 3
    ]

    pos_crowded = pos_count >= 4
    if creation_clash:
        reason = f"Ball-dominant roster — may clash with {player_primary} role"
    elif pos_crowded:
        reason = f"Crowded at {player_pos_group} — fewer minutes available"
    elif star_dominant and player_primary in (_COMPLEMENTARITY.get(star_dominant, {})):
        reason = f"{player_primary} pairs well with the team's {star_dominant} star"
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
    Returns top_n team fit dicts:
      {team, city, name, fit_score, reason, team_needs, contender, player_primary}
    """
    compositions = _build_team_compositions(pool, season, exclude_player_id=player_id)
    if not compositions:
        return []

    la = _league_avg(compositions)
    player_pos_group = _pos_group(player_pos)

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
            "team":           abbrev,
            "city":           city,
            "name":           name,
            "_raw":           raw_score,
            "reason":         reason,
            "team_needs":     top_gaps,
            "contender":      team_info["total_vorp"] >= contender_cutoff,
            "player_primary": _dominant_archetype(player_mix),
        })

    raw_results.sort(key=lambda r: -r["_raw"])

    # Span-based normalization across all 30 teams.
    # Best team → 90, worst team → 35, linear in between.
    # Raw scores can be negative (when penalties outweigh bonuses) — that's fine,
    # span normalization handles it without any division-by-near-zero risk.
    max_raw = raw_results[0]["_raw"]
    min_raw = raw_results[-1]["_raw"]
    span = max(max_raw - min_raw, 1e-6)

    results = []
    for r in raw_results[:top_n]:
        normalized = 35 + 55 * (r["_raw"] - min_raw) / span
        r["fit_score"] = round(min(90, normalized), 1)
        del r["_raw"]
        results.append(r)

    return results
