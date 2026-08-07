const $ = (selector) => document.querySelector(selector);
const originalText = new WeakMap();
const originalAttributes = new WeakMap();
let localeCatalog = {};
let locale = "en";
let csrf = "";
let role = "";
let settingsSchema = {};
let settingValues = {};
let settingBaseline = {};
let settingEffective = {};
let settingsPresets = {};
let settingsPending = {};
let serverConfigSchema = { engine: {}, launch: {} };
let serverConfigValues = { engine: {}, launch: {} };
let serverConfigBaseline = { engine: {}, launch: {} };
let serverConfigPresets = {};
let modConfigStatus = null;
let modLifecycleStatus = null;
let modLifecyclePlan = null;
let modConfigValues = {};
let modConfigBaseline = {};
let modConfigMotdBaseline = [];
let logLoading = false;
let whitelistEntries = [];
let saveIntelligenceCache = null;
let gameDataCache = null;
let mapLocationsCache = [];
let pickedCoordinate = null;
let saveMapBounds = null;
let playerActionCapabilities = null;
let playerActionPlan = null;
let saveRepairPlan = null;
let selectedFilePath = "";

const esc = (value) => String(value).replace(/[&<>"']/g, (char) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
})[char]);
const duration = (seconds) => seconds > 86400
  ? `${Math.floor(seconds / 86400)}d ${Math.floor(seconds % 86400 / 3600)}h`
  : `${(seconds / 3600).toFixed(1)}h`;
const bytes = (value) => value >= 1073741824
  ? `${(value / 1073741824).toFixed(1)} GiB`
  : `${(value / 1048576).toFixed(1)} MiB`;
function localeChoice() {
  const requested = new URLSearchParams(window.location.search).get("lang");
  if (["en", "zh-Hans", "ja"].includes(requested)) return requested;
  const saved = localStorage.getItem("palworld-locale");
  if (["en", "zh-Hans", "ja"].includes(saved)) return saved;
  const preferred = (navigator.languages || [navigator.language || "en"]).find((item) => /^zh|^ja/i.test(item));
  return /^zh/i.test(preferred || "") ? "zh-Hans" : (/^ja/i.test(preferred || "") ? "ja" : "en");
}

function translated(value) {
  return locale === "en" ? value : (localeCatalog[locale]?.[value] || value);
}

function translateTree(root = document.body) {
  if (!root || root.closest?.(".locale-picker")) return;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  nodes.forEach((node) => {
    if (node.parentElement?.closest(".locale-picker,script,style")) return;
    if (!originalText.has(node)) originalText.set(node, node.nodeValue);
    const source = originalText.get(node); const value = source.trim();
    const target = value ? source.replace(value, translated(value)) : source;
    if (node.nodeValue !== target) node.nodeValue = target;
  });
  const elements = [root, ...(root.querySelectorAll?.("[placeholder],[aria-label],[title]") || [])];
  elements.forEach((element) => {
    if (!element?.getAttribute || element.closest(".locale-picker")) return;
    if (!originalAttributes.has(element)) originalAttributes.set(element, {});
    const originals = originalAttributes.get(element);
    ["placeholder", "aria-label", "title"].forEach((name) => {
      if (!element.hasAttribute(name)) return;
      if (!(name in originals)) originals[name] = element.getAttribute(name);
      element.setAttribute(name, translated(originals[name]));
    });
  });
  document.title = translated("Palworld operations");
}

function setLocale(next) {
  locale = ["en", "zh-Hans", "ja"].includes(next) ? next : "en";
  localStorage.setItem("palworld-locale", locale);
  document.documentElement.lang = locale;
  $("#locale").value = locale;
  translateTree(document.body);
}

async function loadLocales() {
  try {
    const response = await fetch("/locales.json", { cache: "no-store" });
    if (!response.ok) throw Error(`locale catalog ${response.status}`);
    localeCatalog = await response.json();
  } catch { localeCatalog = {}; }
  setLocale(localeChoice());
  new MutationObserver((records) => records.forEach((record) => {
    if (record.type === "characterData") return translateTree(record.target.parentElement);
    record.addedNodes.forEach((node) => {
      if (node.nodeType === Node.ELEMENT_NODE) translateTree(node);
      else if (node.nodeType === Node.TEXT_NODE) translateTree(node.parentElement);
    });
  })).observe(document.body, { childList: true, characterData: true, subtree: true });
}

const say = (text) => { $("#result").textContent = translated(text); };
const can = (minimum) => ({ viewer: 1, moderator: 2, admin: 3 })[role] >= ({ viewer: 1, moderator: 2, admin: 3 })[minimum];

async function api(path, options = {}) {
  const method = options.method || "GET";
  const headers = { ...(options.headers || {}) };
  if (options.body) headers["Content-Type"] = "application/json";
  if (method !== "GET" && csrf) headers["X-CSRF-Token"] = csrf;
  const response = await fetch(path, { ...options, method, headers, credentials: "same-origin", cache: "no-store" });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw Error(data.error || data.output || `${response.status} ${response.statusText}`);
  return data;
}

function setIdentity(nextRole, nextCsrf) {
  role = nextRole || "";
  csrf = nextCsrf || "";
  $("#role").textContent = role ? role.toUpperCase() : "LOCKED";
  $("#logout").hidden = !role;
  $("#connect").hidden = Boolean(role);
  $("#pair").hidden = Boolean(role);
  $("#token").hidden = Boolean(role);
  document.querySelectorAll("[data-admin]").forEach((node) => { node.hidden = role !== "admin"; });
  document.querySelectorAll("[data-moderator]").forEach((node) => { node.hidden = !can("moderator"); });
}

async function login() {
  const token = $("#token").value;
  if (!token) return say("Enter a configured role token.");
  try {
    const data = await api("/api/v1/auth/login", { method: "POST", body: JSON.stringify({ token }) });
    setIdentity(data.role, data.csrf);
    $("#token").value = "";
    localStorage.removeItem("palworld-token");
    say(`${data.role} session started.`);
    await loadConsole();
  } catch (error) {
    say(error.message);
  }
}

async function pair() {
  const code = $("#token").value.trim();
  if (!code) return say("Enter a one-time pairing code.");
  try {
    const data = await api("/api/v1/auth/pair", { method: "POST", body: JSON.stringify({ code }) });
    setIdentity(data.role, data.csrf); $("#token").value = "";
    say(`${data.role} device paired; the code is now spent.`); await loadConsole();
  } catch (error) { say(error.message); }
}

async function restoreSession() {
  try {
    const data = await api("/api/v1/auth/session");
    setIdentity(data.role, data.csrf);
    say(`${data.role} session restored.`);
    await loadConsole();
  } catch {
    setIdentity("", "");
    $("#seal").textContent = "CONTROL PLANE LOCKED";
  }
}

async function logout() {
  try { await api("/api/v1/auth/logout", { method: "POST", body: "{}" }); } catch { /* expire locally too */ }
  setIdentity("", "");
  $("#seal").textContent = "CONTROL PLANE LOCKED";
  say("Private session ended.");
}

async function refresh() {
  if (!role) return;
  try {
    const data = await api("/api/v1/status");
    const metrics = data.metrics || {};
    const info = data.info || {};
    $("#seal").textContent = data.service === "active" ? "WORLD ONLINE" : "WORLD OFFLINE";
    $("#players").textContent = metrics.currentplayernum ?? 0;
    $("#capacity").textContent = metrics.maxplayernum ?? 32;
    $("#uptime").textContent = duration(metrics.uptime || 0);
    $("#day").textContent = metrics.days ?? "—";
    $("#build").textContent = info.version || "—";
    if (data.backup) {
      $("#backup").textContent = data.backup.name;
      $("#backupMeta").textContent = `${bytes(data.backup.bytes)} · ${duration(data.backup.age_seconds)} ago`;
    }
    const diskUsed = data.disk?.total ? 100 - data.disk.free / data.disk.total * 100 : 0;
    $("#disk").style.width = `${Math.max(0, Math.min(diskUsed, 100))}%`;
    $("#maintenance").textContent = `Next maintenance · ${data.next_maintenance || "—"}`;
    $("#health").textContent = data.health?.ok === false ? `Health: ${data.health.issues.join(", ")}` : "Health: nominal";
    renderDiagnostics(data);
    renderRoster(data.players?.players || []);
    renderJobs(data.jobs || []);
    $("#updated").textContent = `Updated ${new Date().toLocaleTimeString()}`;
  } catch (error) {
    $("#seal").textContent = "CONTROL PLANE LOST";
    say(error.message);
  }
}

function renderDiagnostics(data) {
  const health = data.health || {};
  const backup = data.backup;
  const diskFree = data.disk?.free || 0;
  const diskPercent = data.disk?.total ? diskFree / data.disk.total * 100 : 0;
  const update = data.update || {};
  const maintenance = data.maintenance || {};
  const exposure = data.exposure || {};
  const autoPause = data.auto_pause || {};
  const channel = (label, value, ok) => `<div class="${ok ? "ok" : "warn"}"><b>${esc(label)}</b><span>${esc(value)}</span></div>`;
  const backupHealthy = Boolean(backup?.checksum && backup?.manifest && Object.keys(backup.manifest).length && backup.age_seconds < 93600);
  const healthValue = health.ok === false
    ? (health.issues || ["reported failure"]).join(", ")
    : (Object.keys(health).length ? "Latest health run passed" : "No health report yet");
  const updateValue = update.update_available === true
    ? `Build ${update.remote_build || "available"} waiting`
    : (update.result || update.status || "No update pending");
  const maintenanceValue = maintenance.result || maintenance.status || (data.next_maintenance ? "Timer scheduled" : "No timer state");
  $("#diagnostics").innerHTML = [
    channel("World process", data.service || "unknown", data.service === "active"),
    channel("Private REST", data.metrics && data.info ? "Responding" : "Unavailable", Boolean(data.metrics && data.info)),
    channel("Health monitor", healthValue, health.ok !== false && Object.keys(health).length > 0),
    channel("Backup continuity", backup ? `${duration(backup.age_seconds)} old · ${backup.checksum ? "checksum" : "missing checksum"}` : "No managed backup", backupHealthy),
    channel("Backup storage", `${bytes(diskFree)} free · ${diskPercent.toFixed(1)}%`, diskPercent >= 10),
    channel("Steam update", updateValue, update.update_available !== true),
    channel("Maintenance", maintenanceValue, Boolean(data.next_maintenance)),
    channel("Auto-pause", autoPause.status || "No evaluation yet", autoPause.status !== "failed" && autoPause.status !== "roster-unavailable"),
    channel("Control-plane bind", exposure.safe === true ? "Loopback-only" : (exposure.issues || ["Not checked"]).join(", "), exposure.safe === true),
    channel("Public game probe", exposure.public_game?.status || "Not checked", exposure.public_game?.reachable === true),
  ].join("");
}

function renderRoster(players) {
  $("#roster").innerHTML = players.map((player) => {
    const id = player.userId || player.userid || "";
    const controls = can("moderator") && id && id !== "<redacted>"
      ? `<button data-mod="kick" data-id="${esc(id)}">Kick</button><button class="danger" data-mod="ban" data-id="${esc(id)}">Ban</button>`
      : "";
    return `<div><b>${esc(player.name || "Unknown")}</b><span>Lv ${esc(player.level ?? "—")} · ${Math.round(player.ping || 0)}ms</span>${controls}</div>`;
  }).join("") || '<p class="muted">No players are online.</p>';
}

