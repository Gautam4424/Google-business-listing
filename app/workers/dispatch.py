import uuid


def enqueue_job(job_id: uuid.UUID) -> None:
    from app.workers.celery_app import run_job_task

    run_job_task.apply_async(args=[str(job_id)], retry=False)
