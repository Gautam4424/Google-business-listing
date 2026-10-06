"""Turn one pasted line ("Acme Ltd 1 High St, Manchester M1 1AA, UK") into project fields.

Primary: Google Places Text Search (gives place_id, phone, website, coordinates).
Fallback: a local parser, used when there is no key, the free-tier limit is reached, or Google finds nothing.
"""

import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import redact
from app.providers import get_places_client
from app.providers.base import ProviderError
from app.providers.google_places import PROVIDER as PLACES
from app.services import quota
from app.services.provider_cache import get_cached, store_response

log = logging.getLogger(__name__)

# Phone + website make this the Text Search Enterprise SKU (counted separately by the quota guard).
FIELD_MASK = ",".join(
    f"places.{f}"
    for f in (
        "id",
        "displayName",
        "formattedAddress",
        "addressComponents",
        "location",
        "googleMapsUri",
        "primaryTypeDisplayName",
        "nationalPhoneNumber",
        "internationalPhoneNumber",
        "websiteUri",
    )
)
SKU = "google_places_text_search_enterprise"
CACHE_MAX_AGE = timedelta(days=7)
MAX_CANDIDATES = 5

COUNTRY_CODES = {
    "canada": "CA",
    "united states": "US",
    "united states of america": "US",
    "usa": "US",
    "us": "US",
    "united kingdom": "GB",
    "uk": "GB",
    "england": "GB",
    "scotland": "GB",
    "wales": "GB",
    "northern ireland": "GB",
    "ireland": "IE",
    "india": "IN",
    "australia": "AU",
    "new zealand": "NZ",
    "germany": "DE",
    "france": "FR",
    "spain": "ES",
    "italy": "IT",
    "netherlands": "NL",
    "united arab emirates": "AE",
    "uae": "AE",
    "singapore": "SG",
    "south africa": "ZA",
}

# Text that comes along when copying from Google search results.
_NOISE = re.compile(r"\s+map of\s.*$", re.IGNORECASE)
_STREET_NUMBER = re.compile(r"^\d+[A-Za-z]?(-\d+[A-Za-z]?)?,?$")


@dataclass
class Candidate:
    business_name: str
    address: str | None = None
    phone: str | None = None
    website_url: str | None = None
    country: str | None = None
    city: str | None = None
    region: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    place_id: str | None = None
    maps_url: str | None = None
    primary_category: str | None = None

    @property
    def service_area(self) -> str | None:
        return ", ".join(p for p in (self.city, self.region) if p) or None


@dataclass
class LookupResult:
    query: str
    source: str  # google_places | parser
    candidates: list[Candidate] = field(default_factory=list)
    warning: str | None = None
    cached: bool = False

    def as_dict(self) -> dict:
        return {
            "query": self.query,
            "source": self.source,
            "cached": self.cached,
            "warning": self.warning,
            "candidates": [{**asdict(c), "service_area": c.service_area} for c in self.candidates],
        }


def clean_query(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    text = _NOISE.sub("", text)
    return text.strip(" ,;·-")


def clean_website(url: str | None) -> str | None:
    """Drop tracking parameters (utm_*, gclid…) that Google adds to GBP website links."""
    if not url:
        return None
    parts = urlsplit(url)
    query = [
        (k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith(("utm_", "gclid", "fbclid"))
    ]
    return urlunsplit(parts._replace(query=urlencode(query)))


def parse_free_text(text: str) -> Candidate:
    """Best-effort split without any API: name = words before the street number, the rest = address."""
    text = clean_query(text)
    words = text.split(" ")
    split_at = next((i for i, w in enumerate(words) if i > 0 and _STREET_NUMBER.match(w)), None)
    if split_at is None:
        name, address = (
            text.split(",", 1)[0].strip(),
            (text.split(",", 1)[1].strip() if "," in text else None),
        )
    else:
        name, address = " ".join(words[:split_at]).strip(" ,"), " ".join(words[split_at:])

    country = city = region = None
    segments = [s.strip() for s in (address or "").split(",") if s.strip()]
    if segments and segments[-1].lower() in COUNTRY_CODES:
        country = COUNTRY_CODES[segments[-1].lower()]
        segments = segments[:-1]
    if len(segments) >= 3:
        # "street, city, REGION POSTCODE"
        city = segments[-2]
        region = segments[-1].split(" ")[0] or None
    elif len(segments) == 2:
        city = segments[-1]
    return Candidate(business_name=name or text, address=address, country=country, city=city, region=region)


def _component(place: dict, kind: str, short: bool = False) -> str | None:
    for c in place.get("addressComponents", []):
        if kind in c.get("types", []):
            return c.get("shortText" if short else "longText")
    return None


def candidate_from_place(place: dict) -> Candidate:
    location = place.get("location") or {}
    return Candidate(
        business_name=(place.get("displayName") or {}).get("text") or "",
        address=place.get("formattedAddress"),
        phone=place.get("internationalPhoneNumber") or place.get("nationalPhoneNumber"),
        website_url=clean_website(place.get("websiteUri")),
        country=_component(place, "country", short=True),
        city=_component(place, "locality") or _component(place, "postal_town"),
        region=_component(place, "administrative_area_level_1", short=True),
        latitude=location.get("latitude"),
        longitude=location.get("longitude"),
        place_id=place.get("id"),
        maps_url=place.get("googleMapsUri"),
        primary_category=(place.get("primaryTypeDisplayName") or {}).get("text"),
    )


def _fallback(query: str, warning: str) -> LookupResult:
    return LookupResult(query=query, source="parser", candidates=[parse_free_text(query)], warning=warning)


def lookup_business(db: Session, text: str) -> LookupResult:
    query = clean_query(text)
    client = get_places_client()
    if client is None:
        return _fallback(
            query, "GOOGLE_API_KEY is not set, so the line was split locally. Please check the fields."
        )

    params = {"textQuery": query, "fieldMask": FIELD_MASK, "pageSize": MAX_CANDIDATES}
    cached = get_cached(db, PLACES, "places:searchText", params, CACHE_MAX_AGE)
    if cached is not None:
        data, from_cache = cached.response or {}, True
    else:
        try:
            quota.consume(db, SKU)
            data = client.search_text(query, field_mask=FIELD_MASK, page_size=MAX_CANDIDATES)
        except quota.QuotaExceeded as exc:
            return _fallback(query, f"Google lookup skipped: {exc}. The line was split locally instead.")
        except (ProviderError, httpx.HTTPError) as exc:
            log.warning("Places lookup failed: %s", redact(str(exc)))
            return _fallback(query, f"Google lookup failed ({redact(str(exc))}). The line was split locally.")
        store_response(
            db, PLACES, "places:searchText", params, data, 200,
            ttl=timedelta(days=get_settings().google_data_ttl_days),
        )  # fmt: skip
        from_cache = False

    candidates = [candidate_from_place(p) for p in data.get("places", [])]
    if not candidates:
        return _fallback(
            query, "Google found no matching business. The line was split locally; please check it."
        )
    return LookupResult(query=query, source="google_places", candidates=candidates, cached=from_cache)
