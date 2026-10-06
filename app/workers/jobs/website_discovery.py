"""`website_discovery` job: read the project's website on its own.

No Google calls except an optional geocode.

Brief §4 flow: "Create project -> Discover website details -> Find and verify GBP". The website NAP found here
is what Phase 3 matching compares Google candidates against.
"""

from app.models import Project
from app.services.website_discovery import discover_website
from app.workers.pipeline import Step, StepContext, StepSkipped, register


def discover(ctx: StepContext) -> dict:
    project = ctx.db.get(Project, ctx.job.project_id) if ctx.job.project_id else None
    if project is None:
        raise ValueError("website_discovery needs a project_id")
    if not project.website_url:
        raise StepSkipped("The project has no website URL")
    business = project.client_business
    _, summary = discover_website(ctx.db, project, project.website_url, business)
    return summary


register("website_discovery", [Step("discover_website", discover, required=True)])
