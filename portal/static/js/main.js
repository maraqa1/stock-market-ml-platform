document.addEventListener("click", (event) => {
  const row = event.target.closest("tr");
  if (!row) return;
  row.classList.toggle("selected");
});

