// ── Helpers ──────────────────────────────────────────────────────────────────

const $ = (sel) => document.querySelector(sel);

function toNumber(value, fallback = 0) {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

function animateCountUp(container, duration = 600, selector = "strong") {
  container.querySelectorAll(selector).forEach((el) => {
    const text = el.textContent;
    const match = text.match(/^-?[\d.]+/);
    if (!match) return;
    const target = parseFloat(match[0]);
    if (!Number.isFinite(target)) return;
    const suffix = text.slice(match[0].length);
    const decimals = (match[0].split(".")[1] || "").length;
    const start = performance.now();
    function tick(now) {
      const p = Math.min((now - start) / duration, 1);
      const eased = 1 - Math.pow(1 - p, 3);
      el.textContent = (target * eased).toFixed(decimals) + suffix;
      if (p < 1) requestAnimationFrame(tick);
      else el.textContent = text;
    }
    requestAnimationFrame(tick);
  });
}

function seasonStart(season) {
  return Number(String(season).slice(0, 4));
}

function titleCase(value) {
  return String(value || "").toLowerCase().replace(/\b[a-z]/g, (c) => c.toUpperCase());
}

function initials(name) {
  return name.split(" ").map((p) => p[0]).join("");
}

function playerPhotoUrl(playerId) {
  if (!playerId) return null;
  return `/api/player-photo/${playerId}`;
}

function playerAvatar(playerId, name, colors, size = 48) {
  const url = playerPhotoUrl(playerId);
  const bg = colors ? `linear-gradient(135deg,${colors[0]},${colors[1]})` : "linear-gradient(135deg,#1e3a5f,#0f2040)";
  const ini = initials(name || "?");
  const fontSize = Math.round(size * 0.34);
  if (url) {
    return `<span class="p-avatar" style="width:${size}px;height:${size}px;background:${bg};font-size:${fontSize}px">
      <img src="${url}" alt="${name}" width="${size}" height="${size}"
           onerror="this.style.display='none';this.parentElement.classList.add('p-avatar-fallback')" />
      <span class="p-avatar-ini">${ini}</span>
    </span>`;
  }
  return `<span class="p-avatar p-avatar-fallback" style="width:${size}px;height:${size}px;background:${bg};font-size:${fontSize}px">
    <span class="p-avatar-ini">${ini}</span>
  </span>`;
}

function fmt(value, key) {
  if (!Number.isFinite(value)) return "0.0";
  if (key === "gp") return Math.round(value);
  return value.toFixed(1);
}

function stableColor(seed, offset = 0) {
  const palette = ["#1e6f5c", "#235b8f", "#bd3340", "#d79d28", "#5f4b8b", "#246a73", "#8a4f2a", "#31572c"];
  let hash = offset;
  for (let i = 0; i < seed.length; i++) hash = (hash * 31 + seed.charCodeAt(i)) % palette.length;
  return palette[Math.abs(hash) % palette.length];
}

function latestSeason(player) {
  return player.seasons[player.seasons.length - 1];
}

// ── Routing ──────────────────────────────────────────────────────────────────

let currentSection = "dashboard";

const MEMBERS_ONLY = new Set(["players","player-profile","watchlist","comparisons","career-outcome","draft-projection","team-dashboard","reports","analytics","lineup"]);

function isLoggedIn() {
  const s = localStorage.getItem('sf_session');
  return s && s !== 'guest';
}

function showLoginWall() {
  const existing = document.getElementById('loginWallModal');
  if (existing) { existing.style.display = 'flex'; return; }
  const modal = document.createElement('div');
  modal.id = 'loginWallModal';
  modal.innerHTML = `
    <div class="lw-card">
      <div class="lw-icon">🔒</div>
      <h3 class="lw-title">Members Only</h3>
      <p class="lw-sub">Create a free account or sign in to access this section.</p>
      <button class="lw-btn" id="lwSignInBtn">Sign In / Create Account</button>
      <button class="lw-dismiss" id="lwDismissBtn">Maybe later</button>
    </div>`;
  document.body.appendChild(modal);
  document.getElementById('lwSignInBtn').addEventListener('click', () => {
    modal.style.display = 'none';
    localStorage.removeItem('sf_session');
    location.reload();
  });
  document.getElementById('lwDismissBtn').addEventListener('click', () => {
    modal.style.display = 'none';
  });
}

function navigate(section) {
  if (MEMBERS_ONLY.has(section) && !isLoggedIn()) {
    localStorage.setItem('sf_redirect', section);
    if (document.getElementById('appShell').style.display !== 'none') {
      showLoginWall();
    }
    return;
  }
  document.querySelectorAll(".page").forEach((p) => p.classList.add("hidden"));
  document.querySelectorAll(".nav-item").forEach((b) => b.classList.remove("active"));
  const page = $(`#page-${section}`);
  if (page) page.classList.remove("hidden");
  // player-profile is a sub-page of players — keep Players nav highlighted
  const navSection = section === "player-profile" ? "players" : section;
  if (section !== "player-profile") currentSection = section;
  const btn = document.querySelector(`.nav-item[data-section="${navSection}"]`);
  if (btn) btn.classList.add("active");
  // keep URL in sync so links are shareable
  history.replaceState(null, '', '#' + section);

  if (section === "dashboard" && !dashboardLoaded) loadDashboard();
  if (section === "players") {
    if (!playersLoaded) initPlayers();
    else renderPlayerGrid();
  }
  if (section === "player-profile") renderPlayerProfile();
  if (section === "prospects" && !prospectsLoaded) loadProspects();
  if (section === "draft" && !draftLoaded) loadDraft();
  if (section === "watchlist") renderWatchlist();
  if (section === "comparisons" && !playersLoaded) initPlayers();
  if (section === "career-outcome") {
    if (!prospectsLoaded) loadProspects().then(() => renderCareerOutcomeBar());
    else renderCareerOutcomeBar();
    renderCareerOutcomePage();
  }
  if (section === "draft-projection") {
    if (!prospectsLoaded) loadProspects().then(() => renderDraftProjectionBar());
    else renderDraftProjectionBar();
    renderDraftProjectionPage();
  }
  closeMobileNav();

  if (typeof gtag === 'function') {
    gtag('event', 'page_view', {
      page_title: section,
      page_location: location.href,
      page_path: '/#' + section,
    });
  }
}

document.querySelectorAll(".nav-item").forEach((btn) => {
  btn.addEventListener("click", () => navigate(btn.dataset.section));
});

// ── Mobile nav drawer ───────────────────────────────────────────────────────
// The sidebar is the only navigation in the app; on narrow viewports it's an
// off-canvas drawer toggled by a hamburger button instead of being hidden
// outright (which used to leave mobile users with no way to navigate at all).
function openMobileNav() {
  $("#sidebar")?.classList.add("open");
  $("#mobileNavBackdrop")?.classList.add("open");
}
function closeMobileNav() {
  $("#sidebar")?.classList.remove("open");
  $("#mobileNavBackdrop")?.classList.remove("open");
}
$("#mobileNavToggle")?.addEventListener("click", openMobileNav);
$("#mobileNavBackdrop")?.addEventListener("click", closeMobileNav);

// ── Dashboard ────────────────────────────────────────────────────────────────

let dashboardLoaded = false;

async function loadDashboard(season) {
  dashboardLoaded = true;
  try {
    const url = season ? `/api/dashboard?season=${season}` : "/api/dashboard";
    const data = await fetch(url).then((r) => r.json());
    const s = data.season;
    $("#dashboardSeasonLabel").textContent = `${Number(s) - 1}–${String(Number(s)).slice(-2)} season overview`;
    $("#scoringSeasonBadge").textContent = s;

    // Populate season selector (only once)
    const sel = $("#dashboardSeasonSelect");
    if (sel && data.seasons_available && sel.options.length === 0) {
      data.seasons_available.forEach((yr) => {
        const opt = document.createElement("option");
        opt.value = yr;
        opt.textContent = `${Number(yr) - 1}–${String(Number(yr)).slice(-2)}`;
        if (String(yr) === String(s)) opt.selected = true;
        sel.appendChild(opt);
      });
    } else if (sel) {
      sel.value = String(s);
    }

    $("#scoringLeaders").innerHTML = data.top_scorers.map((p, i) => `
      <tr>
        <td class="rank">${i + 1}</td>
        <td class="highlight">${p.player}</td>
        <td>${p.team || "—"}</td>
        <td class="highlight">${(p.pts || 0).toFixed(1)}</td>
        <td>${(p.reb || 0).toFixed(1)}</td>
        <td>${(p.ast || 0).toFixed(1)}</td>
        <td>${p.fg_pct ? (p.fg_pct).toFixed(1) + "%" : "—"}</td>
      </tr>
    `).join("");
    animateCountUp($("#scoringLeaders"), 600, "td.highlight");

    $("#assistLeaders").innerHTML = data.top_assisters.map((p, i) => `
      <li>
        <span class="mini-rank">${i + 1}</span>
        <span class="player-col"><strong>${p.player}</strong><span>${p.team || "—"}</span></span>
        <span class="stat-val">${(p.ast || 0).toFixed(1)}</span>
      </li>
    `).join("");
    animateCountUp($("#assistLeaders"), 600, ".stat-val");

    $("#reboundLeaders").innerHTML = data.top_rebounders.map((p, i) => `
      <li>
        <span class="mini-rank">${i + 1}</span>
        <span class="player-col"><strong>${p.player}</strong><span>${p.team || "—"}</span></span>
        <span class="stat-val">${(p.reb || 0).toFixed(1)}</span>
      </li>
    `).join("");
    animateCountUp($("#reboundLeaders"), 600, ".stat-val");

    const awardNames = {
      "nba mvp":          "MVP",
      "nba dpoy":         "Defensive Player of the Year",
      "nba roty":         "Rookie of the Year",
      "nba smoy":         "Sixth Man of the Year",
      "nba mip":          "Most Improved Player",
      "nba allstar_mvp":  "All-Star Game MVP",
      "nba finals_mvp":   "NBA Finals MVP",
      "nba clutch_poy":   "Clutch Player of the Year",
    };
    const awardOrder = ["nba mvp","nba dpoy","nba roty","nba smoy","nba mip","nba allstar_mvp","nba finals_mvp","nba clutch_poy"];
    const sortedAwards = [...data.awards].sort((a, b) => {
      const ai = awardOrder.indexOf(a.award), bi = awardOrder.indexOf(b.award);
      return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi);
    });
    $("#awardList").innerHTML = sortedAwards.map((a) => `
      <li>
        <span class="award-name">${awardNames[a.award] || a.award}</span>
        <span class="award-player">${a.player}</span>
      </li>
    `).join("") || "<li style='color:var(--muted)'>No award data for this season.</li>";

    const EAST = new Set(["ATL","BOS","BRK","CHO","CHA","CHI","CLE","DET","IND","MIL","MIA","NYK","ORL","PHI","TOR","WAS"]);
    const WEST = new Set(["DAL","DEN","GSW","HOU","LAC","LAL","MEM","MIN","NOP","OKC","PHO","POR","SAC","SAS","UTA"]);
    const standingRow = (t, i) => {
      const madePlayoffs = String(t.playoffs).toUpperCase() === "TRUE";
      const playoff = madePlayoffs ? `<span class="playoff-dot" title="Made Playoffs">🏆</span>` : "";
      return `
      <tr class="${madePlayoffs ? "playoff-team" : ""}">
        <td class="rank">${i + 1}</td>
        <td class="highlight">${t.team} ${playoff}</td>
        <td>${t.w}</td>
        <td>${t.l}</td>
        <td>${t.win_pct ? (t.win_pct * 100).toFixed(1) + "%" : "—"}</td>
        <td style="color:${t.net_rtg > 0 ? "var(--green)" : "var(--red)"}">${t.net_rtg > 0 ? "+" : ""}${(t.net_rtg || 0).toFixed(1)}</td>
      </tr>`;
    };
    const east = data.team_standings.filter((t) => EAST.has(t.abbreviation)).sort((a,b) => b.w - a.w);
    const west = data.team_standings.filter((t) => WEST.has(t.abbreviation)).sort((a,b) => b.w - a.w);
    $("#eastStandings").innerHTML = east.map(standingRow).join("");
    $("#westStandings").innerHTML = west.map(standingRow).join("");

    $("#sidebarDbStatus").textContent = `${s} data loaded`;
    await renderPlayoffBracket(data.team_standings, s);
  } catch (e) {
    console.warn("Dashboard load failed", e);
    $("#sidebarDbStatus").textContent = "DB error";
  }
}

$("#dashboardSeasonSelect").addEventListener("change", (e) => {
  dashboardLoaded = false;
  loadDashboard(e.target.value);
});

// ── Playoff Bracket ───────────────────────────────────────────────────────────

const EAST_ABBREVS = new Set(["ATL","BOS","BRK","CHO","CHA","CHI","CLE","DET","IND","MIL","MIA","NYK","ORL","PHI","TOR","WAS"]);

async function renderPlayoffBracket(allTeams, season) {
  const bracket = $("#playoffBracket");
  const note = $("#bracketNote");
  if (!bracket) return;

  // Try to fetch real series data first
  let seriesData = [];
  try {
    seriesData = await fetch(`/api/playoffs?season=${season}`).then(r => r.json());
  } catch(e) {}

  if (seriesData.length > 0) {
    // Render real bracket from series data
    if (note) note.textContent = "Official playoff results";

    const bySeries = (conf, round) => seriesData.filter(s => s.conference === conf && s.round === round);

    const seriesSlot = (s, which) => {
      const abbrev = which === 1 ? s.team1_abbrev : s.team2_abbrev;
      const name = which === 1 ? s.team1 : s.team2;
      const seed = which === 1 ? s.team1_seed : s.team2_seed;
      const myWins = which === 1 ? s.team1_wins : s.team2_wins;
      const oppWins = which === 1 ? s.team2_wins : s.team1_wins;
      const isWinner = abbrev === s.winner_abbrev;
      const words = name.split(" ");
      const shortName = words.length > 1 ? words.slice(-1)[0] : name;
      const score = `${myWins}-${oppWins}`;
      return `<div class="b-slot${isWinner ? " b-winner" : ""}">
        <span class="b-seed-num">${seed}</span>
        <span class="b-team-name">${shortName}</span>
        <span class="b-team-rec">${score}</span>
      </div>`;
    };

    const seriesBlock = (s) => `
      <div class="b-matchup">
        ${seriesSlot(s, 1)}
        <div class="b-vs">vs</div>
        ${seriesSlot(s, 2)}
      </div>`;

    const tbd2 = () => `<div class="b-matchup b-matchup-tbd"><div class="b-slot b-tbd"><span class="b-team-name">TBD</span></div><div class="b-vs">vs</div><div class="b-slot b-tbd"><span class="b-team-name">TBD</span></div></div>`;

    const confRounds = (conf, label, west = false) => {
      const r1series = bySeries(conf, 1);
      const r2series = bySeries(conf, 2);
      const r3series = bySeries(conf, 3);
      const r1 = `<div class="b-round"><div class="b-round-label">First Round</div>${r1series.length ? r1series.map(seriesBlock).join("") : tbd2()+tbd2()}</div>`;
      const r2 = `<div class="b-round"><div class="b-round-label">Semifinals</div>${r2series.length ? r2series.map(seriesBlock).join("") : tbd2()+tbd2()}</div>`;
      const r3 = `<div class="b-round"><div class="b-round-label">Conf Finals</div>${r3series.length ? r3series.map(seriesBlock).join("") : tbd2()}</div>`;
      const rounds = west ? [r3, r2, r1].join("") : [r1, r2, r3].join("");
      return `<div class="b-conf"><div class="b-conf-label">${label}</div><div class="b-rounds">${rounds}</div></div>`;
    };

    const finals = bySeries("Finals", 4);
    const finalsHtml = finals.length ? seriesBlock(finals[0]) : `<div class="b-slot b-tbd"><span class="b-team-name">East Champion</span></div><div class="b-vs">vs</div><div class="b-slot b-tbd"><span class="b-team-name">West Champion</span></div>`;

    bracket.innerHTML = `<div class="b-wrap">
      ${confRounds("East","Eastern Conference",false)}
      <div class="b-finals"><div class="b-finals-label">🏆 NBA Finals</div><div class="b-finals-box">${finalsHtml}</div></div>
      ${confRounds("West","Western Conference",true)}
    </div>`;
    return;
  }

  // Fallback: render from standings
  const playoffTeams = allTeams.filter((t) => String(t.playoffs).toUpperCase() === "TRUE");
  const enoughData = playoffTeams.length >= 14;
  let eastSeeds, westSeeds;
  if (enoughData) {
    eastSeeds = playoffTeams.filter((t) => EAST_ABBREVS.has(t.abbreviation)).sort((a, b) => b.w - a.w).slice(0, 8);
    westSeeds = playoffTeams.filter((t) => !EAST_ABBREVS.has(t.abbreviation)).sort((a, b) => b.w - a.w).slice(0, 8);
    if (note) note.textContent = "First-round matchups by regular-season seeding";
  } else {
    eastSeeds = allTeams.filter((t) => EAST_ABBREVS.has(t.abbreviation)).sort((a, b) => b.w - a.w).slice(0, 8);
    westSeeds = allTeams.filter((t) => !EAST_ABBREVS.has(t.abbreviation)).sort((a, b) => b.w - a.w).slice(0, 8);
    if (note) note.textContent = "Projected — based on current standings";
  }

  const teamSlot = (seeds, seedNum) => {
    const t = seeds[seedNum - 1];
    if (!t) return `<div class="b-slot b-tbd"><span class="b-seed-num">${seedNum}</span><span class="b-team-name">TBD</span></div>`;
    return `<div class="b-slot"><span class="b-seed-num">${seedNum}</span><span class="b-team-name">${t.team}</span><span class="b-team-rec">${t.w}–${t.l}</span></div>`;
  };

  const matchup = (seeds, hi, lo) => `<div class="b-matchup">${teamSlot(seeds,hi)}<div class="b-vs">vs</div>${teamSlot(seeds,lo)}</div>`;
  const tbd2 = () => `<div class="b-matchup b-matchup-tbd"><div class="b-slot b-tbd"><span class="b-team-name">TBD</span></div><div class="b-vs">vs</div><div class="b-slot b-tbd"><span class="b-team-name">TBD</span></div></div>`;

  const confBracket = (seeds, label, west = false) => {
    const r1 = `<div class="b-round"><div class="b-round-label">First Round</div>${matchup(seeds,1,8)}${matchup(seeds,4,5)}${matchup(seeds,2,7)}${matchup(seeds,3,6)}</div>`;
    const r2 = `<div class="b-round"><div class="b-round-label">Semifinals</div>${tbd2()}${tbd2()}</div>`;
    const cf = `<div class="b-round"><div class="b-round-label">Conf Finals</div>${tbd2()}</div>`;
    const rounds = west ? [cf, r2, r1].join("") : [r1, r2, cf].join("");
    return `<div class="b-conf"><div class="b-conf-label">${label}</div><div class="b-rounds">${rounds}</div></div>`;
  };

  bracket.innerHTML = `<div class="b-wrap">
    ${confBracket(eastSeeds,"Eastern Conference",false)}
    <div class="b-finals"><div class="b-finals-label">🏆 NBA Finals</div><div class="b-finals-box"><div class="b-slot b-tbd"><span class="b-team-name">East Champion</span></div><div class="b-vs">vs</div><div class="b-slot b-tbd"><span class="b-team-name">West Champion</span></div></div></div>
    ${confBracket(westSeeds,"Western Conference",true)}
  </div>`;
}

