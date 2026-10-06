import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, JSONType, ProvenanceMixin, TimestampMixin


class Business(IdMixin, TimestampMixin, ProvenanceMixin, Base):
    """Any business we know about: the client, candidates and competitors. Keyed on Google place_id."""

    __tablename__ = "businesses"

    place_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    name: Mapped[str] = mapped_column(String(300))
    domain: Mapped[str | None] = mapped_column(String(255), index=True)
    is_client: Mapped[bool] = mapped_column(Boolean, default=False)
    # {"facebook": "https://facebook.com/acme", ...} found on the business website
    social_profiles: Mapped[dict | None] = mapped_column(JSONType)


class BusinessLocation(IdMixin, TimestampMixin, ProvenanceMixin, Base):
    __tablename__ = "business_locations"

    business_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("businesses.id", ondelete="CASCADE"), index=True
    )
    formatted_address: Mapped[str | None] = mapped_column(Text)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    website_address: Mapped[str | None] = mapped_column(Text)
    website_latitude: Mapped[float | None] = mapped_column(Float)
    website_longitude: Mapped[float | None] = mapped_column(Float)
    pin_vs_website_address_distance_meters: Mapped[float | None] = mapped_column(Float)
    is_service_area_business: Mapped[bool | None] = mapped_column(Boolean)


class GbpProfile(IdMixin, TimestampMixin, ProvenanceMixin, Base):
    """Normalised GBP snapshot (brief §1). Unavailable fields stay NULL, never 'not offered'."""

    __tablename__ = "gbp_profiles"

    business_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("businesses.id", ondelete="CASCADE"), index=True
    )
    place_id: Mapped[str] = mapped_column(String(255), index=True)
    business_name: Mapped[str | None] = mapped_column(String(300))
    formatted_address: Mapped[str | None] = mapped_column(Text)
    map_pin_status: Mapped[str | None] = mapped_column(String(20))  # present | missing
    maps_url: Mapped[str | None] = mapped_column(Text)
    website_url: Mapped[str | None] = mapped_column(Text)
    primary_category: Mapped[str | None] = mapped_column(String(200))
    secondary_categories: Mapped[list | None] = mapped_column(JSONType)
    phone_number: Mapped[str | None] = mapped_column(String(50))
    opening_hours: Mapped[dict | None] = mapped_column(JSONType)
    special_hours: Mapped[list | None] = mapped_column(JSONType)
    business_status: Mapped[str | None] = mapped_column(String(40))
    editorial_summary: Mapped[str | None] = mapped_column(Text)
    plus_code: Mapped[str | None] = mapped_column(String(80))
    service_options: Mapped[list | None] = mapped_column(JSONType)
    accessibility_attributes: Mapped[list | None] = mapped_column(JSONType)
    rating: Mapped[float | None] = mapped_column(Float)
    review_count: Mapped[int | None] = mapped_column(Integer)
    photos_count_available: Mapped[int | None] = mapped_column(Integer)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
