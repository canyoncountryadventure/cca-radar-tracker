const DATA_ROOT = "data";
const TIME_ZONE = "America/Denver";
const $ = (id) => document.getElementById(id);

const classes = {
  full_flush: { rank: 4, short: "Full flush", mark: "◆", css: "full-flush" },
  likely_full: { rank: 3, short: "Likely full", mark: "●", css: "likely-full" },
  moderate: { rank: 2, short: "Moderate", mark: "◐", css: "moderate" },
  minor: { rank: 1, short: "Minor", mark: "○", css: "minor" },
  legacy_spatial_trigger: { rank: 2, short: "Legacy trigger", mark: "!", css: "moderate" },
  no_data: { rank: 0, short: "No event", mark: "—", css: "no-data" },
};

let state = { status: null, model: null, watersheds: null, selected: "zerog" };

function localDate(value) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("en-US", {
    timeZone: TIME_ZONE, month: "short", day: "numeric", year: "numeric",
    hour: "numeric", minute: "2-digit", timeZoneName: "short",
  }).format(new Date(value));
}

function integer(value) {
  return value === null || value === undefined ? "—" : Math.round(value).toLocaleString("en-US");
}

function fixed(value, digits = 2) {
  return value === null || value === undefined ? "—" : Number(value).toFixed(digits);
}

function selectedCanyon() { return state.status.canyons[state.selected]; }
function selectedModel() { return state.model.canyons[state.selected]; }
function selectedFeature() { return state.watersheds.features.find((f) => f.properties.id === state.selected); }

function latestClassification(canyon) {
  const event = canyon.last_rain_event;
  return event?.classification || "no_data";
}

function eventAge(event) {
  if (!event?.end_utc) return "No rain event recorded";
  const days = Math.floor((Date.now() - new Date(event.end_utc)) / 86400000);
  if (days <= 0) return "Today";
  if (days === 1) return "1 day ago";
  return `${days} days ago`;
}

function renderOverview() {
  const canyons = Object.values(state.status.canyons).sort((a, b) => {
    const rank = classes[latestClassification(b)].rank - classes[latestClassification(a)].rank;
    return rank || a.name.localeCompare(b.name);
  });
  const counts = { full_flush: 0, likely_full: 0, moderate: 0, minor: 0, no_data: 0 };
  canyons.forEach((canyon) => { counts[latestClassification(canyon)] = (counts[latestClassification(canyon)] || 0) + 1; });
  $("overview-summary").innerHTML = `<b>${counts.full_flush + counts.likely_full}</b> likely full · <b>${counts.moderate}</b> moderate · <b>${counts.no_data}</b> awaiting rain`;
  $("canyon-grid").innerHTML = canyons.map((canyon) => {
    const event = canyon.last_rain_event;
    const meta = classes[latestClassification(canyon)];
    const ratio = event?.fill_ratio === null || event?.fill_ratio === undefined ? "—" : `${fixed(event.fill_ratio, 2)}× target`;
    return `<button class="canyon-card ${meta.css} ${canyon.id === state.selected ? "selected" : ""}" data-canyon="${canyon.id}">
      <span class="card-state">${meta.mark} ${meta.short}</span>
      <strong>${canyon.name}</strong>
      <span>${eventAge(event)}</span>
      <small>${ratio}</small>
    </button>`;
  }).join("");
  document.querySelectorAll(".canyon-card").forEach((button) => button.addEventListener("click", () => {
    state.selected = button.dataset.canyon;
    $("canyon-select").value = state.selected;
    renderOverview(); renderDetail();
    document.querySelector(".detail-shell").scrollIntoView({ behavior: "smooth", block: "start" });
  }));
}

function radarColor(value) {
  if (value === null || value < 10) return "#0b1114";
  if (value < 20) return "#183f64";
  if (value < 30) return "#14715b";
  if (value < 40) return "#42a84a";
  if (value < 50) return "#ead943";
  if (value < 55) return "#f29b38";
  if (value < 60) return "#ef594f";
  return "#ec64cf";
}

