"""`full_audit` job (brief §4 job flow), one click from website to report:

  website -> find/verify GBP -> profile -> reviews -> services/keywords -> rankings -> competitors/gaps
  -> report

Only finding and reading the Google profile are required. Everything after is optional: a failed step
(e.g. one SerpApi search, or not enough credits) gives `partial_success` and the rest is kept.
Rankings run only when `params.rankings` is true (SerpApi searches: 2 per active keyword, 1 in maps_only).
"""

from app.models import Project
from app.workers.jobs import competitor_analysis, gbp_audit, ranking_check
from app.workers.pipeline import Step, StepContext, StepSkipped, register


def _wants_rankings(ctx: StepContext) -> None:
    if not (ctx.job.params or {}).get("rankings", True):
        raise StepSkipped("Ranking check not requested (no SerpApi searches used)")


def check_budget(ctx: StepContext) -> dict:
    _wants_rankings(ctx)
    return ranking_check.check_budget(ctx)


def collect_rankings(ctx: StepContext) -> dict:
    _wants_rankings(ctx)
    if "check_budget" not in ctx.results:  # never spend credits without a passed budget check
        raise StepSkipped("Skipped: the credit check did not pass")
    return ranking_check.collect_rankings(ctx)


def compute_visibility(ctx: StepContext) -> dict:
    if "collect_rankings" not in ctx.results:
        raise StepSkipped("No rankings collected in this audit")
    return ranking_check.compute_visibility(ctx)


def build_report(ctx: StepContext) -> dict:
    project = ctx.db.get(Project, ctx.job.project_id)
    base = f"/v1/projects/{project.id}/report"
    return {"html": f"{base}?format=html", "pdf": f"{base}?format=pdf", "csv": f"{base}?format=csv"}


register(
    "full_audit",
    [
        Step("resolve_place", gbp_audit.resolve_place, required=True),
        Step("fetch_profile", gbp_audit.fetch_profile, required=True),
        Step("fetch_reviews", gbp_audit.fetch_reviews, required=False),
        Step("crawl_website", gbp_audit.crawl_website, required=False),
        Step("analyze_reviews", gbp_audit.analyze_reviews, required=False),
        Step("build_services", gbp_audit.build_services, required=False),
        Step("generate_keywords", gbp_audit.generate_keywords, required=False),
        Step("verify_match", gbp_audit.verify_match, required=False),
        Step("check_budget", check_budget, required=False),
        Step("collect_rankings", collect_rankings, required=False),
        Step("compute_visibility", compute_visibility, required=False),
        Step("find_competitors", competitor_analysis.find_competitors, required=False),
        Step("build_report", build_report, required=False),
    ],
)
