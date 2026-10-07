"""`retention_cleanup` job: the nightly 30-day clean-up (scheduled by the worker; shows on the Jobs page)."""

from app.services.jobs import recover_stale_jobs
from app.services.retention import cleanup
from app.workers.pipeline import Step, StepContext, register


def clean_old_google_data(ctx: StepContext) -> dict:
    return cleanup(ctx.db)


def recover_jobs(ctx: StepContext) -> dict:
    return {"interrupted_jobs_marked": recover_stale_jobs(ctx.db)}


register(
    "retention_cleanup",
    [
        Step("clean_old_google_data", clean_old_google_data, required=True),
        Step("recover_jobs", recover_jobs, required=False),
    ],
)
