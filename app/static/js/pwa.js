// Installable app: register the service worker (blueprints/pwa.py, served at /sw.js).
if ("serviceWorker" in navigator && window.isSecureContext) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => { /* the site keeps working without it */ });
  });
}
