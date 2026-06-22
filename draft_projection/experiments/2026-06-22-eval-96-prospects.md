# Model evaluation: 96 real historical prospects (2026-06-22)

**No model changes made.** This is a diagnostic evaluation only, per request,
to determine what the *next* improvement should target. Raw per-player
records: `2026-06-22-eval-96-prospects-results.json`.

## Methodology

Sampled from the model's actual held-out test set (same split as training,
`random_state=42` -- so every number here is a genuine out-of-sample
prediction, not a training-data lookup). Took up to 12 players per tier
(8 tiers; Superstar only had 12 in the full test set, capping the total at
96) -- **deliberately stratified, not population-representative**: the
natural test set is 74% Bust, which would mostly just re-confirm "the model
is good at Bust" and bury the rare-tier failures the user actually wants
surfaced.

For each of the 96: actual tier, predicted tier (argmax), full probability
vector, confidence in both the predicted and the *true* tier, and top-5
historical comparables (computed directly from the comp engine's
already-standardized similarity, not re-queried).

## Headline numbers

| | |
|---|---|
| Exact-tier match | 22.9% (22/96) |
| Within 1 tier | 42.7% (41/96) |
| Off by 3+ tiers | 38.5% (37/96) |
| Mean ordinal error | 2.23 tiers |

This is far below the 74.9% accuracy reported for the natural test
distribution -- expected and intentional, since that number is mostly
"correctly says Bust" given Bust's 74% share. This stratified view is the
honest picture of how the model performs across the full outcome spectrum.

## 1. Cases where the model clearly succeeds

15 cases were both exactly correct and reasonably confident (>=0.40 in the
true tier). Pattern: **every one of these is either an obvious Bust or has
unusually strong, unambiguous signal**:

- Obvious busts with clean data: Steve Hood, Otto Kriegshauser, Steve Beck,
  Vince Kempton, John Schroeder, etc. -- conf 1.00, correctly Bust
