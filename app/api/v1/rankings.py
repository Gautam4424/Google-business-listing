"""Phase 7 endpoints (brief §4): run a ranking check, read rankings and visibility."""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.v1.jobs import start_job
from app.core.db import get_db
from app.models import AuditJob, Project
from app.schemas.job import JobOut
from app.services import rankings

router = APIRouter(prefix="/projects/{project_id}/rankings", tags=["rankings"])


class RunOptions(BaseModel):
    mode: Literal["full", "maps_only"] | None = None  # default: RANKING_MODE
    force: bool = False  # ignore the saved results from the last RANKING_CACHE_HOURS


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
    db: Session = Depends(get_db),
) -> dict:
    """SerpApi searches a check would use (saved results are free) and how many are really left."""
    return rankings.estimate(db, _project(db, project_id), mode, force)


@router.post("/run", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def run_rankings(
    project_id: uuid.UUID, body: RunOptions | None = None, db: Session = Depends(get_db)
) -> AuditJob:
    """Check Local Pack + Local Finder positions for every active keyword (brief §2).

    Full mode: 2 SerpApi searches per keyword; maps_only: 1. Refused (422) when not enough searches are left.
    """
    project = _project(db, project_id)
    opts = body or RunOptions()
    est = rankings.estimate(db, project, opts.mode, opts.force)
    if not est["serpapi_configured"]:
        raise HTTPException(422, "SERPAPI_KEY is not set")
    if est["active_keywords"] == 0:
        raise HTTPException(422, "No active keywords: generate keywords and switch some on first")
    if not est["enough"]:
        raise HTTPException(
            422,
            f"Not enough SerpApi searches: this check needs {est['searches_needed']}, "
            f"{est['credits_left']} left (renews {est['renews_on'] or 'next month'}). "
            "Switch some keywords off or use maps_only mode.",
        )
    return start_job(db, "ranking_check", project.id, {"mode": est["mode"], "force": opts.force})


@router.get("")
def get_rankings(project_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    """Latest check: visibility score, counts, per-keyword ranks and change vs the previous check; history."""
    project = _project(db, project_id)
    checks = rankings.ranking_checks(db, project)
    if not checks:
        return {"latest": None, "history": [], "top_businesses": []}
    latest = rankings.check_report(db, project, checks[0], checks[1] if len(checks) > 1 else None)
    history = []
    for i, job in enumerate(checks):
        prev = checks[i + 1] if i + 1 < len(checks) else None
        s = rankings.check_report(db, project, job, prev)["summary"]
        history.append({"job_id": str(job.id), "checked_at": job.created_at.isoformat(),
                        "visibility_score": s["visibility_score"], "keywords": s["keywords"],
                        "in_local_pack": s["in_local_pack"], "status": job.status})  # fmt: skip
    return {"latest": latest, "history": history, "top_businesses": rankings.top_businesses(db, checks[0])}
