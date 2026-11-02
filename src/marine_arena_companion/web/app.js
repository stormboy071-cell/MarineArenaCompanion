const PAGE_SIZE = 50;
const MATCH_PAGE_SIZE = 20;
const STATUS_LABELS = {
  ANALYZED: "Ready to explore",
  PROCESSING: "In progress",
  DECODE_FAILED: "Could not analyze",
  UNSUPPORTED_PROTOCOL: "Format not supported",
  NORMALIZATION_FAILED: "Needs review"
};
const MAP_LABELS = {
  MARINE_ARENA_EU: "Marine Arena EU",
  MARINE_ARENA_OTHER: "Other Marine Arena",
  OTHER_MAP: "Other map",
  UNKNOWN: "Unknown map"
};
const state = {
  view: "overview",
  replayOffset: 0,
  replaySearch: "",
  replayStatus: "",
  replayMap: "",
  playerOffset: 0,
  playerSearch: "",
  playerSort: "recent",
  playerSection: "mine",
  userProfile: null,
  reminders: [],
  reminderSearch: "",
  reminderCategory: "",
  reminderEnabled: "",
  activePlayerId: null,
  activePlayerProfile: null,
  playerProfileTab: "performance",
  matchOffset: 0,
  matchSearch: "",
  matchClassification: "",
  matchResult: "",
  activeReplayDetail: null,
  replayReportPlayerId: null,
  replayDetailTab: "performance",
  replayGraph: "army_value",
  recentFilter: "",
  diagnostics: null
};
let statusTimer = null;
let statusBusy = false;
let searchTimer = null;
let toastTimer = null;

async function get(path) {
  const response = await fetch(path);
  const body = await response.json();
  if (!response.ok) throw Error(body.error || `${response.status} ${response.statusText}`);
  return body;
}

async function post(path) {
  try {
    const body = await getPost(path);
    showToast(path.endsWith("/retry")
      ? `${body.accepted} failed replay${body.accepted === 1 ? "" : "s"} queued for retry.`
      : body.started ? "Library scan started." : "A library scan is already running.");
    await refreshStatus();
    if (state.view === "replays") await loadReplays();
  } catch (error) {
    showToast(error.message, true);
  }
}

async function getPost(path) {
  const response = await fetch(path, { method: "POST" });
  const body = await response.json();
  if (!response.ok) throw Error(body.error || `${response.status} ${response.statusText}`);
  return body;
}

function setText(id, value) {
  const element = document.getElementById(id);
  if (element) element.textContent = value == null ? "" : String(value);
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, character => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[character]));
}

function displayName(value) {
  return String(value ?? "Unknown")
    .replace(/&(?:amp|lt|gt|quot|apos|#39|#x27|#\d+|#x[0-9a-f]+);/gi, entity => {
      const named = { "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&apos;": "'", "&#39;": "'", "&#x27;": "'" };
      const lower = entity.toLowerCase();
      if (named[lower]) return named[lower];
      const code = lower.startsWith("&#x")
        ? Number.parseInt(lower.slice(3, -1), 16)
        : Number.parseInt(lower.slice(2, -1), 10);
      return Number.isFinite(code) && code >= 0 && code <= 0x10ffff
        ? String.fromCodePoint(code)
        : entity;
    });
}

function formatDate(value) {
  if (!value) return "Date not recorded";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? String(value)
    : new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date);
}

function formatDuration(value) {
  if (value == null || !Number.isFinite(Number(value))) return "Not recorded";
  const seconds = Math.max(0, Math.round(Number(value)));
  return `${Math.floor(seconds / 60)}m ${String(seconds % 60).padStart(2, "0")}s`;
}

function statusLabel(status) {
  return STATUS_LABELS[status] || String(status || "Unknown");
}

function mapLabel(map, classification) {
  return map || MAP_LABELS[classification] || MAP_LABELS.UNKNOWN;
}

function statusMarkup(status) {
  const tone = status === "ANALYZED" ? "good" : status === "PROCESSING" ? "working" : "attention";
  return `<span class="result-badge ${tone}">${escapeHtml(statusLabel(status))}</span>`;
}

function resultLabel(result) {
  if (result == null || result === "") return "";
  const normalized = String(result).trim().toLowerCase();
  if (["win", "won", "victory"].includes(normalized)) return "Victory";
  if (["loss", "lost", "defeat"].includes(normalized)) return "Defeat";
  if (["draw", "tie"].includes(normalized)) return "Draw";
  return "Result recorded";
}

function showToast(message, isError = false) {
  const toast = document.getElementById("toast");
  toast.textContent = message;
  toast.classList.toggle("error", isError);
  toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toast.hidden = true; }, 5000);
}

async function refreshStatus() {
  if (statusBusy || document.visibilityState === "hidden") return;
  statusBusy = true;
  try {
    const data = await get("/api/status");
    const library = data.library;
    const scan = data.scan;
    const watcher = data.watcher;
    state.userProfile = data.user_profile;
    renderUserProfileStatus(data.user_profile);
    setText("watcher", watcher.error ? "WATCHER ISSUE" : watcher.active ? "WATCHING" : "WATCHER PAUSED");
    document.getElementById("watcher").classList.toggle("offline", Boolean(watcher.error));
    setText("total", library.total);
    setText("analyzed", `${library.analyzed} analyzed`);
    setText("marine", library.marine_arena);
    setText("playerTotal", library.players ?? "—");
    setText("menuReplayCount", library.total);
    setText("menuPlayerCount", library.players ?? "—");
    setText("menuReminderCount", state.reminders.filter(reminder => reminder.enabled).length);
    setText("failed", library.failed);
    const folderName = watcher.folder
      ? String(watcher.folder).replace(/[\\/]+$/, "").split(/[\\/]/).pop()
      : "";
    setText("folder", folderName
      ? `Watching ${folderName} for completed replays`
      : "Choose your replay folder in the desktop app to begin.");
    setText("scanState", scan.active ? "SCANNING" : "UP TO DATE");
    setText("scanTitle", scan.active ? "Indexing replay files" : "Your replay folder");
    setText("scanText", scan.active
      ? `${scan.processed} of ${scan.discovered} replays checked · ${scan.skipped} already recognized · ${scan.marine_arena} Marine Arena · ${scan.failed} need attention`
      : `${library.total} replay${library.total === 1 ? "" : "s"} in your library`);
    const percent = scan.discovered
      ? Math.min(100, Math.round(scan.processed / scan.discovered * 100))
      : 0;
    document.getElementById("progress").style.width = `${percent}%`;
    document.querySelector(".progress-track").setAttribute("aria-valuenow", String(percent));
    setText("scanPercent", scan.active ? `${percent}%` : "");
    setText("latest", library.latest
      ? `${displayName(library.latest.filename)} · ${mapLabel(library.latest.map_name)}`
      : "No replay has been indexed yet.");
    setText("statusUpdated", `Updated ${new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(new Date())}`);
    setText("footerVersion", `Version ${data.application_version}`);
  } catch (error) {
    setText("watcher", "SERVICE UNAVAILABLE");
    setText("statusUpdated", error.message);
    document.getElementById("watcher").classList.add("offline");
  } finally {
    statusBusy = false;
  }
}

