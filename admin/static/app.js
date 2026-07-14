const $ = (selector) => document.querySelector(selector);
const fmt = (number) => new Intl.NumberFormat().format(number || 0);

function age(seconds) {
  if (seconds == null) return "unknown";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} minutes ago`;
  return `${(seconds / 3600).toFixed(1)} hours ago`;
}

async function refresh() {
  try {
    const response = await fetch("/api/status", {cache: "no-store"});
    const data = await response.json();
    const metrics = data.metrics || {};
    const settings = data.settings || {};
    $("#seal").textContent = data.service === "active" ? "WORLD ONLINE" : "WORLD OFFLINE";
    $("#seal").style.background = data.service === "active" ? "var(--amber)" : "var(--fault)";
    $("#players").textContent = fmt(metrics.currentplayernum);
    $("#fps").textContent = metrics.serverfps ?? "—";
    $("#frame").textContent = metrics.serverframetime ? `${metrics.serverframetime.toFixed(1)} ms` : "—";
    $("#day").textContent = metrics.days ?? "—";
    $("#uptime").textContent = metrics.uptime ? `${(metrics.uptime / 3600).toFixed(1)} h` : "—";
    $("#xp").textContent = settings.ExpRate ? `${settings.ExpRate * 100}%` : "50%";
    $("#death").textContent = settings.DeathPenalty ?? "None";
    $("#pulseText").textContent = `${metrics.serverfps ?? 0} Hz world pulse · ${metrics.currentplayernum ?? 0}/${metrics.maxplayernum ?? 32} inhabitants`;
    if (data.backup) {
      $("#backup").textContent = data.backup.name;
      $("#backupMeta").textContent = `${age(data.backup.age_seconds)} · ${(data.backup.bytes / 1048576).toFixed(1)} MiB`;
    }
    $("#disk").style.width = `${100 - (data.disk.free / data.disk.total * 100)}%`;
    $("#maintenance").textContent = `Next maintenance · ${data.next_maintenance}`;
    $("#updated").textContent = `Updated ${new Date().toLocaleTimeString()}`;
  } catch (error) {
    $("#seal").textContent = "CONTROL PLANE LOST";
    $("#seal").style.background = "var(--fault)";
  }
}

document.querySelectorAll("button").forEach((button) => {
  button.onclick = async () => {
    const response = await fetch(`/api/action/${button.dataset.action}`, {
      method: "POST",
      headers: {"X-Palworld-Ops-Token": $("#token").value},
    });
    const data = await response.json();
    $("#result").textContent = data.output || data.error || "Action completed";
    refresh();
  };
});

refresh();
setInterval(refresh, 10000);
