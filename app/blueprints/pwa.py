"""Installable web app (Progressive Web App).

Browsers offer to install the application ("App installeren", "Zet op
beginscherm") when the site has

* a web app manifest (``/manifest.webmanifest``): name, icons, colours,
  start page, shortcuts;
* a service worker (``/sw.js``, served from the root so it covers the whole
  site) and HTTPS.

The service worker deliberately keeps no business data on the device: pages
always come from the server. It only stores the offline page and the few files
that page needs, and shows it when there is no connection.

These URLs are public (no login): the browser fetches them on the login page.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from flask import Blueprint, current_app, jsonify, render_template, url_for

bp = Blueprint("pwa", __name__)

#: Dark colour of the top bar; also the colour of the app's title bar / splash screen.
THEME_COLOR = "#212529"

#: Static files the offline page needs (kept in the browser's cache).
OFFLINE_ASSETS = [
    "vendor/bootstrap/bootstrap.min.css",
    "vendor/bootstrap-icons/bootstrap-icons.min.css",
    "vendor/bootstrap-icons/fonts/bootstrap-icons.woff2",
    "css/app.css",
    "img/vopa-logo.svg",
    "icons/icon-192.png",
]


@bp.route("/manifest.webmanifest")
def manifest():
    icon = lambda name: url_for("static", filename=f"icons/{name}")  # noqa: E731
    data = {
        "id": "/",
        "name": "VOPA Teams",
        "short_name": "VOPA Teams",
        "description": "Projecten, planning, uren, offertes en bestellingen van VOPA.",
        "lang": "nl",
        "start_url": url_for("main.dashboard"),
        "scope": "/",
        "display": "standalone",
        "background_color": THEME_COLOR,
        "theme_color": THEME_COLOR,
        "icons": [
            {"src": icon("icon-192.png"), "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": icon("icon-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": icon("icon-maskable-192.png"), "sizes": "192x192", "type": "image/png", "purpose": "maskable"},
            {"src": icon("icon-maskable-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
        # Long-press on the app icon (Android, Windows).
        "shortcuts": [
            {"name": "Uren registreren", "url": url_for("timesheet.edit"),
             "icons": [{"src": icon("icon-192.png"), "sizes": "192x192"}]},
            {"name": "Punchlijst", "url": url_for("tasks.board"),
             "icons": [{"src": icon("icon-192.png"), "sizes": "192x192"}]},
            {"name": "Offertes", "url": url_for("quotes.index"),
             "icons": [{"src": icon("icon-192.png"), "sizes": "192x192"}]},
        ],
    }
    response = jsonify(data)
    response.mimetype = "application/manifest+json"
    response.headers["Cache-Control"] = "no-cache"
    return response


def _cache_version(assets: list[str]) -> str:
    """Changes when the offline page or one of its files changes -> new service worker."""
    digest = hashlib.sha256(Path(current_app.root_path, "templates/pwa/offline.html").read_bytes())
    for asset in assets:
        path = Path(current_app.static_folder, asset)
        if path.exists():
            digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


@bp.route("/sw.js")
def service_worker():
    assets = [url_for("static", filename=a) for a in OFFLINE_ASSETS]
    script = render_template("pwa/sw.js", version=_cache_version(OFFLINE_ASSETS),
                             offline_url=url_for("pwa.offline"), assets=assets)
    response = current_app.response_class(script, mimetype="application/javascript")
    response.headers["Cache-Control"] = "no-cache"         # browsers check for a new version on every visit
    return response


@bp.route("/offline")
def offline():
    """Shown by the service worker when the server can't be reached."""
    return render_template("pwa/offline.html", theme_color=THEME_COLOR)
