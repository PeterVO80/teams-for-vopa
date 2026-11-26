"""Installable web app: manifest, service worker, offline page (blueprints/pwa.py)."""

import json
from pathlib import Path


def test_manifest_is_public_and_complete(app):
    anonymous = app.test_client()
    response = anonymous.get("/manifest.webmanifest")
    assert response.status_code == 200 and response.mimetype == "application/manifest+json"
    data = json.loads(response.data)
    assert data["name"] == "VOPA Teams" and data["display"] == "standalone" and data["start_url"] == "/"
    assert data["scope"] == "/" and data["theme_color"] == "#212529"
    sizes = {(icon["sizes"], icon["purpose"]) for icon in data["icons"]}
    assert {("192x192", "any"), ("512x512", "any"), ("192x192", "maskable"), ("512x512", "maskable")} <= sizes
    for icon in data["icons"]:                                         # every icon really exists
        assert anonymous.get(icon["src"]).status_code == 200
    assert [s["name"] for s in data["shortcuts"]] == ["Uren registreren", "Punchlijst", "Offertes"]


def test_service_worker_and_offline_page(app):
    anonymous = app.test_client()
    response = anonymous.get("/sw.js")
    assert response.status_code == 200 and response.mimetype == "application/javascript"
    script = response.get_data(as_text=True)
    assert 'const OFFLINE_URL = "/offline"' in script and "/static/css/app.css" in script
    assert 'request.method !== "GET"' in script                          # forms are never cached / replayed
    assert response.headers["Cache-Control"] == "no-cache"
    html = anonymous.get("/offline").get_data(as_text=True)
    assert "Geen verbinding" in html and "Opnieuw proberen" in html
    # The cache name changes when the offline page or its files change (new version of the app).
    assert "vopa-teams-" in script


def test_pages_link_the_manifest_and_icons(app, client):
    for html in (app.test_client().get("/auth/login").get_data(as_text=True), client.get("/").get_data(as_text=True)):
        assert 'rel="manifest" href="/manifest.webmanifest"' in html
        assert "apple-touch-icon.png" in html and 'name="theme-color"' in html and "js/pwa.js" in html
    icons = Path(app.static_folder, "icons")
    for name in ("icon-192.png", "icon-512.png", "icon-maskable-192.png", "icon-maskable-512.png",
                 "apple-touch-icon.png", "favicon-32.png"):
        assert (icons / name).exists(), name
