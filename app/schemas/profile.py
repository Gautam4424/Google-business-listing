from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.job import JobOut
from app.schemas.project import ProjectOut


class AuditOptions(BaseModel):
    top10_reviews: bool | None = Field(
        default=None,
        description="Fetch the top 10 reviews + owner replies via SerpApi (2 credits). "
        "Default: SERPAPI_REVIEWS_ENABLED (off) -> Google's 5 reviews, free.",
    )


class GbpProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    place_id: str
    business_name: str | None
    formatted_address: str | None
    map_pin_status: str | None
    maps_url: str | None
    website_url: str | None
    primary_category: str | None
    secondary_categories: list[str] | None
    phone_number: str | None
    opening_hours: dict | None
    special_hours: list | None
    business_status: str | None
    service_options: list[str] | None
    accessibility_attributes: list[str] | None
    rating: float | None
    review_count: int | None
    photos_count_available: int | None
    editorial_summary: str | None
    plus_code: str | None
    last_checked_at: datetime | None
    source: str


class ReviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    position: int | None
    review_id: str
    author_name: str | None
    author_url: str | None
    rating: int | None
    review_text: str | None
    published_at: datetime | None
    relative_publish_time: str | None
    owner_reply: str | None
    review_url: str | None
    language: str | None
    source: str
    sentiment: str | None = None
    sentiment_score: float | None = None
    mentioned_services: list[str] | None = None
    tags: list[dict] = Field(default_factory=list, description="[{tag, theme, sentiment, sentence}]")


class OfferingOut(BaseModel):
    name: str
    source: str = Field(description="gbp_category, website_schema, website_service_page, website_heading")
    source_url: str | None
    confidence: float | None


class WebsiteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    url: str
    business_name: str | None
    phone: str | None
    phone_e164: str | None
    address: str | None
    address_parts: dict | None
    latitude: float | None
    longitude: float | None
    geocode_source: str | None
    nap_sources: dict | None
    pages_fetched: list[str] | None
    rendered_with_browser: bool
    notes: list[str] | None
    collected_at: datetime


class ProfileOut(BaseModel):
    project: ProjectOut
    last_audit: JobOut | None = None
    profile: GbpProfileOut | None = None
    website: WebsiteOut | None = None
    nap_check: dict | None = Field(
        default=None, description="name/phone/address: Google vs website, status match | mismatch | missing"
    )
    location: dict | None = None
    social_profiles: dict[str, str] = Field(default_factory=dict)
    offerings: list[OfferingOut] = Field(default_factory=list)
    reviews: list[ReviewOut] = Field(default_factory=list)
    review_summary: dict | None = Field(
        default=None,
        description="Brief Step 5: total_review_count, average_rating, sentiment_distribution, "
        "top_positive_topics, top_negative_topics, themes, monthly",
    )
