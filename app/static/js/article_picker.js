/**
 * Link an order line to the article database.
 *
 * Markup: templates/purchasing/_article_picker.html, a [data-article-picker]
 * block inside a form that also has the fields article_id, description,
 * quantity, unit and unit_price (optionally with a WTForms prefix such as
 * "edit-description", so the "add line" form and the edit popup can be on
 * the same page).
 *
 * Typing in the search box queries /articles/search.json. Choosing a result
 * stores the article id in the hidden `article_id` field and fills
 * description, unit and price. The block shows whether the line is linked;
 * "ontkoppelen" turns it back into a free-text line.
 */
(function () {
  "use strict";

  function initPicker(picker) {
    if (picker.dataset.ready) return;  // never initialise twice
    picker.dataset.ready = "1";

    const form = picker.closest("form");
    // Field by name, with or without a WTForms prefix ("description" / "edit-description").
    const field = (name) => form.querySelector(`[name="${name}"], [name$="-${name}"]`);
    const fields = {
      id: field("article_id"),
      description: field("description"),
      quantity: field("quantity"),
      unit: field("unit"),
      // Quotes keep the article price as purchase price (the sales price follows from the markup).
      price: field("cost_price") || field("unit_price"),
    };
    const search = picker.querySelector("[data-article-search]");
    const results = picker.querySelector("[data-article-results]");
    const linked = picker.querySelector("[data-article-linked]");
    const unlinked = picker.querySelector("[data-article-unlinked]");
    let debounceTimer = null;

    function showLinkState(label) {
      const isLinked = Boolean(fields.id.value);
      linked.classList.toggle("d-none", !isLinked);
      unlinked.classList.toggle("d-none", isLinked);
      if (label !== undefined) picker.querySelector("[data-article-label]").textContent = label;
    }

    function hideResults() {
      results.classList.add("d-none");
      results.innerHTML = "";
    }

    function choose(article) {
      fields.id.value = article.id;
      fields.description.value = article.description;
      fields.unit.value = article.unit;
      fields.price.value = article.unit_price ?? "";
      fields.price.dispatchEvent(new Event("input", { bubbles: true }));   // quote line: recalculate the sales price
      search.value = "";
      hideResults();
      showLinkState(article.label);
      fields.quantity.focus();
    }

    async function lookup() {
      const term = search.value.trim();
      if (term.length < 2) return hideResults();
      const response = await fetch(`${picker.dataset.url}?q=${encodeURIComponent(term)}`);
      const articles = await response.json();
      results.innerHTML = "";
      articles.forEach((article) => {
        const item = document.createElement("button");
        item.type = "button";
        item.className = "list-group-item list-group-item-action small";
        item.textContent = article.label + (article.supplier ? `  ·  ${article.supplier}` : "");
        item.addEventListener("click", () => choose(article));
        results.appendChild(item);
      });
      if (!articles.length) {
        results.innerHTML = '<div class="list-group-item small text-body-secondary">Geen artikels gevonden.</div>';
      }
      results.classList.remove("d-none");
    }

    search.addEventListener("input", () => {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(lookup, 250);
    });
    // Enter in the search box picks the first result instead of submitting the form.
    search.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        results.querySelector("button")?.click();
      }
    });
    document.addEventListener("click", (event) => {
      if (!picker.contains(event.target)) hideResults();
    });
    picker.querySelector("[data-article-unlink]").addEventListener("click", () => {
      fields.id.value = "";
      showLinkState();
    });
    showLinkState();
  }

  const initAll = (root) => root.querySelectorAll("[data-article-picker]").forEach(initPicker);
  initAll(document);                                                        // "add line" form
  document.addEventListener("modal:loaded", (event) => initAll(event.target));  // edit popup
})();