function renderJobs(jobs) {
  $("#jobs").innerHTML = jobs.filter((job) => job.enabled).map((job) => {
    const cancel = can("admin") ? ` <button data-cancel="${esc(job.id)}">Cancel</button>` : "";
    const when = job.phase === "active" ? `restores ${new Date(job.next_run * 1000).toLocaleString()}` : new Date(job.due_at * 1000).toLocaleString();
    const detail = job.type === "settings_event" ? ` · ${esc(job.event_label || job.preset)} · ${esc(job.phase || "pending")}` : "";
    return `<p>${esc(job.type)}${detail} · ${when}${cancel}</p>`;
  }).join("") || '<p class="muted">No pending jobs.</p>';
}

function renderWhitelist(enabled) {
  $("#toggleWhitelist").textContent = enabled ? "Disable whitelist" : "Enable whitelist";
  $("#whitelistState").innerHTML = whitelistEntries.map((entry, index) => `<div><div><b>${esc(entry.name || "Unlabelled identity")}</b><span>${esc(entry.user_id)}</span></div><div></div><div class="row-actions"><button data-whitelist-remove="${index}">Remove staged</button></div></div>`).join("") || '<p class="muted">The staged allow-list is empty. It cannot be enabled.</p>';
}

async function loadRconAdmin() {
  if (!can("admin")) return;
  const [rcon, whitelist] = await Promise.all([api("/api/v1/rcon"), api("/api/v1/whitelist")]);
  const privateBoundary = rcon.firewall?.effective_private === true;
  $("#rconStatus").innerHTML = `<span><b>${rcon.configured ? "ENABLED" : "DISABLED"}</b>configured channel</span><span><b>${privateBoundary ? "PRIVATE" : "UNSAFE"}</b>firewall boundary</span><span><b>${rcon.connection?.ok ? "READY" : "OFFLINE"}</b>${esc(rcon.connection?.output || "no response")}</span><span><b>${esc((rcon.history || []).length)}</b>recent commands</span><span><b>${rcon.paldefender_commands_discovered ? esc(rcon.paldefender_command_count) : "—"}</b>PalDefender commands discovered</span>`;
  $("#toggleRcon").textContent = rcon.configured ? "Disable private RCON" : "Enable private RCON";
  $("#toggleRcon").dataset.enabled = rcon.configured ? "true" : "false";
  $("#rconCatalog").innerHTML = '<option value="">Choose official command</option>' + (rcon.catalog || []).map((item) => `<option value="${esc(item.command)}">${esc(item.command)} ${esc(item.arguments || "")} · ${esc(item.source || "palworld")} · ${esc(item.risk)}</option>`).join("");
  $("#rconSaved").innerHTML = (rcon.saved || []).map((item) => `<div><div><b>${esc(item.name)}</b><span>${esc(item.command)}</span></div><div></div><div class="row-actions"><button data-rcon-run-saved="${item.id}">Execute</button><button data-rcon-delete-saved="${item.id}">Delete</button></div></div>`).join("") || '<p class="muted">No saved commands.</p>';
  $("#jobSavedCommand").innerHTML = '<option value="">Direct RCON command</option>' + (rcon.saved || []).map((item) => `<option value="${item.id}">${esc(item.name)} · ${esc(item.command)}</option>`).join("");
  $("#rconHistory").textContent = [...(rcon.history || [])].reverse().map((item) => `${new Date(item.timestamp * 1000).toLocaleString()} · ${item.actor} · ${item.ok ? "OK" : "FAILED"}\n> ${item.command}\n${item.output}`).join("\n\n") || "No retained commands.";
  whitelistEntries = (whitelist.entries || []).map((entry) => ({ ...entry }));
  $("#toggleWhitelist").dataset.enabled = whitelist.enabled ? "true" : "false";
  renderWhitelist(Boolean(whitelist.enabled));
}

async function loadPlayerHistory() {
  const query = new URLSearchParams({ search: $("#playerHistorySearch").value, limit: "300" });
  const data = await api(`/api/v1/players/history?${query}`);
  $("#playerHistory").innerHTML = (data.players || []).map((player) => `<div><div><b>${esc(player.name)}</b><span>${player.online ? "Online now" : `Last seen ${new Date(player.last_seen * 1000).toLocaleString()}`} · first seen ${new Date(player.first_seen * 1000).toLocaleDateString()}</span></div><div><b>${esc(player.sessions)} sessions</b><span>${duration(player.total_seconds || 0)} recorded play</span></div><div>${player.user_key ? `<small>${esc(player.user_key)}</small>` : ""}</div></div>`).join("") || '<p class="muted">No retained players match.</p>';
}

function renderSaveMap(data, gameData = {}) {
  const points = [];
  (mapLocationsCache || []).forEach((item) => { if (item.position) points.push({ ...item.position, kind: "poi", label: item.label || item.type || "Point of interest" }); });
  (data.map_objects || []).forEach((item) => { if (item.position) points.push({ ...item.position, kind: "object" }); });
  (data.bases || []).forEach((item) => { if (item.position) points.push({ ...item.position, kind: "base", label: item.name || "Base" }); });
  (data.players || []).forEach((item) => { if (item.position) points.push({ ...item.position, kind: "player", label: item.name || "Player" }); });
  if (["ready", "stale"].includes(gameData.state)) (gameData.actors || []).forEach((actor) => {
    if (!actor.position) return;
    if (actor.kind === "PalBox") points.push({ ...actor.position, kind: "palbox", label: actor.name || "Palbox" });
    if (actor.kind === "BaseCampPal") points.push({ ...actor.position, kind: "worker", label: `${actor.name || actor.character_id || "Worker"} · ${actor.activity}` });
    if (actor.kind === "WildPal" && actor.boss) points.push({ ...actor.position, kind: "boss", label: actor.name || actor.character_id || "Boss" });
  });
  if (pickedCoordinate) points.push({ ...pickedCoordinate, kind: "pin", label: "Picked coordinate" });
  const finite = points.filter((point) => Number.isFinite(Number(point.x)) && Number.isFinite(Number(point.y)));
  if (!finite.length) {
    $("#saveMapPoints").innerHTML = "";
    $("#saveMapLegend").textContent = "No coordinate-bearing records in the latest scan.";
    return;
  }
  const xs = finite.map((point) => Number(point.x)); const ys = finite.map((point) => Number(point.y));
  const minX = Math.min(...xs); const maxX = Math.max(...xs); const minY = Math.min(...ys); const maxY = Math.max(...ys);
  saveMapBounds = { minX, maxX, minY, maxY };
  const scaleX = (value) => 30 + (Number(value) - minX) / (maxX - minX || 1) * 940;
  const scaleY = (value) => 490 - (Number(value) - minY) / (maxY - minY || 1) * 460;
  $("#saveMapPoints").innerHTML = finite.map((point) => {
    const radius = point.kind === "player" ? 9 : (point.kind === "base" || point.kind === "palbox" ? 12 : (point.kind === "boss" || point.kind === "pin" ? 7 : (point.kind === "poi" ? 2 : 3)));
    const title = point.label ? `<title>${esc(point.label)}</title>` : "";
    return `<circle class="save-map-${point.kind}" cx="${scaleX(point.x).toFixed(2)}" cy="${scaleY(point.y).toFixed(2)}" r="${radius}">${title}</circle>`;
  }).join("");
  const counts = finite.reduce((result, point) => ({ ...result, [point.kind]: (result[point.kind] || 0) + 1 }), {});
  $("#saveMapLegend").textContent = `${counts.player || 0} PLAYERS · ${counts.base || 0} BASES · ${counts.worker || 0} LIVE WORKERS · ${counts.boss || 0} BOSSES · ${counts.palbox || 0} PALBOXES · ${counts.poi || 0} NAMED POIS · ${counts.object || 0} STRUCTURES`;
}

function renderSaveIntelligence() {
  const data = saveIntelligenceCache || { counts: {}, players: [], guilds: [], bases: [], map_objects: [] };
  const gameData = gameDataCache || { state: "never_polled", counts: {}, actors: [] };
  const counts = data.counts || {};
  const compatible = data.status === "compatible";
  const generated = data.generated_at ? new Date(data.generated_at * 1000).toLocaleString() : "never";
  $("#saveIntelligenceStatus").innerHTML = `<span><b>${esc(data.status || "unknown")}</b> parser state</span><span><b>${esc(counts.players || 0)}</b> offline profiles</span><span><b>${esc(counts.pals || 0)}</b> Pals</span><span><b>${esc(counts.guilds || 0)} / ${esc(counts.bases || 0)}</b> guilds / bases</span>`;
  $("#saveIntelligenceStatus").classList.toggle("save-degraded", !compatible);
  const query = $("#palSearch").value.trim().toLocaleLowerCase();
  const profiles = (data.players || []).map((player) => {
    const ownerMatch = `${player.name || ""} ${player.guild_name || ""}`.toLocaleLowerCase().includes(query);
    const pals = (player.pals || []).filter((pal) => ownerMatch || `${pal.name || ""} ${pal.character_id || ""} ${(pal.work_suitability || []).map((work) => `${work.name} ${work.level}`).join(" ")}`.toLocaleLowerCase().includes(query));
    return { ...player, pals };
  }).filter((player) => !query || player.pals.length || `${player.name || ""} ${player.guild_name || ""}`.toLocaleLowerCase().includes(query));
  $("#offlineProfiles").innerHTML = profiles.map((player) => {
    const inventory = Object.entries(player.inventory || {}).flatMap(([kind, items]) => (items || []).map((item) => `${kind}: ${item.item_id} × ${item.count}`));
    const pals = player.pals || [];
    return `<details><summary>${esc(player.name || "Unknown player")} · Lv ${esc(player.level ?? "—")}<span>${esc(player.guild_name || "No guild")} · ${pals.length} Pals · ${inventory.length} inventory stacks · ${esc((player.paldeck || []).length)} Paldeck species</span></summary><dl><div><dt>Last save scan</dt><dd>${esc(generated)}</dd></div><div><dt>Party / box / base</dt><dd>${pals.filter((pal) => pal.location === "party").length} / ${pals.filter((pal) => pal.location === "palbox").length} / ${pals.filter((pal) => pal.location === "base").length}</dd></div><div><dt>Schema</dt><dd>${esc(data.status || "unknown")}</dd></div></dl><ul>${pals.map((pal) => `<li><b>${esc(pal.name || pal.character_id)}</b><span>Lv ${esc(pal.level ?? "—")} · ${esc(pal.location || "unknown")}${(pal.work_suitability || []).length ? ` · ${(pal.work_suitability || []).map((work) => `${esc(work.name)} ${esc(work.level)}`).join(" · ")}` : ""}</span></li>`).join("")}${inventory.map((item) => `<li><b>${esc(item)}</b></li>`).join("")}</ul></details>`;
  }).join("") || '<p class="muted">No offline profiles are available. Run a save scan as an administrator.</p>';
  $("#saveGuilds").innerHTML = (data.guilds || []).map((guild) => {
    const guildBases = (data.bases || []).filter((base) => base.guild_id === guild.id || guild.id === "<redacted>");
    return `<details><summary>${esc(guild.name || "Unnamed guild")}<span>${esc((guild.members || []).length)} members · ${guildBases.length} bases · guild level ${esc(guild.level ?? "—")}</span></summary><ul>${(guild.members || []).map((member) => `<li><b>${esc(member.name || "Unknown")}</b></li>`).join("")}${guildBases.map((base) => `<li><b>${esc(base.name || "Base")}</b><span>${esc(base.worker_count || 0)} assigned workers · x ${Math.round(base.position?.x || 0)} · y ${Math.round(base.position?.y || 0)}</span></li>`).join("")}</ul></details>`;
  }).join("") || '<p class="muted">No guild records are present.</p>';
  const gdCounts = gameData.counts || {};
  $("#gameDataStatus").textContent = `GAME DATA ${String(gameData.state || "unknown").toUpperCase()} · LAST ATTEMPT ${gameData.last_attempt_at ? new Date(gameData.last_attempt_at * 1000).toLocaleString() : "never"} · ${gdCounts.base_pals || 0} LIVE WORKERS · ${gdCounts.wild_pals || 0} WILD PALS · ${gdCounts.palboxes || 0} PALBOXES`;
  renderSaveMap(data, gameData);
}

