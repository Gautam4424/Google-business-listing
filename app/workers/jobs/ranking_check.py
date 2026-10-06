"""`ranking_check` job (brief §2): budget -> Local Pack + Local Finder per active keyword -> visibility."""

from app.models import Project
from app.services import rankings
from app.workers.pipeline import Step, StepContext, StepPartial, register


def _project(ctx: StepContext) -> Project:
    project = ctx.db.get(Project, ctx.job.project_id) if ctx.job.project_id else None
    if project is None:
        raise ValueError("ranking_check needs a project_id")
    return project


def check_budget(ctx: StepContext) -> dict:
    params = ctx.job.params or {}
    est = rankings.estimate(ctx.db, _project(ctx), params.get("mode"), params.get("force", False))
    if not est["serpapi_configured"]:
        raise RuntimeError("SERPAPI_KEY is not set")
    if est["active_keywords"] == 0:
        raise ValueError("No active keywords: generate keywords and switch some on first")
    if not est["enough"]:
        raise rankings.NotEnoughCredits(
            f"Not enough SerpApi searches: this check needs {est['searches_needed']}, "
            f"{est['credits_left']} left (renews {est['renews_on'] or 'next month'})"
        )
    return est


def collect_rankings(ctx: StepContext) -> dict:
    params = ctx.job.params or {}
    result = rankings.run_check(
        ctx.db, _project(ctx), ctx.job, params.get("mode"), params.get("force", False)
    )
    if result["failed"] and result["failed"] == result["keywords"] * (
        1 if result["mode"] == "maps_only" else 2
    ):
        raise RuntimeError("Every search failed: " + "; ".join(result["errors"][:3]))
    if result["failed"]:
        raise StepPartial(f"{result['failed']} search(es) failed; the rest were saved", result)
    return result


def compute_visibility(ctx: StepContext) -> dict:
    project = _project(ctx)
    previous = next((j for j in rankings.ranking_checks(ctx.db, project) if j.id != ctx.job.id), None)
    return rankings.check_report(ctx.db, project, ctx.job, previous)["summary"]


register(
    "ranking_check",
    [
        Step("check_budget", check_budget, required=True),
        Step("collect_rankings", collect_rankings, required=True),
        Step("compute_visibility", compute_visibility, required=True),
    ],
)
