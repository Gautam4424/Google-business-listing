import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import AuditJob
from app.models.base import utcnow
from app.workers import dispatch

log = logging.getLogger(__name__)

# Jobs that work on one project's data or spend credits: only one at a time per project.
PROJECT_JOBS = {
    "gbp_audit", "full_audit", "ranking_check", "competitor_analysis", "discover_business",
    "website_discovery", "review_analysis",
}  # fmt: skip
ACTIVE = ("queued", "running")
JOB_LABEL = {
    "gbp_audit": "audit", "full_audit": "full audit", "ranking_check": "ranking check",
    "competitor_analysis": "competitor analysis", "discover_business": "business search",
    "website_discovery": "website read", "review_analysis": "review analysis",
}  # fmt: skip
INTERRUPTED = "Interrupted: the app restarted while this job was running. Please run it again."
NEVER_STARTED = "Never started: the job queue was unavailable. Please run it again."


class QueueUnavailable(RuntimeError):
    pass


class JobAlreadyRunning(RuntimeError):
    def __init__(self, job: AuditJob):
        minutes = max(int((utcnow() - _aware(job.created_at)).total_seconds() // 60), 0)
        when = "just now" if minutes < 1 else f"{minutes} min ago"
        super().__init__(
            f"A {JOB_LABEL.get(job.job_type, job.job_type)} is already {job.status} for this project "
            f"(started {when}). Wait for it to finish, then try again."
        )
        self.job = job


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _stale_before():
    return utcnow() - timedelta(minutes=get_settings().job_stale_minutes)


def active_job(db: Session, project_id: uuid.UUID, exclude: uuid.UUID | None = None) -> AuditJob | None:
    """The project's queued/running job, if any (stale ones are ignored: see recover_stale_jobs)."""
    stmt = (
        select(AuditJob)
        .where(AuditJob.project_id == project_id, AuditJob.status.in_(ACTIVE))
        .where(AuditJob.job_type.in_(PROJECT_JOBS), AuditJob.created_at >= _stale_before())
        .order_by(AuditJob.created_at.desc())
    )
    if exclude is not None:
        stmt = stmt.where(AuditJob.id != exclude)
    return db.scalar(stmt.limit(1))


def start_job(
    db: Session,
    job_type: str,
    project_id: uuid.UUID | None,
    params: dict,
    parent: AuditJob | None = None,
) -> AuditJob:
    """Create a queued job and hand it to the worker. Used by the API and by jobs that chain other jobs.

    Refuses (JobAlreadyRunning) a second project job while one is queued or running, so double clicks
    and two people working on the same project never spend credits twice. `parent`: the job starting
    this one (a chain), which is allowed.
    """
    if project_id and job_type in PROJECT_JOBS:
        running = active_job(db, project_id, exclude=parent.id if parent else None)
        if running is not None:
            raise JobAlreadyRunning(running)
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


def recover_stale_jobs(db: Session, worker_starting: bool = False) -> int:
    """Mark jobs that can no longer finish as failed, with a clear message.

    worker_starting: every `running` job belongs to the worker that just restarted, so all are
    interrupted. Otherwise only jobs older than JOB_STALE_MINUTES are.
    """
    now = utcnow()
    stmt = select(AuditJob).where(AuditJob.status.in_(ACTIVE))
    count = 0
    for job in db.scalars(stmt).all():
        old = _aware(job.created_at) < _stale_before()
        if job.status == "running" and (worker_starting or old):
            job.error = INTERRUPTED
        elif job.status == "queued" and old:
            job.error = NEVER_STARTED
        else:
            continue
        job.status, job.finished_at = "failed", now
        steps = [dict(s) for s in job.steps or []]
        for s in steps:
            if s.get("status") in ("running", "pending"):
                s["status"], s["error"] = "skipped", "Not run: the job was interrupted"
        job.steps = steps
        count += 1
    db.commit()
    if count:
        log.warning("Marked %d interrupted job(s) as failed", count)
    return count
