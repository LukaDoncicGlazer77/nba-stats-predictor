"""
Explainability for a single prospect's career projection: strengths,
weaknesses, development indicators, and risk indicators.

Same philosophy as archetype_engine.explain_comp() and the NCAA scraper's
comp explanations elsewhere in this codebase: a bank of independent signal
checks, each firing only when the underlying number actually crosses a
documented threshold, assembled into whichever subset is actually notable
for this specific prospect -- not a fixed template repeated for everyone.
"""
from __future__ import annotations

from typing import Optional

from draft_projection.features import FeatureVector

# (label, check_fn) -- check_fn(values, missing) -> Optional[str], None if not notable.
StrengthCheck = tuple


def _v(values: dict, name: str) -> float:
    return values.get(name, 0.0)


def _present(missing: dict, name: str) -> bool:
    return not missing.get(name, True)


def strengths(fv: FeatureVector) -> list[str]:
    out = []
    v, m = fv.values, fv.missing
    if _present(m, "ts_pct") and _v(v, "ts_pct") >= 0.60:
        out.append(f"Elite scoring efficiency (TS% {_v(v,'ts_pct')*100:.1f}) for the volume/role.")
    if _present(m, "ast_pct") and _v(v, "ast_pct") >= 28:
        out.append(f"High-level playmaking for the position (AST% {_v(v,'ast_pct'):.1f}).")
    if _present(m, "usg_pct") and _v(v, "usg_pct") >= 28 and _present(m, "ts_pct") and _v(v, "ts_pct") >= 0.55:
        out.append("Produces efficiently even while carrying a heavy offensive workload -- a real signal of translatable offensive skill, not just empty volume.")
    if _present(m, "fg3_pct") and _v(v, "fg3_pct") >= 0.38:
        out.append(f"Efficient 3-point shooter (3P% {_v(v,'fg3_pct')*100:.1f}), a skill that tends to translate directly to the NBA level.")
    if _present(m, "blk_per40") and _v(v, "blk_per40") >= 2.5:
        out.append("Real rim-protection signal (blocks per-40) for an interior/wing defender.")
    if _present(m, "stl_per40") and _v(v, "stl_per40") >= 2.0:
        out.append("Disruptive on-ball/event-creating defender (steals per-40).")
    if _present(m, "age_at_draft") and _v(v, "age_at_draft") <= 19.0:
        out.append("Notably young for the draft class relative to production -- more developmental runway than most peers at a similar level.")
    return out