async function loadSaveIntelligence() {
  let locations;
  [saveIntelligenceCache, gameDataCache, locations] = await Promise.all([
    api("/api/v1/save/intelligence"), api("/api/v1/game-data"), api("/api/v1/map/locations"),
  ]);
  mapLocationsCache = locations.locations || [];
  renderSaveIntelligence();
}

function renderPlayerActionFields() {
  const action = $("#playerActionType").value;
  document.querySelectorAll(".player-action-target").forEach((item) => { item.hidden = action === "spawn_pal"; });
  document.querySelectorAll(".player-action-coordinate").forEach((item) => { item.hidden = !["teleport", "spawn_pal"].includes(action); });
  document.querySelectorAll(".player-action-asset").forEach((item) => { item.hidden = !["grant_item", "grant_pal", "spawn_pal"].includes(action); });
  document.querySelectorAll(".player-action-amount").forEach((item) => { item.hidden = action === "teleport"; });
  const available = Boolean(playerActionCapabilities?.actions?.[action]?.available);
  $("#planPlayerAction").disabled = !available;
  $("#executePlayerAction").disabled = true;
  playerActionPlan = null; $("#playerActionPlan").hidden = true;
}

async function loadPlayerActions() {
  try {
    playerActionCapabilities = await api("/api/v1/player-actions");
    const actions = Object.values(playerActionCapabilities.actions || {});
    const count = actions.filter((item) => item.available).length;
    $("#playerActionStatus").textContent = playerActionCapabilities.discovery_succeeded ? `${count} of ${actions.length} typed actions advertised by the running plugin.` : "PalDefender command discovery unavailable; every typed action is disabled.";
  } catch (error) {
    playerActionCapabilities = null;
    $("#playerActionStatus").textContent = "PalDefender command discovery failed; every typed action is disabled.";
  }
  renderPlayerActionFields();
}

function typedPlayerActionPayload() {
  const action = $("#playerActionType").value;
  const payload = { action };
  if (action !== "spawn_pal") payload.target = $("#playerActionTarget").value.trim();
  if (["teleport", "spawn_pal"].includes(action)) Object.assign(payload, { x: $("#playerActionX").value, y: $("#playerActionY").value, z: $("#playerActionZ").value || "0" });
  if (action === "spawn_pal") Object.assign(payload, { pal_id: $("#playerActionAsset").value.trim(), level: $("#playerActionAmount").value });
  if (action === "grant_item") Object.assign(payload, { item_id: $("#playerActionAsset").value.trim(), amount: $("#playerActionAmount").value });
  if (action === "grant_pal") Object.assign(payload, { pal_id: $("#playerActionAsset").value.trim(), level: $("#playerActionAmount").value });
  if (action === "grant_status") payload.points = $("#playerActionAmount").value;
  return payload;
}

async function planTypedPlayerAction() {
  playerActionPlan = await api("/api/v1/player-actions/plan", { method: "POST", body: JSON.stringify({ payload: typedPlayerActionPayload() }) });
  $("#playerActionPlan").textContent = JSON.stringify(playerActionPlan, null, 2); $("#playerActionPlan").hidden = false;
  $("#executePlayerAction").disabled = false;
  say(`Typed ${playerActionPlan.action} plan is ready; review its hash and verification mode.`);
}

async function executeTypedPlayerAction() {
  if (!playerActionPlan) return;
  const payload = typedPlayerActionPayload();
  const confirmation = window.prompt(`Execute the reviewed ${playerActionPlan.action}? Type ${playerActionPlan.confirmation}.`);
  if (confirmation !== playerActionPlan.confirmation) return say("Typed player action was not confirmed.");
  const result = await api("/api/v1/player-actions/execute", { method: "POST", body: JSON.stringify({ payload, expected_sha256: playerActionPlan.reviewed_sha256, confirm: confirmation }) });
  say(`Typed action completed with ${result.verification}.`); playerActionPlan = null; $("#executePlayerAction").disabled = true;
  $("#playerActionPlan").textContent = JSON.stringify(result, null, 2);
}

function saveRepairPayload() {
  const action = $("#saveRepairAction").value;
  if (["cleanup_duplicates", "cleanup_graph"].includes(action)) return { action };
  if (action === "delete_inactive_players") return { action, inactive_days: $("#saveRepairDays").value };
  if (action === "rename_player") return { action, uid: $("#saveRepairSource").value.trim(), new_name: $("#saveRepairName").value.trim() };
  if (action === "edit_inventory_slot") return {
    action, uid: $("#saveRepairSource").value.trim(), container: $("#saveRepairInventoryArea").value,
    slot_index: $("#saveRepairSlot").value, item_id: $("#saveRepairItem").value.trim(),
    stack_count: $("#saveRepairStack").value,
  };
  if (action === "edit_player_progression") {
    const payload = { action, uid: $("#saveRepairSource").value.trim() };
    if ($("#saveRepairLevel").value !== "") payload.level = $("#saveRepairLevel").value;
    if ($("#saveRepairExperience").value !== "") payload.experience = $("#saveRepairExperience").value;
    return payload;
  }
  if (action === "edit_owned_pal") {
    const payload = { action, uid: $("#saveRepairSource").value.trim(), pal_instance_id: $("#saveRepairPalInstance").value.trim() };
    const optional = [["nickname", "#saveRepairPalNickname"], ["level", "#saveRepairLevel"],
      ["rank", "#saveRepairPalRank"], ["talent_hp", "#saveRepairTalentHp"],
      ["talent_attack", "#saveRepairTalentAttack"], ["talent_defense", "#saveRepairTalentDefense"]];
    optional.forEach(([key, selector]) => { if ($(selector).value !== "") payload[key] = $(selector).value.trim(); });
    const passiveText = $("#saveRepairPassives").value.trim();
    if (passiveText) payload.passives = passiveText === "NONE" ? [] : passiveText.split(",").map((value) => value.trim()).filter(Boolean);
    return payload;
  }
  const target = $("#saveRepairTarget").value.trim();
  if (action === "transfer_player") return { action, source_world_id: $("#saveRepairWorld").value.trim(), source_uid: $("#saveRepairSource").value.trim(), target_uid: target };
  return action === "migrate_player"
    ? { action, old_uid: $("#saveRepairSource").value.trim(), new_uid: target }
    : { action, old_uid: $("#saveRepairSource").value.trim(), target_uid: target };
}

function invalidateSaveRepairPlan() {
  const action = $("#saveRepairAction").value;
  const needsIdentity = ["rename_player", "migrate_player", "replace_player", "transfer_player", "edit_inventory_slot", "edit_player_progression", "edit_owned_pal"].includes(action);
  $(".save-repair-world").hidden = action !== "transfer_player";
  $(".save-repair-source").hidden = !needsIdentity;
  $(".save-repair-name").hidden = action !== "rename_player";
  $(".save-repair-target").hidden = !["migrate_player", "replace_player", "transfer_player"].includes(action);
  $(".save-repair-days").hidden = action !== "delete_inactive_players";
  document.querySelectorAll(".save-repair-inventory").forEach((field) => { field.hidden = action !== "edit_inventory_slot"; });
  $(".save-repair-progress").hidden = action !== "edit_player_progression";
  $(".save-repair-character-level").hidden = !["edit_player_progression", "edit_owned_pal"].includes(action);
  document.querySelectorAll(".save-repair-pal").forEach((field) => { field.hidden = action !== "edit_owned_pal"; });
  saveRepairPlan = null; $("#applySaveRepair").disabled = true;
  $("#saveRepairStatus").textContent = "No offline repair plan has been reviewed.";
}

async function diagnoseSaveRepair() {
  $("#saveRepairStatus").textContent = "Decoding the current save for repair diagnostics.";
  const result = await api("/api/v1/save/repair/diagnose", { method: "POST", body: "{}" });
  $("#saveRepairResult").textContent = JSON.stringify(result, null, 2); $("#saveRepairResult").hidden = false;
  $("#saveRepairStatus").textContent = `Diagnostics found ${result.diagnostics?.players || 0} player records; no save was changed.`;
}

async function planSaveRepair() {
  invalidateSaveRepairPlan();
  $("#saveRepairStatus").textContent = "Saving and fingerprinting the zero-player world for review.";
  saveRepairPlan = await api("/api/v1/save/repair/plan", { method: "POST", body: JSON.stringify({ payload: saveRepairPayload() }) });
  $("#saveRepairResult").textContent = JSON.stringify(saveRepairPlan, null, 2); $("#saveRepairResult").hidden = false;
  $("#applySaveRepair").disabled = false;
  $("#saveRepairStatus").textContent = `Reviewed ${saveRepairPlan.action} plan ready for build ${saveRepairPlan.game_build}.`;
}

async function applySaveRepair() {
  if (!saveRepairPlan) return;
  const confirmation = window.prompt(`This stops the zero-player world after a protected backup. Type ${saveRepairPlan.confirmation}.`);
  if (confirmation !== saveRepairPlan.confirmation) return say("Offline save mutation was not confirmed.");
  $("#applySaveRepair").disabled = true; say("Protected offline save mutation is running.");
  const result = await api("/api/v1/save/repair/apply", { method: "POST", body: JSON.stringify({
    payload: saveRepairPayload(), reviewed_sha256: saveRepairPlan.reviewed_sha256, confirm: confirmation,
  }) });
  saveRepairPlan = null; $("#saveRepairResult").textContent = JSON.stringify(result, null, 2);
  $("#saveRepairStatus").textContent = "Offline save mutation completed and the observed staged result passed verification.";
  say("Offline save mutation completed with automatic recovery preserved."); await loadSaveIntelligence();
}

async function scanSaveIntelligence() {
  say("Scanning a temporary copy of the current save. The world remains online.");
  const result = await api("/api/v1/save/intelligence/scan", { method: "POST", body: "{}" });
  say(`Save scan ${result.status}: ${result.counts?.players || 0} players, ${result.counts?.pals || 0} Pals.`);
  await loadSaveIntelligence();
}

async function loadSchema() {
  if (!can("admin")) return;
  const data = await api("/api/v1/settings/schema");
  settingsSchema = data.settings || {};
  settingEffective = data.effective || {};
  settingsPresets = data.presets || {};
  settingsPending = data.pending_restart || {};
  settingValues = {};
  settingBaseline = {};
  Object.entries(settingsSchema).forEach(([key, value]) => {
    const current = data.overrides?.[key] ?? value.default;
    settingValues[key] = String(current);
    settingBaseline[key] = String(current);
  });
  const categories = [...new Set(Object.values(settingsSchema).filter((item) => item.writable).map((item) => item.category))].sort();
  $("#settingsCategory").innerHTML = '<option value="">All categories</option>' + categories.map((category) => `<option value="${esc(category)}">${esc(category)}</option>`).join("");
  $("#settingsPreset").innerHTML = '<option value="">Choose preset</option>' + Object.entries(settingsPresets).map(([name, preset]) => `<option value="${esc(name)}">${esc(name)} · ${esc(preset.description)}</option>`).join("");
  $("#settingsState").innerHTML = settingsPending.applied_at
    ? `<span class="pending">Restart pending · ${new Date(settingsPending.applied_at * 1000).toLocaleString()}</span><span>${esc(settingsPending.source || "settings")} · ${esc((settingsPending.changed || []).join(", "))}</span>`
    : '<span>Rendered settings loaded by the running service</span>';
  renderSettings();
}

