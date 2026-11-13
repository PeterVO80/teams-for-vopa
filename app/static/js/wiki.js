/**
 * Wiki page form: the "parent page" list only shows pages of the wiki that
 * is selected in the "Wiki" field (general wiki or a project wiki).
 *
 * Every <option> of #parent_id carries data-project="<project id>" (0 = the
 * general wiki, "any" = the top level), rendered by blueprints/wiki.py.
 * The server validates the combination again, this only guides the user.
 */
(function () {
  "use strict";

  const wiki = document.getElementById("project_id");
  const parent = document.getElementById("parent_id");
  if (!wiki || !parent) return;

  function filterParents() {
    for (const option of parent.options) {
      const visible = option.dataset.project === "any" || option.dataset.project === wiki.value;
      option.hidden = !visible;
      option.disabled = !visible;  // hidden alone is ignored by some browsers
    }
    if (parent.selectedOptions[0]?.disabled) parent.value = "0";
  }

  wiki.addEventListener("change", filterParents);
  filterParents();
})();
