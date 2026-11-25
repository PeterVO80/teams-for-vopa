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
})();
