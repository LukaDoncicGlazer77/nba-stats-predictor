"""
Declarative, extensible feature schema for the draft career projection
system.

Adding a future college dataset means writing one new CollegeStatsProvider
subclass and adding it to PROVIDERS below -- nothing else in this package
(comp_engine, archetype_adapter, train_career_projection_model.py,
server.py) needs to change, since every consumer goes through
build_feature_vector() rather than querying a specific source table.

Draft position is deliberately represented by a single coarse
`draft_slot_tier` bucket (top-5 / lottery / first-round / second-round-or-
undrafted), never the exact pick number -- per design direction, draft slot
is contextual information for the model, not a primary predictor. A model
fed the exact pick number could simply learn "early picks succeed more" and
call that signal; this system exists specifically to find players whose
production/physical/archetype profile outperforms their consensus slot.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional, Protocol

log = logging.getLogger("draft_projection.features")

# ── Feature schema ───────────────────────────────────────────────────────────
# (name, category, default_value). Category drives comp-engine similarity
# weighting (see comp_engine.CATEGORY_WEIGHTS) and is informational for the
# model. Every feature automatically gets a paired "<name>_missing" column
# in the training/inference row so the model can learn to discount
# predictions built on imputed defaults.
FEATURE_SPECS = [
    ("age_at_draft", "physical", 19.5),
    ("height_in", "physical", 78.0),
    ("weight_lb", "physical", 210.0),

    ("pts_per40", "production", 0.0),
    ("reb_per40", "production", 0.0),
    ("ast_per40", "production", 0.0),
    ("stl_per40", "production", 0.0),
    ("blk_per40", "production", 0.0),
    ("stl_pct", "production", 0.0),
    ("blk_pct", "production", 0.0),
    ("tov_per40", "production", 0.0),
    ("fg_pct", "production", 0.0),
    ("fg3_pct", "production", 0.0),
    ("ft_pct", "production", 0.0),

    ("ts_pct", "efficiency", 0.0),
    ("efg_pct", "efficiency", 0.0),
    ("ast_pct", "efficiency", 0.0),
    ("tov_pct", "efficiency", 0.0),

    ("usg_pct", "role", 0.0),
    ("oreb_pct", "role", 0.0),
    ("dreb_pct", "role", 0.0),
    ("class_year_numeric", "role", 1.0),
    ("position_group", "role", 2.0),
    ("conf_strength", "role", 1.0),

    # Real PER/Win-Shares/BPM-style college advanced metrics -- only
    # possible once a source publishes them (stats.ncaa.org explicitly
    # cannot, see ncaa_scraper.py's docstring; sports-reference.com/cbb
    # does, confirmed live 2026-06-21). WS is intentionally represented as a
    # per-40 rate (ws_per40), not the cumulative season total (ws), since a
    # cumulative stat isn't directly comparable across players with
    # different minutes -- consistent with how every other counting stat in
    # this schema is already rate-normalized.
    ("college_per", "advanced", 0.0),
    ("college_ws_per40", "advanced", 0.0),
    ("college_obpm", "advanced", 0.0),
    ("college_dbpm", "advanced", 0.0),
    ("college_bpm", "advanced", 0.0),

    ("draft_slot_tier", "draft_context", 3.0),
]
FEATURE_NAMES = [f[0] for f in FEATURE_SPECS]
FEATURE_CATEGORY = {f[0]: f[1] for f in FEATURE_SPECS}
FEATURE_DEFAULT = {f[0]: f[2] for f in FEATURE_SPECS}

# ── Conference strength ──────────────────────────────────────────────────────
# Tier multiplier applied as a feature so the model learns that identical
# production in a stronger conference is a stronger signal. Values tuned to
# roughly match KenPom's historical conference-strength rankings.
_CONF_STRENGTH = {
    "ACC": 1.15, "Big 12": 1.15, "Big Ten": 1.15, "SEC": 1.15, "Big East": 1.15,
    "Pac-12": 1.12, "Pac-10": 1.12,
    "AAC": 1.07, "Atlantic 10": 1.07, "Mountain West": 1.07, "WCC": 1.07,
    "MVC": 1.04, "CAA": 1.03, "MAC": 1.02, "MAAC": 1.01, "WAC": 1.00,
    "Horizon": 1.00, "Sun Belt": 0.98, "CUSA": 0.97, "A-Sun": 0.95,
    "Big Sky": 0.93, "America East": 0.92, "Patriot": 0.92, "OVC": 0.91,
    "NEC": 0.90, "Southland": 0.90, "SWAC": 0.88, "MEAC": 0.88,
    "Ivy": 0.94, "Summit": 0.93, "SoCon": 0.95, "Southern": 0.95,
}

_TEAM_CONF: dict[str, str] = {
    # ACC
    "Duke": "ACC", "North Carolina": "ACC", "Virginia": "ACC", "Syracuse": "ACC",
    "Louisville": "ACC", "Miami (FL)": "ACC", "Florida State": "ACC", "NC State": "ACC",
    "Clemson": "ACC", "Georgia Tech": "ACC", "Notre Dame": "ACC", "Pittsburgh": "ACC",
    "Wake Forest": "ACC", "Boston College": "ACC", "Virginia Tech": "ACC",
    "California": "ACC", "Stanford": "ACC", "SMU": "ACC", "Southern Methodist": "ACC",
    # Big Ten
    "Michigan State": "Big Ten", "Michigan": "Big Ten", "Indiana": "Big Ten",
    "Illinois": "Big Ten", "Ohio State": "Big Ten", "Purdue": "Big Ten",
    "Iowa": "Big Ten", "Wisconsin": "Big Ten", "Minnesota": "Big Ten",
    "Penn State": "Big Ten", "Northwestern": "Big Ten", "Nebraska": "Big Ten",
    "Maryland": "Big Ten", "Rutgers": "Big Ten", "UCLA": "Big Ten",
    "USC": "Big Ten", "Washington": "Big Ten", "Oregon": "Big Ten",
    "Michigan State": "Big Ten",
    # Big 12
    "Kansas": "Big 12", "Texas": "Big 12", "Baylor": "Big 12",
    "Oklahoma State": "Big 12", "Oklahoma": "Big 12", "TCU": "Big 12",
    "Texas Tech": "Big 12", "West Virginia": "Big 12", "Iowa State": "Big 12",
    "Kansas State": "Big 12", "Cincinnati": "Big 12", "Houston": "Big 12",
    "UCF": "Big 12", "BYU": "Big 12", "Brigham Young": "Big 12",
    "Utah": "Big 12", "Arizona": "Big 12", "Arizona State": "Big 12",
    "Colorado": "Big 12",
    # SEC
    "Kentucky": "SEC", "Duke": "ACC", "Alabama": "SEC", "Auburn": "SEC",
    "Florida": "SEC", "Georgia": "SEC", "LSU": "SEC", "Mississippi State": "SEC",
    "Ole Miss": "SEC", "South Carolina": "SEC", "Tennessee": "SEC",
    "Vanderbilt": "SEC", "Arkansas": "SEC", "Missouri": "SEC",
    "Mississippi": "SEC", "Texas A&M": "SEC", "Oklahoma": "SEC", "Texas": "SEC",
    # Big East
    "Georgetown": "Big East", "Connecticut": "Big East", "UConn": "Big East",
    "Villanova": "Big East", "Marquette": "Big East", "St. John's": "Big East",
    "Providence": "Big East", "Seton Hall": "Big East", "DePaul": "Big East",
    "Butler": "Big East", "Xavier": "Big East", "Creighton": "Big East",
    # Pac-12 (historical)
    "Arizona": "Pac-12", "UCLA": "Pac-12", "USC": "Pac-12",
    "Stanford": "Pac-12", "California": "Pac-12", "Oregon": "Pac-12",
    "Washington": "Pac-12", "Utah": "Pac-12", "Colorado": "Pac-12",
    "Arizona State": "Pac-12", "Oregon State": "Pac-12", "Washington State": "Pac-12",
    # AAC
    "Memphis": "AAC", "Wichita State": "AAC", "Temple": "AAC",
    "Tulsa": "AAC", "South Florida": "AAC", "Tulane": "AAC",
    "East Carolina": "AAC", "North Texas": "AAC", "UTSA": "AAC",
    # Atlantic 10
    "Saint Louis": "Atlantic 10", "VCU": "Atlantic 10", "Richmond": "Atlantic 10",
    "Dayton": "Atlantic 10", "Rhode Island": "Atlantic 10", "George Mason": "Atlantic 10",
    "La Salle": "Atlantic 10", "Fordham": "Atlantic 10", "George Washington": "Atlantic 10",
    "Massachusetts": "Atlantic 10", "UMass": "Atlantic 10", "Davidson": "Atlantic 10",
    "Saint Joseph's": "Atlantic 10", "Duquesne": "Atlantic 10",
    # Mountain West
    "UNLV": "Mountain West", "San Diego State": "Mountain West",
    "New Mexico": "Mountain West", "Utah State": "Mountain West",
    "Fresno State": "Mountain West", "Nevada": "Mountain West",
    "Colorado State": "Mountain West", "Wyoming": "Mountain West",
    "Boise State": "Mountain West", "Air Force": "Mountain West",
    # WCC
    "Gonzaga": "WCC", "Saint Mary's": "WCC", "San Francisco": "WCC",
    "Pepperdine": "WCC", "Loyola Marymount": "WCC", "Portland": "WCC",
    "Pacific": "WCC", "San Diego": "WCC", "BYU": "WCC",
    # MVC
    "Wichita State": "MVC", "Illinois State": "MVC", "Indiana State": "MVC",
    "Drake": "MVC", "Bradley": "MVC", "Evansville": "MVC",
    "Northern Iowa": "MVC", "Southern Illinois": "MVC",
    # MAC
    "Akron": "MAC", "Toledo": "MAC", "Ohio": "MAC", "Miami (OH)": "MAC",
    "Ball State": "MAC", "Central Michigan": "MAC", "Eastern Michigan": "MAC",
    "Kent State": "MAC", "Western Michigan": "MAC", "Bowling Green": "MAC",
    "Buffalo": "MAC", "Northern Illinois": "MAC",
    # Sun Belt
    "Arkansas State": "Sun Belt", "Troy": "Sun Belt", "Georgia Southern": "Sun Belt",
    "Louisiana": "Sun Belt", "South Alabama": "Sun Belt", "Texas State": "Sun Belt",
    "App State": "Sun Belt", "Appalachian State": "Sun Belt",
    "Georgia State": "Sun Belt", "Little Rock": "Sun Belt",
    # Ivy
    "Harvard": "Ivy", "Princeton": "Ivy", "Yale": "Ivy", "Penn": "Ivy",
    "Columbia": "Ivy", "Cornell": "Ivy", "Dartmouth": "Ivy", "Brown": "Ivy",
    # SoCon / Southern
    "Furman": "SoCon", "Wofford": "SoCon", "Samford": "SoCon",
    "Mercer": "SoCon", "The Citadel": "SoCon", "VMI": "SoCon",
    "Western Carolina": "SoCon", "East Tennessee State": "SoCon",
    "ETSU": "SoCon", "Chattanooga": "SoCon", "UNC Greensboro": "SoCon",
    # SWAC
    "Southern": "SWAC", "Grambling": "SWAC", "Jackson State": "SWAC",
    "Prairie View": "SWAC", "Alcorn State": "SWAC", "Alabama A&M": "SWAC",
    "Alabama State": "SWAC", "Texas Southern": "SWAC", "Bethune-Cookman": "SWAC",
    # MEAC
    "Howard": "MEAC", "Morgan State": "MEAC", "Norfolk State": "MEAC",
    "North Carolina A&T": "MEAC", "Coppin State": "MEAC", "UMES": "MEAC",
    "Maryland-Eastern Shore": "MEAC", "Delaware State": "MEAC",
    "South Carolina State": "MEAC",
    # Big Sky
    "Montana": "Big Sky", "Weber State": "Big Sky", "Eastern Washington": "Big Sky",
    "Idaho": "Big Sky", "Northern Colorado": "Big Sky", "Sacramento State": "Big Sky",
    "Montana State": "Big Sky", "North Dakota": "Big Sky", "Northern Arizona": "Big Sky",
    "Portland State": "Big Sky", "Idaho State": "Big Sky", "Southern Utah": "Big Sky",
    # Horizon
    "Milwaukee": "Horizon", "Wright State": "Horizon", "Oakland": "Horizon",
    "IUPUI": "Horizon", "Green Bay": "Horizon", "Cleveland State": "Horizon",
    "Detroit Mercy": "Horizon", "Robert Morris": "Horizon", "Northern Kentucky": "Horizon",
    # CAA
    "Delaware": "CAA", "Hofstra": "CAA", "James Madison": "CAA",
    "Northeastern": "CAA", "Drexel": "CAA", "Towson": "CAA",
    "Stony Brook": "CAA", "UNCW": "CAA", "William & Mary": "CAA",
    "Elon": "CAA", "Hampton": "CAA",
    # America East
    "Vermont": "America East", "Albany (NY)": "America East",
    "Binghamton": "America East", "Hartford": "America East",
    "Maine": "America East", "Maryland-Baltimore County": "America East",
    "UMBC": "America East", "New Hampshire": "America East",
}


def _team_conf_strength(team: Optional[str]) -> float:
    if not team:
        return 1.0
    conf = _TEAM_CONF.get(team)
    if conf:
        return _CONF_STRENGTH.get(conf, 1.0)
    # Fuzzy fallback: check if any known team name is a substring
    team_lower = team.lower()
    for known_team, conf in _TEAM_CONF.items():
        if known_team.lower() in team_lower or team_lower in known_team.lower():
            return _CONF_STRENGTH.get(conf, 1.0)
    return 1.0


CLASS_YEAR_MAP = {"FR": 1, "SO": 2, "JR": 3, "SR": 4}
POSITION_GROUP_MAP = {"G": 1, "F": 2, "C": 3}


def class_year_to_numeric(class_year) -> float:
    return CLASS_YEAR_MAP.get(str(class_year or "").strip().upper(), FEATURE_DEFAULT["class_year_numeric"])


def position_to_group(position) -> float:
    p = str(position or "").upper()
    if "C" in p:
        return POSITION_GROUP_MAP["C"]
    if "G" in p:
        return POSITION_GROUP_MAP["G"]
    if "F" in p:
        return POSITION_GROUP_MAP["F"]
    return FEATURE_DEFAULT["position_group"]


def parse_height_to_inches(text) -> Optional[float]:
    """'6-2' -> 74.0. Same feet-dash-inches format the NCAA scraper parses."""
    import re
    match = re.match(r"^\s*(\d+)\s*[-' ]\s*(\d+)", str(text or ""))
    if not match:
        return None
    feet, inches = int(match.group(1)), int(match.group(2))
    return float(feet * 12 + inches)


def parse_weight_lb(text) -> Optional[float]:
    import re
    match = re.search(r"(\d+(?:\.\d+)?)", str(text or ""))
    return float(match.group(1)) if match else None


def draft_slot_to_tier(overall_pick) -> float:
    """1=top-5, 2=lottery(6-14), 3=first round(15-30), 4=second round/UDFA.
    Deliberately coarse -- see module docstring."""
    if overall_pick is None or (isinstance(overall_pick, float) and overall_pick != overall_pick):
        return FEATURE_DEFAULT["draft_slot_tier"]
    p = float(overall_pick)
    if p <= 5:
        return 1.0
    if p <= 14:
        return 2.0
    if p <= 30:
        return 3.0
    return 4.0


# ── Provider interface ───────────────────────────────────────────────────────

class CollegeStatsProvider(Protocol):
    name: str

    def fetch(self, conn, q, player_name: str, college: Optional[str], season: Optional[int],
              player_id: Optional[str] = None) -> dict:
        """Returns a dict of (a subset of) FEATURE_NAMES -> raw value, or {}
        if this provider has no data for the prospect. player_id is optional
        since pre-draft prospects don't have one yet."""
        ...


