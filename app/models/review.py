import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, JSONType, ProvenanceMixin, TimestampMixin


class GbpReview(IdMixin, TimestampMixin, ProvenanceMixin, Base):
    __tablename__ = "gbp_reviews"
    __table_args__ = (UniqueConstraint("business_id", "review_id"),)

    business_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("businesses.id", ondelete="CASCADE"), index=True
    )
    review_id: Mapped[str] = mapped_column(String(512))
    author_name: Mapped[str | None] = mapped_column(String(300))
    author_url: Mapped[str | None] = mapped_column(Text)  # required for Google attribution
    rating: Mapped[int | None] = mapped_column(Integer)
    review_text: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    owner_reply: Mapped[str | None] = mapped_column(Text)
    review_url: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(String(10))
    sentiment: Mapped[str | None] = mapped_column(String(10))  # positive | neutral | negative
    position: Mapped[int | None] = mapped_column(
        Integer
    )  # order returned by the source (most relevant first)
    relative_publish_time: Mapped[str | None] = mapped_column(String(60))  # e.g. "a month ago"
    sentiment_score: Mapped[float | None] = mapped_column(Float)  # -1 .. 1
    mentioned_services: Mapped[list | None] = mapped_column(JSONType)


class ReviewTag(IdMixin, TimestampMixin, ProvenanceMixin, Base):
    __tablename__ = "review_tags"

    review_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("gbp_reviews.id", ondelete="CASCADE"), index=True
    )
    tag: Mapped[str] = mapped_column(String(200))
    theme: Mapped[str | None] = mapped_column(String(50))
    sentiment: Mapped[str | None] = mapped_column(String(10))
    sentence: Mapped[str | None] = mapped_column(Text)  # evidence: the sentence the tag was found in
    mentioned_service_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("services.id", ondelete="SET NULL")
    )
