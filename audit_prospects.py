#!/usr/bin/env python3
"""
Audit all 2026 draft prospects: archetype mix + top comps.
Flags players with no CBB data, unexpected primary archetypes, or
missing/weak comps so they can be reviewed manually.
Run from statfuel-site/: python3 audit_prospects.py
"""
import os, sys, re
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql://postgres.ovgnihzycxdjzouurpfz:statfuel.online"
    "@aws-1-us-west-2.pooler.supabase.com:6543/postgres?sslmode=require",
)

import psycopg2, psycopg2.extras
import archetype_engine as ae
from draft_projection import archetype_adapter as ada
import draft_projection.comp_engine as comp_engine
import draft_projection.service as svc

DB = os.environ["DATABASE_URL"]
conn = psycopg2.connect(DB)

def q(c, sql, params=()):
    sql = re.sub(r'\?', '%s', sql)
    cur = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(sql, params)
    return cur.fetchall()

# Load NBA pool for comp engine
print("Building pools…", flush=True)
nba_pool = ae.annotate(ae.load_pool(conn, q))
cbb_pool  = comp_engine.build_historical_pool(conn, q, current_season=2026)
print(f"NBA pool: {len(nba_pool)}  CBB comp pool: {len(cbb_pool)}\n")

# Load all prospects
prospects = q(conn, """
    SELECT name, pos, school, rank, age, height, weight
    FROM archive_draft_prospects_2026
    ORDER BY rank::int
""")

SEP = "─" * 68
ARCH_ORDER = ae._ARCH_ORDER

no_cbb   = []   # no college data at all
warnings = []   # archetype or comp issues worth noting

print(f"{'#':>3}  {'Name':<24} {'Pos':<5} {'School':<22}  {'Primary Archetype':<22}  Top Comp")
print("─" * 110)

for p in prospects:
    rank = int(p["rank"])
    name = p["name"]
    pos  = p["pos"] or ""
    school = p["school"] or ""

    # 1. Archetype mix via CBB adapter
    mix = ada.compute_archetype_mix(conn, q, player_name=name)

    # 2. Top comp via CBB comp engine
    try:
        pick_info = svc._KNOWN_PICKS.get(name.lower(), {})
        overall_pick = float(pick_info.get("overall_pick") or rank)
        age_at_draft = float(pick_info.get("age_at_draft") or p["age"] or 20)
        college = pick_info.get("college") or school

        raw_comps = comp_engine.find_top_comps(
            conn, q, cbb_pool,
            player_name=name,
            college=college,
            age_at_draft=age_at_draft,
            overall_pick=overall_pick,
            top_n=3,
        )
        top_comp = raw_comps[0] if raw_comps else None
    except Exception as e:
        top_comp = None
        # uncomment to debug: import traceback; traceback.print_exc()

    # Format output
    if mix is None:
        primary = "NO CBB DATA"
        no_cbb.append((rank, name, school))
    else:
        primary = max(mix, key=mix.get)
        pct = mix[primary]
        primary = f"{primary} ({pct:.0f}%)"

    comp_str = "—"
    if top_comp:
        comp_str = f"{top_comp['player']} {top_comp['draft_season']} ({top_comp['similarity']:.0f}%)"

    print(f"{rank:>3}  {name:<24} {pos:<5} {school:<22}  {primary:<30}  {comp_str}")

    # Flag potential issues
    if mix is None:
        pass  # already tracked in no_cbb
    else:
        dom = max(mix, key=mix.get)
        dom_pct = mix[dom]

        # Flag if top archetype is surprising given position
        flag = None
        pos_upper = pos.upper()
        if "G" in pos_upper and dom in ("Rim Protector", "Scoring Big", "Playmaking Big") and dom_pct > 20:
            flag = f"Guard reading as {dom} ({dom_pct:.0f}%) — check height/stats"
        elif "C" in pos_upper and dom in ("Heliocentric Engine", "3&D Wing") and dom_pct > 25:
            flag = f"Center reading as {dom} ({dom_pct:.0f}%) — check stats"
        elif dom_pct < 18:
            flag = f"Very diffuse mix — no clear dominant archetype (max {dom_pct:.0f}%)"
        elif mix.get("Rim Protector", 0) > 15 and pos_upper in ("G", "G-F") and rank <= 60:
            flag = f"High Rim Protector for a guard: {mix['Rim Protector']:.0f}%"

        if flag:
            warnings.append((rank, name, pos, school, flag, mix))

        if top_comp and top_comp["similarity"] < 60:
            warnings.append((rank, name, pos, school, f"Weak top comp: {top_comp['player']} only {top_comp['similarity']:.0f}% similarity", mix))

print()
print(SEP)
print(f"NO CBB DATA ({len(no_cbb)} players):")
for rank, name, school in no_cbb:
    print(f"  #{rank:>3}  {name}  ({school})")

print()
print(SEP)
print(f"FLAGGED FOR REVIEW ({len(warnings)}):")
for rank, name, pos, school, flag, mix in warnings:
    dom = max(mix, key=mix.get)
    top3 = sorted(mix.items(), key=lambda x: -x[1])[:3]
    mix_str = "  |  ".join(f"{a}: {v:.0f}%" for a,v in top3)
    print(f"  #{rank:>3}  {name} ({pos}, {school})")
    print(f"         ⚠  {flag}")
    print(f"         Mix: {mix_str}")

conn.close()
print()
print("Audit complete.")