// ── Players ──────────────────────────────────────────────────────────────────

const fallbackPlayers = [];
let players = fallbackPlayers;
let dataSource = "loading";
let playersLoaded = false;

const playerState = {
  player: null,
  metric: "pts",
  projMetric: "pts",
  seasonsAhead: 3,
  minutesChange: 0,
  usageChange: 0,
  durabilityChange: 0,
};

const metricLabels = { pts:"PTS", reb:"REB", ast:"AST", min:"MIN", three:"3P", stl:"STL", blk:"BLK", tov:"TOV", net:"BPM", ts:"TS%", gp:"GP" };

function estimateMinutes(row) {
  const pts = toNumber(row.pts), reb = toNumber(row.reb), ast = toNumber(row.ast), usg = toNumber(row.usg_pct) * 100;
  return Math.max(8, Math.min(38, 12 + pts * 0.48 + reb * 0.42 + ast * 0.55 + usg * 0.12));
}

function classifyPosition(latest) {
  if (latest.pos) return latest.pos;
  const h = toNumber(latest.player_height) / 2.54;
  if (h >= 82) return "C";
  if (h >= 80) return "F/C";
  if (h >= 77) return "F";
  if (h >= 75) return "G/F";
  return "G";
}

function playerSummary(player) {
  const l = latestSeason(player);
  return `${player.name} last appeared for ${player.team} in ${l.season}, averaging ${fmt(l.pts,"pts")} pts, ${fmt(l.reb,"reb")} reb, ${fmt(l.ast,"ast")} ast per game.`;
}

function playerNotes(player) {
  const l = latestSeason(player);
  const notes = [
    `${l.gp} games played in the latest available season.`,
    `${fmt(l.usg,"usg")}% usage rate with ${fmt(l.ts,"ts")}% true shooting.`,
    `${fmt(l.net,"net")} box plus/minus in the latest season sample.`,
    `${fmt(l.stl,"stl")} steals, ${fmt(l.blk,"blk")} blocks, and ${fmt(l.tov,"tov")} turnovers per game.`,
  ];
  if (player.country && player.country !== "USA") notes.push(`International profile: ${player.country}.`);
  return notes;
}

function buildPlayersFromRows(rows) {
  const grouped = rows.reduce((map, row) => {
    const name = row.player_name && row.player_name.trim();
    if (!name) return map;
    if (!map.has(name)) map.set(name, []);
    map.get(name).push(row);
    return map;
  }, new Map());

  return [...grouped.entries()].map(([name, rowsForPlayer]) => {
    // For seasons with multiple team entries (trades), keep only TOT row or the highest-games row
    // but also collect the individual teams played for that season
    const seasonMap = {};
    const seasonTeams = {};
    for (const row of rowsForPlayer) {
      const key = row.season;
      const team = row.team_abbreviation || row.team || "";
      if (!seasonMap[key]) {
        seasonMap[key] = row;
        seasonTeams[key] = [];
      }
      if (team !== "TOT") seasonTeams[key].push(team);
      if (team === "TOT") { seasonMap[key] = row; continue; }
      const existing = seasonMap[key];
      const existingTeam = existing.team_abbreviation || existing.team || "";
      if (existingTeam !== "TOT" && toNumber(row.gp) > toNumber(existing.gp)) seasonMap[key] = row;
    }
    const deduped = Object.values(seasonMap);

    const seasons = deduped
      .sort((a, b) => seasonStart(a.season) - seasonStart(b.season))
      .map((row) => ({
        teamsThisSeason: (seasonTeams[row.season] || []).filter((t, i, arr) => arr.indexOf(t) === i),
        season: row.season,
        age: toNumber(row.age),
        gp: toNumber(row.gp),
        min: row.min === undefined ? estimateMinutes(row) : toNumber(row.min),
        pts: toNumber(row.pts),
        reb: toNumber(row.reb),
        ast: toNumber(row.ast),
        three: toNumber(row.three),
        stl: toNumber(row.stl),
        blk: toNumber(row.blk),
        tov: toNumber(row.tov),
        fg: toNumber(row.fg),
        threePct: toNumber(row.three_pct),
        ftPct: toNumber(row.ft_pct),
        net: toNumber(row.net_rating),
        ts: toNumber(row.ts_pct) * 100,
        usg: toNumber(row.usg_pct) * 100,
        per: toNumber(row.per),
        vorp: toNumber(row.vorp),
        ws: toNumber(row.ws),
        ows: toNumber(row.ows),
        dws: toNumber(row.dws),
        team: row.team_abbreviation,
        pos: row.pos,
        playerId: row.player_id,
        country: row.country,
        college: row.college,
        heightCm: toNumber(row.player_height),
        weightKg: toNumber(row.player_weight),
      }))
      .filter((s) => s.season);
    const latest = seasons[seasons.length - 1];
    return {
      id: name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, ""),
      name,
      playerId: latest.playerId || null,
      team: latest.team,
      position: classifyPosition(latest),
      height: latest.heightCm ? `${Math.round(latest.heightCm)} cm` : "N/A",
      weight: latest.weightKg ? `${Math.round(latest.weightKg)} kg` : "N/A",
      age: latest.age,
      country: latest.country,
      college: latest.college,
      experience: `${seasons.length} seasons`,
      archetype: `${latest.pos || titleCase(latest.country) || "NBA"} player profile`,
      colors: [stableColor(name), stableColor(name, 3)],
      seasons,
    };
  })
  .sort((a, b) => {
    const lastName = (n) => n.trim().split(" ").slice(-1)[0].toLowerCase();
    return lastName(a.name).localeCompare(lastName(b.name)) || a.name.localeCompare(b.name);
  })
  .map((p) => ({ ...p, summary: playerSummary(p), notes: playerNotes(p) }));
}

async function initPlayers() {
  if (playersLoaded) return;
  playersLoaded = true;
  renderPlayerGrid();
  try {
    const rows = await fetch("/api/seasons").then((r) => r.json());
    dataSource = "SQLite archive API";
    players = buildPlayersFromRows(rows);
  } catch (e) {
    console.warn("Players API failed", e);
    dataSource = "no data";
  }
  renderPlayerGrid();
}

function weightedAverage(values) {
  const weights = [0.12, 0.16, 0.2, 0.23, 0.29].slice(-values.length);
  const total = weights.reduce((s, w) => s + w, 0);
  return values.reduce((s, v, i) => s + v * weights[i], 0) / total;
}

function slope(seasons, key) {
  const pts = seasons.map((s, i) => ({ x: i + 1, y: s[key] }));
  const xAvg = weightedAverage(pts.map((p) => p.x));
  const yAvg = weightedAverage(pts.map((p) => p.y));
  const num = pts.reduce((s, p) => s + (p.x - xAvg) * (p.y - yAvg), 0);
  const den = pts.reduce((s, p) => s + (p.x - xAvg) ** 2, 0);
  return den ? num / den : 0;
}

function ageCurve(age, key) {
  if (key === "ts") return age > 34 ? -0.6 : age > 31 ? -0.25 : age < 27 ? 0.15 : 0;
  if (key === "net") return age > 34 ? -0.035 : age < 27 ? 0.02 : 0;
  if (age < 25) return 0.045;
  if (age < 29) return 0.018;
  if (age < 32) return -0.008;
  if (age < 35) return -0.028;
  return -0.055;
}

function projectPlayer(player) {
  const seasons = player.seasons;
  const last = latestSeason(player);
  const projections = [];
  const keys = ["pts","reb","ast","three","stl","blk","tov","net","ts","min","gp","usg"];
  const firstYear = Number(last.season.slice(0, 4));

  for (let year = 1; year <= playerState.seasonsAhead; year++) {
    const age = last.age + year;
    const sy = firstYear + year - 1;
    const proj = { season: `${sy}-${String((sy + 1) % 100).padStart(2, "0")}`, age };
    keys.forEach((key) => {
      const recent = seasons.slice(-5).map((s) => s[key]);
      proj[key] = weightedAverage(recent) + slope(seasons.slice(-5), key) * 0.42 * year + weightedAverage(recent) * ageCurve(age, key) * year;
    });
    proj.min += playerState.minutesChange;
    proj.usg += playerState.usageChange;
    proj.gp += playerState.durabilityChange;
    const mf = proj.min / (last.min || 1), uf = proj.usg / (last.usg || 1);
    proj.pts *= 0.58 * mf + 0.42 * uf;
    proj.ast *= 0.7 * mf + 0.3 * uf;
    proj.reb *= 0.86 * mf + 0.14 * uf;
    proj.three *= 0.7 * mf + 0.3 * uf;
    proj.stl *= 0.85 * mf + 0.15 * uf;
    proj.blk *= 0.9 * mf + 0.1 * uf;
    proj.tov *= 0.62 * mf + 0.38 * uf;
    proj.net *= 0.74 * mf + 0.26 * uf;
    proj.gp = Math.max(38, Math.min(82, proj.gp));
    proj.min = Math.max(22, Math.min(39, proj.min));
    proj.usg = Math.max(18, Math.min(39, proj.usg));
    proj.ts = Math.max(39, Math.min(72, proj.ts));
    projections.push(proj);
  }
  return projections;
}

function renderPlayerGrid() {
  const grid = $("#playerGrid");
  if (!grid) return;
  if (!playersLoaded || players.length === 0) {
    grid.innerHTML = `<div class="loading-state">Loading players...</div>`;
    if ($("#playerCount")) $("#playerCount").textContent = "Loading...";
    if ($("#matchCount")) $("#matchCount").textContent = "";
    return;
  }
  const FEATURED = ["LeBron James", "Victor Wembanyama", "Luka Dončić", "Michael Jordan", "Stephen Curry", "Kobe Bryant", "Kevin Durant"];
  const query = ($("#playerSearch") ? $("#playerSearch").value : "").toLowerCase().trim();
  const matches = query
    ? players.filter((p) => [p.name, p.team || "", p.position || ""].join(" ").toLowerCase().includes(query))
    : FEATURED.map((name) => players.find((p) => p.name === name)).filter(Boolean);
  if ($("#playerCount")) $("#playerCount").textContent = query ? `${matches.length.toLocaleString()} results` : "Most Popular Searches";
  if ($("#matchCount")) $("#matchCount").textContent = query ? "" : "Search above to find any player";
  grid.innerHTML = "";
  matches.forEach((player) => {
    const l = latestSeason(player);
    const card = document.createElement("div");
    card.className = "player-card";
    card.innerHTML = `
      <div class="player-card-avatar" style="background:linear-gradient(135deg,${player.colors[0]},${player.colors[1]})">
        <span class="player-card-ini">${initials(player.name)}</span>
      </div>
      <div class="player-card-name">${player.name}</div>
      <div class="player-card-meta">${l.team || "—"} · ${player.position || "—"} · ${l.season}</div>
      <div class="player-card-stats">
        <div class="player-card-stat"><strong>${fmt(l.pts,"pts")}</strong><span>PTS</span></div>
        <div class="player-card-stat"><strong>${fmt(l.reb,"reb")}</strong><span>REB</span></div>
        <div class="player-card-stat"><strong>${fmt(l.ast,"ast")}</strong><span>AST</span></div>
      </div>`;
    if (player.playerId) {
      const avatarDiv = card.querySelector(".player-card-avatar");
      const img = new Image();
      img.onload = () => { avatarDiv.innerHTML = ""; avatarDiv.appendChild(img); };
      img.src = playerPhotoUrl(player.playerId);
      img.alt = player.name;
      img.style.cssText = "width:100%;height:100%;object-fit:cover;object-position:top center;border-radius:10px;";
    }
    card.addEventListener("click", () => openPlayerProfile(player));
    grid.appendChild(card);
  });
}

function openPlayerProfile(player) {
  playerState.player = player;
  navigate("player-profile");
}

function renderPlayerProfile() {
  if (!playerState.player) return;
  const player = playerState.player;
  if (typeof gtag === 'function') {
    gtag('event', 'player_profile_view', {
      player_name: player.name,
      player_id: player.id,
    });
  }
  const projections = projectPlayer(player);
  renderProfile(player);
  renderProjectionsPane(player, projections);
  renderSeasonTable(player);
  renderPredictions(player, projections);
  loadCollegeStatsPanel(player);
  syncControls();
  drawRadar(player);
  drawChart(player, projections, true);
  drawDevCurve(player, true);
  // Reset to overview tab
  document.querySelectorAll(".profile-tab").forEach((t) =>
    t.classList.toggle("active", t.dataset.pane === "overview")
  );
  document.querySelectorAll(".profile-pane").forEach((p) =>
    p.classList.toggle("hidden", p.id !== "pane-overview")
  );
  // Reset archetype lazy-load flag so switching players reloads it
  const archetypePanel = $("#archetypePanel");
  if (archetypePanel) {
    delete archetypePanel.dataset.loaded;
    archetypePanel.innerHTML = `<p class="pcard-summary" style="color:var(--muted)">Select this tab to load archetype data&hellip;</p>`;
  }
  // Update watchlist button
  const ids = getWatchlist();
  const btn = $("#profileWatchlistBtn");
  if (btn) {
    const saved = ids.includes(player.id);
    btn.textContent = saved ? "✓ Saved" : "🔖 Save";
    btn.classList.toggle("saved", saved);
  }
}

function drawRadar(player) {
  const canvas = $("#skillRadar");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const l = latestSeason(player);
  const w = canvas.width, h = canvas.height;
  const cx = w / 2, cy = h / 2;
  const r = Math.min(w, h) / 2 - 50;
  const skills = [
    { label: "Scoring",    value: Math.min(l.pts / 32, 1) },
    { label: "Playmaking", value: Math.min(l.ast / 11, 1) },
    { label: "Rebounding", value: Math.min(l.reb / 14, 1) },
    { label: "Defense",    value: Math.min((l.stl * 2 + l.blk) / 5, 1) },
    { label: "Efficiency", value: Math.min(l.ts > 0 ? l.ts / 70 : 0.5, 1) },
    { label: "Durability", value: Math.min(l.gp / 75, 1) },
  ];
  const n = skills.length;
  const step = (Math.PI * 2) / n;
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = "#182030";
  ctx.fillRect(0, 0, w, h);
  // Grid rings
  [0.25, 0.5, 0.75, 1].forEach((pct) => {
    ctx.beginPath();
    for (let i = 0; i < n; i++) {
      const a = -Math.PI / 2 + step * i;
      const x = cx + r * pct * Math.cos(a), y = cy + r * pct * Math.sin(a);
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    }
    ctx.closePath();
    ctx.strokeStyle = "rgba(255,255,255,0.08)";
    ctx.lineWidth = 1;
    ctx.stroke();
  });
  // Spokes
  for (let i = 0; i < n; i++) {
    const a = -Math.PI / 2 + step * i;
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.lineTo(cx + r * Math.cos(a), cy + r * Math.sin(a));
    ctx.strokeStyle = "rgba(255,255,255,0.08)";
    ctx.lineWidth = 1;
    ctx.stroke();
  }
  // Filled polygon
  ctx.beginPath();
  skills.forEach((s, i) => {
    const a = -Math.PI / 2 + step * i;
    const x = cx + r * s.value * Math.cos(a), y = cy + r * s.value * Math.sin(a);
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  });
  ctx.closePath();
  ctx.fillStyle = player.colors[0] + "44";
  ctx.fill();
  ctx.strokeStyle = player.colors[0];
  ctx.lineWidth = 2.5;
  ctx.stroke();
  // Vertex dots
  skills.forEach((s, i) => {
    const a = -Math.PI / 2 + step * i;
    const x = cx + r * s.value * Math.cos(a), y = cy + r * s.value * Math.sin(a);
    ctx.beginPath();
    ctx.arc(x, y, 4, 0, Math.PI * 2);
    ctx.fillStyle = player.colors[0];
    ctx.fill();
  });
  // Labels
  skills.forEach((s, i) => {
    const a = -Math.PI / 2 + step * i;
    const lr = r + 32;
    const x = cx + lr * Math.cos(a), y = cy + lr * Math.sin(a);
    ctx.textAlign = "center";
    ctx.font = "bold 11px system-ui";
    ctx.fillStyle = "rgba(220,232,255,0.75)";
    ctx.fillText(s.label, x, y + 4);
    ctx.font = "10px system-ui";
    ctx.fillStyle = "rgba(220,232,255,0.35)";
    ctx.fillText(`${Math.round(s.value * 100)}`, x, y + 17);
  });
}

function renderProfile(player) {
  const l = latestSeason(player);
  $("#playerTeam").textContent = `${player.team} · ${player.position}`;
  $("#playerName").textContent = player.name;
  $("#profileUpdated").textContent = dataSource;
  const avatarEl = $("#playerAvatar");
  avatarEl.style.background = `linear-gradient(135deg, ${player.colors[0]}, ${player.colors[1]})`;
  avatarEl.classList.remove("avatar-fallback");
  if (player.playerId) {
    const ini = initials(player.name);
    avatarEl.innerHTML = `<span class="avatar-ini">${ini}</span>`;
    const img = new Image();
    img.onload = () => { avatarEl.innerHTML = ""; avatarEl.appendChild(img); };
    img.onerror = () => {};
    img.src = playerPhotoUrl(player.playerId);
    img.alt = player.name;
    img.style.cssText = "width:100%;height:100%;object-fit:cover;object-position:top center;border-radius:12px;";
  } else {
    avatarEl.innerHTML = `<span class="avatar-ini">${escapeHtml(initials(player.name))}</span>`;
  }
  $("#playerSummary").textContent = player.summary;
  $("#playerTags").innerHTML = [player.archetype, player.experience, `${fmt(l.pts,"pts")} PPG`, `${fmt(l.ast,"ast")} APG`]
    .map((t) => `<span class="tag">${t}</span>`).join("");
  $("#heroStats").innerHTML = [["PTS",l.pts],["REB",l.reb],["AST",l.ast],["3P",l.three],["TS%",l.ts]]
    .map(([label, val]) => `<div class="hero-stat"><strong>${fmt(val,"pts")}</strong><span>${label} last season</span></div>`).join("");
  animateCountUp($("#heroStats"));
  const facts = [["Age",player.age],["Position",player.position],["Experience",player.experience],
    ["Minutes",fmt(l.min,"min")],["Usage",`${fmt(l.usg,"usg")}%`],["Games",l.gp],
    ["College",player.college||"—"],["Season",l.season],["Team",l.team||"—"]];
  $("#profileFacts").innerHTML = facts.map(([label,val]) => `<div><dt>${label}</dt><dd>${val}</dd></div>`).join("");
  $("#scoutingNotes").innerHTML = player.notes.map((n) => `<li>${n}</li>`).join("");
}

