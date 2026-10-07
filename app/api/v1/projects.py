import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.jobs import start_job
from app.core.db import get_db
from app.models import AuditJob, BusinessLocation, GbpProfile, GbpReview, Project, Service
from app.models.base import utcnow
from app.schemas.discovery import DiscoverOptions, DiscoveryOut, SelectCandidate
from app.schemas.job import JobOut
from app.schemas.profile import AuditOptions, GbpProfileOut, OfferingOut, ProfileOut, ReviewOut, WebsiteOut
from app.schemas.project import ProjectCreate, ProjectOut
from app.services import project_delete
from app.services.discovery import link_place, select_candidate
from app.services.jobs import JobAlreadyRunning, active_job
from app.services.nap import Nap, compare_nap
from app.services.review_analysis import review_summary, tags_by_review
from app.services.website_discovery import latest_website_profile

router = APIRouter(prefix="/projects", tags=["projects"])


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
def create_project(body: ProjectCreate, db: Session = Depends(get_db)) -> ProjectOut:
    website_url = str(body.website_url) if body.website_url else None
    project = Project(
        name=body.name,
        input_business_name=body.business_name,
        input_address=body.address,
        input_phone=body.phone,
        website_url=website_url,
        country=body.country,
        language=body.language,
        service_areas=[a.model_dump() for a in body.service_areas],
        user_keywords=body.keywords,
    )
    db.add(project)
    if body.place_id:  # picked by the user in Quick fill; verified by the audit's verify_match step
        link_place(db, project, body.place_id, body.business_name, website_url, None)
        project.match_status = "manually_selected"
        project.matched_at = utcnow()
    db.commit()
    return ProjectOut.from_model(project)


@router.get("", response_model=list[ProjectOut])
def list_projects(limit: int = 50, offset: int = 0, db: Session = Depends(get_db)) -> list[ProjectOut]:
    limit = max(1, min(limit, 200))
    rows = db.scalars(select(Project).order_by(Project.created_at.desc()).limit(limit).offset(offset))
    return [ProjectOut.from_model(p) for p in rows]


def _get_project(db: Session, project_id: uuid.UUID) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    return project


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(project_id: uuid.UUID, db: Session = Depends(get_db)) -> ProjectOut:
    return ProjectOut.from_model(_get_project(db, project_id))


