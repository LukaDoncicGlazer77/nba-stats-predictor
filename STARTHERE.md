# Draft Career Projection System — Production Deployment & Incident Recovery

## What was built: 8-tier career outcome prediction for NBA draft prospects

Full system in `draft_projection/` package (labels.py, features.py, comp_engine.py, predict.py, service.py, etc.), served via `GET /api/draft-projection?name=&college=&overall_pick=` in `server.py`, with a "Draft Projection" sidebar page in the frontend.

**Architecture**: Hybrid two-stage hierarchical XGBoost model (as of final deploy, commit `8da2b7f`):
- **Stage 1 (binary)**: Bust vs. Non-Bust (trained on all 8,208 labeled players, 74.3%/25.7% split)
- **Stage 2 (7-way multiclass)**: tier among Non-Bust only (End-of-Bench through Superstar, trained on 2,112 non-Bust players)
- **Composition is soft** (not a hard gate): `P(tier=k) = P_stage1(non-bust) × P_stage2(tier=k|non-bust)` for non-Bust tiers, `P(bust) = 1 - P_stage1(non-bust)`

Model trained on 77 seasons of college basketball data (1950-2026, 275,687 rows) scraped from sports-reference.com/cbb (NCAA scraper was permanently Akamai-blocked, switched data sources mid-session). Historical draft pool of 8,208 players with real career outcomes.

**Data quality**: ~74% college-data coverage for 2001+ draft cohort (pre-2001 players have zero college signal by design — that era's college data doesn't exist in sports-reference).

## Session history: 2026-06-22 through 2026-06-25

### 1. System design & training (2026-06-22, approved by user)
- Designed two-stage hierarchical architecture to address class-imbalance (Bust is 74% of the population)
- Trained on full 1950-2026 dataset after fixing historical college-to-draft name-matching bug
- Evaluated on 96-player diagnostic set (stratified sampling), compared vs. single-model and ensemble baselines
- **Result**: Hierarchical model won — best rare-tier recall without sacrificing overall accuracy

**Key lock-in decisions**:
- Draft position is deliberately minor coarse feature (`draft_slot_tier`: buckets, not exact pick) — capped at 5% weight
- Labels measure absolute career outcome (did they stick/start/make All-Star), never "disappointment vs. draft slot"
- Missing-value indicator columns (`_missing` flags) identified as label-leak (both correlated ~100% with Bust because both stem from "no recorded NBA career"), moved to low priority
- No hard gate at Stage 1 — players stage 1 only gives modest non-bust chance can still carry elevated tier probabilities if stage 2 is confident

### 2. Production deployment (2026-06-24, commit `9fae101`, user approved with "Flag ON")
- `USE_HIERARCHICAL_MODEL = True` in `service.py`, pulling both stage1 and stage2 models from `career_projection_model_hierarchical.pkl`
- Committed + pushed to `main`, Railway auto-deployed
- **Immediate failure**: site returned 502s on all routes, not just `/api/draft-projection`

### 3. Production incident & root-cause diagnosis (2026-06-25)

**Symptom**: Every route failing with generic DB connection errors:
```
psycopg2.OperationalError: connection to server failed: FATAL (EAUTHQUERY) authentication query failed: connection to database not available
psycopg2.OperationalError: connection to server failed: FATAL (ECHECKOUTTIMEOUT) unable to check out connection from the pool after 15000ms
```

**Root cause 1: Thundering-herd race condition in `_get_draft_projection_pool()`**

The historical pool build (fetching + feature-engineering 8,208 players from a 275k-row table) takes ~45 seconds with the expanded 1950-2026 dataset. The `_get_draft_projection_pool()` function had a plain global-variable cache with no lock:

```python
_draft_projection_pool = None
def _get_draft_projection_pool():
    global _draft_projection_pool
    if _draft_projection_pool is None:  # <-- RACE CONDITION HERE
        # 45-second build, holds a DB connection the whole time
        _draft_projection_pool = comp_engine.build_historical_pool(...)
```