# A college season can only ever precede (or, loosely, coincide with) a
# player's real draft -- matching a college season that happened *after*
# the player was drafted means the name matched a different, unrelated
# person, not a real data point about this player. Confirmed as a real bug
# (2026-06-22): pre-2001 NBA draftees were matching unrelated same-named
# players from the 2001-2026 college scrape at a 7.5% rate, which should
# have been ~0%. MAX_COLLEGE_TO_DRAFT_GAP_YEARS is generous (redshirts,
# grad transfers, international/late-bloomer gaps) without being unbounded.
MAX_COLLEGE_TO_DRAFT_GAP_YEARS = 6


def _select_plausible_row(candidates: list[tuple], draft_season) -> dict:
    """candidates: [(academic_year, feature_dict), ...], already ordered
    DESC by academic_year (so the first plausible match is also the most
    recent, i.e. most representative, eligible season). Returns {} if no
    candidate's academic_year plausibly precedes draft_season -- a name
    match with no temporally-plausible season is treated as no match at
    all, not silently falling back to some other person's stats.

    draft_season=None (a not-yet-drafted current prospect) skips the check
    entirely -- there's no real draft year yet to validate against, and a
    prospect's college seasons are inherently before their (future) draft."""
    if not candidates:
        return {}
    if draft_season is None:
        return candidates[0][1]
    for academic_year, feats in candidates:
        if academic_year is None:
            continue
        if academic_year <= draft_season and academic_year >= draft_season - MAX_COLLEGE_TO_DRAFT_GAP_YEARS:
            return feats
    return {}


