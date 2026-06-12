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
  const titleText = (value) => String(value || "normal").replaceAll("_", " ").replace(/\b\w/g, (char) => char.toUpperCase());
  const updatePositionSummary = (summary = {}, refreshedAt = "", state = {}) => {
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
    const basketStateElement = document.querySelector('[data-position-summary="basket_state"]');
    const newEntriesElement = document.querySelector('[data-position-summary="new_entries_paused"]');
    const redPositionElement = document.querySelector('[data-position-summary="red_position_pct"]');
    const basketReturnElement = document.querySelector('[data-position-summary="basket_return"]');
    const basketPauseBanner = document.querySelector("[data-basket-pause-banner]");
    const staleness = document.querySelector("[data-position-staleness]");
    if (marketElement) marketElement.textContent = moneyFormatter.format(marketValue);
    if (countElement) countElement.textContent = `${count} open position${count === 1 ? "" : "s"}`;
    if (costElement) costElement.textContent = moneyFormatter.format(costBasis);
    if (unrealizedElement) unrealizedElement.textContent = signedMoney(unrealized);
    if (unrealizedPctElement) unrealizedPctElement.textContent = signedPct(unrealizedPct);
    if (unrealizedPctDetail) unrealizedPctDetail.textContent = `${signedPct(unrealizedPct)} unrealized`;
    if (basketStateElement) basketStateElement.textContent = titleText(state.basket_state);
    if (newEntriesElement) newEntriesElement.textContent = `New entries paused: ${state.new_entries_paused ? "yes" : "no"}`;
    if (redPositionElement) redPositionElement.textContent = signedPct(Number(state.red_position_pct || 0));
    if (basketReturnElement) basketReturnElement.textContent = `Basket return ${signedPct(Number(state.basket_return || unrealizedPct))}`;
    if (basketPauseBanner) {
      const reasonText = state.new_entries_paused ? String(state.basket_risk_reason_text || "") : "";
      basketPauseBanner.textContent = reasonText;
      basketPauseBanner.hidden = !reasonText;
    }
    updateSignedClass(unrealizedElement, unrealized);
    updateSignedClass(unrealizedPctElement, unrealizedPct);
    updateSignedClass(basketReturnElement, Number(state.basket_return || unrealizedPct));
    if (redPositionElement) redPositionElement.classList.toggle("text-down", Number(state.red_position_pct || 0) > 0.7);
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
        updatePositionSummary(payload.summary, payload.refreshed_at, payload);
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


const heldVsCandidateZone = document.querySelector("[data-held-vs-candidate-url]");
if (heldVsCandidateZone) {
  let inFlight = false;
  const refreshUrl = heldVsCandidateZone.dataset.heldVsCandidateUrl;
  const refreshMs = Number(heldVsCandidateZone.dataset.heldVsCandidateRefreshMs || 5000);
  const moneyFormatter = new Intl.NumberFormat(undefined, { style: "currency", currency: "USD" });
  const pctFormatter = new Intl.NumberFormat(undefined, { style: "percent", minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const intFormatter = new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 });
  const generated = heldVsCandidateZone.querySelector("[data-held-vs-generated]");
  const heldBody = heldVsCandidateZone.querySelector("[data-held-vs-held-body]");
  const availableBody = heldVsCandidateZone.querySelector("[data-held-vs-available-body]");

  const escapeHtml = (value) => String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
  const titleText = (value) => String(value || "missing").replaceAll("_", " ").replace(/\b\w/g, (char) => char.toUpperCase());
  const pill = (value) => `<span class="pill pill-info">${escapeHtml(titleText(value))}</span>`;
  const sideText = (value) => {
    const raw = String(value || "").toLowerCase();
    if (raw === "short") return "▼ Short";
    if (raw === "long") return "▲ Long";
    return escapeHtml(value || "-");
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
  const numberText = (value) => {
    const number = Number(value);
    return Number.isFinite(number) ? intFormatter.format(number) : "-";
  };
  const updateSummary = (summary = {}) => {
    const update = (key, value) => {
      const element = heldVsCandidateZone.querySelector(`[data-held-vs-summary="${key}"]`);
      if (element) element.textContent = value;
    };
    update("open_positions", numberText(summary.open_positions || 0));
    update("held_warning_rows", numberText(summary.held_warning_rows || 0));
    update("available_candidates", numberText(summary.available_candidates || 0));
    update("unrealized_pl", signedMoney(summary.unrealized_pl || 0));
    update("unrealized_plpc_basis", `${signedPct(summary.unrealized_plpc_basis || 0)} open basis`);
    update("top_candidate", summary.top_candidate || "None");
    update("top_candidate_edge_bps", `${numberText(summary.top_candidate_edge_bps || 0)} bps edge`);
  };
  const renderHeld = (rows = []) => {
    if (!heldBody) return;
    if (!rows.length) {
      heldBody.innerHTML = '<tr><td colspan="9" class="empty small">No open broker positions found in the latest tracking file.</td></tr>';
      return;
    }
    heldBody.innerHTML = rows.map((row) => `
      <tr>
        <td class="num-l col-pinned" data-sort-value="${escapeHtml(row.symbol)}"><a href="/symbols/${encodeURIComponent(row.symbol)}">${escapeHtml(row.symbol)}</a></td>
        <td>${sideText(row.position_side)}</td>
        <td data-sort-value="${Number(row.unrealized_pl || 0)}">${signedMoney(row.unrealized_pl || 0)} ${signedPct(row.unrealized_plpc || 0)}</td>
        <td>${pill(row.trade_quality_status || "missing")}</td>
        <td class="num" data-sort-value="${Number(row.directional_expected_edge_bps || 0)}">${numberText(row.directional_expected_edge_bps)}</td>
        <td>${pill(row.holding_quality || "missing")}</td>
        <td title="${escapeHtml(row.decision_reason || "")}">${escapeHtml(titleText(row.decision))}</td>
        <td>${pill(row.rotation_flag || "watch")}</td>
        <td>${escapeHtml(String(row.warnings || "none").replaceAll("|", ", ").replaceAll("_", " "))}</td>
      </tr>`).join("");
  };
  const renderAvailable = (rows = []) => {
    if (!availableBody) return;
    if (!rows.length) {
      availableBody.innerHTML = '<tr><td colspan="8" class="empty small">No eligible non-held candidates available after excluding held symbols and open broker orders.</td></tr>';
      return;
    }
    heldVsCandidateZone.querySelectorAll('[data-held-vs-summary="top_candidate"]').forEach((element) => {
      element.textContent = rows[0]?.symbol || "None";
    });
    availableBody.innerHTML = rows.map((row) => `
      <tr>
        <td class="num-l col-pinned" data-sort-value="${escapeHtml(row.symbol)}"><a href="/symbols/${encodeURIComponent(row.symbol)}">${escapeHtml(row.symbol)}</a></td>
        <td>${sideText(row.side)}</td>
        <td>${pill(row.trade_quality_status || "unknown")}</td>
        <td class="num" data-sort-value="${Number(row.directional_expected_edge_bps || 0)}">${numberText(row.directional_expected_edge_bps)}</td>
        <td class="num" data-sort-value="${Number(row.directional_risk_score_bps || 0)}">${numberText(row.directional_risk_score_bps)}</td>
        <td class="num" data-sort-value="${Number(row.confidence_score || 0)}">${Number(row.confidence_score || 0).toFixed(3)}</td>
        <td class="num" data-sort-value="${Number(row.candidate_rank || 0)}">${numberText(row.candidate_rank)}</td>
        <td>${escapeHtml(row.sector || "-")}</td>
      </tr>`).join("");
  };
  window.setInterval(async () => {
    if (document.hidden || inFlight) return;
    inFlight = true;
    try {
      const response = await fetch(refreshUrl);
      if (!response.ok) throw new Error(`Held vs candidate refresh failed: ${response.status}`);
      const payload = await response.json();
      if (generated) generated.textContent = `read-only · ${payload.generated_at || "not available"}`;
      updateSummary(payload.summary || {});
      renderHeld(payload.held_positions || []);
      renderAvailable(payload.available_candidates || []);
    } catch (error) {
      console.error(error);
    } finally {
      inFlight = false;
    }
  }, refreshMs);
}