function drawCourt(player) {
  const canvas = $("#courtCanvas");
  const ctx = canvas.getContext("2d");
  const [primary, secondary] = player.colors;
  const w = canvas.width, h = canvas.height;
  const grad = ctx.createLinearGradient(0, 0, w, h);
  grad.addColorStop(0, primary); grad.addColorStop(1, "#171717");
  ctx.fillStyle = grad; ctx.fillRect(0, 0, w, h);
  ctx.strokeStyle = secondary; ctx.globalAlpha = 0.5; ctx.lineWidth = 4;
  ctx.strokeRect(60, 30, w - 120, h - 60);
  ctx.beginPath(); ctx.arc(w/2, h/2, 60, 0, Math.PI*2); ctx.stroke();
  ctx.beginPath(); ctx.arc(w/2, h/2, 14, 0, Math.PI*2); ctx.stroke();
  ctx.strokeRect(60, h/2 - 70, 140, 140);
  ctx.strokeRect(w - 200, h/2 - 70, 140, 140);
  ctx.globalAlpha = 0.15;
  for (let i = 0; i < 50; i++) {
    ctx.fillStyle = i % 3 === 0 ? secondary : "#fff";
    ctx.fillRect(Math.random() * w, Math.random() * h, 2, 2);
  }
  ctx.globalAlpha = 1;
}

function computeTypicalRangeByAge(metric) {
  const byAge = {};
  for (const p of players) {
    for (const s of p.seasons) {
      const age = Math.round(s.age);
      if (!age || age < 18 || age > 42) continue;
      const val = s[metric];
      if (!Number.isFinite(val) || val <= 0) continue;
      if (!byAge[age]) byAge[age] = [];
      byAge[age].push(val);
    }
  }
  const result = {};
  for (let age = 18; age <= 42; age++) {
    const vals = byAge[age] || [];
    if (vals.length < 10) continue;
    const sorted = [...vals].sort((a, b) => a - b);
    const n = sorted.length;
    result[age] = {
      p25:    sorted[Math.floor(n * 0.25)],
      median: sorted[Math.floor(n * 0.50)],
      p75:    sorted[Math.floor(n * 0.75)],
    };
  }
  return result;
}

// ── Predictions & Value ──────────────────────────────────────────────────────

function escapeHtml(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

async function loadArchetypePanel(player) {
  const panel = $("#archetypePanel");
  const sub = $("#archetypeSub");
  if (!panel) return;
  const last = latestSeason(player);
  if (!last || !last.playerId || !last.season) {
    panel.innerHTML = "<p class='pcard-summary'>Archetype data unavailable for this player.</p>";
    return;
  }
  panel.innerHTML = "<p class='pcard-summary'>Loading archetype model&hellip;</p>";
  try {
    const url = `/api/archetype?player_id=${encodeURIComponent(last.playerId)}&season=${encodeURIComponent(last.season)}`;
    const report = await fetch(url).then((r) => (r.ok ? r.json() : Promise.reject(r.status)));
    renderArchetypePanel(report, player);
    if (sub) sub.textContent = `${report.season} · ${report.development_stage.replace("_", " ")}`;
  } catch (e) {
    panel.innerHTML = "<p class='pcard-summary'>Archetype data unavailable for this player/season.</p>";
  }
}

function renderArchetypePanel(report, player) {
  const panel = $("#archetypePanel");
  if (!panel) return;

  const weights = Object.entries(report.archetype_weights)
    .filter(([, w]) => w > 1)
    .sort((a, b) => b[1] - a[1]);
  const weightBars = weights
    .map(
      ([label, w]) => `<div><dt>${escapeHtml(label)}</dt><dd>${w}%</dd></div>`
    )
    .join("");

  const sameStageRows = report.same_stage_comps
    .map(
      (c) => `<li>${escapeHtml(c.player)} (${c.season}) &mdash; ${c.similarity}%<br><span class="comp-explanation">${escapeHtml(c.explanation || "")}</span></li>`
    )
    .join("");
  const projectedRows = report.projected_engine_comps
    .map(
      (c) => `<li>${escapeHtml(c.player)} (${c.season}) &mdash; ${c.engine_similarity}% engine match<br><span class="comp-explanation">${escapeHtml(c.explanation || "")}</span></li>`
    )
    .join("");

  const shotCreationHtml = report.college_shot_creation ? `
    <div style="margin-top:20px">
      <p class="pcard-summary" style="margin-bottom:8px"><strong>College Shot Creation by Zone</strong> &mdash; most recent college season</p>
      <div style="font-size:0.75rem;color:var(--muted);margin-bottom:8px">What % of made shots were self-created vs. assisted</div>
      ${buildShotCreationPanel(report.college_shot_creation)}
    </div>` : "";

  panel.innerHTML = `
    <div class="overview-top-row">
      <div class="profile-col">
        <div class="pfact-grid">${weightBars}</div>
        ${shotCreationHtml}
      </div>
      <div class="profile-col">
        <p class="pcard-summary"><strong>Same-stage comps</strong> &mdash; strict &plusmn;2yr age/experience band, true statistical comparison:</p>
        <ul class="pcard-notes">${sameStageRows}</ul>
        <p class="pcard-summary" style="margin-top:14px;"><strong>Projected engine comps</strong> &mdash; scouting layer, no age band, offensive-engine match only:</p>
        <ul class="pcard-notes">${projectedRows}</ul>
      </div>
    </div>
  `;
}

async function renderPredictions(player, projections = []) {
  const el = $("#predGrid");
  if (!el) return;

  const seasons = (player.seasons || []).filter(s => s.pts != null);
  if (!seasons.length) { el.innerHTML = "<p style='color:var(--muted)'>Not enough data</p>"; return; }

  const last = seasons[seasons.length - 1];
  const age  = parseFloat(last.age) || 26;

  const nextSeason = projections[0] || null;
  const projPts = nextSeason ? (nextSeason.pts || 0).toFixed(1) : "—";
  const projReb = nextSeason ? (nextSeason.reb || 0).toFixed(1) : "—";
  const projAst = nextSeason ? (nextSeason.ast || 0).toFixed(1) : "—";
  const projMin = nextSeason ? (nextSeason.min || 0).toFixed(1) : "—";

  const tsPct  = (parseFloat(last.ts) || 0) / 100;   // ts stored as 0-100
  const bpm    = parseFloat(last.net) || 0;           // net_rating stored as net
  const vorp   = parseFloat(last.vorp) || 0;
  const perVal = parseFloat(last.per) || 15;
  const ws     = parseFloat(last.ws) || 0;

  const pts     = parseFloat(last.pts) || 0;
  const ast     = parseFloat(last.ast) || 0;
  const reb     = parseFloat(last.reb) || 0;

  const perScore  = Math.min(100, Math.max(0, ((perVal - 5) / 30) * 100));
  const bpmScore  = Math.min(100, Math.max(0, ((bpm + 5) / 20) * 100));
  const tsScore   = Math.min(100, Math.max(0, ((tsPct - 0.40) / 0.35) * 100));
  const vorpScore = Math.min(100, Math.max(0, ((vorp + 1) / 9) * 100));
  const ptsScore  = Math.min(100, Math.max(0, (pts / 35) * 100));
  const astScore  = Math.min(100, Math.max(0, (ast / 12) * 100));
  const rebScore  = Math.min(100, Math.max(0, (reb / 15) * 100));
  const effScore  = Math.round(
    perScore  * 0.20 +
    bpmScore  * 0.15 +
    tsScore   * 0.10 +
    vorpScore * 0.15 +
    ptsScore  * 0.20 +
    astScore  * 0.10 +
    rebScore  * 0.10
  );

  const [tierLabel, tierColor] =
    effScore >= 80 ? ["MVP Caliber", "#f0c040"] :
    effScore >= 65 ? ["All-Star",    "#5b8af0"] :
    effScore >= 50 ? ["Starter",     "#3ecf8e"] :
    effScore >= 35 ? ["Rotation",    "#7a8fb0"] :
                     ["Developmental","rgba(255,255,255,0.3)"];

  // Arc gauge math — semicircle r=50, center 65,68
  const arcR = 50, arcCx = 65, arcCy = 68;
  const arcLen = Math.PI * arcR; // ≈ 157.08
  const arcOffset = arcLen * (1 - effScore / 100);

  // ML salary
  let mlSalaryM = null, mlSalaryPct = null;
  try {
    const res = await fetch(`/api/salary-predict?player_id=${encodeURIComponent(player.playerId)}`);
    if (res.ok) { const d = await res.json(); mlSalaryM = d.predicted_salary_m; mlSalaryPct = d.salary_pct; }
  } catch (_) {}

  if (mlSalaryM === null) {
    const raw = Math.max(0, (vorp * 6.5) + (ws * 1.8));
    mlSalaryM   = Math.min(62, Math.max(1.2, raw));
    mlSalaryPct = (mlSalaryM / 155) * 100;
  }
  const mlTier = mlSalaryM >= 40 ? "Max Contract" :
                 mlSalaryM >= 25 ? "Near-Max"     :
                 mlSalaryM >= 15 ? "Starter"       :
                 mlSalaryM >= 7  ? "Role Player"   : "Minimum";

  // XGBoost stats prediction
  let xgPreds = null;
  try {
    const res = await fetch(`/api/stats-predict?player_id=${encodeURIComponent(player.playerId)}`);
    if (res.ok) { const d = await res.json(); xgPreds = d.predictions; }
  } catch (_) {}

  const fmt1 = (v, fallback="—") => v != null ? Number(v).toFixed(1) : fallback;
  const fmtPct = (v, fallback="—") => v != null ? (Number(v) * 100).toFixed(1) + "%" : fallback;
  const nextSeasonStart = parseInt(last.season)||2025;
  const nextSeasonLabel = xgPreds ? `${nextSeasonStart}-${String(nextSeasonStart+1).slice(2)}` : "Next Season";

  // Circular ring math — r=42, circumference≈263.9
  const ringR = 42, ringC = 2 * Math.PI * ringR;
  const ringOffset = ringC * (1 - Math.min(mlSalaryM, 62) / 62);

  // ── Potential Score ──────────────────────────────────────────────────────
  // Peak projected score: take best pts/reb/ast season from trajectory
  const bestProjScore = projections.length > 0
    ? Math.max(...projections.map(s => {
        const p = Math.min(100, ((s.pts||0) / 35) * 100);
        const r = Math.min(100, ((s.reb||0) / 15) * 100);
        const a = Math.min(100, ((s.ast||0) / 12) * 100);
        return p * 0.5 + r * 0.25 + a * 0.25;
      }))
    : effScore;
  // Age ceiling: younger players have more room to grow
  const ageCeiling = age <= 22 ? 1.22 : age <= 25 ? 1.12 : age <= 28 ? 1.04 : age <= 32 ? 0.97 : 0.88;
  const potScore = Math.min(100, Math.round(Math.max(effScore, bestProjScore) * ageCeiling));

  const [potTier, potColor, potDesc] =
    potScore >= 85 ? ["Superstar",    "#f0c040", "Franchise cornerstone, perennial All-Star candidate"] :
    potScore >= 70 ? ["All-Star",     "#5b8af0", "Projected ceiling as a consistent All-Star contributor"] :
    potScore >= 55 ? ["Solid Starter","#3ecf8e", "Expected to hold a starting role at peak performance"] :
    potScore >= 40 ? ["Rotation",     "#7a8fb0", "Viable rotation piece with a defined role"] :
                     ["Developmental","rgba(255,255,255,0.35)", "Still developing — ceiling highly uncertain"];

  // Needle math: semi-circle center (110,115), r=90
  // Score 0 → left end, Score 100 → right end
  const potAngle = Math.PI * (1 - potScore / 100); // radians from positive x-axis
  const potNx = 110 + 82 * Math.cos(potAngle);
  const potNy = 115 - 82 * Math.sin(potAngle);
  // Arrowhead: two points perpendicular to needle direction, near the tip
  const perpAngle = potAngle + Math.PI / 2;
  const tipX = 110 + 90 * Math.cos(potAngle);
  const tipY = 115 - 90 * Math.sin(potAngle);
  const arr1x = (potNx + 6 * Math.cos(perpAngle)).toFixed(1);
  const arr1y = (potNy - 6 * Math.sin(perpAngle)).toFixed(1);
  const arr2x = (potNx - 6 * Math.cos(perpAngle)).toFixed(1);
  const arr2y = (potNy + 6 * Math.sin(perpAngle)).toFixed(1);

  // Age factor label for factors strip
  const ageLabel = age <= 22 ? `Age ${age} · High upside` :
                   age <= 25 ? `Age ${age} · Still developing` :
                   age <= 28 ? `Age ${age} · Entering prime` :
                   age <= 32 ? `Age ${age} · Peak or declining` :
                               `Age ${age} · Likely declining`;

  el.innerHTML = `
    <!-- Potential Score panel -->
    <div class="pred-panel pred-potential-panel">
      <div class="pred-pot-gauge-wrap">
        <svg class="pred-pot-svg" viewBox="0 0 220 130" fill="none">
          <defs>
            <linearGradient id="potGrad" x1="20" y1="115" x2="200" y2="115" gradientUnits="userSpaceOnUse">
              <stop offset="0%"   stop-color="#ef4444"/>
              <stop offset="35%"  stop-color="#f97316"/>
              <stop offset="65%"  stop-color="#facc15"/>
              <stop offset="100%" stop-color="#22c55e"/>
            </linearGradient>
            <filter id="needleGlow">
              <feGaussianBlur stdDeviation="2.5" result="b"/>
              <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
            </filter>
          </defs>
          <!-- Background track -->
          <path d="M 20,115 A 90,90 0 0,1 200,115"
            stroke="rgba(255,255,255,0.08)" stroke-width="14" stroke-linecap="round"/>
          <!-- Colored gradient track -->
          <path d="M 20,115 A 90,90 0 0,1 200,115"
            stroke="url(#potGrad)" stroke-width="14" stroke-linecap="round" opacity="0.85"/>
          <!-- Tick marks at 0/25/50/75/100 -->
          ${[0,25,50,75,100].map(v => {
            const a = Math.PI * (1 - v/100);
            const ix = 110 + 97 * Math.cos(a), iy = 115 - 97 * Math.sin(a);
            const ox = 110 + 107 * Math.cos(a), oy = 115 - 107 * Math.sin(a);
            return `<line x1="${ix.toFixed(1)}" y1="${iy.toFixed(1)}" x2="${ox.toFixed(1)}" y2="${oy.toFixed(1)}"
              stroke="rgba(255,255,255,0.3)" stroke-width="1.5"/>`;
          }).join("")}
          <!-- Needle base circle -->
          <circle cx="110" cy="115" r="6" fill="rgba(255,255,255,0.15)" stroke="rgba(255,255,255,0.3)" stroke-width="1.5"/>
          <!-- Needle line -->
          <line x1="110" y1="115" x2="${potNx.toFixed(1)}" y2="${potNy.toFixed(1)}"
            stroke="${potColor}" stroke-width="2.5" stroke-linecap="round" filter="url(#needleGlow)"/>
          <!-- Arrowhead -->
          <polygon points="${tipX.toFixed(1)},${tipY.toFixed(1)} ${arr1x},${arr1y} ${arr2x},${arr2y}"
            fill="${potColor}" filter="url(#needleGlow)"/>
          <!-- Score text -->
          <text x="110" y="98" text-anchor="middle"
            font-size="26" font-weight="800" fill="${potColor}" font-family="inherit">${potScore}</text>
          <text x="110" y="110" text-anchor="middle"
            font-size="9" fill="rgba(255,255,255,0.4)" font-family="inherit">/ 100</text>
        </svg>
        <div class="pred-pot-label">Potential Score</div>
      </div>
      <div class="pred-pot-info">
        <div class="pred-pot-title">Projected Peak Rating</div>
        <div class="pred-pot-tier" style="color:${potColor}">${potTier}</div>
        <div class="pred-pot-desc">${potDesc}</div>
        <div class="pred-pot-factors">
          <span class="pred-pot-factor">${ageLabel}</span>
          <span class="pred-pot-factor">Eff Score ${effScore}/100</span>
          ${projections.length > 0 ? `<span class="pred-pot-factor">${projections.length} season${projections.length > 1 ? "s" : ""} projected</span>` : ""}
        </div>
      </div>
    </div>

    <!-- Efficiency gauge panel -->
    <div class="pred-panel">
      <div class="pred-panel-label">Efficiency Score</div>
      <div class="pred-gauge-wrap">
        <svg class="pred-gauge-svg" viewBox="0 0 130 76" fill="none">
          <path class="pred-arc-bg"
            d="M 15,72 A ${arcR},${arcR} 0 0,1 115,72"
            stroke-width="9"/>
          <path class="pred-arc-fill"
            d="M 15,72 A ${arcR},${arcR} 0 0,1 115,72"
            stroke="${tierColor}" stroke-width="9"
            stroke-dasharray="${arcLen.toFixed(2)}"
            stroke-dashoffset="${arcOffset.toFixed(2)}"/>
          <filter id="arcGlow">
            <feGaussianBlur stdDeviation="2" result="b"/>
            <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
          </filter>
        </svg>
        <div class="pred-gauge-center">
          <div class="pred-gauge-num" style="color:${tierColor}">${effScore}</div>
          <div class="pred-gauge-tier" style="color:${tierColor}">${tierLabel}</div>
        </div>
      </div>
      <div class="pred-gauge-stats">
        <span class="pred-gs">PER <b>${perVal.toFixed(1)}</b></span>
        <span class="pred-gs">BPM <b>${bpm >= 0 ? "+" : ""}${bpm.toFixed(1)}</b></span>
        <span class="pred-gs">TS% <b>${(tsPct * 100).toFixed(1)}%</b></span>
        <span class="pred-gs">VORP <b>${vorp.toFixed(1)}</b></span>
      </div>
    </div>

    <!-- Salary ring panel -->
    <div class="pred-panel pred-salary-panel">
      <div class="pred-panel-label">ML Salary Prediction · RF</div>
      <div class="pred-ring-wrap">
        <svg class="pred-ring-svg" viewBox="0 0 110 110">
          <circle class="pred-ring-bg" cx="55" cy="55" r="${ringR}" stroke-width="8"/>
          <circle class="pred-ring-fill"
            cx="55" cy="55" r="${ringR}"
            stroke="url(#salGrad)" stroke-width="8"
            stroke-dasharray="${ringC.toFixed(2)}"
            stroke-dashoffset="${ringOffset.toFixed(2)}"/>
          <defs>
            <linearGradient id="salGrad" x1="0%" y1="0%" x2="100%" y2="100%">
              <stop offset="0%" stop-color="#f97316"/>
              <stop offset="100%" stop-color="#facc15"/>
            </linearGradient>
          </defs>
        </svg>
        <div class="pred-ring-center">
          <div class="pred-salary-num">$${mlSalaryM.toFixed(1)}M</div>
          <div class="pred-salary-tier">${mlTier}</div>
        </div>
      </div>
      <div class="pred-salary-sub">${mlSalaryPct !== null ? mlSalaryPct.toFixed(1) : "—"}% of salary cap</div>
    </div>

    <!-- XGBoost Stats Prediction panel -->
    <div class="pred-panel pred-xg-panel">
      <div class="pred-panel-label">XGBoost Forecast · ${nextSeasonLabel}</div>
      <div class="pred-xg-grid">
        <div class="pred-xg-tile">
          <div class="pred-xg-val" style="color:#5b8af0">${fmt1(xgPreds?.pts_per_game, projPts)}</div>
          <div class="pred-xg-key">PTS</div>
        </div>
        <div class="pred-xg-tile">
          <div class="pred-xg-val" style="color:#3ecf8e">${fmt1(xgPreds?.trb_per_game, projReb)}</div>
          <div class="pred-xg-key">REB</div>
        </div>
        <div class="pred-xg-tile">
          <div class="pred-xg-val" style="color:#f97316">${fmt1(xgPreds?.ast_per_game, projAst)}</div>
          <div class="pred-xg-key">AST</div>
        </div>
        <div class="pred-xg-tile">
          <div class="pred-xg-val" style="color:#a78bfa">${fmt1(xgPreds?.stl_per_game)}</div>
          <div class="pred-xg-key">STL</div>
        </div>
        <div class="pred-xg-tile">
          <div class="pred-xg-val" style="color:#f87171">${fmt1(xgPreds?.blk_per_game)}</div>
          <div class="pred-xg-key">BLK</div>
        </div>
        <div class="pred-xg-tile">
          <div class="pred-xg-val" style="color:#34d399">${fmt1(xgPreds?.x3p_per_game)}</div>
          <div class="pred-xg-key">3PM</div>
        </div>
      </div>
      <div class="pred-xg-adv">
        <span>FG% <b>${xgPreds ? (xgPreds.fg_percent*100).toFixed(1)+"%" : "—"}</b></span>
        <span>TS% <b>${xgPreds ? (xgPreds.ts_percent*100).toFixed(1)+"%" : "—"}</b></span>
        <span>PER <b>${fmt1(xgPreds?.per)}</b></span>
        <span>VORP <b>${fmt1(xgPreds?.vorp)}</b></span>
        <span>WS <b>${fmt1(xgPreds?.ws)}</b></span>
      </div>
      <div class="pred-xg-note">XGBoost · 18k seasons trained</div>
    </div>
  `;
}

// Canvas charts use a fixed width/height attribute (e.g. 1400x380) but are
// displayed at `width:100%` in CSS, which can be much narrower than 1400px —
// the browser then scales the whole drawing (including text) down to fit,
// making labels unreadably small on anything but a very wide viewport. This
// resizes the backing buffer to match the actual on-screen size (scaled by
// devicePixelRatio for crispness) so fonts are drawn at their real px size.
function sizeCanvasToDisplay(canvas) {
  const dpr = window.devicePixelRatio || 1;
  const aspect = (canvas.getAttribute("height") || canvas.height) / (canvas.getAttribute("width") || canvas.width);
  const cssWidth = canvas.clientWidth || canvas.parentElement.clientWidth || 700;
  const cssHeight = Math.round(cssWidth * aspect);
  canvas.width = Math.round(cssWidth * dpr);
  canvas.height = Math.round(cssHeight * dpr);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { ctx, w: cssWidth, h: cssHeight };
}

function drawChart(player, projections = [], dark = false, canvasId = "trendChart", metric = playerState.metric) {
  const canvas = $(`#${canvasId}`);
  if (!canvas) return;
  const { ctx, w, h } = sizeCanvasToDisplay(canvas);
  const pad = { top: 28, right: 28, bottom: 58, left: 52 };
  const actual = player.seasons.map((s) => ({ label: s.season, value: s[metric], type: "actual" }));
  const proj = projections.map((s) => ({ label: s.season, value: s[metric], type: "projected" }));
  const all = actual.concat(proj);
  const vals = all.map((p) => p.value);
  const minV = metric === "net" ? Math.min(...vals) - 3 : Math.max(0, Math.min(...vals) - 3);
  const maxV = Math.max(...vals) + 3;
  const pw = w - pad.left - pad.right, ph = h - pad.top - pad.bottom;
  const gridColor = dark ? "rgba(255,255,255,0.07)" : "#e3e6e1";
  const labelColor = dark ? "rgba(220,232,255,0.38)" : "#626262";
  const titleColor = dark ? "rgba(220,232,255,0.8)" : "#151515";
  const bgColor = dark ? "#182030" : "#fff";

  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = bgColor; ctx.fillRect(0, 0, w, h);
  ctx.strokeStyle = gridColor; ctx.lineWidth = 1;
  ctx.fillStyle = labelColor; ctx.font = "12px system-ui";
  for (let i = 0; i <= 4; i++) {
    const y = pad.top + (ph / 4) * i;
    const val = maxV - ((maxV - minV) / 4) * i;
    ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(w - pad.right, y); ctx.stroke();
    ctx.fillText(val.toFixed(1), 8, y + 4);
  }
  const ptFor = (pt, idx) => ({
    x: pad.left + (pw / Math.max(all.length - 1, 1)) * idx,
    y: pad.top + ph - ((pt.value - minV) / (maxV - minV)) * ph,
  });
  ctx.lineWidth = 3; ctx.strokeStyle = player.colors[0];
  ctx.beginPath();
  actual.forEach((pt, i) => { const p = ptFor(pt, i); i === 0 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y); });
  ctx.stroke();
  if (proj.length) {
    ctx.setLineDash([8, 6]); ctx.strokeStyle = "#bd3340";
    ctx.beginPath();
    const bridge = ptFor(actual[actual.length - 1], actual.length - 1);
    ctx.moveTo(bridge.x, bridge.y);
    proj.forEach((pt, i) => { const p = ptFor(pt, actual.length + i); ctx.lineTo(p.x, p.y); });
    ctx.stroke(); ctx.setLineDash([]);
  }
  all.forEach((pt, i) => {
    const p = ptFor(pt, i);
    ctx.fillStyle = pt.type === "actual" ? player.colors[0] : "#bd3340";
    ctx.beginPath(); ctx.arc(p.x, p.y, 5, 0, Math.PI * 2); ctx.fill();
    ctx.save(); ctx.translate(p.x, h - 18); ctx.rotate(-0.5);
    ctx.fillStyle = labelColor; ctx.font = "11px system-ui";
    ctx.fillText(pt.label, 0, 0); ctx.restore();
  });
  ctx.fillStyle = titleColor; ctx.font = "700 14px system-ui";
  ctx.fillText(`${metricLabels[metric]} trend`, pad.left, 20);
}