async function loadRawSettings() {
  if (!can("admin")) return;
  const data = await api("/api/v1/settings/raw");
  $("#rawSettings").value = data.content || "";
  $("#rawSettings").maxLength = data.max_bytes || 49152;
}

function captureSettingValues() {
  document.querySelectorAll("[data-setting]").forEach((input) => { settingValues[input.dataset.setting] = input.value; });
}

function renderSettings() {
  const filter = $("#settingsSearch").value.toLowerCase();
  const category = $("#settingsCategory").value;
  const matches = Object.entries(settingsSchema).filter(([key, item]) => item.writable
    && ["boolean", "integer", "number", "string"].includes(item.type)
    && (!category || item.category === category)
    && [key, item.label, item.description, item.category].join(" ").toLowerCase().includes(filter));
  const groups = matches.reduce((result, entry) => {
    const name = entry[1].category || "Other";
    (result[name] ||= []).push(entry);
    return result;
  }, {});
  const inputFor = (key, item) => {
    const current = settingValues[key];
    if (item.choices) return `<select data-setting="${esc(key)}" data-type="${item.type}">${item.choices.map((choice) => `<option${String(choice) === current ? " selected" : ""}>${esc(choice)}</option>`).join("")}</select>`;
    if (item.type === "boolean") return `<select data-setting="${esc(key)}" data-type="boolean"><option${current === "true" ? " selected" : ""}>true</option><option${current === "false" ? " selected" : ""}>false</option></select>`;
    const numeric = ["integer", "number"].includes(item.type);
    const bounds = numeric ? `${item.recommended_min ?? ""}–${item.recommended_max ?? ""}${item.unit ? ` ${item.unit}` : ""}` : "";
    return `<input data-setting="${esc(key)}" data-type="${item.type}" type="${numeric ? "number" : "text"}"${item.recommended_min !== undefined ? ` min="${esc(item.recommended_min)}"` : ""}${item.recommended_max !== undefined ? ` max="${esc(item.recommended_max)}"` : ""}${item.step !== undefined ? ` step="${esc(item.step)}"` : ""}${item.max_length !== undefined ? ` maxlength="${esc(item.max_length)}"` : ""} value="${esc(current)}"><small>${esc(bounds)}</small>`;
  };
  $("#settings").innerHTML = Object.entries(groups).map(([name, entries]) => `<section class="settings-group"><h3>${esc(name)}</h3><div class="settings-fields">${entries.map(([key, item]) => {
    const changed = settingValues[key] !== settingBaseline[key];
    const effective = settingEffective[key] !== undefined ? ` · rendered ${settingEffective[key]}` : "";
    return `<label class="${changed ? "changed" : ""}"><b>${esc(item.label || key)}</b><small>${esc(key)} · ${esc(item.type)}${esc(effective)}</small><small class="setting-description">${esc(item.description)}</small>${inputFor(key, item)}</label>`;
  }).join("")}</div></section>`).join("") || '<p class="muted">No writable settings match this filter.</p>';
}

async function settings(action) {
  captureSettingValues();
  const updates = {};
  Object.entries(settingValues).forEach(([key, current]) => {
    if (current === settingBaseline[key]) return;
    let value = current;
    const type = settingsSchema[key].type;
    if (type === "boolean") value = value.toLowerCase() === "true";
    else if (type === "integer") value = parseInt(value, 10);
    else if (type === "number") value = parseFloat(value);
    updates[key] = value;
  });
  const body = { updates };
  if (action === "apply") body.confirm = "APPLY SETTINGS";
  const result = await api(`/api/v1/settings/${action}`, { method: "POST", body: JSON.stringify(body) });
  $("#settingsResult").textContent = JSON.stringify(result, null, 2);
  if (action === "apply") await loadSchema();
}

function loadPreset() {
  const name = $("#settingsPreset").value;
  if (!name || !settingsPresets[name]) return say("Choose a preset to stage.");
  Object.entries(settingsPresets[name].values || {}).forEach(([key, value]) => { settingValues[key] = String(value); });
  renderSettings();
  say(`${name} staged. Review highlighted fields, then plan the changes.`);
}

async function rollbackTypedSettings() {
  const confirmation = window.prompt("Roll back to the newest typed-settings snapshot? Type ROLLBACK SETTINGS.");
  if (confirmation !== "ROLLBACK SETTINGS") return say("Typed rollback was not confirmed.");
  const result = await api("/api/v1/settings/rollback", { method: "POST", body: JSON.stringify({ confirm: confirmation }) });
  $("#settingsResult").textContent = JSON.stringify(result, null, 2);
  await loadSchema();
}

async function rawSettings(action) {
  const body = { content: $("#rawSettings").value };
  if (action === "apply") {
    const confirmation = window.prompt("Apply the reviewed raw INI diff? Type APPLY RAW SETTINGS.");
    if (confirmation !== "APPLY RAW SETTINGS") return say("Raw settings were not applied.");
    body.confirm = confirmation;
  }
  const result = await api(`/api/v1/settings/raw/${action}`, { method: "POST", body: JSON.stringify(body) });
  $("#rawSettingsResult").textContent = result.diff || JSON.stringify(result, null, 2);
  if (action === "apply") {
    await Promise.all([loadSchema(), loadRawSettings()]);
    say("Raw settings rendered successfully; a service restart is pending.");
  }
}

async function rollbackRawSettings() {
  const confirmation = window.prompt("Roll back to the newest raw-INI snapshot? Type ROLLBACK RAW SETTINGS.");
  if (confirmation !== "ROLLBACK RAW SETTINGS") return say("Raw rollback was not confirmed.");
  const result = await api("/api/v1/settings/raw/rollback", { method: "POST", body: JSON.stringify({ confirm: confirmation }) });
  $("#rawSettingsResult").textContent = JSON.stringify(result, null, 2);
  await Promise.all([loadSchema(), loadRawSettings()]);
}

function configInput(scope, key, item) {
  const current = String(serverConfigValues[scope][key]);
  const attrs = `data-server-config="${esc(key)}" data-scope="${scope}" data-type="${esc(item.type)}"`;
  if (item.choices) return `<select ${attrs}>${item.choices.map((choice) => `<option${String(choice) === current ? " selected" : ""}>${esc(choice)}</option>`).join("")}</select>`;
  if (item.type === "boolean") return `<select ${attrs}><option${current === "true" ? " selected" : ""}>true</option><option${current === "false" ? " selected" : ""}>false</option></select>`;
  return `<input ${attrs} type="number"${item.min !== undefined ? ` min="${esc(item.min)}"` : ""}${item.max !== undefined ? ` max="${esc(item.max)}"` : ""}${item.step !== undefined ? ` step="${esc(item.step)}"` : ""} value="${esc(current)}">`;
}

function captureServerConfig() {
  document.querySelectorAll("[data-server-config]").forEach((input) => {
    serverConfigValues[input.dataset.scope][input.dataset.serverConfig] = input.value;
  });
}

function renderServerConfig() {
  const entries = [
    ...Object.entries(serverConfigSchema.engine).map((entry) => ["engine", ...entry]),
    ...Object.entries(serverConfigSchema.launch).map((entry) => ["launch", ...entry]),
  ];
  const groups = entries.reduce((result, [scope, key, item]) => {
    const name = `${scope === "engine" ? "Engine.ini" : "Launch"} · ${item.category}`;
    (result[name] ||= []).push([scope, key, item]);
    return result;
  }, {});
  $("#serverConfig").innerHTML = Object.entries(groups).map(([name, fields]) => `<section class="settings-group"><h3>${esc(name)}</h3><div class="settings-fields">${fields.map(([scope, key, item]) => {
    const changed = String(serverConfigValues[scope][key]) !== String(serverConfigBaseline[scope][key]);
    const range = item.min !== undefined ? ` · ${item.min}–${item.max}` : "";
    return `<label class="${changed ? "changed" : ""}"><b>${esc(item.label)}</b><small>${esc(key)} · ${esc(item.type)}${esc(range)}</small><small class="setting-description">${esc(item.description)}</small>${configInput(scope, key, item)}</label>`;
  }).join("")}</div></section>`).join("");
}

async function loadServerConfig() {
  if (!can("admin")) return;
  const [schema, raw] = await Promise.all([api("/api/v1/server-config/schema"), api("/api/v1/server-config/raw")]);
  serverConfigSchema = { engine: schema.engine || {}, launch: schema.launch || {} };
  serverConfigPresets = schema.presets || {};
  serverConfigValues = { engine: {}, launch: {} };
  serverConfigBaseline = { engine: {}, launch: {} };
  Object.entries(serverConfigSchema.engine).forEach(([key, item]) => {
    const value = schema.engine_values?.[key] ?? item.default;
    serverConfigValues.engine[key] = String(value); serverConfigBaseline.engine[key] = String(value);
  });
  Object.entries(serverConfigSchema.launch).forEach(([key, item]) => {
    const value = schema.launch_values?.[key] ?? item.default;
    serverConfigValues.launch[key] = String(value); serverConfigBaseline.launch[key] = String(value);
  });
  $("#serverConfigPreset").innerHTML = '<option value="">Choose Engine preset</option>' + Object.keys(serverConfigPresets).map((name) => `<option>${esc(name)}</option>`).join("");
  $("#serverConfigState").innerHTML = schema.pending_restart?.applied_at
    ? `<span class="pending">Restart pending · ${new Date(schema.pending_restart.applied_at * 1000).toLocaleString()}</span><span>${esc(schema.pending_restart.source)}</span>`
    : `<span>Active launch preview · ${esc((schema.launch_args || []).join(" ") || "no optional flags")}</span>`;
  $("#rawEngine").value = raw.content || "";
  $("#rawEngine").maxLength = raw.max_bytes || 65536;
  renderServerConfig();
}

function typedConfigValue(value, type) {
  if (type === "boolean") return String(value).toLowerCase() === "true";
  if (type === "integer") return parseInt(value, 10);
  if (type === "number") return parseFloat(value);
  return value;
}

async function serverConfig(action) {
  captureServerConfig();
  const body = { engine: {}, launch: {} };
  for (const scope of ["engine", "launch"]) Object.entries(serverConfigValues[scope]).forEach(([key, value]) => {
    if (String(value) !== String(serverConfigBaseline[scope][key])) body[scope][key] = typedConfigValue(value, serverConfigSchema[scope][key].type);
  });
  if (action === "apply") {
    const confirmation = window.prompt("Apply reviewed Engine.ini and launch changes? Type APPLY SERVER CONFIG.");
    if (confirmation !== "APPLY SERVER CONFIG") return say("Server configuration was not applied.");
    body.confirm = confirmation;
  }
  const result = await api(`/api/v1/server-config/${action}`, { method: "POST", body: JSON.stringify(body) });
  $("#serverConfigResult").textContent = JSON.stringify(result, null, 2);
  if (action === "apply") { await loadServerConfig(); say("Engine and launch configuration rendered; restart pending."); }
}

function loadServerConfigPreset() {
  const name = $("#serverConfigPreset").value;
  if (!name || !serverConfigPresets[name]) return say("Choose an Engine preset to stage.");
  Object.entries(serverConfigPresets[name]).forEach(([key, value]) => { serverConfigValues.engine[key] = String(value); });
  renderServerConfig(); say(`${name} Engine tuning staged for review.`);
}

