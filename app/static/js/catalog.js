/**
 * Catalogue screen: selecting supplier articles for import.
 *
 * The selection (catalogue item ids) is kept in sessionStorage, so ticking
 * items on page 1, going to page 2 and ticking more works as expected. On
 * "Selectie importeren" the ids are sent in the hidden `item_ids` field.
 * "Alle ... gefilterde importeren" sends scope=filter instead: the server then
 * imports everything that matches the current filter (no ids needed).
 * After an import the server redirects with ?done=1, which clears the selection.
 */
(function () {
  "use strict";

  const table = document.querySelector("[data-catalog]");
  const form = document.querySelector("[data-import-form]");
  if (!table || !form) return;

  const storageKey = `catalog-selection-${table.dataset.catalog}`;
  const load = () => {
    try { return new Set(JSON.parse(sessionStorage.getItem(storageKey) || "[]")); } catch { return new Set(); }
  };
  const save = () => {
    try { sessionStorage.setItem(storageKey, JSON.stringify([...selection])); } catch { /* private mode */ }
  };

  // After a successful import: start with an empty selection and clean the URL.
  const url = new URL(window.location);
  if (url.searchParams.has("done")) {
    try { sessionStorage.removeItem(storageKey); } catch { /* ignore */ }
    url.searchParams.delete("done");
    history.replaceState(null, "", url);
  }

  const selection = load();
  const boxes = [...table.querySelectorAll("[data-item]")];
  const pageBox = table.querySelector("[data-select-page]");
  const counter = form.querySelector("[data-selected-count]");
  const importSelected = form.querySelector("[data-import-selected]");

  function refresh() {
    boxes.forEach((box) => { box.checked = selection.has(box.dataset.item); });
    const onPage = boxes.filter((b) => b.checked).length;
    pageBox.checked = boxes.length > 0 && onPage === boxes.length;
    pageBox.indeterminate = onPage > 0 && onPage < boxes.length;
    counter.textContent = selection.size.toLocaleString("nl-BE");
    importSelected.disabled = selection.size === 0;
  }

  boxes.forEach((box) => box.addEventListener("change", () => {
    box.checked ? selection.add(box.dataset.item) : selection.delete(box.dataset.item);
    save(); refresh();
  }));

  pageBox.addEventListener("change", () => {
    boxes.forEach((box) => pageBox.checked ? selection.add(box.dataset.item) : selection.delete(box.dataset.item));
    save(); refresh();
  });

  form.querySelector("[data-clear-selection]").addEventListener("click", () => {
    selection.clear(); save(); refresh();
  });

  // Clicking anywhere on a row toggles its checkbox (except on links).
  table.addEventListener("click", (event) => {
    const row = event.target.closest("tbody tr");
    if (!row || event.target.closest("a, input")) return;
    const box = row.querySelector("[data-item]");
    if (box) { box.checked = !box.checked; box.dispatchEvent(new Event("change")); }
  });

  form.addEventListener("submit", (event) => {
    const scope = form.querySelector('[name="scope"]');
    if (event.submitter?.hasAttribute("data-import-filter")) {
      const total = Number(form.dataset.filterTotal);
      if (total > 500 && !confirm(`Alle ${total.toLocaleString("nl-BE")} artikelen van deze filter importeren?`)) {
        event.preventDefault();
        return;
      }
      scope.value = "filter";
    } else {
      scope.value = "selected";
      form.querySelector('[name="item_ids"]').value = [...selection].join(",");
    }
    form.querySelectorAll("button[type=submit]").forEach((b) => { b.disabled = true; });
  });

  refresh();
})();