function drawDevCurve(player, dark = false) {
  const canvas = $("#devCurveChart");
  if (!canvas) return;
  const { ctx, w, h } = sizeCanvasToDisplay(canvas);
  const pad = { top: 36, right: 110, bottom: 42, left: 52 };
  const metric = playerState.metric;

  // Player seasons by age
  const playerByAge = {};
  for (const s of player.seasons) {
    const age = Math.round(s.age);
    if (age && Number.isFinite(s[metric])) playerByAge[age] = s[metric];
  }
  const playerAges = Object.keys(playerByAge).map(Number).sort((a, b) => a - b);
  if (playerAges.length === 0) return;

  // Typical range by age across all NBA players
  const typical = players.length > 1 ? computeTypicalRangeByAge(metric) : {};

  // X-axis spans the player's full age range, anchored to ages with data
  const ageMin = Math.min(...playerAges, ...Object.keys(typical).map(Number));
  const ageMax = Math.max(...playerAges, ...Object.keys(typical).map(Number));
  const ageSpan = ageMax - ageMin;

  // Y-axis scale
  const playerVals = playerAges.map((a) => playerByAge[a]);
  const bandVals   = Object.values(typical).flatMap((t) => [t.p25, t.p75]);
  const allVals    = [...playerVals, ...bandVals].filter(Number.isFinite);
  const rawMin = Math.min(...allVals), rawMax = Math.max(...allVals);
  const vPad = (rawMax - rawMin) * 0.15 || 2;
  const minV = metric === "net" ? rawMin - vPad : Math.max(0, rawMin - vPad);
  const maxV = rawMax + vPad;

  const pw = w - pad.left - pad.right, ph = h - pad.top - pad.bottom;
  const xFor = (age) => pad.left + (pw / Math.max(ageSpan, 1)) * (age - ageMin);
  const yFor = (v)   => pad.top  + ph - ((v - minV) / (maxV - minV)) * ph;

  const gridColor  = dark ? "rgba(255,255,255,0.07)" : "#e3e6e1";
  const labelColor = dark ? "rgba(220,232,255,0.75)" : "#626262";
  const titleColor = dark ? "rgba(220,232,255,0.85)" : "#151515";
  const bgColor    = dark ? "#182030" : "#fff";

  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = bgColor; ctx.fillRect(0, 0, w, h);

  // Grid lines + Y labels
  ctx.strokeStyle = gridColor; ctx.lineWidth = 1;
  ctx.fillStyle = labelColor; ctx.font = "bold 15px system-ui";
  for (let i = 0; i <= 4; i++) {
    const y = pad.top + (ph / 4) * i;
    const val = maxV - ((maxV - minV) / 4) * i;
    ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(w - pad.right, y); ctx.stroke();
    ctx.fillText(val.toFixed(1), 4, y + 5);
  }

  // Age labels on X axis
  ctx.fillStyle = labelColor; ctx.font = "bold 14px system-ui";
  for (let age = Math.ceil(ageMin / 2) * 2; age <= ageMax; age += 2) {
    ctx.fillText(`${age}`, xFor(age) - 8, h - 10);
  }
  // "Age" axis label
  ctx.fillStyle = dark ? "rgba(220,232,255,0.55)" : "rgba(80,80,80,0.5)";
  ctx.font = "13px system-ui";
  ctx.fillText("Age", pad.left + pw / 2 - 10, h - 0);

  // ── Typical range band (gray) ──────────────────────────────────────────────
  const typicalAges = Object.keys(typical).map(Number).sort((a, b) => a - b)
    .filter((a) => a >= ageMin && a <= ageMax);

  if (typicalAges.length > 1) {
    // Shaded band
    ctx.beginPath();
    typicalAges.forEach((age, i) => {
      const x = xFor(age), y = yFor(typical[age].p75);
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    });
    [...typicalAges].reverse().forEach((age) => {
      ctx.lineTo(xFor(age), yFor(typical[age].p25));
    });
    ctx.closePath();
    ctx.fillStyle = dark ? "rgba(170,185,210,0.14)" : "rgba(130,145,175,0.16)";
    ctx.fill();

    // Median dashed line
    ctx.setLineDash([5, 4]);
    ctx.strokeStyle = dark ? "rgba(180,190,210,0.45)" : "rgba(100,115,145,0.6)";
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    typicalAges.forEach((age, i) => {
      const x = xFor(age), y = yFor(typical[age].median);
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    });
    ctx.stroke();
    ctx.setLineDash([]);

    // Right-edge percentile labels
    const lastAge = typicalAges[typicalAges.length - 1];
    const lastT   = typical[lastAge];
    const lx = xFor(lastAge) + 6;
    ctx.fillStyle = dark ? "rgba(190,205,230,0.85)" : "rgba(80,95,125,0.7)";
    ctx.font = "bold 13px system-ui";
    ctx.fillText("75th %ile", lx, yFor(lastT.p75) + 4);
    ctx.fillText("Median",    lx, yFor(lastT.median) + 4);
    ctx.fillText("25th %ile", lx, yFor(lastT.p25) + 4);
  }

  // ── Player curve by age ────────────────────────────────────────────────────
  ctx.lineWidth = 2.5; ctx.strokeStyle = player.colors[0];
  ctx.beginPath();
  playerAges.forEach((age, i) => {
    const x = xFor(age), y = yFor(playerByAge[age]);
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  });
  ctx.stroke();

  // Dots
  ctx.fillStyle = player.colors[0];
  playerAges.forEach((age) => {
    ctx.beginPath();
    ctx.arc(xFor(age), yFor(playerByAge[age]), 4, 0, Math.PI * 2);
    ctx.fill();
  });

  // Title
  ctx.fillStyle = titleColor; ctx.font = "700 13px system-ui";
  ctx.fillText(`${metricLabels[metric]} by Age — vs. Typical NBA Career Arc`, pad.left, 22);

  // Legend
  const lx2 = w - pad.right + 8;
  ctx.fillStyle = player.colors[0]; ctx.fillRect(lx2, pad.top + 4, 16, 3);
  ctx.fillStyle = labelColor; ctx.font = "10px system-ui";
  ctx.fillText(player.name.split(" ").pop(), lx2 + 20, pad.top + 8);
  ctx.fillStyle = dark ? "rgba(170,185,210,0.3)" : "rgba(130,145,175,0.35)";
  ctx.fillRect(lx2, pad.top + 20, 16, 10);
  ctx.fillStyle = labelColor;
  ctx.fillText("NBA avg", lx2 + 20, pad.top + 27);
}

const PROJ_BREAKDOWN_ICONS = {
  pts: '<path d="M12 2l3 7h7l-5.5 4.5L18 21l-6-4.5L6 21l1.5-7.5L2 9h7z"/>',
  reb: '<rect x="4" y="4" width="16" height="16" rx="3"/><path d="M4 14h16"/>',
  ast: '<circle cx="6" cy="12" r="2.5"/><circle cx="18" cy="6" r="2.5"/><circle cx="18" cy="18" r="2.5"/><path d="M8.2 11l7.6-4M8.2 13l7.6 4"/>',
  three: '<circle cx="12" cy="12" r="9"/><path d="M9 9l6 6M15 9l-6 6"/>',
  stl: '<path d="M12 2l8 4v6c0 5-3.5 8-8 10-4.5-2-8-5-8-10V6z"/>',
  blk: '<path d="M4 4h16v16H4z"/><path d="M4 4l16 16"/>',
  tov: '<path d="M3 12a9 9 0 1 0 9-9"/><path d="M3 4v8h8"/>',
  ts: '<path d="M3 17l5-5 4 4 8-9"/>',
  usg: '<path d="M12 2v20M2 12h20"/>',
  min: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/>',
  gp: '<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M3 10h18M8 2v4M16 2v4"/>',
  net: '<path d="M3 12h4l3 8 4-16 3 8h4"/>',
};
const PROJ_BREAKDOWN_STATS = [
  ["pts", "PTS", false], ["reb", "REB", false], ["ast", "AST", false], ["three", "3PM", false],
  ["stl", "STL", false], ["blk", "BLK", false], ["tov", "TOV", true], ["ts", "TS%", false],
  ["usg", "USG%", false], ["min", "MIN", false], ["gp", "GP", false], ["net", "BPM", false],
];

function renderProjection(player, projections) {
  const final = projections[projections.length - 1];
  const current = latestSeason(player);
  $("#projFinalSeasonLabel").textContent = `vs. ${current.season} season`;
  $("#projectionCards").innerHTML = PROJ_BREAKDOWN_STATS.map(([key, label, invert]) => {
    const curV = current[key], projV = final[key];
    const delta = (Number.isFinite(curV) && Number.isFinite(projV)) ? projV - curV : null;
    let deltaClass = "flat", arrow = "→";
    if (delta !== null && Math.abs(delta) > 0.05) {
      const up = delta > 0;
      const good = invert ? !up : up;
      deltaClass = good ? "up" : "down";
      arrow = up ? "▲" : "▼";
    }
    const deltaText = delta === null ? "—" : `${arrow} ${Math.abs(delta).toFixed(1)}`;
    return `<div class="proj-breakdown-card">
      <div class="proj-breakdown-top">
        <svg class="proj-breakdown-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${PROJ_BREAKDOWN_ICONS[key] || ""}</svg>
        <span class="proj-breakdown-label">${label}</span>
      </div>
      <div class="proj-breakdown-val">${fmt(projV, key)}${(key==="ts"||key==="usg")?"%":""}</div>
      <div class="proj-breakdown-delta ${deltaClass}">${deltaText} <span class="proj-breakdown-cur">from ${fmt(curV, key)}${(key==="ts"||key==="usg")?"%":""}</span></div>
    </div>`;
  }).join("");
}

function renderProjectionRows(player, projections) {
  const tbody = $("#projectionRows");
  if (!tbody) return;
  tbody.innerHTML = projections.map((s) => `
    <tr class="proj-row-future">
      <td>${s.season} <span class="proj-future-badge">proj.</span></td>
      <td>${s.age}</td><td>${fmt(s.gp,"gp")}</td><td>${fmt(s.min,"min")}</td>
      <td>${fmt(s.pts,"pts")}</td><td>${fmt(s.reb,"reb")}</td><td>${fmt(s.ast,"ast")}</td>
      <td>${fmt(s.three,"three")}</td><td>${fmt(s.stl,"stl")}</td><td>${fmt(s.blk,"blk")}</td>
      <td>${fmt(s.tov,"tov")}</td><td>${fmt(s.ts,"ts")}</td><td>${fmt(s.usg,"usg")}</td>
    </tr>`).join("");
}

function renderProjVerdict(player, projections) {
  const recent = player.seasons.slice(-5);
  if (recent.length < 2) {
    $("#projVerdictLabel").textContent = "Not enough seasons to model a trajectory";
    $("#projVerdictSub").textContent = "";
    $("#projConfNum").textContent = "—";
    return;
  }
  const ptsSlope = slope(recent, "pts");
  const efficSlope = slope(recent, "ts") + slope(recent, "net") * 0.5;
  const composite = ptsSlope * 0.6 + efficSlope * 4;
  const banner = $("#projVerdict");
  banner.classList.remove("ascending", "declining", "stable");
  let icon, label, tone;
  if (composite > 0.6) { icon = "↗"; label = "Ascending Trajectory"; tone = "ascending"; }
  else if (composite < -0.6) { icon = "↘"; label = "Declining Trajectory"; tone = "declining"; }
  else { icon = "→"; label = "Stable Trajectory"; tone = "stable"; }
  banner.classList.add(tone);
  $("#projVerdictIcon").textContent = icon;
  $("#projVerdictLabel").textContent = label;
  const final = projections[projections.length - 1];
  const cur = latestSeason(player);
  const ptsDelta = final.pts - cur.pts;
  $("#projVerdictSub").textContent = `Modeled ${sign(ptsDelta.toFixed(1))} PTS over ${projections.length} season${projections.length > 1 ? "s" : ""}, based on the last ${recent.length} seasons' age curve and role.`;
  // Confidence reflects sample size + how consistently pts moved in the
  // trend's direction season-over-season -- not the raw size of the trend
  // (a steep but consistent rise should score higher, not lower).
  const diffs = [];
  for (let i = 1; i < recent.length; i++) diffs.push(recent[i].pts - recent[i - 1].pts);
  const sameSignCount = diffs.filter((d) => d === 0 || Math.sign(d) === Math.sign(ptsSlope)).length;
  const consistency = diffs.length ? sameSignCount / diffs.length : 0.5;
  const base = 55 + recent.length * 5;
  const confidence = Math.max(35, Math.min(92, base * (0.6 + 0.4 * consistency)));
  $("#projConfNum").textContent = `${Math.round(confidence)}%`;
}

