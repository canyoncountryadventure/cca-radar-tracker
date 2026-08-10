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
  selectedEvent: null,
  radarView: "accumulation",
  map: null,
  baseLayers: {},
  activeBase: "satellite",
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

function zuluDateTime(value) {
  if (!value) return "Not available";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "Not available";
  return `${parsed.toISOString().slice(0, 16).replace("T", " ")}Z`;
}

function moabAndZuluDateTime(value) {
  if (!value) return "Not available";
  return `Moab: ${dateTime(value)} | Zulu: ${zuluDateTime(value)}`;
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

function summaryDateTime(value) {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "—";
  return new Intl.DateTimeFormat("en-US", {
    timeZone: TIME_ZONE,
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(parsed);
}

function eventSortTime(event) {
  if (!event) return Number.NEGATIVE_INFINITY;
  const parsed = new Date(event.end_utc || event.start_utc || 0).getTime();
  return Number.isFinite(parsed) ? parsed : Number.NEGATIVE_INFINITY;
}

function fillVisual(event) {
  if (!event) {
    return {
      percent: 0,
      text: "—",
      css: "fill-none",
      title: "No retained rain event",
    };
  }

  const ratio = Math.max(0, Number(event.fill_ratio || 0));
  const percent = Math.min(100, Math.round(ratio * 100));
  let css = percent === 0 ? "fill-none" : "fill-minor";
  if (ratio >= 0.9) css = "fill-full";
  else if (ratio >= 0.75) css = "fill-large";
  else if (ratio >= 0.50) css = "fill-substantial";
  else if (ratio >= 0.25) css = "fill-some";

  return {
    percent,
    text: `${percent}%`,
    css,
    title: `Current modeled condition: ${percent}%`,
  };
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

function detailedRadarEvents(status) {
  const candidates = [
    status?.last_rain_event,
    ...(Array.isArray(status?.events) ? status.events : []),
  ];
  const seen = new Set();
  return candidates.filter((event) => {
    if (!event) return false;
    const hasRadar = Boolean(
      event.grid_bbox
      && (event.peak_grid_dbz || event.accumulated_rain_grid_inches)
    );
    if (!hasRadar) return false;
    const identity = `${event.start_utc || ""}|${event.end_utc || ""}`;
    if (seen.has(identity)) return false;
    seen.add(identity);
    return true;
  });
}

function mostRecentStormRadar(status) {
  return detailedRadarEvents(status).sort((a, b) => eventSortTime(b) - eventSortTime(a))[0] || null;
}

function setHealth() {
  const status = app.status || {};
  const health = status.health || {};
  const pill = $("health-pill");
  const checked = status.last_checked_utc || status.last_scheduled_run_utc;
  const confirmed = status.latest_archive_confirmed_frame_utc;
  const newestLive = status.latest_provisional_frame_utc;
  const missingFrames = Array.isArray(status.missing_archive_frames_utc)
    ? status.missing_archive_frames_utc
    : [];
  const missing = missingFrames.length || Number(health.missing_archive_frame_count || 0);
  const provisional = Number(health.provisional_frame_count || 0);
  const replay = status.manual_replay_from_utc
    || status.earliest_missing_archive_frame_utc
    || missingFrames[0]
    || null;

  if (checked && confirmed && missing === 0) {
    pill.textContent = `Archive current through ${moabAndZuluDateTime(confirmed)}`;
    if (provisional > 0) pill.textContent += ` · ${provisional} live frame${provisional === 1 ? "" : "s"} pending`;
    pill.className = "health-pill ok";
  } else if (checked && missing > 0) {
    pill.textContent = `${missing} archive frame${missing === 1 ? "" : "s"} missing · retry pending`;
    pill.className = "health-pill bad";
  } else {
    pill.textContent = health.message || "Radar archive confirmation has not been established yet.";
    pill.className = `health-pill ${health.ok === false ? "bad" : "ok"}`;
  }

  const parts = [];
  if (checked) parts.push(`Checked ${moabAndZuluDateTime(checked)}`);
  if (missing > 0 && replay) {
    parts.push(
      `Earliest missing frame: ${moabAndZuluDateTime(replay)}`
    );
    parts.push(
      `Manual replay Zulu, only if still missing after the next check: ${replay}`
    );
  }
  if (newestLive && provisional > 0) parts.push(`Newest live ${moabAndZuluDateTime(newestLive)}`);
  $("last-updated").textContent = parts.join(" • ");
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
    const status = canyonStatus(id);
    const event = status.last_rain_event;
    const condition = status.condition_estimate || {};
    const record = status.historical_records?.peak_individual_event;
    const meta = eventCondition(event);
    return {
      id,
      model,
      event,
      condition,
      record,
      meta,
      fill: fillVisual({ fill_ratio: Number(condition.percent || 0) / 100 }),
      sortTime: eventSortTime(event),
    };
  });

  // Most recently wet canyon first; canyons with no retained event stay last.
  rows.sort((a, b) => b.sortTime - a.sortTime || a.model.name.localeCompare(b.model.name));

  container.innerHTML = rows.map(({ id, model, event, condition, record, meta, fill }) => `
    <button type="button" class="summary-row ${meta.css} ${id === app.selectedId ? "selected" : ""}" data-canyon-id="${escapeHtml(id)}">
      <span
        class="summary-bubble ${fill.css}"
        style="--bubble-fill: ${fill.percent}%"
        title="${escapeHtml(fill.title)}"
        aria-label="${escapeHtml(fill.title)}"
      >
        <span class="summary-bubble-text">${escapeHtml(fill.text)}</span>
      </span>
      <span class="summary-copy">
        <span class="summary-name">${escapeHtml(model.name)}</span>
        <span class="summary-condition">${escapeHtml(condition.current_condition || "unknown")} · ${escapeHtml(condition.confidence || "Unknown")} confidence</span>
        <span class="summary-record">${record ? `Peak event: ${number(record.percent || 0, 0)}% — ${escapeHtml(dateOnly(record.end_utc || record.start_utc))}` : "No historical peak yet"}</span>
      </span>
      <span class="summary-date">${condition.last_meaningful_refill_utc ? `Last meaningful: ${summaryDateTime(condition.last_meaningful_refill_utc)}` : "No meaningful refill"}</span>
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
  if (value < 15) return "#4568a6";
  if (value < 20) return "#60b4d4";
  if (value < 25) return "#43d67e";
  if (value < 30) return "#0eb314";
  if (value < 35) return "#0b840e";
  if (value < 40) return "#327308";
  if (value < 45) return "#ffe200";
  if (value < 50) return "#ffac00";
  if (value < 55) return "#f80000";
  if (value < 60) return "#aa0000";
  if (value < 65) return "#ffeaff";
  if (value < 70) return "#f960fa";
  return "#a400f7";
}

function rainfallColor(inches) {
  if (inches >= 2) return "#7b1fa2";
  if (inches >= 1) return "#d32f2f";
  if (inches >= 0.5) return "#f57c00";
  if (inches >= 0.25) return "#fdd835";
  if (inches >= 0.1) return "#66bb6a";
  if (inches >= 0.05) return "#26c6da";
  if (inches >= 0.01) return "#42a5f5";
  return "#90caf9";
}

function setMapButtonState(selector, activeValue) {
  document.querySelectorAll(selector).forEach((button) => {
    const value = button.dataset.mapBase || button.dataset.mapOverlay;
    const active = value === activeValue || activeValue?.has?.(value);
    button.classList.toggle("active", Boolean(active));
    button.setAttribute("aria-pressed", String(Boolean(active)));
  });
}

function setBaseLayer(name) {
  const next = app.baseLayers[name];
  if (!app.map || !next || app.activeBase === name) return;
  const current = app.baseLayers[app.activeBase];
  if (current && app.map.hasLayer(current)) app.map.removeLayer(current);
  next.addTo(app.map);
  app.activeBase = name;
  setMapButtonState("[data-map-base]", name);
}

function setOverlayLayer(name, visible) {
  if (!app.map) return;
  const layer = name === "radar" ? app.radarLayer : app.watershedLayer;
  if (!layer) return;
  if (visible && !app.map.hasLayer(layer)) layer.addTo(app.map);
  if (!visible && app.map.hasLayer(layer)) app.map.removeLayer(layer);
  const active = new Set();
  if (app.radarLayer && app.map.hasLayer(app.radarLayer)) active.add("radar");
  if (app.watershedLayer && app.map.hasLayer(app.watershedLayer)) active.add("watersheds");
  setMapButtonState("[data-map-overlay]", active);
}

function bindMapToggles() {
  document.querySelectorAll("[data-map-base]").forEach((button) => {
    button.addEventListener("click", () => setBaseLayer(button.dataset.mapBase));
  });
  document.querySelectorAll("[data-map-overlay]").forEach((button) => {
    button.addEventListener("click", () => {
      const name = button.dataset.mapOverlay;
      setOverlayLayer(name, button.getAttribute("aria-pressed") !== "true");
    });
  });
}

function initializeMap() {
  const satellite = L.tileLayer(
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    {
      maxZoom: 19,
      attribution: "Imagery © Esri, Maxar, Earthstar Geographics, and the GIS User Community",
    },
  );
  const topo = L.tileLayer(
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}",
    {
      maxZoom: 19,
      attribution: "Topographic map © Esri and contributors",
    },
  );

  app.baseLayers = { satellite, topo };
  app.activeBase = "satellite";
  app.map = L.map("map", {
    layers: [satellite],
    zoomControl: true,
    preferCanvas: true,
    zoomSnap: 0.25,
    wheelPxPerZoomLevel: 90,
  });

  app.map.createPane("radarPane");
  app.map.getPane("radarPane").style.zIndex = "410";
  app.map.createPane("watershedPane");
  app.map.getPane("watershedPane").style.zIndex = "420";

  app.radarLayer = L.layerGroup().addTo(app.map);
  app.watershedLayer = L.geoJSON(app.watersheds, {
    pane: "watershedPane",
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

  bindMapToggles();
  setMapButtonState("[data-map-base]", app.activeBase);
  setOverlayLayer("radar", true);
  setOverlayLayer("watersheds", true);

  const bounds = app.watershedLayer.getBounds();
  if (bounds.isValid()) app.map.fitBounds(bounds.pad(0.05), { maxZoom: 10 });

  requestAnimationFrame(() => app.map.invalidateSize({ pan: false }));
  window.addEventListener("resize", () => app.map.invalidateSize({ pan: false }));
}
function drawSelectedRadar() {
  if (!app.radarLayer) return;
  app.radarLayer.clearLayers();
  const status = selectedStatus();
  const event = app.selectedEvent || status.last_rain_event || status.latest_analysis;
  const hasAccumulation = Array.isArray(event?.accumulated_rain_grid_inches);
  const hasPeakGrid = Array.isArray(event?.peak_grid_dbz || event?.grid_dbz);
  const showAccumulation = app.radarView === "accumulation" && hasAccumulation;
  const grid = showAccumulation ? event.accumulated_rain_grid_inches : (event?.peak_grid_dbz || event?.grid_dbz);
  const bbox = event?.grid_bbox;
  const time = event?.peak_frame_utc || event?.timestamp_utc || event?.end_utc;
  const requestedUnavailableAccumulation = app.radarView === "accumulation" && !hasAccumulation;
  const meanCheckWarning = event?.accumulation_grid_mean_consistent === false
    ? " WARNING: displayed grid mean does not match the reported watershed mean."
    : "";
  $("radar-time").textContent = showAccumulation
    ? `Total radar-estimated rainfall across the full event — watershed area-weighted mean: ${number(event.basin_rain_inches, 3)} in; maximum watershed cell: ${number(event.maximum_watershed_cell_storm_inches, 3)} in.${meanCheckWarning}`
    : requestedUnavailableAccumulation && hasPeakGrid
    ? "Total-rain map unavailable for this older storm; showing its retained peak dBZ frame instead."
    : requestedUnavailableAccumulation
    ? `No retained radar grid is available for the selected storm${event?.end_utc || event?.start_utc ? ` ending ${dateTime(event.end_utc || event.start_utc)}` : ""}.`
    : time
    ? `Peak five-minute reflectivity frame: ${dateTime(time)}`
    : "No retained radar grid for the selected canyon";

  const accumulationLegend = [
    ["<0.01", 0.005], ["0.01–0.05", 0.03], ["0.05–0.10", 0.075],
    ["0.10–0.25", 0.175], ["0.25–0.50", 0.375], ["0.50–1.00", 0.75],
    ["1.00–2.00", 1.5], ["2.00+", 2.01],
  ];
  const dbzLegend = [10, 20, 30, 40, 45, 50, 55, 60, 65, 70];
  $("radar-legend").innerHTML = showAccumulation
    ? `<span class="radar-legend-title">Event rain (in)</span>${accumulationLegend.map(([label, value]) => `<span class="radar-legend-item"><i style="--legend-color:${rainfallColor(value)}"></i>${label}</span>`).join("")}`
    : `<span class="radar-legend-title">Peak reflectivity (dBZ)</span>${dbzLegend.map((value) => `<span class="radar-legend-item"><i style="--legend-color:${radarColor(value)}"></i>${value}</span>`).join("")}`;

  document.querySelectorAll("[data-radar-view]").forEach((button) => {
    const active = button.dataset.radarView === (showAccumulation ? "accumulation" : "peak");
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
    if (button.dataset.radarView === "accumulation") {
      button.disabled = !hasAccumulation;
      button.title = hasAccumulation ? "Show total event rainfall" : "Total-rain map unavailable for this older storm";
    }
  });

  if (!Array.isArray(grid) || !grid.length || !Array.isArray(grid[0]) || !bbox) return;
  const [left, bottom, right, top] = bbox.map(Number);
  const rows = grid.length;
  const columns = grid[0].length;
  const cellWidth = (right - left) / columns;
  const cellHeight = (top - bottom) / rows;

  grid.forEach((row, rowIndex) => {
    row.forEach((rawValue, columnIndex) => {
      const value = Number(rawValue);
      if (!Number.isFinite(value) || (showAccumulation ? value <= 0 : value < 10)) return;
      const color = showAccumulation ? rainfallColor(value) : radarColor(value);
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
          pane: "radarPane",
        },
      ).bindTooltip(showAccumulation ? `${number(value, 3)} in event rain` : `${number(value, 1)} dBZ`).addTo(app.radarLayer);
    });
  });
}

function bindRadarViews() {
  document.querySelectorAll("[data-radar-view]").forEach((button) => {
    button.addEventListener("click", () => {
      app.radarView = button.dataset.radarView;
      drawSelectedRadar();
    });
  });
}

function updateMapSelection(fit = false) {
  if (!app.watershedLayer) return;
  app.watershedLayer.setStyle(watershedStyle);
  const layer = app.layersById.get(app.selectedId);
  if (fit && layer) app.map.fitBounds(layer.getBounds().pad(0.35), { maxZoom: 11 });
  if (layer && app.map.hasLayer(app.watershedLayer)) layer.bringToFront();
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

function metricCard(label, value, note, help) {
  const description = help || note;
  return `
    <article
      class="metric-card"
      tabindex="0"
      aria-label="${escapeHtml(`${label}: ${value}. ${description}`)}"
    >
      <span class="metric-help-icon" aria-hidden="true">?</span>
      <div class="metric-label">${escapeHtml(label)}</div>
      <div class="metric-value">${escapeHtml(value)}</div>
      <div class="metric-note">${escapeHtml(note)}</div>
      <div class="metric-tooltip" role="tooltip">${escapeHtml(description)}</div>
    </article>
  `;
}

function renderMetrics(model, event) {
  const runoff = directRunoff(event);
  const runoffRange = directRunoffRange(event);
  const peak = routedPeak(event);
  const peakRange = routedPeakRange(event);
  const normalIa = Number(model.hydrology?.initial_abstraction_inches?.normal);
  const rain = Number(event?.basin_rain_inches || 0);
  const zeroReason = event && runoff === 0 && Number.isFinite(normalIa) && rain <= normalIa
    ? `rain did not exceed ${number(normalIa, 3)} in initial abstraction`
    : "normal antecedent-condition estimate";

  const modifier = Number(model.pothole_modifier || 0);
  const modifierValue = modifier === 0 ? "0%" : `${modifier > 0 ? "+" : "−"}${number(Math.abs(modifier) * 100, 0)}%`;

  const cards = [
    metricCard(
      "Watershed area-weighted mean rainfall",
      event ? `${number(event.basin_rain_inches, 3)} in` : "—",
      "area-weighted event accumulation",
      "Radar-estimated rainfall averaged across every grid cell inside the watershed for the retained event. It is not a rain-gauge measurement or the maximum point rainfall.",
    ),
    metricCard(
      "Estimated watershed runoff",
      event ? `${compactNumber(runoff, 1)} ft³` : "—",
      event ? `${rangeText(runoffRange, "ft³", 0)} dry–wet; ${zeroReason}` : "NRCS direct-runoff estimate; not measured canyon delivery",
      "The normal-antecedent-condition estimate of total direct-runoff volume generated by the watershed. Cubic feet is a storm volume, not a flow rate. The note shows the dry-to-wet uncertainty range, and actual delivery to canyon pools may be lower.",
    ),
    metricCard(
      event?.peak_flow_status === "provisional_field_calibration" ? "Estimated canyon peak" : "Experimental routed peak",
      event ? `${number(peak, 2)} cfs` : "—",
      event ? `${rangeText(peakRange, "cfs", 2)} dry–wet range; ${event.peak_flow_status === "provisional_field_calibration" ? "provisional 0.14 Zero G calibration" : "uncalibrated"}` : "screening flow-rate context",
      "Peak flow is calibrated separately from runoff volume. Zero G uses a provisional 0.14 factor based on one field-estimated 3–6 cfs flash; other canyons remain experimental. Peak flow does not control refill classification.",
    ),
    metricCard(
      "Estimated empty-pool storage",
      `${number(model.fill_target_ft3, 0)} ft³`,
      "estimated empty pool/pothole storage",
      "The provisional volume required to fill all modeled canyon pool and pothole storage if it started empty. It is normalized from Zero G by technical-section length and the canyon-specific pothole-storage adjustment.",
    ),
    metricCard(
      "Storage-fill ratio",
      event ? `${number(event.fill_ratio || 0, 2)}×` : "—",
      "normal-condition watershed runoff ÷ empty-storage target",
      "Normal-condition estimated watershed runoff divided by provisional empty-pool storage. A 0.72× ratio means modeled runoff equals 72% of the empty-storage target; it does not mean observed pools are exactly 72% full.",
    ),
    metricCard(
      "Technical section",
      `${number(model.technical_length_miles, 2)} mi`,
      `${number(model.length_ratio_to_zerog, 2)}× Zero G length`,
      "The user-supplied length of the canyon's technical pool- and pothole-bearing section. It is compared with Zero G's 0.75-mile technical reference length.",
    ),
    metricCard(
      "Pothole-storage adjustment",
      modifierValue,
      `${modifierText(model)} per technical mile`,
      "A canyon-specific adjustment to the assumed pool-storage volume per technical mile relative to Zero G. Positive values mean more storage per mile; negative values mean less.",
    ),
    metricCard(
      "Peak radar",
      event?.peak_dbz != null ? `${number(event.peak_dbz, 1)} dBZ` : "—",
      "maximum reflectivity inside watershed",
      "The single highest radar reflectivity value detected inside the watershed during the event. It is an intensity indicator, not the basin-average rainfall depth.",
    ),
    metricCard(
      "Storm duration",
      event ? `${number(eventDuration(event), 0)} min` : "—",
      `${number(event?.wet_frames || 0, 0)} wet five-minute frames`,
      "The retained event span based on consecutive five-minute radar frames. Wet frames exceeded the lower event-detection threshold; they do not necessarily pass the separate 50/55/60 dBZ footprint tests.",
    ),
    metricCard(
      "Drainage area",
      `${number(model.area_sq_mi, 3)} mi²`,
      "used for runoff volume, not pool-storage scaling",
      "The watershed-polygon area. It converts modeled runoff depth to total runoff volume and converts radar coverage percentages to square miles. It is not used to scale pool-storage capacity.",
    ),
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

function matchingHistoryEvent(status, historyEvent) {
  if (!historyEvent) return null;
  const events = detailedRadarEvents(status);
  const historyStart = historyEvent.start_utc || null;
  const historyEnd = historyEvent.end_utc || null;

  const exactMatch = events.find((event) =>
    (event.start_utc || null) === historyStart
    && (event.end_utc || null) === historyEnd
  );
  if (exactMatch) return exactMatch;

  // A retained history row may outlive its detailed radar payload. Never use
  // an array-position fallback here: events and refill_history are separately
  // deduplicated and retained, so their indexes are not stable identities.
  return historyEvent;
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
      ${eventMeta("Area-weighted mean rainfall", `${number(event.basin_rain_inches, 3)} in`)}
      ${eventMeta("Maximum watershed cell", event.maximum_watershed_cell_storm_inches == null ? "Not available" : `${number(event.maximum_watershed_cell_storm_inches, 3)} in`)}
      ${eventMeta("Atlas 14 context", atlasText(event))}
      ${eventMeta("Estimated watershed runoff", `${number(runoff, 0)} ft³`)}
      ${eventMeta("Routed peak — context", `${number(peak, 2)} cfs`)}
      ${eventMeta("Peak radar", `${number(event.peak_dbz, 1)} dBZ`)}
      ${eventMeta("Fill ratio", `${number(event.fill_ratio || 0, 2)}×`)}
      ${eventMeta("Historical dBZ footprint", (decision.heavy_rain_footprint_observed ?? event.spatial_gate_seen) ? "Reached (context only)" : "Not reached")}
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


function ensureRefillHistoryPanel() {
  let panel = $("refill-history");
  if (panel) return panel;
  panel = document.createElement("article");
  panel.id = "refill-history";
  panel.className = "event-card";
  panel.style.margin = "12px";
  panel.style.padding = "18px 20px";
  const metrics = $("metrics-grid");
  metrics.parentNode.insertBefore(panel, metrics);
  return panel;
}

function milestoneValue(summary, percent) {
  const value = summary?.milestones_utc?.[String(percent)];
  return value ? dateTime(value) : "Not reached";
}

function renderRefillHistory(status, model) {
  const panel = ensureRefillHistoryPanel();
  const summary = status.cumulative_refill_evidence || {};
  const history = Array.isArray(status.refill_history) ? status.refill_history : [];
  const condition = status.condition_estimate || {};
  const records = status.historical_records || {};
  const peakEvent = records.peak_individual_event;
  const peakWindow = records.peak_seven_day_evidence;

  const rows = history.map((event, index) => `
    <tr>
      <td><button type="button" class="event-date-button" data-history-index="${index}">${escapeHtml(dateTime(event.end_utc || event.start_utc))}</button></td>
      <td>${number(event.basin_rain_inches, 3)} in</td>
      <td>${number(event.direct_runoff_ft3, 0)} ft³</td>
      <td>${number(event.event_fill_ratio, 2)}×</td>
      <td>${escapeHtml(event.classification_label || "Modeled event")}</td>
    </tr>
  `).join("");

  panel.innerHTML = `
    <p class="event-kicker">CURRENT CONDITION AND REFILL HISTORY</p>
    <h3>Current condition: ${escapeHtml(condition.current_condition || "unknown")} — ${escapeHtml(condition.confidence || "Unknown")} confidence</h3>
    <p class="event-summary">
      The percentage decreases ${number(condition.decay_percentage_points_per_day || 0.8, 1)} point per day. New modeled runoff adds to the current balance, capped at 100%; confidence also decreases as the supporting observation ages.
    </p>
    <div class="event-meta-grid">
      ${eventMeta("Last verified", condition.last_verified ? `${number(condition.last_verified.percent, 0)}% — ${dateOnly(condition.last_verified.observed_utc)}` : "No field verification")}
      ${eventMeta("Condition basis", condition.basis_utc ? `${condition.basis} — ${dateOnly(condition.basis_utc)}` : condition.basis || "None")}
      ${eventMeta("Detailed history", `${history.length} event${history.length === 1 ? "" : "s"} retained for 90 days`)}
      ${eventMeta("Last meaningful refill", condition.last_meaningful_refill_utc ? dateTime(condition.last_meaningful_refill_utc) : "None")}
      ${eventMeta("Largest individual event", peakEvent ? `${number(peakEvent.percent || 0, 0)}% — ${dateOnly(peakEvent.end_utc || peakEvent.start_utc)}` : "None")}
      ${eventMeta("Historic seven-day high", peakWindow ? `${number(peakWindow.percent || 0, 0)}% — ${dateOnly(peakWindow.through_utc)}` : "None")}
    </div>
    <p class="event-coverage">
      Click any storm date below to map that event. Recent storms show total rainfall; older storms without a recoverable accumulation grid clearly fall back to their retained peak radar frame.
    </p>
    <div class="table-wrap">
      <table>
        <thead>
          <tr><th>Storm end</th><th>Radar rain</th><th>Modeled runoff</th><th>Storm ratio</th><th>Result</th></tr>
        </thead>
        <tbody>${rows || `<tr><td colspan="5">No retained modeled rain events.</td></tr>`}</tbody>
      </table>
    </div>
  `;
  panel.querySelectorAll("[data-history-index]").forEach((button) => {
    button.addEventListener("click", () => {
      const historyEvent = history[Number(button.dataset.historyIndex)];
      const event = matchingHistoryEvent(status, historyEvent);
      if (!event) return;
      app.selectedEvent = event;
      drawSelectedRadar();
      $("radar-time").scrollIntoView({ behavior: "smooth", block: "center" });
    });
  });
}

function initialAbstraction(curveNumber) {
  const cn = Number(curveNumber);
  if (!Number.isFinite(cn) || cn <= 0) return null;
  const traditionalRetention = 1000 / cn - 10;
  const adjustedRetention = 1.33 * (traditionalRetention ** 1.15);
  return 0.05 * adjustedRetention;
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
        <td class="${qualified ? "gate-pass" : "gate-fail"}">${qualified ? "REACHED" : "—"}</td>
      </tr>
    `;
  }).join("");

  $("intensity-gates").innerHTML = `
    <table>
      <thead><tr><th>Intensity</th><th>Historical %</th><th>Historical area</th><th>Event peak %</th><th>Event peak area</th><th>Comparison</th></tr></thead>
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
      <li><strong>Rain-event grouping:</strong> ${escapeHtml(method.rain_event_explanation || "Events use separate start, accumulation, and dry-gap rules.")}</li>
      <li><strong>Frame reconciliation:</strong> ${escapeHtml(method.frame_reconciliation_explanation || "Recent timestamps are automatically revisited and replaced by exact archived radar frames.")}</li>
      <li><strong>Estimated watershed runoff:</strong> ${escapeHtml(method.runoff_formula || "Not available")}. ${escapeHtml(method.direct_runoff_explanation || "")}</li>
      <li><strong>Routed peak flow — context:</strong> ${escapeHtml(method.peak_flow_formula || "Not available")}. ${escapeHtml(method.peak_flow_explanation || "")}</li>
      <li><strong>Pool-storage target:</strong> ${escapeHtml(method.target_formula || "Not available")}. ${escapeHtml(method.target_explanation || "")}</li>
      <li><strong>Intense-rain footprint:</strong> ${escapeHtml(method.spatial_formula || "Not available")}. ${escapeHtml(method.spatial_explanation || "")}</li>
      <li><strong>Estimated fill ratio:</strong> ${escapeHtml(method.fill_ratio_explanation || "Not available")}</li>
      <li><strong>Multi-storm accumulation:</strong> ${escapeHtml(method.cumulative_refill_explanation || "Not available")}</li>
      <li><strong>Pool loss and decay:</strong> ${escapeHtml(method.pool_loss_explanation || "Not available")}</li>
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
  renderRefillHistory(status, model);
  renderStorageCalculation(model);
  renderHydrologyCalculation(model, event);
  renderDecision(model, event);
  renderIntensityGates(model, event);
  renderSummary();
}

function selectCanyon(id, fitMap = false) {
  if (!app.model?.canyons?.[id]) return;
  app.selectedId = id;
  app.selectedEvent = mostRecentStormRadar(canyonStatus(id));
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
    bindRadarViews();
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
