import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, JSONType, TimestampMixin, utcnow


class AuditJob(IdMixin, TimestampMixin, Base):
    """Background job. Status: queued -> running -> completed | partial_success | failed."""

    __tablename__ = "audit_jobs"

    project_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    job_type: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    params: Mapped[dict] = mapped_column(JSONType, default=dict)
    # [{"name", "required", "status", "started_at", "finished_at", "result", "error"}]
    steps: Mapped[list] = mapped_column(JSONType, default=list)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DataSource(IdMixin, Base):
    """Raw provider response, kept for traceability and as a cache. Deleted after expires_at."""

    __tablename__ = "data_sources"

    provider: Mapped[str] = mapped_column(String(50))
    endpoint: Mapped[str] = mapped_column(String(200))
    request_hash: Mapped[str] = mapped_column(String(64), index=True)
    request_params: Mapped[dict] = mapped_column(JSONType, default=dict)
    response: Mapped[dict | list | None] = mapped_column(JSONType)
    status_code: Mapped[int | None] = mapped_column(Integer)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class AppSetting(Base):
    """A setting changed on the Settings page. Overrides the .env value (key = the .env name)."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class SettingChange(IdMixin, Base):
    """History of changes made on the Settings page (API keys are stored masked here)."""

    __tablename__ = "setting_changes"

    key: Mapped[str] = mapped_column(String(80), index=True)
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)  # None = reset to the .env value
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class ApiUsage(IdMixin, Base):
    """Calls per provider SKU per day, used by the free-tier quota guard."""

    __tablename__ = "api_usage"
    __table_args__ = (UniqueConstraint("sku", "day"),)

    sku: Mapped[str] = mapped_column(String(50))
    day: Mapped[date] = mapped_column(Date)
    count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
