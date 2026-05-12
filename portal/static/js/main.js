document.addEventListener("click", (event) => {
  const row = event.target.closest("tr");
  if (!row || event.target.closest("button, a, form, select, input, summary")) return;
  row.classList.toggle("selected");
});

document.querySelectorAll("details[data-key]").forEach((node) => {
  const key = `trading.details.${node.dataset.key}`;
  const saved = localStorage.getItem(key);
  if (saved === "open") node.open = true;
  if (saved === "closed") node.open = false;
  node.addEventListener("toggle", () => {
    localStorage.setItem(key, node.open ? "open" : "closed");
  });
});

const autoRefresh = document.querySelector("[data-auto-refresh-url]");
if (autoRefresh) {
  const refreshUrl = autoRefresh.dataset.autoRefreshUrl;
  const refreshMs = Number(autoRefresh.dataset.autoRefreshMs || 5000);
  let inFlight = false;

  window.setInterval(async () => {
    if (inFlight || document.hidden) return;
    inFlight = true;
    autoRefresh.textContent = "Refreshing Alpaca positions...";
    try {
      const response = await fetch(refreshUrl, { method: "POST" });
      if (!response.ok) throw new Error(`Refresh failed: ${response.status}`);
      autoRefresh.textContent = "Positions refreshed. Reloading...";
      window.location.reload();
    } catch (error) {
      autoRefresh.textContent = "Auto-refresh paused after refresh error";
      inFlight = false;
      console.error(error);
    }
  }, refreshMs);
}