function drawRadar(event, geometry) {
  const canvas = $("radar-canvas");
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = "#0b1114"; ctx.fillRect(0, 0, canvas.width, canvas.height);
  const grid = event?.peak_grid_dbz || event?.grid_dbz;
  const bbox = event?.grid_bbox;
  if (!grid?.length || !bbox) {
    ctx.fillStyle = "#7e888d"; ctx.font = "600 20px system-ui"; ctx.textAlign = "center";
    ctx.fillText("No rain-event radar frame recorded", canvas.width / 2, canvas.height / 2);
    return;
  }
  const rows = grid.length, columns = grid[0].length;
  const cw = canvas.width / columns, ch = canvas.height / rows;
  grid.forEach((row, r) => row.forEach((value, c) => {
    ctx.fillStyle = radarColor(value);
    ctx.fillRect(c * cw, r * ch, Math.ceil(cw) + 1, Math.ceil(ch) + 1);
  }));
  const [left, bottom, right, top] = bbox;
  const project = ([lon, lat]) => [(lon - left) / (right - left) * canvas.width, (top - lat) / (top - bottom) * canvas.height];
  const polygons = geometry.type === "Polygon" ? [geometry.coordinates] : geometry.coordinates;
  ctx.strokeStyle = "#fff"; ctx.lineWidth = 4; ctx.shadowColor = "#000"; ctx.shadowBlur = 7;
  polygons.forEach((polygon) => polygon.forEach((ring) => {
    ctx.beginPath(); ring.forEach((point, i) => { const [x, y] = project(point); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
    ctx.closePath(); ctx.stroke();
  }));
  ctx.shadowBlur = 0;
}

function coverageText(event) {
  if (!event?.peak_coverage_percent) return "Coverage unavailable";
  return `Peak coverage: 50+ dBZ ${event.peak_coverage_percent["50"] || 0}%, 55+ ${event.peak_coverage_percent["55"] || 0}%, 60+ ${event.peak_coverage_percent["60"] || 0}%`;
}

function eventHtml(event, emptyText) {
  if (!event) return `<strong>${emptyText}</strong><p>The tracker will preserve the date and complete calculation after one occurs.</p>`;
  return `<strong>${localDate(event.start_utc)}</strong>
    <p>${event.classification_label || "Radar trigger"}</p>
    <dl><div><dt>End</dt><dd>${localDate(event.end_utc)}</dd></div>
    <div><dt>Basin rain</dt><dd>${event.basin_rain_inches == null ? "Legacy event" : `${fixed(event.basin_rain_inches, 3)} in`}</dd></div>
    <div><dt>Runoff</dt><dd>${event.estimated_runoff_ft3 == null ? "Not calculated" : `${integer(event.estimated_runoff_ft3)} ft³`}</dd></div>
    <div><dt>Peak</dt><dd>${event.peak_dbz ?? "—"} dBZ</dd></div></dl>
    <small>${coverageText(event)}</small>`;
}

function renderRuleTable(model, event) {
  const actual = event?.peak_covered_area_sq_mi || {};
  $("rule-table").innerHTML = `<div class="table-wrap"><table><thead><tr><th>Intensity</th><th>Required area</th><th>Required %</th><th>Last event peak</th></tr></thead><tbody>${model.spatial_rules.map((rule) => `<tr>
    <td>${rule.dbz}+ dBZ</td><td>${fixed(rule.minimum_area_sq_mi, 3)} mi²</td><td>${fixed(rule.minimum_coverage_percent, 2)}%</td>
    <td>${actual[String(Math.round(rule.dbz))] == null ? "—" : `${fixed(actual[String(Math.round(rule.dbz))], 3)} mi²`}</td></tr>`).join("")}</tbody></table></div>`;
}

function renderAtlas(model) {
  const periods = ["1", "2", "5", "10", "25"];
  const durations = ["5-min", "10-min", "15-min", "30-min", "60-min"];
  $("atlas-table").innerHTML = `<table><thead><tr><th>Duration</th>${periods.map((p) => `<th>${p}-yr</th>`).join("")}</tr></thead><tbody>${durations.map((d) => `<tr><td>${d}</td>${periods.map((p) => `<td>${fixed(model.atlas14_inches[d][p], 3)}″</td>`).join("")}</tr>`).join("")}</tbody></table>`;
}

function renderMethodology() {
  const method = state.model.method;
  $("methodology").innerHTML = `<h4>Equations used</h4><ul>
    <li><b>Radar rainfall:</b> ${method.rainfall_formula}</li>
    <li><b>Delivered runoff:</b> ${method.runoff_formula}</li>
    <li><b>Fill target:</b> ${method.target_formula}</li>
    <li><b>Intensity gates:</b> ${method.spatial_formula}</li>
    <li><b>Scaling basis:</b> ${method.scaling_basis}</li></ul>
    <h4>Classification rules</h4><ul>${Object.values(method.classification).map((value) => `<li>${value}</li>`).join("")}</ul>
    <h4>Primary sources</h4><ul>${method.sources.map((source) => `<li><a href="${source.url}" target="_blank" rel="noopener">${source.label}</a></li>`).join("")}</ul>
    <h4>Known limitations</h4><ul>${method.limitations.map((value) => `<li>${value}</li>`).join("")}</ul>`;
}

function renderDetail() {
  const canyon = selectedCanyon(), model = selectedModel(), feature = selectedFeature();
  const event = canyon.last_rain_event, qualifying = canyon.last_qualifying_event;
  const key = latestClassification(canyon), meta = classes[key];
  const banner = $("condition-banner");
  banner.className = `condition-banner ${meta.css}`;
  $("condition-mark").textContent = meta.mark;
  $("condition-label").textContent = event?.classification_label || "No rain event recorded since Version 2 monitoring began";
  $("condition-detail").textContent = event ? `Last event: ${localDate(event.start_utc)}. ${coverageText(event)}.` : "The model is active and waiting for a measurable storm.";
  $("metric-rain").textContent = event?.basin_rain_inches == null ? "—" : fixed(event.basin_rain_inches, 3);
  $("metric-runoff").textContent = event?.estimated_runoff_ft3 == null ? "—" : integer(event.estimated_runoff_ft3);
  $("metric-target").textContent = integer(model.fill_target_ft3);
  $("metric-ratio").textContent = event?.fill_ratio == null ? "—" : `${fixed(event.fill_ratio, 2)}×`;
  $("metric-dbz").textContent = event?.peak_dbz ?? "—";
  $("metric-duration").textContent = event?.wet_frames ? `${event.wet_frames * 5} min` : "—";
  $("metric-atlas").textContent = event?.atlas14_return_period_years ? `~${event.atlas14_return_period_years} yr` : "—";
  $("metric-area").textContent = fixed(canyon.area_sq_mi, 3);
  $("radar-time").textContent = event?.peak_frame_utc ? localDate(event.peak_frame_utc) : "No rain event recorded";
  drawRadar(event || canyon.latest_analysis, feature.geometry);
  $("rain-event").innerHTML = eventHtml(event, "No rain event recorded");
  $("qualifying-event").innerHTML = eventHtml(qualifying, "No qualifying storm recorded");
  $("calculation-title").textContent = `${canyon.name} calculation`;
  $("calibration-badge").textContent = model.calibration;
  $("calibration-badge").className = `calibration-badge ${model.calibration.startsWith("field") ? "field" : ""}`;
  $("calculation-summary").innerHTML = `<p><b>${fixed(canyon.area_sq_mi, 3)} mi² watershed</b> × area scale <b>${fixed(model.scale_factor, 4)}</b> produces a likely-fill target of <b>${integer(model.fill_target_ft3)} ft³</b> and a full-flush target of <b>${integer(model.flush_target_ft3)} ft³</b>.</p>`;
  $("event-equation").innerHTML = event ? `<code>${integer(event.radar_rain_volume_ft3)} ft³ radar rain × ${(model.runoff_coefficient * 100).toFixed(0)}% = ${integer(event.estimated_runoff_ft3)} ft³ delivered runoff</code><code>${integer(event.estimated_runoff_ft3)} ÷ ${integer(model.fill_target_ft3)} = ${fixed(event.fill_ratio, 2)}× target</code>` : `<code>Waiting for a rain event to populate the calculation.</code>`;
  renderRuleTable(model, event); renderAtlas(model); renderMethodology();
}

async function load() {
  try {
    const stamp = Date.now();
    const [statusResponse, modelResponse, watershedResponse] = await Promise.all([
      fetch(`${DATA_ROOT}/status.json?v=${stamp}`, { cache: "no-store" }),
      fetch(`${DATA_ROOT}/model.json?v=${stamp}`, { cache: "no-store" }),
      fetch(`${DATA_ROOT}/watersheds.geojson`, { cache: "force-cache" }),
    ]);
    if (![statusResponse, modelResponse, watershedResponse].every((response) => response.ok)) throw new Error("One or more dashboard data files could not be loaded");
    state.status = await statusResponse.json(); state.model = await modelResponse.json(); state.watersheds = await watershedResponse.json();
    const names = Object.values(state.status.canyons).sort((a, b) => a.name.localeCompare(b.name));
    $("canyon-select").innerHTML = names.map((canyon) => `<option value="${canyon.id}">${canyon.name}</option>`).join("");
    $("canyon-select").value = state.selected;
    $("canyon-select").addEventListener("change", (event) => { state.selected = event.target.value; renderOverview(); renderDetail(); });
    const health = state.status.health || { ok: false, message: "Unknown tracker status" };
    $("health-dot").classList.add(health.ok ? "ok" : "bad");
    $("last-check").textContent = state.status.last_checked_utc ? `Updated ${localDate(state.status.last_checked_utc)}` : health.message;
    $("health-dot").title = health.message;
    renderOverview(); renderDetail();
  } catch (error) {
    $("health-dot").classList.add("bad"); $("last-check").textContent = "Dashboard data unavailable";
    $("condition-label").textContent = "Tracker unavailable"; $("condition-detail").textContent = error.message;
  }
}

load();
