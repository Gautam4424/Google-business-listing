import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, JSONType, TimestampMixin, utcnow


class Keyword(IdMixin, TimestampMixin, Base):
    __tablename__ = "keywords"
    __table_args__ = (UniqueConstraint("project_id", "keyword", "location_name", "device"),)

    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    service_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("services.id", ondelete="SET NULL"))
    keyword: Mapped[str] = mapped_column(String(300))
    service: Mapped[str | None] = mapped_column(String(200))
    location_name: Mapped[str] = mapped_column(String(200))
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    language: Mapped[str] = mapped_column(String(10))
    country: Mapped[str] = mapped_column(String(2))
    device: Mapped[str] = mapped_column(String(10), default="mobile")
    search_radius_meters: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(32), default="generated")  # generated | user
    pattern: Mapped[str | None] = mapped_column(String(20))  # in_city | city | near_me | user
    active: Mapped[bool] = mapped_column(Boolean, default=True)  # only active keywords are rank-checked
    project_service_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("project_services.id", ondelete="SET NULL")
    )


class RankingRun(IdMixin, Base):
    """One provider request for one keyword, with the exact search context (brief §2)."""

    __tablename__ = "ranking_runs"

    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    keyword_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("keywords.id", ondelete="CASCADE"), index=True
    )
    audit_job_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("audit_jobs.id", ondelete="SET NULL")
    )
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    provider: Mapped[str] = mapped_column(String(50))
    result_type: Mapped[str] = mapped_column(String(20))  # local_pack | local_finder
    country: Mapped[str] = mapped_column(String(2))
    language: Mapped[str] = mapped_column(String(10))
    device: Mapped[str] = mapped_column(String(10))
    location_name: Mapped[str | None] = mapped_column(String(200))
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    search_radius_meters: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="succeeded")
    error: Mapped[str | None] = mapped_column(Text)
    raw_response_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("data_sources.id", ondelete="SET NULL")
    )
    keyword: Mapped[str | None] = mapped_column(String(300))  # the exact query sent
    search_location: Mapped[str | None] = mapped_column(String(300))  # provider's location (canonical / ll)
    pack_shown: Mapped[bool | None] = mapped_column(Boolean)  # local_pack: did Google show one at all
    estimated: Mapped[bool] = mapped_column(Boolean, default=False)  # local_pack derived from Maps top 3
    from_cache: Mapped[bool] = mapped_column(Boolean, default=False)
    client_rank: Mapped[int | None] = mapped_column(Integer)


class RankingResult(IdMixin, Base):
    __tablename__ = "ranking_results"

    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("ranking_runs.id", ondelete="CASCADE"), index=True
    )
    rank: Mapped[int] = mapped_column(Integer)
    place_id: Mapped[str | None] = mapped_column(String(255), index=True)
    cid: Mapped[str | None] = mapped_column(
        String(40), index=True
    )  # Google's numeric CID (links Pack <-> Maps)
    phone: Mapped[str | None] = mapped_column(String(50))
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    business_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("businesses.id", ondelete="SET NULL")
    )
    business_name: Mapped[str] = mapped_column(String(300))
    address: Mapped[str | None] = mapped_column(Text)
    primary_category: Mapped[str | None] = mapped_column(String(200))
    categories: Mapped[list | None] = mapped_column(JSONType)  # all GBP categories (Maps results)
    rating: Mapped[float | None] = mapped_column(Float)
    review_count: Mapped[int | None] = mapped_column(Integer)
    website_url: Mapped[str | None] = mapped_column(Text)
    maps_url: Mapped[str | None] = mapped_column(Text)
    is_client_business: Mapped[bool] = mapped_column(Boolean, default=False)
