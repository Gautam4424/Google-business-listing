"""`review_analysis` job: re-run the review NLP on the stored reviews. No API calls."""

from app.models import Project
from app.services.review_analysis import analyze_project_reviews
from app.workers.pipeline import Step, StepContext, register


def analyze(ctx: StepContext) -> dict:
    project = ctx.db.get(Project, ctx.job.project_id) if ctx.job.project_id else None
    if project is None:
        raise ValueError("review_analysis needs a project_id")
    return analyze_project_reviews(ctx.db, project)


register("review_analysis", [Step("analyze_reviews", analyze, required=True)])
