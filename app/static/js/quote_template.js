// Quote form: the contact person select follows the customer; choosing a template text copies it into the accompanying text
// (introduction), where it can still be changed. When there is already text,
// the user chooses: replace it, or add the template below it.
(function () {
  "use strict";

  // Contact person: only the contact persons of the chosen customer.
  const contactSelect = document.querySelector("[data-contact-select]");
  if (contactSelect) {
    const customer = contactSelect.form.elements.customer_id;
    const all = [...contactSelect.options].slice(1);        // without "- geen contactpersoon -"
    const filter = () => {
      const current = contactSelect.value;
      all.forEach((option) => option.remove());
      const mine = all.filter((option) => option.dataset.company === customer.value);
      mine.forEach((option) => contactSelect.add(option));
      contactSelect.value = mine.some((option) => option.value === current) ? current : "0";
      contactSelect.disabled = !mine.length;
      contactSelect.options[0].textContent = !customer.value || customer.value === "0"
        ? "- kies eerst een klant -" : mine.length ? "- geen contactpersoon -" : "- klant heeft nog geen contactpersonen -";
    };
    customer.addEventListener("change", filter);
    filter();
  }

  const select = document.querySelector("[data-text-template]");
  if (!select) return;
  const field = select.form.elements.introduction;
  select.addEventListener("change", () => {
    const option = select.options[select.selectedIndex];
    const body = option.dataset.body;
    if (!body) return;
    const current = field.value.trim();
    if (!current) {
      field.value = body;
    } else if (current !== body.trim()) {
      const replace = window.confirm(
        "Er staat al een begeleidende tekst.\n\nOK = vervangen door het sjabloon\nAnnuleren = het sjabloon onderaan toevoegen");
      field.value = replace ? body : `${field.value.replace(/\s+$/, "")}\n\n${body}`;
    }
    select.value = "";
    field.focus();
  });
})();
