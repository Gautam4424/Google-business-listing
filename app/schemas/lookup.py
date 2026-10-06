from pydantic import BaseModel, Field


class LookupRequest(BaseModel):
    query: str = Field(
        min_length=3,
        max_length=500,
        examples=["Astaneh Construction 3080 Yonge St Ste 6060, Toronto, ON M4N 1S1, Canada"],
    )


class LookupCandidate(BaseModel):
    business_name: str
    address: str | None
    phone: str | None
    website_url: str | None
    country: str | None
    city: str | None
    region: str | None
    service_area: str | None
    latitude: float | None
    longitude: float | None
    place_id: str | None
    maps_url: str | None
    primary_category: str | None


class LookupResponse(BaseModel):
    query: str
    source: str = Field(description="google_places, or parser when Google was not used")
    cached: bool
    warning: str | None
    candidates: list[LookupCandidate]
