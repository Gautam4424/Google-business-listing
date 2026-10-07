import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models import AuditJob, Project
from app.schemas.job import JobCreate, JobOut
from app.services import jobs as jobs_service
from app.workers.pipeline import PIPELINES

log = logging.getLogger(__name__)
router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def create_job(body: JobCreate, db: Session = Depends(get_db)) -> AuditJob:
    if body.job_type not in PIPELINES:
        raise HTTPException(
            422,
            f"Unknown job_type '{body.job_type}'. Available: {sorted(PIPELINES)}",
        )
    if body.project_id and db.get(Project, body.project_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    return start_job(db, body.job_type, body.project_id, body.params)


def start_job(db: Session, job_type: str, project_id: uuid.UUID | None, params: dict) -> AuditJob:
    try:
        return jobs_service.start_job(db, job_type, project_id, params)
    except jobs_service.JobAlreadyRunning as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except jobs_service.QueueUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Job queue unavailable") from exc


@router.get("", response_model=list[JobOut])
def list_jobs(
    project_id: uuid.UUID | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
) -> list[AuditJob]:
    stmt = select(AuditJob).order_by(AuditJob.created_at.desc())
    if project_id:
        stmt = stmt.where(AuditJob.project_id == project_id)
    if status_filter:
        stmt = stmt.where(AuditJob.status == status_filter)
    return list(db.scalars(stmt.limit(max(1, min(limit, 200))).offset(max(offset, 0))))


@router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: uuid.UUID, db: Session = Depends(get_db)) -> AuditJob:
    job = db.get(AuditJob, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    return job
