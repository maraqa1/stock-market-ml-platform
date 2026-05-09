const searchInput = document.querySelector("#global-search");
const searchResults = document.querySelector("#search-results");

let searchTimer;
let highlightedIndex = -1;

const searchOptions = () => Array.from(searchResults?.querySelectorAll("[role='option']") || []);

const escapeHtml = (value) => String(value || "").replace(/[&<>"']/g, (char) => ({
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
}[char]));

const hideSearch = () => {
  if (!searchResults) return;
  searchResults.hidden = true;
  searchResults.innerHTML = "";
  highlightedIndex = -1;
};

const setHighlighted = (index) => {
  const options = searchOptions();
  options.forEach((option, optionIndex) => {
    option.setAttribute("aria-selected", optionIndex === index ? "true" : "false");
  });
  highlightedIndex = index;
};

const renderSearch = (payload) => {
  if (!searchResults) return;
  const groups = payload.groups || [];
  if (!groups.length) {
    searchResults.innerHTML = "<section><ul><li role=\"option\">No results</li></ul></section>";
    searchResults.hidden = false;
    return;
  }
  searchResults.innerHTML = groups
    .map((group) => {
      const items = (group.items || [])
        .map((item) => {
          const label = escapeHtml(item.symbol || item.run_id || "");
          const meta = escapeHtml(item.name || item.side || "");
          const url = escapeHtml(item.url || "#");
          return `<li role="option" aria-selected="false" data-url="${url}"><strong>${label}</strong>${meta ? ` <span>${meta}</span>` : ""}</li>`;
        })
        .join("");
      return items ? `<section><header>${group.label}</header><ul>${items}</ul></section>` : "";
    })
    .join("");
  searchResults.hidden = false;
  setHighlighted(0);
};

const fetchSearch = async () => {
  const value = searchInput?.value.trim() || "";
  if (value.length < 2) {
    hideSearch();
    return;
  }
  try {
    const response = await fetch(`/api/search?q=${encodeURIComponent(value)}&limit=5`);
    if (!response.ok) throw new Error("Search unavailable");
    renderSearch(await response.json());
  } catch (error) {
    if (searchResults) {
      searchResults.innerHTML = "<section><ul><li role=\"option\">Search unavailable</li></ul></section>";
      searchResults.hidden = false;
    }
  }
};

searchInput?.addEventListener("input", () => {
  window.clearTimeout(searchTimer);
  searchTimer = window.setTimeout(fetchSearch, 200);
});

searchInput?.addEventListener("keydown", (event) => {
  const options = searchOptions();
  if (event.key === "Escape") {
    hideSearch();
    searchInput.blur();
    return;
  }
  if (!options.length) return;
  if (event.key === "ArrowDown") {
    event.preventDefault();
    setHighlighted(Math.min(highlightedIndex + 1, options.length - 1));
  } else if (event.key === "ArrowUp") {
    event.preventDefault();
    setHighlighted(Math.max(highlightedIndex - 1, 0));
  } else if (event.key === "Enter") {
    const selected = options[highlightedIndex];
    if (selected?.dataset.url) {
      event.preventDefault();
      window.location = selected.dataset.url;
    }
  }
});

searchResults?.addEventListener("click", (event) => {
  const item = event.target.closest("[role='option']");
  if (item?.dataset.url) window.location = item.dataset.url;
});
