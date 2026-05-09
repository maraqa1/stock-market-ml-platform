document.addEventListener("keydown", (event) => {
  if (event.key !== "/" || event.ctrlKey || event.metaKey || event.altKey) return;
  if (event.target.closest("input, textarea, select, [contenteditable='true']")) return;
  const search = document.querySelector("#global-search");
  if (!search) return;
  event.preventDefault();
  search.focus();
});
