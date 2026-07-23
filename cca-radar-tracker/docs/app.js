"use strict";

const DATA_ROOT = "data";
const TIME_ZONE = "America/Denver";
const BLUE = "#3f98ff";
const BLUE_BRIGHT = "#73b8ff";

const $ = (id) => document.getElementById(id);

const conditionMeta = {
  full_flush: { rank: 4, mark: "◆", css: "full_flush", short: "Strong flush likely" },
  likely_full: { rank: 3, mark: "●", css: "likely_full", short: "Major refill likely" },
  moderate: { rank: 2, mark: "◐", css: "moderate", short: "Refill possible" },
  minor: { rank: 1, mark: "○", css: "minor", short: "No meaningful refill indicated" },
  legacy_spatial_trigger: { rank: 2, mark: "!", css: "moderate", short: "Earlier radar trigger" },
  none: { rank: 0, mark: "·", css: "none", short: "No rain event recorded" },
};

const app = {
  model: null,
  status: null,
  watersheds: null,
  selectedId: null,
  map: null,
  watershedLayer: null,
  radarLayer: null,
  layersById: new Map(),
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function number(value, digits = 0) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "—";
  return numeric.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function compactNumber(value, digits = 1) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "—";
  if (Math.abs(numeric) >= 1_000_000) return `${number(numeric / 1_000_000, digits)}M`;
  if (Math.abs(numeric) >= 10_000) return `${number(numeric / 1_000, digits)}k`;
  return number(numeric, 0);
}

function dateTime(value) {
  if (!value) return "Not available";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "Not available";
  return new Intl.DateTimeFormat("en-US", {
    timeZone: TIME_ZONE,
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  }).format(parsed);
}

function dateOnly(value) {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "—";
  return new Intl.DateTimeFormat("en-US", {
    timeZone: TIME_ZONE,
    month: "short",
    day: "numeric",
    year: "numeric",
  }).format(parsed);
}

function eventDuration(event) {
  if (!event) return 0;
  if (Number.isFinite(Number(event.storm_duration_minutes))) return Number(event.storm_duration_minutes);
  if (event.start_utc && event.end_utc) {
    const minutes = Math.round((new Date(event.end_utc) - new Date(event.start_utc)) / 60_000) + 5;
    return Math.max(5, minutes);
  }
  return Math.max(0, Number(event.frames || 0) * 5);
}

function atlasText(event) {
  const years = Number(event?.atlas14_return_period_years);
  if (!Number.isFinite(years) || years <= 0) return "Not available";
  if (years < 1) return "<1 yr equivalent";
  if (years >= 1000) return "≥1,000 yr equivalent";
  return `${number(years, years < 10 ? 1 : 0)} yr equivalent`;
}

function rangeText(values, unit, digits = 0) {
  if (!values || typeof values !== "object") return `— ${unit}`;
  const low = Number(values.dry);
  const high = Number(values.wet);
  if (!Number.isFinite(low) || !Number.isFinite(high)) return `— ${unit}`;
  return `${number(low, digits)}–${number(high, digits)} ${unit}`;
}

function directRunoff(event) {
  return Number(event?.direct_runoff_ft3 ?? event?.estimated_runoff_ft3 ?? 0);
}

function directRunoffRange(event) {
  return event?.direct_runoff_ft3_range ?? event?.estimated_runoff_ft3_range ?? null;
}

function routedPeak(event) {
  return Number(event?.routed_peak_cfs ?? event?.estimated_peak_cfs ?? 0);
}

function routedPeakRange(event) {
  return event?.routed_peak_cfs_range ?? event?.estimated_peak_cfs_range ?? null;
}

function eventCondition(event) {
  if (!event) return conditionMeta.none;
  return conditionMeta[event.classification] || conditionMeta.none;
}

function modifierText(model) {
  const modifier = Number(model.pothole_modifier || 0);
  const percent = Math.round(Math.abs(modifier) * 100);
  if (modifier === 0) return "Same rate as Zero G";
  return `${percent}% ${modifier > 0 ? "higher" : "lower"} than Zero G`;
}

function fetchJson(path) {
  const separator = path.includes("?") ? "&" : "?";
  return fetch(`${path}${separator}v=${Date.now()}`, { cache: "no-store" }).then((response) => {
    if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
    return response.json();
  });
}

function canyonStatus(id) {
  return app.status?.canyons?.[id] || {};
}