async function rollbackServerConfig() {
  const confirmation = window.prompt("Restore the newest Engine and launch snapshot? Type ROLLBACK SERVER CONFIG.");
  if (confirmation !== "ROLLBACK SERVER CONFIG") return say("Server configuration rollback was not confirmed.");
  const result = await api("/api/v1/server-config/rollback", { method: "POST", body: JSON.stringify({ confirm: confirmation }) });
  $("#serverConfigResult").textContent = JSON.stringify(result, null, 2); await loadServerConfig();
}

async function rawEngine(action) {
  const body = { content: $("#rawEngine").value };
  if (action === "apply") {
    const confirmation = window.prompt("Apply the reviewed raw Engine.ini? Type APPLY ENGINE INI.");
    if (confirmation !== "APPLY ENGINE INI") return say("Raw Engine.ini was not applied.");
    body.confirm = confirmation;
  }
  const result = await api(`/api/v1/server-config/raw/${action}`, { method: "POST", body: JSON.stringify(body) });
  $("#rawEngineResult").textContent = result.diff || JSON.stringify(result, null, 2);
  if (action === "apply") await loadServerConfig();
}

async function rollbackRawEngine() {
  const confirmation = window.prompt("Restore the newest raw Engine.ini snapshot? Type ROLLBACK ENGINE INI.");
  if (confirmation !== "ROLLBACK ENGINE INI") return say("Raw Engine.ini rollback was not confirmed.");
  const result = await api("/api/v1/server-config/raw/rollback", { method: "POST", body: JSON.stringify({ confirm: confirmation }) });
  $("#rawEngineResult").textContent = JSON.stringify(result, null, 2); await loadServerConfig();
}

function modConfigInput(key, item) {
  const current = String(modConfigValues[key]);
  const attrs = `data-mod-config="${esc(key)}" data-type="${esc(item.type)}"`;
  if (item.type === "boolean") return `<select ${attrs}><option${current === "true" ? " selected" : ""}>true</option><option${current === "false" ? " selected" : ""}>false</option></select>`;
  if (item.type === "string") return `<input ${attrs} type="text" maxlength="${esc(item.max_length || 500)}" value="${esc(current)}">`;
  return `<input ${attrs} type="number"${item.min !== undefined ? ` min="${esc(item.min)}"` : ""}${item.max !== undefined ? ` max="${esc(item.max)}"` : ""}${item.step !== undefined ? ` step="${esc(item.step)}"` : ""} value="${esc(current)}">`;
}

function captureModConfig() {
  document.querySelectorAll("[data-mod-config]").forEach((input) => { modConfigValues[input.dataset.modConfig] = input.value; });
}

function renderModConfig() {
  const defender = modConfigStatus?.paldefender || {};
  const container = $("#modConfig");
  if (!defender.installed || !defender.config_exists) {
    container.innerHTML = `<p class="muted">${defender.installed ? "PalDefender is installed, but Config.json has not been generated. Start it once before structured editing." : "PalDefender is not installed in a supported managed path."}</p>`;
    $("#modConfigMotd").disabled = true;
    ["#planModConfig", "#applyModConfig", "#rollbackModConfig"].forEach((selector) => { $(selector).disabled = true; });
  } else {
    $("#modConfigMotd").disabled = false;
    ["#planModConfig", "#applyModConfig", "#rollbackModConfig"].forEach((selector) => { $(selector).disabled = false; });
    const category = $("#modConfigCategory").value;
    const search = $("#modConfigSearch").value.trim().toLowerCase();
    const entries = Object.entries(defender.schema || {}).filter(([key, item]) => {
      if (category && item.category !== category) return false;
      return !search || `${key} ${item.label} ${item.description} ${item.warning}`.toLowerCase().includes(search);
    });
    const groups = entries.reduce((result, entry) => { (result[entry[1].category] ||= []).push(entry); return result; }, {});
    container.innerHTML = Object.entries(groups).map(([name, fields]) => `<section class="settings-group"><h3>${esc(name)}</h3><div class="settings-fields">${fields.map(([key, item]) => {
      const changed = String(modConfigValues[key]) !== String(modConfigBaseline[key]);
      const range = item.min !== undefined ? ` · ${item.min}–${item.max}` : "";
      const warning = item.warning ? `<small class="bad">${esc(item.warning)}</small>` : "";
      return `<label class="${changed ? "changed" : ""}"><b>${esc(item.label)}</b><small>${esc(key)} · ${esc(item.type)}${esc(range)}</small><small class="setting-description">${esc(item.description || "")}</small>${warning}${modConfigInput(key, item)}</label>`;
    }).join("")}</div></section>`).join("") || '<p class="muted">No mod settings match this filter.</p>';
  }
  const lua = modConfigStatus?.ue4ss || {};
  $("#luaModList").innerHTML = lua.installed
    ? (lua.mods || []).map((item) => `<div><div><b>${esc(item.name)}</b><span>${item.enabled ? "Enabled" : "Disabled"} · restart or supported reload required after change</span></div><div class="integrity"><span class="${item.enabled ? "good" : ""}">${item.enabled ? "ENABLED" : "DISABLED"}</span></div><div class="row-actions"><button data-lua-mod="${esc(item.name)}" data-lua-enabled="${item.enabled ? "false" : "true"}">${item.enabled ? "Disable" : "Enable"}</button></div></div>`).join("") || '<p class="muted">No Lua mods were discovered.</p>'
    : '<p class="muted">No managed UE4SS Mods directory was discovered.</p>';
}

function renderModLifecycle() {
  const state = modLifecycleStatus || {};
  const supported = state.supported === true;
  const active = state.server_active === true;
  $("#modLifecycleState").innerHTML = supported
    ? `<span><b>${esc(state.runtime)}</b> supported runtime</span><span class="${active ? "pending" : "good"}"><b>${active ? "WORLD ACTIVE" : "WORLD STOPPED"}</b> loaded DLL files ${active ? "cannot" : "can"} change</span>`
    : `<span class="pending"><b>UNAVAILABLE</b> ${esc(state.reason || "Wine mod runtime is not configured")}</span>`;
  $("#modLifecycleList").innerHTML = Object.entries(state.components || {}).map(([name, item]) => {
    const label = name === "paldefender" ? "PalDefender" : "UE4SS";
    const integrity = item.drift?.length ? `<span class="bad">DRIFT</span>` : (item.managed ? `<span class="good">HASH VERIFIED</span>` : `<span class="pending">UNMANAGED</span>`);
    const installed = item.installed ? `${item.version || "detected"}${item.current_version && item.version !== item.current_version ? ` · current ${item.current_version}` : ""}` : `Not installed · reviewed ${item.current_version}`;
    const controls = supported ? `<button data-mod-lifecycle-component="${esc(name)}" data-mod-lifecycle-action="install">Plan ${item.installed ? "update" : "install"}</button>${item.managed ? `<button data-mod-lifecycle-component="${esc(name)}" data-mod-lifecycle-action="remove" class="danger">Plan remove</button><button data-mod-lifecycle-component="${esc(name)}" data-mod-lifecycle-action="rollback">Plan rollback</button>` : ""}` : "";
    return `<div><div><b>${label}</b><span>${esc(installed)} · ${esc(item.source || "")}</span></div><div class="integrity">${integrity}</div><div class="row-actions">${controls}</div></div>`;
  }).join("") || '<p class="muted">No managed loader status is available.</p>';
}

async function loadModLifecycle() {
  if (!can("admin")) return;
  modLifecycleStatus = await api("/api/v1/mod-lifecycle");
  renderModLifecycle();
}

async function planModLifecycle(component, action) {
  modLifecyclePlan = await api("/api/v1/mod-lifecycle/plan", { method: "POST", body: JSON.stringify({ component, action }) });
  $("#modLifecycleResult").textContent = JSON.stringify(modLifecyclePlan, null, 2);
  $("#modLifecycleExecute").innerHTML = modLifecyclePlan.server_active
    ? '<span class="pending">Stop the world, then create a fresh lifecycle plan.</span>'
    : `<button id="executeModLifecycle" class="danger">Execute reviewed ${esc(action)}</button>`;
  $("#executeModLifecycle")?.addEventListener("click", () => executeModLifecycle().catch((error) => say(error.message)));
}

async function executeModLifecycle() {
  if (!modLifecyclePlan) return;
  const confirmation = window.prompt(`Execute this reviewed loader change? Type ${modLifecyclePlan.confirmation}.`);
  if (confirmation !== modLifecyclePlan.confirmation) return say("Mod lifecycle execution was not confirmed.");
  const result = await api("/api/v1/mod-lifecycle/execute", { method: "POST", body: JSON.stringify({
    component: modLifecyclePlan.component, action: modLifecyclePlan.action,
    expected_plan_hash: modLifecyclePlan.plan_hash, confirm: confirmation,
  }) });
  $("#modLifecycleResult").textContent = JSON.stringify(result, null, 2);
  $("#modLifecycleExecute").innerHTML = ""; modLifecyclePlan = null;
  await Promise.all([loadModLifecycle(), loadModConfig()]);
  say("Reviewed mod lifecycle change completed; restart remains explicit.");
}

async function loadModConfig() {
  if (!can("admin")) return;
  modConfigStatus = await api("/api/v1/mod-config");
  const defender = modConfigStatus.paldefender || {};
  modConfigValues = {}; modConfigBaseline = {};
  Object.entries(defender.schema || {}).forEach(([key, item]) => {
    const value = defender.values?.[key] ?? item.default;
    modConfigValues[key] = String(value); modConfigBaseline[key] = String(value);
  });
  modConfigMotdBaseline = [...(defender.motd || [])];
  $("#modConfigMotd").value = modConfigMotdBaseline.join("\n");
  const categories = [...new Set(Object.values(defender.schema || {}).map((item) => item.category))].sort();
  const selected = $("#modConfigCategory").value;
  $("#modConfigCategory").innerHTML = '<option value="">All mod categories</option>' + categories.map((name) => `<option${name === selected ? " selected" : ""}>${esc(name)}</option>`).join("");
  $("#modConfigState").innerHTML = defender.config_exists
    ? `<span><b>PALDEFENDER</b> ${esc(defender.path)}</span><span><b>${esc(Object.keys(defender.schema || {}).length)}</b> typed fields</span><span><b>${esc(defender.unknown_key_count || 0)}</b> unknown keys preserved</span><span><b>${esc((defender.sha256 || "").slice(0, 12))}…</b> reviewed source hash</span>`
    : `<span class="pending"><b>${defender.installed ? "CONFIG NOT GENERATED" : "NOT INSTALLED"}</b> structured PalDefender editing unavailable</span>`;
  renderModConfig();
}

function modConfigPayload() {
  captureModConfig();
  const updates = {};
  Object.entries(modConfigValues).forEach(([key, value]) => {
    if (String(value) !== String(modConfigBaseline[key])) updates[key] = typedConfigValue(value, modConfigStatus.paldefender.schema[key].type);
  });
  const motd = $("#modConfigMotd").value.split("\n");
  const normalizedMotd = motd.length === 1 && motd[0] === "" ? [] : motd;
  const payload = { updates };
  if (JSON.stringify(normalizedMotd) !== JSON.stringify(modConfigMotdBaseline)) payload.motd = normalizedMotd;
  return payload;
}

async function modConfigAction(action) {
  const body = modConfigPayload();
  if (action === "apply") {
    const confirmation = window.prompt("Apply the reviewed PalDefender merge? Type APPLY MOD CONFIG.");
    if (confirmation !== "APPLY MOD CONFIG") return say("Mod configuration was not applied.");
    body.confirm = confirmation; body.expected_sha256 = modConfigStatus.paldefender.sha256;
  }
  const result = await api(`/api/v1/mod-config/${action}`, { method: "POST", body: JSON.stringify(body) });
  $("#modConfigResult").textContent = JSON.stringify(result, null, 2);
  if (action === "apply") { await loadModConfig(); say("Mod configuration installed; reload or restart remains explicit."); }
}