ThreadingHTTPServer spawns a thread per request, so every concurrent request that arrived before the first build finished saw `None` and started its own redundant build. Each one held its own DB connection open for the full 45 seconds. Supabase's connection pool here is only 15 connections — saturated almost immediately.

**Fix (commit `7b7788e`)**: Added `threading.Lock()` with double-checked None test, so only one thread ever performs the build; others wait:
```python
_draft_projection_pool_lock = threading.Lock()
def _get_draft_projection_pool():
    global _draft_projection_pool
    if _draft_projection_pool is not None:
        return _draft_projection_pool
    with _draft_projection_pool_lock:
        if _draft_projection_pool is None:  # re-check after acquiring lock
            # ... build once, others wait ...
```

**Root cause 2: Cold-start gateway timeout**

Even with the race condition fixed, the first request after server startup still has to wait through the ~45-second pool build, exceeding the platform's gateway timeout (~43s observed), resulting in a 502 with no application-level error.

**Fix (commit `8da2b7f`)**: Pre-warm the pool in a daemon background thread at server startup instead of waiting for the first real request:
```python
def _warm_draft_projection_pool_background():
    try:
        _get_draft_projection_pool()
    except Exception as exc:
        print(f"Background warm-up failed (will retry lazily): {exc}")

def main():
    ensure_users_table()
    threading.Thread(target=_warm_draft_projection_pool_background, daemon=True).start()
    # ... server binding ...
```

### 4. Current status (2026-06-25, after both fixes deployed)

**New symptom**: Instance shows CRASHED status after the pre-warming deploy (commit `8da2b7f`), with **zero Python errors in logs** — just repeated "Serving NBA predictor..." startup messages indicating crash-restart cycles.

**Diagnosis**: This pattern (no exception, clean restarts) is a strong signal of an **OOM kill** (kernel forcibly terminating the process when memory limit exceeded), not a code exception.

**Why memory bloat is real**:
- Deployed hierarchical model loads two XGBoost models into memory (stage1 + stage2), vs. one before
- Historical pool pre-warming means both models + feature pool are built/loaded before the first request, not lazily
- Pool size more than doubled (1950-2026 vs. 2001-2026), more rows to feature-engineer

**Current blockers**:
- Railway's `status` command became unauthorized (required fresh login) — recovered via `railway login` prompt
- Cannot reliably poll deployment state without hitting transient CLI issues
- No visibility into actual container memory limit or usage (would need Railway dashboard or direct `railway run` command)

**Not yet resolved**: Container restarts before the pool warm-up completes, so `/api/draft-projection` still times out/fails on every startup.

## Path forward

### Immediate (to stabilize the site):
1. **Roll back to commit `9f00af5`** (the `ensure_users_table()` retry fix, before any pool-related changes) to restore the single-model deploy + minimal footprint, verify site is stable
2. OR investigate memory usage directly:
   - Check Railway dashboard's memory graphs for the service
   - If memory is indeed the limiter, consider lazy-loading only one model at a time, or trimming the pre-warmed pool (e.g., sample a subset of players for initial build instead of all 8,208)

### Medium-term:
- Once stable, re-approach the hierarchical model in a memory-constrained way (e.g., load stage2 only on first `/api/draft-projection` request, not at startup; let stage1 dominate the cold-start)
- Document the tradeoff: hierarchical model is better ML but heavier; single model is production-stable with current resource limits

### Testing discipline going forward:
- Any change to `_get_draft_projection_pool()`, model loading, or startup initialization needs to be load-tested against realistic concurrency *before* production deploy (50+ concurrent requests during the ~45s warm-up window, or similar)
- Monitor `railway logs` after every deploy until a full warm-up cycle completes without crashes

## Files touched this session

- `draft_projection/` — full package (54-year-old data now, 1950-2026)
- `service.py` — added `USE_HIERARCHICAL_MODEL` flag, dispatcher logic for model type, hierarchical model loading
- `predict.py` — added `load_trained_hierarchical_model()`, `ml_tier_probabilities_hierarchical()` soft-composition function
- `train_career_projection_hierarchical.py` — training script for the two-stage model (saved as `career_projection_model_hierarchical.pkl`)
- `server.py` — fixed `ensure_users_table()` retry logic (commit `9f00af5`), added threading.Lock to pool cache (commit `7b7788e`), added background pool warm-up (commit `8da2b7f`)
- `requirements.txt` — unchanged (xgboost/sklearn already present)
- `.gitignore` — added experiment model files and `.venv/`