_NCAA_RAW_COLS = [
    "pts_per40", "reb_per40", "ast_per40", "stl_per40", "blk_per40", "tov_per40",
    "fg_pct", "fg3_pct", "ft_pct", "ts_pct", "efg_pct", "ast_pct", "tov_pct", "oreb_pct",
    "dreb_pct", "usg_pct",
]


def _ncaa_row_to_features(r: dict) -> dict:
    out = {k: r.get(k) for k in _NCAA_RAW_COLS}
    out["height_in"] = r.get("height_in")
    out["weight_lb"] = r.get("weight_lb")
    out["class_year_numeric"] = class_year_to_numeric(r.get("class_year"))
    out["position_group"] = position_to_group(r.get("position"))
    return out


class NCAAStatsProvider:
    """Reads archive_ncaa_player_stats (the stats.ncaa.org scraper output).
    Matches on the same name normalization used by /api/ncaa-stats, so a
    prospect named "Cooper Flagg" matches a scraped "Flagg, Cooper" row."""
    name = "ncaa_stats"

    def fetch(self, conn, q, player_name, college=None, season=None, player_id=None) -> dict:
        from server import normalize_name_for_match
        key = normalize_name_for_match(player_name)
        try:
            rows = q(conn, f"""
                SELECT {", ".join(_NCAA_RAW_COLS)}, class_year, position, height_in, weight_lb, academic_year
                FROM archive_ncaa_player_stats
                WHERE name_key = ?
                ORDER BY academic_year DESC
            """, (key,))
        except Exception as exc:
            log.warning("NCAAStatsProvider query failed for %s: %s", player_name, exc)
            return {}
        candidates = [(r["academic_year"], _ncaa_row_to_features(dict(r))) for r in rows]
        return _select_plausible_row(candidates, season)

    def bulk_fetch_all(self, conn, q, *, name_keys=None) -> dict:
        """Used by pool-building (thousands of players) to avoid one
        round-trip per player -- a single query, grouped in Python. Returns
        ALL seasons per name_key (not just the most recent) so the caller
        can pick the one that's actually temporally plausible for a given
        player's draft season -- see _select_plausible_row.

        name_keys: if provided, fetches only those players (dramatically
        reduces memory when building a filtered pool)."""
        try:
            if name_keys:
                rows = q(conn, f"""
                    SELECT name_key, {", ".join(_NCAA_RAW_COLS)}, class_year, position,
                           height_in, weight_lb, academic_year
                    FROM archive_ncaa_player_stats
                    WHERE name_key = ANY(%s)
                    ORDER BY name_key, academic_year DESC
                """, (list(name_keys),))
            else:
                rows = q(conn, f"""
                    SELECT name_key, {", ".join(_NCAA_RAW_COLS)}, class_year, position,
                           height_in, weight_lb, academic_year
                    FROM archive_ncaa_player_stats
                    ORDER BY name_key, academic_year DESC
                """)
        except Exception as exc:
            log.warning("NCAAStatsProvider bulk fetch failed: %s", exc)
            return {}
        out: dict = {}
        for r in rows:
            key = r["name_key"]
            out.setdefault(key, []).append((r["academic_year"], _ncaa_row_to_features(dict(r))))
        return out


