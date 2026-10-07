import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, JSONType, ProvenanceMixin, TimestampMixin, utcnow


class Competitor(IdMixin, TimestampMixin, Base):
    __tablename__ = "competitors"
    __table_args__ = (UniqueConstraint("project_id", "business_id"),)

    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    business_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("businesses.id", ondelete="CASCADE"))
    keyword_share: Mapped[float] = mapped_column(Float)  # 0..1 of tracked keywords
    local_pack_count: Mapped[int] = mapped_column(Integer, default=0)
    local_finder_count: Mapped[int] = mapped_column(Integer, default=0)
    reason: Mapped[str] = mapped_column(String(200))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CompetitorMetric(IdMixin, TimestampMixin, ProvenanceMixin, Base):
    __tablename__ = "competitor_metrics"

    competitor_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("competitors.id", ondelete="CASCADE"), index=True
    )
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    audit_job_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("audit_jobs.id", ondelete="SET NULL"), index=True
    )
    primary_category: Mapped[str | None] = mapped_column(String(200))
    rating: Mapped[float | None] = mapped_column(Float)
    review_count: Mapped[int | None] = mapped_column(Integer)
    review_velocity_30d: Mapped[float | None] = mapped_column(Float)  # NULL until two snapshots exist
    categories: Mapped[list | None] = mapped_column(JSONType)
    services: Mapped[list | None] = mapped_column(JSONType)
    website_domain: Mapped[str | None] = mapped_column(String(255))
    local_pack_appearances: Mapped[int | None] = mapped_column(Integer)
    local_finder_appearances: Mapped[int | None] = mapped_column(Integer)
    keyword_overlap: Mapped[float | None] = mapped_column(Float)
    # [{"keyword", "local_pack_rank", "local_finder_rank",
    #   "client_local_pack_rank", "client_local_finder_rank"}]
    keywords: Mapped[list | None] = mapped_column(JSONType)
    # {"positive": {tag: n}, "negative": {tag: n}} from the public review sample (max 5, Places API)
    review_topics: Mapped[dict | None] = mapped_column(JSONType)
    review_sample_size: Mapped[int | None] = mapped_column(Integer)
    profile_source: Mapped[str | None] = mapped_column(String(40))  # google_places | search_results


class GapRecommendation(IdMixin, TimestampMixin, Base):
    __tablename__ = "gap_recommendations"

    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    audit_job_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("audit_jobs.id", ondelete="SET NULL")
    )
    gap_type: Mapped[str] = mapped_column(String(30))  # category | service | review | review_topic | ranking
    title: Mapped[str | None] = mapped_column(String(300))
    priority: Mapped[str] = mapped_column(String(10), default="medium")  # high | medium | low
    client_value: Mapped[dict | list | None] = mapped_column(JSONType)
    competitor_pattern: Mapped[dict | list | None] = mapped_column(JSONType)
    recommendation: Mapped[str] = mapped_column(Text)
    evidence: Mapped[dict | None] = mapped_column(JSONType)
