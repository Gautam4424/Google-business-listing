import uuid
from datetime import datetime

from pydantic import AnyHttpUrl, BaseModel, Field, field_validator

from app.models import Project


class ServiceArea(BaseModel):
    name: str = Field(min_length=1, max_length=200, examples=["Manchester, UK"])
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    business_name: str = Field(min_length=1, max_length=300)
    address: str | None = None
    phone: str | None = Field(default=None, max_length=50)
    website_url: AnyHttpUrl | None = None
    country: str = Field(min_length=2, max_length=2, examples=["GB"])
    language: str = Field(default="en", min_length=2, max_length=10)
    service_areas: list[ServiceArea] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list, description="User-entered keywords")
    place_id: str | None = Field(
        default=None,
        max_length=255,
        description="Google place_id chosen from /v1/lookup/business; links the project to that GBP",
    )

    @field_validator("country")
    @classmethod
    def upper_country(cls, v: str) -> str:
        return v.upper()

    @field_validator("keywords")
    @classmethod
    def clean_keywords(cls, v: list[str]) -> list[str]:
        seen, out = set(), []
        for k in (k.strip() for k in v):
            if k and k.lower() not in seen:
                seen.add(k.lower())
                out.append(k)
        return out


class ProjectOut(BaseModel):
    id: uuid.UUID
    name: str
    business_name: str
    address: str | None
    phone: str | None
    website_url: str | None
    country: str
    language: str
    service_areas: list[ServiceArea]
    keywords: list[str]
    client_business_id: uuid.UUID | None
    place_id: str | None
    match_status: str | None = None
    match_confidence: float | None = None
    match_reasons: list[str] | None = None
    mismatch_reasons: list[str] | None = None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, p: Project) -> "ProjectOut":
        return cls(
            id=p.id,
            name=p.name,
            business_name=p.input_business_name,
            address=p.input_address,
            phone=p.input_phone,
            website_url=p.website_url,
            country=p.country,
            language=p.language,
            service_areas=p.service_areas or [],
            keywords=p.user_keywords or [],
            client_business_id=p.client_business_id,
            place_id=p.client_business.place_id if p.client_business else None,
            match_status=p.match_status,
            match_confidence=p.match_confidence,
            match_reasons=p.match_reasons,
            mismatch_reasons=p.mismatch_reasons,
            created_at=p.created_at,
            updated_at=p.updated_at,
        )
