const DATA_ROOT = "data";
const TIME_ZONE = "America/Denver";
const $ = (id) => document.getElementById(id);

const classes = {
  full_flush: { rank: 4, short: "Full flush", mark: "◆", css: "full-flush", score: 100, plain: "Strong evidence that the pools fully refilled and the canyon received completely new water." },
  likely_full: { rank: 3, short: "Likely full", mark: "●", css: "likely-full", score: 78, plain: "The storm met both the water-volume and heavy-rain tests; substantial or complete refill is likely." },
  moderate: { rank: 2, short: "Partial refill", mark: "◐", css: "moderate", score: 48, plain: "Some new water is possible, but the evidence is not strong enough to call the pools full." },
  minor: { rank: 1, short: "Little change", mark: "○", css: "minor", score: 16, plain: "The modeled storm was too small or too gentle to expect a meaningful change in pool depth." },
  legacy_spatial_trigger: { rank: 2, short: "Earlier trigger", mark: "!", css: "moderate", score: 48, plain: "This event came from the earlier ZeroG trigger method and does not have every current calculation." },
  no_data: { rank: 0, short: "No event", mark: "—", css: "no-data", score: 0, plain: "No measurable rain event has been recorded for this watershed yet." },
};

let state = { status: null, model: null, watersheds: null, selected: "zerog" };
let map;
let radarLayer;
let watershedLayer;

function localDate(value) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("en-US", {
    timeZone: TIME_ZONE, month: "short", day: "numeric", year: "numeric",
    hour: "numeric", minute: "2-digit", timeZoneName: "short",
  }).format(new Date(value));
}

function localTime(value) {
  return new Intl.DateTimeFormat("en-US", { timeZone: TIME_ZONE, hour: "numeric", minute: "2-digit" }).format(new Date(value));
}

function integer(value) { return value == null ? "—" : Math.round(value).toLocaleString("en-US"); }
function fixed(value, digits = 2) { return value == null ? "—" : Number(value).toFixed(digits); }
function selectedCanyon() { return state.status.canyons[state.selected]; }
function selectedModel() { return state.model.canyons[state.selected]; }
function selectedFeature() { return state.watersheds.features.find((feature) => feature.properties.id === state.selected); }
function latestClassification(canyon) { return canyon.last_rain_event?.classification || "no_data"; }

function eventAge(event) {
  if (!event?.end_utc) return "No recorded rain event";
  const now = new Date();
  const end = new Date(event.end_utc);
  const dateParts = (date) => new Intl.DateTimeFormat("en-CA", { timeZone: TIME_ZONE, year: "numeric", month: "2-digit", day: "2-digit" }).format(date);
  if (dateParts(now) === dateParts(end)) return `Today · ${localTime(event.end_utc)}`;
  const days = Math.floor((now - end) / 86400000);
  if (days === 1) return `Yesterday · ${localTime(event.end_utc)}`;
  return `${days} days ago`;
}

