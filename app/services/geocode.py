"""Address -> coordinates. OpenStreetMap Nominatim first (free, no key), Google Geocoding as fallback.

Nominatim's policy needs a real contact in the User-Agent and at most 1 request per second:
https://operations.osmfoundation.org/policies/nominatim/
"""

import logging
import threading
import time
from dataclasses import dataclass
from datetime import timedelta

import httpx
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import redact
from app.providers.base import ProviderError
from app.services import quota
from app.services.provider_cache import get_cached, store_response

log = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
GOOGLE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
CACHE_AGE = timedelta(days=30)

_nominatim_lock = threading.Lock()
_nominatim_last = 0.0


@dataclass
class GeocodeResult:
    latitude: float
    longitude: float
    source: str  # nominatim | google_geocoding
    formatted: str | None = None


def nominatim_configured() -> bool:
    ua = get_settings().nominatim_user_agent
    return "@" in ua and "example.com" not in ua


def _nominatim(db: Session, address: str, country: str | None, transport) -> GeocodeResult | None:
    global _nominatim_last
    params = {"q": address, "format": "jsonv2", "limit": 1}
    if country:
        params["countrycodes"] = country.lower()
    cached = get_cached(db, "nominatim", "search", params, CACHE_AGE)
    if cached is not None:
        data = cached.response
    else:
        with _nominatim_lock:  # max 1 request per second, per process
            wait = 1.0 - (time.monotonic() - _nominatim_last)
            if wait > 0:
                time.sleep(wait)
            with httpx.Client(timeout=15, transport=transport) as client:
                r = client.get(
                    NOMINATIM_URL, params=params, headers={"User-Agent": get_settings().nominatim_user_agent}
                )
            _nominatim_last = time.monotonic()
        if r.status_code >= 400:
            raise ProviderError("nominatim", r.text[:200], r.status_code)
        data = r.json()
        store_response(db, "nominatim", "search", params, data, r.status_code, ttl=CACHE_AGE)
    if not data:
        return None
    top = data[0]
    return GeocodeResult(float(top["lat"]), float(top["lon"]), "nominatim", top.get("display_name"))


def _google(db: Session, address: str, country: str | None, transport) -> GeocodeResult | None:
    key = get_settings().google_api_key
    if not key or not key.get_secret_value():
        return None
    params = {"address": address, **({"region": country.lower()} if country else {})}
    cached = get_cached(db, "google_geocoding", "geocode", params, CACHE_AGE)
    if cached is not None:
        data = cached.response
    else:
        quota.consume(db, "google_geocoding")
        with httpx.Client(timeout=15, transport=transport) as client:
            r = client.get(GOOGLE_URL, params={**params, "key": key.get_secret_value()})
        data = r.json()
        if data.get("status") not in ("OK", "ZERO_RESULTS"):
            if data.get("status") == "REQUEST_DENIED":
                quota.release(db, "google_geocoding")  # not billed
            raise ProviderError("google_geocoding", f"{data.get('status')}: {data.get('error_message', '')}")
        store_response(
            db, "google_geocoding", "geocode", params, data, r.status_code,
            ttl=timedelta(days=get_settings().google_data_ttl_days),
        )  # fmt: skip
    results = data.get("results") or []
    if not results:
        return None
    loc = results[0]["geometry"]["location"]
    return GeocodeResult(loc["lat"], loc["lng"], "google_geocoding", results[0].get("formatted_address"))


def geocode(
    db: Session, address: str, country: str | None, transport: httpx.BaseTransport | None = None
) -> tuple[GeocodeResult | None, list[str]]:
    """Returns (result or None, notes explaining what was tried)."""
    notes: list[str] = []
    providers = ([("nominatim", _nominatim)] if nominatim_configured() else []) + [("google", _google)]
    if not nominatim_configured():
        notes.append("Nominatim skipped: set NOMINATIM_USER_AGENT with your contact email to use it")
    for name, fn in providers:
        try:
            result = fn(db, address, country, transport)
        except (ProviderError, httpx.HTTPError, quota.QuotaExceeded, KeyError, ValueError) as exc:
            db.rollback()
            notes.append(f"{name}: {redact(str(exc))}")
            continue
        if result:
            return result, notes
        notes.append(f"{name}: no result")
    return None, notes
