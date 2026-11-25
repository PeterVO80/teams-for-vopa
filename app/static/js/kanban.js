/**
 * Kanban drag & drop for the punch list.
 *
 * Every `.kanban-list` becomes a SortableJS list; all lists share the group
 * "tasks" so cards can move between columns. After a drop we send the new
 * status and the complete card order of the target column to the server
 * (`POST /tasks/<id>/move`). If saving fails the page is reloaded so the
 * board never shows a state that differs from the database.
 */
(function () {
  "use strict";

  const board = document.querySelector(".kanban");
  if (!board || board.hasAttribute("data-read-only")) return;   // read-only: cards can't be moved

  // URL template rendered with task_id=0, e.g. "/tasks/0/move".
  const moveUrlTemplate = board.dataset.moveUrl;
  const moveUrl = (taskId) => moveUrlTemplate.replace("/0/", `/${taskId}/`);

  /** Update the little counter badge in each column header. */
  function refreshCounts() {
    board.querySelectorAll(".kanban-column").forEach((column) => {
      const count = column.querySelectorAll(".kanban-card").length;
      column.querySelector(".kanban-count").textContent = count;
    });
  }

  async function onDrop(event) {
    const card = event.item;
    const targetList = event.to;
    const order = [...targetList.querySelectorAll(".kanban-card")].map((c) => Number(c.dataset.id));

    card.classList.add("saving");
    try {
      await window.postJSON(moveUrl(card.dataset.id), {
        status: targetList.dataset.status,
        order: order,
      });
      refreshCounts();
    } catch (error) {
      alert("Opslaan mislukt - de pagina wordt herladen.");
      window.location.reload();
    } finally {
      card.classList.remove("saving");
    }
  }

  board.querySelectorAll(".kanban-list").forEach((list) => {
    new Sortable(list, {
      group: "tasks",
      animation: 150,
      ghostClass: "kanban-ghost",
      // Ignore tiny mouse movements so clicking a card still opens it.
      delay: 50,
      delayOnTouchOnly: true,
      onEnd: (event) => {
        // Only save when something actually changed.
        if (event.from !== event.to || event.oldIndex !== event.newIndex) {
          onDrop(event);
        }
      },
    });
  });
})();