function renderUserProfileStatus(profile) {
  const configured = Boolean(profile?.configured);
  const dialog = document.getElementById("profileDialog");
  const input = document.getElementById("battleTagInput");
  document.getElementById("profileCancel").hidden = !configured;
  document.getElementById("profileDialogTitle").textContent = configured
    ? "Update your profile"
    : "Set up your profile";
  document.getElementById("profileSettings").textContent = configured
    ? `My profile · ${profile.display_name}`
    : "Set up profile";
  if (document.activeElement !== input) input.value = profile?.battle_tag || "";
  if (!configured && !dialog.open) dialog.showModal();
}

async function saveUserProfile(event) {
  event.preventDefault();
  const input = document.getElementById("battleTagInput");
  const message = document.getElementById("profileFormMessage");
  message.textContent = "";
  message.classList.remove("error");
  try {
    const response = await fetch("/api/profile", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ battle_tag: input.value })
    });
    const profile = await response.json();
    if (!response.ok) throw Error(profile.error || `${response.status} ${response.statusText}`);
    state.userProfile = profile;
    document.getElementById("profileDialog").close();
    renderUserProfileStatus(profile);
    showToast("Your local player profile is saved.");
    if (state.view === "players" && state.playerSection === "mine") await loadMyProfile();
    if (state.view === "players" && state.playerSection === "opponents") await loadPlayers();
  } catch (error) {
    message.textContent = error.message;
    message.classList.add("error");
  }
}

