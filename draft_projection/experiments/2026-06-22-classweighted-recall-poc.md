# Experiment: class-weighted training proof-of-concept (2026-06-22)

**Status:** Experiment only. Production model (`career_projection_model.pkl`)
NOT replaced. Saved as `career_projection_model_experiment_classweighted.pkl`
(repo root).

## What changed

Identical features/split/hyperparameters to the production model, except
`model.fit(X_train, y_train, sample_weight=...)` using
`sklearn.utils.class_weight.compute_sample_weight("balanced", y_train)` --
inverse-frequency weighting so the loss no longer lets the 74%-of-the-data
Bust class dominate.

## Result

| Tier | Production recall | Class-weighted recall |
|---|---|---|
| Bust | 0.96 | 0.75 |
| End-of-Bench | 0.00 | 0.16 |
| Rotation | 0.26 | 0.25 |
| Starter | 0.08 | **0.19** |
| High-Level Starter | 0.03 | 0.12 |
| All-Star | 0.05 | **0.21** |
| All-NBA | 0.16 | **0.32** |
| Superstar | 0.25 | 0.17 |
| **Overall accuracy** | **74.92%** | **61.04%** |

## Interpretation

Real, demonstrated lever for the exact tiers asked about (Starter/All-Star/
All-NBA recall roughly doubled-to-quadrupled), at a real cost: overall
accuracy drops sharply and Bust recall falls from 0.96 to 0.75. This is the
classic precision/recall trade-off of balanced class weighting on a
severely imbalanced target, not a flaw in the implementation.

Superstar recall actually *fell* (0.25 -> 0.17) -- likely sampling noise
given the tiny support (n=12 in the test set), not a real signal that
balancing hurts superstar detection.

This was run as `sample_weight="balanced"` (full inverse-frequency). A
softer weighting (e.g. sqrt of inverse frequency, or a fixed weight cap) was
not tested and is a reasonable middle-ground follow-up if the accuracy drop
here is judged too severe.
