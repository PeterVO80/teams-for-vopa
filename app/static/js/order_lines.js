/**
 * Article autocomplete on the purchase order page.
 *
 * Typing in #article-search queries /articles/search.json. Picking a result
 * fills description, unit and unit price and stores the article id in the
 * hidden `article_id` field, linking the order line to the article database.
 * Typing in the description field afterwards turns it back into a free-text
 * line (the link is removed).
 */
(function () {
  "use strict";

  const search = document.getElementById("article-search");
  if (!search) return;

  const results = document.getElementById("article-results");
  const fields = {
    id: document.getElementById("article_id"),
    description: document.getElementById("description"),
    unit: document.getElementById("unit"),
    price: document.getElementById("unit_price"),
    quantity: document.getElementById("quantity"),
  };
  let debounceTimer = null;

  function hideResults() {
    results.classList.add("d-none");
    results.innerHTML = "";
  }

  function choose(article) {
    fields.id.value = article.id;
    fields.description.value = article.description;
    fields.unit.value = article.unit;
    fields.price.value = article.unit_price ?? "";
    search.value = article.label;
    hideResults();
    fields.quantity.focus();
  }

  async function lookup() {
    const term = search.value.trim();
    if (term.length < 2) return hideResults();
    const response = await fetch(`${search.dataset.url}?q=${encodeURIComponent(term)}`);
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
    results.classList.toggle("d-none", articles.length === 0);
  }

  search.addEventListener("input", () => {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(lookup, 250);
  });
  document.addEventListener("click", (e) => {
    if (!results.contains(e.target) && e.target !== search) hideResults();
  });
  // Editing the description manually means it is no longer the database article.
  fields.description.addEventListener("input", () => { fields.id.value = ""; });
})();
