import logging
import uuid

from celery import Celery
from celery.schedules import crontab
from celery.signals import after_setup_logger, worker_ready

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.models import AuditJob
from app.services.jobs import recover_stale_jobs
from app.workers import jobs as _jobs  # noqa: F401  (registers pipelines)
from app.workers.pipeline import run_job

log = logging.getLogger(__name__)
settings = get_settings()

celery = Celery("local_seo_audit", broker=settings.redis_url)
celery.conf.update(
    task_serializer="json",
    accept_content=["json"],
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_ignore_result=True,
    broker_connection_retry_on_startup=True,
    timezone="UTC",
    # Run by the worker itself (`-B`): no extra container.
    beat_schedule={
        "nightly-retention-cleanup": {
            "task": "maintenance.cleanup",
            "schedule": crontab(hour=settings.cleanup_hour_utc, minute=0),
        },
        "recover-stale-jobs": {"task": "maintenance.recover", "schedule": 1800.0},
    },
)


@after_setup_logger.connect
def _log_level(logger, **_):
    logger.setLevel(logging.DEBUG if settings.debug else logging.INFO)
    # httpx logs full request URLs at INFO/DEBUG, and SerpApi URLs contain the API key: never below WARNING.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


@worker_ready.connect
def _recover_on_start(**_):
    """Jobs that were running when the worker stopped can't finish: mark them, with a clear message."""
    with SessionLocal() as db:
        recover_stale_jobs(db, worker_starting=True)


@celery.task(name="jobs.run")
def run_job_task(job_id: str) -> None:
    with SessionLocal() as db:
        run_job(db, uuid.UUID(job_id))


@celery.task(name="maintenance.cleanup")
def cleanup_task() -> None:
    with SessionLocal() as db:
        job = AuditJob(job_type="retention_cleanup", project_id=None, params={}, status="queued")
        db.add(job)
        db.commit()
        run_job(db, job.id)


@celery.task(name="maintenance.recover")
def recover_task() -> None:
    with SessionLocal() as db:
        recover_stale_jobs(db)
