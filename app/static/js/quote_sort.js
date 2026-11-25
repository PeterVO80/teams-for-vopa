// Quote lines: drag to change the order (SortableJS, like the kanban board).
//  - the grip on a section title moves the whole section (title + its lines);
//  - the grip on a line moves that line, also into another section.
// Lines above the first section title stay above it. After a drop the new
// order of all lines is saved (POST data-reorder-url) and the page reloads,
// so the subtotals are right again.
(function () {
  "use strict";
  const table = document.querySelector("table.quote-lines[data-reorder-url]");
  if (!table || !window.Sortable) return;

  async function save() {
    const order = [];
    table.querySelectorAll("tbody.quote-section").forEach((tbody) => {
      if (tbody.dataset.section) order.push(Number(tbody.dataset.section));
      tbody.querySelectorAll("tr.quote-line").forEach((row) => order.push(Number(row.dataset.id)));
    });
    try {
      await window.postJSON(table.dataset.reorderUrl, { order });
    } catch (error) {
      alert("Opslaan van de volgorde mislukt - de pagina wordt herladen.");
    }
    window.location.reload();
  }

  // Sections (tbody elements with a title) within the table.
  new Sortable(table, {
    draggable: "tbody[data-section]",
    handle: ".section-handle",
    animation: 150,
    ghostClass: "quote-drag-ghost",
    // Nothing above the lines without a section.
    onMove: (event) => !(event.related.matches("tbody:not([data-section])") && !event.willInsertAfter),
    onEnd: (event) => { if (event.oldIndex !== event.newIndex) save(); },
  });

  // Lines within and between sections.
  table.querySelectorAll("tbody.quote-section").forEach((tbody) => {
    new Sortable(tbody, {
      group: "quote-lines",
      draggable: "tr.quote-line",
      handle: ".line-handle",
      animation: 150,
      ghostClass: "quote-drag-ghost",
      // A line can't go above its section title.
      onMove: (event) => !(event.related.classList.contains("quote-section-title") && !event.willInsertAfter),
      onEnd: (event) => { if (event.from !== event.to || event.oldIndex !== event.newIndex) save(); },
    });
  });
})();
