from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "development"
    database_url: str = "sqlite:///./local.db"
    redis_url: str = "redis://localhost:6379/0"

    google_api_key: SecretStr | None = None
    serpapi_key: SecretStr | None = None

    # Free-tier guard. 0 = no cap for that window.
    quota_places_details_monthly: int = 900
    quota_places_details_daily: int = 30
    quota_places_textsearch_monthly: int = 4500
    quota_places_textsearch_daily: int = 150
    # Text Search that also returns phone/website is billed as the Enterprise SKU (~1,000 free/month).
    quota_places_textsearch_enterprise_monthly: int = 900
    quota_places_textsearch_enterprise_daily: int = 30
    quota_geocoding_monthly: int = 9000
    quota_geocoding_daily: int = 0
    quota_serpapi_monthly: int = 240
    quota_serpapi_daily: int = 0
    # Top 10 reviews via SerpApi costs 2 credits per audit; off by default (Google's 5 reviews are free).
    serpapi_reviews_enabled: bool = False
    # Max active keywords per project (each keyword = 2 SerpApi searches per ranking run).
    keyword_cap: int = 10
    # full = Local Pack + Local Finder (2 SerpApi searches per keyword);
    # maps_only = 1 per keyword, Local Pack estimated from the Maps top 3
    ranking_mode: str = "full"
    ranking_cache_hours: int = 24  # re-checking a keyword within this window reuses the saved result (free)
    maps_zoom: int = 14  # Local Finder search area around the keyword's coordinates

    google_data_ttl_days: int = 30
    http_timeout_seconds: float = 20.0
    serpapi_timeout_seconds: float = 120.0  # Google searches through SerpApi can take 30-60 s
    nominatim_user_agent: str = "local-seo-audit/0.1"
    # Re-render JavaScript-only websites with headless Chromium (needs the browser in the image).
    browser_fallback: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
