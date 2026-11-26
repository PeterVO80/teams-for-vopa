// Service worker of VOPA Teams (blueprints/pwa.py), version {{ version }}.
// No business data is stored on the device: pages always come from the
// server. Only the offline page and the files it needs are cached; the
// offline page is shown when the server can't be reached.
"use strict";
const CACHE = "vopa-teams-{{ version }}";
const OFFLINE_URL = {{ offline_url|tojson }};
const ASSETS = {{ assets|tojson }};

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll([OFFLINE_URL, ...ASSETS])).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  // Remove the caches of previous versions.
  event.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;                       // forms etc.: straight to the server
  if (request.mode === "navigate") {
    // Pages: always the server; the offline page only when it can't be reached.
    event.respondWith(fetch(request).catch(() => caches.match(OFFLINE_URL)));
    return;
  }
  const url = new URL(request.url);
  if (url.origin === self.location.origin && ASSETS.includes(url.pathname)) {
    // Files of the offline page: from the server, from the cache when offline.
    // (ignoreSearch: the icon font is requested with a ?version suffix)
    event.respondWith(fetch(request).catch(() => caches.match(request, { ignoreSearch: true })));
  }
});