_CBB_RAW_COLS = [
    "pts_per40", "reb_per40", "ast_per40", "stl_per40", "blk_per40", "tov_per40",
    "fg_pct", "fg3_pct", "ft_pct", "ts_pct", "efg_pct", "ast_pct", "tov_pct", "oreb_pct",
    "dreb_pct", "usg_pct", "stl_pct", "blk_pct",
    "per", "ws_per40", "obpm", "dbpm", "bpm",
]
_CBB_ADVANCED = {"per", "ws_per40", "obpm", "dbpm", "bpm"}


def _cbb_row_to_features(r: dict) -> dict:
    out = {k: r.get(k) for k in _CBB_RAW_COLS if k not in _CBB_ADVANCED}
    out["college_per"] = r.get("per")
    out["college_ws_per40"] = r.get("ws_per40")
    out["college_obpm"] = r.get("obpm")
    out["college_dbpm"] = r.get("dbpm")
    out["college_bpm"] = r.get("bpm")
    out["height_in"] = r.get("height_in")
    out["weight_lb"] = r.get("weight_lb")
    out["class_year_numeric"] = class_year_to_numeric(r.get("class_year"))
    out["position_group"] = position_to_group(r.get("position"))
    out["conf_strength"] = _team_conf_strength(r.get("team"))
    return out