function renderOverview() {
  const canyons = Object.values(state.status.canyons).sort((a, b) => {
    const rank = classes[latestClassification(b)].rank - classes[latestClassification(a)].rank;
    return rank || a.name.localeCompare(b.name);
  });
  const counts = { full_flush: 0, likely_full: 0, moderate: 0, minor: 0, no_data: 0 };
  canyons.forEach((canyon) => { counts[latestClassification(canyon)] = (counts[latestClassification(canyon)] || 0) + 1; });
  $("overview-summary").innerHTML = `<b>${counts.full_flush + counts.likely_full}</b> likely full · <b>${counts.moderate}</b> possible partial refill · <b>${counts.minor}</b> little change`;
  $("canyon-grid").innerHTML = canyons.map((canyon) => {
    const event = canyon.last_rain_event;
    const meta = classes[latestClassification(canyon)];
    const ratio = event?.fill_ratio == null ? "—" : `${fixed(event.fill_ratio, 2)}× estimated fill ratio`;
    return `<button class="canyon-card ${meta.css} ${canyon.id === state.selected ? "selected" : ""}" data-canyon="${canyon.id}" data-tooltip="${meta.plain}">
      <span class="card-state">${meta.mark} ${meta.short}</span><strong>${canyon.name}</strong>
      <span>${eventAge(event)}</span><small>${ratio}</small>
      <span class="likelihood-track" aria-hidden="true"><i style="width:${meta.score}%"></i></span>
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
  if (value == null || value < 10) return "transparent";
  if (value < 20) return "#183f64";
  if (value < 30) return "#14715b";
  if (value < 40) return "#42a84a";
  if (value < 50) return "#ead943";
  if (value < 55) return "#f29b38";
  if (value < 60) return "#ef594f";
  return "#ec64cf";
}

function initializeMap() {
  if (map || typeof L === "undefined") return;
  const topo = L.tileLayer("https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png", {
    maxZoom: 17, attribution: "Map © OpenTopoMap contributors",
  });
  const satellite = L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", {
    maxZoom: 19, attribution: "Imagery © Esri and contributors",
  });
  map = L.map("radar-map", { layers: [topo], zoomControl: true, preferCanvas: true });
  radarLayer = L.layerGroup().addTo(map);
  watershedLayer = L.layerGroup().addTo(map);
  L.control.layers({ "Topographic": topo, "Satellite": satellite }, { "Radar pixels": radarLayer, "Watershed": watershedLayer }, { collapsed: false }).addTo(map);
}

function drawRadarMap(event, feature) {
  initializeMap();
  if (!map) return;
  radarLayer.clearLayers(); watershedLayer.clearLayers();
  const outline = L.geoJSON(feature, { style: { color: "#fff", weight: 4, opacity: 1, fillOpacity: 0.03 } }).addTo(watershedLayer);
  const grid = event?.peak_grid_dbz || event?.grid_dbz;
  const bbox = event?.grid_bbox;
  if (grid?.length && bbox) {
    const [left, bottom, right, top] = bbox;
    const rows = grid.length, columns = grid[0].length;
    const cellWidth = (right - left) / columns, cellHeight = (top - bottom) / rows;
    grid.forEach((row, r) => row.forEach((value, c) => {
      if (value == null || value < 10) return;
      const color = radarColor(value);
      L.rectangle([[top - (r + 1) * cellHeight, left + c * cellWidth], [top - r * cellHeight, left + (c + 1) * cellWidth]], {
        stroke: true, color, weight: 0.45, opacity: 0.9, fillColor: color, fillOpacity: 0.62,
      }).bindTooltip(`${value} dBZ`).addTo(radarLayer);
    }));
  }
  map.fitBounds(outline.getBounds(), { padding: [24, 24] });
  setTimeout(() => map.invalidateSize(), 0);
}

function coverageText(event) {
  if (!event?.peak_coverage_percent) return "Heavy-rain coverage unavailable";
  return `Peak watershed coverage: 50+ dBZ ${event.peak_coverage_percent["50"] || 0}%, 55+ ${event.peak_coverage_percent["55"] || 0}%, 60+ ${event.peak_coverage_percent["60"] || 0}%`;
}

function eventHtml(event, emptyText) {
  if (!event) return `<strong>${emptyText}</strong><p>The date and full calculation will remain here after one occurs.</p>`;
  return `<strong>${localDate(event.start_utc)}</strong><p>${event.classification_label || "Radar trigger"}</p>
    <dl><div><dt>End</dt><dd>${localDate(event.end_utc)}</dd></div>
    <div><dt>Basin-average rain</dt><dd>${event.basin_rain_inches == null ? "Earlier method" : `${fixed(event.basin_rain_inches, 3)} in`}</dd></div>
    <div><dt>Delivered runoff</dt><dd>${event.estimated_runoff_ft3 == null ? "Not calculated" : `${integer(event.estimated_runoff_ft3)} ft³`}</dd></div>
    <div><dt>1-hour equivalent</dt><dd>${event.delivered_runoff_one_hour_cfs == null ? "—" : `${fixed(event.delivered_runoff_one_hour_cfs, 2)} cfs`}</dd></div>
    <div><dt>Peak radar</dt><dd>${event.peak_dbz ?? "—"} dBZ</dd></div>
    <div><dt>Fill ratio</dt><dd>${event.fill_ratio == null ? "—" : `${fixed(event.fill_ratio, 2)}×`}</dd></div></dl>
    <small>${coverageText(event)}</small>`;
}

function renderRuleTable(model, event) {
  const actual = event?.peak_covered_area_sq_mi || {};
  $("rule-table").innerHTML = `<p class="plain-note">A heavy-rain footprint is a reality check: enough of the watershed must receive intense rain before the model calls the pools full.</p><div class="table-wrap"><table><thead><tr><th>Intensity</th><th>Required area</th><th>Required %</th><th>Last event peak</th></tr></thead><tbody>${model.spatial_rules.map((rule) => `<tr>
    <td>${rule.dbz}+ dBZ</td><td>${fixed(rule.minimum_area_sq_mi, 3)} mi²</td><td>${fixed(rule.minimum_coverage_percent, 2)}%</td>
    <td>${actual[String(Math.round(rule.dbz))] == null ? "—" : `${fixed(actual[String(Math.round(rule.dbz))], 3)} mi²`}</td></tr>`).join("")}</tbody></table></div>`;
}

function renderAtlas(model) {
  const periods = ["1", "2", "5", "10", "25"];
  const durations = ["5-min", "10-min", "15-min", "30-min", "60-min"];
  $("atlas-table").innerHTML = `<table><thead><tr><th>Duration</th>${periods.map((period) => `<th>${period}-yr</th>`).join("")}</tr></thead><tbody>${durations.map((duration) => `<tr><td>${duration}</td>${periods.map((period) => `<td>${fixed(model.atlas14_inches[duration][period], 3)}″</td>`).join("")}</tr>`).join("")}</tbody></table>`;
}

function renderMethodology() {
  const method = state.model.method;
  $("methodology").innerHTML = `<h4>Equations used</h4><ul>
    <li><b>Radar rainfall:</b> ${method.rainfall_formula}. Reflectivity is logarithmic: Z = 10^(dBZ/10), and solving the NWS Z–R relationship gives rainfall rate R.</li>
    <li><b>Why cap it:</b> Very high reflectivity can represent hail rather than proportionally greater rain, so the rainfall conversion stops increasing at 55 dBZ. The original dBZ remains visible for the heavy-rain test.</li>
    <li><b>Delivered runoff:</b> ${method.runoff_formula}. ${method.runoff_coefficient_explanation}</li>
    <li><b>Fill target:</b> ${method.target_formula}. ${method.target_explanation}</li>
    <li><b>Estimated fill ratio:</b> ${method.fill_ratio_explanation}</li>
    <li><b>Heavy-rain footprint:</b> ${method.spatial_formula}. ${method.spatial_explanation}</li>
    <li><b>Atlas 14:</b> ${method.atlas_explanation}</li>
    <li><b>Scaling basis:</b> ${method.scaling_basis}</li></ul>
    <h4>Classification rules</h4><ul>${Object.values(method.classification).map((value) => `<li>${value}</li>`).join("")}</ul>
    <h4>Primary sources</h4><ul>${method.sources.map((source) => `<li><a href="${source.url}" target="_blank" rel="noopener">${source.label}</a></li>`).join("")}</ul>
    <h4>Known limitations</h4><ul>${method.limitations.map((value) => `<li>${value}</li>`).join("")}</ul>`;
}

function atlasLabel(event) {
  const years = event?.atlas14_return_period_years;
  if (years == null) return "—";
  if (years >= 1000) return "≥1,000 yr eq.";
  if (years < 1) return "<1 yr eq.";
  return `~${fixed(years, 1)} yr eq.`;
}

function renderDetail() {
  const canyon = selectedCanyon(), model = selectedModel(), feature = selectedFeature();
  const event = canyon.last_rain_event, qualifying = canyon.last_qualifying_event;
  const key = latestClassification(canyon), meta = classes[key];
  const banner = $("condition-banner"); banner.className = `condition-banner ${meta.css}`;
  $("condition-mark").textContent = meta.mark;
  $("condition-label").textContent = event?.classification_label || "No measurable rain event recorded";
  $("condition-detail").textContent = event ? `Last event: ${localDate(event.start_utc)}. ${meta.plain}` : meta.plain;
  $("metric-rain").textContent = event?.basin_rain_inches == null ? "—" : fixed(event.basin_rain_inches, 3);
  $("metric-runoff").textContent = event?.estimated_runoff_ft3 == null ? "—" : integer(event.estimated_runoff_ft3);
  $("metric-runoff-cfs").textContent = event?.delivered_runoff_one_hour_cfs == null ? "—" : fixed(event.delivered_runoff_one_hour_cfs, 2);
  $("metric-target").textContent = integer(model.fill_target_ft3);
  $("metric-target-cfs").textContent = fixed(model.fill_target_ft3 / 3600, 2);
  $("metric-ratio").textContent = event?.fill_ratio == null ? "—" : `${fixed(event.fill_ratio, 2)}×`;
  $("metric-dbz").textContent = event?.peak_dbz ?? "—";
  $("metric-duration").textContent = event?.wet_frames ? `${event.wet_frames * 5} min` : "—";
  $("metric-atlas").textContent = atlasLabel(event);
  $("metric-area").textContent = fixed(canyon.area_sq_mi, 3);
  $("radar-time").textContent = event?.peak_frame_utc ? localDate(event.peak_frame_utc) : "Watershed map";
  drawRadarMap(event || canyon.latest_analysis, feature);
  const archive = $("iem-archive-link"); archive.href = event?.iem_archive_url || "https://mesonet.agron.iastate.edu/current/mcview.phtml";
  archive.textContent = event ? "Open this exact date and time in the IEM archived radar viewer ↗" : "Open the IEM radar archive ↗";
  $("rain-event").innerHTML = eventHtml(event, "No measurable rain event recorded");
  $("qualifying-event").innerHTML = eventHtml(qualifying, "No likely-full storm recorded");
  $("calculation-title").textContent = `${canyon.name} calculation`;
  $("calibration-badge").textContent = model.calibration;
  $("calibration-badge").className = `calibration-badge ${model.calibration.startsWith("field") ? "field" : ""}`;
  $("calculation-summary").innerHTML = `<p><b>${fixed(canyon.area_sq_mi, 3)} mi² watershed</b> × area scale <b>${fixed(model.scale_factor, 4)}</b> produces a likely-fill target of <b>${integer(model.fill_target_ft3)} ft³</b>—equivalent to <b>${fixed(model.fill_target_ft3 / 3600, 2)} cfs for one hour</b>—and a full-flush target of <b>${integer(model.flush_target_ft3)} ft³</b>.</p>`;
  $("event-equation").innerHTML = event ? `<code>${integer(event.radar_rain_volume_ft3)} ft³ radar rain × ${(model.runoff_coefficient * 100).toFixed(0)}% provisional delivery = ${integer(event.estimated_runoff_ft3)} ft³ delivered runoff</code><code>${integer(event.estimated_runoff_ft3)} ÷ ${integer(model.fill_target_ft3)} = ${fixed(event.fill_ratio, 2)}× estimated fill ratio</code><code>${fixed(event.basin_rain_inches, 3)}″ basin-average rain over ${event.atlas14_duration_minutes || event.wet_frames * 5} minutes ≈ ${atlasLabel(event)} Atlas 14 equivalent</code>` : `<code>Waiting for a measurable rain event to populate the calculation.</code>`;
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
