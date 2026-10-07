"""`competitor_analysis` job (brief §3): competitors + gaps from the latest ranking check. 0 SerpApi searches.

Also runs automatically as the last step of every `ranking_check`.
"""

from app.models import Project
from app.services import competitors
from app.workers.pipeline import Step, StepContext, StepSkipped, register


def _project(ctx: StepContext) -> Project:
    project = ctx.db.get(Project, ctx.job.project_id) if ctx.job.project_id else None
    if project is None:
        raise ValueError("competitor_analysis needs a project_id")
    return project


def find_competitors(ctx: StepContext) -> dict:
    """Pick competitors with the brief's rule, snapshot their profiles, rebuild the gap list."""
    project = _project(ctx)
    # rankings collected by this job (ranking_check / full_audit), else the latest finished check
    ranking_job = ctx.job if "collect_rankings" in ctx.results else None
    try:
        return competitors.analyze(ctx.db, project, ctx.job, ranking_job)
    except competitors.NoRankingCheck as exc:
        raise StepSkipped(str(exc)) from exc


register("competitor_analysis", [Step("find_competitors", find_competitors, required=True)])
