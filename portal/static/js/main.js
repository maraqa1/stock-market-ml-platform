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
