import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, MetaData, String, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column

# JSONB on Postgres, plain JSON elsewhere (SQLite in tests).
JSONType = JSON().with_variant(JSONB(), "postgresql")

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class IdMixin:
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ProvenanceMixin:
    """Brief §5: every collected record says where it came from, when, and how sure we are."""

    source: Mapped[str] = mapped_column(String(64))
    source_url: Mapped[str | None] = mapped_column(Text)  # URL or provider endpoint
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confidence_score: Mapped[float | None] = mapped_column(Float)

    @declared_attr
    def raw_response_id(cls) -> Mapped[uuid.UUID | None]:
        return mapped_column(Uuid, ForeignKey("data_sources.id", ondelete="SET NULL"))