class SportsReferenceCBBProvider:
    """Reads archive_cbb_player_stats (sports-reference.com/cbb scraper
    output) -- the primary college-stats source going forward (see
    cbb_scraper.py's module docstring for why this replaced
    stats.ncaa.org as the working source). Listed ahead of NCAAStatsProvider
    in PROVIDERS so it wins ties when both have data for a player; NCAA
    stays registered as a fallback for any feature this source might lack
    for a given player/season (e.g. before this scraper existed)."""
    name = "cbb_stats"

    def fetch(self, conn, q, player_name, college=None, season=None, player_id=None) -> dict:
        from server import name_key_candidates
        for key in name_key_candidates(player_name):
            try:
                rows = q(conn, f"""
                    SELECT {", ".join(_CBB_RAW_COLS)}, class_year, position, height_in, weight_lb, team, academic_year
                    FROM archive_cbb_player_stats
                    WHERE name_key = ?
                    ORDER BY academic_year DESC
                """, (key,))
            except Exception as exc:
                log.warning("SportsReferenceCBBProvider query failed for %s: %s", player_name, exc)
                return {}
            if rows:
                candidates = [(r["academic_year"], _cbb_row_to_features(dict(r))) for r in rows]
                return _select_plausible_row(candidates, season)
        return {}

    def bulk_fetch_all(self, conn, q, *, name_keys=None) -> dict:
        """Used by pool-building (thousands of players) to avoid one
        round-trip per player -- a single query, grouped in Python. Returns
        ALL seasons per name_key (not just the most recent) so the caller
        can pick the one that's actually temporally plausible for a given
        player's draft season -- see _select_plausible_row.

        name_keys: if provided, fetches only those players (dramatically
        reduces memory when building a filtered pool)."""
        try:
            if name_keys:
                rows = q(conn, f"""
                    SELECT name_key, {", ".join(_CBB_RAW_COLS)}, class_year, position,
                           height_in, weight_lb, team, academic_year
                    FROM archive_cbb_player_stats
                    WHERE name_key = ANY(%s)
                    ORDER BY name_key, academic_year DESC
                """, (list(name_keys),))
            else:
                rows = q(conn, f"""
                    SELECT name_key, {", ".join(_CBB_RAW_COLS)}, class_year, position,
                           height_in, weight_lb, team, academic_year
                    FROM archive_cbb_player_stats
                    ORDER BY name_key, academic_year DESC
                """)
        except Exception as exc:
            log.warning("SportsReferenceCBBProvider bulk fetch failed: %s", exc)
            return {}
        out: dict = {}
        for r in rows:
            key = r["name_key"]
            out.setdefault(key, []).append((r["academic_year"], _cbb_row_to_features(dict(r))))
        return out


class CareerInfoProvider:
    """Physical profile (height/weight/position) for players who are already
    in archive_player_career_info -- i.e. historical players, used when
    building the training pool. Doesn't help pre-draft 2026 prospects (they
    aren't in this table yet); DraftBoardProvider below covers that case.
    Matters because it means physical-profile features have real signal in
    the historical pool even while archive_ncaa_player_stats is empty."""
    name = "career_info"

    def fetch(self, conn, q, player_name, college=None, season=None, player_id=None) -> dict:
        if not player_id:
            return {}
        try:
            rows = q(conn, """
                SELECT ht_in_in, wt, pos FROM archive_player_career_info WHERE player_id = ?
            """, (player_id,))
        except Exception as exc:
            log.warning("CareerInfoProvider query failed for %s: %s", player_name, exc)
            return {}
        if not rows:
            return {}
        return self._row_to_features(dict(rows[0]))

    def bulk_fetch_all(self, conn, q, *, name_keys=None) -> dict:
        try:
            rows = q(conn, "SELECT player_id, ht_in_in, wt, pos FROM archive_player_career_info")
        except Exception as exc:
            log.warning("CareerInfoProvider bulk fetch failed: %s", exc)
            return {}
        return {r["player_id"]: self._row_to_features(dict(r)) for r in rows}

    @staticmethod
    def _row_to_features(r: dict) -> dict:
        out = {}
        try:
            out["height_in"] = float(r["ht_in_in"]) if r.get("ht_in_in") else None
        except (TypeError, ValueError):
            out["height_in"] = None
        try:
            out["weight_lb"] = float(r["wt"]) if r.get("wt") else None
        except (TypeError, ValueError):
            out["weight_lb"] = None
        if r.get("pos"):
            out["position_group"] = position_to_group(r["pos"])
        return out


# Pre-draft scouting-board tables, most recent first. A future draft class's
# board (e.g. archive_draft_prospects_2027) is added here -- one line, no
# other code changes, since DraftBoardProvider tries each in turn.
DRAFT_BOARD_TABLES = ["archive_draft_prospects_2026"]


