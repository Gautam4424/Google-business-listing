import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models import Project


class DiscoverOptions(BaseModel):
    then_audit: bool = Field(default=True, description="Start the GBP audit when a match is auto-selected")


class SelectCandidate(BaseModel):
    place_id: str = Field(max_length=255)
    then_audit: bool = Field(default=True, description="Start the GBP audit after selecting")


class CandidateOut(BaseModel):
    place_id: str | None
    business_name: str
    address: str | None = None
    phone: str | None = None
    website_url: str | None = None
    primary_category: str | None = None
    maps_url: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    match_confidence: float
    match_reasons: list[str]
    mismatch_reasons: list[str]
    signals: dict


class DiscoveryOut(BaseModel):
    """Brief §1 shape: place_id, match_confidence, match_reasons, manual_review_required (+ candidates)."""

    project_id: uuid.UUID
    status: str | None = Field(
        description="auto_selected | manual_review_required | manually_selected | not_found"
    )
    place_id: str | None
    match_confidence: float | None
    match_reasons: list[str]
    mismatch_reasons: list[str]
    manual_review_required: bool
    candidates: list[CandidateOut]
    matched_at: datetime | None

    @classmethod
    def from_project(cls, p: Project) -> "DiscoveryOut":
        return cls(
            project_id=p.id,
            status=p.match_status,
            place_id=p.client_business.place_id if p.client_business else None,
            match_confidence=p.match_confidence,
            match_reasons=p.match_reasons or [],
            mismatch_reasons=p.mismatch_reasons or [],
            manual_review_required=p.match_status == "manual_review_required",
            candidates=[CandidateOut.model_validate(c) for c in p.match_candidates or []],
            matched_at=p.matched_at,
        )
