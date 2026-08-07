"use strict";

const state = { code: "", plan: null, polling: null };
const $ = (selector) => document.querySelector(selector);
const panels = {
  unlock: $("#unlockPanel"), configure: $("#configurePanel"),
  review: $("#reviewPanel"), install: $("#installPanel"),
};

function showStage(name) {
  Object.entries(panels).forEach(([key, panel]) => { panel.hidden = key !== name; });
  const order = ["unlock", "configure", "review", "install"];
  const current = order.indexOf(name);
  document.querySelectorAll("#routeSteps li").forEach((item, index) => {
    item.classList.toggle("active", index === current);
    item.classList.toggle("complete", index < current);
  });
  window.scrollTo({ top: 0, behavior: "auto" });
  panels[name].querySelector("h2")?.focus({ preventScroll: true });
}

function setConnection(label, ready = false) {
  const signal = $("#connectionState");
  signal.classList.toggle("ready", ready);
  signal.querySelector("span").textContent = label;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    cache: "no-store",
    headers: { "Content-Type": "application/json", "X-Bootstrap-Code": state.code, ...(options.headers || {}) },
  });
  const body = await response.json().catch(() => ({ error: "Bootstrap returned an unreadable response" }));
  if (!response.ok) throw new Error(body.error || `Request failed with status ${response.status}`);
  return body;
}

function errorAt(selector, error) { $(selector).textContent = error ? error.message || String(error) : ""; }

$("#unlockForm").addEventListener("submit", async (event) => {
  event.preventDefault(); errorAt("#unlockError");
  state.code = $("#launchCode").value.trim();
  try {
    await api("/api/v1/bootstrap/status");
    $("#launchCode").value = "";
    setConnection("Terminal proof accepted · loopback session", true);
    showStage("configure");
  } catch (error) {
    state.code = ""; errorAt("#unlockError", error);
  }
});

function formPayload() {
  const data = new FormData($("#configForm"));
  const payload = Object.fromEntries(data.entries());
  payload.mode = document.querySelector('input[name="mode"]:checked').value;
  ["game_port", "rest_port", "ops_port", "rcon_port"].forEach((key) => { payload[key] = Number(payload[key]); });
  payload.player_exp_rate = Number(payload.player_exp_rate);
  return payload;
}

function appendCard(parent, title, content, className = "") {
  const card = document.createElement("section"); card.className = `plan-card ${className}`.trim();
  const heading = document.createElement("h3"); heading.textContent = title; card.appendChild(heading);
  const body = document.createElement("p"); body.textContent = content; card.appendChild(body); parent.appendChild(card);
  return card;
}

function renderPlan(plan) {
  const root = $("#planSummary"); root.replaceChildren();
  const mode = plan.mode === "adopt" ? "Adopt existing server" : "Install new server";
  appendCard(root, mode, `${plan.configuration.server_name} · UDP ${plan.configuration.game_port} · ${plan.configuration.install_dir}`);
  const changes = document.createElement("section"); changes.className = "plan-card";
  const heading = document.createElement("h3"); heading.textContent = "Bound changes"; changes.appendChild(heading);
  const list = document.createElement("ul"); plan.changes.forEach((change) => { const item = document.createElement("li"); item.textContent = change; list.appendChild(item); });
  changes.appendChild(list); root.appendChild(changes);
  if (plan.adoption) {
    const players = plan.adoption.worlds.reduce((total, world) => total + world.players, 0);
    appendCard(root, "Reviewed existing tree", `Build ${plan.adoption.build_id} · ${plan.adoption.worlds.length} world(s) · ${players} player save(s) · fingerprint ${plan.adoption.fingerprint}`);
  }
  if (plan.missing_prerequisites.length) {
    appendCard(root, "Execution blocked", `Install these host prerequisites, then build a new plan: ${plan.missing_prerequisites.join(", ")}`, "blocked");
  } else {
    appendCard(root, "Host prerequisites", "Required commands and the configured SteamCMD executable are present.");
  }
  $("#executeButton").disabled = plan.missing_prerequisites.length > 0;
  $("#regenerateSecrets").disabled = false;
  $("#confirmationHint").textContent = `Type ${plan.confirmation} exactly.`;
  $("#confirmation").value = "";
}

function randomSecret() {
  const bytes = new Uint8Array(32); crypto.getRandomValues(bytes);
  return btoa(String.fromCharCode(...bytes)).replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
}
function generateSecrets() { $("#adminPassword").value = randomSecret(); $("#opsToken").value = randomSecret(); }

$("#configForm").addEventListener("submit", async (event) => {
  event.preventDefault(); errorAt("#configError");
  try {
    state.plan = await api("/api/v1/bootstrap/plan", { method: "POST", body: JSON.stringify(formPayload()) });
    renderPlan(state.plan); generateSecrets(); showStage("review");
  } catch (error) { errorAt("#configError", error); }
});

$("#regenerateSecrets").addEventListener("click", generateSecrets);
document.querySelectorAll("[data-copy]").forEach((button) => button.addEventListener("click", async () => {
  const input = document.getElementById(button.dataset.copy);
  await navigator.clipboard.writeText(input.value); button.textContent = "Copied";
  setTimeout(() => { button.textContent = "Copy"; }, 1400);
}));
$("#editPlan").addEventListener("click", () => { state.plan = null; showStage("configure"); });

$("#executeForm").addEventListener("submit", async (event) => {
  event.preventDefault(); errorAt("#executeError");
  const payload = {
    plan_id: state.plan.plan_id,
    confirmation: $("#confirmation").value,
    admin_password: $("#adminPassword").value,
    ops_token: $("#opsToken").value,
  };
  try {
    await api("/api/v1/bootstrap/execute", { method: "POST", body: JSON.stringify(payload) });
    $("#adminPassword").value = ""; $("#opsToken").value = ""; $("#confirmation").value = "";
    showStage("install"); setConnection("Reviewed installation running", true); pollStatus();
  } catch (error) { errorAt("#executeError", error); }
});

async function pollStatus() {
  clearTimeout(state.polling);
  try {
    const status = await api("/api/v1/bootstrap/status");
    $("#installLog").textContent = status.output || "Waiting for installer output…";
    $("#installLog").scrollTop = $("#installLog").scrollHeight;
    panels.install.classList.toggle("complete", status.phase === "complete");
    panels.install.classList.toggle("failed", status.phase === "failed");
    if (status.phase === "complete") {
      $("#installHeading").textContent = "The world is under management.";
      $("#installMessage").textContent = "The installer completed successfully and the reviewed services were enabled.";
      $("#completion").hidden = false; setConnection("Bootstrap complete", true); return;
    }
    if (status.phase === "failed") {
      $("#installHeading").textContent = "Installation stopped before completion.";
      $("#installMessage").textContent = "Review the latest output, correct the host condition, then retry the same bound plan while this process remains open.";
      $("#installError").textContent = status.error || "Installer failed";
      $("#retryInstall").hidden = false; setConnection("Install failed · review output"); return;
    }
    state.polling = setTimeout(pollStatus, 1500);
  } catch (error) { $("#installError").textContent = error.message; state.polling = setTimeout(pollStatus, 3000); }
}

$("#retryInstall").addEventListener("click", () => {
  $("#retryInstall").hidden = true; panels.install.classList.remove("failed");
  $("#installError").textContent = "Return to the reviewed plan and re-enter the same credentials you saved before the first attempt.";
  $("#adminPassword").value = ""; $("#opsToken").value = ""; $("#regenerateSecrets").disabled = true; showStage("review");
});
