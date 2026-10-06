import logging
import uuid

from sqlalchemy.orm import Session

from app.models import AuditJob
from app.models.base import utcnow
from app.workers import dispatch

log = logging.getLogger(__name__)


class QueueUnavailable(RuntimeError):
    pass


def start_job(db: Session, job_type: str, project_id: uuid.UUID | None, params: dict) -> AuditJob:
    """Create a queued job and hand it to the worker. Used by the API and by jobs that chain other jobs."""
    job = AuditJob(job_type=job_type, project_id=project_id, params=params, status="queued")
    db.add(job)
    db.commit()
    try:
        dispatch.enqueue_job(job.id)
    except Exception as exc:
        log.exception("Could not enqueue job %s", job.id)
        job.status, job.error, job.finished_at = (
            "failed",
            f"Job queue unavailable: {type(exc).__name__}",
            utcnow(),
        )
        db.commit()
        raise QueueUnavailable("Job queue unavailable") from exc
    return job
