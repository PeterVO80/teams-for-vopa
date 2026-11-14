/**
 * Fetch extended descriptions from Rexel in batches.
 *
 * The server handles a few articles per request (POST data-url ->
 * {processed: [...], remaining: n}); this script keeps calling it until
 * nothing is pending, showing progress and a short log. Small batches avoid
 * request timeouts on big imports; the work can be paused and resumed (queued
 * articles simply stay queued, also after a restart).
 */
(function () {
  "use strict";

  const panel = document.querySelector("[data-details-runner]");
  if (!panel) return;

  const remaining = panel.querySelector("[data-remaining]");
  const bar = panel.querySelector("[data-bar]");
  const log = panel.querySelector("[data-log]");
  const startBtn = panel.querySelector("[data-start]");
  const stopBtn = panel.querySelector("[data-stop]");
  const total = Number(remaining.textContent) || 0;
  let running = false;

  function addLog(item) {
    const li = document.createElement("li");
    const icon = { done: "✅", not_found: "➖", error: "⚠️" }[item.status] || "";
    li.textContent = `${icon} ${item.article_number}: ${
      item.status === "done" ? item.description
        : item.status === "error" ? (item.error || "fout") : "geen uitgebreide gegevens bij Rexel"}`;
    log.prepend(li);
    while (log.children.length > 6) log.lastChild.remove();
  }

  function setRunning(state) {
    running = state;
    startBtn.classList.toggle("d-none", state);
    stopBtn.classList.toggle("d-none", !state);
  }

  async function run() {
    setRunning(true);
    while (running) {
      let data;
      try {
        data = await window.postJSON(panel.dataset.url, {});
      } catch (error) {
        addLog({ status: "error", article_number: "", error: "verbinding verbroken - klik opnieuw op Starten" });
        break;
      }
      data.processed.forEach(addLog);
      remaining.textContent = data.remaining;
      bar.style.width = `${total ? Math.round(((total - data.remaining) / total) * 100) : 100}%`;
      if (data.remaining === 0 || data.processed.length === 0) {
        startBtn.textContent = "Klaar";
        startBtn.disabled = true;
        break;
      }
    }
    setRunning(false);
  }

  startBtn.addEventListener("click", run);
  stopBtn.addEventListener("click", () => { running = false; });
  if (panel.dataset.autostart && total > 0) run();
})();