function navigate(view) {
  const page = document.querySelector(`[data-page="${view}"]`);
  if (!page) return;
  state.view = view;
  document.querySelectorAll("[data-page]").forEach(section => {
    const active = section === page;
    section.hidden = !active;
    section.classList.toggle("active", active);
  });
  document.querySelectorAll(".menu-item[data-view]").forEach(button => {
    const active = button.dataset.view === view;
    button.classList.toggle("active", active);
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  setText("viewHeading", {
    overview: "Overview", replays: "Replay library", players: "Players",
    reminders: "Overlay reminders", diagnostics: "Library health"
  }[view]);
  if (view === "overview") loadRecent();
  if (view === "replays") loadReplays();
  if (view === "players") {
    if (state.playerSection === "mine") loadMyProfile();
    else loadPlayers();
  }
  if (view === "reminders") loadReminders();
  if (view === "diagnostics") loadDiagnostics();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function replayQuery(limit = PAGE_SIZE, offset = 0, extra = {}) {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  Object.entries(extra).forEach(([key, value]) => {
    if (value) params.set(key, value);
  });
  return params;
}

function renderReplayRows(target, items, emptyText, withAction = true) {
  if (!items.length) {
    target.innerHTML = `<tr><td colspan="${withAction ? 5 : 4}" class="empty-cell">${escapeHtml(emptyText)}</td></tr>`;
    return;
  }
  target.innerHTML = items.map(replay => `
    <tr>
      <td><span class="table-primary">${escapeHtml(displayName(replay.filename))}</span></td>
      <td>${escapeHtml(mapLabel(replay.map_name, replay.map_classification))}</td>
      <td>${statusMarkup(replay.decode_status)}</td>
      <td class="muted-cell">${escapeHtml(formatDate(replay.imported_at))}</td>
      ${withAction ? `<td><button class="row-action" type="button" data-open-replay="${Number(replay.id)}">Details <span aria-hidden="true">→</span></button></td>` : ""}
    </tr>`).join("");
}

async function loadRecent() {
  const extra = {};
  if (state.recentFilter === "MARINE_ARENA") extra.classification = "MARINE_ARENA";
  if (state.recentFilter === "ISSUES") extra.status = "NEEDS_ATTENTION";
  try {
    const data = await get(`/api/replays?${replayQuery(5, 0, extra)}`);
    renderReplayRows(document.getElementById("recentReplays"), data.items, "No replays match this filter.", false);
  } catch (error) {
    document.getElementById("recentReplays").innerHTML =
      `<tr><td colspan="4" class="empty-cell">${escapeHtml(error.message)}</td></tr>`;
  }
}

async function loadReplays() {
  const params = replayQuery(PAGE_SIZE, state.replayOffset, {
    q: state.replaySearch,
    status: state.replayStatus,
    classification: state.replayMap
  });
  try {
    const data = await get(`/api/replays?${params}`);
    renderReplayRows(document.getElementById("replayRows"), data.items, "No replays match these filters.");
    setText("replayResultCount", `${data.total} replay${data.total === 1 ? "" : "s"} found`);
    const pageNumber = Math.floor(state.replayOffset / PAGE_SIZE) + 1;
    const pageCount = Math.max(1, Math.ceil(data.total / PAGE_SIZE));
    setText("replayPageLabel", `Page ${pageNumber} of ${pageCount}`);
    document.getElementById("replayPrevious").disabled = state.replayOffset === 0;
    document.getElementById("replayNext").disabled = state.replayOffset + PAGE_SIZE >= data.total;
  } catch (error) {
    document.getElementById("replayRows").innerHTML =
      `<tr><td colspan="5" class="empty-cell">${escapeHtml(error.message)}</td></tr>`;
    setText("replayResultCount", "Unable to load replays");
  }
}

function renderReplayDetail(replay, scroll = true) {
  const target = document.getElementById("replayDetail");
  const players = replay.players.length
    ? replay.players.map(player => {
      const result = resultLabel(player.result);
      const placement = [
        player.slot == null ? "" : `Slot ${player.slot}`,
        player.team == null ? "" : `Team ${player.team}`
      ].filter(Boolean).join(" · ");
      return `<span class="player-tag">${escapeHtml(displayName(player.display_name))}${placement ? ` · ${escapeHtml(placement)}` : ""}${result ? ` · ${escapeHtml(result)}` : ""}</span>`;
    }).join("")
    : '<span class="muted-cell">No player details recorded</span>';
  const activityLabels = [
    ["units_created", "Units deployed"],
    ["units_destroyed", "Units destroyed"],
    ["upgrades_completed", "Upgrades completed"],
    ["abilities_used", "Abilities and commands recorded"]
  ];
  const activity = activityLabels
    .map(([key, label]) => ({ label, count: Number(replay.activity?.[key] || 0) }))
    .filter(item => item.count > 0);
  const activityMarkup = activity.length
    ? activity.map(item => `<article class="activity-card"><b>${item.count.toLocaleString()}</b><span>${escapeHtml(item.label)}</span></article>`).join("")
    : '<p class="empty-state activity-empty">This replay does not contain enough recognized activity for a readable summary yet.</p>';
  const reports = replay.player_reports || [];
  if (!reports.some(report => Number(report.player_id) === state.replayReportPlayerId)) {
    state.replayReportPlayerId = reports.length ? Number(reports[0].player_id) : null;
  }
  const activeReport = reports.find(report => Number(report.player_id) === state.replayReportPlayerId);
  const reportPlayerOptions = replay.players.map(player => `
    <option value="${Number(player.player_id)}"${Number(player.player_id) === state.replayReportPlayerId ? " selected" : ""}>
      ${escapeHtml(player.clan_tag ? `[${player.clan_tag}] ${displayName(player.display_name)}` : displayName(player.display_name))}
    </option>`).join("");
  const reportTabs = [
    ["performance", "Player activity"],
    ["score", "Score samples"],
    ["units", "Units & upgrades"],
    ["build", "Build order"],
    ["graphs", "Graphs"]
  ];
  let reportContent = '<p class="empty-state">Player-attributed match details are not available in this replay.</p>';
  if (activeReport) {
    const performance = activeReport.performance || {};
    const scores = activeReport.score_summary || {};
    const units = activeReport.units || {};
    const metrics = {
      performance: [
        ["APM", performance.actions_per_minute == null ? "—" : Number(performance.actions_per_minute).toLocaleString(), `${Number(performance.recorded_actions || 0)} recorded actions`],
        ["Average unspent resources", performance.average_unspent_resources == null ? "—" : Number(performance.average_unspent_resources).toLocaleString(), "Minerals and gas across replay samples"],
        ["Time supply capped", performance.time_supply_capped_seconds == null ? "—" : formatDuration(performance.time_supply_capped_seconds), "Estimated from recorded supply snapshots"],
        ["Workers created", performance.workers_created == null ? "—" : Number(performance.workers_created).toLocaleString(), "Worker units identified in replay events"]
      ],
      score: [
        ["Resources on hand", scores.resources_on_hand],
        ["Army investment", scores.army_investment],
        ["Economy investment", scores.economy_investment],
        ["Technology investment", scores.technology_investment]
      ].map(([label, value]) => [label, value == null ? "—" : Number(value).toLocaleString(), "Final replay score sample"]),
      units: [
        ["Units trained", units.trained],
        ["Units lost", units.units_lost],
        ["Units killed", units.units_killed],
        ["Structures built", units.structures_built],
        ["Structures razed", units.structures_razed]
      ].map(([label, value]) => [label, value == null ? "—" : Number(value).toLocaleString(), "Recorded owner-attributed events"])
    };
    if (state.replayDetailTab === "performance" || state.replayDetailTab === "score" || state.replayDetailTab === "units") {
      const entries = metrics[state.replayDetailTab];
      reportContent = `<div class="profile-stats">${entries.map(([label, value, note]) => `
        <article class="profile-stat"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong><small>${escapeHtml(note)}</small></article>`).join("")}</div>`;
      if (state.replayDetailTab === "units" && units.top_units?.length) {
        reportContent += `<div class="map-history"><h3>Most trained</h3><div class="map-chip-list">${units.top_units.map(item => `<span class="map-chip">${escapeHtml(item.name)} <b>${Number(item.count).toLocaleString()}</b></span>`).join("")}</div></div>`;
      }
      if (state.replayDetailTab === "units") {
        reportContent += `<div class="map-history"><h3>Upgrades recorded</h3><div class="map-chip-list">${units.top_upgrades?.length
          ? units.top_upgrades.map(item => `<span class="map-chip">${escapeHtml(item.name)} <b>${Number(item.count).toLocaleString()}</b></span>`).join("")
          : '<span class="note">No player-attributed upgrade events recorded</span>'}</div></div>`;
      }
      if (state.replayDetailTab === "performance") {
        reportContent += '<p class="note">Statistics are calculated from replay samples. Missing or unattributed events are shown as unavailable.</p>';
      }
    } else if (state.replayDetailTab === "build") {
      const buildOrder = activeReport.build_order || [];
      reportContent = buildOrder.length
        ? `<div class="build-order-list">${buildOrder.map(item => `
          <div class="build-order-row"><time>${escapeHtml(formatDuration(item.time_seconds))}</time>
            <span class="build-order-type">${escapeHtml(item.category)}</span>
            <strong>${escapeHtml(item.item)}</strong>
            <span class="build-order-supply">${item.supply == null ? "—" : `${Number(item.supply)} supply`}</span></div>`).join("")}</div>
          ${buildOrder.length >= 150 ? '<p class="note">Showing the first 150 recorded build-order events.</p>' : ""}`
        : '<p class="empty-state">No player-attributed unit or upgrade events were recorded.</p>';
    } else {
      const graphOptions = {
        army_value: ["Army investment", "Minerals + gas invested in army"],
        resource_collection_rate: ["Resource collection rate", "Minerals + gas collected per minute"],
        upgrade_spending: ["Technology investment", "Minerals + gas invested in technology"],
        workers_active: ["Workers active", "Workers active at each replay sample"]
      };
      const [graphTitle, graphDescription] = graphOptions[state.replayGraph] || graphOptions.army_value;
      const points = activeReport.graphs?.[state.replayGraph] || [];
      const values = points.map(point => Number(point.value)).filter(Number.isFinite);
      const min = values.length ? Math.min(...values) : 0;
      const max = values.length ? Math.max(...values) : 0;
      const range = max - min || 1;
      const coordinates = points.map((point, index) => {
        const x = points.length < 2 ? 320 : 28 + index / (points.length - 1) * 584;
        const y = 178 - (Number(point.value) - min) / range * 150;
        return `${x.toFixed(1)},${y.toFixed(1)}`;
      }).join(" ");
      reportContent = `
        <div class="graph-controls"><div><strong>${escapeHtml(graphTitle)}</strong><small>${escapeHtml(graphDescription)}</small></div>
          <label class="select-filter"><span class="sr-only">Choose match graph</span><select class="replay-graph">
            ${Object.entries(graphOptions).map(([key, [label]]) => `<option value="${key}"${key === state.replayGraph ? " selected" : ""}>${escapeHtml(label)}</option>`).join("")}
          </select></label>
        </div>
        ${points.length > 1
          ? `<svg class="match-graph" viewBox="0 0 640 220" role="img" aria-label="${escapeHtml(graphTitle)} over match time">
              <line x1="28" y1="178" x2="612" y2="178"></line><line x1="28" y1="28" x2="28" y2="178"></line>
              <polyline points="${coordinates}"></polyline>
              <text x="28" y="204">${escapeHtml(formatDuration(points[0].time))}</text>
              <text x="612" y="204" text-anchor="end">${escapeHtml(formatDuration(points[points.length - 1].time))}</text>
              <text x="32" y="22">${escapeHtml(max.toLocaleString())}</text>
            </svg>`
          : '<p class="empty-state">Not enough replay samples to draw this graph.</p>'}`;
    }
  }
  const reportMarkup = `
    <section class="profile-section match-report">
      <div class="activity-heading"><h3>Match analysis</h3><span>Details from this replay</span></div>
      ${reportPlayerOptions ? `<label class="report-player-select"><span>Player</span><select class="report-player">${reportPlayerOptions}</select></label>` : ""}
      <div class="profile-tabs" role="tablist" aria-label="Match analysis categories">
        ${reportTabs.map(([key, label]) => `<button type="button" class="profile-tab ${state.replayDetailTab === key ? "active" : ""}" role="tab" aria-selected="${state.replayDetailTab === key}" data-match-tab="${key}">${label}</button>`).join("")}
      </div>
      <div class="report-content">${reportContent}</div>
    </section>`;
  const detailMeta = [
    ["Map", mapLabel(replay.map_name, replay.map_classification)],
    ["Game length", formatDuration(replay.duration_seconds)],
    ["Players", replay.players.length],
    ["Analysis", statusLabel(replay.decode_status)]
  ];
  target.innerHTML = `
    <div class="detail-heading"><div><p class="eyebrow">REPLAY DETAILS</p><h2>${escapeHtml(displayName(replay.filename))}</h2></div><button type="button" class="icon-button" data-close-detail aria-label="Close replay details">×</button></div>
    <div class="detail-meta">${detailMeta.map(([label, value]) => `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`).join("")}</div>
    <div class="detail-section"><h3>Players in this game</h3><div class="tag-list">${players}</div></div>
    <div class="detail-section"><div class="activity-heading"><h3>Match activity</h3><span>Whole-match counts</span></div><div class="activity-grid">${activityMarkup}</div></div>
    <p class="note">These are match-wide recorded events, not player-specific performance ratings or coaching advice.</p>`;
  target.innerHTML += reportMarkup;
  target.hidden = false;
  if (scroll) target.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function showReplay(id) {
  navigate("replays");
  const target = document.getElementById("replayDetail");
  target.hidden = false;
  target.innerHTML = '<div class="empty-state">Loading replay details…</div>';
  try {
    state.activeReplayDetail = await get(`/api/replays/${id}`);
    state.replayReportPlayerId = null;
    state.replayDetailTab = "performance";
    renderReplayDetail(state.activeReplayDetail);
  } catch (error) {
    target.innerHTML = `<div class="detail-heading"><h2>Replay details unavailable</h2><button type="button" class="icon-button" data-close-detail aria-label="Close replay details">×</button></div><p class="inline-warning">${escapeHtml(error.message)}</p>`;
  }
}

async function loadPlayers() {
  const params = replayQuery(PAGE_SIZE, state.playerOffset, { q: state.playerSearch, sort: state.playerSort });
  try {
    const data = await get(`/api/opponents?${params}`);
    const target = document.getElementById("playerRows");
    target.innerHTML = data.items.length
      ? data.items.map(player => `
        <button class="player-row ${Number(player.id) === state.activePlayerId ? "selected" : ""}" type="button" data-open-player="${Number(player.id)}">
          <span class="player-avatar" aria-hidden="true">${escapeHtml(displayName(player.display_name).trim().slice(0, 1).toUpperCase() || "?")}</span>
          <span class="player-row-copy"><strong>${escapeHtml(displayName(player.display_name))}</strong><small>${player.clan_tag ? `Clan · ${escapeHtml(player.clan_tag)} · ` : ""}Last seen ${escapeHtml(formatDate(player.latest_encounter))}</small></span>
          <span class="game-count">${Number(player.games)}<small>${Number(player.games) === 1 ? "game" : "games"}</small></span>
        </button>`).join("")
      : '<div class="empty-state">No players match this search.</div>';
    setText("playerResultCount", `${data.total} player${data.total === 1 ? "" : "s"}`);
    const pageNumber = Math.floor(state.playerOffset / PAGE_SIZE) + 1;
    const pageCount = Math.max(1, Math.ceil(data.total / PAGE_SIZE));
    setText("playerPageLabel", `${pageNumber} / ${pageCount}`);
    document.getElementById("playerPrevious").disabled = state.playerOffset === 0;
    document.getElementById("playerNext").disabled = state.playerOffset + PAGE_SIZE >= data.total;
    if (state.activePlayerId && !data.items.some(player => Number(player.id) === state.activePlayerId)) {
      state.activePlayerId = null;
      document.getElementById("playerDetail").innerHTML = '<div class="empty-state">Select a player to view their replay history.</div>';
    }
  } catch (error) {
    document.getElementById("playerRows").innerHTML = `<div class="empty-state">${escapeHtml(error.message)}</div>`;
  }
}

async function loadMyProfile() {
  if (!state.userProfile?.configured) return;
  const params = replayQuery(MATCH_PAGE_SIZE, state.matchOffset, {
    q: state.matchSearch,
    classification: state.matchClassification,
    result: state.matchResult
  });
  const target = document.getElementById("myProfilePanel");
  try {
    const data = await get(`/api/profile?${params}`);
    state.activePlayerProfile = data;
    renderPlayerProfile(data, target);
  } catch (error) {
    target.innerHTML = `<p class="inline-warning">${escapeHtml(error.message)}</p>`;
  }
}

async function showPlayer(id) {
  state.activePlayerId = id;
  state.activePlayerProfile = null;
  state.playerProfileTab = "performance";
  state.matchOffset = 0;
  state.matchSearch = "";
  state.matchClassification = "";
  state.matchResult = "";
  await loadPlayers();
  await loadPlayerDetail();
}

async function loadPlayerDetail() {
  if (!state.activePlayerId) return;
  const params = replayQuery(MATCH_PAGE_SIZE, state.matchOffset, {
    q: state.matchSearch,
    classification: state.matchClassification,
    result: state.matchResult
  });
  const target = document.getElementById("playerDetail");
  try {
    const data = await get(`/api/opponents/${state.activePlayerId}?${params}`);
    state.activePlayerProfile = data;
    renderPlayerProfile(data);
  } catch (error) {
    target.innerHTML = `<p class="inline-warning">${escapeHtml(error.message)}</p>`;
  }
}

function renderPlayerProfile(data, target = document.getElementById("playerDetail")) {
    const matches = data.matches.length
      ? data.matches.map(match => `
        <div class="match-row">
          <div><button type="button" class="match-replay-link" data-open-replay="${Number(match.replay_id)}">${escapeHtml(displayName(match.filename))}</button><small>${escapeHtml(formatDate(match.imported_at))}</small></div>
          <span>${escapeHtml(mapLabel(match.map_name, match.map_classification))}</span>
          <span class="match-result">${escapeHtml(resultLabel(match.result) || "Result not recorded")}</span>
        </div>`).join("")
      : '<div class="empty-state">No matches found for these filters.</div>';
    const recordedResults = new Set([
      ...data.matches.map(match => match.result).filter(result => result != null).map(String),
      ...(state.matchResult ? [state.matchResult] : [])
    ]);
    const resultOptions = new Map();
    recordedResults.forEach(result => {
      const label = resultLabel(result);
      if (label && !resultOptions.has(label)) resultOptions.set(label, result);
    });
    const profile = data.profile || {};
    const winRate = profile.win_rate == null ? "—" : `${Number(profile.win_rate).toLocaleString()}%`;
    const apm = profile.average_apm == null ? "—" : Number(profile.average_apm).toLocaleString();
    const unitsLost = profile.units_lost == null ? "—" : Number(profile.units_lost).toLocaleString();
    const outcomeSummary = [
      [Number(profile.wins || 0), "win", "wins"],
      [Number(profile.losses || 0), "loss", "losses"],
      [Number(profile.draws || 0), "draw", "draws"]
    ].map(([count, singular, plural]) => `${count} ${count === 1 ? singular : plural}`).join(" · ");
    const historyNames = (data.name_history || []).map(alias => `
      <span class="name-history-item">
        ${alias.clan_tag ? `<span class="clan-badge">${escapeHtml(alias.clan_tag)}</span>` : ""}
        <span>${escapeHtml(displayName(alias.display_name))}</span>
      </span>`).join("");
    const tendencyCards = [
      ["Most deployed", profile.top_units || []],
      ["Upgrades recorded", profile.top_upgrades || []]
    ].map(([title, entries]) => `
      <article class="tendency-card"><h3>${title}</h3>
        ${entries.length
          ? `<ul>${entries.map(entry => `<li><span>${escapeHtml(entry.name)}</span><b>${Number(entry.count).toLocaleString()}</b></li>`).join("")}</ul>`
          : '<p class="note">No data recorded</p>'}
      </article>`).join("");
    const mapCards = (profile.map_history || []).map(map => `
      <span class="map-chip">${escapeHtml(map.map_name)} <b>${Number(map.games).toLocaleString()}</b></span>`).join("");
    const performanceMarkup = `
      <section class="profile-section"><div class="activity-heading"><h3>Performance snapshot</h3></div>
        <div class="profile-stats">
          <article class="profile-stat"><span>Win rate</span><strong>${winRate}</strong><small>${outcomeSummary}</small></article>
          <article class="profile-stat"><span>Recorded APM</span><strong>${apm}</strong><small>${Number(profile.apm_games || 0)} game${Number(profile.apm_games || 0) === 1 ? "" : "s"} with linked actions</small></article>
          <article class="profile-stat"><span>Recorded results</span><strong>${Number(profile.recorded_results || 0)}</strong><small>of ${Number(profile.games || data.all_time_games)} games</small></article>
        </div>
      </section>`;
    const unitsMarkup = `
      <section class="profile-section"><div class="activity-heading"><h3>Units &amp; upgrades</h3><span>Aggregated from attributed replay events</span></div>
        <div class="profile-stats">
          <article class="profile-stat"><span>Tracked units lost</span><strong>${unitsLost}</strong><small>${Number(profile.unit_loss_games || 0)} game${Number(profile.unit_loss_games || 0) === 1 ? "" : "s"} with tracked losses</small></article>
        </div>
        <div class="tendency-grid">${tendencyCards}</div>
      </section>`;
    const mapsMarkup = `
      <section class="profile-section"><div class="activity-heading"><h3>Recorded results</h3></div>
        <div class="profile-stats">
          <article class="profile-stat"><span>Wins</span><strong>${Number(profile.wins || 0)}</strong></article>
          <article class="profile-stat"><span>Losses</span><strong>${Number(profile.losses || 0)}</strong></article>
          <article class="profile-stat"><span>Draws</span><strong>${Number(profile.draws || 0)}</strong></article>
        </div>
        <div class="map-history"><h3>Maps played</h3><div class="map-chip-list">${mapCards || '<span class="note">No map data recorded</span>'}</div></div>
        ${historyNames ? `<div class="name-history"><h3>Name history</h3><div class="name-history-list">${historyNames}</div></div>` : ""}
      </section>`;
    const matchHistoryMarkup = `
      <div class="match-filters">
        <label class="search-box"><span aria-hidden="true">⌕</span><input class="match-search" type="search" maxlength="200" value="${escapeHtml(state.matchSearch)}" placeholder="Search replay or map…" autocomplete="off"></label>
        <label class="select-filter"><span class="sr-only">Filter matches by map</span><select class="match-classification">
          <option value="">All maps</option><option value="MARINE_ARENA">Marine Arena</option><option value="OTHER_MAP">Other maps</option><option value="UNKNOWN">Unknown map</option>
        </select></label>
        <label class="select-filter"><span class="sr-only">Filter by recorded result</span><select class="match-result-filter">
          <option value="">All results</option>${[...resultOptions].map(([label, result]) => `<option value="${escapeHtml(result)}"${state.matchResult === result ? " selected" : ""}>${escapeHtml(label)}</option>`).join("")}
        </select></label>
      </div>
      <div class="match-list">${matches}</div>
      <div class="list-footer"><span>${data.matches_total} matching game${data.matches_total === 1 ? "" : "s"} · ${data.all_time_games} total</span><div class="pager"><button type="button" class="pager-button" data-match-page="-1" ${state.matchOffset === 0 ? "disabled" : ""}>←</button><span>Page ${Math.floor(state.matchOffset / MATCH_PAGE_SIZE) + 1} of ${Math.max(1, Math.ceil(data.matches_total / MATCH_PAGE_SIZE))}</span><button type="button" class="pager-button" data-match-page="1" ${state.matchOffset + MATCH_PAGE_SIZE >= data.matches_total ? "disabled" : ""}>→</button></div></div>`;
    const profileTabs = [
      ["performance", "Performance"],
      ["units", "Units & upgrades"],
      ["maps", "Maps & results"],
      ["matches", "Match history"],
    ];
    const activeTabContent = data.battle_tag && !data.all_time_games
      ? `<section class="profile-section unavailable-profile-category"><h3>No matching replay player yet</h3><p>No participant named “${escapeHtml(displayName(data.display_name))}” was found in the analyzed replay library. The Battle.net numeric suffix is not available in most replay participant records, so matching uses the recorded player name only.</p></section>`
      : state.playerProfileTab === "units"
        ? unitsMarkup
        : state.playerProfileTab === "maps"
          ? mapsMarkup
          : state.playerProfileTab === "matches"
            ? matchHistoryMarkup
            : performanceMarkup;
    target.innerHTML = `
      <div class="detail-heading player-profile-heading"><div><p class="eyebrow">${data.battle_tag ? "MY PROFILE · REPLAY MATCH" : "PLAYER PROFILE"}</p><h2>${escapeHtml(displayName(data.display_name))}</h2><p class="intro">${data.battle_tag ? `${escapeHtml(data.battle_tag)} · ` : data.clan_tag ? `<span class="clan-badge">${escapeHtml(data.clan_tag)}</span> · ` : ""}${data.all_time_games} recorded game${data.all_time_games === 1 ? "" : "s"}</p></div>${data.battle_tag ? '<button type="button" class="secondary-button" data-action="edit-profile">Edit profile</button>' : ""}</div>
      <div class="profile-tabs" role="tablist" aria-label="Player profile categories">
        ${profileTabs.map(([key, label]) => `<button type="button" class="profile-tab ${state.playerProfileTab === key ? "active" : ""}" role="tab" aria-selected="${state.playerProfileTab === key}" data-profile-tab="${key}">${label}</button>`).join("")}
      </div>
      <div class="player-profile-content">${activeTabContent}</div>`;
    const matchClassification = target.querySelector(".match-classification");
    if (matchClassification) matchClassification.value = state.matchClassification;
}

function secondsToTime(seconds) {
  const value = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(value / 3600);
  const minutes = Math.floor((value % 3600) / 60);
  const remainder = value % 60;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`;
}

function timeToSeconds(value) {
  const parts = value.split(":").map(Number);
  if (parts.length !== 3 || parts.some(part => !Number.isSafeInteger(part) || part < 0)) return null;
  const [hours, minutes, seconds] = parts;
  if (minutes > 59 || seconds > 59) return null;
  const elapsed = hours * 3600 + minutes * 60 + seconds;
  return elapsed <= 2147483647 ? elapsed : null;
}

function renderReminders() {
  const query = state.reminderSearch.toLowerCase();
  const filtered = state.reminders.filter(reminder => {
    const text = `${reminder.title} ${reminder.item_label} ${reminder.note}`.toLowerCase();
    return (!query || text.includes(query))
      && (!state.reminderCategory || reminder.category === state.reminderCategory)
      && (!state.reminderEnabled
        || (state.reminderEnabled === "enabled" ? reminder.enabled : !reminder.enabled));
  });
  const categoryNames = {
    unit_combo: "Unit combination",
    hero: "Hero",
    elite: "Elite",
    custom: "Custom"
  };
  const target = document.getElementById("reminderRows");
  target.innerHTML = filtered.length
    ? filtered.map(reminder => `
      <article class="reminder-card ${reminder.enabled ? "" : "disabled"}">
        <div class="reminder-card-main">
          <div class="reminder-card-top"><span class="reminder-time">${secondsToTime(reminder.at_seconds)}</span><span class="reminder-kind">${escapeHtml(categoryNames[reminder.category] || "Custom")}</span></div>
          ${reminder.item_label ? `<div class="reminder-item-label">${escapeHtml(reminder.item_label)}</div>` : ""}
          <h3>${escapeHtml(reminder.title)}</h3>
          ${reminder.note ? `<p class="reminder-note">${escapeHtml(reminder.note)}</p>` : ""}
        </div>
        <div class="reminder-actions">
          <label class="check-field" title="${reminder.enabled ? "Disable" : "Enable"} reminder"><input type="checkbox" data-toggle-reminder="${Number(reminder.id)}" ${reminder.enabled ? "checked" : ""} aria-label="${reminder.enabled ? "Disable" : "Enable"} ${escapeHtml(reminder.title)}"></label>
          <button type="button" class="reminder-action" data-edit-reminder="${Number(reminder.id)}">Edit</button>
          <button type="button" class="reminder-action delete" data-delete-reminder="${Number(reminder.id)}">Delete</button>
        </div>
      </article>`).join("")
    : '<div class="empty-state">No reminders match these filters. Add a cue with the form or change the filters.</div>';
  const activeCount = state.reminders.filter(reminder => reminder.enabled).length;
  setText("reminderCount", `${activeCount} ACTIVE · ${state.reminders.length} TOTAL`);
  setText("menuReminderCount", activeCount);
}

async function loadReminders() {
  try {
    const data = await get("/api/reminders");
    state.reminders = data.items;
    renderReminders();
  } catch (error) {
    document.getElementById("reminderRows").innerHTML = `<div class="inline-warning">${escapeHtml(error.message)}</div>`;
  }
}

function resetReminderForm() {
  document.getElementById("reminderForm").reset();
  document.getElementById("reminderId").value = "";
  document.getElementById("reminderTime").value = "00:30:00";
  document.getElementById("reminderEnabled").checked = true;
  document.getElementById("reminderFormHeading").textContent = "Create a reminder";
  document.getElementById("reminderFormEyebrow").textContent = "NEW OVERLAY CUE";
  document.getElementById("reminderSubmit").textContent = "Save reminder";
  document.getElementById("reminderCancel").hidden = true;
  const message = document.getElementById("reminderFormMessage");
  message.textContent = "";
  message.classList.remove("error");
}

function editReminder(id) {
  const reminder = state.reminders.find(item => item.id === id);
  if (!reminder) return;
  document.getElementById("reminderId").value = String(reminder.id);
  document.getElementById("reminderTitle").value = reminder.title;
  document.getElementById("reminderTime").value = secondsToTime(reminder.at_seconds);
  document.getElementById("reminderCategory").value = reminder.category;
  document.getElementById("reminderItem").value = reminder.item_label;
  document.getElementById("reminderNote").value = reminder.note;
  document.getElementById("reminderEnabled").checked = Boolean(reminder.enabled);
  document.getElementById("reminderFormHeading").textContent = "Edit reminder";
  document.getElementById("reminderFormEyebrow").textContent = "UPDATE OVERLAY CUE";
  document.getElementById("reminderSubmit").textContent = "Save changes";
  document.getElementById("reminderCancel").hidden = false;
  document.getElementById("reminderFormMessage").textContent = "";
  document.getElementById("reminderForm").scrollIntoView({ behavior: "smooth", block: "start" });
}

async function saveReminder(event) {
  event.preventDefault();
  const message = document.getElementById("reminderFormMessage");
  const atSeconds = timeToSeconds(document.getElementById("reminderTime").value);
  if (atSeconds == null) {
    message.textContent = "Enter a valid match time.";
    message.classList.add("error");
    return;
  }
  const body = {
    title: document.getElementById("reminderTitle").value,
    at_seconds: atSeconds,
    category: document.getElementById("reminderCategory").value,
    item_label: document.getElementById("reminderItem").value,
    note: document.getElementById("reminderNote").value,
    enabled: document.getElementById("reminderEnabled").checked
  };
  const reminderId = document.getElementById("reminderId").value;
  try {
    const response = await fetch(reminderId ? `/api/reminders/${Number(reminderId)}` : "/api/reminders", {
      method: reminderId ? "PUT" : "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Unable to save reminder");
    resetReminderForm();
    await loadReminders();
    showToast(reminderId ? "Reminder updated." : "Reminder added to your overlay.");
  } catch (error) {
    message.textContent = error.message;
    message.classList.add("error");
  }
}

async function toggleReminder(id, enabled) {
  const reminder = state.reminders.find(item => item.id === id);
  if (!reminder) return;
  try {
    const response = await fetch(`/api/reminders/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...reminder, enabled: Boolean(enabled) })
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || "Unable to update reminder");
    await loadReminders();
  } catch (error) {
    showToast(error.message, true);
    await loadReminders();
  }
}