function selectedModel() {
  return app.model?.canyons?.[app.selectedId] || null;
}

function selectedStatus() {
  return canyonStatus(app.selectedId);
}

function setHealth() {
  const health = app.status?.health || {};
  const pill = $("health-pill");
  pill.textContent = health.message || "Radar status unavailable";
  pill.className = `health-pill ${health.ok === false ? "bad" : "ok"}`;
  const checked = app.status?.last_checked_utc || app.status?.latest_frame_utc;
  $("last-updated").textContent = checked ? `Last checked ${dateTime(checked)}` : "";
}

function populateSelect() {
  const select = $("canyon-select");
  select.innerHTML = "";
  Object.entries(app.model.canyons)
    .sort(([, a], [, b]) => a.name.localeCompare(b.name))
    .forEach(([id, canyon]) => {
      const option = document.createElement("option");
      option.value = id;
      option.textContent = canyon.name;
      select.append(option);
    });
  select.addEventListener("change", () => selectCanyon(select.value, true));
}

function renderSummary() {
  const container = $("canyon-summary");
  const rows = Object.entries(app.model.canyons).map(([id, model]) => {
    const event = canyonStatus(id).last_rain_event;
    const meta = eventCondition(event);
    return { id, model, event, meta };
  });
  rows.sort((a, b) => b.meta.rank - a.meta.rank || a.model.name.localeCompare(b.model.name));

  container.innerHTML = rows.map(({ id, model, event, meta }) => `
    <button type="button" class="summary-row ${meta.css} ${id === app.selectedId ? "selected" : ""}" data-canyon-id="${escapeHtml(id)}">
      <span class="summary-mark">${meta.mark}</span>
      <span>
        <span class="summary-name">${escapeHtml(model.name)}</span>
        <span class="summary-condition">${escapeHtml(event?.classification_label || meta.short)}</span>
      </span>
      <span class="summary-date">${event ? dateOnly(event.start_utc) : "—"}</span>
    </button>
  `).join("");

  container.querySelectorAll("[data-canyon-id]").forEach((button) => {
    button.addEventListener("click", () => selectCanyon(button.dataset.canyonId, true));
  });
}

function watershedStyle(feature) {
  const selected = feature.properties.id === app.selectedId;
  return {
    color: selected ? BLUE_BRIGHT : BLUE,
    weight: selected ? 4 : 2,
    opacity: selected ? 1 : 0.92,
    fillColor: BLUE,
    fillOpacity: selected ? 0.25 : 0.10,
  };
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
  const topo = L.tileLayer("https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png", {
    maxZoom: 17,
    attribution: "Map © OpenTopoMap contributors",
  });
  const satellite = L.tileLayer(
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    { maxZoom: 19, attribution: "Imagery © Esri and contributors" },
  );

  app.map = L.map("map", { layers: [topo], zoomControl: true, preferCanvas: true });
  app.radarLayer = L.layerGroup().addTo(app.map);
  app.watershedLayer = L.geoJSON(app.watersheds, {
    style: watershedStyle,
    onEachFeature(feature, layer) {
      const id = feature.properties.id;
      app.layersById.set(id, layer);
      layer.bindTooltip(feature.properties.name, { sticky: true });
      layer.on({
        click: () => selectCanyon(id, true),
        mouseover: () => layer.setStyle({ color: BLUE_BRIGHT, weight: 4, fillOpacity: 0.20 }),
        mouseout: () => app.watershedLayer.resetStyle(layer),
      });
    },
  }).addTo(app.map);

  L.control.layers(
    { Topographic: topo, Satellite: satellite },
    { "Peak-event radar pixels": app.radarLayer, "Watershed polygons": app.watershedLayer },
    { collapsed: true },
  ).addTo(app.map);

  const bounds = app.watershedLayer.getBounds();
  if (bounds.isValid()) app.map.fitBounds(bounds.pad(0.05));
}

