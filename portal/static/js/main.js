document.addEventListener("click", (event) => {
  const row = event.target.closest("tr");
  if (!row) return;
  row.classList.toggle("selected");
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
  let inFlight = false;

  window.setInterval(async () => {
    if (inFlight || document.hidden || !target) return;
    inFlight = true;
    try {
      const response = await fetch(refreshUrl);
      if (!response.ok) throw new Error(`Pipeline refresh failed: ${response.status}`);
      target.innerHTML = await response.text();
    } catch (error) {
      console.error(error);
    } finally {
      inFlight = false;
    }
  }, refreshMs);
}