const cadence = document.querySelector("[data-next-monitor]");
if (cadence) {
  const target = cadence.querySelector("[data-countdown-target]");
  const nextAt = new Date(cadence.dataset.nextMonitor);

  const renderCountdown = () => {
    if (!target || Number.isNaN(nextAt.getTime())) return;
    const remaining = Math.max(0, nextAt.getTime() - Date.now());
    const minutes = Math.floor(remaining / 60000);
    const seconds = Math.floor((remaining % 60000) / 1000);
    target.textContent = `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
  };

  renderCountdown();
  window.setInterval(renderCountdown, 1000);
}

const pipelineZone = document.querySelector("[data-pipeline-refresh-url]");
if (pipelineZone) {
  const refreshUrl = pipelineZone.dataset.pipelineRefreshUrl;
  const refreshMs = Number(pipelineZone.dataset.pipelineRefreshMs || 60000);
  const target = pipelineZone.querySelector("[data-pipeline-strip]");
  const banner = pipelineZone.querySelector("[data-pipeline-stale-banner]");
  let inFlight = false;
  let failures = 0;

  window.setInterval(async () => {
    if (inFlight || document.hidden || !target) return;
    inFlight = true;
    try {
      const response = await fetch(refreshUrl);
      if (!response.ok) throw new Error(`Pipeline refresh failed: ${response.status}`);
      target.innerHTML = await response.text();
      failures = 0;
      if (banner) banner.hidden = true;
    } catch (error) {
      failures += 1;
      if (banner && failures >= 1) {
        banner.hidden = false;
        banner.textContent = "Pipeline freshness may be stale.";
      }
      console.error(error);
    } finally {
      inFlight = false;
    }
  }, refreshMs);
}

const toast = document.querySelector("[data-toast]");
const showToast = (message, isError = false) => {
  if (!toast) return;
  toast.textContent = message;
  toast.hidden = false;
  toast.classList.toggle("toast--error", isError);
  window.setTimeout(() => {
    toast.hidden = true;
  }, isError ? 8000 : 4000);
};

const confirmDialog = document.querySelector("[data-confirm-dialog]");
const confirmText = confirmDialog?.querySelector("[data-confirm-text]");
const confirmTitle = confirmDialog?.querySelector("[data-confirm-title]");
const confirmPrimary = confirmDialog?.querySelector("[data-confirm-primary]");
const confirmPrimaryLabel = confirmDialog?.querySelector("[data-confirm-primary-label]");

const confirmAction = ({ title, text, danger = true }) =>
  new Promise((resolve) => {
    if (!confirmDialog) {
      resolve(window.confirm(text));
      return;
    }
    if (confirmTitle) confirmTitle.textContent = title;
    if (confirmText) confirmText.textContent = text;
    if (confirmPrimary) {
      confirmPrimary.disabled = false;
      confirmPrimary.classList.toggle("btn-danger", danger);
      confirmPrimary.classList.toggle("btn-primary", !danger);
    }
    if (confirmPrimaryLabel) confirmPrimaryLabel.textContent = "Confirm";
    const handler = () => {
      confirmDialog.removeEventListener("close", handler);
      resolve(confirmDialog.returnValue === "confirm");
    };
    confirmDialog.addEventListener("close", handler);
    confirmDialog.showModal();
  });

const postJson = async (url, payload) => {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.message || `Request failed: ${response.status}`);
  return data;
};

document.addEventListener("click", async (event) => {
  const resumeForm = event.target.closest("[data-kill-switch-resume]");
  if (!resumeForm) return;
  event.preventDefault();
  const switchName = resumeForm.dataset.switchName || "";
  const ok = await confirmAction({
    title: "Resume kill switch",
    text: `resume ${switchName}`,
    danger: true,
  });
  if (ok) resumeForm.submit();
});

document.addEventListener("click", async (event) => {
  const queueButton = event.target.closest("[data-queue-action]");
  if (!queueButton) return;
  const action = queueButton.dataset.queueAction;
  const symbol = queueButton.dataset.symbol;
  const eventId = queueButton.dataset.eventId;
  const decision = queueButton.dataset.decision;
  const ok = await confirmAction({
    title: action === "apply" ? "Apply queue recommendation" : "Override queue recommendation",
    text: `${action} ${decision} for ${symbol}`,
    danger: action === "apply" && decision === "close",
  });
  if (!ok) return;
  queueButton.disabled = true;
  if (confirmPrimary) confirmPrimary.disabled = true;
  if (confirmPrimaryLabel) confirmPrimaryLabel.textContent = "Working...";
  try {
    const result = await postJson(`/trading/queue/${encodeURIComponent(eventId)}/${action}`, {
      symbol,
      position_id: queueButton.dataset.positionId,
      decision,
    });
    queueButton.closest("tr")?.remove();
    showToast(`Queue action recorded${result.broker_order_id ? ` · broker order ${result.broker_order_id}` : ""}`);
  } catch (error) {
    queueButton.disabled = false;
    showToast(error.message, true);
  }
});

document.addEventListener("click", async (event) => {
  const closeButton = event.target.closest("[data-close-position]");
  if (!closeButton) return;
  event.stopPropagation();
  const symbol = closeButton.dataset.symbol;
  const positionId = closeButton.dataset.positionId;
  const qty = closeButton.dataset.qty || "";
  const marketValue = closeButton.dataset.marketValue || "";
  const ok = await confirmAction({
    title: "Close paper position",
    text: `close ${qty} ${symbol} shares, estimated value ${marketValue}, broker position ${positionId}`,
    danger: true,
  });
  if (!ok) return;
  closeButton.disabled = true;
  if (confirmPrimary) confirmPrimary.disabled = true;
  if (confirmPrimaryLabel) confirmPrimaryLabel.textContent = "Working...";
  try {
    const result = await postJson(`/api/trading/positions/${encodeURIComponent(positionId)}/close`, { symbol });
    showToast(`Close recorded${result.broker_order_id ? ` · broker order ${result.broker_order_id}` : ""}`);
  } catch (error) {
    closeButton.disabled = false;
    showToast(error.message, true);
  }
});

const lineageDialog = document.querySelector("[data-lineage-dialog]");
const lineageContent = document.querySelector("[data-lineage-content]");
document.querySelector("[data-dialog-close]")?.addEventListener("click", () => lineageDialog?.close());

const openLineageUrl = async (url) => {
  try {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`Lineage failed: ${response.status}`);
    if (lineageContent) lineageContent.innerHTML = await response.text();
    lineageDialog?.showModal();
  } catch (error) {
    showToast(error.message, true);
  }
};

document.addEventListener("click", async (event) => {
  const trigger = event.target.closest("[data-open-lineage]");
  if (!trigger) return;
  event.preventDefault();
  event.stopPropagation();
  await openLineageUrl(trigger.dataset.lineageUrl);
});

document.addEventListener("click", async (event) => {
  const row = event.target.closest("[data-lineage-url]");
  if (!row || event.target.closest("button, a, form")) return;
  await openLineageUrl(row.dataset.lineageUrl);
});

document.addEventListener("click", (event) => {
  const trigger = event.target.closest("[data-open-basket-lineage]");
  if (!trigger) return;
  const template = document.querySelector("[data-basket-lineage-template]");
  if (lineageContent && template) lineageContent.innerHTML = template.innerHTML;
  lineageDialog?.showModal();
});

const shortlistFilters = document.querySelector("[data-shortlist-filters]");
if (shortlistFilters) {
  const sideSelect = shortlistFilters.querySelector("[data-shortlist-side]");
  const sectorSelect = shortlistFilters.querySelector("[data-shortlist-sector]");
  const rows = Array.from(document.querySelectorAll("[data-shortlist-row]"));
  const applyFilters = () => {
    const side = sideSelect?.value || "";
    const sector = sectorSelect?.value || "";
    rows.forEach((row) => {
      const sideOk = !side || row.dataset.side === side;
      const sectorOk = !sector || row.dataset.sector === sector;
      row.hidden = !(sideOk && sectorOk);
    });
  };
  sideSelect?.addEventListener("change", applyFilters);
  sectorSelect?.addEventListener("change", applyFilters);
}

const positionsBody = document.querySelector("[data-positions-body]");
if (positionsBody) {
  let failures = 0;
  let inFlight = false;
  const banner = document.querySelector("[data-position-stale-banner]");
  const refreshUrl = positionsBody.dataset.refreshUrl;
  const refreshMs = Number(positionsBody.dataset.refreshMs || 5000);
  const moneyFormatter = new Intl.NumberFormat(undefined, { style: "currency", currency: "USD" });
  const pctFormatter = new Intl.NumberFormat(undefined, { style: "percent", minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const updateSignedClass = (element, value) => {
    if (!element) return;
    element.classList.toggle("text-up", value > 0);
    element.classList.toggle("text-down", value < 0);
  };
  const signedMoney = (value) => {
    const number = Number(value || 0);
    if (number === 0) return moneyFormatter.format(0);
    return `${number > 0 ? "+" : "-"}${moneyFormatter.format(Math.abs(number))}`;
  };
  const signedPct = (value) => {
    const number = Number(value || 0);
    if (number === 0) return pctFormatter.format(0);
    return `${number > 0 ? "+" : "-"}${pctFormatter.format(Math.abs(number))}`;
  };
  const updatePositionSummary = (summary = {}, refreshedAt = "") => {
    const count = Number(summary.position_count || 0);
    const marketValue = Number(summary.position_market_value || 0);
    const costBasis = Number(summary.position_cost_basis || 0);
    const unrealized = Number(summary.position_unrealized_pl || 0);
    const unrealizedPct = Number(summary.position_unrealized_plpc || 0);
    const marketElement = document.querySelector('[data-position-summary="market_value"]');
    const countElement = document.querySelector('[data-position-summary="count"]');
    const costElement = document.querySelector('[data-position-summary="cost_basis"]');
    const unrealizedElement = document.querySelector('[data-position-summary="unrealized_pl"]');
    const unrealizedPctElement = document.querySelector('[data-position-summary="unrealized_plpc"]');
    const unrealizedPctDetail = document.querySelector('[data-position-summary="unrealized_plpc_detail"]');
    const staleness = document.querySelector("[data-position-staleness]");
    if (marketElement) marketElement.textContent = moneyFormatter.format(marketValue);
    if (countElement) countElement.textContent = `${count} open position${count === 1 ? "" : "s"}`;
    if (costElement) costElement.textContent = moneyFormatter.format(costBasis);
    if (unrealizedElement) unrealizedElement.textContent = signedMoney(unrealized);
    if (unrealizedPctElement) unrealizedPctElement.textContent = signedPct(unrealizedPct);
    if (unrealizedPctDetail) unrealizedPctDetail.textContent = `${signedPct(unrealizedPct)} unrealized`;
    updateSignedClass(unrealizedElement, unrealized);
    updateSignedClass(unrealizedPctElement, unrealizedPct);
    if (staleness) staleness.textContent = `live, refreshed ${refreshedAt || "not available"}`;
  };
  window.setInterval(async () => {
    if (document.hidden || inFlight) return;
    inFlight = true;
    try {
      const response = await fetch(refreshUrl);
      if (!response.ok) throw new Error(`Position refresh failed: ${response.status}`);
      const contentType = response.headers.get("content-type") || "";
      if (contentType.includes("application/json")) {
        const payload = await response.json();
        positionsBody.innerHTML = payload.body_html || "";
        updatePositionSummary(payload.summary, payload.refreshed_at);
      } else {
        positionsBody.innerHTML = await response.text();
      }
      failures = 0;
      if (banner) banner.hidden = true;
    } catch (error) {
      failures += 1;
      if (banner && failures >= 2) {
        banner.hidden = false;
        banner.textContent = "Position prices may be stale.";
      }
      console.error(error);
    } finally {
      inFlight = false;
    }
  }, refreshMs);
}