- **Derrick Rose** (#1, 2008, Memphis) -- correctly All-NBA, conf 0.64
- **Jason Kidd** (#2, 1994, California) -- correctly Superstar, conf 0.50
- **Jaden Ivey** / **Moses Moody** (2021 lottery picks) -- correctly Rotation

The model succeeds when the signal is either extremely one-sided (clear
bust) or when a player's production/physical profile was historically
unambiguous (Derrick Rose, Jason Kidd -- both had loud, clean #1/#2-pick
production profiles).

## 2. Cases where the model clearly fails

22 cases were off by 4+ tiers. **Every single one of the worst failures is
the model predicting Bust for a real Hall-of-Famer or multi-time All-Star:**

| Player | Pick/Year | True | Predicted | True-tier confidence |
|---|---|---|---|---|
| Steve Nash | #15, 1996 | Superstar | Bust | 0.00 |
| Mark Price | #25, 1986 | Superstar | Bust | 0.02 |
| Dirk Nowitzki | #9, 1998 | Superstar | Rotation | 0.01 |
| Sam Cassell | #24, 1993 | All-NBA | Bust | 0.03 |
| Goran Dragić | #45, 2008 | All-NBA | Bust | 0.02 |
| Willis Reed | #10, 1964 | All-NBA | Rotation | 0.11 |
| Gail Goodrich | #10, 1965 | All-NBA | Bust | 0.06 |
| Mehmet Okur | #38, 2001 | All-Star | Bust | 0.05 |

But **not all big failures lack data** -- three have real college stats on
file and still fail badly:

| Player | True | Predicted | Has college data |
|---|---|---|---|
| **Paul George** (#10, 2010, Fresno State) | Superstar | Starter | Yes |
| **Hassan Whiteside** (#33, 2010, Marshall) | High-Level Starter | Bust | Yes |
| **Chris Kaman** (#6, 2003, Central Michigan) | All-Star | End-of-Bench | Yes |

This is the most important distinction in this whole evaluation -- see below.

## 3. Common failure patterns

**Pattern A -- the missing-data tautology causing concrete, named failures.**
19 of the 22 biggest failures lack college data (mostly pre-2001 era, or
international players the CBB scrape can't reach by construction). This
isn't abstract anymore: Steve Nash, Mark Price, Dirk Nowitzki, Sam Cassell,
Goran Dragić, Willis Reed, Gail Goodrich, and Sam Jones are *literally*
predicted Bust with ~0% confidence in their real outcome, for the exact
reason flagged in the previous session's missing-flag investigation (no
career_info row -> no physical data -> the model's top-importance feature
fires "missing" -> Bust). This is the single largest, most visible failure
driver in this sample.

**Pattern B -- genuine class-imbalance underrating, independent of data
completeness.** Paul George, Hassan Whiteside, and Chris Kaman *have* real
college data and still get badly underrated. This confirms the rare upper
tiers are poorly learned for a second, separate reason: there simply isn't
enough training signal for Superstar/All-NBA/All-Star (12-43 examples each)
against Bust's 6,096, so even players with clean, complete data regress
toward "probably Bust." This is exactly the failure mode the class-weighting
experiment partially fixed (and partially broke something else doing it).

**Pattern C -- systematic pessimism, not random error.** Of all wrong
predictions, **65 were under-predictions (model too pessimistic) vs. only 9
over-predictions** (model too optimistic) -- a 7:1 skew. The model doesn't
fail symmetrically; it has a strong, consistent bias toward calling everyone
worse than they actually became. (Counter-example confirming it's not
absolute: Jalen Green, #2 2021, predicted Superstar but became a Starter --
the model can be fooled by hype/measurables into overrating too, just far
less often than the reverse.)

**Pattern D -- the comp engine often has the right answer, just not ranked
first.** Only 18.8% of the time does the #1 comp's real outcome match the
prospect's true tier -- but 49.0% of the time, *some* comp in the top 5
does. The right comparable is frequently *in the pool*, just not surfaced
as the top match. This is real, recoverable signal currently being
discarded.

**Confidence calibration is weak but not useless.** Mean confidence when
correct (0.647) is meaningfully higher than when wrong (0.497) -- so the
model's confidence score does carry *some* real information, just not
enough to flag the catastrophic failures above (most of which sit at
true-tier confidence near 0.00-0.05, meaning the model isn't just wrong,
it's *certain* it's right about being wrong).

## 4. Which missing features would have most helped

For Pattern A (the majority of catastrophic failures): **nothing new** --
the fix is removing the tautological missing-flags identified previously
(`height_in_missing`, `weight_lb_missing`, `age_at_draft_missing`,
`position_group_missing`), not adding a feature. Adding a real external
biographical-backfill source for pre-2001/international players would help
too, but is a much bigger lift for the same effect.

For Pattern B (Paul George / Whiteside / Kaman -- failures *with* data):
no single missing feature jumps out from this sample. These look like a
genuine modeling-capacity problem (rare classes don't get enough gradient
signal), not a missing-input problem.

For Pattern D (comp engine near-misses): conference-strength-adjusted
production (already on the improvement list) would likely help separate
"genuinely great against good competition" from "good stats against weak
competition," which is exactly the kind of thing that would re-rank a
near-miss comp into the right slot.

## Recommendation on what to fix next

The evidence points to **two fixes, not one**, addressing two genuinely
separate failure mechanisms found in this sample:

1. **Better features (highest priority, lowest effort)**: remove the 4
   tautological missing-flags. This directly targets Pattern A, which is
   the single largest and most embarrassing failure mode found (confident,
   wrong Bust predictions for Hall-of-Famers) -- and was already the #4
   ranked recommendation from the previous session, now with much more
   concrete supporting evidence.
2. **Hierarchical classification (second priority, more effort)**: targets
   Pattern B, which fix #1 will *not* solve (Paul George/Whiteside/Kaman
   have complete data and still fail). This was already the #2 ranked
   recommendation; this evaluation specifically confirms it's necessary
   even after fix #1, not redundant with it.

**Better matching** is a real, secondary contributor (Pattern D) worth
doing but not urgent -- the comp engine already surfaces the right answer
in its top 5 half the time, so this is a refinement, not a core fix.

**Better labels** is *not* supported by this evaluation -- every failure
case inspected has a label that looks correct on inspection (Steve Nash
really was a Superstar, Dirk really was a Superstar); the problem is the
model's prediction, not the ground truth it's being measured against. I
would not prioritize relabeling work based on what this sample shows.

**Something else worth flagging**: Pattern C (the 7:1 pessimism skew) is
itself a strong argument for hierarchical classification specifically
(rather than e.g. just more data) -- a Bust/non-Bust gate would directly
break the mechanism causing systematic under-prediction, since right now
every prediction is implicitly "compared against Bust" in the same softmax.
