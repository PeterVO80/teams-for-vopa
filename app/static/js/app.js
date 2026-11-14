/**
 * Generic behaviour used on every page.
 *
 *  - `data-confirm` on a <form>: ask for confirmation before submitting.
 *  - `data-autosubmit` on a form field: submit the form when it changes.
 *  - `.clickable-row[data-href]`: make a complete table row clickable.
 *  - `window.postJSON()`: fetch() wrapper that sends the CSRF token.
 */
(function () {
  "use strict";

  // Confirmation dialogs for destructive actions (delete, cancel, ...).
  document.addEventListener("submit", (event) => {
    const message = event.target.dataset.confirm;
    if (message && !window.confirm(message)) {
      event.preventDefault();
    }
  });

  // Filters that apply as soon as they change: <select data-autosubmit>.
  // data-autosubmit-clear="field" empties another field first (e.g. the
  // product group when the manufacturer changes).
  document.addEventListener("change", (event) => {
    const field = event.target.closest("[data-autosubmit]");
    if (!field || !field.form) return;
    const clear = field.dataset.autosubmitClear;
    if (clear && field.form.elements[clear]) field.form.elements[clear].value = "";
    field.form.submit();
  });

  // Clickable table rows (ignore clicks on links/buttons inside the row).
  document.addEventListener("click", (event) => {
    const row = event.target.closest(".clickable-row[data-href]");
    if (row && !event.target.closest("a, button, input, form")) {
      window.location = row.dataset.href;
    }
  });

  /**
   * POST a JSON body to `url`, including Flask-WTF's CSRF token header.
   * Resolves with the parsed JSON response, rejects on HTTP errors.
   */
  window.postJSON = async function (url, body) {
    const token = document.querySelector('meta[name="csrf-token"]').content;
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": token },
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    return response.json();
  };
})();