function sign(v) { return v > 0 ? `+${v}` : `${v}`; }

function renderProjectionsPane(player, projections) {
  renderProjVerdict(player, projections);
  renderProjection(player, projections);
  renderProjectionRows(player, projections);
  drawChart(player, projections, true, "projTrendChart", playerState.projMetric);
}

function renderSeasonTable(player) {
  const projections = projectPlayer(player);
  const rows = player.seasons.map((s) => ({ ...s, projected: false }))
    .concat(projections.map((s) => ({ ...s, projected: true })));
  $("#seasonRows").innerHTML = rows.map((s) => {
    const teamLabel = s.projected
      ? " (proj.)"
      : s.teamsThisSeason && s.teamsThisSeason.length > 1
        ? ` (${s.teamsThisSeason.join(", ")})`
        : s.team ? ` (${s.team})` : "";
    return `
    <tr${s.projected ? ' style="color:var(--red);opacity:0.85"' : ""}>
      <td>${s.season}${teamLabel}</td>
      <td>${s.age}</td><td>${fmt(s.gp,"gp")}</td><td>${fmt(s.min,"min")}</td>
      <td>${fmt(s.pts,"pts")}</td><td>${fmt(s.reb,"reb")}</td><td>${fmt(s.ast,"ast")}</td>
      <td>${fmt(s.three,"three")}</td><td>${fmt(s.stl,"stl")}</td><td>${fmt(s.blk,"blk")}</td>
      <td>${fmt(s.tov,"tov")}</td><td>${fmt(s.ts,"ts")}</td><td>${fmt(s.usg,"usg")}</td>
    </tr>`;
  }).join("");
}

function updateRangeFill(input) {
  if (!input) return;
  const pct = ((input.value - input.min) / (input.max - input.min)) * 100;
  input.style.setProperty("--range-pct", `${pct}%`);
}

function syncControls() {
  $("#seasonValue").textContent = playerState.seasonsAhead;
  const sign = (v) => v > 0 ? `+${v}` : v;
  $("#minutesValue").textContent = sign(playerState.minutesChange);
  $("#usageValue").textContent = sign(playerState.usageChange);
  $("#durabilityValue").textContent = sign(playerState.durabilityChange);
  ["seasonRange","minutesRange","usageRange","durabilityRange"].forEach((id) => updateRangeFill($(`#${id}`)));
}

// ── Player grid + profile event wiring ─────────────────────────────────────

$("#playerSearch").addEventListener("input", renderPlayerGrid);

$("#metricSelect").addEventListener("change", (e) => {
  playerState.metric = e.target.value;
  if (playerState.player) {
    drawChart(playerState.player, projectPlayer(playerState.player), true);
    drawDevCurve(playerState.player, true);
  }
});

$("#projMetricSelect").addEventListener("change", (e) => {
  playerState.projMetric = e.target.value;
  if (playerState.player) {
    drawChart(playerState.player, projectPlayer(playerState.player), true, "projTrendChart", playerState.projMetric);
  }
});

[["seasonRange","seasonsAhead"],["minutesRange","minutesChange"],["usageRange","usageChange"],["durabilityRange","durabilityChange"]].forEach(([id, key]) => {
  $(`#${id}`).addEventListener("input", (e) => {
    playerState[key] = Number(e.target.value);
    syncControls();
    if (playerState.player) {
      const proj = projectPlayer(playerState.player);
      renderProjectionsPane(playerState.player, proj);
      renderSeasonTable(playerState.player);
      drawChart(playerState.player, proj, true);
      drawDevCurve(playerState.player, true);
    }
  });
});

$("#resetControls").addEventListener("click", () => {
  playerState.seasonsAhead = 3; playerState.minutesChange = 0;
  playerState.usageChange = 0; playerState.durabilityChange = 0;
  $("#seasonRange").value = 3; $("#minutesRange").value = 0;
  $("#usageRange").value = 0; $("#durabilityRange").value = 0;
  syncControls();
  if (playerState.player) {
    const proj = projectPlayer(playerState.player);
    renderProjectionsPane(playerState.player, proj);
    renderSeasonTable(playerState.player);
    drawChart(playerState.player, proj, true);
    drawDevCurve(playerState.player, true);
  }
});

$("#backToPlayers").addEventListener("click", () => navigate("players"));

$("#profileWatchlistBtn").addEventListener("click", () => {
  if (!playerState.player) return;
  const ids = getWatchlist();
  const id = playerState.player.id;
  const updated = ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id];
  saveWatchlist(updated);
  const saved = updated.includes(id);
  const btn = $("#profileWatchlistBtn");
  btn.textContent = saved ? "✓ Saved" : "🔖 Save";
  btn.classList.toggle("saved", saved);
});

// Profile tab switching
document.querySelectorAll(".profile-tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".profile-tab").forEach((t) => t.classList.remove("active"));
    document.querySelectorAll(".profile-pane").forEach((p) => p.classList.add("hidden"));
    tab.classList.add("active");
    const pane = $(`#pane-${tab.dataset.pane}`);
    if (pane) pane.classList.remove("hidden");
    if (tab.dataset.pane === "overview" && playerState.player) {
      drawRadar(playerState.player);
      drawChart(playerState.player, projectPlayer(playerState.player), true);
      drawDevCurve(playerState.player, true);
    }
    if (tab.dataset.pane === "projections" && playerState.player) {
      drawChart(playerState.player, projectPlayer(playerState.player), true, "projTrendChart", playerState.projMetric);
    }
    // Lazy-load archetype panel on first visit to the tab
    if (tab.dataset.pane === "archetype" && playerState.player) {
      const panel = $("#archetypePanel");
      if (panel && !panel.dataset.loaded) {
        loadArchetypePanel(playerState.player);
        panel.dataset.loaded = "1";
      }
    }
  });
});

// ── Prospects ────────────────────────────────────────────────────────────────

let prospectsLoaded = false;

let allProspects = [];

const STATUS_COLORS = {
  "Freshman":    "rgba(91,141,238,0.18)",
  "Sophomore":   "rgba(91,141,238,0.12)",
  "Junior":      "rgba(62,207,142,0.14)",
  "Senior":      "rgba(62,207,142,0.2)",
  "International": "rgba(215,157,40,0.18)",
  "G League":    "rgba(189,51,64,0.16)",
};

function renderProspectsTable(filter = "") {
  const q = filter.toLowerCase().trim();
  const rows = q
    ? allProspects.filter((p) => [p.name, p.school, p.pos, p.country, p.status].join(" ").toLowerCase().includes(q))
    : allProspects;
  $("#prospectsTable").innerHTML = rows.map((p) => {
    const color = STATUS_COLORS[p.status] || "rgba(255,255,255,0.05)";
    const pickDisplay = p.rank <= 100
      ? `<strong style="color:var(--blue)">#${p.rank}</strong>`
      : `<span style="color:var(--muted)">NR</span>`;
    const rowClass = p.rank <= 30 ? ' class="top-prospect"' : "";
    return `<tr${rowClass}>
      <td class="rank">${pickDisplay}</td>
      <td><strong>${p.name}</strong></td>
      <td>${p.pos}</td>
      <td>${p.age}</td>
      <td>${p.school}</td>
      <td>${p.height}</td>
      <td>${p.weight}</td>
      <td><span class="status-badge" style="background:${color}">${p.status}</span></td>
      <td>${p.country}</td>
    </tr>`;
  }).join("");
}

async function loadProspects() {
  prospectsLoaded = true;
  try {
    allProspects = await fetch("/api/prospects").then((r) => r.json());
    renderProspectsTable();
  } catch (e) {
    console.warn("Prospects load failed", e);
  }
}

$("#prospectSearch").addEventListener("input", (e) => renderProspectsTable(e.target.value));

// ── Draft Class ──────────────────────────────────────────────────────────────

let draftLoaded = false;

async function loadDraft(season) {
  draftLoaded = true;
  const url = season ? `/api/draft?season=${season}` : "/api/draft";
  try {
    const data = await fetch(url).then((r) => r.json());
    const select = $("#draftSeasonSelect");
    if (!select.options.length) {
      data.seasons.forEach((s) => {
        const opt = document.createElement("option");
        opt.value = s; opt.textContent = s;
        if (s === data.season) opt.selected = true;
        select.appendChild(opt);
      });
    }
    $("#draftTable").innerHTML = data.picks.map((p) => `
      <tr>
        <td><strong>#${p.overall_pick}</strong></td>
        <td>${p.round}</td>
        <td>${p.player}</td>
        <td>${p.team || "—"}</td>
        <td>${p.college || "—"}</td>
        <td>${p.career_pts || "—"}</td>
        <td>${p.career_reb || "—"}</td>
        <td>${p.career_ast || "—"}</td>
        <td>${p.seasons_played || 0}</td>
      </tr>`).join("");
  } catch (e) {
    console.warn("Draft load failed", e);
  }
}

$("#draftSeasonSelect").addEventListener("change", (e) => loadDraft(e.target.value));

// ── Comparisons ──────────────────────────────────────────────────────────────

// ── Comparisons (4-player, 6-tab, engine-wired) ───────────────────────────────

const CMP_COLORS = ["#5b8af0", "#f97316", "#3ecf8e", "#f5c842"];
const CMP_LABELS = ["A", "B", "C", "D"];

let comparePlayers = [null, null, null, null];
let cmpActiveTab = "overview";
let cmpMode = "current";
let archetypeSimCache = {};

function cmpColor(i) { return CMP_COLORS[i % 4]; }

function cmpEffScore(player) {
  const last = latestSeason(player);
  if (!last) return 0;
  const per = parseFloat(last.per) || 15;
  const bpm = parseFloat(last.net) || 0;
  const ts  = (parseFloat(last.ts) || 50) / 100;
  const pts = parseFloat(last.pts) || 0;
  const ast = parseFloat(last.ast) || 0;
  const reb = parseFloat(last.reb) || 0;
  const vorp= parseFloat(last.vorp) || 0;
  return Math.round(
    Math.min(100,Math.max(0,((per-5)/30)*100))*0.20 +
    Math.min(100,Math.max(0,((bpm+5)/20)*100))*0.15 +
    Math.min(100,Math.max(0,((ts-0.40)/0.35)*100))*0.10 +
    Math.min(100,Math.max(0,((vorp+1)/9)*100))*0.15 +
    Math.min(100,(pts/35)*100)*0.20 +
    Math.min(100,(ast/12)*100)*0.10 +
    Math.min(100,(reb/15)*100)*0.10
  );
}

function cmpPosGroup(pos) {
  if (!pos) return null;
  const p = pos.toUpperCase();
  if (/\bC\b/.test(p) || p.includes("PF") || p === "F-C" || p === "C-F") return "Big";
  if (p.includes("PG") || p.includes("SG") || p === "G" || p === "G-F") return "Guard";
  return "Wing";
}

function cmpPosPenalty(posA, posB) {
  const gA = cmpPosGroup(posA), gB = cmpPosGroup(posB);
  if (!gA || !gB || gA === gB) return 1.0;
  if ((gA === "Guard" && gB === "Big") || (gA === "Big" && gB === "Guard")) return 0.70;
  return 0.88;
}

function cmpLocalSimilarity(pA, pB) {
  if (!pA || !pB) return 0;
  const la = latestSeason(pA), lb = latestSeason(pB);
  if (!la || !lb) return 0;
  // Weighted feature set — scoring efficiency and playmaking matter most
  const features = [
    {k:"pts",  w:1.5, max:40},
    {k:"ast",  w:2.0, max:14},
    {k:"reb",  w:1.0, max:18},
    {k:"ts",   w:1.5, max:70},
    {k:"usg",  w:1.5, max:42},
    {k:"net",  w:2.0, max:15, offset:5},
    {k:"stl",  w:0.8, max:3.5},
    {k:"blk",  w:0.8, max:4},
    {k:"three",w:1.2, max:6},
  ];
  let dot=0, magA=0, magB=0;
  features.forEach(({k, w, max, offset=0}) => {
    const va=(parseFloat(la[k])||0)+offset, vb=(parseFloat(lb[k])||0)+offset;
    const na=(va/max)*w, nb=(vb/max)*w;
    dot+=na*nb; magA+=na*na; magB+=nb*nb;
  });
  if (!magA || !magB) return 0;
  const cosine = dot / (Math.sqrt(magA) * Math.sqrt(magB));
  const penalty = cmpPosPenalty(pA.position, pB.position);
  return Math.round(cosine * penalty * 100);
}

// Backs "Similar Players" panels with the real archetype/comp engine
// (archetype_engine.py's same_stage_comps -- playstyle/efficiency/advanced/
// physical weighted, not raw stat cosine) instead of a local fallback.
async function fetchArchetypeSimilar(player) {
  if (archetypeSimCache[player.playerId]) return archetypeSimCache[player.playerId];
  const last = latestSeason(player);
  if (!last || !last.playerId || !last.season) return null;
  try {
    const url = `/api/archetype?player_id=${encodeURIComponent(last.playerId)}&season=${encodeURIComponent(last.season)}`;
    const report = await fetch(url).then((r) => (r.ok ? r.json() : Promise.reject(r.status)));
    const similar = (report.same_stage_comps || []).map((c) => ({
      player: c.player, player_name: c.player, season: c.season,
      similarity: c.similarity, dominant_engine: c.dominant_engine,
      explanation: c.explanation, breakdown: c.breakdown,
    }));
    archetypeSimCache[player.playerId] = similar;
    return similar;
  } catch { return null; }
}

function cmpAvatar(player, size = 52) {
  const bg = (player.colors && player.colors[0]) || "#1e3a5f";
  const ini = player.name.split(" ").map(w => w[0]).join("").slice(0,2).toUpperCase();
  return `<div class="cmp-avatar" style="width:${size}px;height:${size}px;background:${bg}">
    <img src="/api/player-photo/${player.playerId}" onerror="this.style.display='none'" style="position:absolute;inset:0;width:100%;height:100%;object-fit:cover;object-position:top;border-radius:inherit"/>
    <span style="position:relative;z-index:1;font-size:${size*0.3}px;font-weight:900;color:#fff">${ini}</span>
  </div>`;
}

function renderPlayerBar() {
  const bar = $("#cmpPlayerBar");
  if (!bar) return;
  const slots = comparePlayers;
  bar.innerHTML = slots.map((player, i) => {
    const color = cmpColor(i);
    if (!player) {
      const label = cmpMode === "prospect" ? `Add Player/Prospect ${CMP_LABELS[i]}` : `Add Player ${CMP_LABELS[i]}`;
      return `<div class="cmp-search-slot" id="cmpSlot${i}" data-slot="${i}">
        <div class="cmp-slot-inner">
          <span class="cmp-slot-icon">+</span>
          <span class="cmp-slot-label">${label}</span>
          <input id="cmpSearch${i}" type="search" placeholder="Search by name..." class="cmp-search-input" autocomplete="off" spellcheck="false"/>
        </div>
        <div class="compare-dropdown" id="cmpDrop${i}"></div>
      </div>`;
    }
    if (player.isProspect) {
      return `<div class="cmp-search-slot cmp-slot-filled" id="cmpSlot${i}" data-slot="${i}">
        <div class="cmp-slot-color-bar" style="background:${color}"></div>
        <div class="cmp-selected-inner">
          ${cmpAvatar(player, 44)}
          <div class="cmp-selected-info">
            <div class="cmp-selected-name">${player.name}</div>
            <div class="cmp-selected-meta">${player.position||"—"} · ${player.team||"—"} · ${player.status||"Prospect"}</div>
            <div class="cmp-selected-meta">${player.height||"—"} · ${player.weight||"—"} · Mock Rank #${player.rank||"—"}</div>
          </div>
          <button class="cmp-remove-btn" data-slot="${i}">✕</button>
        </div>
      </div>`;
    }
    const last = latestSeason(player);
    const score = cmpEffScore(player);
    // Prefer the real comp engine (playstyle/efficiency-weighted) over the raw
    // stat-cosine fallback whenever we have it; fetch it in the background and
    // re-render once available rather than blocking this synchronous render.
    let simScore = i === 0 ? 100 : cmpLocalSimilarity(comparePlayers[0], player);
    if (i !== 0 && comparePlayers[0]) {
      const cached = archetypeSimCache[comparePlayers[0].playerId];
      if (cached) {
        const match = cached.find((s) => s.player === player.name);
        if (match) simScore = match.similarity;
      } else {
        fetchArchetypeSimilar(comparePlayers[0]).then(() => renderPlayerBar());
      }
    }
    return `<div class="cmp-search-slot cmp-slot-filled" id="cmpSlot${i}" data-slot="${i}">
      <div class="cmp-slot-color-bar" style="background:${color}"></div>
      <div class="cmp-selected-inner">
        ${cmpAvatar(player, 44)}
        <div class="cmp-selected-info">
          <div class="cmp-selected-name">${player.name}</div>
          <div class="cmp-selected-meta">${player.position} · ${last?.team||"—"} · ${last?.season||"—"}</div>
          <div class="cmp-selected-meta">${last?.pts!=null?`${(+last.pts).toFixed(1)} PTS`:"—"} · ${last?.reb!=null?`${(+last.reb).toFixed(1)} REB`:"—"} · ${last?.ast!=null?`${(+last.ast).toFixed(1)} AST`:"—"}</div>
        </div>
        <div class="cmp-badge-col">
          <div class="cmp-sim-badge">
            <div class="cmp-badge-num" style="color:${color}">${simScore}</div>
            <div class="cmp-badge-lbl">Similarity</div>
          </div>
          <div class="cmp-draft-badge">
            <div class="cmp-badge-num" style="color:var(--gold)">${score}</div>
            <div class="cmp-badge-lbl">Draftability</div>
          </div>
        </div>
        <button class="cmp-remove-btn" data-slot="${i}">✕</button>
      </div>
    </div>`;
  }).join("");
  slots.forEach((p, i) => { if (!p) setupCmpSearch(i); });
}

function setupCmpSearch(i) {
  const input = $(`#cmpSearch${i}`);
  const dropdown = $(`#cmpDrop${i}`);
  if (!input || !dropdown) return;
  input.addEventListener("input", () => {
    const q = input.value.toLowerCase().trim();
    const pool = cmpMode === "prospect" ? [...players, ...allProspects.map(wrapProspect)] : players;
    if (!q || !pool.length) { dropdown.classList.remove("open"); return; }
    const matches = pool.filter(p => p.name.toLowerCase().includes(q)).slice(0, 8);
    dropdown.innerHTML = matches.map(p => `<div class="compare-option" data-name="${escapeHtml(p.name)}">${escapeHtml(p.name)} <span style="color:var(--muted);font-size:0.75em">· ${escapeHtml(p.isProspect ? (p.team||"Prospect") : (latestSeason(p)?.team||""))}</span></div>`).join("");
    dropdown.classList.toggle("open", matches.length > 0);
    dropdown.querySelectorAll(".compare-option").forEach(opt => {
      opt.addEventListener("click", () => {
        const found = pool.find(p => p.name === opt.dataset.name);
        if (found) { comparePlayers[i] = found; renderPlayerBar(); renderComparison(); }
        dropdown.classList.remove("open");
      });
    });
  });
}