@router.delete("/{project_id}")
def delete_project(project_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    """Delete the project and all its data (audits, reviews, keywords, rankings, competitors, gaps, jobs).

    Refused (409) while one of its jobs is queued or running. API usage counters are kept.
    """
    project = _get_project(db, project_id)
    running = active_job(db, project.id)
    if running is not None:
        raise HTTPException(409, str(JobAlreadyRunning(running)).replace("try again", "delete the project"))
    name = project.name
    return {"deleted": name, **project_delete.delete_project(db, project)}


@router.post("/{project_id}/gbp-audit", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def run_gbp_audit(
    project_id: uuid.UUID, body: AuditOptions | None = None, db: Session = Depends(get_db)
) -> AuditJob:
    """Start the GBP audit: Google profile, reviews, and the website (NAP, social links, offerings).

    Reviews come from Google (max 5, free). `top10_reviews: true` fetches the top 10 + owner replies via
    SerpApi instead (2 SerpApi credits).
    """
    _get_project(db, project_id)
    params = {"top10_reviews": body.top10_reviews} if body and body.top10_reviews is not None else {}
    return start_job(db, "gbp_audit", project_id, params)


@router.post("/{project_id}/website-discovery", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def run_website_discovery(project_id: uuid.UUID, db: Session = Depends(get_db)) -> AuditJob:
    """Read only the project's website: NAP (+ coordinates), social links, offerings.

    No Google profile calls (only an optional geocode).
    """
    project = _get_project(db, project_id)
    if not project.website_url:
        raise HTTPException(422, "The project has no website URL")
    return start_job(db, "website_discovery", project_id, {})


@router.get("/{project_id}/profile", response_model=ProfileOut)
def get_profile(project_id: uuid.UUID, db: Session = Depends(get_db)) -> ProfileOut:
    """Latest results for the project: Google profile, reviews, website NAP, NAP check, offerings."""
    project = _get_project(db, project_id)
    business = project.client_business
    last_job = db.scalar(
        select(AuditJob)
        .where(
            AuditJob.project_id == project.id,
            AuditJob.job_type.in_(
                (
                    "gbp_audit",
                    "website_discovery",
                    "discover_business",
                    "review_analysis",
                    "ranking_check",
                    "competitor_analysis",
                    "full_audit",
                )
            ),
        )
        .order_by(AuditJob.created_at.desc())
        .limit(1)
    )
    services = db.scalars(
        select(Service)
        .where(Service.project_id == project.id)
        .order_by(Service.confidence_score.desc(), Service.service_name)
    ).all()
    out = ProfileOut(
        project=ProjectOut.from_model(project),
        last_audit=JobOut.model_validate(last_job) if last_job else None,
        offerings=[
            OfferingOut(
                name=s.service_name, source=s.source, source_url=s.source_url, confidence=s.confidence_score
            )
            for s in services
        ],
    )
    site = latest_website_profile(db, project.id)
    if site:
        out.website = WebsiteOut.model_validate(site)
        out.social_profiles = site.social_profiles or {}

    profile = location = None
    if business is not None:
        if not site:
            out.social_profiles = business.social_profiles or {}
        profile = db.scalar(
            select(GbpProfile)
            .where(GbpProfile.business_id == business.id)
            .order_by(GbpProfile.created_at.desc())
        )
        if profile:
            out.profile = GbpProfileOut.model_validate(profile)
        location = db.scalar(select(BusinessLocation).where(BusinessLocation.business_id == business.id))
        if location:
            out.location = {
                "latitude": location.latitude,
                "longitude": location.longitude,
                "website_latitude": location.website_latitude,
                "website_longitude": location.website_longitude,
                "pin_vs_website_address_distance_meters": location.pin_vs_website_address_distance_meters,
            }
        rows = db.scalars(
            select(GbpReview).where(GbpReview.business_id == business.id).order_by(GbpReview.position)
        ).all()
        tags = tags_by_review(db, [r.id for r in rows])
        out.reviews = [
            ReviewOut.model_validate(r).model_copy(update={"tags": tags.get(r.id, [])}) for r in rows
        ]
        if any(r.sentiment for r in rows):
            out.review_summary = review_summary(db, project)

    if profile and site:
        lists = (site.nap_sources or {}).get("all") or {}
        nap = Nap(
            name=site.business_name,
            phone=site.phone,
            phone_e164=site.phone_e164,
            address=site.address,
            all_addresses=lists.get("addresses") or [],
            all_phones_e164=lists.get("phones_e164") or [],
        )
        out.nap_check = compare_nap(
            nap, profile.business_name, profile.phone_number, profile.formatted_address, project.country
        )
    return out


@router.post("/{project_id}/discover-business", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def run_discover_business(
    project_id: uuid.UUID, body: DiscoverOptions | None = None, db: Session = Depends(get_db)
) -> AuditJob:
    """Find and verify the project's Google Business Profile (brief §1).

    Reads the website (if any), searches Google for name + address, scores up to 5 candidates.
    A confident match (>= 0.85) is linked automatically (and the audit started when `then_audit`);
    otherwise the candidates are saved for `discover-business/select`. 1 Google search (cached 7 days).
    """
    _get_project(db, project_id)
    then_audit = body.then_audit if body else True
    return start_job(db, "discover_business", project_id, {"then_audit": then_audit})


@router.get("/{project_id}/discover-business", response_model=DiscoveryOut)
def get_discovery(project_id: uuid.UUID, db: Session = Depends(get_db)) -> DiscoveryOut:
    """The latest match result: status, confidence, reasons and candidates (best first)."""
    return DiscoveryOut.from_project(_get_project(db, project_id))


@router.post("/{project_id}/discover-business/select", response_model=DiscoveryOut)
def select_business(
    project_id: uuid.UUID, body: SelectCandidate, db: Session = Depends(get_db)
) -> DiscoveryOut:
    """The user confirms which candidate is the business. Optionally starts the audit."""
    project = _get_project(db, project_id)
    try:
        select_candidate(db, project, body.place_id)
    except LookupError as exc:
        raise HTTPException(422, str(exc)) from exc
    if body.then_audit:
        start_job(db, "gbp_audit", project.id, {})
    return DiscoveryOut.from_project(project)


@router.post("/{project_id}/reviews/analyze", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def run_review_analysis(project_id: uuid.UUID, db: Session = Depends(get_db)) -> AuditJob:
    """Re-run sentiment, tags, themes and mentioned services on the stored reviews. No API calls."""
    project = _get_project(db, project_id)
    if project.client_business is None:
        raise HTTPException(422, "The project is not linked to a Google listing yet")
    return start_job(db, "review_analysis", project_id, {})


@router.get("/{project_id}/reviews")
def get_reviews(project_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    """Reviews with sentiment and tags, plus the review summary (brief Step 5)."""
    project = _get_project(db, project_id)
    business = project.client_business
    if business is None:
        return {"review_summary": None, "reviews": []}
    rows = db.scalars(
        select(GbpReview).where(GbpReview.business_id == business.id).order_by(GbpReview.position)
    ).all()
    tags = tags_by_review(db, [r.id for r in rows])
    return {
        "review_summary": review_summary(db, project) if any(r.sentiment for r in rows) else None,
        "reviews": [
            ReviewOut.model_validate(r).model_copy(update={"tags": tags.get(r.id, [])}).model_dump()
            for r in rows
        ],
    }
