"""`discover_business` job (brief §1 + §4 flow): read the website -> find and verify the GBP.

Confident match (>= 0.85, no close second) -> linked automatically, and with params.then_audit the
`gbp_audit` job is started. Otherwise the candidates are saved for the user to choose from.
"""

from datetime import timedelta

from app.models import Project
from app.models.base import utcnow
from app.services.discovery import discover
from app.services.jobs import start_job
from app.services.website_discovery import discover_website, latest_website_profile
from app.workers.pipeline import Step, StepContext, StepSkipped, register

WEBSITE_FRESH_FOR = timedelta(days=7)


def _project(ctx: StepContext) -> Project:
    project = ctx.db.get(Project, ctx.job.project_id) if ctx.job.project_id else None
    if project is None:
        raise ValueError("discover_business needs a project_id")
    return project


def read_website(ctx: StepContext) -> dict:
    project = _project(ctx)
    if not project.website_url:
        raise StepSkipped("No website URL: matching will use your inputs only")
    latest = latest_website_profile(ctx.db, project.id)
    if latest and latest.collected_at and utcnow() - _aware(latest.collected_at) < WEBSITE_FRESH_FOR:
        raise StepSkipped("Website read recently; reusing it")
    _, summary = discover_website(ctx.db, project, project.website_url, project.client_business)
    return summary


def _aware(dt):
    from datetime import UTC

    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def match_business(ctx: StepContext) -> dict:
    project = _project(ctx)
    decision = discover(ctx.db, project)
    result = {
        "status": decision.status,
        "place_id": decision.place_id,
        "match_confidence": decision.match_confidence,
        "match_reasons": decision.match_reasons,
        "manual_review_required": decision.status == "manual_review_required",
        "candidates": len(decision.candidates),
    }
    if decision.reason:
        result["note"] = decision.reason
    if decision.status == "not_found":
        raise LookupError(decision.reason or "Business not found on Google")
    if decision.status == "auto_selected" and ctx.job.params.get("then_audit"):
        audit = start_job(ctx.db, "gbp_audit", project.id, {}, parent=ctx.job)
        result["audit_job_id"] = str(audit.id)
    return result


register(
    "discover_business",
    [
        Step("read_website", read_website, required=False),
        Step("match_business", match_business, required=True),
    ],
)
