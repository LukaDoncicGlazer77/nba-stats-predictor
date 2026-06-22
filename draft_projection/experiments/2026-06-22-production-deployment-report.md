# NBA Draft Career Projection — Production Deployment Report (2026-06-22)

## Deployment summary

Deployed to **production** (statfuel.online) — no staging environment exists
on Railway for this project (confirmed via `railway status`; only one
environment, `production`). Confirmed with the user before pushing.

- Commit `7a4af9b`: draft_projection/ package, server.py route, training
  scripts, `career_projection_model.pkl`
- Commit `f456cfc`: fixed a shooting-percentage scale bug found during
  smoke testing (see "Known limitations")
- Both deploys: `SUCCESS` on Railway

## Final model metrics

XGBoost multiclass classifier, 8 career-outcome tiers (Bust -> Superstar).

| | |
|---|---|
| Training set | 6,976 players |
| Test set | 1,232 players (held-out 15%, stratified) |
| Accuracy | 74.92% |
| Macro F1 | 0.23 |
| Weighted F1 | 0.71 |

Per-tier precision / recall / F1:

| Tier | Precision | Recall | F1 |
|---|---|---|---|
| Bust | 0.85 | 0.96 | 0.90 |
| End-of-Bench | 0.00 | 0.00 | 0.00 |
| Rotation | 0.28 | 0.26 | 0.27 |
| Starter | 0.17 | 0.08 | 0.11 |
| High-Level Starter | 0.10 | 0.03 | 0.05 |
| All-Star | 0.13 | 0.05 | 0.07 |
| All-NBA | 0.19 | 0.16 | 0.17 |
| Superstar | 0.33 | 0.25 | 0.29 |

Draft position (`draft_slot_tier`) ranks #5 of 54 features by importance
(0.0160), vs. 0.3416 combined for all college-performance features
(production+advanced+efficiency+role) -- **21.3x** higher. The no-draft-
dominance design goal holds.

## Coverage rates

- `archive_cbb_player_stats`: 122,378 rows, 45,004 unique players, 26
  seasons (2000-01 through 2025-26)
- Historical labeled drafted players matched to real college data: **73.6%**
  for the reachable 2001+ draft cohort (964/1,309); **11.7%** overall
  (964/8,208) once pre-2001 draftees (structurally unreachable -- no scrape
  covers that era) are included
- Pre-2001 false-match rate: **0.0%** (was 7.5% before a season-plausibility
  matching fix shipped earlier this session -- see
  `draft_projection/features.py`'s `_select_plausible_row`)

## Why production was chosen over the alternatives tested

Three alternatives were built and evaluated, never deployed:

1. **Class-weighted training** (`sample_weight="balanced"`): improved
   Starter/High-Level-Starter/All-Star/All-NBA recall (0.08->0.19,
   0.03->0.12, 0.05->0.21, 0.16->0.32) but at a severe broad cost --
   accuracy 74.9%->61.0%, Bust recall 0.96->0.75, weighted F1 0.71->0.67,
   Superstar recall *fell* (0.25->0.17). Rejected: the cost wasn't tier-
   specific, it was global.
2. **Missing-flag ablation** (drop all 27 `_missing` indicator columns):
   accuracy/recall virtually unchanged (74.84% vs 74.92%), but
   `draft_slot_tier` jumped from rank #5 to rank #1 individually (still
   passes in aggregate, 5.2x vs 21.3x). Rejected as the literal fix --
   the real bug (4 specific physical/identity flags acting as label-leakage
   proxies, see below) needs a narrower, untested fix instead of blanket
   removal.
3. **Ensembles** (50/50, 75/25, 25/75, confidence-weighted blends of
   production + class-weighted): no blend beat both baselines
   simultaneously. The best-quality blend (75/25 production-heavy) only
   marginally improved macro/weighted F1 and barely moved upper-tier
   recall at all. Confidence-weighted blending actually had the *worst*
   macro F1 of any of the six models compared. Rejected: added complexity
   (two model files, blend logic) for no clear win.

Full writeups: `draft_projection/experiments/2026-06-22-missing-flag-ablation.md`,
`2026-06-22-classweighted-recall-poc.md`.

## Known limitations

1. **Rare upper-tier recall is weak.** Starter (0.08), High-Level Starter
   (0.03), and All-Star (0.05) recall are all low -- the model is good at
   "will this person have any real NBA career" and much weaker at
   distinguishing among the rarer outcome tiers. See the ranked future-
   improvements list for paths to address this.
2. **Top 4 feature importances are `_missing` flags, not real signal**
   (`weight_lb_missing`, `age_at_draft_missing`, `position_group_missing`,
   `height_in_missing`, combining for 0.6424 of total importance). Root
   cause: these flags are a near-tautological proxy for the Bust label
   itself (when missing, a player is Bust 100.0% of the time, since both
   facts trace back to "this player generated zero recorded NBA box-score
   minutes"). Not yet fixed -- the ablation experiment removed too much
   (all missing-flags, not just these 4) and shifted draft-position to the
   single highest individual feature, so a narrower fix is needed first.
3. **College-data coverage caps at 73.6%** even within the reachable
   2001+ era. Root cause not yet diagnosed (likely name-format mismatches:
   suffixes, accented characters, multi-school transfers).
4. **Archetype matching doesn't yet read from `archive_cbb_player_stats`**
   -- `archetype_adapter.py` still queries the empty `archive_ncaa_player_stats`
   table, so every prospect currently shows "No college archetype profile
   available yet" in the UI even though real college stats exist. This is
   a real, user-visible gap (confirmed in production smoke testing) --
   not yet fixed.
5. **Found and fixed during this deployment**: shooting percentages
   (TS%/FT%/3P%/FG%/eFG%) from sports-reference.com/cbb are stored as 0-1
   fractions, but `explain.py`'s human-facing thresholds and display
   formatting assumed the 0-100 scale the old NCAA scraper used. Fixed in
   commit `f456cfc` (presentation-layer only, no retraining needed -- the
   model itself trained consistently on the actual stored scale).

## Smoke test results

| Prospect | Status | Expected outcome | Top comp |
|---|---|---|---|
| AJ Dybantsa | 200 OK | Starter | RJ Barrett |
| Cameron Boozer | 200 OK | All-Star | Zion Williamson |
| Darryn Peterson | 200 OK | High-Level Starter | Kentavious Caldwell-Pope |
| Cooper Flagg (historical, 2025 #1 pick) | 200 OK | Rotation Player | Sindarius Thornwell |

Frontend verified end-to-end via Playwright against the live site
(statfuel.online): onboarding skip -> account signup -> Draft Projection
nav -> search -> full result render, zero console errors.
