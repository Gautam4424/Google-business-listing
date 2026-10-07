"""Phase 8 endpoints (brief §3, §4): competitors and gap analysis. No SerpApi searches."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.jobs import start_job
from app.core.db import get_db
from app.models import AuditJob, GapRecommendation, Project
from app.schemas.job import JobOut
from app.services import competitors
from app.services.gaps import PRIORITY_ORDER, TYPE_ORDER, gap_out

router = APIRouter(prefix="/projects/{project_id}", tags=["competitors"])


def _project(db: Session, project_id: uuid.UUID) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    return project


@router.get("/competitors")
def get_competitors(project_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    """Competitors from the latest analysis, side by side with the client (categories, reviews, rankings)."""
    return competitors.competitors_report(db, _project(db, project_id))


@router.post("/competitors/analyze", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def analyze_competitors(project_id: uuid.UUID, db: Session = Depends(get_db)) -> AuditJob:
    """Re-run competitor discovery + gaps on the latest ranking check (0 SerpApi searches).

    Uses up to COMPETITOR_MAX Google Place Details calls (cached), only for competitors' review samples.
    """
    project = _project(db, project_id)
    try:
        competitors.latest_ranking_check(db, project)
    except competitors.NoRankingCheck as exc:
        raise HTTPException(422, str(exc)) from exc
    return start_job(db, "competitor_analysis", project.id, {})


@router.get("/gaps")
def get_gaps(project_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    """Gap recommendations (brief §3 shape: gap_type, client_value, competitor_pattern, recommendation)."""
    project = _project(db, project_id)
    rows = db.scalars(select(GapRecommendation).where(GapRecommendation.project_id == project.id)).all()
    gaps = sorted(
        (gap_out(g) for g in rows),
        key=lambda g: (PRIORITY_ORDER.get(g["priority"], 9), TYPE_ORDER.get(g["gap_type"], 9)),
    )
    job_id = rows[0].audit_job_id if rows else None
    return {
        "analysis_job_id": str(job_id) if job_id else None,
        "counts": {t: sum(1 for g in gaps if g["gap_type"] == t) for t in TYPE_ORDER},
        "gaps": gaps,
        "note": "Recommendations are things to review, not to copy: only add a category or service the "
        "business really provides and is eligible for.",
    }
