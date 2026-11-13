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
 * 2. Changing planning bars directly on the board
 *    - drag the left/right edge of a bar: change the start/end date;
 *    - drag the bar itself: move it to other days (same duration) and/or to
 *      another team member's row.
 *    A preview follows the pointer; on release the change is saved
 *    (POST data-reschedule-url) and the board reloads. Escape cancels.
 *    Moving less than a few pixels counts as a click: that still opens the
 *    edit popup.
 *
 *    Hovering a bar shows its details immediately (Bootstrap tooltip without
 *    delay instead of the browser's slow native title tooltip).
 *
 * 3. Behaviour of the planning form (popup and full page)
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
    if (grid.dataset.project) params.set("project", grid.dataset.project);  // project page
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
      // Find the day column under the pointer by its x position only: this
      // keeps working while dragging across a planning bar, outside the row,
      // and on touch screens (where events stay on the start cell).
      const index = selection.cells.findIndex((cell) => {
        const rect = cell.getBoundingClientRect();
        return event.clientX >= rect.left && event.clientX < rect.right;
      });
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

  // ------------------------------------------- 2. resize / move bars
  const DRAG_THRESHOLD = 5;   // pixels before a press on a bar becomes a drag
  let drag = null;            // state of the bar being dragged (see pointerdown)
  let suppressClick = false;  // swallow the click that follows a drag

  const rowOf = (el) => el.closest(".planning-row");
  const cellsOf = (row) => [...row.querySelectorAll(".planning-cell")];

  /** Day column index under x, clamped to the visible period. */
  function indexAtX(row, x) {
    const cells = cellsOf(row);
    const index = cells.findIndex((c) => {
      const r = c.getBoundingClientRect();
      return x >= r.left && x < r.right;
    });
    if (index !== -1) return index;
    return x < cells[0].getBoundingClientRect().left ? 0 : cells.length - 1;
  }

  /** ISO date plus n days (noon avoids daylight-saving surprises). */
  function addDays(iso, n) {
    const d = new Date(`${iso}T12:00:00`);
    d.setDate(d.getDate() + n);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  }

  function place(el, first, last) {
    el.style.gridColumn = `${first + 4} / span ${last - first + 1}`;  // 3 fixed columns before the days
  }

  function startDrag() {
    drag.active = true;
    const { bar } = drag;
    hideTooltips();
    document.body.classList.add("planning-dragging");  // no tooltips while dragging
    drag.originalColumn = bar.style.gridColumn;
    if (drag.mode === "move") {
      drag.ghost = bar.cloneNode(true);
      drag.ghost.classList.add("drag-ghost");
      drag.ghost.removeAttribute("data-modal-url");
      grid.querySelectorAll(`[data-assignment="${bar.dataset.assignment}"]`)
        .forEach((b) => b.classList.add("drag-source"));
      grid.classList.add("dragging");
    } else {
      grid.classList.add("resizing");
    }
  }

  function updateDrag(event) {
    const { bar } = drag;
    const first = Number(bar.dataset.first);
    const last = Number(bar.dataset.last);

    if (drag.mode === "start" || drag.mode === "end") {
      const cells = cellsOf(drag.row);
      const index = indexAtX(drag.row, event.clientX);
      if (drag.mode === "start") {
        const newFirst = Math.min(index, last);
        place(bar, newFirst, last);
        drag.result = { start: cells[newFirst].dataset.day, end: bar.dataset.end, user: drag.row.dataset.user };
      } else {
        const newLast = Math.max(index, first);
        place(bar, first, newLast);
        drag.result = { start: bar.dataset.start, end: cells[newLast].dataset.day, user: drag.row.dataset.user };
      }
      return;
    }

    // Move: the row under the pointer is the new team member.
    const hovered = document.elementFromPoint(event.clientX, event.clientY)?.closest(".planning-row:not(.planning-head)");
    if (hovered && grid.contains(hovered)) drag.targetRow = hovered;
    const row = drag.targetRow;
    const delta = indexAtX(row, event.clientX) - drag.originIndex;  // one column = one day
    const count = cellsOf(row).length;
    const ghost = drag.ghost;
    if (ghost.parentElement !== row) {
      row.appendChild(ghost);
      ghost.style.gridRow = "2";  // first lane (row 1 is the spacing above the bars)
      ghost.style.backgroundColor = row.dataset.color;
      ghost.style.color = row.dataset.textColor;
    }
    place(ghost, Math.max(0, Math.min(count - 1, first + delta)), Math.max(0, Math.min(count - 1, last + delta)));
    drag.result = {
      start: addDays(bar.dataset.start, delta),
      end: addDays(bar.dataset.end, delta),
      user: row.dataset.user,
    };
  }

  function endDrag(save) {
    const d = drag;
    drag = null;
    grid.classList.remove("dragging", "resizing");
    document.body.classList.remove("planning-dragging");
    if (!d || !d.active) return;
    suppressClick = true;
    setTimeout(() => { suppressClick = false; }, 0);

    const unchanged = !d.result || (d.result.start === d.bar.dataset.start && d.result.end === d.bar.dataset.end
      && d.result.user === rowOf(d.bar).dataset.user);
    if (!save || unchanged) {  // cancelled or nothing changed: restore
      d.bar.style.gridColumn = d.originalColumn;
      d.ghost?.remove();
      grid.querySelectorAll(".drag-source").forEach((b) => b.classList.remove("drag-source"));
      return;
    }
    reschedule(d.bar.dataset.rescheduleUrl, d.result);
  }

  async function reschedule(url, result) {
    const token = document.querySelector('meta[name="csrf-token"]').content;
    try {
      const response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": token },
        body: JSON.stringify({ start_date: result.start, end_date: result.end, user_id: Number(result.user) }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok) alert(data.error || "Opslaan mislukt.");
    } catch (error) {
      alert("Opslaan mislukt - controleer de verbinding.");
    }
    window.location.reload();  // redraw bars, lanes and load colours
  }

  // Instant tooltips with the full description of a bar.
  function hideTooltips() {
    document.querySelectorAll(".planning-chip[data-tooltip-html]")
      .forEach((el) => bootstrap.Tooltip.getInstance(el)?.hide());
  }

  if (grid) {
    // All boards on the page (the project page has one per tab content).
    document.querySelectorAll(".planning-chip[data-tooltip-html]").forEach((el) => {
      new bootstrap.Tooltip(el, {
        title: el.dataset.tooltipHtml,
        html: true,               // sanitised by Bootstrap's allow-list
        delay: { show: 0, hide: 0 },
        animation: false,
        placement: "top",
        container: "body",
        customClass: "planning-tooltip",
      });
    });
    // A click opens the edit popup: close the tooltip.
    document.addEventListener("click", (event) => {
      if (event.target.closest(".planning-chip")) hideTooltips();
    });

    grid.addEventListener("pointerdown", (event) => {
      const bar = event.target.closest(".planning-chip[data-assignment]");
      if (!bar || event.button !== 0 || bar.classList.contains("drag-ghost")) return;
      const handle = event.target.closest(".bar-handle");
      drag = {
        bar,
        mode: handle ? (handle.classList.contains("start") ? "start" : "end") : "move",
        x: event.clientX,
        y: event.clientY,
        active: false,
        row: rowOf(bar),
        targetRow: rowOf(bar),
        originIndex: indexAtX(rowOf(bar), event.clientX),
      };
      if (event.pointerType === "mouse") event.preventDefault();  // no text selection / native link drag
    });

    document.addEventListener("pointermove", (event) => {
      if (!drag) return;
      if (!drag.active) {
        if (Math.hypot(event.clientX - drag.x, event.clientY - drag.y) < DRAG_THRESHOLD) return;
        startDrag();
      }
      updateDrag(event);
    });
    document.addEventListener("pointerup", () => endDrag(true));
    document.addEventListener("pointercancel", () => endDrag(false));
    document.addEventListener("keydown", (event) => { if (event.key === "Escape" && drag) endDrag(false); });
    // A drag ends with a click on the bar: don't let it open the popup.
    document.addEventListener("click", (event) => {
      if (suppressClick && event.target.closest(".planning-chip")) {
        event.preventDefault();
        event.stopImmediatePropagation();
      }
    }, true);
    // Links are natively draggable; that would interfere with moving bars.
    grid.addEventListener("dragstart", (event) => {
      if (event.target.closest?.(".planning-chip")) event.preventDefault();
    });
  }

  // ------------------------------------------------------- 3. the form
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