function drawSelectedRadar() {
  if (!app.radarLayer) return;
  app.radarLayer.clearLayers();
  const status = selectedStatus();
  const event = status.last_rain_event || status.latest_analysis;
  const grid = event?.peak_grid_dbz || event?.grid_dbz;
  const bbox = event?.grid_bbox;
  const time = event?.peak_frame_utc || event?.timestamp_utc || event?.end_utc;
  $("radar-time").textContent = time
    ? `Selected event radar: ${dateTime(time)}`
    : "No retained radar grid for the selected canyon";

  if (!Array.isArray(grid) || !grid.length || !Array.isArray(grid[0]) || !bbox) return;
  const [left, bottom, right, top] = bbox.map(Number);
  const rows = grid.length;
  const columns = grid[0].length;
  const cellWidth = (right - left) / columns;
  const cellHeight = (top - bottom) / rows;

  grid.forEach((row, rowIndex) => {
    row.forEach((rawValue, columnIndex) => {
      const value = Number(rawValue);
      if (!Number.isFinite(value) || value < 10) return;
      const color = radarColor(value);
      L.rectangle(
        [
          [top - (rowIndex + 1) * cellHeight, left + columnIndex * cellWidth],
          [top - rowIndex * cellHeight, left + (columnIndex + 1) * cellWidth],
        ],
        {
          stroke: true,
          color,
          weight: 0.45,
          opacity: 0.9,
          fillColor: color,
          fillOpacity: 0.62,
          interactive: true,
        },
      ).bindTooltip(`${number(value, 1)} dBZ`).addTo(app.radarLayer);
    });
  });
}

function updateMapSelection(fit = false) {
  if (!app.watershedLayer) return;
  app.watershedLayer.setStyle(watershedStyle);
  const layer = app.layersById.get(app.selectedId);
  if (fit && layer) app.map.fitBounds(layer.getBounds().pad(0.35), { maxZoom: 11 });
  if (layer) layer.bringToFront();
  drawSelectedRadar();
  setTimeout(() => app.map.invalidateSize(), 0);
}

function renderCondition(model, event) {
  const meta = eventCondition(event);
  const banner = $("condition-banner");
  banner.className = `condition-banner ${meta.css}`;
  $("condition-icon").textContent = meta.mark;
  $("condition-title").textContent = event?.classification_label || "No rain event recorded";
  $("condition-kicker").textContent = event ? "LAST RAIN EVENT MODEL RESULT" : "MODEL STATUS";
  $("condition-copy").textContent = event
    ? `${dateTime(event.start_utc)}. ${event.classification_explanation || "Model result is provisional and has not been field verified."}`
    : "No completed radar rain event has been retained for this canyon yet.";
}

function metricCard(label, value, note) {
  return `
    <article class="metric-card">
      <div class="metric-label">${escapeHtml(label)}</div>
      <div class="metric-value">${escapeHtml(value)}</div>
      <div class="metric-note">${escapeHtml(note)}</div>
    </article>
  `;
}

function renderMetrics(model, event) {
  const runoff = directRunoff(event);
  const runoffRange = directRunoffRange(event);
  const peakRange = routedPeakRange(event);
  const normalIa = Number(model.hydrology?.initial_abstraction_inches?.normal);
  const rain = Number(event?.basin_rain_inches || 0);
  const zeroReason = event && runoff === 0 && Number.isFinite(normalIa) && rain <= normalIa
    ? `rain did not exceed ${number(normalIa, 3)} in initial abstraction`
    : "normal antecedent-condition estimate";

  const modifier = Number(model.pothole_modifier || 0);
  const modifierValue = modifier === 0 ? "0%" : `${modifier > 0 ? "+" : "−"}${number(Math.abs(modifier) * 100, 0)}%`;

  const cards = [
    metricCard("Basin-average radar rain", event ? `${number(event.basin_rain_inches, 3)} in` : "—", "area-weighted event accumulation"),
    metricCard("Estimated watershed runoff", event ? `${compactNumber(runoff, 1)} ft³` : "—", event ? `${rangeText(runoffRange, "ft³", 0)} dry–wet; ${zeroReason}` : "NRCS direct-runoff estimate; not measured canyon delivery"),
    metricCard("Routed peak flow — context", event ? rangeText(peakRange, "cfs", 2) : "—", "dry–wet screening range; not a fill trigger by itself"),
    metricCard("Estimated empty-pool storage", `${number(model.fill_target_ft3, 0)} ft³`, "estimated empty pool/pothole storage"),
    metricCard("Storage-fill ratio", event ? `${number(event.fill_ratio || 0, 2)}×` : "—", "normal-condition watershed runoff ÷ empty-storage target"),
    metricCard("Technical section", `${number(model.technical_length_miles, 2)} mi`, `${number(model.length_ratio_to_zerog, 2)}× Zero G length`),
    metricCard("Pothole-storage adjustment", modifierValue, `${modifierText(model)} per technical mile`),
    metricCard("Peak radar", event?.peak_dbz != null ? `${number(event.peak_dbz, 1)} dBZ` : "—", "maximum reflectivity inside watershed"),
    metricCard("Storm duration", event ? `${number(eventDuration(event), 0)} min` : "—", `${number(event?.wet_frames || 0, 0)} wet five-minute frames`),
    metricCard("Drainage area", `${number(model.area_sq_mi, 3)} mi²`, "used for runoff volume, not pool-storage scaling"),
  ];
  $("metrics-grid").innerHTML = cards.join("");
}