def weaknesses(fv: FeatureVector) -> list[str]:
    out = []
    v, m = fv.values, fv.missing
    pos = _v(v, "position_group")  # 1=G, 2=F, 3=C (default 2 if missing)

    # ── existing red-flag signals ──────────────────────────────────────────────
    if _present(m, "ts_pct") and _v(v, "ts_pct") < 0.50 and _present(m, "usg_pct") and _v(v, "usg_pct") >= 25:
        out.append(f"Inefficient on real offensive volume (TS% {_v(v,'ts_pct')*100:.1f} on {_v(v,'usg_pct'):.1f}% usage) -- a real translation risk, not just a small-sample blip.")
    if _present(m, "tov_pct") and _v(v, "tov_pct") >= 22:
        out.append("High turnover rate relative to usage -- decision-making/ball security is a real question mark.")
    if _present(m, "ft_pct") and _v(v, "ft_pct") < 0.65:
        out.append(f"Poor free-throw shooting (FT% {_v(v,'ft_pct')*100:.1f}) -- a reliable indicator of shooting touch that tends to predict NBA 3-point/FT performance.")
    if _present(m, "age_at_draft") and _v(v, "age_at_draft") >= 22.5:
        out.append("Old for the draft class -- less developmental runway, and production needs to be judged against a younger peer's projection curve, not taken at face value.")

    # ── 3-point shooting ───────────────────────────────────────────────────────
    if pos < 2.5 and _present(m, "fg3_pct") and _v(v, "fg3_pct") < 0.33:
        out.append(
            f"Below-average 3-point shooting ({_v(v,'fg3_pct')*100:.1f}%) for a perimeter player. "
            "NBA defenses shade hard toward non-shooters off screens and in DHO actions; this limits "
            "playability in modern spacing-dependent offenses."
        )

    # ── guard playmaking vs usage ──────────────────────────────────────────────
    if pos < 1.5 and _present(m, "usg_pct") and _v(v, "usg_pct") >= 22 and _present(m, "ast_pct") and _v(v, "ast_pct") < 18:
        out.append(
            f"High usage ({_v(v,'usg_pct'):.1f}%) but below-average assist rate (AST% {_v(v,'ast_pct'):.1f}) "
            "for a guard -- primarily a self-creation scorer who doesn't consistently elevate teammates. "
            "Limits ceiling as a primary initiator at the next level."
        )

    # ── big-man rebounding ─────────────────────────────────────────────────────
    if pos > 1.5 and _present(m, "dreb_pct") and _v(v, "dreb_pct") < 14:
        out.append(
            f"Below-average defensive rebounding rate (DREB% {_v(v,'dreb_pct'):.1f}) for a big. "
            "Consistent boardwork is one of the most transferable college-to-NBA skills -- "
            "low rates here limit projected role as a frontcourt anchor."
        )

    # ── rim protection for centers ─────────────────────────────────────────────
    if pos > 2.5 and _present(m, "blk_per40") and _v(v, "blk_per40") < 1.2:
        out.append(
            f"Limited shot-blocking production ({_v(v,'blk_per40'):.1f} blocks/40 min) for a center. "
            "Rim protection is the highest-leverage defensive contribution a big can provide; "
            "no meaningful signal here reduces projected defensive value at the next level."
        )

    # ── moderate turnover concern (below the red-flag threshold but notable) ───
    if _present(m, "tov_pct") and 17 <= _v(v, "tov_pct") < 22:
        out.append(
            f"Elevated turnover rate (TOV% {_v(v,'tov_pct'):.1f}) -- not disqualifying but worth monitoring, "
            "especially if the player is expected to handle heavier ball-handling responsibility at the next level."
        )

    # ── negative college all-around impact ─────────────────────────────────────
    if _present(m, "college_bpm") and _v(v, "college_bpm") < 0:
        out.append(
            f"Negative college BPM ({_v(v,'college_bpm'):.1f}) -- all-in-one metrics don't register a net-positive "
            "impact at the college level, suggesting the raw stat line doesn't translate cleanly "
            "to a winning contribution in aggregate."
        )

    return out


def development_indicators(fv: FeatureVector) -> list[str]:
    out = []
    v, m = fv.values, fv.missing
    if _present(m, "class_year_numeric") and _v(v, "class_year_numeric") <= 1.5 and _present(m, "usg_pct") and _v(v, "usg_pct") >= 25:
        out.append("Carried a significant offensive role as an underclassman -- typically a positive developmental signal (was trusted with volume early).")
    if _present(m, "oreb_pct") and _v(v, "oreb_pct") >= 8:
        out.append("Strong offensive-rebounding rate -- often translates to a tangible specific NBA skill (extra possessions) even for players who don't carry a high-usage offensive role.")
    return out


def risk_indicators(fv: FeatureVector, *, missing_data_count: Optional[int] = None) -> list[str]:
    out = []
    v, m = fv.values, fv.missing
    total_missing = missing_data_count if missing_data_count is not None else sum(1 for x in m.values() if x)
    if total_missing >= len(m) * 0.6:
        out.append(
            "Most of this prospect's college statistical profile is unavailable (no scraped college "
            "data matched) -- this projection is leaning heavily on physical profile and draft "
            "context alone and should be treated as low-confidence until real college stats are loaded."
        )
    if _present(m, "ft_pct") and _v(v, "ft_pct") < 0.60 and _present(m, "fg3_pct") and _v(v, "fg3_pct") < 0.30:
        out.append("Shooting indicators (FT% and 3P%) both point the same negative direction -- real spacing/shooting risk at the next level.")
    return out


def build_explainability(fv: FeatureVector) -> dict:
    return {
        "strengths": strengths(fv),
        "weaknesses": weaknesses(fv),
        "development_indicators": development_indicators(fv),
        "risk_indicators": risk_indicators(fv),
    }
