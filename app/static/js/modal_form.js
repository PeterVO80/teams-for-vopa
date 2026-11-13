/**
 * Forms in a popup (Bootstrap modal), loaded from the server on demand.
 *
 * Usage in a template:
 *   <a href="/full/page/url" data-modal-url="/form/url">...</a>
 * The `href` stays as a fallback (e.g. opening the link in a new tab).
 *
 * Protocol with the server (see `_is_modal()` in blueprints/team.py):
 *  - Every request carries the header `X-Modal: 1`.
 *  - GET returns an HTML fragment: the content of `.modal-content`.
 *  - A <form data-modal-form> inside it is submitted with fetch():
 *      200 + JSON {ok: true}  -> saved: the page reloads (shows the flash message)
 *      422 + HTML             -> validation errors: the fragment is replaced
 *  - A <button data-modal-post="url" data-confirm="..."> posts to `url`
 *    (used for "delete") and reloads the page on success.
 *
 * After new content is inserted, a `modal:loaded` event is dispatched on the
 * content element so page scripts can initialise it (see team.js).
 */
(function () {
  "use strict";

  const modalEl = document.getElementById("app-modal");
  if (!modalEl) return;
  const content = document.getElementById("app-modal-content");
  const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
  const HEADERS = { "X-Modal": "1" };

  function setContent(html) {
    content.innerHTML = html;
    content.dispatchEvent(new CustomEvent("modal:loaded", { bubbles: true }));
    // Put the cursor in the first empty field (or the first field).
    const fields = [...content.querySelectorAll("input:not([type=hidden]), select, textarea")];
    (fields.find((f) => !f.value) || fields[0])?.focus();
  }

  function showError(message) {
    content.innerHTML = `
      <div class="modal-header"><h1 class="modal-title fs-5">Fout</h1>
        <button type="button" class="btn-close" data-bs-dismiss="modal"></button></div>
      <div class="modal-body"><div class="alert alert-danger mb-0">${message}</div></div>`;
  }

  /** Load `url` into the popup and show it. Public: window.ModalForm.open(url). */
  async function open(url) {
    try {
      const response = await fetch(url, { headers: HEADERS });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      setContent(await response.text());
    } catch (error) {
      showError("Het formulier kon niet geladen worden. Probeer opnieuw.");
    }
    modal.show();
  }

  /** Handle the server's answer to a POST from the popup. */
  async function handleResponse(response) {
    if (response.ok && response.headers.get("Content-Type")?.includes("application/json")) {
      window.location.reload();  // saved: show the updated page + flash message
    } else if (response.status === 422) {
      setContent(await response.text());  // validation errors: show them
    } else {
      throw new Error(`HTTP ${response.status}`);
    }
  }

  // Open the popup for every [data-modal-url] link or button.
  document.addEventListener("click", (event) => {
    const trigger = event.target.closest("[data-modal-url]");
    if (!trigger || event.ctrlKey || event.metaKey || event.shiftKey) return;  // allow "open in new tab"
    event.preventDefault();
    open(trigger.dataset.modalUrl);
  });

  // Submit forms inside the popup without leaving the page.
  content.addEventListener("submit", async (event) => {
    const form = event.target.closest("form[data-modal-form]");
    if (!form) return;
    event.preventDefault();
    const submit = form.querySelector("[type=submit]");
    if (submit) submit.disabled = true;  // prevent double submits
    try {
      const response = await fetch(form.action, { method: "POST", body: new FormData(form), headers: HEADERS });
      await handleResponse(response);
    } catch (error) {
      showError("Opslaan mislukt. Controleer de verbinding en probeer opnieuw.");
    } finally {
      if (submit) submit.disabled = false;
    }
  });

  // Secondary actions such as "delete".
  content.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-modal-post]");
    if (!button) return;
    if (button.dataset.confirm && !window.confirm(button.dataset.confirm)) return;
    const token = document.querySelector('meta[name="csrf-token"]').content;
    try {
      const response = await fetch(button.dataset.modalPost, {
        method: "POST",
        headers: { ...HEADERS, "X-CSRFToken": token },
      });
      await handleResponse(response);
    } catch (error) {
      showError("De actie is mislukt. Probeer opnieuw.");
    }
  });

  window.ModalForm = { open, element: modalEl };
})();