async function rollbackModConfig() {
  const confirmation = window.prompt("Restore the newest private mod-configuration snapshot? Type ROLLBACK MOD CONFIG.");
  if (confirmation !== "ROLLBACK MOD CONFIG") return say("Mod configuration rollback was not confirmed.");
  const result = await api("/api/v1/mod-config/rollback", { method: "POST", body: JSON.stringify({
    confirm: confirmation, expected_sha256: modConfigStatus.paldefender.sha256,
  }) });
  $("#modConfigResult").textContent = JSON.stringify(result, null, 2); await loadModConfig();
}

async function updateControl(action) {
  const result = await api(`/api/v1/updates/${action}`, { method: "POST", body: "{}" });
  if (action === "check") {
    say(result.update_available ? `Steam build ${result.remote_build} is available.` : "Installed Steam build is current.");
    return refresh();
  }
  const plan = $("#updatePlan");
  plan.hidden = false;
  const execute = result.executable
    ? `\n\n<button class="danger" data-update-install="${esc(result.target_build)}">Install reviewed build ${esc(result.target_build)}</button>`
    : "";
  plan.innerHTML = `${esc(JSON.stringify(result, null, 2))}${execute}`;
  plan.scrollIntoView({ block: "center" });
}

async function loadBuilds() {
  const result = await api("/api/v1/builds");
  const pin = result.pin?.build_id;
  const currentControls = pin
    ? `<button data-build-unpin="${esc(pin)}">Unpin build ${esc(pin)}</button>`
    : `<button data-build-pin="${esc(result.current_build)}">Pin current build</button>`;
  $("#buildList").innerHTML = `<div><div><b>Installed build ${esc(result.current_build)}</b><span>${pin ? `Pinned by ${esc(result.pin.source)}` : "Automatic updates allowed"}</span></div><div></div><div class="row-actions">${currentControls}</div></div>`
    + (result.snapshots || []).map((snapshot) => `<div><div><b>Snapshot build ${esc(snapshot.build_id)}</b><span>${new Date(snapshot.created_at * 1000).toLocaleString()} · ${bytes(snapshot.bytes || 0)} · ${esc(snapshot.files || 0)} files</span></div><div class="integrity"><span class="good">MANIFEST</span><span class="good">CRITICAL HASHES</span></div><div class="row-actions"><button data-build-plan="${esc(snapshot.build_id)}">Plan rollback</button><button data-build-pin="${esc(snapshot.build_id)}">Pin</button></div></div>`).join("");
}

async function loadConfigHealth() {
  const data = await api("/api/v1/diagnostics/config");
  const record = (label, item, kind) => `<div><div><b>${esc(label)}</b><span>${item.exists === false ? "Missing" : (item.corrupted ? esc(item.reason) : "Structure valid")}</span></div><div class="integrity"><span class="${item.corrupted ? "bad" : "good"}">${item.corrupted ? "RECOVERY NEEDED" : "VALID"}</span></div><div class="row-actions">${item.corrupted || item.exists === false ? `<button data-config-recover="${kind}">Preserve & regenerate</button>` : ""}</div></div>`;
  $("#configHealth").innerHTML = record("PalWorldSettings.ini", data.world, "world") + record("Engine.ini", data.engine, "engine")
    + `<div><div><b>Environment file</b><span>${esc((data.environment.issues || []).join("; ") || "Syntax, required keys, and ports valid")}</span></div><div class="integrity"><span class="${data.environment.valid ? "good" : "bad"}">${data.environment.valid ? "VALID" : "HOST REPAIR"}</span></div><div></div></div>`
    + (data.world_options_conflicts || []).map((path) => `<div><div><b>WorldOptions conflict</b><span>${esc(path)} overrides managed world settings</span></div><div class="integrity"><span class="bad">CONFLICT</span></div><div class="row-actions"><button data-config-recover="world-options" data-config-target="${esc(path)}">Preserve & disable</button></div></div>`).join("");
}

async function loadEvents() {
  const events = await api("/api/v1/events?limit=100");
  $("#audit").innerHTML = events.slice(0, 40).map((event) => {
    const detail = event.player || event.message || event.phase || event.backup || event.type || "";
    return `<p>${new Date(event.timestamp * 1000).toLocaleString()} · ${esc(event.action)} · ${esc(event.result)}${detail ? ` · ${esc(detail)}` : ""}</p>`;
  }).join("") || '<p>No events recorded yet.</p>';
}

async function loadBackups() {
  const data = await api("/api/v1/backups");
  $("#backupList").innerHTML = data.backups.map((backup) => {
    const actions = can("admin") ? `<button data-backup-plan="${esc(backup.name)}">Plan restore</button><a class="button" href="/api/v1/backups/${encodeURIComponent(backup.name)}/download">Download</a><button data-backup-verify="${esc(backup.name)}">Verify</button><button class="danger" data-backup-quarantine="${esc(backup.name)}">Quarantine</button>` : "";
    return `<div><div><b>${esc(backup.name)}</b><span>${esc(backup.tier)} · ${bytes(backup.bytes)} · ${new Date(backup.created_at * 1000).toLocaleString()}</span></div><div class="integrity"><span class="${backup.checksum ? "good" : "bad"}">${backup.checksum ? "CHECKSUM" : "NO CHECKSUM"}</span><span class="${backup.manifest && Object.keys(backup.manifest).length ? "good" : "bad"}">${backup.manifest && Object.keys(backup.manifest).length ? "MANIFEST" : "NO MANIFEST"}</span></div><div class="row-actions">${actions}</div></div>`;
  }).join("") || '<p class="muted">No managed backups exist.</p>';
}