// Single delegated listener for all comparisons interactions
document.addEventListener("click", e => {
  // Close dropdowns
  if (!e.target.closest(".compare-dropdown") && !e.target.closest(".cmp-search-input")) {
    document.querySelectorAll(".compare-dropdown.open").forEach(d => d.classList.remove("open"));
  }
  // Remove player slot
  const removeBtn = e.target.closest(".cmp-remove-btn");
  if (removeBtn) {
    const i = parseInt(removeBtn.dataset.slot);
    if (!isNaN(i)) { comparePlayers[i] = null; renderPlayerBar(); renderComparison(); }
  }
  // Tab switch
  const tab = e.target.closest(".cmp-tab");
  if (tab && tab.closest("#cmpTabs")) {
    document.querySelectorAll("#cmpTabs .cmp-tab").forEach(t => t.classList.remove("active"));
    tab.classList.add("active");
    cmpActiveTab = tab.dataset.tab;
    renderComparison();
  }
  // Mode switch
  const modeBtn = e.target.closest(".cmp-mode-btn");
  if (modeBtn) {
    document.querySelectorAll(".cmp-mode-btn").forEach(b => b.classList.remove("active"));
    modeBtn.classList.add("active");
    cmpMode = modeBtn.dataset.mode;
    if (cmpMode === "prospect" && !prospectsLoaded) {
      loadProspects().then(() => { renderPlayerBar(); renderComparison(); });
    }
    renderPlayerBar();
    renderComparison();
  }
  // Career Outcome page: remove the prospect slot
  const careerRemoveBtn = e.target.closest(".career-remove-btn");
  if (careerRemoveBtn) {
    careerOutcomeProspect = null;
    renderCareerOutcomeBar();
    renderCareerOutcomePage();
  }
  // Draft Projection page: remove the prospect slot
  const draftProjRemoveBtn = e.target.closest(".draft-proj-remove-btn");
  if (draftProjRemoveBtn) {
    draftProjectionProspect = null;
    renderDraftProjectionBar();
    renderDraftProjectionPage();
  }
});

function wrapProspect(p) {
  return {
    name: p.name,
    playerId: null,
    team: p.school,
    position: p.pos,
    height: p.height,
    weight: p.weight,
    age: parseFloat(p.age) || null,
    country: p.country,
    college: p.school,
    colors: [stableColor(p.name), stableColor(p.name, 3)],
    seasons: [],
    isProspect: true,
    rank: p.rank,
    status: p.status,
  };
}

function activePlayers() {
  return comparePlayers.map((p,i) => p ? {player:p, color:cmpColor(i), idx:i} : null).filter(Boolean);
}

// ── Career Outcome (standalone page, separate from Comparisons) ────────────
let careerOutcomeProspect = null;

function renderCareerOutcomeBar() {
  const bar = $("#careerOutcomeBar");
  if (!bar) return;
  const color = cmpColor(0);
  if (!careerOutcomeProspect) {
    bar.innerHTML = `<div class="cmp-search-slot co-search-slot" id="careerSlot0">
      <div class="cmp-slot-inner">
        <span class="co-slot-icon-badge">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>
        </span>
        <span class="cmp-slot-label">Search for a 2026 draft prospect to project their career outcome</span>
        <input id="careerSearch0" type="search" placeholder="Search by name..." class="cmp-search-input co-search-input" autocomplete="off" spellcheck="false"/>
      </div>
      <div class="compare-dropdown" id="careerDrop0"></div>
    </div>`;
    setupCareerOutcomeSearch();
    return;
  }
  const p = careerOutcomeProspect;
  bar.innerHTML = `<div class="cmp-search-slot cmp-slot-filled co-search-slot co-search-slot-filled" id="careerSlot0">
    <div class="cmp-slot-color-bar" style="background:${color}"></div>
    <div class="cmp-selected-inner">
      ${cmpAvatar(p, 48)}
      <div class="cmp-selected-info">
        <div class="cmp-selected-name">${p.name}</div>
        <div class="cmp-selected-meta">${p.position||"—"} · ${p.team||"—"} · ${p.status||"Prospect"}</div>
        <div class="cmp-selected-meta">${p.height||"—"} · ${p.weight||"—"} · Mock Rank #${p.rank||"—"}</div>
      </div>
      <button class="cmp-remove-btn career-remove-btn">✕</button>
    </div>
  </div>`;
}

function setupCareerOutcomeSearch() {
  const input = $("#careerSearch0");
  const dropdown = $("#careerDrop0");
  if (!input || !dropdown) return;
  input.addEventListener("input", () => {
    const q = input.value.toLowerCase().trim();
    const pool = allProspects.map(wrapProspect);
    if (!q || !pool.length) { dropdown.classList.remove("open"); return; }
    const matches = pool.filter(p => p.name.toLowerCase().includes(q)).slice(0, 8);
    dropdown.innerHTML = matches.map(p => `<div class="compare-option" data-name="${escapeHtml(p.name)}">${escapeHtml(p.name)} <span style="color:var(--muted);font-size:0.75em">· ${escapeHtml(p.team||"Prospect")}</span></div>`).join("");
    dropdown.classList.toggle("open", matches.length > 0);
    dropdown.querySelectorAll(".compare-option").forEach(opt => {
      opt.addEventListener("click", () => {
        const found = pool.find(p => p.name === opt.dataset.name);
        if (found) {
          careerOutcomeProspect = found;
          renderCareerOutcomeBar();
          renderCareerOutcomePage();
        }
        dropdown.classList.remove("open");
      });
    });
  });
}

function renderCareerOutcomePage() {
  const container = $("#careerOutcomeContent");
  if (!container) return;
  if (!careerOutcomeProspect) {
    container.innerHTML = `<div class="compare-placeholder"><div class="compare-placeholder-icon">⚖</div><div>Search for a draft prospect above to project their career outcome.</div></div>`;
    return;
  }
  renderCareerOutcomeView(container, careerOutcomeProspect);
}

const fmtV = (v, key) => {
  if (v==null||v===undefined||v==="") return "—";
  if (key==="ts"||key==="usg") return (+v).toFixed(1)+"%";
  return (+v).toFixed(1);
};

function build4Table(rows, active) {
  return `<table class="cmp-table4">
    <thead><tr><th>Stat</th>${active.map(a=>`<th style="color:${a.color}">${a.player.name.split(" ").slice(-1)[0]}</th>`).join("")}</tr></thead>
    <tbody>${rows.map(([label,key,lb])=>{
      const vals=active.map(a=>{const s=latestSeason(a.player);return s?parseFloat(s[key]):NaN;});
      const valid=vals.filter(v=>!isNaN(v));
      const best=valid.length?(lb?Math.min(...valid):Math.max(...valid)):null;
      return `<tr><td>${label}</td>${vals.map((v,i)=>{
        const isBest=best!==null&&!isNaN(v)&&v===best;
        return `<td class="${isBest?"t4-best":""}" style="${isBest?`color:${active[i].color}`:""}">${fmtV(isNaN(v)?null:v,key)}</td>`;
      }).join("")}</tr>`;
    }).join("")}</tbody></table>`;
}

function renderComparison() {
  const container = $("#comparisonContent");
  if (!container) return;
  const active = activePlayers();
  if (active.length < 1) {
    container.innerHTML = `<div class="compare-placeholder"><div class="compare-placeholder-icon">⚖</div><div>Search for players above to compare their stats and profiles.</div></div>`;
    return;
  }
  if (active.length < 2) {
    container.innerHTML = `<div class="compare-placeholder"><div class="compare-placeholder-icon">🔍</div><div>Add at least one more player to start comparing.</div></div>`;
    return;
  }
  const tab = cmpActiveTab;
  if      (tab==="overview")    renderOverviewTab(container,active);
  else if (tab==="advanced")    renderAdvancedTab(container,active);
  else if (tab==="scouting")    renderScoutingTab(container,active);
  else if (tab==="trajectory")  renderTrajectoryTab(container,active);
  else if (tab==="statprofile") renderStatProfileTab(container,active);
  else if (tab==="similar")     renderSimilarTab(container,active);
}

