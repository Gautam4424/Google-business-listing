"""Runs a job's steps in order and records per-step status.

Final status:
  any required step failed  -> failed (later steps are skipped)
  any optional step failed  -> partial_success
  otherwise                 -> completed
"""

import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.security import redact
from app.models import AuditJob
from app.models.base import utcnow

log = logging.getLogger(__name__)


class StepSkipped(Exception):
    """Raise from a step when it cannot run but that is not an error (e.g. an optional key is not set)."""


class StepPartial(Exception):
    """Raise from a step that did part of its work (e.g. some searches failed): result kept, job partial."""

    def __init__(self, message: str, result: dict):
        super().__init__(message)
        self.result = result


@dataclass
class StepContext:
    db: Session
    job: AuditJob
    settings: Settings
    results: dict  # outputs of earlier steps, by step name (saved on the job)
    data: dict = field(default_factory=dict)  # large in-memory hand-offs between steps (not saved)


@dataclass(frozen=True)
class Step:
    name: str
    func: Callable[[StepContext], dict | None]
    required: bool = True


# job_type -> steps. Filled by app.workers.jobs modules.
PIPELINES: dict[str, list[Step]] = {}


def register(job_type: str, steps: list[Step]) -> None:
    PIPELINES[job_type] = steps


def _set_step(job: AuditJob, index: int, **changes) -> None:
    steps = [dict(s) for s in job.steps]  # new list so SQLAlchemy sees the JSON change
    steps[index].update(changes)
    job.steps = steps


def run_job(db: Session, job_id: uuid.UUID) -> AuditJob:
    job = db.get(AuditJob, job_id)
    if job is None:
        raise LookupError(f"Job {job_id} not found")
    if job.status != "queued":
        log.warning("Job %s is %s, not queued; ignoring", job_id, job.status)
        return job

    steps = PIPELINES.get(job.job_type)
    if steps is None:
        job.status, job.error, job.finished_at = "failed", f"Unknown job type: {job.job_type}", utcnow()
        db.commit()
        return job

    job.steps = [{"name": s.name, "required": s.required, "status": "pending"} for s in steps]
    job.status, job.started_at = "running", utcnow()
    db.commit()

    ctx = StepContext(db=db, job=job, settings=get_settings(), results={})
    optional_failed = required_failed = False

    for i, step in enumerate(steps):
        if required_failed:
            _set_step(job, i, status="skipped", error="Not run: an earlier required step failed")
            continue

        _set_step(job, i, status="running", started_at=utcnow().isoformat())
        db.commit()
        started = time.monotonic()
        try:
            result = step.func(ctx) or {}
            ctx.results[step.name] = result
            _set_step(job, i, status="succeeded", result=result)
        except StepSkipped as exc:
            _set_step(job, i, status="skipped", error=redact(str(exc)))
        except StepPartial as exc:
            ctx.results[step.name] = exc.result
            _set_step(job, i, status="partial", result=exc.result, error=redact(str(exc)))
            optional_failed = True
        except Exception as exc:
            db.rollback()
            log.exception("Job %s step %s failed", job.id, step.name)
            _set_step(job, i, status="failed", error=redact(f"{type(exc).__name__}: {exc}"))
            if step.required:
                required_failed = True
            else:
                optional_failed = True
        _set_step(
            job, i, finished_at=utcnow().isoformat(), duration_ms=int((time.monotonic() - started) * 1000)
        )
        db.commit()

    if required_failed:
        job.status = "failed"
        job.error = next(s["error"] for s in job.steps if s["status"] == "failed")
    elif optional_failed:
        job.status = "partial_success"
    else:
        job.status = "completed"
    job.finished_at = utcnow()
    db.commit()
    return job
