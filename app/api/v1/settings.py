"""Settings page: status (read-only) and the settings that can be changed in the app.

Changed values are stored in the database and override .env within seconds (no restart). Keys are
write-only: they are never returned, only masked. Infrastructure settings stay in .env.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.models import AuditJob
from app.services import app_settings
from app.services.app_settings import mask
from app.services.quota import SKU_LABEL, SKU_LIMITS, next_daily_reset, next_monthly_reset, usage_report

router = APIRouter(prefix="/settings", tags=["settings"])


class SettingsUpdate(BaseModel):
    values: dict[str, str | int | bool] = Field(description="{.env name: new value}, e.g. {'KEYWORD_CAP': 8}")
    accept_charges: bool = Field(False, description="Required to save a monthly limit above the free tier")


@router.get("/editable")
def get_editable(db: Session = Depends(get_db)) -> dict:
    """Settings that can be changed here, grouped, with their current value, .env value and source."""
    return app_settings.listing(db)


@router.patch("")
def update_settings(body: SettingsUpdate, db: Session = Depends(get_db)) -> dict:
    """Change settings (all or nothing). Applies within seconds in the web app and the worker."""
    try:
        changed = app_settings.update(db, body.values, body.accept_charges)
    except app_settings.CostConfirmationNeeded as exc:
        raise HTTPException(409, str(exc)) from exc
    except app_settings.SettingError as exc:
        raise HTTPException(422, str(exc)) from exc
    restart = [k for k in changed if app_settings.BY_KEY[k].restart]
    return {"changed": changed, "restart_needed": restart}


@router.delete("/{key}")
def reset_setting(key: str, db: Session = Depends(get_db)) -> dict:
    """Go back to the .env value for this setting."""
    try:
        was = app_settings.reset(db, key)
    except app_settings.SettingError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"key": key, "reset": was}


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
