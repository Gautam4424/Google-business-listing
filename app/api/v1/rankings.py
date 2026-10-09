"""Phase 7 endpoints (brief §4): run a ranking check, read rankings and visibility."""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.v1.jobs import start_job
from app.core.db import get_db
from app.models import AuditJob, Project
from app.schemas.job import JobOut
from app.services import rankings

router = APIRouter(prefix="/projects/{project_id}/rankings", tags=["rankings"])

SearchFrom = Literal["city", "area", "country", "business", "current"]


class Here(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


class RunOptions(BaseModel):
    mode: Literal["full", "maps_only"] | None = None  # default: RANKING_MODE
    force: bool = False  # ignore the saved results from the last RANKING_CACHE_HOURS
    search_from: SearchFrom | None = Field(
        None,
        description="city = city centre point | area = the city as Google's named area ('Choose area') | "
        "country | business location | current = your browser's location (needs `here`). "
        "Default: the project's last choice",
    )
    here: Here | None = Field(None, description="Your location from the browser, for search_from=current")


def _here(search_from: str | None, here: dict | None) -> dict | None:
    if search_from != "current":
        return None
    if here is None:
        raise HTTPException(422, "Your current location is needed: allow location access in the browser")
    return here


def _project(db: Session, project_id: uuid.UUID) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    return project


@router.get("/estimate")
def get_estimate(
    project_id: uuid.UUID,
    mode: Literal["full", "maps_only"] | None = None,
    force: bool = False,
    search_from: SearchFrom | None = None,
    lat: float | None = Query(None, ge=-90, le=90),
    lng: float | None = Query(None, ge=-180, le=180),
    db: Session = Depends(get_db),
) -> dict:
    """SerpApi searches a check would use (saved = free), how many are left, and the search points.

    For search_from=current, pass the browser's location as `lat` and `lng`.
    """
    here = _here(search_from, {"lat": lat, "lng": lng} if lat is not None and lng is not None else None)
    return rankings.estimate(db, _project(db, project_id), mode, force, search_from, here)


@router.post("/run", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def run_rankings(
    project_id: uuid.UUID, body: RunOptions | None = None, db: Session = Depends(get_db)
) -> AuditJob:
    """Check Local Pack + Local Finder positions for every active keyword (brief §2).

    Full mode: 2 SerpApi searches per keyword; maps_only: 1. Searched from the chosen point (city centre,
    whole country or the business location; remembered per project). Runs keyword by keyword (Local Pack
    first) and stops at a free-tier limit, keeping the keywords done so far. Refused (422) only when no
    search can run at all (limit reached), with the reset time.
    """
    project = _project(db, project_id)
    opts = body or RunOptions()
    here = _here(opts.search_from, opts.here.model_dump() if opts.here else None)
    est = rankings.estimate(db, project, opts.mode, opts.force, opts.search_from, here)
    if not est["serpapi_configured"]:
        raise HTTPException(422, "SERPAPI_KEY is not set")
    if est["active_keywords"] == 0:
        raise HTTPException(422, "No active keywords: generate keywords and switch some on first")
    if not est["can_start"]:
        raise HTTPException(422, est["limit_message"] or "No SerpApi searches left")
    params = {"mode": est["mode"], "force": opts.force, "search_from": est["search_from"]}
    if here:
        params["here"] = here
    job = start_job(db, "ranking_check", project.id, params)
    if est["search_from"] in rankings.SAVED_SCOPES:  # remembered for the next check and the full audit
        project.search_from = est["search_from"]
        db.commit()
    return job


@router.get("/keywords/{keyword_id}")
def get_keyword_results(
    project_id: uuid.UUID,
    keyword_id: uuid.UUID,
    job_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
) -> dict:
    """All businesses found for one keyword: Local Pack (3) + Local Finder (top 20), latest check."""
    project = _project(db, project_id)
    job = db.get(AuditJob, job_id) if job_id else None
    if job is not None and job.project_id != project.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Check not found for this project")
    for candidate in [job] if job else rankings.ranking_checks(db, project):
        found = rankings.keyword_results(db, candidate, keyword_id) if candidate else None
        if found:
            return found
    raise HTTPException(status.HTTP_404_NOT_FOUND, "No results for this keyword yet")


@router.get("")
def get_rankings(project_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    """Latest check: visibility score, counts, per-keyword ranks and change vs the previous check made from
    the same place; history."""
    project = _project(db, project_id)
    checks = rankings.ranking_checks(db, project)
    extra = {"live": rankings.live_check(db, project), "limit": rankings.limit_notice(db),
             "failed": rankings.last_failed_check(db, project, checks[0] if checks else None),
             "search_from": rankings.scope_of(project)}  # fmt: skip
    if not checks:
        return {"latest": None, "history": [], "top_businesses": [], **extra}
    latest = rankings.check_report(db, project, checks[0], rankings.previous_check(checks, checks[0]))
    stop = next((s for s in checks[0].steps or [] if s.get("name") == "collect_rankings"), {})
    latest["note"] = stop.get("error") if stop.get("status") == "partial" else None
    history = []
    for job in checks:
        s = rankings.check_report(db, project, job, rankings.previous_check(checks, job))["summary"]
        history.append({"job_id": str(job.id), "checked_at": job.created_at.isoformat(),
                        "visibility_score": s["visibility_score"], "keywords": s["keywords"],
                        "in_local_pack": s["in_local_pack"], "status": job.status,
                        "search_from": rankings.job_scope(job)})  # fmt: skip
    return {"latest": latest, "history": history, "top_businesses": rankings.top_businesses(db, checks[0]),
            **extra}  # fmt: skip
