"""Road distance between our office and a customer (for field-service kilometres).

The office address is set by an administrator on the *Instellingen* page
(``AppSetting`` keys ``office.*``). The distance is calculated by road, one
way, in km:

* with ``GOOGLE_MAPS_API_KEY`` set: Google Distance Matrix API (needs a Google
  Cloud project with that API enabled; paid beyond the free monthly credit);
* otherwise OpenStreetMap, free: the addresses are looked up with Nominatim
  and the route is calculated with the public OSRM server. Both are free
  services with fair-use limits - fine for an occasional calculation when a
  relation is saved.

The result is only a suggestion: it is stored on the relation and can be
changed by hand (``Company.distance_auto`` tells whether it was calculated).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from decimal import ROUND_HALF_UP, Decimal

from flask import current_app

from ..models import AppSetting

#: Generic client name (no personal data) - Nominatim asks for an identifying User-Agent.
USER_AGENT = "VOPA-Teams/1.0 (project management app; distance to customers)"
OFFICE_FIELDS = ("street", "postal_code", "city", "country")


class DistanceError(Exception):
    """The distance could not be calculated (message shown to the user)."""


def company_name() -> str:
    return AppSetting.get("company.name") or "VOPA"


def office_address() -> dict[str, str]:
    return {f: AppSetting.get(f"office.{f}") or "" for f in OFFICE_FIELDS}


def address_text(street: str | None, postal_code: str | None, city: str | None, country: str | None) -> str:
    """One-line address for the route services, e.g. "Kerkstraat 1, 9000 Gent, België"."""
    place = " ".join(p.strip() for p in (postal_code or "", city or "") if p and p.strip())
    return ", ".join(p for p in ((street or "").strip(), place, (country or "").strip()) if p)


def office_text() -> str:
    """Office address for the route services (without box number: same building)."""
    return address_text(**office_address())


def office_display_text() -> str:
    """Office address as printed (timesheet header), with the box number: "Kerkstraat 1 bus 3, ..."."""
    address = office_address()
    box = (AppSetting.get("office.box") or "").strip()
    if box and address["street"]:
        address["street"] = f"{address['street'].strip()} bus {box}"
    return address_text(**address)


def _get_json(url: str) -> dict | list:
    """GET a JSON document (separate function so tests can replace it)."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise DistanceError("De routeplanner is niet bereikbaar. Probeer later opnieuw of vul de afstand zelf in.") from exc


def _km(meters: float) -> Decimal:
    return (Decimal(str(meters)) / 1000).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def _google(origin: str, destination: str, key: str) -> Decimal:
    query = urllib.parse.urlencode({"origins": origin, "destinations": destination, "units": "metric",
                                    "mode": "driving", "key": key})
    data = _get_json(f"https://maps.googleapis.com/maps/api/distancematrix/json?{query}")
    try:
        element = data["rows"][0]["elements"][0]
        if element.get("status") != "OK":
            raise KeyError(element.get("status"))
        return _km(element["distance"]["value"])
    except (KeyError, IndexError, TypeError) as exc:
        raise DistanceError(f"Google Maps vond geen route ({data.get('status', '')} {exc}).".strip()) from exc


def _geocode(address: str) -> tuple[float, float]:
    query = urllib.parse.urlencode({"q": address, "format": "json", "limit": 1})
    results = _get_json(f"https://nominatim.openstreetmap.org/search?{query}")
    if not results:
        raise DistanceError(f"Adres niet gevonden op de kaart: {address}")
    return float(results[0]["lon"]), float(results[0]["lat"])


def _osm(origin: str, destination: str) -> Decimal:
    (lon1, lat1), (lon2, lat2) = _geocode(origin), _geocode(destination)
    data = _get_json(f"https://router.project-osrm.org/route/v1/driving/{lon1},{lat1};{lon2},{lat2}?overview=false")
    try:
        return _km(data["routes"][0]["distance"])
    except (KeyError, IndexError, TypeError) as exc:
        raise DistanceError("Geen route gevonden tussen het kantoor en dit adres.") from exc


def road_distance_km(destination: str) -> Decimal:
    """Distance by road from the office to ``destination``, one way, in km."""
    origin = office_text()
    if not origin:
        raise DistanceError("Het adres van het kantoor is nog niet ingesteld (Beheer > Instellingen).")
    if not destination:
        raise DistanceError("Vul eerst het adres van de relatie in.")
    key = current_app.config.get("GOOGLE_MAPS_API_KEY")
    return _google(origin, destination, key) if key else _osm(origin, destination)


def provider_name() -> str:
    return "Google Maps" if current_app.config.get("GOOGLE_MAPS_API_KEY") else "OpenStreetMap (gratis)"
