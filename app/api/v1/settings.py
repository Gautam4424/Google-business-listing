"""Read-only settings & status for the in-house Settings page. Keys are masked; change values in .env."""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.models import AuditJob
from app.services.quota import SKU_LABEL, SKU_LIMITS, next_daily_reset, next_monthly_reset, usage_report

router = APIRouter(prefix="/settings", tags=["settings"])


def mask(secret) -> str | None:
    """'AIza…Y4': enough to recognise which key is set, never enough to use it."""
    value = secret.get_secret_value() if secret else ""
    if not value:
        return None
    return f"{value[:4]}…{value[-2:]}" if len(value) > 10 else "set"


def _worker_online() -> bool | None:
    """True/False from a 1-second worker ping; None when the queue (Redis) itself is unreachable."""
    try:
        import redis

        redis.Redis.from_url(get_settings().redis_url, socket_connect_timeout=1, socket_timeout=1).ping()
    except Exception:
        return None
    try:
        from app.workers.celery_app import celery

        return bool(celery.control.ping(timeout=1.0))
    except Exception:
        return False


@router.get("")
def get_app_settings(db: Session = Depends(get_db)) -> dict:
    s = get_settings()
    usage = {u.sku: u for u in usage_report(db)}
    last_cleanup = db.scalar(
        select(AuditJob).where(AuditJob.job_type == "retention_cleanup").order_by(AuditJob.created_at.desc())
    )
    return {
        "app": {"debug": s.debug, "environment": s.environment, "access": "In-house: no login, server only"},
        "keys": {"GOOGLE_API_KEY": mask(s.google_api_key), "SERPAPI_KEY": mask(s.serpapi_key)},
        "limits": [
            {
                "sku": sku,
                "label": SKU_LABEL.get(sku, sku),
                "monthly_setting": monthly.upper(),
                "monthly_limit": usage[sku].monthly_limit,
                "month_count": usage[sku].month_count,
                "daily_setting": daily.upper(),
                "daily_limit": usage[sku].daily_limit,
                "day_count": usage[sku].day_count,
                "remaining": usage[sku].remaining,
                "blocked": usage[sku].blocked_message,
            }
            for sku, (monthly, daily) in SKU_LIMITS.items()
        ],
        "resets": {"daily": next_daily_reset().isoformat(), "monthly": next_monthly_reset().isoformat()},
        "features": {
            "RANKING_MODE": s.ranking_mode,
            "RANKING_CACHE_HOURS": s.ranking_cache_hours,
            "KEYWORD_CAP": s.keyword_cap,
            "MAPS_ZOOM": s.maps_zoom,
            "SERPAPI_REVIEWS_ENABLED": s.serpapi_reviews_enabled,
            "COMPETITOR_MAX": s.competitor_max,
            "COMPETITOR_DETAILS": s.competitor_details,
            "COMPETITOR_DETAILS_RESERVE": s.competitor_details_reserve,
            "BROWSER_FALLBACK": s.browser_fallback,
            "PROVIDER_RETRIES": s.provider_retries,
            "JOB_STALE_MINUTES": s.job_stale_minutes,
        },
        "retention": {
            "GOOGLE_DATA_TTL_DAYS": s.google_data_ttl_days,
            "CLEANUP_HOUR_UTC": s.cleanup_hour_utc,
            "last_cleanup": {
                "at": last_cleanup.created_at.isoformat(),
                "status": last_cleanup.status,
                "result": next(
                    (st.get("result") for st in last_cleanup.steps or [] if st.get("result")), None
                ),
            }
            if last_cleanup
            else None,
        },
        "worker_online": _worker_online(),
    }