async function deleteReminder(id) {
  const reminder = state.reminders.find(item => item.id === id);
  if (!reminder || !window.confirm(`Delete “${reminder.title}”?`)) return;
  try {
    const response = await fetch(`/api/reminders/${id}`, { method: "DELETE" });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || "Unable to delete reminder");
    if (Number(document.getElementById("reminderId").value) === id) resetReminderForm();
    await loadReminders();
    showToast("Reminder deleted.");
  } catch (error) {
    showToast(error.message, true);
  }
}

function renderHealthSummary(data) {
  const library = data.library;
  setText("healthTotal", Number(library.total).toLocaleString());
  setText("healthAnalyzed", Number(library.analyzed).toLocaleString());
  setText("healthFailed", Number(library.failed).toLocaleString());
  const target = document.getElementById("healthIssues");
  target.innerHTML = data.issues.length
    ? data.issues.map(issue => `
      <article class="health-issue ${issue.severity === "warning" ? "warning" : ""}">
        <span class="health-issue-mark" aria-hidden="true">${issue.severity === "warning" ? "!" : "i"}</span>
        <div><strong>${escapeHtml(issue.title)}</strong><p>${escapeHtml(issue.action)}</p></div>
        <span class="health-issue-count">${Number(issue.count).toLocaleString()} ${Number(issue.count) === 1 ? "replay" : "replays"}</span>
      </article>`).join("")
    : `<div class="health-good"><strong>${library.total ? "Everything looks good" : "Your library is ready"}</strong><span>${library.total ? "No replay processing issues were found." : "Choose Scan Library to add your first replay."}</span></div>`;
}