function eventMeta(label, value) {
  return `<div class="event-meta"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`;
}

function coverageText(event) {
  if (!event?.peak_coverage_percent) return "Peak watershed coverage unavailable.";
  const p = event.peak_coverage_percent;
  return `Peak watershed coverage: 50+ dBZ ${number(p["50"] || 0, 1)}%, 55+ ${number(p["55"] || 0, 1)}%, 60+ ${number(p["60"] || 0, 1)}%.`;
}

function renderEventCard(event, title, emptyText) {
  if (!event) {
    return `
      <p class="section-kicker">${escapeHtml(title)}</p>
      <h3>${escapeHtml(emptyText)}</h3>
      <p class="empty-state">The model will retain the date and calculation here after a qualifying event is recorded.</p>
    `;
  }
  const runoff = directRunoff(event);
  const peak = routedPeak(event);
  const decision = event.decision_tests || {};
  return `
    <p class="section-kicker">${escapeHtml(title)}</p>
    <h3>${escapeHtml(dateTime(event.start_utc))}</h3>
    <p class="event-result"><strong>${escapeHtml(event.classification_label || "Modeled rain event")}</strong><br>${escapeHtml(event.classification_explanation || "Model classification explanation unavailable.")}</p>
    <div class="event-meta-grid">
      ${eventMeta("End", dateTime(event.end_utc))}
      ${eventMeta("Basin-average rain", `${number(event.basin_rain_inches, 3)} in`)}
      ${eventMeta("Atlas 14 context", atlasText(event))}
      ${eventMeta("Estimated watershed runoff", `${number(runoff, 0)} ft³`)}
      ${eventMeta("Routed peak — context", `${number(peak, 2)} cfs`)}
      ${eventMeta("Peak radar", `${number(event.peak_dbz, 1)} dBZ`)}
      ${eventMeta("Fill ratio", `${number(event.fill_ratio || 0, 2)}×`)}
      ${eventMeta("Heavy-rain footprint", (decision.heavy_rain_footprint_met ?? event.spatial_gate_seen) ? "Passed" : "Not reached")}
    </div>
    <p class="event-coverage">${escapeHtml(coverageText(event))}</p>
    ${event.iem_archive_url ? `<a class="event-link" href="${escapeHtml(event.iem_archive_url)}" target="_blank" rel="noopener">Open archived radar animation</a>` : ""}
  `;
}

function renderEvents(status) {
  $("last-rain-event").innerHTML = renderEventCard(
    status.last_rain_event,
    "LAST RAIN EVENT",
    "No rain event recorded"
  );
  $("last-major-event").innerHTML = renderEventCard(
    status.last_qualifying_event,
    "LAST MAJOR REFILL EVENT — RETAINED",
    "No likely-full or strong-flush event recorded"
  );
}

function initialAbstraction(curveNumber) {
  const cn = Number(curveNumber);
  if (!Number.isFinite(cn) || cn <= 0) return null;
  return 0.2 * (1000 / cn - 10);
}

function renderStorageCalculation(model) {
  const multiplier = Number(model.storage_rate_multiplier);
  const modifier = Number(model.pothole_modifier);
  $("storage-calculation").innerHTML = `
    <p class="formula">52,442 ft³ × (${number(model.technical_length_miles, 2)} mi ÷ 0.75 mi) × ${number(multiplier, 2)} = <strong>${number(model.fill_target_ft3, 0)} ft³</strong></p>
    <p class="formula-note">The ${number(multiplier, 2)} multiplier equals 1 + (${modifier >= 0 ? "+" : ""}${number(modifier, 2)}). That represents ${escapeHtml(modifierText(model))}. The target is provisional empty storage, not observed current water volume.</p>
  `;
}

