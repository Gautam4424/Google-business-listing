"""Free-tier guard: count every billable call and refuse it before a free limit is reached."""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models import ApiUsage
from app.models.base import utcnow

# sku -> (monthly setting, daily setting)
SKU_LIMITS = {
    "google_places_text_search": ("quota_places_textsearch_monthly", "quota_places_textsearch_daily"),
    "google_places_text_search_enterprise": (
        "quota_places_textsearch_enterprise_monthly",
        "quota_places_textsearch_enterprise_daily",
    ),
    "google_places_details": ("quota_places_details_monthly", "quota_places_details_daily"),
    "google_geocoding": ("quota_geocoding_monthly", "quota_geocoding_daily"),
    "serpapi_search": ("quota_serpapi_monthly", "quota_serpapi_daily"),
}


class QuotaExceeded(Exception):
    pass


@dataclass
class UsageStatus:
    sku: str
    day_count: int
    daily_limit: int
    month_count: int
    monthly_limit: int

    @property
    def remaining(self) -> int:
        left = self.monthly_limit - self.month_count
        if self.daily_limit:
            left = min(left, self.daily_limit - self.day_count)
        return max(left, 0)


def _today() -> date:
    return datetime.now(UTC).date()


def _limits(sku: str, settings: Settings) -> tuple[int, int]:
    if sku not in SKU_LIMITS:
        raise KeyError(f"Unknown SKU: {sku}")
    monthly_attr, daily_attr = SKU_LIMITS[sku]
    return getattr(settings, monthly_attr), getattr(settings, daily_attr)


def _counts(db: Session, sku: str, day: date) -> tuple[int, int]:
    month_start = day.replace(day=1)
    day_count = db.scalar(select(ApiUsage.count).where(ApiUsage.sku == sku, ApiUsage.day == day)) or 0
    month_count = db.scalar(
        select(func.coalesce(func.sum(ApiUsage.count), 0)).where(
            ApiUsage.sku == sku, ApiUsage.day >= month_start, ApiUsage.day <= day
        )
    )
    return day_count, int(month_count or 0)


def get_usage(
    db: Session, sku: str, day: date | None = None, settings: Settings | None = None
) -> UsageStatus:
    settings = settings or get_settings()
    day = day or _today()
    monthly_limit, daily_limit = _limits(sku, settings)
    day_count, month_count = _counts(db, sku, day)
    return UsageStatus(sku, day_count, daily_limit, month_count, monthly_limit)


def _locked_row(db: Session, sku: str, day: date) -> ApiUsage:
    stmt = select(ApiUsage).where(ApiUsage.sku == sku, ApiUsage.day == day).with_for_update()
    row = db.scalar(stmt)
    if row is None:
        if db.get_bind().dialect.name == "postgresql":
            # Safe when several workers create today's row at the same time.
            db.execute(
                pg_insert(ApiUsage)
                .values(id=uuid.uuid4(), sku=sku, day=day, count=0, updated_at=utcnow())
                .on_conflict_do_nothing(index_elements=["sku", "day"])
            )
        else:
            db.add(ApiUsage(sku=sku, day=day, count=0))
            db.flush()
        row = db.scalar(stmt)
    return row


def consume(
    db: Session, sku: str, n: int = 1, day: date | None = None, settings: Settings | None = None
) -> None:
    """Reserve n calls for sku, or raise QuotaExceeded. Commits so the reservation survives a failed call."""
    settings = settings or get_settings()
    day = day or _today()
    monthly_limit, daily_limit = _limits(sku, settings)

    row = _locked_row(db, sku, day)
    _, month_count = _counts(db, sku, day)
    if month_count + n > monthly_limit:
        db.rollback()
        raise QuotaExceeded(f"{sku}: monthly free-tier limit reached ({month_count}/{monthly_limit})")
    if daily_limit and row.count + n > daily_limit:
        db.rollback()
        raise QuotaExceeded(f"{sku}: daily limit reached ({row.count}/{daily_limit})")
    row.count += n
    db.commit()


def release(db: Session, sku: str, n: int = 1, day: date | None = None) -> None:
    """Give back a reservation for a call the provider rejected without charging (e.g. invalid key)."""
    row = _locked_row(db, sku, day or _today())
    row.count = max(row.count - n, 0)
    db.commit()


def usage_report(db: Session, settings: Settings | None = None) -> list[UsageStatus]:
    return [get_usage(db, sku, settings=settings) for sku in SKU_LIMITS]
