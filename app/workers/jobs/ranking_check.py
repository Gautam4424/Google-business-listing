"""`ranking_check` job (brief §2): budget -> Local Pack + Local Finder per active keyword -> visibility."""

from app.models import Project
from app.services import rankings
from app.workers.jobs.competitor_analysis import find_competitors
from app.workers.pipeline import Step, StepContext, StepPartial, register


def _project(ctx: StepContext) -> Project:
    project = ctx.db.get(Project, ctx.job.project_id) if ctx.job.project_id else None
    if project is None:
        raise ValueError("ranking_check needs a project_id")
    return project


def check_budget(ctx: StepContext) -> dict:
    params = ctx.job.params or {}
    est = rankings.estimate(
        ctx.db, _project(ctx), params.get("mode"), params.get("force", False), params.get("search_from"),
        params.get("here"),
    )  # fmt: skip
    if not est["serpapi_configured"]:
        raise RuntimeError("SERPAPI_KEY is not set")
    if est["active_keywords"] == 0:
        raise ValueError("No active keywords: generate keywords and switch some on first")
    if not est["can_start"]:
        raise rankings.NotEnoughCredits(est["limit_message"] or "No SerpApi searches left")
    if not est["enough"]:  # still runs: keyword by keyword until the limit, results so far are kept
        est["note"] = (
            f"Only {est['credits_left']} of {est['searches_needed']} searches left: "
            "the check stops at the limit and keeps the keywords done so far"
        )
    return est


def collect_rankings(ctx: StepContext) -> dict:
    params = ctx.job.params or {}
    result = rankings.run_check(
        ctx.db,
        _project(ctx),
        ctx.job,
        params.get("mode"),
        params.get("force", False),
        params.get("search_from"),
        params.get("here"),
    )
    saved = result["searches_made"] + result["searches_from_cache"]
    if not saved:
        reason = result["stopped_by_limit"] or "; ".join(result["errors"][:3])
        raise RuntimeError(f"No ranking data: {reason}")
    if result["stopped_by_limit"]:
        raise StepPartial(
            f"Limit reached after {result['keywords_checked']} of {result['keywords']} keywords; "
            f"their results are saved. {result['stopped_by_limit']}",
            result,
        )
    if result["failed"]:
        raise StepPartial(f"{result['failed']} search(es) failed; the rest were saved", result)
    return result


def compute_visibility(ctx: StepContext) -> dict:
    project = _project(ctx)
    previous = rankings.previous_check(rankings.ranking_checks(ctx.db, project), ctx.job)
    return rankings.check_report(ctx.db, project, ctx.job, previous)["summary"]


register(
    "ranking_check",
    [
        Step("check_budget", check_budget, required=True),
        Step("collect_rankings", collect_rankings, required=True),
        Step("compute_visibility", compute_visibility, required=True),
        # Phase 8: competitors + gaps from these results (0 SerpApi searches)
        Step("find_competitors", find_competitors, required=False),
    ],
)
