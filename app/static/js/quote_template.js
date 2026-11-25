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
  // The text is edited in the WYSIWYG editor (static/js/editor.js) when it loaded.
  const getText = () => (field.mdEditor ? field.mdEditor.getMarkdown() : field.value);
  const setText = (value) => {
    if (field.mdEditor) {
      field.mdEditor.setMarkdown(value);
      field.mdEditor.focus();
    } else {
      field.value = value;
      field.focus();
    }
  };
  select.addEventListener("change", () => {
    const option = select.options[select.selectedIndex];
    const body = option.dataset.body;
    if (!body) return;
    const current = getText().trim();
    if (!current) {
      setText(body);
    } else if (current !== body.trim()) {
      const replace = window.confirm(
        "Er staat al een begeleidende tekst.\n\nOK = vervangen door het sjabloon\nAnnuleren = het sjabloon onderaan toevoegen");
      setText(replace ? body : `${current}\n\n${body}`);
    }
    select.value = "";
  });
})();
