// Read-only access to modules (app/permissions.py): hide the buttons that
// would change something the user may only read, and show forms of those
// modules read-only. The server refuses such changes anyway (403); this only
// keeps the screens clean. Rules: <script id="read-only-rules"> in base.html.
(function () {
  "use strict";
  const data = document.getElementById("read-only-rules");
  if (!data) return;
  const rules = JSON.parse(data.textContent);
  const hide = rules.hide.map((r) => new RegExp(r));
  const post = rules.post.map((r) => new RegExp(r));

  function path(url) {
    try { return new URL(url, window.location.href).pathname; } catch (e) { return ""; }
  }
  const matches = (patterns, url) => !!url && patterns.some((r) => r.test(path(url)));

  function apply(root) {
    root.querySelectorAll("a[href], [data-modal-url]").forEach((el) => {
      if (matches(hide, el.getAttribute("data-modal-url")) || (el.tagName === "A" && matches(hide, el.getAttribute("href")))) {
        el.classList.add("d-none");
      }
    });
    root.querySelectorAll("[data-modal-post]").forEach((el) => {
      if (matches(post, el.dataset.modalPost)) el.classList.add("d-none");
    });
    root.querySelectorAll("form").forEach((form) => {
      if ((form.getAttribute("method") || "get").toLowerCase() !== "post" || form.dataset.readOnly) return;
      if (!matches(post, form.getAttribute("action") || window.location.href)) return;
      form.dataset.readOnly = "1";
      const fields = form.querySelectorAll("input:not([type=hidden]), select, textarea");
      if (!fields.length) {                       // a delete / action button
        form.classList.add("d-none");
        return;
      }
      fields.forEach((field) => { field.disabled = true; });
      form.querySelectorAll("button").forEach((button) => {
        if ((button.getAttribute("type") || "submit") === "submit") button.classList.add("d-none");
      });
      const note = document.createElement("div");
      note.className = "alert alert-secondary py-2 small";
      note.innerHTML = '<i class="bi bi-eye"></i> Alleen lezen: je hebt geen schrijfrechten voor deze module.';
      (form.querySelector(".modal-body") || form).prepend(note);
    });
  }

  apply(document);
  document.addEventListener("modal:loaded", (event) => apply(event.target));
})();
