"use strict";

// Keep all modeled events in status.json for hydrology, QA/QC, replay, and audit.
// Only the user-facing refill-history table is filtered to reduce clutter.
const MIN_VISIBLE_EVENT_RAIN_INCHES = 0.05;

(function installEventListFilter() {
  const originalRenderRefillHistory = window.renderRefillHistory;
  if (typeof originalRenderRefillHistory !== "function") {
    console.warn("Refill-history filter was not installed because renderRefillHistory is unavailable.");
    return;
  }

  window.renderRefillHistory = function renderFilteredRefillHistory(status, model) {
    const fullHistory = Array.isArray(status?.refill_history) ? status.refill_history : [];
    const visibleHistory = fullHistory.filter((event) => {
      const basinRain = Number(event?.basin_rain_inches);
      return Number.isFinite(basinRain) && basinRain >= MIN_VISIBLE_EVENT_RAIN_INCHES;
    });

    const displayStatus = {
      ...status,
      refill_history: visibleHistory,
    };

    return originalRenderRefillHistory(displayStatus, model);
  };
})();
