/**
 * "Bereken afstand" in the relation form: asks the server for the road
 * distance between the office and the address typed in the form
 * (blueprints/relations.py: calc_distance) and fills the distance field.
 */
(function () {
  "use strict";

  document.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-calc-distance]");
    if (!button) return;
    const box = button.closest("[data-distance-url]");
    const form = button.closest("form");
    const status = box.querySelector("[data-distance-status]");
    const params = new URLSearchParams();
    ["street", "postal_code", "city", "country"].forEach((name) => params.set(name, form.elements[name]?.value || ""));
    button.disabled = true;
    status.className = "small ms-2 text-body-secondary";
    status.textContent = "Bezig met berekenen...";
    try {
      const response = await fetch(`${box.dataset.distanceUrl}?${params}`, { headers: { Accept: "application/json" } });
      const data = await response.json();
      if (data.ok) {
        form.elements.distance_km.value = data.km.toFixed(1);
        status.className = "small ms-2 text-success";
        status.textContent = `${data.km.toFixed(1).replace(".", ",")} km enkel (${data.provider})`;
      } else {
        status.className = "small ms-2 text-danger";
        status.textContent = data.error;
      }
    } catch (error) {
      status.className = "small ms-2 text-danger";
      status.textContent = "Berekenen mislukt.";
    } finally {
      button.disabled = false;
    }
  });
})();
