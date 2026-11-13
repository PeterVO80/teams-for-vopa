/**
 * Stock movement popup: show only the fields that apply to the chosen kind.
 *
 * Elements with data-show-for="issue receipt" are only visible for those
 * kinds (e.g. "Naar locatie" only for a transfer). The label of the quantity
 * field changes for an inventory count ("Geteld aantal").
 */
(function () {
  "use strict";

  function init(root) {
    const form = root.querySelector("[data-stock-form]");
    if (!form) return;
    const quantityLabel = form.querySelector('label[for="quantity"]');

    function update() {
      const kind = form.querySelector('[name="kind"]:checked')?.value;
      form.querySelectorAll("[data-show-for]").forEach((el) => {
        el.classList.toggle("d-none", !el.dataset.showFor.split(" ").includes(kind));
      });
      if (quantityLabel) quantityLabel.textContent = kind === "count" ? "Geteld aantal" : "Aantal";
    }

    form.querySelectorAll('[name="kind"]').forEach((radio) => radio.addEventListener("change", update));
    update();
  }

  init(document);
  document.addEventListener("modal:loaded", (event) => init(event.target));
})();
