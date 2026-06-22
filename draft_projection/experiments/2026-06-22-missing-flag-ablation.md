# Experiment: missing-indicator ablation (2026-06-22)

**Status:** Experiment only. Production model (`career_projection_model.pkl`) was NOT
replaced. Experiment artifact saved as `career_projection_model_ablation_no_missing_flags.pkl`
(repo root, alongside the production model).

## Motivation

The first trained model's top 4 feature importances were all `_missing` flags
(`weight_lb_missing`, `age_at_draft_missing`, `position_group_missing`,
`height_in_missing`), combining for 0.6424 of total importance -- more than
40x the importance of any single real basketball feature. This needed
explaining before trusting the model.

## Root-cause finding

`archive_player_career_info` only contains players who generated real NBA
box-score rows. A drafted player with zero recorded NBA minutes has no row
there at all -- this is a *join coverage gap*, not a partial-data problem
(when a row *does* exist, it is always 100% complete: ht/wt/pos/birth_date
all present, 3,844/3,844).

Missing rates, historical pool (n=8,208):
- height_in: 52.3% missing
- weight_lb: 52.4% missing
- age_at_draft: 53.2% missing
- position_group: 52.3% missing

Sharply era-skewed: 65.4% missing for pre-1980 draftees, 53.3% for 1980-2000,
only 7.4% for 2000-2015, 5.9% for 2015+.

**The confound:** when `height_in` is missing, the player is labeled Bust
**100.0%** of the time (n=4,291, zero exceptions). This is not a soft
correlation -- both facts (no career_info row, Bust label) trace back to the
same root cause: zero recorded NBA box-score minutes. The model was
rediscovering its own label-construction logic through a backdoor column,
not learning a real basketball signal.

Control check -- college-data missing-flags do NOT show this pattern:
`pts_per40` missing -> 78.9% Bust vs. 39.5% Bust when present. Real,
informative, *imperfect* correlation (driven by the 2001+ scrape coverage
limit), not a tautology. This is why the fix should target the 4
physical/identity flags specifically, not all missing-flags broadly.

## Backfill investigation

Checked every `archive_*` table with a height/weight/position column
(`archive_advanced`, `archive_per_36_minutes`, `archive_player_per_game`,
etc.) -- all have the identical limitation: rows only exist for players who
generated recorded stats. No internal backfill source exists for
never-played draftees. Only an external source (re-scraping biographical
pages specifically for drafted-but-never-played picks) could close this,
and that's a separate scraping project, not a quick join.

## Ablation result: current model vs. no missing-flags at all

| | Current (27 `_missing` flags) | Ablation (0 `_missing` flags) |
|---|---|---|
| Accuracy | 74.92% | 74.84% |
| Bust recall | 0.96 | 0.96 |
| End-of-Bench recall | 0.00 | 0.00 |
| Rotation recall | 0.26 | 0.25 |
| Starter recall | 0.08 | 0.08 |
| High-Level Starter recall | 0.03 | 0.03 |
| All-Star recall | 0.05 | 0.05 |
| All-NBA recall | 0.16 | 0.16 |
| Superstar recall | 0.25 | 0.25 |
| `draft_slot_tier` rank | #5 (0.0160) | **#1 (0.1371)** |
| College-performance / draft_slot_tier ratio | 21.3x | 5.2x |

**Interpretation:** accuracy/recall are practically unchanged -- the
missing-flags weren't adding real predictive power beyond what the actual
feature values (XGBoost handles default zeros natively in its split logic)
already provide. But removing them shifts `draft_slot_tier` from rank #5 to
the single highest-importance feature overall (8.6x increase), even though
college-performance still outweighs it in aggregate (5.2x, down from 21.3x).
This cuts against the system's explicit design goal of draft position never
becoming a primary individual predictor.

## Recommendation (not yet implemented/tested)

Don't blanket-remove all missing-flags (the ablation tested here is broader
than the actual fix). Remove only the 4 tautological ones
(`height_in_missing`, `weight_lb_missing`, `age_at_draft_missing`,
`position_group_missing`) while keeping the legitimate college-data
missing-flags, which encode real epistemic uncertainty. This narrower
experiment has not been run yet.
