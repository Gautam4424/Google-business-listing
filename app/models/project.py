import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Float, ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, IdMixin, JSONType, TimestampMixin

if TYPE_CHECKING:
    from app.models.business import Business


class Project(IdMixin, TimestampMixin, Base):
    __tablename__ = "projects"

    name: Mapped[str] = mapped_column(String(200))
    website_url: Mapped[str | None] = mapped_column(Text)
    input_business_name: Mapped[str] = mapped_column(String(300))
    input_address: Mapped[str | None] = mapped_column(Text)
    input_phone: Mapped[str | None] = mapped_column(String(50))
    country: Mapped[str] = mapped_column(String(2))  # ISO 3166-1 alpha-2, e.g. "GB"
    language: Mapped[str] = mapped_column(String(10), default="en")
    # [{"name": "Manchester, UK", "latitude": 53.48, "longitude": -2.24}]
    service_areas: Mapped[list] = mapped_column(JSONType, default=list)
    user_keywords: Mapped[list] = mapped_column(JSONType, default=list)
    # Where ranking checks search from: city (city centre) | country (whole country) | business (its own pin)
    search_from: Mapped[str] = mapped_column(String(10), default="city", server_default="city")
    settings: Mapped[dict] = mapped_column(JSONType, default=dict)
    client_business_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("businesses.id", ondelete="SET NULL")
    )
    client_business: Mapped["Business"] = relationship(lazy="joined")

    # GBP discovery & matching (Phase 3)
    # auto_selected | manual_review_required | manually_selected | not_found
    match_status: Mapped[str | None] = mapped_column(String(30))
    match_confidence: Mapped[float | None] = mapped_column(Float)
    match_reasons: Mapped[list | None] = mapped_column(JSONType)
    mismatch_reasons: Mapped[list | None] = mapped_column(JSONType)
    match_candidates: Mapped[list | None] = mapped_column(JSONType)
    matched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
