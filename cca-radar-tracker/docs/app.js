const DATA_URL = "data/status.json";
const TIME_ZONE = "America/Denver";
const RECENT_DAYS = 14;

const $ = (id) => document.getElementById(id);

function localDate(value, includeDate = true) {
  if (!value) return "—";
  const options = includeDate
    ? { timeZone: TIME_ZONE, month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short" }
    : { timeZone: TIME_ZONE, hour: "numeric", minute: "2-digit", timeZoneName: "short" };
  return new Intl.DateTimeFormat("en-US", options).format(new Date(value));
}

function ageDays(value) {
  return (Date.now() - new Date(value).getTime()) / 86400000;
}

function radarColor(value) {
  if (value === null || value < 10) return "#0c0f11";
  if (value < 20) return "#123d61";
  if (value < 30) return "#18735c";
  if (value < 40) return "#3fa641";
  if (value < 50) return "#f3d946";
  if (value < 55) return "#f29b38";
  if (value < 60) return "#ef594f";
  return "#eb65d0";
}

function drawRadar(analysis, geometry) {
  const canvas = $("radar-canvas");
  const context = canvas.getContext("2d");
  context.clearRect(0, 0, canvas.width, canvas.height);
  context.fillStyle = "#0c0f11";
  context.fillRect(0, 0, canvas.width, canvas.height);
  if (!analysis?.grid_dbz?.length) {
    context.fillStyle = "#8b8f91";
    context.font = "600 18px system-ui";
    context.textAlign = "center";
    context.fillText("Waiting for the first radar frame", canvas.width / 2, canvas.height / 2);
    return;
  }

  const rows = analysis.grid_dbz.length;
  const columns = analysis.grid_dbz[0].length;
  const cellWidth = canvas.width / columns;
  const cellHeight = canvas.height / rows;
  analysis.grid_dbz.forEach((row, rowIndex) => {
    row.forEach((value, columnIndex) => {
      context.fillStyle = radarColor(value);
      context.fillRect(columnIndex * cellWidth, rowIndex * cellHeight, Math.ceil(cellWidth) + 1, Math.ceil(cellHeight) + 1);
    });
  });

  const [left, bottom, right, top] = analysis.grid_bbox;
  const project = ([longitude, latitude]) => [
    (longitude - left) / (right - left) * canvas.width,
    (top - latitude) / (top - bottom) * canvas.height,
  ];
  if (!geometry) return;
  const polygons = geometry.type === "Polygon" ? [geometry.coordinates] : geometry.coordinates;
  context.strokeStyle = "#ffffff";
  context.lineWidth = 4;
  context.shadowColor = "rgba(0, 0, 0, .85)";
  context.shadowBlur = 5;
  polygons.forEach((polygon) => {
    polygon.forEach((ring) => {
      context.beginPath();
      ring.forEach((point, index) => {
        const [x, y] = project(point);
        if (index === 0) context.moveTo(x, y);
        else context.lineTo(x, y);
      });
      context.closePath();
      context.stroke();
    });
  });
  context.shadowBlur = 0;
}

function showStatus(status, watershed) {
  const analysis = status.latest_analysis;
  const event = status.last_qualifying_event;
  const card = $("status-card");
  card.classList.remove("loading", "triggered", "clear");

  if (event && ageDays(event.end_utc) <= RECENT_DAYS) {
    card.classList.add("triggered");
    $("status-icon").textContent = "!";
    $("status-label").textContent = "Recent pool-filling radar trigger detected";
    $("status-detail").textContent = `The last qualifying storm occurred ${localDate(event.end_utc)}.`;
  } else {
    card.classList.add("clear");
    $("status-icon").textContent = "✓";
    $("status-label").textContent = "No recent pool-filling radar trigger";
    $("status-detail").textContent = event
      ? `The last recorded qualifying storm ended ${localDate(event.end_utc)}.`
      : "No storm has met the rule since automated monitoring began.";
  }

  $("maximum-dbz").textContent = analysis?.maximum_dbz ?? "—";
  [50, 55, 60].forEach((threshold) => {
    const value = analysis?.coverage_percent?.[threshold];
    $(`coverage-${threshold}`).textContent = value === undefined ? "—" : `${value}%`;
  });
  $("frame-time").textContent = analysis ? localDate(analysis.frame_utc) : "No frame analyzed yet";
  drawRadar(analysis, watershed?.features?.[0]?.geometry);

  if (event) {
    const duration = event.qualifying_frames === 1 ? "one five-minute frame" : `${event.qualifying_frames} qualifying frames`;
    $("event-content").innerHTML = `
      <strong>${localDate(event.start_utc)}</strong>
      <span>Ended ${localDate(event.end_utc)} · ${duration} · peak ${event.peak_dbz} dBZ · peak coverage ≥50: ${event.peak_coverage_percent["50"]}%, ≥55: ${event.peak_coverage_percent["55"]}%, ≥60: ${event.peak_coverage_percent["60"]}%</span>`;
  }

  const health = status.health || { ok: false, message: "Tracker status unavailable" };
  $("health-dot").classList.add(health.ok ? "ok" : "bad");
  $("health-dot").title = health.message;
  $("last-check").textContent = status.last_checked_utc ? `Last automated check: ${localDate(status.last_checked_utc)}` : "Waiting for first check";
}

async function loadStatus() {
  try {
    const [statusResponse, watershedResponse] = await Promise.all([
      fetch(`${DATA_URL}?v=${Date.now()}`, { cache: "no-store" }),
      fetch("data/watershed.geojson", { cache: "force-cache" }),
    ]);
    if (!statusResponse.ok) throw new Error(`Status request failed (${statusResponse.status})`);
    if (!watershedResponse.ok) throw new Error(`Watershed request failed (${watershedResponse.status})`);
    showStatus(await statusResponse.json(), await watershedResponse.json());
  } catch (error) {
    $("status-card").classList.remove("loading");
    $("status-card").classList.add("triggered");
    $("status-icon").textContent = "×";
    $("status-label").textContent = "Radar status unavailable";
    $("status-detail").textContent = "The tracker could not load its latest result.";
    $("health-dot").classList.add("bad");
    $("health-dot").title = error.message;
    drawRadar(null, null);
  }
}

loadStatus();
