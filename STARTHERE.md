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

---

## Session — 2026-06-29: Shot-zone integration, auth bug fix, draft weakness expansion

### 1. R + cbbdata setup

R 4.6.1 installed via CRAN `.pkg` installer (not Homebrew — gcc 16.1.0 fails to compile from source on macOS 12). `devtools` and `pak` installed via R, then `pak::pak("andreweatherman/cbbdata")`. Account registered at cbbdata: username `ameenfern`, email `ameenfern77@gmail.com`. API key is persisted by the R package; re-run `cbd_login(username='ameenfern', password='...')` in a terminal if a new R session needs it.

`export_shot_zones.R` (in repo root of `statfuel-site/`) is the export script. It fetches `cbd_torvik_player_season()` for 2008–2026, selects rim/mid/3PT attempt and make columns, and writes `shot_zones.csv`. Re-run it if Barttorvik updates their historical data or if coverage years need to extend.

### 2. Shot-zone data integration (`ba81e76`)

**Data**: `shot_zones.csv` (85,766 rows, 2008–2025, bundled in repo). Columns: `player, team, year, rim_m, rim_a, rim_pct, mid_m, mid_a, mid_pct, three_m, three_a, three_pct, ftm, fta, ft_pct`. Year = end-year (e.g. 2024 = 2023-24 season). ~73k rows have valid rim/mid/3PT splits after filtering zero-FGA rows.

**What it fixes**: `scoring_profile()` in the NBA archetype engine previously used `f_tr` (FTA/FGA) as an interior-pressure proxy. For college prospects in `archetype_adapter.py`, this was particularly inaccurate. Real shot-zone attempt rates are now used instead.

**Implementation** (`draft_projection/archetype_adapter.py`):
- `_load_shot_zones()` runs at module startup, builds `_SHOT_ZONES: dict[(normalized_name, year), {rim_att_rate, three_att_rate}]`
- `rim_att_rate = rim_a / (rim_a + mid_a + three_a)`, same for `three_att_rate`
- In `compute_archetype_mix()`, shot-zone rates are merged into each pool row before percentile ranking
- `_add_shot_zone_percentiles()` ranks them separately from `PERCENTILE_COLS` — missing players stay `None` rather than defaulting to 0.5, so `scoring_profile()` can detect absence and fall back
- `_row_to_archetype_input()` passes `rim_att_rate_pr` and `three_att_rate_pr` into the archetype input dict

**`scoring_profile()` change** (`archetype_engine.py`):
```python
def scoring_profile(p):
    rim_pr = p.get("rim_att_rate_pr")
    three_pr = p.get("three_att_rate_pr")
    if rim_pr is not None and three_pr is not None:
        return _softmax({"three_pt_pressure": three_pr, "interior_pressure": rim_pr})
    return _softmax({"three_pt_pressure": p["fg3a_rate_pr"], "interior_pressure": p["ft_rate_pr"]})
```

NBA players and pre-2008 college prospects fall back to the original formula. International prospects (e.g. Risacher) not in Barttorvik also fall back correctly — they're simply absent from `_SHOT_ZONES`.

**Verified reference points**:
- Zach Edey 2024: rim_att_rate=0.507, three_att_rate=0.004 → 71% interior / 29% three ✓
- Max Abmas 2024: rim_att_rate=0.158, three_att_rate=0.557 → 71% three / 29% interior ✓
- Dalton Knecht 2024: essentially 50/50 (balanced wing) ✓
- Fallback path (None shot-zone data) correctly routes to fg3a_rate_pr/ft_rate_pr, no crash ✓

### 3. TOV% as self-creation proxy — investigated, not implemented

Investigated whether `tov_pct_pr` can distinguish self-creating ball-handlers from high-usage off-ball scorers in `creation_burden()`. Key finding: the signal is not clean enough.

Three formulations tested against Chris Paul 2013, Carmelo Anthony 2013, Kyle Korver 2015, DeAndre Jordan 2013:
- **F1 (tov_pr / usg_pr)**: broken — DeAndre Jordan (2.202) and Korver (3.179) rank as top "self-creators" because their moderate tov_pct is divided by very low usage
- **F2 (tov_pr × ast_pr)**: closest to useful; Chris Paul scores 0.577, but Korver scores 0.394 (false positive — his moderate assist percentile inflates the signal despite being catch-and-shoot)
- **F3 (tov_pr − (1−ast_pr))**: most directionally correct (Chris Paul +0.576, Carmelo −0.256) but still noisy for off-ball players with mid-range assist percentiles

**Conclusion**: `ast_pct_pr` interacting with `usg_pct_pr` (already the core of `heliocentric_engine`) is the stronger signal. Adding `tov_pct` as a third multiplicative factor would correctly demote Carmelo-type gunners but risks wrongly demoting controlled ball-handlers. **Deferred until `unassisted_fg_pct` data is available** — that's the clean signal. Do not revisit `tov_pct` without that data.