function renderHydrologyCalculation(model, event) {
  const container = $("hydrology-calculation");
  if (!event) {
    container.innerHTML = `<div class="calc-line">No completed rain event is available.</div>`;
    return;
  }
  const hydro = model.hydrology || {};
  const cn = Number(hydro.curve_number?.normal);
  const abstraction = Number(hydro.initial_abstraction_inches?.normal ?? initialAbstraction(cn));
  const rain = Number(event.basin_rain_inches || 0);
  const runoff = directRunoff(event);
  const runoffDepth = Number(event.runoff_depth_inches?.normal || 0);
  const comparison = Number.isFinite(abstraction)
    ? (rain <= abstraction
      ? `${number(rain, 3)} in ≤ ${number(abstraction, 3)} in, so normal-condition direct runoff = 0`
      : `${number(rain, 3)} in > ${number(abstraction, 3)} in, so the NRCS runoff equation is applied`)
    : "Initial abstraction unavailable";

  container.innerHTML = [
    `${number(rain, 3)} in basin-average radar rain over ${number(eventDuration(event), 0)} minutes`,
    `Normal curve number ${number(cn, 1)}; initial abstraction ${number(abstraction, 3)} in`,
    comparison,
    `Runoff depth ${number(runoffDepth, 4)} in; estimated watershed runoff ${number(runoff, 0)} ft³`,
    `${rangeText(directRunoffRange(event), "ft³", 0)} dry–wet watershed-runoff range`,
    `${rangeText(routedPeakRange(event), "cfs", 2)} routed peak-flow range`,
    `${number(runoff, 0)} ÷ ${number(model.fill_target_ft3, 0)} = ${number(event.fill_ratio || 0, 2)}× storage-fill ratio`,
    `${atlasText(event)} Atlas 14 context from watershed-average radar rain`,
  ].map((line) => `<div class="calc-line">${escapeHtml(line)}</div>`).join("");
}

function decisionRow(pass, title, detail) {
  return `
    <div class="decision-row ${pass ? "pass" : "fail"}">
      <span class="decision-symbol">${pass ? "✓" : "×"}</span>
      <span><strong>${escapeHtml(title)}</strong><span>${escapeHtml(detail)}</span></span>
    </div>
  `;
}

function renderDecision(model, event) {
  const container = $("decision-calculation");
  if (!event) {
    container.innerHTML = decisionRow(false, "No event to classify", "A completed radar rain event is required.");
    return;
  }
  const tests = event.decision_tests || {};
  const ratio = Number(event.fill_ratio || 0);
  const minimumFrames = Number(tests.minimum_wet_frames_required || 2);
  container.innerHTML = [
    decisionRow(Boolean(tests.storage_target_met ?? ratio >= 1), "Empty-storage volume test", `${number(ratio, 2)}×; likely-full threshold is 1.00×`),
    decisionRow(Boolean(tests.flush_target_met ?? ratio >= 2), "Strong-flush volume test", `${number(ratio, 2)}×; strong-flush threshold is 2.00×`),
    decisionRow(Boolean(tests.heavy_rain_footprint_met ?? event.spatial_gate_seen), "Intense-rain footprint", "Any one fixed dBZ/coverage gate must pass"),
    decisionRow(Boolean(tests.minimum_wet_duration_met ?? Number(event.wet_frames || 0) >= minimumFrames), "Minimum wet duration", `${number(event.wet_frames || 0, 0)} wet frames; ${minimumFrames} required`),
    decisionRow(true, event.classification_label || "Model result", event.classification_explanation || "Classification explanation unavailable"),
  ].join("");
}

