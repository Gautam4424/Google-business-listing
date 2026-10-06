import logging
import uuid

from celery import Celery

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.workers import jobs as _jobs  # noqa: F401  (registers pipelines)
from app.workers.pipeline import run_job

# httpx logs full request URLs at INFO, and SerpApi URLs contain the API key.
logging.getLogger("httpx").setLevel(logging.WARNING)

celery = Celery("local_seo_audit", broker=get_settings().redis_url)
celery.conf.update(
    task_serializer="json",
    accept_content=["json"],
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_ignore_result=True,
    broker_connection_retry_on_startup=True,
)


@celery.task(name="jobs.run")
def run_job_task(job_id: str) -> None:
    with SessionLocal() as db:
        run_job(db, uuid.UUID(job_id))
