const parseNumber = (value) => {
  const parsed = Number(String(value || "").replace(/[^0-9.+-]/g, ""));
  return Number.isFinite(parsed) ? parsed : 0;
};

const tableText = (row) => row.textContent.toLowerCase();

const rowValue = (row, index, type) => {
  const cell = row.children[index];
  const raw = cell?.dataset.sortValue || cell?.textContent || "";
  return type === "number" ? parseNumber(raw) : raw.trim().toLowerCase();
};

const updateUrl = (params) => {
  const url = new URL(window.location);
  Object.entries(params).forEach(([key, value]) => {
    if (value) url.searchParams.set(key, value);
    else url.searchParams.delete(key);
  });
  window.history.pushState({}, "", url);
};

const sortTable = (table, key, direction, updateHistory = true) => {
  const header = table.querySelector(`th[data-sort-key="${key}"]`);
  if (!header) return;
  const index = Array.from(header.parentElement.children).indexOf(header);
  const type = header.dataset.sortType || "string";
  const tbody = table.tBodies[0];
  const rows = Array.from(tbody.querySelectorAll("tr")).filter((row) => !row.querySelector(".empty"));
  const factor = direction === "desc" ? -1 : 1;
  rows.sort((left, right) => {
    const a = rowValue(left, index, type);
    const b = rowValue(right, index, type);
    if (a < b) return -1 * factor;
    if (a > b) return 1 * factor;
    return 0;
  });
  rows.forEach((row) => tbody.appendChild(row));
  table.querySelectorAll("th[data-sortable]").forEach((th) => {
    th.removeAttribute("aria-sort");
    th.querySelector(".sort-indicator")?.remove();
  });
  header.setAttribute("aria-sort", direction === "desc" ? "descending" : "ascending");
  const indicator = document.createElement("span");
  indicator.className = "sort-indicator";
  indicator.textContent = direction === "desc" ? " ▼" : " ▲";
  header.appendChild(indicator);
  if (updateHistory) updateUrl({ sort: key, dir: direction });
};

const filterTables = (value, updateHistory = true) => {
  const query = String(value || "").trim().toLowerCase();
  document.querySelectorAll("table[data-table]").forEach((table) => {
    table.querySelectorAll("tbody tr").forEach((row) => {
      if (row.querySelector(".empty")) return;
      row.hidden = query ? !tableText(row).includes(query) : false;
    });
  });
  if (updateHistory) updateUrl({ q: query });
};

const debounce = (fn, delay) => {
  let timer;
  return (...args) => {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => fn(...args), delay);
  };
};

const initTableControls = () => {
  const params = new URLSearchParams(window.location.search);
  const initialQuery = params.get("q") || "";
  const initialSort = params.get("sort") || "";
  const initialDirection = params.get("dir") === "desc" ? "desc" : "asc";

  document.querySelectorAll("table[data-table] th[data-sortable]").forEach((header) => {
    header.tabIndex = 0;
    header.addEventListener("click", () => {
      const table = header.closest("table");
      const key = header.dataset.sortKey;
      const current = header.getAttribute("aria-sort");
      const direction = current === "ascending" ? "desc" : "asc";
      sortTable(table, key, direction);
    });
    header.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        header.click();
      }
    });
  });

  document.querySelectorAll("[data-table-filter]").forEach((input) => {
    input.value = initialQuery;
    input.addEventListener("input", debounce(() => filterTables(input.value), 100));
  });

  if (initialQuery) filterTables(initialQuery, false);
  if (initialSort) {
    document.querySelectorAll("table[data-table]").forEach((table) => {
      sortTable(table, initialSort, initialDirection, false);
    });
  }
};

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initTableControls);
} else {
  initTableControls();
}
