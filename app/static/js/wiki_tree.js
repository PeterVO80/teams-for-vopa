/**
 * Wiki tree: "expand all" / "collapse all" buttons for every [data-wiki-tree].
 * The branches themselves are <details> elements and work without this script.
 */
(function () {
  "use strict";
  document.querySelectorAll("[data-wiki-tree]").forEach((card) => {
    const setAll = (open) => card.querySelectorAll("details").forEach((d) => { d.open = open; });
    card.querySelector("[data-tree-expand]")?.addEventListener("click", () => setAll(true));
    card.querySelector("[data-tree-collapse]")?.addEventListener("click", () => setAll(false));
    // Make sure the current page is visible in a long tree.
    card.querySelector(".wiki-node.current")?.scrollIntoView({ block: "nearest" });
  });
})();