async function loadDiagnostics(force = false) {
  if (state.diagnostics && !force) {
    return;
  }
  try {
    const data = await get("/api/health");
    state.diagnostics = data;
    renderHealthSummary(data);
  } catch {
    document.getElementById("healthIssues").innerHTML =
      '<div class="empty-state">App health is temporarily unavailable. Try refreshing in a moment.</div>';
  }
}

function debounce(callback, delay = 300) {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(callback, delay);
}

document.addEventListener("click", event => {
  const target = event.target.closest("[data-view], [data-action], [data-open-replay], [data-open-player], [data-close-detail], [data-match-page], [data-match-tab], [data-profile-tab], [data-player-section], [data-recent-filter], [data-edit-reminder], [data-delete-reminder]");
  if (!target) return;
  if (target.dataset.view) {
    event.preventDefault();
    if (target.dataset.statusFilter) {
      state.replayStatus = target.dataset.statusFilter;
      document.getElementById("replayStatus").value = target.dataset.statusFilter;
      state.replayOffset = 0;
    }
    navigate(target.dataset.view);
  } else if (target.dataset.action === "scan") {
    post("/api/scan");
  } else if (target.dataset.action === "retry") {
    post("/api/retry");
  } else if (target.dataset.action === "refresh") {
    refreshStatus();
    if (state.view === "replays") loadReplays();
    if (state.view === "players") {
      if (state.playerSection === "mine") loadMyProfile();
      else loadPlayers();
    }
  } else if (target.dataset.action === "edit-profile") {
    document.getElementById("profileFormMessage").textContent = "";
    document.getElementById("profileDialog").showModal();
  } else if (target.dataset.action === "refresh-diagnostics") {
    loadDiagnostics(true);
  } else if (target.dataset.action === "open-overlay-preview") {
    window.open("/overlay", "_blank", "noopener");
  } else if (target.dataset.editReminder) {
    editReminder(Number(target.dataset.editReminder));
  } else if (target.dataset.deleteReminder) {
    deleteReminder(Number(target.dataset.deleteReminder));
  } else if (target.dataset.openReplay) {
    showReplay(Number(target.dataset.openReplay));
  } else if (target.dataset.openPlayer) {
    showPlayer(Number(target.dataset.openPlayer));
  } else if (target.dataset.profileTab) {
    state.playerProfileTab = target.dataset.profileTab;
    if (state.activePlayerProfile) {
      const profileTarget = state.playerSection === "mine"
        ? document.getElementById("myProfilePanel")
        : document.getElementById("playerDetail");
      renderPlayerProfile(state.activePlayerProfile, profileTarget);
    }
  } else if (target.dataset.playerSection) {
    state.playerSection = target.dataset.playerSection;
    document.querySelectorAll("[data-player-section]").forEach(button => {
      const active = button.dataset.playerSection === state.playerSection;
      button.classList.toggle("active", active);
      button.setAttribute("aria-selected", String(active));
    });
    document.getElementById("myProfilePanel").hidden = state.playerSection !== "mine";
    document.getElementById("opponentsSection").hidden = state.playerSection !== "opponents";
    if (state.playerSection === "mine") loadMyProfile();
    else loadPlayers();
  } else if (target.dataset.matchTab) {
    state.replayDetailTab = target.dataset.matchTab;
    if (state.activeReplayDetail) renderReplayDetail(state.activeReplayDetail, false);
  } else if (target.hasAttribute("data-close-detail")) {
    document.getElementById("replayDetail").hidden = true;
  } else if (target.dataset.matchPage) {
    state.matchOffset = Math.max(0, state.matchOffset + Number(target.dataset.matchPage) * MATCH_PAGE_SIZE);
    if (state.playerSection === "mine") loadMyProfile();
    else loadPlayerDetail();
  } else if (target.hasAttribute("data-recent-filter")) {
    state.recentFilter = target.dataset.recentFilter;
    document.querySelectorAll("[data-recent-filter]").forEach(button => button.classList.toggle("active", button === target));
    loadRecent();
  }
});

