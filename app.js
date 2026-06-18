// ── Helpers ──────────────────────────────────────────────────────────────────

const $ = (sel) => document.querySelector(sel);

function toNumber(value, fallback = 0) {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
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

const MEMBERS_ONLY = new Set(["players","player-profile","watchlist","comparisons","team-dashboard","reports","analytics","lineup"]);

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
    showLoginWall();
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
}

document.querySelectorAll(".nav-item").forEach((btn) => {
  btn.addEventListener("click", () => navigate(btn.dataset.section));
});

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

    $("#assistLeaders").innerHTML = data.top_assisters.map((p, i) => `
      <li>
        <span class="mini-rank">${i + 1}</span>
        <span class="player-col"><strong>${p.player}</strong><span>${p.team || "—"}</span></span>
        <span class="stat-val">${(p.ast || 0).toFixed(1)}</span>
      </li>
    `).join("");

    $("#reboundLeaders").innerHTML = data.top_rebounders.map((p, i) => `
      <li>
        <span class="mini-rank">${i + 1}</span>
        <span class="player-col"><strong>${p.player}</strong><span>${p.team || "—"}</span></span>
        <span class="stat-val">${(p.reb || 0).toFixed(1)}</span>
      </li>
    `).join("");

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
  const firstYear = Number(last.season.slice(0, 4)) + 1;

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
  const projections = projectPlayer(player);
  renderProfile(player);
  renderProjection(player, projections);
  renderSeasonTable(player);
  renderPredictions(player, projections);
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
    avatarEl.innerHTML = `<span class="avatar-ini">${initials(player.name)}</span>`;
  }
  $("#playerSummary").textContent = player.summary;
  $("#playerTags").innerHTML = [player.archetype, player.experience, `${fmt(l.pts,"pts")} PPG`, `${fmt(l.ast,"ast")} APG`]
    .map((t) => `<span class="tag">${t}</span>`).join("");
  $("#heroStats").innerHTML = [["PTS",l.pts],["REB",l.reb],["AST",l.ast],["3P",l.three],["TS%",l.ts]]
    .map(([label, val]) => `<div class="hero-stat"><strong>${fmt(val,"pts")}</strong><span>${label} last season</span></div>`).join("");
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
  const nextSeasonLabel = xgPreds ? `${(parseInt(last.season)||2025)+1}-${String((parseInt(last.season)||2025)+2).slice(2)}` : "Next Season";

  // Circular ring math — r=42, circumference≈263.9
  const ringR = 42, ringC = 2 * Math.PI * ringR;
  const ringOffset = ringC * (1 - Math.min(mlSalaryM, 62) / 62);

  el.innerHTML = `
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

function drawChart(player, projections = [], dark = false) {
  const canvas = $("#trendChart");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const w = canvas.width, h = canvas.height;
  const pad = { top: 28, right: 28, bottom: 58, left: 52 };
  const actual = player.seasons.map((s) => ({ label: s.season, value: s[playerState.metric], type: "actual" }));
  const proj = projections.map((s) => ({ label: s.season, value: s[playerState.metric], type: "projected" }));
  const all = actual.concat(proj);
  const vals = all.map((p) => p.value);
  const minV = playerState.metric === "net" ? Math.min(...vals) - 3 : Math.max(0, Math.min(...vals) - 3);
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
  ctx.fillText(`${metricLabels[playerState.metric]} trend`, pad.left, 20);
}

function drawDevCurve(player, dark = false) {
  const canvas = $("#devCurveChart");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const w = canvas.width, h = canvas.height;
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

function renderProjection(player, projections) {
  const final = projections[projections.length - 1];
  $("#projectionCards").innerHTML = [
    ["Projected PTS", final.pts, "pts"], ["Projected REB", final.reb, "reb"],
    ["Projected AST", final.ast, "ast"], ["Projected 3P", final.three, "three"],
    ["Projected MIN", final.min, "min"], ["Projected GP", final.gp, "gp"],
  ].map(([label, val, key]) => `<div class="projection-card"><span>${label}</span><strong>${fmt(val, key)}</strong></div>`).join("");
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

function syncControls() {
  $("#seasonValue").textContent = playerState.seasonsAhead;
  const sign = (v) => v > 0 ? `+${v}` : v;
  $("#minutesValue").textContent = sign(playerState.minutesChange);
  $("#usageValue").textContent = sign(playerState.usageChange);
  $("#durabilityValue").textContent = sign(playerState.durabilityChange);
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

[["seasonRange","seasonsAhead"],["minutesRange","minutesChange"],["usageRange","usageChange"],["durabilityRange","durabilityChange"]].forEach(([id, key]) => {
  $(`#${id}`).addEventListener("input", (e) => {
    playerState[key] = Number(e.target.value);
    syncControls();
    if (playerState.player) {
      const proj = projectPlayer(playerState.player);
      renderProjection(playerState.player, proj);
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
    renderProjection(playerState.player, proj);
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
    // Re-draw canvases when switching back to overview
    if (tab.dataset.pane === "overview" && playerState.player) {
      drawRadar(playerState.player);
      drawChart(playerState.player, projectPlayer(playerState.player), true);
      drawDevCurve(playerState.player, true);
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

let compareA = null, compareB = null;
let cmpActiveTab = "overview";

function setupCompareSearch(inputId, dropdownId, onSelect) {
  const input = $(`#${inputId}`);
  const dropdown = $(`#${dropdownId}`);
  if (!input) return;
  input.addEventListener("input", () => {
    const q = input.value.toLowerCase().trim();
    if (!q || players.length === 0) { dropdown.classList.remove("open"); return; }
    const matches = players.filter((p) => p.name.toLowerCase().includes(q)).slice(0, 8);
    dropdown.innerHTML = matches.map((p) => `<div class="compare-option" data-name="${p.name}">${p.name} · ${p.team}</div>`).join("");
    dropdown.classList.toggle("open", matches.length > 0);
    dropdown.querySelectorAll(".compare-option").forEach((opt) => {
      opt.addEventListener("click", () => {
        const player = players.find((p) => p.name === opt.dataset.name);
        onSelect(player);
        input.value = player.name;
        dropdown.classList.remove("open");
        renderComparison();
      });
    });
  });
  document.addEventListener("click", (e) => {
    if (!input.contains(e.target) && !dropdown.contains(e.target)) dropdown.classList.remove("open");
  });
}

// Tab switching
document.addEventListener("click", (e) => {
  const tab = e.target.closest(".cmp-tab");
  if (!tab) return;
  document.querySelectorAll(".cmp-tab").forEach(t => t.classList.remove("active"));
  tab.classList.add("active");
  cmpActiveTab = tab.dataset.tab;
  renderComparison();
});

function cmpAvatar(player, size = 52) {
  const bg = (player.colors && player.colors[0]) || "#1e3a5f";
  const ini = player.name.split(" ").map(w => w[0]).join("").slice(0,2).toUpperCase();
  return `<div class="cmp-avatar" style="width:${size}px;height:${size}px;background:${bg}">
    <img src="/api/player-photo/${player.playerId}" onerror="this.style.display='none'" style="position:absolute;inset:0;width:100%;height:100%;object-fit:cover;object-position:top;border-radius:inherit"/>
    <span style="position:relative;z-index:1;font-size:${size*0.3}px;font-weight:900;color:#fff">${ini}</span>
  </div>`;
}

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

function renderPlayerBar() {
  const renderSlot = (player, slotId, inputId) => {
    const slot = $(`#${slotId}`);
    if (!slot) return;
    if (!player) {
      slot.querySelector(".cmp-slot-inner").style.display = "flex";
      slot.querySelector(".cmp-selected-card") && slot.querySelector(".cmp-selected-card").remove();
      return;
    }
    slot.querySelector(".cmp-slot-inner").style.display = "none";
    let card = slot.querySelector(".cmp-selected-card");
    if (!card) { card = document.createElement("div"); card.className = "cmp-selected-card"; slot.prepend(card); }
    const last = latestSeason(player);
    const score = cmpEffScore(player);
    const tierLabel = score>=80?"MVP Caliber":score>=65?"All-Star":score>=50?"Starter":score>=35?"Rotation":"Developmental";
    const tierColor = score>=80?"#f0c040":score>=65?"#5b8af0":score>=50?"#3ecf8e":score>=35?"#7a8fb0":"#555";
    card.innerHTML = `
      <div class="cmp-selected-inner">
        ${cmpAvatar(player, 56)}
        <div class="cmp-selected-info">
          <div class="cmp-selected-name">${player.name}</div>
          <div class="cmp-selected-meta">${player.position} · ${last?.team||"—"} · ${last?.season||"—"}</div>
          <div class="cmp-selected-meta" style="color:var(--muted)">${last?.pts!=null?`${(+last.pts).toFixed(1)} PTS`:"—"} · ${last?.reb!=null?`${(+last.reb).toFixed(1)} REB`:"—"} · ${last?.ast!=null?`${(+last.ast).toFixed(1)} AST`:"—"}</div>
        </div>
        <div class="cmp-eff-badge" style="border-color:${tierColor};color:${tierColor}">
          <div class="cmp-eff-num">${score}</div>
          <div class="cmp-eff-lbl">${tierLabel}</div>
        </div>
        <button class="cmp-remove-btn" data-slot="${slotId}">✕</button>
      </div>`;
  };
  renderSlot(compareA, "cmpSlotA", "compareSearchA");
  renderSlot(compareB, "cmpSlotB", "compareSearchB");
}

document.addEventListener("click", (e) => {
  const btn = e.target.closest(".cmp-remove-btn");
  if (!btn) return;
  if (btn.dataset.slot === "cmpSlotA") { compareA = null; $("#compareSearchA").value = ""; }
  if (btn.dataset.slot === "cmpSlotB") { compareB = null; $("#compareSearchB").value = ""; }
  renderPlayerBar();
  renderComparison();
});

function drawRadar(canvasId, playerA, playerB, la, lb) {
  const canvas = $(`#${canvasId}`);
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const w = canvas.width, h = canvas.height;
  const cx = w/2, cy = h/2, r = Math.min(w,h)*0.36;
  const labels = ["PTS","REB","AST","STL","BLK","3PM","TS%"];
  const maxes  = [35,   15,   12,   3,    3,    5,    85];
  const getVals = (s) => [
    +s.pts||0, +s.reb||0, +s.ast||0, +s.stl||0, +s.blk||0, +s.three||0, +s.ts||0
  ];
  const vA = getVals(la), vB = getVals(lb);
  const n = labels.length;
  const angle = (i) => (Math.PI*2/n)*i - Math.PI/2;

  ctx.clearRect(0,0,w,h);
  // Grid rings
  for (let ring=1; ring<=4; ring++) {
    ctx.beginPath();
    for (let i=0;i<n;i++) {
      const a=angle(i), rr=r*(ring/4);
      i===0?ctx.moveTo(cx+Math.cos(a)*rr,cy+Math.sin(a)*rr):ctx.lineTo(cx+Math.cos(a)*rr,cy+Math.sin(a)*rr);
    }
    ctx.closePath();
    ctx.strokeStyle="rgba(255,255,255,0.07)"; ctx.lineWidth=1; ctx.stroke();
  }
  // Spokes
  for (let i=0;i<n;i++) {
    const a=angle(i);
    ctx.beginPath(); ctx.moveTo(cx,cy); ctx.lineTo(cx+Math.cos(a)*r,cy+Math.sin(a)*r);
    ctx.strokeStyle="rgba(255,255,255,0.07)"; ctx.lineWidth=1; ctx.stroke();
  }
  // Draw polygon helper
  const drawPoly = (vals, color) => {
    ctx.beginPath();
    vals.forEach((v,i) => {
      const pct = Math.min(v/maxes[i],1);
      const a=angle(i);
      const x=cx+Math.cos(a)*r*pct, y=cy+Math.sin(a)*r*pct;
      i===0?ctx.moveTo(x,y):ctx.lineTo(x,y);
    });
    ctx.closePath();
    ctx.fillStyle=color.replace(")",",0.18)").replace("rgb","rgba"); ctx.fill();
    ctx.strokeStyle=color; ctx.lineWidth=2; ctx.stroke();
  };
  drawPoly(vA, playerA.colors[0]||"#5b8af0");
  drawPoly(vB, playerB.colors[0]||"#f97316");
  // Labels
  ctx.fillStyle="rgba(220,232,255,0.65)"; ctx.font="bold 11px system-ui"; ctx.textAlign="center";
  labels.forEach((lbl,i) => {
    const a=angle(i), lx=cx+Math.cos(a)*(r+18), ly=cy+Math.sin(a)*(r+18)+4;
    ctx.fillText(lbl,lx,ly);
  });
}

function renderComparison() {
  const container = $("#comparisonContent");
  renderPlayerBar();
  if (!compareA || !compareB) {
    container.innerHTML = `<div class="compare-placeholder"><div class="compare-placeholder-icon">⚖️</div><div>Search for two players above to compare their stats.</div></div>`;
    return;
  }
  const la = latestSeason(compareA), lb = latestSeason(compareB);
  if (!la || !lb) { container.innerHTML = `<div class="compare-placeholder">No season data found for one of the players.</div>`; return; }

  const statRows = [
    ["Points",    "pts",   false],["Rebounds","reb",  false],["Assists","ast",  false],
    ["3-Pointers","three", false],["Steals",  "stl",  false],["Blocks", "blk",  false],
    ["Turnovers", "tov",   true], ["TS%",     "ts",   false],["USG%",   "usg",  false],
    ["Minutes",   "min",   false],["Games",   "gp",   false],
  ];
  const advRows = [
    ["PER",  "per", false],["BPM",  "net", false],["VORP","vorp",false],
    ["WS",   "ws",  false],["OWS",  "ows", false],["DWS", "dws", false],
  ];

  const fmtV = (v, key) => {
    if (v==null||v===undefined||v==="") return "—";
    if (key==="ts"||key==="usg") return (+v).toFixed(1)+"%";
    return (+v).toFixed(1);
  };

  const colorA = compareA.colors?.[0] || "#5b8af0";
  const colorB = compareB.colors?.[0] || "#f97316";

  const statTableRows = (rows, la, lb) => rows.map(([label,key,lowerBetter]) => {
    const va = parseFloat(la[key]), vb = parseFloat(lb[key]);
    const aWins = !isNaN(va)&&!isNaN(vb)&&(lowerBetter?va<vb:va>vb);
    const bWins = !isNaN(va)&&!isNaN(vb)&&(lowerBetter?vb<va:vb>va);
    const maxes = {pts:40,reb:18,ast:14,three:6,stl:3,blk:3,tov:6,ts:90,usg:45,min:42,gp:82,per:35,net:15,vorp:8,ws:15,ows:10,dws:8,ws48:0.3,obpm:12,dbpm:8};
    const mx = maxes[key]||20;
    const pctA = Math.min(100,Math.max(0,((va||0)/mx)*100));
    const pctB = Math.min(100,Math.max(0,((vb||0)/mx)*100));
    const barA = `<div class="cmp-bar-track"><div class="cmp-bar-fill" style="width:${pctA}%;background:${aWins?colorA:"rgba(255,255,255,0.15)"}"></div></div>`;
    const barB = `<div class="cmp-bar-track"><div class="cmp-bar-fill" style="width:${pctB}%;background:${bWins?colorB:"rgba(255,255,255,0.15)"}"></div></div>`;
    return `<tr class="cmp-stat-row">
      <td class="cmp-td-a ${aWins?"cmp-win":""}">
        <div class="cmp-td-inner-a">
          <span class="cmp-stat-num" style="${aWins?`color:${colorA}`:""}">${fmtV(la[key],key)}</span>
          ${barA}
        </div>
      </td>
      <td class="cmp-td-label">${label}</td>
      <td class="cmp-td-b ${bWins?"cmp-win":""}">
        <div class="cmp-td-inner-b">
          ${barB}
          <span class="cmp-stat-num" style="${bWins?`color:${colorB}`:""}">${fmtV(lb[key],key)}</span>
        </div>
      </td>
    </tr>`;
  }).join("");

  const winCount = (rows, la, lb) => {
    let wA=0,wB=0;
    rows.forEach(([,key,lowerBetter])=>{
      const va=parseFloat(la[key]),vb=parseFloat(lb[key]);
      if(isNaN(va)||isNaN(vb))return;
      if(lowerBetter?va<vb:va>vb)wA++; else if(lowerBetter?vb<va:vb>va)wB++;
    });
    return [wA,wB];
  };

  // Similar players (by closest PTS+REB+AST)
  const similarTo = (player, la) => players
    .filter(p=>p.playerId!==player.playerId&&p.playerId!==compareA.playerId&&p.playerId!==compareB.playerId)
    .map(p=>{const s=latestSeason(p);if(!s)return null;
      const d=Math.abs((+s.pts||0)-(+la.pts||0))+Math.abs((+s.reb||0)-(+la.reb||0))+Math.abs((+s.ast||0)-(+la.ast||0));
      return{p,s,d};}).filter(Boolean).sort((a,b)=>a.d-b.d).slice(0,5);

  const simRowsA = similarTo(compareA, la);
  const simRowsB = similarTo(compareB, lb);

  if (cmpActiveTab === "overview") {
    const [wA, wB] = winCount([...statRows,...advRows], la, lb);
    container.innerHTML = `
      <div class="cmp-overview">
        <!-- Left col: Radar + Advanced -->
        <div style="display:grid;gap:14px">
          <div class="cmp-pcard">
            <div class="cmp-pcard-title">Attribute Radar</div>
            <div class="cmp-radar-legend">
              <span style="color:${colorA}">⬤ ${compareA.name}</span>
              <span style="color:${colorB}">⬤ ${compareB.name}</span>
            </div>
            <canvas id="cmpRadar" width="280" height="280"></canvas>
          </div>
          <div class="cmp-pcard">
            <div class="cmp-pcard-title">Advanced Metrics</div>
            <div class="cmp-table-header">
              <span style="color:${colorA}">${compareA.name.split(" ").pop()}</span>
              <span style="text-align:center"></span>
              <span style="color:${colorB};text-align:right">${compareB.name.split(" ").pop()}</span>
            </div>
            <table class="cmp-table"><tbody>${statTableRows(advRows,la,lb)}</tbody></table>
          </div>
        </div>
        <!-- Right col: Win strip + Key Stats -->
        <div class="cmp-pcard cmp-stats-card">
          <div class="cmp-win-strip">
            <div class="cmp-win-a" style="background:${colorA}22">
              ${cmpAvatar(compareA,28)}
              <span style="color:${colorA}">${wA} wins</span>
            </div>
            <div class="cmp-win-divider"></div>
            <div class="cmp-win-b" style="background:${colorB}22">
              <span style="color:${colorB}">${wB} wins</span>
              ${cmpAvatar(compareB,28)}
            </div>
          </div>
          <div class="cmp-table-header">
            <span style="color:${colorA}">${compareA.name.split(" ").pop()}</span>
            <span style="text-align:center">Stat</span>
            <span style="color:${colorB};text-align:right">${compareB.name.split(" ").pop()}</span>
          </div>
          <table class="cmp-table"><tbody>${statTableRows(statRows,la,lb)}</tbody></table>
        </div>
      </div>`;
    setTimeout(() => drawRadar("cmpRadar", compareA, compareB, la, lb), 0);

  } else if (cmpActiveTab === "advanced") {
    const allAdv = [
      ["PER","per",false],["True Shooting%","ts",false],["Usage%","usg",false],
      ["BPM","net",false],["Offensive BPM","obpm",false],["Defensive BPM","dbpm",false],
      ["VORP","vorp",false],["Win Shares","ws",false],["Off Win Shares","ows",false],
      ["Def Win Shares","dws",false],["WS/48","ws48",false],
    ];
    container.innerHTML = `
      <div class="cmp-adv-page">
        <div class="cmp-pcard cmp-full">
          <div class="cmp-pcard-title">Advanced Metrics · ${la.season}</div>
          <div class="cmp-table-header">
            <span>${compareA.name}</span><span></span><span>${compareB.name}</span>
          </div>
          <table class="cmp-table"><tbody>${statTableRows(allAdv,la,lb)}</tbody></table>
        </div>
      </div>`;

  } else if (cmpActiveTab === "trajectory") {
    container.innerHTML = `
      <div class="cmp-traj-page">
        <div class="cmp-pcard cmp-full">
          <div class="cmp-pcard-title">Career Trajectory Comparison</div>
          <div class="cmp-traj-controls">
            ${["pts","reb","ast","three","stl","blk"].map(m=>`<button class="cmp-traj-btn${m==="pts"?" active":""}" data-metric="${m}">${m.toUpperCase()}</button>`).join("")}
          </div>
          <canvas id="cmpTrajChart" width="1200" height="400"></canvas>
          <div class="cmp-radar-legend" style="margin-top:12px">
            <span style="color:${compareA.colors[0]||"#5b8af0"}">— ${compareA.name}</span>
            <span style="color:${compareB.colors[0]||"#f97316"}">— ${compareB.name}</span>
          </div>
        </div>
      </div>`;
    let trajMetric = "pts";
    const drawTraj = () => {
      const canvas = $("#cmpTrajChart"); if(!canvas) return;
      const ctx=canvas.getContext("2d"), w=canvas.width, h=canvas.height;
      const pad={top:24,right:24,bottom:40,left:48};
      const pw=w-pad.left-pad.right, ph=h-pad.top-pad.bottom;
      const seasA = compareA.seasons.filter(s=>s[trajMetric]!=null).map(s=>({x:+s.age||0,y:+s[trajMetric]}));
      const seasB = compareB.seasons.filter(s=>s[trajMetric]!=null).map(s=>({x:+s.age||0,y:+s[trajMetric]}));
      const allPts = [...seasA,...seasB];
      if (!allPts.length) return;
      const minX=Math.min(...allPts.map(p=>p.x)), maxX=Math.max(...allPts.map(p=>p.x));
      const maxY=Math.max(...allPts.map(p=>p.y))*1.15;
      const px=(v)=>pad.left+((v-minX)/(maxX-minX||1))*pw;
      const py=(v)=>pad.top+ph-(v/maxY)*ph;
      ctx.clearRect(0,0,w,h);
      ctx.fillStyle="#0b1729"; ctx.fillRect(0,0,w,h);
      // Grid
      for(let i=0;i<=4;i++){
        const y=pad.top+(ph/4)*i, val=maxY-(maxY/4)*i;
        ctx.beginPath();ctx.moveTo(pad.left,y);ctx.lineTo(w-pad.right,y);
        ctx.strokeStyle="rgba(255,255,255,0.06)";ctx.lineWidth=1;ctx.stroke();
        ctx.fillStyle="rgba(220,232,255,0.5)";ctx.font="11px system-ui";ctx.textAlign="right";
        ctx.fillText(val.toFixed(1),pad.left-6,y+4);
      }
      // Age labels
      ctx.fillStyle="rgba(220,232,255,0.5)"; ctx.font="11px system-ui"; ctx.textAlign="center";
      for(let age=Math.ceil(minX);age<=maxX;age+=2) ctx.fillText(age,px(age),h-10);
      // Lines
      const drawLine=(seas,color)=>{
        if(!seas.length)return;
        ctx.beginPath();
        seas.forEach((p,i)=>{i===0?ctx.moveTo(px(p.x),py(p.y)):ctx.lineTo(px(p.x),py(p.y));});
        ctx.strokeStyle=color;ctx.lineWidth=2.5;ctx.stroke();
        seas.forEach(p=>{ctx.beginPath();ctx.arc(px(p.x),py(p.y),3.5,0,Math.PI*2);ctx.fillStyle=color;ctx.fill();});
      };
      drawLine(seasA, compareA.colors[0]||"#5b8af0");
      drawLine(seasB, compareB.colors[0]||"#f97316");
    };
    setTimeout(drawTraj,0);
    document.querySelectorAll(".cmp-traj-btn").forEach(btn=>{
      btn.addEventListener("click",()=>{
        document.querySelectorAll(".cmp-traj-btn").forEach(b=>b.classList.remove("active"));
        btn.classList.add("active");
        trajMetric=btn.dataset.metric;
        drawTraj();
      });
    });

  } else if (cmpActiveTab === "similar") {
    const simCard = (player, rows) => `
      <div class="cmp-pcard cmp-sim-card">
        <div class="cmp-pcard-title">Similar to ${player.name}</div>
        <div class="cmp-sim-list">
          ${rows.map((r,i)=>`
            <div class="cmp-sim-row">
              <span class="cmp-sim-rank">${i+1}</span>
              ${cmpAvatar(r.p,36)}
              <div class="cmp-sim-info">
                <div class="cmp-sim-name">${r.p.name}</div>
                <div class="cmp-sim-meta">${r.p.position} · ${r.s.team||"—"}</div>
              </div>
              <div class="cmp-sim-stats">
                <span>${(+r.s.pts||0).toFixed(1)} PTS</span>
                <span>${(+r.s.reb||0).toFixed(1)} REB</span>
                <span>${(+r.s.ast||0).toFixed(1)} AST</span>
              </div>
            </div>`).join("")}
        </div>
      </div>`;
    container.innerHTML = `<div class="cmp-sim-page">${simCard(compareA,simRowsA)}${simCard(compareB,simRowsB)}</div>`;
  }
}

setupCompareSearch("compareSearchA", "compareDropdownA", (p) => { compareA = p; });
setupCompareSearch("compareSearchB", "compareDropdownB", (p) => { compareB = p; });

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

loadDashboard();
navigate("dashboard");