// ── Career Outcome (prospect projection) ───────────────────────────────────────
async function renderCareerOutcomeView(container, prospect) {
  container.innerHTML = `<div class="compare-placeholder"><div class="compare-placeholder-icon">⏳</div><div>Projecting career outcome for ${escapeHtml(prospect.name)}...</div></div>`;
  let data;
  try {
    data = await fetch(`/api/prospect-outcome?name=${encodeURIComponent(prospect.name)}`).then(r => r.json());
    if (data.error) throw new Error(data.error);
  } catch (e) {
    container.innerHTML = `<div class="compare-placeholder"><div class="compare-placeholder-icon">⚠</div><div>Couldn't load a career outcome projection for ${escapeHtml(prospect.name)}.</div></div>`;
    return;
  }
  const { comps, summary } = data;
  if (!comps.length) {
    container.innerHTML = `<div class="compare-placeholder"><div>No comparable historical draft picks found for ${escapeHtml(prospect.name)}.</div></div>`;
    return;
  }
  const co_icon = (key) => `<svg class="co-stat-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${PROJ_BREAKDOWN_ICONS[key] || ""}</svg>`;
  const co_avatar = (c) => {
    const ini = c.player.split(" ").map(w => w[0]).join("").slice(0, 2).toUpperCase();
    return `<div class="co-comp-avatar" style="background:${stableColor(c.player)}">
      <img src="/api/player-photo/${c.player_id}" onerror="this.style.display='none'" />
      <span>${ini}</span>
    </div>`;
  };

  const co_section_icon = (svg) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${svg}</svg>`;

  // Min/max spread across the comp set, computed client-side from data already
  // fetched -- gives each headline number a sense of range, not just an average.
  const co_range = (key) => {
    const vals = comps.map(c => parseFloat(c[key])).filter(v => Number.isFinite(v));
    if (!vals.length) return null;
    return { min: Math.min(...vals), max: Math.max(...vals) };
  };
  const co_range_track = (avg, range) => {
    if (!range || range.max === range.min) return "";
    const pct = Math.max(0, Math.min(100, ((avg - range.min) / (range.max - range.min)) * 100));
    return `<div class="co-stat-range"><div class="co-stat-range-track"><div class="co-stat-range-dot" style="left:${pct}%"></div></div><div class="co-stat-range-lbl">${fmtV(range.min)}–${fmtV(range.max)} range</div></div>`;
  };
  const co_stat_tile = (key, iconKey, label, avg) => {
    const range = co_range(key);
    return `<div class="co-stat-tile">${co_icon(iconKey)}<div class="co-stat-value">${avg ?? "—"}</div><div class="co-stat-label">${label}</div>${range ? co_range_track(avg ?? range.min, range) : ""}</div>`;
  };

  // In-cell bars scaled to the max value in that column across the comp set --
  // a quick visual scan of who's the outlier, not just a wall of numbers.
  const co_col_max = (key) => Math.max(1, ...comps.map(c => parseFloat(c[key]) || 0));
  const co_bar_cell = (val, key, colorVar) => {
    const max = co_col_max(key);
    const v = parseFloat(val);
    const pct = Number.isFinite(v) ? Math.max(4, (v / max) * 100) : 0;
    return `<td class="co-bar-cell">
      <div class="co-bar-track"><div class="co-bar-fill" style="width:${pct}%;background:${colorVar}"></div></div>
      <span>${val ?? "—"}</span>
    </td>`;
  };

  container.innerHTML = `
    <div class="cmp-pcard co-card" style="max-width:960px;margin:0 auto" id="careerOutcomeCard">
      <div class="co-hero">
        <div class="co-hero-top">
          <div class="co-hero-avatar-wrap">${cmpAvatar(prospect, 76)}</div>
          <div>
            <p class="co-hero-eyebrow">Projected Career Outcome</p>
            <h3 class="co-hero-name">${escapeHtml(prospect.name)}</h3>
            <p class="co-sub">
              Based on ${summary.comp_count} historical draft picks near projected slot #${prospect.rank}${prospect.position ? ` at a similar position (${prospect.position})` : ""}.
            </p>
          </div>
        </div>

        <div class="co-hero-stats">
          ${co_stat_tile("career_pts", "pts", "Avg Career PTS", summary.avg_career_pts)}
          ${co_stat_tile("career_reb", "reb", "Avg Career REB", summary.avg_career_reb)}
          ${co_stat_tile("career_ast", "ast", "Avg Career AST", summary.avg_career_ast)}
          ${co_stat_tile("seasons_played", "gp", "Avg Seasons Played", summary.avg_seasons_played)}
        </div>
      </div>

      <div class="co-body">
        <div class="co-section-head">
          ${co_section_icon('<circle cx="6" cy="12" r="2.5"/><circle cx="18" cy="6" r="2.5"/><circle cx="18" cy="18" r="2.5"/><path d="M8.2 11l7.6-4M8.2 13l7.6 4"/>')}
          <span>Closest Historical Draft Comps</span>
        </div>
        <table class="cmp-table4 co-comp-table">
          <thead><tr><th>Player</th><th>Pick</th><th>Yr</th><th>PTS</th><th>REB</th><th>AST</th><th>Seasons</th></tr></thead>
          <tbody>${comps.map((c, i) => `<tr>
            <td class="co-comp-name-cell"><span class="co-comp-rank">${i + 1}</span>${co_avatar(c)}<span>${c.player}</span>${c.position_match ? `<span class="co-match-badge" title="Same position bucket"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M4 12l5 5L20 6"/></svg>Pos match</span>` : ""}</td>
            <td>#${c.overall_pick}</td>
            <td>${c.draft_season}</td>
            ${co_bar_cell(c.career_pts, "career_pts", "var(--accent-1)")}
            ${co_bar_cell(c.career_reb, "career_reb", "var(--blue)")}
            ${co_bar_cell(c.career_ast, "career_ast", "var(--accent-3)")}
            <td>${c.seasons_played ?? "—"}</td>
          </tr>`).join("")}</tbody>
        </table>
        <p class="co-footnote">
          Comps are matched by draft slot and position only (no measurables or scouting data for 2026 prospects) — treat as a rough historical baseline, not a scouting projection. Recently drafted comps reflect only 1–2 seasons of data.
        </p>
        <div id="coCollegeSection"></div>
      </div>
    </div>`;
  attachCollegeStats(prospect);
}

// Best-effort: the NCAA stats table may not be populated yet (it's loaded
// separately via load_ncaa_stats.py), so a miss here just means the section
// doesn't render -- never an error state on top of the career-outcome view.
async function fetchCollegeStats(name) {
  try {
    const rows = await fetch(`/api/ncaa-stats?name=${encodeURIComponent(name)}`).then(r => r.json());
    return Array.isArray(rows) ? rows : [];
  } catch {
    return [];
  }
}

function buildCollegeStatsTable(rows, tableClass = "cmp-table4") {
  return `
    <table class="${tableClass}">
      <thead><tr><th>Season</th><th>Team</th><th>GP</th><th>PTS</th><th>REB</th><th>AST</th><th>FG%</th><th>3P%</th><th>FT%</th><th>TS%</th></tr></thead>
      <tbody>${rows.map(r => `<tr>
        <td>${r.season ?? "—"}</td>
        <td>${escapeHtml(r.team || "—")}</td>
        <td>${r.gp ?? "—"}</td>
        <td>${r.pts_per_game ?? "—"}</td>
        <td>${r.reb_per_game ?? "—"}</td>
        <td>${r.ast_per_game ?? "—"}</td>
        <td>${r.fg_pct ?? "—"}</td>
        <td>${r.fg3_pct ?? "—"}</td>
        <td>${r.ft_pct ?? "—"}</td>
        <td>${r.ts_pct ?? "—"}</td>
      </tr>`).join("")}</tbody>
    </table>
    <p style="color:var(--muted);font-size:0.72rem;margin-top:12px">
      Box-score totals from stats.ncaa.org. Advanced rate stats (AST%/OREB%/DREB%/USG%) only appear when both team and opponent season totals were available for that team's page.
    </p>`;
}

// Best-effort: the NCAA stats table may not be populated yet (it's loaded
// separately via load_ncaa_stats.py), so a miss here just means the section
// doesn't render -- never an error state on top of the career-outcome view.
async function attachCollegeStats(prospect) {
  const rows = await fetchCollegeStats(prospect.name);
  if (!rows.length) return;
  const section = document.getElementById("coCollegeSection");
  if (!section) return;
  section.innerHTML = `
    <div class="co-section-head">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2"/><path d="M3 10h18M8 2v4M16 2v4"/></svg>
      <span>College Stats${rows[0].team ? ` — ${escapeHtml(rows[0].team)}` : ""}</span>
    </div>
    ${buildCollegeStatsTable(rows)}`;
}

// ── Draft Projection (hybrid ML + comp engine + archetype model) ───────────
let draftProjectionProspect = null;

function renderDraftProjectionBar() {
  const bar = $("#draftProjectionBar");
  if (!bar) return;
  const color = cmpColor(0);

  if (!draftProjectionProspect) {
    const top10 = allProspects.slice(0, 10);
    const chips = top10.map(p => {
      const wrapped = wrapProspect(p);
      return `<button class="dp-chip" data-name="${escapeHtml(wrapped.name)}">
        <span class="dp-chip-rank">#${p.rank}</span>
        <span class="dp-chip-name">${escapeHtml(wrapped.name)}</span>
        <span class="dp-chip-meta">${escapeHtml(p.pos)} · ${escapeHtml(p.school)}</span>
      </button>`;
    }).join("");

    bar.innerHTML = `
      <div class="dp-search-hero">
        <div class="dp-search-label">2026 NBA Draft — ${allProspects.length} prospects ranked</div>
        <div class="dp-search-wrap" id="draftProjSlot0">
          <svg class="dp-search-icon" viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg">
            <circle cx="8.5" cy="8.5" r="5.5" stroke="currentColor" stroke-width="1.6"/>
            <path d="M13 13L17 17" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>
          </svg>
          <input id="draftProjSearch0" type="search" placeholder="Search any prospect by name…"
            class="dp-search-input" autocomplete="off" spellcheck="false"/>
          <div class="compare-dropdown" id="draftProjDrop0"></div>
        </div>
        <div class="dp-chips-label">Top lottery picks — click to project</div>
        <div class="dp-chips-row">${chips}</div>
      </div>`;

    setupDraftProjectionSearch();

    bar.querySelectorAll(".dp-chip").forEach(btn => {
      btn.addEventListener("click", () => {
        const pool = allProspects.map(wrapProspect);
        const found = pool.find(p => p.name === btn.dataset.name);
        if (found) {
          draftProjectionProspect = found;
          renderDraftProjectionBar();
          renderDraftProjectionPage();
        }
      });
    });
    return;
  }

  const p = draftProjectionProspect;
  bar.innerHTML = `<div class="dp-selected-bar">
    <div class="cmp-slot-color-bar" style="background:${color};border-radius:6px 6px 0 0"></div>
    <div class="cmp-selected-inner">
      ${cmpAvatar(p, 48)}
      <div class="cmp-selected-info">
        <div class="cmp-selected-name" style="font-size:1rem">${escapeHtml(p.name)}</div>
        <div class="cmp-selected-meta">${p.position||"—"} · ${p.team||"—"} · ${p.status||"Prospect"}</div>
        <div class="cmp-selected-meta">${p.height||"—"} · ${p.weight||"—"} · Mock Rank #${p.rank||"—"}</div>
      </div>
      <button class="cmp-remove-btn draft-proj-remove-btn" title="Clear">✕</button>
    </div>
  </div>`;
}

function setupDraftProjectionSearch() {
  const input = $("#draftProjSearch0");
  const dropdown = $("#draftProjDrop0");
  if (!input || !dropdown) return;
  input.addEventListener("input", () => {
    const q = input.value.toLowerCase().trim();
    const pool = allProspects.map(wrapProspect);
    if (!q || !pool.length) { dropdown.classList.remove("open"); return; }
    const matches = pool.filter(p => p.name.toLowerCase().includes(q)).slice(0, 8);
    dropdown.innerHTML = matches.map(p => {
      const raw = allProspects.find(r => wrapProspect(r).name === p.name) || {};
      return `<div class="compare-option" data-name="${escapeHtml(p.name)}">
        <span class="dp-drop-rank">#${raw.rank||"—"}</span>
        ${escapeHtml(p.name)}
        <span style="color:var(--muted);font-size:0.75em">· ${escapeHtml(raw.pos||"—")} · ${escapeHtml(raw.school||"Prospect")}</span>
      </div>`;
    }).join("");
    dropdown.classList.toggle("open", matches.length > 0);
    dropdown.querySelectorAll(".compare-option").forEach(opt => {
      opt.addEventListener("click", () => {
        const found = pool.find(p => p.name === opt.dataset.name);
        if (found) {
          draftProjectionProspect = found;
          renderDraftProjectionBar();
          renderDraftProjectionPage();
        }
        dropdown.classList.remove("open");
      });
    });
  });
}

function renderDraftProjectionPage() {
  const container = $("#draftProjectionContent");
  if (!container) return;
  if (!draftProjectionProspect) {
    container.innerHTML = `<div class="compare-placeholder"><div class="compare-placeholder-icon">◎</div><div>Search for a draft prospect above to generate a career-tier projection.</div></div>`;
    return;
  }
  renderDraftProjectionView(container, draftProjectionProspect);
}

const TIER_BAR_COLOR = {
  "Bust": "#e25c5c", "End-of-Bench Player": "#e08a4e", "Rotation Player": "#e0c14e",
  "Starter": "#8fd15a", "High-Level Starter": "#4ecbb0", "All-Star": "#4eb8e0",
  "All-NBA": "#7e6ee0", "Superstar": "#c061e0",
};

function buildOutcomeProbabilityBars(probabilities) {
  return Object.entries(probabilities).map(([tier, prob]) => {
    const pct = Math.round(prob * 1000) / 10;
    const color = TIER_BAR_COLOR[tier] || "var(--accent)";
    return `<div class="dp-prob-row">
      <div class="dp-prob-label">${tier}</div>
      <div class="dp-prob-track"><div class="dp-prob-fill" style="width:${pct}%;background:${color}"></div></div>
      <div class="dp-prob-pct">${pct}%</div>
    </div>`;
  }).join("");
}

const ARCHETYPE_COLOR = {
  "Heliocentric Engine":  "#c061e0",
  "Secondary Playmaker":  "#7e6ee0",
  "Off-Ball Scorer":      "#4eb8e0",
  "Scoring Big":          "#4ecbb0",
  "Playmaking Big":       "#8fd15a",
  "Rim Protector":        "#e0c14e",
  "3&D Wing":             "#4e9de0",
  "Defensive Wing":       "#e08a4e",
  "Hybrid Offensive Big": "#c06160",
};

function buildArchetypeMixBars(mix) {
  if (!mix) {
    return `<p style="color:var(--muted);font-size:0.85rem">No college archetype profile available yet -- requires college statistical data that hasn't been loaded.</p>`;
  }
  const entries = Object.entries(mix).sort((a, b) => b[1] - a[1]);
  return entries.map(([name, pct], i) => {
    const color = ARCHETYPE_COLOR[name] || `hsl(${(i * 40) % 360},65%,60%)`;
    return `
    <div class="dp-prob-row">
      <div class="dp-prob-label">${name}</div>
      <div class="dp-prob-track"><div class="dp-prob-fill" style="width:${pct}%;background:${color}"></div></div>
      <div class="dp-prob-pct">${pct.toFixed(1)}%</div>
    </div>`;
  }).join("");
}

function buildShotCreationPanel(shotCreation) {
  if (!shotCreation) return "";
  const zones = [
    { key: "rim",   label: "At the Rim" },
    { key: "mid",   label: "Mid-Range"  },
    { key: "three", label: "3PT"        },
  ];
  const rows = zones.map(({ key, label }) => {
    const z = shotCreation[key];
    if (!z || z.made < 1) return "";
    const astW  = z.ast_pct;
    const unastW = z.unast_pct;
    return `
    <div class="dp-sc-row">
      <div class="dp-sc-label">${label}</div>
      <div class="dp-sc-bar-wrap">
        <div class="dp-sc-bar-ast"  style="width:${astW}%"  title="Assisted ${z.ast_pct}%"></div>
        <div class="dp-sc-bar-unast" style="width:${unastW}%" title="Unassisted ${z.unast_pct}%"></div>
      </div>
      <div class="dp-sc-stats">
        <span class="dp-sc-tag ast">AST ${z.ast_pct}% <span class="dp-sc-count">(${z.assisted}/${z.made})</span></span>
        <span class="dp-sc-tag unast">UNAST ${z.unast_pct}% <span class="dp-sc-count">(${z.unassisted}/${z.made})</span></span>
      </div>
    </div>`;
  }).join("");
  if (!rows.trim()) return "";
  return `
  <div class="dp-sc-wrap">
    <div class="dp-sc-legend">
      <span><span class="dp-sc-swatch ast"></span>Assisted</span>
      <span><span class="dp-sc-swatch unast"></span>Unassisted</span>
    </div>
    ${rows}
  </div>`;
}

function buildExplainabilityLists(explainability) {
  const section = (title, items) => {
    if (!items || !items.length) return "";
    return `<div class="dp-explain-block">
      <div class="dp-explain-title">${title}</div>
      <ul class="dp-explain-list">${items.map(i => `<li>${escapeHtml(i)}</li>`).join("")}</ul>
    </div>`;
  };
  const out = [
    section("Strengths", explainability.strengths),
    section("Weaknesses", explainability.weaknesses),
    section("Development Indicators", explainability.development_indicators),
    section("Risk Indicators", explainability.risk_indicators),
  ].join("");
  return out || `<p style="color:var(--muted);font-size:0.85rem">No notable signals crossed a reporting threshold for this prospect.</p>`;
}

function buildComparablesTable(comparables) {
  return `<table class="cmp-table4">
    <thead><tr><th>Player</th><th>Pick</th><th>Year</th><th>College</th><th>Similarity</th><th>Archetype Match</th><th>Actual Outcome</th></tr></thead>
    <tbody>${comparables.map(c => `<tr title="${escapeHtml(c.explanation || "")}">
      <td>${escapeHtml(c.player)}</td>
      <td>${c.overall_pick != null ? "#" + Math.round(c.overall_pick) : "—"}</td>
      <td>${c.draft_season ?? "—"}</td>
      <td>${escapeHtml(c.college || "—")}</td>
      <td>${c.similarity}%</td>
      <td>${c.archetype_match != null ? c.archetype_match + "%" : "—"}</td>
      <td>${escapeHtml(c.actual_outcome)}</td>
    </tr>`).join("")}</tbody>
  </table>
  <p style="color:var(--muted);font-size:0.72rem;margin-top:12px">Hover a row for the plain-language comp explanation.</p>`;
}

async function renderDraftProjectionView(container, prospect) {
  container.innerHTML = `<div class="compare-placeholder"><div class="compare-placeholder-icon">⏳</div><div>Generating career-tier projection for ${escapeHtml(prospect.name)}...</div></div>`;
  let data;
  try {
    data = await fetch(`/api/draft-projection?name=${encodeURIComponent(prospect.name)}`).then(r => r.json());
    if (data.error) throw new Error(data.error);
  } catch (e) {
    container.innerHTML = `<div class="compare-placeholder"><div class="compare-placeholder-icon">⚠</div><div>Couldn't load a draft projection for ${escapeHtml(prospect.name)}.</div></div>`;
    return;
  }

  const dq = data.data_quality;
  const banner = dq.college_data_available ? "" : `
    <div class="dp-banner">
      ${escapeHtml(dq.confidence_note)}
    </div>`;
  const mlBanner = dq.ml_model_loaded ? "" : `
    <div class="dp-banner">
      The ML layer hasn't been trained yet -- these probabilities currently come from the historical comp engine alone.
    </div>`;

  container.innerHTML = `
    <div class="cmp-pcard" style="max-width:1040px;margin:0 auto" id="draftProjCard">
      <div class="cmp-pcard-title">Career-Tier Projection — ${escapeHtml(prospect.name)}</div>
      ${banner}${mlBanner}

      <div class="cmp-overview4" style="grid-template-columns:repeat(3,1fr);gap:12px;margin-top:14px">
        <div class="cmp-pcard"><div class="cmp-pcard-title">Floor</div><div style="font-size:1.3rem;font-weight:800">${escapeHtml(data.floor_outcome)}</div></div>
        <div class="cmp-pcard"><div class="cmp-pcard-title">Expected</div><div style="font-size:1.3rem;font-weight:800;color:var(--accent)">${escapeHtml(data.expected_outcome)}</div></div>
        <div class="cmp-pcard"><div class="cmp-pcard-title">Ceiling</div><div style="font-size:1.3rem;font-weight:800">${escapeHtml(data.ceiling_outcome)}</div></div>
      </div>

      <div class="cmp-pcard-title" style="margin-top:20px">Outcome Probabilities</div>
      <div class="dp-prob-list">${buildOutcomeProbabilityBars(data.outcome_probabilities)}</div>

      <div class="cmp-pcard-title" style="margin-top:20px">College Archetype Profile</div>
      <div class="dp-prob-list">${buildArchetypeMixBars(data.archetype.mix)}</div>

      ${data.archetype.shot_creation ? `
      <div class="cmp-pcard-title" style="margin-top:20px">Shot Creation by Zone</div>
      <div style="font-size:0.75rem;color:var(--muted);margin-bottom:8px">What % of made shots were self-created vs. assisted, by zone</div>
      ${buildShotCreationPanel(data.archetype.shot_creation)}` : ""}

      <div class="cmp-pcard-title" style="margin-top:20px">Top Historical Comparables</div>
      ${data.comparables.length ? buildComparablesTable(data.comparables) : `<p style="color:var(--muted);font-size:0.85rem">No comparable historical draft picks found.</p>`}

      <div class="cmp-pcard-title" style="margin-top:20px">Scouting Notes</div>
      ${buildExplainabilityLists(data.explainability)}
    </div>`;
}

// Same data source, surfaced on every NBA player's profile (most have an
// NCAA history) rather than only on 2026 draft prospects. Hidden entirely
// when there's no match -- most current NBA vets won't have rows yet since
// the scraper hasn't been run against full historical data.
async function loadCollegeStatsPanel(player) {
  const card = $("#collegeStatsCard");
  if (!card) return;
  card.classList.add("hidden");
  const rows = await fetchCollegeStats(player.name);
  if (!rows.length) return;
  $("#collegeStatsSub").textContent = rows[0].team ? `${rows[0].team}` : "";
  $("#collegeStatsPanel").innerHTML = buildCollegeStatsTable(rows, "dark-table");
  card.classList.remove("hidden");
}

// ── Overview ──────────────────────────────────────────────────────────────────
function renderOverviewTab(container, active) {
  const statRows = [["Points","pts",false],["Rebounds","reb",false],["Assists","ast",false],["3PM","three",false],["Steals","stl",false],["Blocks","blk",false],["Turnovers","tov",true],["TS%","ts",false],["USG%","usg",false],["Minutes","min",false]];
  container.innerHTML = `
    <div class="cmp-overview2">
      <div class="cmp-pcard">
        <div class="cmp-pcard-title">Attribute Radar</div>
        <div class="cmp-radar-legend">${active.map(a=>`<span style="color:${a.color}">⬤ ${a.player.name.split(" ").pop()}</span>`).join("")}</div>
        <canvas id="cmpRadar" width="340" height="340" style="display:block;margin:0 auto"></canvas>
      </div>
      <div class="cmp-pcard">
        <div class="cmp-pcard-title">Key Statistics</div>
        ${build4Table(statRows,active)}
      </div>
    </div>`;
  setTimeout(()=>drawSpider4("cmpRadar",active),0);
}

function drawSpider4(canvasId, active) {
  const canvas=$(`#${canvasId}`);if(!canvas)return;
  const ctx=canvas.getContext("2d"),w=canvas.width,h=canvas.height,cx=w/2,cy=h/2,r=Math.min(w,h)*0.36;
  const labels=["PTS","REB","AST","STL","BLK","3PM","TS%"],maxes=[35,15,12,3,3,5,72];
  const n=labels.length,angle=i=>(Math.PI*2/n)*i-Math.PI/2;
  ctx.clearRect(0,0,w,h);
  for(let ring=1;ring<=4;ring++){ctx.beginPath();for(let i=0;i<n;i++){const a=angle(i),rr=r*(ring/4);i===0?ctx.moveTo(cx+Math.cos(a)*rr,cy+Math.sin(a)*rr):ctx.lineTo(cx+Math.cos(a)*rr,cy+Math.sin(a)*rr);}ctx.closePath();ctx.strokeStyle="rgba(255,255,255,0.07)";ctx.lineWidth=1;ctx.stroke();}
  for(let i=0;i<n;i++){const a=angle(i);ctx.beginPath();ctx.moveTo(cx,cy);ctx.lineTo(cx+Math.cos(a)*r,cy+Math.sin(a)*r);ctx.strokeStyle="rgba(255,255,255,0.07)";ctx.lineWidth=1;ctx.stroke();}
  active.forEach(({player,color})=>{
    const s=latestSeason(player);if(!s)return;
    const vals=[+s.pts||0,+s.reb||0,+s.ast||0,+s.stl||0,+s.blk||0,+s.three||0,+s.ts||0];
    ctx.beginPath();vals.forEach((v,i)=>{const pct=Math.min(v/maxes[i],1),a=angle(i),x=cx+Math.cos(a)*r*pct,y=cy+Math.sin(a)*r*pct;i===0?ctx.moveTo(x,y):ctx.lineTo(x,y);});
    ctx.closePath();ctx.save();ctx.globalAlpha=0.15;ctx.fillStyle=color;ctx.fill();ctx.restore();
    ctx.strokeStyle=color;ctx.lineWidth=2;ctx.stroke();
  });
  ctx.fillStyle="rgba(220,232,255,0.65)";ctx.font="bold 12px system-ui";ctx.textAlign="center";
  labels.forEach((lbl,i)=>{const a=angle(i);ctx.fillText(lbl,cx+Math.cos(a)*(r+20),cy+Math.sin(a)*(r+20)+4);});
}

// ── Advanced Metrics ──────────────────────────────────────────────────────────
function renderAdvancedTab(container, active) {
  const rows=[["PER","per",false],["True Shooting%","ts",false],["Usage%","usg",false],["BPM","net",false],["Off BPM","obpm",false],["Def BPM","dbpm",false],["VORP","vorp",false],["Win Shares","ws",false],["Off WS","ows",false],["Def WS","dws",false],["Points","pts",false],["Rebounds","reb",false],["Assists","ast",false],["3PM","three",false],["Steals","stl",false],["Blocks","blk",false],["Turnovers","tov",true],["Minutes","min",false],["Games","gp",false]];
  const season=latestSeason(active[0].player)?.season||"";
  container.innerHTML=`<div class="cmp-pcard cmp-full">
    <div class="cmp-pcard-title">Advanced Metrics · ${season}</div>
    <div class="cmp-adv-header"><span>Metric</span>${active.map(a=>`<span style="color:${a.color}">${a.player.name}</span>`).join("")}</div>
    ${build4Table(rows,active)}
  </div>`;
}

// ── Scouting Report ───────────────────────────────────────────────────────────
function renderScoutingTab(container, active) {
  const cards=active.map(({player,color})=>{
    const last=latestSeason(player),score=cmpEffScore(player);
    const tier=score>=80?"MVP Caliber":score>=65?"All-Star":score>=50?"Starter":score>=35?"Rotation":"Developmental";
    const arch=score>=80?"All-Around Star":score>=65?"Score-First Guard":score>=50?"Two-Way Wing":score>=35?"3-and-D Wing":"Floor Spacer";
    const str=[],wk=[];
    if(last){
      if((+last.pts||0)>=20)str.push(`Elite scorer (${(+last.pts).toFixed(1)} PPG)`);
      if((+last.ast||0)>=7) str.push(`Top distributor (${(+last.ast).toFixed(1)} APG)`);
      if((+last.reb||0)>=8) str.push(`Strong rebounder (${(+last.reb).toFixed(1)} RPG)`);
      if((+last.blk||0)>=1.5)str.push(`Rim protector (${(+last.blk).toFixed(1)} BPG)`);
      if((+last.stl||0)>=1.5)str.push(`Ball-hawk (${(+last.stl).toFixed(1)} SPG)`);
      if((+last.ts||0)>=60) str.push(`Efficient scorer (${(+last.ts).toFixed(1)} TS%)`);
      if((+last.net||0)>=3) str.push(`High impact (${(+last.net).toFixed(1)} BPM)`);
      if((+last.usg||0)>=30)str.push(`High-usage offensive engine (${(+last.usg).toFixed(1)} USG%)`);
      if((+last.three||0)>=2.5)str.push(`High-volume 3PT threat (${(+last.three).toFixed(1)} 3PM)`);
      if((+last.obpm||0)>=4)str.push(`Plus offensive impact (${(+last.obpm).toFixed(1)} OBPM)`);
      if((+last.dbpm||0)>=3)str.push(`Plus defensive impact (${(+last.dbpm).toFixed(1)} DBPM)`);
      if((+last.vorp||0)>=5)str.push(`High season value (${(+last.vorp).toFixed(1)} VORP)`);
      if((+last.ws||0)>=8)str.push(`Major win contributor (${(+last.ws).toFixed(1)} Win Shares)`);
      if((+last.per||0)>=22)str.push(`Elite per-possession production (${(+last.per).toFixed(1)} PER)`);
      if((+last.gp||0)>=75)str.push(`Highly durable (${last.gp} games played)`);
      if((+last.tov||0)>=3.5)wk.push(`Turnover-prone (${(+last.tov).toFixed(1)} TOV)`);
      if((+last.ts||0)<50&&last.ts)wk.push(`Below-avg efficiency (${(+last.ts).toFixed(1)} TS%)`);
      if((+last.net||0)<-1&&last.net)wk.push(`Negative impact (${(+last.net).toFixed(1)} BPM)`);
      if((+last.obpm||0)<-1&&last.obpm)wk.push(`Limited offensive impact (${(+last.obpm).toFixed(1)} OBPM)`);
      if((+last.dbpm||0)<-1&&last.dbpm)wk.push(`Defensive liability (${(+last.dbpm).toFixed(1)} DBPM)`);
      if((+last.vorp||0)<1&&last.vorp!=null)wk.push(`Minimal season impact (${(+last.vorp).toFixed(1)} VORP)`);
      if((+last.gp||0)>0&&(+last.gp||0)<50)wk.push(`Durability concern (${last.gp} games played)`);
      if((+last.usg||0)<15&&last.usg)wk.push(`Limited offensive role (${(+last.usg).toFixed(1)} USG%)`);
      if((+last.blk||0)<0.3&&(+last.reb||0)<4)wk.push(`Minimal rim/interior presence`);
      if(!str.length)str.push("Balanced contributor");
      if(!wk.length)wk.push("No major concerns identified");
    }
    return `<div class="cmp-scout-card" style="border-top:3px solid ${color}">
      <div style="display:flex;align-items:center;gap:14px;margin-bottom:14px">
        ${cmpAvatar(player,52)}
        <div style="flex:1;min-width:0">
          <div class="cmp-scout-name">${player.name}</div>
          <div class="cmp-scout-archetype">${arch}</div>
        </div>
        <div style="text-align:center">
          <div style="font-size:2.1rem;font-weight:900;color:${color};line-height:1">${score}</div>
          <div style="font-size:0.68rem;color:var(--muted)">${tier}</div>
        </div>
      </div>
      <div class="cmp-scout-section">Strengths</div>
      ${str.slice(0,6).map(s=>`<div class="cmp-scout-item" style="color:var(--green)">✓ ${s}</div>`).join("")}
      <div class="cmp-scout-section">Areas to Improve</div>
      ${wk.slice(0,5).map(w=>`<div class="cmp-scout-item" style="color:var(--muted)">↑ ${w}</div>`).join("")}
    </div>`;
  }).join("");
  container.innerHTML=`<div class="cmp-scout-grid">${cards}</div>`;
}

// ── Player Trajectories ───────────────────────────────────────────────────────
function renderTrajectoryTab(container, active) {
  const metrics=[{k:"pts",l:"PTS"},{k:"reb",l:"REB"},{k:"ast",l:"AST"},{k:"three",l:"3PM"},{k:"stl",l:"STL"},{k:"blk",l:"BLK"},{k:"net",l:"BPM"}];
  container.innerHTML=`
    <div class="cmp-pcard cmp-full">
      <div class="cmp-pcard-title">Career Trajectory Comparison</div>
      <div class="cmp-traj-controls">${metrics.map((m,i)=>`<button class="cmp-traj-btn${i===0?" active":""}" data-metric="${m.k}">${m.l}</button>`).join("")}</div>
      <div class="cmp-radar-legend" style="margin-bottom:10px">${active.map(a=>`<span style="color:${a.color}">— ${a.player.name}</span>`).join("")}</div>
      <canvas id="cmpTrajChart" width="1100" height="360" style="width:100%;height:auto;display:block"></canvas>
    </div>`;
  let trajMetric="pts";
  const drawTraj=()=>{
    const canvas=$("#cmpTrajChart");if(!canvas)return;
    const ctx=canvas.getContext("2d"),w=canvas.width,h=canvas.height;
    const pad={top:24,right:32,bottom:44,left:52},pw=w-pad.left-pad.right,ph=h-pad.top-pad.bottom;
    const series=active.map(a=>({color:a.color,pts:a.player.seasons.filter(s=>s[trajMetric]!=null&&s.age!=null).map(s=>({x:+s.age||0,y:+s[trajMetric]})).sort((a,b)=>a.x-b.x)}));
    const all=series.flatMap(s=>s.pts);
    ctx.clearRect(0,0,w,h);ctx.fillStyle="#0b1729";ctx.fillRect(0,0,w,h);
    if(!all.length)return;
    const minX=Math.min(...all.map(p=>p.x)),maxX=Math.max(...all.map(p=>p.x));
    const allY=all.map(p=>p.y),minY=Math.min(0,...allY),maxY=Math.max(...allY)*1.15;
    const scX=v=>pad.left+((v-minX)/Math.max(maxX-minX,1))*pw;
    const scY=v=>pad.top+ph-((v-minY)/Math.max(maxY-minY,0.01))*ph;
    for(let i=0;i<=5;i++){
      const y=pad.top+(ph/5)*i,val=maxY-((maxY-minY)/5)*i;
      ctx.beginPath();ctx.moveTo(pad.left,y);ctx.lineTo(w-pad.right,y);ctx.strokeStyle="rgba(255,255,255,0.05)";ctx.lineWidth=1;ctx.stroke();
      ctx.fillStyle="rgba(220,232,255,0.4)";ctx.font="11px system-ui";ctx.textAlign="right";ctx.fillText(val.toFixed(1),pad.left-6,y+4);
    }
    ctx.fillStyle="rgba(220,232,255,0.4)";ctx.font="11px system-ui";ctx.textAlign="center";
    for(let age=Math.ceil(minX);age<=maxX;age+=2)ctx.fillText(age,scX(age),h-10);
    series.forEach(({color,pts})=>{
      if(!pts.length)return;
      ctx.beginPath();pts.forEach((p,i)=>i===0?ctx.moveTo(scX(p.x),scY(p.y)):ctx.lineTo(scX(p.x),scY(p.y)));
      ctx.strokeStyle=color;ctx.lineWidth=2.5;ctx.stroke();
      pts.forEach(p=>{ctx.beginPath();ctx.arc(scX(p.x),scY(p.y),3.5,0,Math.PI*2);ctx.fillStyle=color;ctx.fill();});
    });
  };
  setTimeout(drawTraj,0);
  document.querySelectorAll(".cmp-traj-btn").forEach(btn=>{
    btn.addEventListener("click",()=>{
      document.querySelectorAll(".cmp-traj-btn").forEach(b=>b.classList.remove("active"));
      btn.classList.add("active");trajMetric=btn.dataset.metric;drawTraj();
    });
  });
}

// ── Stat Profile (ring charts) ────────────────────────────────────────────────
const RING_STATS=[
  {key:"pts",label:"PTS",max:40},{key:"reb",label:"REB",max:18},{key:"ast",label:"AST",max:14},
  {key:"stl",label:"STL",max:3.5},{key:"blk",label:"BLK",max:4},{key:"ts",label:"TS%",max:72},
  {key:"usg",label:"USG%",max:42},{key:"net",label:"BPM",max:15,offset:15},
  {key:"ws",label:"WS",max:18},{key:"obpm",label:"OBPM",max:12,offset:12},
  {key:"dbpm",label:"DBPM",max:8,offset:8},{key:"vorp",label:"VORP",max:8},
];

function renderStatProfileTab(container, active) {
  const rings=RING_STATS.map((stat,i)=>`
    <div class="cmp-ring-item">
      <canvas id="ring_${i}" width="120" height="120"></canvas>
      <div class="cmp-ring-label">${stat.label}</div>
      <div class="cmp-ring-vals">${active.map(a=>{const s=latestSeason(a.player);const v=s?parseFloat(s[stat.key]):NaN;return `<span class="cmp-ring-val" style="background:${a.color}22;color:${a.color}">${isNaN(v)?"—":v.toFixed(1)}</span>`;}).join("")}</div>
    </div>`).join("");
  container.innerHTML=`
    <div class="cmp-pcard cmp-full">
      <div class="cmp-pcard-title">Stat Profile Comparison</div>
      <div class="cmp-radar-legend" style="margin-bottom:16px">${active.map(a=>`<span style="color:${a.color}">⬤ ${a.player.name}</span>`).join("")}</div>
      <div class="cmp-ring-grid">${rings}</div>
    </div>`;
  setTimeout(()=>{RING_STATS.forEach((stat,i)=>drawRingChart(`ring_${i}`,active,stat));},0);
}

function drawRingChart(canvasId, active, stat) {
  const canvas=$(`#${canvasId}`);if(!canvas)return;
  const ctx=canvas.getContext("2d"),w=canvas.width,h=canvas.height,cx=w/2,cy=h/2;
  const outerR=Math.min(w,h)*0.43,trackW=Math.max(7,outerR*0.22),gap=trackW*0.28;
  ctx.clearRect(0,0,w,h);
  active.forEach(({player,color},i)=>{
    const s=latestSeason(player),raw=s?parseFloat(s[stat.key]):NaN;
    let pct=0;
    if(!isNaN(raw)){pct=stat.offset?Math.min(1,Math.max(0,(raw+stat.offset)/(stat.max*2))):Math.min(1,Math.max(0,raw/stat.max));}
    const r=outerR-i*(trackW+gap);if(r<4)return;
    ctx.beginPath();ctx.arc(cx,cy,r,0,Math.PI*2);ctx.strokeStyle="rgba(255,255,255,0.06)";ctx.lineWidth=trackW;ctx.stroke();
    if(pct>0.01){
      ctx.beginPath();ctx.arc(cx,cy,r,-Math.PI/2,-Math.PI/2+pct*Math.PI*2);ctx.strokeStyle=color;ctx.lineWidth=trackW;ctx.lineCap="round";ctx.stroke();ctx.lineCap="butt";
    }
  });
}

function buildLocalReasons(anchor, comp) {
  const la = latestSeason(anchor), lb = latestSeason(comp);
  if (!la || !lb) return [];
  const reasons = [];
  const close = (a, b, pct=0.15) => Math.abs(a-b) <= Math.max(a,b)*pct;
  if (close(+la.pts||0, +lb.pts||0)) reasons.push("Similar scoring volume");
  if (close(+la.ast||0, +lb.ast||0)) reasons.push("Similar playmaking rate");
  if (close(+la.reb||0, +lb.reb||0)) reasons.push("Similar rebounding profile");
  if (close(+la.ts||0,  +lb.ts||0))  reasons.push("Similar scoring efficiency");
  if (close(+la.usg||0, +lb.usg||0)) reasons.push("Similar usage role");
  if (close(+la.net||0, +lb.net||0, 0.25)) reasons.push("Similar overall impact");
  if (cmpPosGroup(anchor.position) === cmpPosGroup(comp.position)) reasons.push(`Same position group (${cmpPosGroup(anchor.position)})`);
  return reasons.slice(0, 4);
}

// ── Similar Players ───────────────────────────────────────────────────────────
function renderSimilarTab(container, active) {
  const anchor=active[0].player;
  container.innerHTML=`
    <div class="cmp-pcard cmp-full">
      <div class="cmp-pcard-title">Similar Players · <span style="color:var(--sidebar-active);text-transform:none;letter-spacing:0">${anchor.name}</span></div>
      <div id="cmpSimilarContent" style="color:var(--muted);font-size:0.85rem;padding:16px 0">Loading from comparison engine...</div>
    </div>`;
  fetchSimilarTab(anchor);
}

async function fetchSimilarTab(anchor) {
  const el=$("#cmpSimilarContent");if(!el)return;
  try {
    const similar=await fetchArchetypeSimilar(anchor);
    if(similar&&similar.length){
      el.innerHTML=`<div class="cmp-comp-grid">${similar.slice(0,8).map(s=>{
        const narrative=s.explanation||"";
        const simPct=s.similarity;
        const subs=s.breakdown||{};
        const simColor=simPct>=80?"var(--green)":simPct>=60?"var(--sidebar-active)":"var(--muted)";
        const subBar=(label,val)=>{
          if(val==null)return"";
          const c=val>=80?"var(--green)":val>=60?"var(--sidebar-active)":"var(--muted)";
          return `<div class="cmp-sub-row"><span class="cmp-sub-lbl">${label}</span><div class="cmp-sub-track"><div class="cmp-sub-fill" style="width:${Math.round(val)}%;background:${c}"></div></div><span class="cmp-sub-val" style="color:${c}">${val}%</span></div>`;
        };
        return `<div class="cmp-comp-card">
          <div class="cmp-comp-header">
            <div class="cmp-comp-score" style="border-color:${simColor}">
              <div class="cmp-comp-score-num" style="color:${simColor}">${simPct}%</div>
              <div class="cmp-comp-score-lbl">Overall</div>
            </div>
            <div style="flex:1;min-width:0">
              <div style="font-size:0.9rem;font-weight:800;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${escapeHtml(s.player||s.player_name||"")}</div>
              <div style="font-size:0.7rem;color:var(--muted)">${s.season||""} &middot; ${(s.dominant_engine||"").replace(/_/g," ")}</div>
            </div>
          </div>
          <div class="cmp-sub-scores">
            ${subBar("Play Style",subs.playstyle_similarity)}
            ${subBar("Stats (eff-adj.)",subs.efficiency_adjusted_stats_similarity)}
            ${subBar("Advanced",subs.advanced_metrics_similarity)}
            ${subBar("Physical",subs.physical_similarity)}
          </div>
          ${narrative?`<div class="cmp-comp-narrative">${escapeHtml(narrative)}</div>`:""}
        </div>`;
      }).join("")}</div>`;
      return;
    }
  } catch {}
  // Local fallback with position-aware similarity
  const rows=players.filter(p=>p.playerId!==anchor.playerId)
    .map(p=>{const s=latestSeason(p);if(!s)return null;const sim=cmpLocalSimilarity(anchor,p);return{p,s,sim};})
    .filter(r=>r&&r.sim>0).sort((a,b)=>b.sim-a.sim).slice(0,8);
  if(!rows.length){el.innerHTML=`<div style="color:var(--muted)">No season data for this player.</div>`;return;}
  el.innerHTML=`<div class="cmp-comp-grid">${rows.map(({p,s,sim})=>{
    const simColor=sim>=80?"var(--green)":sim>=60?"var(--sidebar-active)":"var(--muted)";
    const reasons=buildLocalReasons(anchor,p);
    return `<div class="cmp-comp-card">
      <div class="cmp-comp-header">
        <div class="cmp-comp-score" style="border-color:${simColor}">
          <div class="cmp-comp-score-num" style="color:${simColor}">${sim}%</div>
          <div class="cmp-comp-score-lbl">Match</div>
        </div>
        <div style="flex:1;min-width:0">
          <div style="font-size:0.9rem;font-weight:800">${p.name}</div>
          <div style="font-size:0.7rem;color:var(--muted)">${p.position} · ${s.team||"—"} · ${s.season||"—"}</div>
        </div>
      </div>
      ${reasons.length?`<div class="cmp-comp-reasons">${reasons.map(r=>`<div class="cmp-comp-reason">${r}</div>`).join("")}</div>`:""}
    </div>`;
  }).join("")}</div>`;
}

renderPlayerBar();

// ── Watchlist ────────────────────────────────────────────────────────────────

function getWatchlist() {
  try { return JSON.parse(localStorage.getItem("nba_watchlist") || "[]"); } catch { return []; }
}

function saveWatchlist(list) {
  localStorage.setItem("nba_watchlist", JSON.stringify(list));
}

function renderWatchlist() {
  const ids = getWatchlist();
  const watched = players.filter((p) => ids.includes(p.id));
  const empty = $("#watchlistEmpty"), table = $("#watchlistTable");
  if (!watched.length) {
    empty.classList.remove("hidden"); table.classList.add("hidden"); return;
  }
  empty.classList.add("hidden"); table.classList.remove("hidden");
  $("#watchlistRows").innerHTML = watched.map((p) => {
    const l = latestSeason(p);
    return `<tr>
      <td><strong>${p.name}</strong></td>
      <td>${l.season}</td>
      <td>${l.team || "—"}</td>
      <td>${fmt(l.pts,"pts")}</td>
      <td>${fmt(l.reb,"reb")}</td>
      <td>${fmt(l.ast,"ast")}</td>
      <td>${fmt(l.ts,"ts")}%</td>
      <td><button class="watchlist-remove" data-id="${p.id}">Remove</button></td>
    </tr>`;
  }).join("");
  $("#watchlistRows").querySelectorAll(".watchlist-remove").forEach((btn) => {
    btn.addEventListener("click", () => {
      saveWatchlist(getWatchlist().filter((id) => id !== btn.dataset.id));
      renderWatchlist();
    });
  });
}

$("#clearWatchlist").addEventListener("click", () => { saveWatchlist([]); renderWatchlist(); });

// ── Global Search ─────────────────────────────────────────────────────────────

const globalSearch = $("#globalSearch");
const globalResults = $("#globalSearchResults");

globalSearch.addEventListener("input", () => {
  const q = globalSearch.value.toLowerCase().trim();
  if (!q || players.length === 0) { globalResults.classList.add("hidden"); return; }
  const matches = players.filter((p) =>
    p.name.toLowerCase().includes(q) || (p.team || "").toLowerCase().includes(q)
  ).slice(0, 8);
  if (!matches.length) { globalResults.classList.add("hidden"); return; }
  globalResults.innerHTML = matches.map((p) => {
    const l = latestSeason(p);
    return `<div class="search-result-item" data-id="${p.id}">
      <span class="search-result-avatar" style="background:${p.colors[0]}">${initials(p.name)}</span>
      <span><strong>${p.name}</strong><span>${l.team || "—"} · ${p.position} · ${fmt(l.pts,"pts")} PPG</span></span>
    </div>`;
  }).join("");
  globalResults.classList.remove("hidden");
  globalResults.querySelectorAll(".search-result-item").forEach((item) => {
    item.addEventListener("click", () => {
      const player = players.find((p) => p.id === item.dataset.id);
      if (player) {
        globalSearch.value = "";
        globalResults.classList.add("hidden");
        openPlayerProfile(player);
      }
    });
  });
});

document.addEventListener("click", (e) => {
  if (!globalSearch.contains(e.target) && !globalResults.contains(e.target)) {
    globalResults.classList.add("hidden");
  }
});

// ── Boot ─────────────────────────────────────────────────────────────────────

(function initTopbar() {
  const session = localStorage.getItem('sf_session');
  const userEl  = document.getElementById('topbarUser');
  const logoutEl = document.getElementById('topbarLogout');

  if (userEl) {
    if (session && session !== 'guest') {
      userEl.textContent = session;
    } else if (session === 'guest') {
      userEl.textContent = 'Guest';
    }
  }

  if (logoutEl) {
    logoutEl.addEventListener('click', () => {
      localStorage.removeItem('sf_session');
      location.reload();
    });
  }
})();

loadDashboard();
const _initialSection = (location.hash.slice(1) || "dashboard");
navigate(_initialSection);

// Heartbeat: ping every 60s to track active time for logged-in users
(function startHeartbeat() {
  const email = localStorage.getItem('sf_session');
  if (!email || email === 'guest') return;
  const ping = () => fetch('/api/heartbeat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email }),
  }).catch(() => {});
  ping();
  setInterval(ping, 60000);
})();
