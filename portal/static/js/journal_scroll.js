const journalBody = document.querySelector("[data-journal-body]");
let journalLoading = false;

const journalParams = (cursor) => {
  const params = new URLSearchParams(window.location.search);
  if (cursor) params.set("cursor", cursor);
  return params.toString();
};

const appendJournalRows = async (sentinel) => {
  if (!sentinel?.dataset.nextCursor || journalLoading) return;
  journalLoading = true;
  try {
    const response = await fetch(`/api/journal/events?${journalParams(sentinel.dataset.nextCursor)}`);
    if (!response.ok) throw new Error("Journal unavailable");
    const payload = await response.json();
    sentinel.remove();
    (payload.events || []).forEach((event) => {
      const row = document.createElement("tr");
      row.dataset.eventId = event.id;
      row.innerHTML = `
        <td class="num-l" title="${event.event_at}"><span class="num time">${event.event_at}</span></td>
        <td class="num-l col-pinned" data-sort-value="${event.symbol}"><a href="/symbols/${event.symbol}">${event.symbol}</a></td>
        <td>${event.event_type}</td>
        <td><span class="pill pill-info">${event.source}</span></td>
        <td>${event.details_summary}</td>
      `;
      journalBody.appendChild(row);
    });
    const next = document.createElement("tr");
    next.id = "js-load-more";
    if (payload.next_cursor) {
      next.dataset.nextCursor = payload.next_cursor;
      next.innerHTML = `<td colspan="5"><button class="btn btn-ghost btn-sm" type="button">Load older</button></td>`;
      journalBody.appendChild(next);
      observeJournalSentinel();
    } else {
      next.innerHTML = `<td colspan="5" class="empty small">End of results.</td>`;
      journalBody.appendChild(next);
    }
  } finally {
    journalLoading = false;
  }
};

const observeJournalSentinel = () => {
  const sentinel = document.querySelector("#js-load-more[data-next-cursor]");
  if (!sentinel) return;
  if (!("IntersectionObserver" in window)) {
    sentinel.querySelector("button")?.addEventListener("click", () => appendJournalRows(sentinel));
    return;
  }
  const observer = new IntersectionObserver((entries) => {
    if (entries.some((entry) => entry.isIntersecting)) {
      observer.disconnect();
      appendJournalRows(sentinel);
    }
  });
  observer.observe(sentinel);
};

observeJournalSentinel();