class DraftBoardProvider:
    """Physical profile + position for *pre-draft* prospects, from the
    scouting-board table(s) server.py's /api/prospects already reads.
    The only physical-profile source available for a prospect who hasn't
    been drafted yet (and so has no archive_player_career_info row)."""
    name = "draft_board"

    def fetch(self, conn, q, player_name, college=None, season=None, player_id=None) -> dict:
        for table in DRAFT_BOARD_TABLES:
            try:
                rows = q(conn, f"SELECT pos, age, height, weight FROM {table} WHERE name = ?", (player_name,))
            except Exception as exc:
                log.warning("DraftBoardProvider query against %s failed for %s: %s", table, player_name, exc)
                continue
            if rows:
                r = dict(rows[0])
                out = {
                    "height_in": parse_height_to_inches(r.get("height")),
                    "weight_lb": parse_weight_lb(r.get("weight")),
                }
                if r.get("pos"):
                    out["position_group"] = position_to_group(r["pos"])
                try:
                    out["age_at_draft"] = float(r["age"]) if r.get("age") else None
                except (TypeError, ValueError):
                    pass
                return out
        return {}


# Providers are tried in order; the first provider with a real (non-None)
# value for a given feature wins it, so multiple college datasets can
# coexist (e.g. a future international-league provider) instead of one
# being forced to fully replace another. Adding a dataset = appending here.
# SportsReferenceCBBProvider is listed first -- it's the richer, currently
# working source (real PER/WS/BPM, weight); NCAAStatsProvider stays
# registered as a fallback in case it ever has a player/season the other
_NBA_ADV_COLS_NOVICE = [
    "player_id", "season",
    "usg_percent", "ast_percent", "blk_percent", "drb_percent",
    "stl_percent", "ts_percent", "tov_percent",
    "per", "ws_48", "bpm", "obpm", "dbpm",
]
_NBA_PG_COLS_NOVICE = [
    "player_id", "season",
    "pts_per_game", "trb_per_game", "ast_per_game",
    "stl_per_game", "blk_per_game", "tov_per_game",
    "fg_percent", "x3p_percent", "ft_percent",
    "mp_per_game", "g",
]


def _sf(v) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _nba_season_to_features(adv: dict, pg: dict) -> dict:
    """Convert one NBA season's advanced + per-game rows to the CBB feature schema."""
    out = {}
    # Rate stats: both NBA (archive_advanced) and CBB store these as percentages (e.g. 25.3)
    for nba_col, feat in [
        ("usg_percent", "usg_pct"), ("ast_percent", "ast_pct"),
        ("blk_percent", "blk_pct"), ("drb_percent", "dreb_pct"),
        ("stl_percent", "stl_pct"), ("tov_percent", "tov_pct"),
    ]:
        v = _sf(adv.get(nba_col))
        if v is not None:
            out[feat] = v
    # TS% stored as decimal (0.567) in both NBA and CBB
    ts = _sf(adv.get("ts_percent"))
    if ts is not None:
        out["ts_pct"] = ts
    # Shooting percentages — decimal in both
    for nba_col, feat in [("fg_percent", "fg_pct"), ("x3p_percent", "fg3_pct"), ("ft_percent", "ft_pct")]:
        v = _sf(pg.get(nba_col))
        if v is not None:
            out[feat] = v
    # Per-40 counting stats (per-game * 40 / mpg)
    mpg = _sf(pg.get("mp_per_game"))
    if mpg and mpg > 0:
        for nba_col, feat in [
            ("pts_per_game", "pts_per40"), ("trb_per_game", "reb_per40"),
            ("ast_per_game", "ast_per40"), ("stl_per_game", "stl_per40"),
            ("blk_per_game", "blk_per40"), ("tov_per_game", "tov_per40"),
        ]:
            v = _sf(pg.get(nba_col))
            if v is not None:
                out[feat] = v * 40.0 / mpg
    # Advanced metrics — PER, WS/40 (from WS/48), BPM family
    per_v = _sf(adv.get("per"))
    if per_v is not None:
        out["college_per"] = per_v
    ws48 = _sf(adv.get("ws_48"))
    if ws48 is not None:
        out["college_ws_per40"] = ws48 * (40.0 / 48.0)
    for nba_col, feat in [("obpm", "college_obpm"), ("dbpm", "college_dbpm"), ("bpm", "college_bpm")]:
        v = _sf(adv.get(nba_col))
        if v is not None:
            out[feat] = v
    return out


def _average_nba_season_features(season_triples: list[tuple]) -> dict:
    """Minutes-weighted average of feature dicts across multiple NBA seasons.
    season_triples: [(season_int, adv_dict, pg_dict), ...]"""
    weighted: dict = {}
    total_weight = 0.0
    for _, adv, pg in season_triples:
        feats = _nba_season_to_features(adv, pg)
        if not feats:
            continue
        g = _sf(pg.get("g")) or 0.0
        mpg = _sf(pg.get("mp_per_game")) or 0.0
        weight = g * mpg  # total minutes as weight
        if weight <= 0:
            weight = 1.0
        total_weight += weight
        for k, v in feats.items():
            weighted[k] = weighted.get(k, 0.0) + v * weight
    if total_weight <= 0:
        return {}
    return {k: v / total_weight for k, v in weighted.items()}


