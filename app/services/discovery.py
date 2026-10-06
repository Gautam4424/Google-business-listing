"""Find the project's Google Business Profile, score the candidates and link the right one (Phase 3)."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Business, Project
from app.models.base import utcnow
from app.services.business_lookup import lookup_business
from app.services.matching import Decision, Score, build_reference, decide, domain_of, score_candidate
from app.services.website_discovery import latest_website_profile


class ManualReviewRequired(Exception):
    pass


def search_query(project: Project) -> str:
    parts = [project.input_business_name, project.input_address]
    if not project.input_address and project.service_areas:
        parts.append(project.service_areas[0].get("name"))
    return " ".join(p for p in parts if p)


def link_place(
    db: Session, project: Project, place_id: str, name: str, website_url: str | None, confidence: float | None
) -> Business:
    """One businesses row per place_id, shared across projects; marked as this project's client."""
    business = db.scalar(select(Business).where(Business.place_id == place_id))
    if business is None:
        business = Business(
            place_id=place_id,
            name=name,
            source="google_places",
            source_url="places:searchText",
            last_verified_at=utcnow(),
        )
        db.add(business)
    business.is_client = True
    business.confidence_score = confidence
    business.domain = domain_of(website_url) or business.domain
    project.client_business = business
    return business


def _save(project: Project, decision: Decision) -> None:
    project.match_status = decision.status
    project.match_confidence = decision.match_confidence
    project.match_reasons = decision.match_reasons or None
    best = decision.candidates[0] if decision.candidates else {}
    project.mismatch_reasons = (best.get("mismatch_reasons") or []) + (
        [decision.reason] if decision.reason else []
    ) or None
    project.match_candidates = decision.candidates or None
    project.matched_at = utcnow()


def discover(db: Session, project: Project) -> Decision:
    """Search Google, score candidates, auto-link when confident. Commits."""
    found = lookup_business(db, search_query(project))
    if found.source != "google_places":
        decision = Decision("not_found", None, None, [], [], found.warning or "Not found on Google")
    else:
        ref = build_reference(project, latest_website_profile(db, project.id))
        decision = decide(ref, found.candidates)
    _save(project, decision)
    if decision.status == "auto_selected":
        best = decision.candidates[0]
        link_place(
            db,
            project,
            best["place_id"],
            best["business_name"],
            best["website_url"],
            best["match_confidence"],
        )
    db.commit()
    return decision


def select_candidate(db: Session, project: Project, place_id: str) -> dict:
    """The user picks one of the stored candidates."""
    row = next((c for c in project.match_candidates or [] if c.get("place_id") == place_id), None)
    if row is None:
        raise LookupError("place_id is not one of this project's candidates; run discover-business first")
    link_place(
        db, project, place_id, row["business_name"], row.get("website_url"), row.get("match_confidence")
    )
    project.match_status = "manually_selected"
    project.match_confidence = row.get("match_confidence")
    project.match_reasons = row.get("match_reasons") or None
    project.mismatch_reasons = row.get("mismatch_reasons") or None
    project.matched_at = utcnow()
    db.commit()
    return row


class _ProfileCandidate:
    """Adapter so a saved GBP profile can be scored like a search candidate."""

    def __init__(self, profile, location):
        self.place_id = profile.place_id
        self.business_name = profile.business_name
        self.address = profile.formatted_address
        self.phone = profile.phone_number
        self.website_url = profile.website_url
        self.latitude = location.latitude if location else None
        self.longitude = location.longitude if location else None


def verify_linked(db: Session, project: Project, profile, location) -> Score:
    """Score the already-linked profile against the website (e.g. after Quick fill). Keeps the status."""
    website = latest_website_profile(db, project.id)
    score = score_candidate(build_reference(project, website), _ProfileCandidate(profile, location))
    project.match_confidence = score.match_confidence
    project.match_reasons = score.match_reasons or None
    project.mismatch_reasons = score.mismatch_reasons or None
    project.match_status = project.match_status or "manually_selected"
    project.matched_at = utcnow()
    if project.client_business:
        project.client_business.confidence_score = score.match_confidence
    db.commit()
    return score
