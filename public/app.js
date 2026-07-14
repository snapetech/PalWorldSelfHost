const $ = (selector) => document.querySelector(selector);
const NS = "http://www.w3.org/2000/svg";
const layers = {
  players: true,
  fastTravelPoint: false,
  towerTravelPoint: false,
};
let scale = 1;
let locations = [];

function duration(seconds) {
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  return days ? `${days}d ${hours}h` : `${hours}h ${minutes}m`;
}

// Projection constants from ARXII-13/Palworld-Interactive-Map.
function position(worldX, worldY) {
  const mapX = (worldY - 157935) / 459;
  const mapY = -((worldX + 123930) / 459);
  return {
    x: ((8192 / 3149) * mapX + 5075.45) / 8.192,
    // Leaflet latitude is bottom-origin; SVG/image pixels are top-origin.
    y: 1000 - ((-8192 / 3136) * mapY + 4960.62) / 8.192,
  };
}

function svgNode(tag, attributes = {}) {
  const node = document.createElementNS(NS, tag);
  Object.entries(attributes).forEach(([key, value]) =>
    node.setAttribute(key, value),
  );
  return node;
}

function drawLocations() {
  const group = $("#locations");
  group.replaceChildren();
  locations
    .filter((item) => layers[item.type])
    .forEach((item) => {
      const at = position(item.location.X, item.location.Y);
      const node = svgNode("g", {
        class: `location ${item.type === "towerTravelPoint" ? "tower" : "travel"}`,
        transform: `translate(${at.x} ${at.y})`,
      });
      const dot = svgNode("circle", {
        r: item.type === "towerTravelPoint" ? 5 : 3,
      });
      const title = svgNode("title");
      title.textContent = item.label;
      node.append(dot, title);
      group.appendChild(node);
    });
}

function drawPlayers(players) {
  const group = $("#markers");
  group.replaceChildren();
  $("#roster").replaceChildren();
  $("#empty").hidden = players.length > 0;
  players.forEach((player) => {
    if (
      layers.players &&
      Number.isFinite(player.location_x) &&
      Number.isFinite(player.location_y)
    ) {
      const at = position(player.location_x, player.location_y);
      const node = svgNode("g", {
        class: "marker",
        transform: `translate(${at.x} ${at.y})`,
      });
      const dot = svgNode("circle", { r: 10 });
      const label = svgNode("text", { x: 17, y: 7 });
      label.textContent = player.name || "Explorer";
      node.append(dot, label);
      group.appendChild(node);
    }
    const item = document.createElement("li");
    const name = document.createElement("b");
    const detail = document.createElement("span");
    name.textContent = player.name || "Explorer";
    detail.textContent = `LV ${player.level ?? "—"} · ${Math.round(player.ping ?? 0)} ms`;
    item.append(name, detail);
    $("#roster").appendChild(item);
  });
}

function setScale(next) {
  scale = Math.max(1, Math.min(3, next));
  $("#mapContent").style.width = `${scale * 100}%`;
  if (scale === 1) $(".map-wrap").scrollTo(0, 0);
}
function drawHistory(samples) {
  const points=(samples||[]).filter(x=>Number.isFinite(x.players));
  if(!points.length){$("#historyLine").setAttribute("points","");return}
  const min=points[0].timestamp,max=points.at(-1).timestamp||min+1,peak=Math.max(1,...points.map(x=>x.players));
  $("#historyLine").setAttribute("points",points.map(x=>`${(x.timestamp-min)/(max-min||1)*1000},${170-x.players/peak*150}`).join(" "));
}

async function refresh() {
  try {
    const response = await fetch("/palworld/status.json", {
      cache: "no-store",
    });
    const data = await response.json();
    $(".signal").classList.toggle("online", data.online);
    $("#state").textContent = data.online ? "WORLD ONLINE" : "WORLD OFFLINE";
    $("#players").textContent = data.player_count;
    $("#capacity").textContent = `/ ${data.max_players}`;
    $("#uptime").textContent = duration(data.uptime);
    $("#maintenance").textContent = data.next_maintenance || "—";
    $("#day").textContent = data.day ?? "—";
    $("#serverName").textContent = data.name || "Palworld server";
    $("#updated").textContent =
      `Signal updated ${new Date(data.generated_at * 1000).toLocaleTimeString()}`;
    drawHistory(data.history);
    const last=data.maintenance_result||{};
    $("#lastMaintenance").textContent=last.result?`Last maintenance: ${last.result}${last.finished_at?` · ${new Date(last.finished_at*1000).toLocaleString()}`:""}`:"";
    drawPlayers(data.players || []);
  } catch {
    $("#state").textContent = "SIGNAL LOST";
  }
}

document.querySelectorAll("[data-layer]").forEach((input) => {
  input.addEventListener("change", () => {
    layers[input.dataset.layer] = input.checked;
    if (input.dataset.layer === "players")
      $("#markers").style.display = input.checked ? "" : "none";
    else drawLocations();
  });
});
$("#zoomIn").addEventListener("click", () => setScale(scale + 0.5));
$("#zoomOut").addEventListener("click", () => setScale(scale - 0.5));
$("#resetMap").addEventListener("click", () => setScale(1));

fetch("/palworld/locations.json")
  .then((response) => response.json())
  .then((data) => {
    locations = data;
    drawLocations();
  });
refresh();
setInterval(refresh, 15000);