function renderIntensityGates(model, event) {
  const rows = (model.spatial_rules || []).map((rule) => {
    const key = String(Math.round(Number(rule.dbz)));
    const coverage = Number(event?.peak_coverage_percent?.[key] || 0);
    const area = Number(event?.peak_covered_area_sq_mi?.[key] || 0);
    const qualified = coverage + 1e-9 >= Number(rule.minimum_coverage_percent);
    return `
      <tr>
        <td>${number(rule.dbz, 0)}+ dBZ</td>
        <td>${number(rule.minimum_coverage_percent, 0)}%</td>
        <td>${number(rule.minimum_area_sq_mi, 3)} mi²</td>
        <td>${number(coverage, 1)}%</td>
        <td>${number(area, 3)} mi²</td>
        <td class="${qualified ? "gate-pass" : "gate-fail"}">${qualified ? "PASS" : "—"}</td>
      </tr>
    `;
  }).join("");

  $("intensity-gates").innerHTML = `
    <table>
      <thead><tr><th>Intensity</th><th>Required %</th><th>Required area</th><th>Event peak %</th><th>Event peak area</th><th>Result</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

function renderMethods() {
  const method = app.model.method || {};
  const classifications = method.classification || {};
  const sources = method.sources || [];
  const limitations = method.limitations || [];
  $("methods-content").innerHTML = `
    <h3>Equations and decision inputs</h3>
    <ul>
      <li><strong>Radar rainfall:</strong> ${escapeHtml(method.rainfall_formula || "Not available")}. ${escapeHtml(method.rainfall_explanation || "")}</li>
      <li><strong>Estimated watershed runoff:</strong> ${escapeHtml(method.runoff_formula || "Not available")}. ${escapeHtml(method.direct_runoff_explanation || "")}</li>
      <li><strong>Routed peak flow — context:</strong> ${escapeHtml(method.peak_flow_formula || "Not available")}. ${escapeHtml(method.peak_flow_explanation || "")}</li>
      <li><strong>Pool-storage target:</strong> ${escapeHtml(method.target_formula || "Not available")}. ${escapeHtml(method.target_explanation || "")}</li>
      <li><strong>Intense-rain footprint:</strong> ${escapeHtml(method.spatial_formula || "Not available")}. ${escapeHtml(method.spatial_explanation || "")}</li>
      <li><strong>Estimated fill ratio:</strong> ${escapeHtml(method.fill_ratio_explanation || "Not available")}</li>
      <li><strong>Atlas 14 context:</strong> ${escapeHtml(method.atlas_explanation || "Not available")}</li>
      <li><strong>Why drainage area is still present:</strong> ${escapeHtml(method.scaling_basis || "Not available")}</li>
    </ul>

    <h3>Classification language</h3>
    <ul>
      ${Object.entries(classifications).map(([key, value]) => `<li><strong>${escapeHtml(key.replaceAll("_", " "))}:</strong> ${escapeHtml(value)}</li>`).join("")}
    </ul>
    <p>${escapeHtml(method.condition_language || "Condition statements are modeled estimates, not field observations.")}</p>

    <h3>Limitations</h3>
    <ul>${limitations.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>

    <h3>Primary sources</h3>
    <ul>${sources.map((source) => `<li><a href="${escapeHtml(source.url)}" target="_blank" rel="noopener">${escapeHtml(source.label)}</a></li>`).join("")}</ul>
  `;
}

function renderSelected() {
  const model = selectedModel();
  const status = selectedStatus();
  if (!model) return;
  const event = status.last_rain_event;

  $("detail-heading").textContent = model.name;
  $("canyon-select").value = app.selectedId;
  $("calculation-title").textContent = `${model.name} calculation`;
  $("calibration-badge").textContent = model.calibration || "Provisional model";

  renderCondition(model, event);
  renderMetrics(model, event);
  renderEvents(status);
  renderStorageCalculation(model);
  renderHydrologyCalculation(model, event);
  renderDecision(model, event);
  renderIntensityGates(model, event);
  renderSummary();
}

function selectCanyon(id, fitMap = false) {
  if (!app.model?.canyons?.[id]) return;
  app.selectedId = id;
  renderSelected();
  updateMapSelection(fitMap);
  history.replaceState(null, "", `#${encodeURIComponent(id)}`);
}

async function initialize() {
  try {
    [app.model, app.status, app.watersheds] = await Promise.all([
      fetchJson(`${DATA_ROOT}/model.json`),
      fetchJson(`${DATA_ROOT}/status.json`),
      fetchJson("watersheds.geojson"),
    ]);

    setHealth();
    populateSelect();
    renderMethods();
    const hashId = decodeURIComponent(location.hash.replace(/^#/, ""));
    app.selectedId = app.model.canyons[hashId] ? hashId : Object.keys(app.model.canyons)[0];
    initializeMap();
    selectCanyon(app.selectedId, false);
  } catch (error) {
    console.error(error);
    const box = $("error-box");
    box.hidden = false;
    box.textContent = `Unable to load canyon-condition data: ${error.message}`;
    $("health-pill").textContent = "Data load failed";
    $("health-pill").className = "health-pill bad";
  }
}

initialize();
