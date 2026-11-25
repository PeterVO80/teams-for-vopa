// Quote line form (templates/quotes/_line_fields.html): sales price =
// purchase price + markup. Changing the purchase price or the markup
// recalculates the sales price; typing a sales price recalculates the markup.
(function () {
  "use strict";
  const num = (input) => {
    const value = parseFloat((input.value || "").replace(/\s/g, "").replace(",", "."));
    return Number.isFinite(value) ? value : null;
  };
  const show = (value, decimals) => String(Math.round(value * 10 ** decimals) / 10 ** decimals).replace(".", ",");

  document.addEventListener("input", (event) => {
    const form = event.target.form;
    if (!form || !form.querySelector("[data-quote-line]")) return;
    const cost = form.elements.cost_price, markup = form.elements.markup, price = form.elements.unit_price;
    if (!cost || !markup || !price) return;                // labour / section: nothing to calculate
    const c = num(cost), m = num(markup), p = num(price);
    if (event.target === cost || event.target === markup) {
      if (c !== null && m !== null) price.value = show(c * (1 + m / 100), 4);
    } else if (event.target === price && c) {
      if (p !== null) markup.value = show((p / c - 1) * 100, 1);
    }
  });

  // Labour: choosing a kind of service fills the description and the hourly
  // rate - unless the user typed something else than the previous service's.
  document.addEventListener("change", (event) => {
    const select = event.target.closest("[data-service-select]");
    if (!select) return;
    const form = select.form;
    const option = select.options[select.selectedIndex];
    const previous = select.dataset.previous ? JSON.parse(select.dataset.previous) : { name: "", rate: "" };
    const description = form.elements.description, price = form.elements.unit_price;
    if (!description.value.trim() || description.value === previous.name) description.value = option.dataset.name;
    const rate = option.dataset.rate ? show(parseFloat(option.dataset.rate), 2) : "";
    const previousRate = previous.rate ? parseFloat(previous.rate.replace(",", ".")) : null;
    if (rate && (!price.value.trim() || num(price) === previousRate)) price.value = rate;
    select.dataset.previous = JSON.stringify({ name: option.dataset.name, rate });
  });
  // Remember what the current service filled in (edit popup).
  const remember = (root) => root.querySelectorAll("[data-service-select]").forEach((select) => {
    const option = select.options[select.selectedIndex];
    select.dataset.previous = JSON.stringify({ name: option.dataset.name || "", rate: option.dataset.rate ? show(parseFloat(option.dataset.rate), 2) : "" });
  });
  remember(document);
  document.addEventListener("modal:loaded", (event) => remember(event.target));
})();