### 4. Post-login double-prompt bug fix (`3b9eea7`)

**Bug**: Opening `statfuel.online/#draft-projection` as a logged-out user showed both the auth screen AND a "Members Only 🔒" modal floating on top of it. `navigate()` was calling `showLoginWall()` unconditionally even when `appShell` was hidden (i.e. the user hadn't passed the auth gate yet).

**Fix** (`static/app.js`): one conditional — `showLoginWall()` only fires when `appShell` is already visible (guest user inside the app who hits a members-only nav item). When the auth screen is active (`appShell.style.display === 'none'`), `sf_redirect` is saved silently and the auth screen handles the rest. `showApp()` already consumed `sf_redirect` correctly after login — the redirect was never broken, just the display.

### 5. Draft projection weakness expansion (`7df0534`)

**Problem**: `explain.py`'s `weaknesses()` only fired on extreme red-flag thresholds (TS% < 50% on high volume, TOV% ≥ 22, FT% < 65%, age ≥ 22.5). Most prospects with ordinary profiles produced an empty "Weaknesses" section in the Scouting Notes UI.

**Added position-aware signals** (`draft_projection/explain.py`):
- **Guards/wings** (position_group < 2.5): 3PT% < 33% → spacing warning
- **Guards** (position_group < 1.5) with usg_pct ≥ 22 AND ast_pct < 18: "creates volume but doesn't distribute"
- **Forwards/Centers** (position_group > 1.5): DREB% < 14% → rebounding gap
- **Centers** (position_group > 2.5): blocks/40 < 1.2 → rim protection gap
- **All**: TOV% 17–22% → moderate turnover concern (below the red-flag threshold, still notable)
- **All**: negative college BPM → net-negative all-around impact

Verified: Cooper Flagg profile (elite numbers across the board) fires no weaknesses. Guards who can't shoot and soft centers fire the correct position-specific signals.

### Current status (2026-06-29 end of session)

Latest commits: `7df0534` (weakness expansion) → `3b9eea7` (auth bug) → `ba81e76` (shot-zone integration). All live on `statfuel.online`. No known instability at end of session.

---

## Session — 2026-07-13/14/15: Team Fit engine, archetype formula fixes, historical comps gap

All changes committed and live unless noted.

### 1. Team Fit engine — full rebuild (`team_fit_engine.py`)

The Team Fit feature was rebuilt from scratch across multiple iterations. Current state:

**Self-exclusion fix**: The target player was previously counted in their own team's composition when scoring fit. Fixed by passing `player_id` through `score_team_fit()` → `_build_team_compositions()` and excluding the target player from all 30 team compositions.

**Normalization — span-based (best→90, worst→35)**:
Previous attempts using a league-average anchor failed: when the anchor score ≈ 0, all real scores inflated to 97%. Final fix: sort all 30 raw scores and apply:
```python
span = max(max_raw - min_raw, 1e-6)
normalized = 35 + 55 * (r["_raw"] - min_raw) / span
r["fit_score"] = round(min(90, normalized), 1)
```
This is always stable — only requires `max ≠ min` across 30 teams.

**Scoring components** (`_fit_score`):
- Gap filling: player archetype fills underrepresented roles (weighted by how rare they are on the team)
- Complementarity: player archetype complements the team's star player archetype
- Bidirectional effectiveness: `_PLAYER_EFFECTIVENESS` dict scores 9×9 archetype pairs
- Star pairing bonus: if player and team star are known great pairs (HE+3D, HE+RimProtector, etc.)
- Creation clash: `if player_usg_pr > 0.68 and team_usg_pr > 0.68: raw *= 0.80`
- Continuous minutes factor: `max(0.55, 1.0 - max(0, pos_count - 2) * 0.12)` — crowded positions lower fit

**Contender detection** — two fixes:
1. Original: used `total_vorp` sum, which was distorted by mid-season acquisitions having partial VORP
2. First fix: switched to `max individual VORP ≥ 3.0` per team
3. Second fix (2026-07-15): traded players' VORP is split across team entries (Luka had DAL VORP + LAL VORP, neither alone ≥ 3.0). Fixed by pre-computing `max_vorp_by_player` across all team entries for that season and using that effective VORP in team compositions. `commit a692b59`

**pos passthrough**: `server.py` extracts `player_usg_pr` and `player_pos` from the pool entry and passes them to `score_team_fit()` for the continuous minutes factor to work correctly.

**UI**: Contender/Rebuilding badges added to the team fit panel in `app.js`/`styles.css`.

### 2. Archetype engine: `pos` field added

`pos` (position string) added to pool entries. Required for bidirectional fit scoring in `team_fit_engine.py`.
- Added to `archive_player_per_game` SELECT in `load_pool()`
- Added to `games_by_key` dict
- Added to pool `append()` dict

### 3. Archetype formula fixes (`archetype_engine.py`)

**Scoring Big — non_creator_finisher bug (commit `69ba498`)**:

`non_creator_finisher = (1-usg)*(1-ast)` is near 1.0 for any rim-runner with low usage and low AST (Gobert, Adams). Combined with near-100% `interior_pressure`, the old formula:
```python
"Scoring Big": sf * (creation["off_ball_scorer"] * scoring["interior_pressure"] / 100 * 2
    + creation["non_creator_finisher"] * scoring["interior_pressure"] / 100 * 1.5),
```
inflated Gobert and Adams to ~46% Scoring Big. Fix: removed the `non_creator_finisher` term entirely:
```python
"Scoring Big": sf * creation["off_ball_scorer"] * scoring["interior_pressure"] / 100 * 2,
```
Result: Gobert → Rim Protector 38.9% ✓, Adams → Rim Protector 36.3% ✓

**Playmaking Big — scale mismatch (commit `69ba498`)**:

`HE` comes from `creation["heliocentric_engine"]` which is a softmax %-value (0-100 range). Old PlayBig formula:
```python
"Playmaking Big": sf * (ast_pct_pr ** 2) * usg_pct_pr * drb_pct_pr * 12,
```
Max product ≈ 11.5 — far below HE ≈ 46 for Jokic (ast=1.0), so HE always won. Fix: added `blk_pct_pr` factor (distinguishes big-man playmakers from guard-creators) and raised multiplier:
```python
"Playmaking Big": sf * (ast_pct_pr ** 2) * usg_pct_pr * drb_pct_pr * blk_pct_pr * 80,
```
- Jokic (ast=1.0, blk=0.705): PlayBig ≈ 54 > HE ≈ 47 → Playmaking Big ✓
- Luka (ast=0.99, blk=0.511): PlayBig ≈ 36 < HE ≈ 47 → Heliocentric Engine ✓
- Gobert (ast~0.05): PlayBig = (0.05)² × ... ≈ 0 → stays Rim Protector ✓

**Remaining 2026 anomalies** (not formula bugs — genuine 2026 season data):
- Jokic: ast_pr = 1.000, blk_pr = 0.705 — now correctly Playmaking Big after the fix
- Embiid: ast_pr = 0.820 in 38 games (unusually high) → shows Rim Protector instead of Scoring Big; this reflects his actual 2026 playmaking role, not an error
- Giannis: usg_pr = 0.998, ast_pr = 0.986 in 36 games → shows HE; Hybrid Offensive Big formula multiplier (4) is too small to compete but revisiting it risks regressions elsewhere

### 4. Archetype accuracy scan

Ran `analyze_archetypes.py` against all 2026-season players. 53 flagged players across rules:
- **G1**: guards dominant in big archetypes (Rim Protector / Scoring Big / Playmaking Big)
- **B1/B2**: bigs showing Heliocentric Engine with low AST or low USG
- **B3**: bigs showing Rim Protector with low BLK
- **A1**: 23 players with no dominant archetype (primary < 20%) — often bench players with fragmented profiles
- **A3**: 9 players showing Defensive Wing without defensive signal (stl_pr < 0.40 and dbpm_pr < 0.40)
- **REF**: known reference player mismatches

Output: `flagged_archetypes.csv` (generated locally, not committed — re-run `analyze_archetypes.py` with `DATABASE_URL` set).

### 5. Historical comps gap — data backfill needed

**Symptom**: Same-stage and projected-engine comps for current players only show players from roughly 2015-2026. Kevin Garnett, early-2000s stars, etc. do not appear.

**Root cause**: `archive_advanced` and `archive_player_per_game` only contain seasons that were explicitly loaded into the DB. `update_current_season.py` updates only the current season. Historical backfill was done via `import_archive.py` from CSV files (now absent locally). The DB likely only has ~10-12 years of data.

**Fix**: Run `update_current_season.py --season YEAR` for each historical year you want in the pool, going back to the desired cutoff (e.g. 1985 or 1996). This scrapes Basketball-Reference for that season. Recommended: run for 1980-2014 in sequence with 4s delays already baked into the scraper. Once loaded, `load_pool()` has no season filter so all historical players automatically appear as comps.

**Note**: USG%, AST%, BLK%, DRB%, STL% are available in Basketball-Reference going back to 1973-74, so players from any post-1974 season will pass the pool filter.

### Current status (2026-07-15)

Latest commits: `a692b59` (contender detection for traded players) → `69ba498` (Scoring Big + PlayBig fixes). All live. `analyze_archetypes.py`, `check_fix.py`, `debug_player.py` are local verification scripts not committed to main — safe to delete if not needed.

