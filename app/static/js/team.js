/**
 * Team planning page.
 *
 * 1. Selecting days in the planning grid
 *    Press the mouse on a day of a team member and drag to another day in the
 *    same row: the selected days are highlighted. On release, the planning
 *    popup opens with that person and the first/last selected day as start
 *    and end date. A simple click selects just that one day.
 *    Pointer events are used, so this works with mouse, pen and touch (on a
 *    touch screen a drag scrolls the table, a tap selects a day).
 *
 * 2. Behaviour of the planning form (popup and full page)
 *    - the project selector is only shown for type "Project";
 *    - moving the start date past the end date moves the end date along;
 *    - choosing a Saturday/Sunday as start or end ticks "plan weekend days".
 */
(function () {
  "use strict";

  // ------------------------------------------------------------ 1. grid
  const grid = document.querySelector(".planning-grid[data-new-url]");
  let selection = null;  // {cells, anchor, current} while selecting

  function highlight() {
    const [from, to] = [selection.anchor, selection.current].sort((a, b) => a - b);
    selection.cells.forEach((cell, i) => cell.classList.toggle("selecting", i >= from && i <= to));
  }

  function clearSelection() {
    grid?.querySelectorAll(".planning-cell.selecting").forEach((c) => c.classList.remove("selecting"));
    selection = null;
  }

  function finishSelection() {
    const [from, to] = [selection.anchor, selection.current].sort((a, b) => a - b);
    const first = selection.cells[from];
    const params = new URLSearchParams({
      user: first.dataset.user,
      start: first.dataset.day,
      end: selection.cells[to].dataset.day,
    });
    selection = null;  // keep the highlight visible until the popup closes
    window.ModalForm.open(`${grid.dataset.newUrl}?${params}`);
  }

  if (grid) {
    grid.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) return;                       // left button / touch only
      const cell = event.target.closest(".planning-cell");
      if (!cell || event.target.closest(".planning-chip")) return;  // chips open their own popup
      clearSelection();
      const cells = [...cell.parentElement.querySelectorAll(".planning-cell")];
      const index = cells.indexOf(cell);
      selection = { cells, anchor: index, current: index };
      highlight();
      if (event.pointerType === "mouse") event.preventDefault();  // no text selection while dragging
    });

    document.addEventListener("pointermove", (event) => {
      if (!selection) return;
      // elementFromPoint also works for touch, where events stay on the start cell.
      const cell = document.elementFromPoint(event.clientX, event.clientY)?.closest(".planning-cell");
      const index = selection.cells.indexOf(cell);         // -1 for other rows: ignored
      if (index !== -1 && index !== selection.current) {
        selection.current = index;
        highlight();
      }
    });

    document.addEventListener("pointerup", () => { if (selection) finishSelection(); });
    // The browser cancels the pointer when a touch turns into scrolling.
    document.addEventListener("pointercancel", clearSelection);
    window.ModalForm?.element.addEventListener("hidden.bs.modal", clearSelection);
  }

  // ------------------------------------------------------- 2. the form
  function initAssignmentForm(root) {
    const kind = root.querySelector("#kind");
    const projectField = root.querySelector("[data-project-field]");
    const start = root.querySelector("#start_date");
    const end = root.querySelector("#end_date");
    if (!kind || !start || !end) return;  // not the planning form

    const toggleProject = () => projectField?.classList.toggle("d-none", kind.value !== "project");
    kind.addEventListener("change", toggleProject);
    toggleProject();

    const weekends = root.querySelector("#include_weekends");
    const isWeekend = (value) => value && [0, 6].includes(new Date(`${value}T12:00:00`).getDay());
    const checkWeekend = () => {
      if (weekends && (isWeekend(start.value) || isWeekend(end.value))) weekends.checked = true;
    };

    start.addEventListener("change", () => {
      if (!end.value || end.value < start.value) end.value = start.value;
      checkWeekend();
    });
    end.addEventListener("change", checkWeekend);
  }

  initAssignmentForm(document);  // full-page form
  document.addEventListener("modal:loaded", (event) => initAssignmentForm(event.target));  // popup
})();
