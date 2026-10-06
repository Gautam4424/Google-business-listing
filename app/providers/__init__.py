from app.core.config import get_settings
from app.providers.google_places import GooglePlacesClient
from app.providers.serpapi import SerpApiClient


def get_places_client() -> GooglePlacesClient | None:
    s = get_settings()
    key = s.google_api_key.get_secret_value() if s.google_api_key else ""
    return GooglePlacesClient(key, timeout=s.http_timeout_seconds) if key else None


def get_serpapi_client() -> SerpApiClient | None:
    s = get_settings()
    key = s.serpapi_key.get_secret_value() if s.serpapi_key else ""
    return SerpApiClient(key, timeout=s.http_timeout_seconds) if key else None