document.getElementById("replaySearch").addEventListener("input", event => {
  state.replaySearch = event.target.value.trim();
  state.replayOffset = 0;
  debounce(loadReplays);
});
document.getElementById("replayStatus").addEventListener("change", event => {
  state.replayStatus = event.target.value;
  state.replayOffset = 0;
  loadReplays();
});
document.getElementById("replayMap").addEventListener("change", event => {
  state.replayMap = event.target.value;
  state.replayOffset = 0;
  loadReplays();
});
document.getElementById("replayPrevious").addEventListener("click", () => {
  state.replayOffset = Math.max(0, state.replayOffset - PAGE_SIZE);
  loadReplays();
});
document.getElementById("replayNext").addEventListener("click", () => {
  state.replayOffset += PAGE_SIZE;
  loadReplays();
});
document.getElementById("playerSearch").addEventListener("input", event => {
  state.playerSearch = event.target.value.trim();
  state.playerOffset = 0;
  debounce(loadPlayers);
});
document.getElementById("playerSort").addEventListener("change", event => {
  state.playerSort = event.target.value;
  state.playerOffset = 0;
  loadPlayers();
});
document.getElementById("playerPrevious").addEventListener("click", () => {
  state.playerOffset = Math.max(0, state.playerOffset - PAGE_SIZE);
  loadPlayers();
});
document.getElementById("playerNext").addEventListener("click", () => {
  state.playerOffset += PAGE_SIZE;
  loadPlayers();
});
document.getElementById("reminderForm").addEventListener("submit", saveReminder);
document.getElementById("reminderCancel").addEventListener("click", resetReminderForm);
document.getElementById("reminderSearch").addEventListener("input", event => {
  state.reminderSearch = event.target.value.trim();
  renderReminders();
});
document.getElementById("reminderFilter").addEventListener("change", event => {
  state.reminderCategory = event.target.value;
  renderReminders();
});
document.getElementById("reminderEnabledFilter").addEventListener("change", event => {
  state.reminderEnabled = event.target.value;
  renderReminders();
});
document.getElementById("reminderCategory").addEventListener("change", event => {
  const examples = {
    unit_combo: "e.g. 2 Marines + 1 Medic",
    hero: "e.g. Raynor",
    elite: "e.g. Elite wave",
    custom: "Optional label"
  };
  document.getElementById("reminderItem").placeholder = examples[event.target.value] || "Optional label";
});
document.getElementById("reminderRows").addEventListener("change", event => {
  const toggle = event.target.closest("[data-toggle-reminder]");
  if (toggle) toggleReminder(Number(toggle.dataset.toggleReminder), toggle.checked);
});
document.getElementById("playerDetail").addEventListener("input", event => {
  if (!event.target.matches(".match-search")) return;
  state.matchSearch = event.target.value.trim();
  state.matchOffset = 0;
  debounce(loadPlayerDetail);
});
document.getElementById("myProfilePanel").addEventListener("input", event => {
  if (!event.target.matches(".match-search")) return;
  state.matchSearch = event.target.value.trim();
  state.matchOffset = 0;
  debounce(loadMyProfile);
});
document.getElementById("playerDetail").addEventListener("change", event => {
  if (event.target.matches(".match-classification")) state.matchClassification = event.target.value;
  if (event.target.matches(".match-result-filter")) state.matchResult = event.target.value;
  if (event.target.matches(".match-classification, .match-result-filter")) {
    state.matchOffset = 0;
    loadPlayerDetail();
  }
});
document.getElementById("myProfilePanel").addEventListener("change", event => {
  if (event.target.matches(".match-classification")) state.matchClassification = event.target.value;
  if (event.target.matches(".match-result-filter")) state.matchResult = event.target.value;
  if (event.target.matches(".match-classification, .match-result-filter")) {
    state.matchOffset = 0;
    loadMyProfile();
  }
});
document.getElementById("profileForm").addEventListener("submit", saveUserProfile);
document.getElementById("profileCancel").addEventListener("click", () => {
  document.getElementById("profileDialog").close();
});
document.getElementById("profileDialog").addEventListener("cancel", event => {
  if (!state.userProfile?.configured) event.preventDefault();
});
document.getElementById("replayDetail").addEventListener("change", event => {
  if (event.target.matches(".report-player")) {
    state.replayReportPlayerId = Number(event.target.value);
    if (state.activeReplayDetail) renderReplayDetail(state.activeReplayDetail, false);
  }
  if (event.target.matches(".replay-graph")) {
    state.replayGraph = event.target.value;
    if (state.activeReplayDetail) renderReplayDetail(state.activeReplayDetail, false);
  }
});
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") refreshStatus();
});

refreshStatus();
loadRecent();
loadReminders();
if (location.hash === "#reminders") navigate("reminders");
statusTimer = setInterval(() => {
  if (document.visibilityState === "visible") refreshStatus();
}, 10000);
