// Quote form: choosing a template text copies it into the accompanying text
// (introduction), where it can still be changed. When there is already text,
// the user chooses: replace it, or add the template below it.
(function () {
  "use strict";
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