class NBANoviceProvider:
    """NBA rookie-season proxy for prep-to-pro draftees who skipped college
    (KG, Kobe, LeBron, McGrady, Dwight Howard, etc.) — without this
    provider, their feature vectors are 65% default, making them invisible
    as comps against college prospects.

    Uses first 1-3 NBA seasons (draft_season+1 through +3) mapped to the
    CBB feature schema. Rate stats (USG%, AST%, TS%, BPM, PER) are used
    directly — they're league-relative and comparable across college/NBA.
    Per-40 counting stats reflect a slightly different scoring environment
    but are still useful signal.

    CBB providers are listed earlier in PROVIDERS, so their values always
    win — this only fills in features still None after CBB lookups fail.
    Bulk path fetches all NBA seasons unfiltered (NBA tables are ~50k rows,
    far smaller than the 275k-row CBB table, so no memory concern)."""
    name = "nba_novice"

    def fetch(self, conn, q, player_name, college=None, season=None, player_id=None) -> dict:
        if not player_id or season is None:
            return {}
        draft_season = int(season)
        min_s, max_s = draft_season + 1, draft_season + 3
        try:
            adv_rows = q(conn, f"""
                SELECT {', '.join(_NBA_ADV_COLS_NOVICE)} FROM archive_advanced
                WHERE player_id = ? AND season::int BETWEEN ? AND ?
            """, (player_id, min_s, max_s))
            pg_rows = q(conn, f"""
                SELECT {', '.join(_NBA_PG_COLS_NOVICE)} FROM archive_player_per_game
                WHERE player_id = ? AND season::int BETWEEN ? AND ?
            """, (player_id, min_s, max_s))
        except Exception as exc:
            log.warning("NBANoviceProvider.fetch failed for %s: %s", player_name, exc)
            return {}
        adv_by_s = {int(r["season"]): dict(r) for r in adv_rows if r.get("season")}
        # Prefer the row with most games (TOT row for traded players)
        pg_by_s: dict = {}
        for r in pg_rows:
            try:
                s = int(r["season"])
            except (TypeError, ValueError):
                continue
            existing = pg_by_s.get(s)
            if existing is None or (_sf(r.get("g")) or 0) > (_sf(existing.get("g")) or 0):
                pg_by_s[s] = dict(r)
        seasons = sorted(set(adv_by_s) | set(pg_by_s))
        triples = [(s, adv_by_s.get(s, {}), pg_by_s.get(s, {})) for s in seasons]
        return _average_nba_season_features(triples)

    def bulk_fetch_all(self, conn, q, *, name_keys=None) -> dict:
        """Returns {player_id: [(season_int, adv_dict, pg_dict), ...]} for every
        player in archive_advanced / archive_player_per_game. Caller filters to
        the draft-season window [draft_season+1, draft_season+3]."""
        try:
            adv_rows = q(conn, f"SELECT {', '.join(_NBA_ADV_COLS_NOVICE)} FROM archive_advanced")
            pg_rows = q(conn, f"SELECT {', '.join(_NBA_PG_COLS_NOVICE)} FROM archive_player_per_game")
        except Exception as exc:
            log.warning("NBANoviceProvider bulk fetch failed: %s", exc)
            return {}
        adv_by: dict = {}
        for r in adv_rows:
            try:
                s = int(r["season"])
            except (TypeError, ValueError):
                continue
            adv_by.setdefault(r["player_id"], {})[s] = dict(r)
        pg_by: dict = {}
        for r in pg_rows:
            try:
                s = int(r["season"])
            except (TypeError, ValueError):
                continue
            pid = r["player_id"]
            existing = pg_by.setdefault(pid, {}).get(s)
            if existing is None or (_sf(r.get("g")) or 0) > (_sf(existing.get("g")) or 0):
                pg_by[pid] = pg_by.get(pid, {})
                pg_by[pid][s] = dict(r)
        all_ids = set(adv_by) | set(pg_by)
        out: dict = {}
        for pid in all_ids:
            seasons = sorted(set(adv_by.get(pid, {})) | set(pg_by.get(pid, {})))
            out[pid] = [(s, adv_by.get(pid, {}).get(s, {}), pg_by.get(pid, {}).get(s, {}))
                        for s in seasons]
        return out


# doesn't. College production/efficiency/role/advanced features only come
# from college-stats providers; physical profile additionally falls back to
# career info (historical players) or the draft board (pre-draft prospects),
# so physical signal isn't entirely dependent on either scraper having run.
PROVIDERS: list[CollegeStatsProvider] = [
    SportsReferenceCBBProvider(), NCAAStatsProvider(), CareerInfoProvider(), DraftBoardProvider(),
    NBANoviceProvider(),
]


@dataclass
class FeatureVector:
    values: dict = field(default_factory=dict)
    missing: dict = field(default_factory=dict)

    def as_row(self) -> dict:
        row = dict(self.values)
        for name, is_missing in self.missing.items():
            row[f"{name}_missing"] = 1.0 if is_missing else 0.0
        return row


def _raw_to_vector(raw: dict, age_at_draft: Optional[float], overall_pick: Optional[float]) -> FeatureVector:
    raw = dict(raw)
    if age_at_draft is not None:
        raw["age_at_draft"] = age_at_draft

    # If age_at_draft is still missing but we have class_year, estimate it.
    # A freshman entering college is ~18; add class_year to get typical draft age.
    # This prevents the age_at_draft_missing flag (41% feature importance in stage1)
    # from dominating the bust prediction for all current prospects.
    if raw.get("age_at_draft") is None:
        cy = raw.get("class_year_numeric")
        if cy is not None and not (isinstance(cy, float) and cy != cy):
            raw["age_at_draft"] = 18.0 + float(cy)

    # Similarly, if weight_lb is missing but we have real college stats,
    # use the median rather than marking it missing — weight_lb_missing has
    # 32% importance in stage1 and was a label-leak during training.
    has_college_signal = raw.get("pts_per40") is not None or raw.get("ast_per40") is not None
    if raw.get("weight_lb") is None and has_college_signal:
        raw["weight_lb"] = FEATURE_DEFAULT["weight_lb"]

    raw["draft_slot_tier"] = draft_slot_to_tier(overall_pick)

    values, missing = {}, {}
    for name in FEATURE_NAMES:
        v = raw.get(name)
        if v is None or (isinstance(v, float) and v != v):
            values[name] = FEATURE_DEFAULT[name]
            missing[name] = True
        else:
            values[name] = float(v)
            missing[name] = False
    return FeatureVector(values=values, missing=missing)


