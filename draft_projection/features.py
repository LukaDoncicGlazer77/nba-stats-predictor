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
    "dreb_pct", "usg_pct", "per", "ws_per40", "obpm", "dbpm", "bpm",
]


def _cbb_row_to_features(r: dict) -> dict:
    out = {k: r.get(k) for k in _CBB_RAW_COLS if k not in ("per", "ws_per40", "obpm", "dbpm", "bpm")}
    out["college_per"] = r.get("per")
    out["college_ws_per40"] = r.get("ws_per40")
    out["college_obpm"] = r.get("obpm")
    out["college_dbpm"] = r.get("dbpm")
    out["college_bpm"] = r.get("bpm")
    out["height_in"] = r.get("height_in")
    out["weight_lb"] = r.get("weight_lb")
    out["class_year_numeric"] = class_year_to_numeric(r.get("class_year"))
    out["position_group"] = position_to_group(r.get("position"))
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
        from server import normalize_name_for_match
        key = normalize_name_for_match(player_name)
        try:
            rows = q(conn, f"""
                SELECT {", ".join(_CBB_RAW_COLS)}, class_year, position, height_in, weight_lb, academic_year
                FROM archive_cbb_player_stats
                WHERE name_key = ?
                ORDER BY academic_year DESC
            """, (key,))
        except Exception as exc:
            log.warning("SportsReferenceCBBProvider query failed for %s: %s", player_name, exc)
            return {}
        candidates = [(r["academic_year"], _cbb_row_to_features(dict(r))) for r in rows]
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
                    SELECT name_key, {", ".join(_CBB_RAW_COLS)}, class_year, position,
                           height_in, weight_lb, academic_year
                    FROM archive_cbb_player_stats
                    WHERE name_key = ANY(%s)
                    ORDER BY name_key, academic_year DESC
                """, (list(name_keys),))
            else:
                rows = q(conn, f"""
                    SELECT name_key, {", ".join(_CBB_RAW_COLS)}, class_year, position,
                           height_in, weight_lb, academic_year
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
# doesn't. College production/efficiency/role/advanced features only come
# from college-stats providers; physical profile additionally falls back to
# career info (historical players) or the draft board (pre-draft prospects),
# so physical signal isn't entirely dependent on either scraper having run.
PROVIDERS: list[CollegeStatsProvider] = [
    SportsReferenceCBBProvider(), NCAAStatsProvider(), CareerInfoProvider(), DraftBoardProvider(),
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
    bulk path currently knows about exactly three lookup-key shapes
    (name_key for cbb_stats/ncaa_stats, player_id for career_info). A future
    provider that wants the bulk path needs one more `if bulk_tables.get(...)`
    branch here -- a small, explicit addition, not a refactor. A provider
    that skips this (like DraftBoardProvider) still works correctly via the
    per-player build_feature_vector path; it just won't be fast at
    pool-building scale.
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
        vectors.append(_raw_to_vector(raw, req.get("age_at_draft"), req.get("overall_pick")))
    return vectors
