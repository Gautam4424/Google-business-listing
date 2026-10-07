import time
from functools import lru_cache

from pydantic import SecretStr, TypeAdapter
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "development"
    # false: INFO logs and short error messages; true: DEBUG logs (never shows API keys)
    debug: bool = False
    database_url: str = "sqlite:///./local.db"
    redis_url: str = "redis://localhost:6379/0"

    google_api_key: SecretStr | None = None
    serpapi_key: SecretStr | None = None

    # Free-tier guard. 0 = no cap for that window.
    quota_places_details_monthly: int = 900
    quota_places_details_daily: int = 100
    quota_places_textsearch_monthly: int = 4500
    quota_places_textsearch_daily: int = 150
    # Text Search that also returns phone/website is billed as the Enterprise SKU (~1,000 free/month).
    quota_places_textsearch_enterprise_monthly: int = 900
    quota_places_textsearch_enterprise_daily: int = 30
    quota_geocoding_monthly: int = 9000
    quota_geocoding_daily: int = 0
    quota_serpapi_monthly: int = 240
    quota_serpapi_daily: int = 40  # one busy day cannot use up the month (a full 10-keyword check = 20)
    # Top 10 reviews via SerpApi costs 2 credits per audit; off by default (Google's 5 reviews are free).
    serpapi_reviews_enabled: bool = False
    # Max active keywords per project (each keyword = 2 SerpApi searches per ranking run).
    keyword_cap: int = 10
    # full = Local Pack + Local Finder (2 SerpApi searches per keyword);
    # maps_only = 1 per keyword, Local Pack estimated from the Maps top 3
    ranking_mode: str = "full"
    ranking_cache_hours: int = 24  # re-checking a keyword within this window reuses the saved result (free)
    maps_zoom: int = 14  # Local Finder search area around the keyword's coordinates
    # Phase 8: competitors come from the stored ranking results (0 SerpApi searches).
    competitor_max: int = 10  # most visible competitors analysed per project
    # 1 Google Place Details per competitor (cached) for its review sample; off = search-result data only
    competitor_details: bool = True
    competitor_details_cache_days: int = 7
    # competitor lookups stop when only this many Place Details are left (kept for client audits)
    competitor_details_reserve: int = 10

    google_data_ttl_days: int = 30  # Google content older than this is removed by the nightly clean-up
    cleanup_hour_utc: int = 3  # when the nightly clean-up runs
    # A temporary provider error (timeout, 429, 5xx) is retried this many times after a short wait.
    provider_retries: int = 1
    provider_retry_delay_seconds: float = 2.0
    # A queued/running job older than this is treated as interrupted (worker restart, lost queue).
    job_stale_minutes: int = 120
    http_timeout_seconds: float = 20.0
    serpapi_timeout_seconds: float = 120.0  # Google searches through SerpApi can take 30-60 s
    nominatim_user_agent: str = "local-seo-audit/0.1"
    # Re-render JavaScript-only websites with headless Chromium (needs the browser in the image).
    browser_fallback: bool = True


@lru_cache
def base_settings() -> Settings:
    """The one Settings object, loaded from the environment / .env (see get_settings for overrides)."""
    return Settings()


# Values changed on the Settings page are stored in the `app_settings` table and override .env.
# Every process (api, worker) re-reads them at most every OVERRIDE_REFRESH_SECONDS, so a change
# applies within seconds without a restart. Removing an override restores the .env value.
OVERRIDE_REFRESH_SECONDS = 5.0
_last_refresh = float("-inf")
_env_values: dict[str, object] = {}  # .env value of every attribute currently overridden


def get_settings() -> Settings:
    if time.monotonic() - _last_refresh >= OVERRIDE_REFRESH_SECONDS:
        refresh_overrides()
    return base_settings()


def refresh_overrides() -> None:
    """Apply the app_settings table to the Settings object (never raises: no table yet = no overrides)."""
    global _last_refresh
    _last_refresh = time.monotonic()
    try:
        from sqlalchemy import text

        from app.core.db import engine

        with engine.connect() as conn:
            rows = dict(conn.execute(text("SELECT key, value FROM app_settings")).all())
    except Exception:
        return  # database not ready / table not created yet / busy: keep the current values
    apply_overrides(rows)


def apply_overrides(rows: dict[str, str]) -> None:
    s = base_settings()
    wanted = {}
    for key, raw in rows.items():
        attr = key.lower()
        field = Settings.model_fields.get(attr)
        if field is None:
            continue
        try:
            wanted[attr] = TypeAdapter(field.annotation).validate_python(raw)
        except Exception:
            continue  # an invalid stored value never breaks the app; the .env value stays
    for attr in list(_env_values):
        if attr not in wanted:  # override removed: back to the .env value
            setattr(s, attr, _env_values.pop(attr))
    for attr, value in wanted.items():
        _env_values.setdefault(attr, getattr(s, attr))
        setattr(s, attr, value)


def env_value(attr: str) -> object:
    """The .env (or default) value of a setting, even while it is overridden."""
    return _env_values.get(attr, getattr(base_settings(), attr))


def reset_overrides() -> None:
    """Drop every override in this process (tests)."""
    global _last_refresh
    apply_overrides({})
    _last_refresh = float("-inf")