def build_feature_vector(conn, q, *, player_name: str, college: Optional[str] = None,
                          season: Optional[int] = None, age_at_draft: Optional[float] = None,
                          overall_pick: Optional[float] = None,
                          player_id: Optional[str] = None) -> FeatureVector:
    """Single entry point from a prospect's identity to a feature vector, for
    one-at-a-time lookups (e.g. the serving endpoint, N=1). Every consumer
    should call this or bulk_build_feature_vectors() rather than querying
    provider tables directly -- that's what makes adding a provider a
    one-file change."""
    raw: dict = {}
    for provider in PROVIDERS:
        try:
            fetched = provider.fetch(conn, q, player_name, college, season, player_id=player_id)
        except Exception as exc:
            log.warning("Provider %s failed for %s: %s", provider.name, player_name, exc)
            fetched = {}
        for k, v in fetched.items():
            if k not in raw or raw[k] is None:
                raw[k] = v
    return _raw_to_vector(raw, age_at_draft, overall_pick)


def bulk_build_feature_vectors(conn, q, requests: list[dict], *, allowed_name_keys=None) -> list[FeatureVector]:
    """Same merge logic as build_feature_vector, but for many prospects at
    once (e.g. building the ~8,000-player historical comp pool) -- uses each
    provider's bulk_fetch_all() (one query total per provider) instead of
    one round-trip per player per provider, which would take many minutes
    against a remote DB connection. Providers without a bulk path (e.g.
    DraftBoardProvider, only ever needed for a handful of current prospects)
    are simply skipped here -- they still work fine via build_feature_vector.

    Each request dict: {player_name, college, season, age_at_draft,
    overall_pick, player_id}.

    Note: unlike build_feature_vector (fully generic over PROVIDERS), this
    bulk path currently knows about four lookup-key shapes: name_key for
    cbb_stats/ncaa_stats, player_id for career_info, and player_id+draft_season
    for nba_novice. A future provider that wants the bulk path needs one more
    branch here. A provider that skips this (like DraftBoardProvider) still
    works correctly via the per-player build_feature_vector path; it just
    won't be fast at pool-building scale.
    """
    from server import normalize_name_for_match

    bulk_tables = {}
    for provider in PROVIDERS:
        bulk_fetch = getattr(provider, "bulk_fetch_all", None)
        if bulk_fetch is None:
            continue
        try:
            bulk_tables[provider.name] = bulk_fetch(conn, q, name_keys=allowed_name_keys)
        except Exception as exc:
            log.warning("Provider %s bulk fetch failed, skipping: %s", provider.name, exc)
            bulk_tables[provider.name] = {}

    vectors = []
    for req in requests:
        raw: dict = {}
        draft_season = req.get("season")
        # Checked before ncaa_stats so it wins ties, matching PROVIDERS' order.
        # Each table maps name_key -> [(academic_year, feature_dict), ...];
        # _select_plausible_row rejects any candidate season that couldn't
        # really precede this player's draft_season, so a name collision
        # with an unrelated later-era player doesn't get matched in.
        cbb_table = bulk_tables.get("cbb_stats")
        if cbb_table:
            candidates = cbb_table.get(normalize_name_for_match(req.get("player_name", ""))) or []
            fetched = _select_plausible_row(candidates, draft_season)
            for k, v in fetched.items():
                if k not in raw or raw[k] is None:
                    raw[k] = v
        ncaa_table = bulk_tables.get("ncaa_stats")
        if ncaa_table:
            candidates = ncaa_table.get(normalize_name_for_match(req.get("player_name", ""))) or []
            fetched = _select_plausible_row(candidates, draft_season)
            for k, v in fetched.items():
                if k not in raw or raw[k] is None:
                    raw[k] = v
        career_table = bulk_tables.get("career_info")
        if career_table and req.get("player_id"):
            fetched = career_table.get(req["player_id"]) or {}
            for k, v in fetched.items():
                if k not in raw or raw[k] is None:
                    raw[k] = v
        # NBA novice proxy: fills in production/advanced features for prep-to-pro
        # draftees (no CBB data). CBB values already in raw always win because they
        # were set earlier; this only populates keys that are still None.
        nba_novice_table = bulk_tables.get("nba_novice")
        if nba_novice_table and req.get("player_id") and draft_season:
            player_seasons = nba_novice_table.get(req["player_id"]) or []
            min_s, max_s = int(draft_season) + 1, int(draft_season) + 3
            relevant = [(s, adv, pg) for s, adv, pg in player_seasons if min_s <= s <= max_s]
            if relevant:
                fetched = _average_nba_season_features(relevant)
                for k, v in fetched.items():
                    if k not in raw or raw[k] is None:
                        raw[k] = v
        vectors.append(_raw_to_vector(raw, req.get("age_at_draft"), req.get("overall_pick")))
    return vectors