## Known recovered state

Before deploying the hierarchical model, I recovered the original 26-season model (commit `7a4af9b`) via `git show` and saved it as `career_projection_model_original_26season_approved.pkl` (gitignored), preserving it locally in case an emergency rollback to the exact pre-incident state is needed.

---

## Session — 2026-06-25/26/27: Archetype scoring fix, draft picker redesign, user analytics, auth redirect

All changes committed and live on statfuel.online unless noted.

### 1. Archetype engine: raw defensive scores in `named_archetype_mix` (`990a0b7`)

`defensive_role()` now returns both the softmaxed percentages **and** the raw pre-softmax linear scores:
- `rim_protector_raw` = `0.6 * blk_pct_pr + 0.4 * drb_pct_pr`
- `versatile_defender_raw` = `0.5 * stl_pct_pr + 0.5 * max(dbpm,0)/5`

`named_archetype_mix()` was updated to use these raw scores for Playmaking Big, Rim Protector, 3&D Wing, and Defensive Wing — previously it divided the softmaxed percentage by 100, which was arithmetically equivalent but semantically wrong (softmax distorts the ratio). This changed the dominant archetype for some players (e.g. Cameron Carr shifted from Playmaking Big 37.3% → Heliocentric Engine 37.0%).

### 2. Draft projection player picker redesign (`b71d7c7`, `12fdcb0`, `b07ab21`)

Replaced the generic single `cmp-search-slot` card on the Draft Projection page with a purpose-built search hero:
- Full-width glass panel with subtle top-center radial glow
- Large centered search bar with SVG icon and accent focus ring
- Dropdown results show rank + school alongside name
- Top-10 lottery picks as pill chips (`dp-chip`) that stretch evenly across the full width — click any to instantly load their projection
- Filled state uses `dp-selected-bar` (same glass panel style as other cards)
- Fixed: `cmp-player-bar` used `repeat(4,1fr)` grid; added `dp-player-bar` override with `grid-template-columns:1fr` so it fills the full designated strip

### 3. Google Analytics added (`5ff0b84`)

Tag ID `G-WJMZDK5CEK` added to `<head>` in `index.html`. Data visible at analytics.google.com.

### 4. User time-on-site tracking (`aa4b0c8`, `5525022`)

**`/api/admin/user-emails`** now returns per-user:
- `member_for` — human-readable join duration (e.g. `"4 days"`, `"2 weeks"`)
- `time_on_site` — real active time (e.g. `"1h 23m"`)

**Heartbeat system** (`/api/heartbeat`, POST `{email}`):
- Client pings every 60s while a logged-in user is on the site
- Server accumulates `total_active_seconds` on `archive_users`; gaps >90s (tab closed/away) are not counted
- Two new columns added via `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`: `last_seen_at TIMESTAMPTZ`, `total_active_seconds INTEGER DEFAULT 0`
- Guests and unauthenticated sessions are not tracked

### 5. Post-login redirect + hash routing (`f8b2a13`)

- `navigate(section)` now calls `history.replaceState` to keep `location.hash` in sync — sections are shareable as `statfuel.online/#draft-projection`
- When a guest hits a members-only section, the intended section is stored in `localStorage` as `sf_redirect` before showing the login wall
- On startup, `location.hash` is read as the initial section instead of always defaulting to `"dashboard"`
- After `showApp()` reveals the app shell, it reads and clears `sf_redirect` and calls `navigate()` with the stored destination

### 6. Supabase outage — 2026-06-27

Project showed "unhealthy" status in Supabase dashboard; pooler returned `FATAL: (EAUTHQUERY) authentication query failed`. No incident on status.supabase.com. Resolved on its own (~few hours). No action taken on our end — data was never at risk. If this recurs: check status.supabase.com, then check the project card in the Supabase dashboard for "paused" or "unhealthy" badge before touching any credentials or connection strings.