function chartPoints(samples, key, maximum) {
  if (!samples.length) return "";
  const first = samples[0].timestamp;
  const last = samples.at(-1).timestamp || first + 1;
  return samples.filter((sample) => Number.isFinite(sample[key])).map((sample) => {
    const x = (sample.timestamp - first) / (last - first || 1) * 1000;
    const y = 160 - Math.max(0, Math.min(sample[key] / maximum, 1)) * 140;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
}

async function loadTelemetry() {
  const hours = $("#telemetryHours").value;
  const data = await api(`/api/v1/metrics/history?hours=${encodeURIComponent(hours)}&limit=1000`);
  const samples = data.samples || [];
  const peakFps = Math.max(1, ...samples.map((sample) => sample.fps || 0));
  const peakFrame = Math.max(1, ...samples.map((sample) => sample.frame_ms || 0));
  const peakPlayers = Math.max(1, ...samples.map((sample) => sample.players || 0));
  const peakRss = Math.max(1, ...samples.map((sample) => sample.rss_bytes || 0));
  $("#fpsLine").setAttribute("points", chartPoints(samples, "fps", peakFps));
  $("#frameLine").setAttribute("points", chartPoints(samples, "frame_ms", peakFrame));
  $("#playerLine").setAttribute("points", chartPoints(samples, "players", peakPlayers));
  $("#rssLine").setAttribute("points", chartPoints(samples, "rss_bytes", peakRss));
  const latest = samples.at(-1) || {};
  $("#fpsNow").textContent = `${Number(latest.fps || 0).toFixed(1)} FPS`;
  $("#frameNow").textContent = `${Number(latest.frame_ms || 0).toFixed(1)} ms`;
  $("#playersNow").textContent = latest.players ?? 0;
  $("#rssNow").textContent = bytes(latest.rss_bytes || 0);
  $("#telemetrySummary").innerHTML = `<span><b>${samples.length}</b> retained points</span><span><b>${duration(latest.uptime || 0)}</b> world uptime</span><span><b>${latest.day ?? "—"}</b> world day</span><span><b>${latest.restarts ?? "—"}</b> restarts</span>`;
}

async function loadLogs() {
  if (logLoading || !role) return;
  logLoading = true;
  $("#logs").textContent = "Loading bounded journal…";
  try {
    const query = new URLSearchParams({
      unit: $("#logUnit").value,
      lines: $("#logLines").value,
      since_minutes: $("#logSince").value,
      filter: $("#logFilter").value,
    });
    const data = await api(`/api/v1/logs?${query}`);
    $("#logs").textContent = data.output || "No journal entries match this bounded view.";
    $("#refreshLogs").textContent = `${data.matched} matched · refreshed ${new Date().toLocaleTimeString()}`;
  } catch (error) {
    $("#logs").textContent = `${error.message}\nCheck that journald is available to the palworld service account.`;
  } finally {
    logLoading = false;
  }
}

async function loadConsole() {
  await refresh();
  const tasks = [loadBackups(), loadTelemetry(), loadLogs(), loadEvents(), loadPlayerHistory(), loadSaveIntelligence()];
  if (can("admin")) tasks.push(loadSchema(), loadRawSettings(), loadServerConfig(), loadModConfig(), loadModLifecycle(), loadBuilds(), loadRconAdmin(), loadFiles(), loadPlayerActions());
  await Promise.allSettled(tasks);
}

function joinedFilePath(base, name) {
  return base === "." ? name : `${base.replace(/\/$/, "")}/${name}`;
}

async function loadFiles() {
  if (!can("admin")) return;
  const query = new URLSearchParams({ root: $("#fileRoot").value, path: $("#filePath").value || "." });
  const data = await api(`/api/v1/files?${query}`);
  const current = data.path || ".";
  const parent = current === "." ? "" : current.split("/").slice(0, -1).join("/") || ".";
  $("#fileList").innerHTML = (parent ? `<div><div><b>Parent directory</b></div><button data-file-open="${esc(parent)}" data-file-kind="directory">Open</button></div>` : "") +
    (data.entries || []).map((entry) => `<div><div><b>${esc(entry.name)}</b><span>${esc(entry.kind)}${entry.bytes == null ? "" : ` · ${bytes(entry.bytes)}`}</span></div>${entry.kind === "directory" || entry.kind === "file" ? `<button data-file-open="${esc(joinedFilePath(current, entry.name))}" data-file-kind="${esc(entry.kind)}">${entry.kind === "directory" ? "Open" : "Read"}</button>` : ""}</div>`).join("") || '<p class="muted">Directory is empty.</p>';
}

async function readFileText(path) {
  const query = new URLSearchParams({ root: $("#fileRoot").value, path, read: "true" });
  const data = await api(`/api/v1/files?${query}`);
  selectedFilePath = data.path; $("#filePath").value = data.path; $("#fileEditor").value = data.content;
  say(`Loaded ${data.path} · ${data.bytes} bytes · ${data.sha256.slice(0, 12)}…`);
}

async function uploadManagedFile(path, blob) {
  const digest = [...new Uint8Array(await crypto.subtle.digest("SHA-256", await blob.arrayBuffer()))].map((byte) => byte.toString(16).padStart(2, "0")).join("");
  const query = new URLSearchParams({ root: $("#fileRoot").value, path });
  const response = await fetch(`/api/v1/files/upload?${query}`, { method: "PUT", body: blob, credentials: "same-origin", headers: {
    "X-CSRF-Token": csrf, "X-Content-SHA256": digest, "X-File-Confirmation": "UPLOAD FILE",
  }});
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw Error(data.error || data.output || `${response.status} ${response.statusText}`);
  say(`${path} installed atomically${data.preserved ? "; prior file preserved" : ""}. Restart separately if needed.`);
  await loadFiles();
}

document.addEventListener("click", async (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  try {
    if (button.dataset.action) {
      let body = {};
      if (button.dataset.action === "stop") {
        const confirmation = window.prompt("Gracefully stop the server? Type STOP SERVER.");
        if (confirmation !== "STOP SERVER") return say("Server stop was not confirmed.");
        body = { confirm: confirmation };
      }
      say((await api(`/api/v1/action/${button.dataset.action}`, { method: "POST", body: JSON.stringify(body) })).output || "Action completed.");
      if (["start", "stop", "restart"].includes(button.dataset.action)) await refresh();
    }
    if (button.dataset.mod) {
      const confirm = button.dataset.mod === "ban" ? "BAN PLAYER" : "";
      await api(`/api/v1/${button.dataset.mod}`, { method: "POST", body: JSON.stringify({ userid: button.dataset.id, confirm, message: "Operator action" }) });
      say(`${button.dataset.mod} completed.`);
    }
    if (button.dataset.cancel) await api(`/api/v1/jobs/${button.dataset.cancel}/cancel`, { method: "POST", body: "{}" });
    if (button.dataset.backupVerify) {
      const result = await api("/api/v1/backups/verify", { method: "POST", body: JSON.stringify({ name: button.dataset.backupVerify }) });
      say(result.output || "Backup verification passed.");
    }
    if (button.dataset.backupPlan) {
      const result = await api("/api/v1/backups/restore/plan", { method: "POST", body: JSON.stringify({ name: button.dataset.backupPlan }) });
      const plan = $("#restorePlan");
      plan.hidden = false;
      plan.innerHTML = `${esc(JSON.stringify(result.plan, null, 2))}\n\n<button data-backup-restore="${esc(button.dataset.backupPlan)}" class="danger">Execute this restore</button>`;
      plan.scrollIntoView({ block: "center" });
      say("Restore plan ready. Review every step before execution.");
    }
    if (button.dataset.backupRestore) {
      const confirmation = window.prompt(`Restore ${button.dataset.backupRestore}? Type RESTORE WORLD to execute.`);
      if (confirmation !== "RESTORE WORLD") return say("Restore was not confirmed.");
      say("Restore is running. Do not start another world mutation.");
      const result = await api("/api/v1/backups/restore", { method: "POST", body: JSON.stringify({ name: button.dataset.backupRestore, confirm: confirmation }) });
      say(result.output || "Restore completed and the world passed verification.");
      await loadBackups();
    }
    if (button.dataset.backupQuarantine) {
      if (!window.confirm(`Move ${button.dataset.backupQuarantine} and its sidecars into host quarantine?`)) return;
      await api("/api/v1/backups/quarantine", { method: "POST", body: JSON.stringify({ name: button.dataset.backupQuarantine, confirm: "QUARANTINE BACKUP" }) });
      say("Backup moved to quarantine.");
      await loadBackups();
    }
    if (button.dataset.updateInstall) {
      const confirmation = window.prompt(`Install reviewed Steam build ${button.dataset.updateInstall}? Type INSTALL UPDATE.`);
      if (confirmation !== "INSTALL UPDATE") return say("Update installation was not confirmed.");
      say("Protected update maintenance is running.");
      const result = await api("/api/v1/updates/install", { method: "POST", body: JSON.stringify({ target_build: button.dataset.updateInstall, confirm: confirmation }) });
      say(result.output || "Update maintenance completed and the world passed verification.");
      await refresh();
    }
    if (button.dataset.buildPlan) {
      const plan = await api("/api/v1/builds/plan", { method: "POST", body: JSON.stringify({ target_build: button.dataset.buildPlan }) });
      const panel = $("#updatePlan");
      panel.hidden = false;
      panel.innerHTML = `${esc(JSON.stringify(plan, null, 2))}\n\n<button class="danger" data-build-rollback="${esc(button.dataset.buildPlan)}">Roll back executable tree to ${esc(button.dataset.buildPlan)}</button>`;
      panel.scrollIntoView({ block: "center" });
    }
    if (button.dataset.buildRollback) {
      const confirmation = window.prompt(`Roll back executable files to build ${button.dataset.buildRollback} without changing saves? Type ROLLBACK BUILD.`);
      if (confirmation !== "ROLLBACK BUILD") return say("Build rollback was not confirmed.");
      say("Protected build rollback is running.");
      const result = await api("/api/v1/builds/rollback", { method: "POST", body: JSON.stringify({ target_build: button.dataset.buildRollback, confirm: confirmation }) });
      say(result.output || "Executable rollback completed, verified, and pinned.");
      await Promise.all([refresh(), loadBuilds()]);
    }
    if (button.dataset.buildPin) {
      const confirmation = window.prompt(`Pin Steam build ${button.dataset.buildPin}? Type PIN BUILD.`);
      if (confirmation !== "PIN BUILD") return say("Build pin was not confirmed.");
      await api("/api/v1/builds/pin", { method: "POST", body: JSON.stringify({ target_build: button.dataset.buildPin, confirm: confirmation }) });
      say(`Build ${button.dataset.buildPin} pinned; automatic updates are blocked.`);
      await loadBuilds();
    }
    if (button.dataset.buildUnpin) {
      const confirmation = window.prompt(`Resume automatic updates from pinned build ${button.dataset.buildUnpin}? Type UNPIN BUILD.`);
      if (confirmation !== "UNPIN BUILD") return say("Build was not unpinned.");
      await api("/api/v1/builds/unpin", { method: "POST", body: JSON.stringify({ confirm: confirmation }) });
      say("Build pin removed; automatic updates may resume.");
      await loadBuilds();
    }
    if (button.dataset.configRecover) {
      const confirmation = window.prompt("The world will stop and the current file will be preserved before recovery. Type RECOVER CONFIG.");
      if (confirmation !== "RECOVER CONFIG") return say("Configuration recovery was not confirmed.");
      const result = await api("/api/v1/diagnostics/config/recover", { method: "POST", body: JSON.stringify({ kind: button.dataset.configRecover, target: button.dataset.configTarget || "", confirm: confirmation }) });
      say(`${result.kind || button.dataset.configRecover} recovery completed; review it before starting the world.`);
      await loadConfigHealth();
    }
    if (button.dataset.rconRunSaved) {
      const confirmation = window.prompt("Execute this saved RCON command? Type EXECUTE RCON.");
      if (confirmation !== "EXECUTE RCON") return say("Saved command was not executed.");
      const result = await api(`/api/v1/rcon/saved/${button.dataset.rconRunSaved}/execute`, { method: "POST", body: JSON.stringify({ confirm: confirmation }) });
      say(result.output || "Saved command completed."); await loadRconAdmin();
    }
    if (button.dataset.rconDeleteSaved) {
      const confirmation = window.prompt("Delete this saved command? Type DELETE SAVED COMMAND.");
      if (confirmation !== "DELETE SAVED COMMAND") return say("Saved command was not deleted.");
      await api(`/api/v1/rcon/saved/${button.dataset.rconDeleteSaved}/delete`, { method: "POST", body: JSON.stringify({ confirm: confirmation }) });
      say("Saved command deleted."); await loadRconAdmin();
    }
    if (button.dataset.whitelistRemove !== undefined) {
      whitelistEntries.splice(Number(button.dataset.whitelistRemove), 1);
      renderWhitelist($("#toggleWhitelist").dataset.enabled === "true");
      say("Identity removed from the staged list; replace the reviewed list to persist.");
    }
    if (button.dataset.fileOpen) {
      if (button.dataset.fileKind === "directory") { $("#filePath").value = button.dataset.fileOpen; await loadFiles(); }
      else await readFileText(button.dataset.fileOpen);
    }
    if (button.dataset.luaMod) {
      const enabled = button.dataset.luaEnabled === "true";
      const confirmation = window.prompt(`${enabled ? "Enable" : "Disable"} Lua mod ${button.dataset.luaMod}? Type SET LUA MOD STATE.`);
      if (confirmation !== "SET LUA MOD STATE") return say("Lua mod state was not changed.");
      await api("/api/v1/mod-config/lua-state", { method: "POST", body: JSON.stringify({
        name: button.dataset.luaMod, enabled, confirm: confirmation,
      }) });
      say(`Lua mod ${button.dataset.luaMod} ${enabled ? "enabled" : "disabled"}; restart or supported reload remains explicit.`);
      await loadModConfig();
    }
    if (button.dataset.modLifecycleComponent) {
      await planModLifecycle(button.dataset.modLifecycleComponent, button.dataset.modLifecycleAction);
    }
    if (role) await refresh();
  } catch (error) { say(error.message); }
});

$("#connect").onclick = login;
$("#pair").onclick = pair;
$("#browseFiles").onclick = () => loadFiles().catch((error) => say(error.message));
$("#fileRoot").onchange = () => { $("#filePath").value = "."; selectedFilePath = ""; $("#fileEditor").value = ""; loadFiles().catch((error) => say(error.message)); };
$("#saveFileText").onclick = async () => {
  try {
    if (!selectedFilePath) return say("Select an allowlisted text file first.");
    const confirmation = window.prompt(`Replace ${selectedFilePath} after preserving it? Type UPLOAD FILE.`);
    if (confirmation !== "UPLOAD FILE") return say("File was not changed.");
    await uploadManagedFile(selectedFilePath, new Blob([$("#fileEditor").value], { type: "text/plain;charset=utf-8" }));
  } catch (error) { say(error.message); }
};
$("#quarantineFile").onclick = async () => {
  try {
    if (!selectedFilePath) return say("Select a regular file first.");
    const confirmation = window.prompt(`Move ${selectedFilePath} to reversible quarantine? Type QUARANTINE FILE.`);
    if (confirmation !== "QUARANTINE FILE") return say("File was not quarantined.");
    await api("/api/v1/files/quarantine", { method: "POST", body: JSON.stringify({ root: $("#fileRoot").value, path: selectedFilePath, confirm: confirmation }) });
    selectedFilePath = ""; $("#fileEditor").value = ""; $("#filePath").value = "."; say("File moved to host quarantine."); await loadFiles();
  } catch (error) { say(error.message); }
};
$("#fileUpload").onsubmit = async (event) => {
  event.preventDefault();
  try {
    const file = $("#uploadFile").files[0]; const path = $("#uploadPath").value.trim();
    const confirmation = window.prompt(`Upload ${file?.name || "file"} to ${path}? Type UPLOAD FILE.`);
    if (confirmation !== "UPLOAD FILE") return say("File was not uploaded.");
    await uploadManagedFile(path, file); event.target.reset();
  } catch (error) { say(error.message); }
};
$("#logout").onclick = logout;
$("#planSettings").onclick = () => settings("plan").catch((error) => say(error.message));
$("#applySettings").onclick = () => settings("apply").catch((error) => say(error.message));
$("#applyPreset").onclick = loadPreset;
$("#rollbackSettings").onclick = () => rollbackTypedSettings().catch((error) => say(error.message));
$("#planRawSettings").onclick = () => rawSettings("plan").catch((error) => say(error.message));
$("#applyRawSettings").onclick = () => rawSettings("apply").catch((error) => say(error.message));
$("#rollbackRawSettings").onclick = () => rollbackRawSettings().catch((error) => say(error.message));
$("#planServerConfig").onclick = () => serverConfig("plan").catch((error) => say(error.message));
$("#applyServerConfig").onclick = () => serverConfig("apply").catch((error) => say(error.message));
$("#rollbackServerConfig").onclick = () => rollbackServerConfig().catch((error) => say(error.message));
$("#loadServerConfigPreset").onclick = loadServerConfigPreset;
$("#planRawEngine").onclick = () => rawEngine("plan").catch((error) => say(error.message));
$("#applyRawEngine").onclick = () => rawEngine("apply").catch((error) => say(error.message));
$("#rollbackRawEngine").onclick = () => rollbackRawEngine().catch((error) => say(error.message));
$("#refreshModConfig").onclick = () => Promise.all([loadModConfig(), loadModLifecycle()]).catch((error) => say(error.message));
$("#planModConfig").onclick = () => modConfigAction("plan").catch((error) => say(error.message));
$("#applyModConfig").onclick = () => modConfigAction("apply").catch((error) => say(error.message));
$("#rollbackModConfig").onclick = () => rollbackModConfig().catch((error) => say(error.message));
$("#modConfigSearch").oninput = () => { captureModConfig(); renderModConfig(); };
$("#modConfigCategory").onchange = () => { captureModConfig(); renderModConfig(); };
$("#refreshBackups").onclick = () => loadBackups().catch((error) => say(error.message));
$("#loadPlayerHistory").onclick = () => loadPlayerHistory().catch((error) => say(error.message));
$("#refreshSaveIntelligence").onclick = () => loadSaveIntelligence().catch((error) => say(error.message));
$("#scanSaveIntelligence").onclick = () => scanSaveIntelligence().catch((error) => say(error.message));
$("#palSearch").oninput = renderSaveIntelligence;
$("#saveMap").onclick = (event) => {
  if (!saveMapBounds) return;
  const rect = $("#saveMap").getBoundingClientRect();
  const viewX = Math.max(30, Math.min(970, (event.clientX - rect.left) / rect.width * 1000));
  const viewY = Math.max(30, Math.min(490, (event.clientY - rect.top) / rect.height * 520));
  const x = saveMapBounds.minX + (viewX - 30) / 940 * (saveMapBounds.maxX - saveMapBounds.minX || 1);
  const y = saveMapBounds.minY + (490 - viewY) / 460 * (saveMapBounds.maxY - saveMapBounds.minY || 1);
  pickedCoordinate = { x: Math.round(x), y: Math.round(y), z: 0 };
  $("#playerActionX").value = pickedCoordinate.x; $("#playerActionY").value = pickedCoordinate.y; $("#playerActionZ").value = pickedCoordinate.z;
  $("#coordinatePick").textContent = `Picked X ${pickedCoordinate.x} · Y ${pickedCoordinate.y} · Z auto/0. Planning only; no player action was executed.`;
  $("#copyMapCoordinate").disabled = false;
  renderSaveMap(saveIntelligenceCache || {}, gameDataCache || {});
};
$("#copyMapCoordinate").onclick = async () => {
  if (!pickedCoordinate) return;
  try {
    await navigator.clipboard.writeText(`${pickedCoordinate.x} ${pickedCoordinate.y} ${pickedCoordinate.z}`);
    say("Picked coordinates copied. No player action was executed.");
  } catch { say(`Picked coordinates: ${pickedCoordinate.x} ${pickedCoordinate.y} ${pickedCoordinate.z}`); }
};
$("#playerActionType").onchange = renderPlayerActionFields;
$("#planPlayerAction").onclick = () => planTypedPlayerAction().catch((error) => say(error.message));
$("#executePlayerAction").onclick = () => executeTypedPlayerAction().catch((error) => say(error.message));
$("#saveRepairAction").onchange = invalidateSaveRepairPlan;
[$("#saveRepairWorld"), $("#saveRepairSource"), $("#saveRepairName"), $("#saveRepairTarget"), $("#saveRepairDays"),
  $("#saveRepairInventoryArea"), $("#saveRepairSlot"), $("#saveRepairItem"), $("#saveRepairStack"),
  $("#saveRepairLevel"), $("#saveRepairExperience"), $("#saveRepairPalInstance"), $("#saveRepairPalNickname"),
  $("#saveRepairPalRank"), $("#saveRepairTalentHp"), $("#saveRepairTalentAttack"),
  $("#saveRepairTalentDefense"), $("#saveRepairPassives")].forEach((input) => { input.oninput = invalidateSaveRepairPlan; });
$("#diagnoseSaveRepair").onclick = () => diagnoseSaveRepair().catch((error) => say(error.message));
$("#planSaveRepair").onclick = () => planSaveRepair().catch((error) => {
  invalidateSaveRepairPlan(); $("#saveRepairStatus").textContent = "Offline repair plan was blocked; the save was not changed."; say(error.message);
});
$("#applySaveRepair").onclick = () => applySaveRepair().catch((error) => say(error.message));
$("#playerHistorySearch").onkeydown = (event) => { if (event.key === "Enter") loadPlayerHistory().catch((error) => say(error.message)); };
$("#refreshTelemetry").onclick = () => loadTelemetry().catch((error) => say(error.message));
$("#refreshLogs").onclick = loadLogs;
$("#checkExposure").onclick = async () => {
  try {
    const result = await api("/api/v1/diagnostics/exposure/check", { method: "POST", body: "{}" });
    say(`Exposure check complete: ${result.public_game?.status || "local listeners checked"}.`);
    await refresh();
  } catch (error) { say(error.message); await refresh(); }
};
$("#checkConfig").onclick = () => loadConfigHealth().catch((error) => say(error.message));
$("#checkUpdate").onclick = () => updateControl("check").catch((error) => say(error.message));
$("#planUpdate").onclick = () => updateControl("plan").catch((error) => say(error.message));
$("#loadBuilds").onclick = () => loadBuilds().catch((error) => say(error.message));
$("#refreshRcon").onclick = () => loadRconAdmin().catch((error) => say(error.message));
$("#rconCatalog").onchange = () => { if ($("#rconCatalog").value) $("#rconCommand").value = $("#rconCatalog").value; };
$("#toggleRcon").onclick = async () => {
  try {
    const enabled = $("#toggleRcon").dataset.enabled === "true";
    const confirmation = enabled ? "DISABLE PRIVATE RCON" : "ENABLE PRIVATE RCON";
    const typed = window.prompt(`This takes a protected backup and restarts only with zero players online. Type ${confirmation}.`);
    if (typed !== confirmation) return say("RCON transition was not confirmed.");
    say("Protected RCON transition is running.");
    await api("/api/v1/rcon/toggle", { method: "POST", body: JSON.stringify({ enabled: !enabled, confirm: typed }) });
    say(`Private RCON ${enabled ? "disabled" : "enabled"}.`); await loadRconAdmin();
  } catch (error) { say(error.message); }
};
$("#applyWhitelist").onclick = async () => {
  try {
    const plan = await api("/api/v1/whitelist/plan", { method: "POST", body: JSON.stringify({ entries: whitelistEntries }) });
    const confirmation = window.prompt(`Replace the allow-list (${plan.before_count} → ${plan.after_count})? Type REPLACE WHITELIST.`);
    if (confirmation !== "REPLACE WHITELIST") return say("Allow-list was not replaced.");
    await api("/api/v1/whitelist/replace", { method: "POST", body: JSON.stringify({ entries: whitelistEntries, confirm: confirmation }) });
    say("Reviewed allow-list replaced."); await loadRconAdmin();
  } catch (error) { say(error.message); }
};
$("#toggleWhitelist").onclick = async () => {
  try {
    const enabled = $("#toggleWhitelist").dataset.enabled === "true";
    const action = enabled ? "disable" : "enable";
    const confirmation = `${action.toUpperCase()} WHITELIST`;
    const typed = window.prompt(`${enabled ? "Disable" : "Enable"} enforced player allow-list? Type ${confirmation}.`);
    if (typed !== confirmation) return say("Whitelist transition was not confirmed.");
    await api(`/api/v1/whitelist/${action}`, { method: "POST", body: JSON.stringify({ confirm: typed }) });
    say(`Whitelist ${action}d.`); await loadRconAdmin();
  } catch (error) { say(error.message); }
};
$("#telemetryHours").onchange = () => loadTelemetry().catch((error) => say(error.message));
$("#logFilter").onkeydown = (event) => { if (event.key === "Enter") loadLogs(); };
$("#logUnit").onchange = loadLogs;
$("#settingsSearch").oninput = () => { captureSettingValues(); renderSettings(); };
$("#settingsCategory").onchange = () => { captureSettingValues(); renderSettings(); };
$("#token").onkeydown = (event) => { if (event.key === "Enter") login(); };
$("#locale").onchange = (event) => setLocale(event.target.value);
$("#announce").onsubmit = async (event) => {
  event.preventDefault();
  try {
    await api("/api/v1/announce", { method: "POST", body: JSON.stringify({ message: new FormData(event.target).get("message") }) });
    event.target.reset(); say("Announcement sent.");
  } catch (error) { say(error.message); }
};
$("#rconExec").onsubmit = async (event) => {
  event.preventDefault();
  try {
    const confirmation = window.prompt("Execute this administrator command now? Type EXECUTE RCON.");
    if (confirmation !== "EXECUTE RCON") return say("RCON command was not executed.");
    const result = await api("/api/v1/rcon/execute", { method: "POST", body: JSON.stringify({ command: $("#rconCommand").value, confirm: confirmation }) });
    say(result.output || "RCON command completed."); await loadRconAdmin();
  } catch (error) { say(error.message); }
};
$("#rconSave").onsubmit = async (event) => {
  event.preventDefault();
  try {
    await api("/api/v1/rcon/saved", { method: "POST", body: JSON.stringify({ name: $("#rconSaveName").value, command: $("#rconCommand").value, confirm: "SAVE RCON COMMAND" }) });
    event.target.reset(); say("RCON command saved."); await loadRconAdmin();
  } catch (error) { say(error.message); }
};
$("#whitelistAdd").onsubmit = (event) => {
  event.preventDefault();
  const userId = $("#whitelistIdentity").value.trim();
  if (whitelistEntries.some((entry) => entry.user_id.toLowerCase() === userId.toLowerCase())) return say("That identity is already staged.");
  whitelistEntries.push({ user_id: userId, name: $("#whitelistName").value.trim() });
  event.target.reset(); renderWhitelist($("#toggleWhitelist").dataset.enabled === "true"); say("Identity staged; replace the reviewed list to persist.");
};
$("#job").onsubmit = async (event) => {
  event.preventDefault();
  const form = new FormData(event.target);
  try {
    const body = { type: form.get("type"), due_at: Math.floor(new Date(form.get("due_at")).getTime() / 1000), message: form.get("message") };
    if (body.type === "settings_event") {
      const confirmation = window.prompt("Schedule this automatically restored settings event? Type SCHEDULE TIMED EVENT.");
      if (confirmation !== "SCHEDULE TIMED EVENT") return say("Timed event was not scheduled.");
      Object.assign(body, { preset: form.get("preset"), duration_seconds: Number(form.get("duration_seconds")), confirm: confirmation });
    }
    if (body.type === "rcon") {
      const confirmation = window.prompt("Schedule this administrator command? Type SCHEDULE RCON COMMAND.");
      if (confirmation !== "SCHEDULE RCON COMMAND") return say("RCON command was not scheduled.");
      const savedId = form.get("saved_id");
      Object.assign(body, {
        command: savedId ? "" : form.get("message"), saved_id: savedId || null,
        interval_seconds: form.get("interval_seconds") || null, confirm: confirmation,
      });
    }
    await api("/api/v1/jobs", { method: "POST", body: JSON.stringify(body) });
    event.target.reset(); say("Scheduled work created."); await refresh();
  } catch (error) { say(error.message); }
};

$("#token").value = localStorage.getItem("palworld-token") || "";
loadLocales().finally(restoreSession);
setInterval(() => { if (role) refresh(); }, 10000);
setInterval(() => { if (role && $("#logLive").checked) loadLogs(); }, 5000);
