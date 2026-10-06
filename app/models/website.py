import uuid

from sqlalchemy import Boolean, Float, ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, JSONType, ProvenanceMixin, TimestampMixin


class WebsiteProfile(IdMixin, TimestampMixin, ProvenanceMixin, Base):
    """What the business's own website says (one snapshot per crawl). Independent of the Google profile."""

    __tablename__ = "website_profiles"

    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    url: Mapped[str] = mapped_column(Text)
    business_name: Mapped[str | None] = mapped_column(String(300))
    phone: Mapped[str | None] = mapped_column(String(50))
    phone_e164: Mapped[str | None] = mapped_column(String(20))
    address: Mapped[str | None] = mapped_column(Text)
    address_parts: Mapped[dict | None] = mapped_column(JSONType)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    geocode_source: Mapped[str | None] = mapped_column(String(30))  # schema | nominatim | google_geocoding
    nap_sources: Mapped[dict | None] = mapped_column(JSONType)  # field -> {source, url, confidence}
    social_profiles: Mapped[dict | None] = mapped_column(JSONType)
    pages_fetched: Mapped[list | None] = mapped_column(JSONType)
    rendered_with_browser: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[list | None] = mapped_column(JSONType)
