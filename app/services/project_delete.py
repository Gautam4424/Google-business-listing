"""Delete a project and everything collected for it.

Child rows are deleted explicitly (not only by database cascades) so it behaves the same everywhere.
The client's Google business record (profile snapshots, reviews, location) goes too, unless another
project uses it as its client or competitor. API usage counters are kept: they are for the whole app.
"""

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import (
    AuditJob,
    Business,
    BusinessLocation,
    Competitor,
    CompetitorMetric,
    GapRecommendation,
    GbpProfile,
    GbpReview,
    Keyword,
    Project,
    ProjectService,
    RankingResult,
    RankingRun,
    ReviewTag,
    Service,
    WebsiteProfile,
)


def delete_project(db: Session, project: Project) -> dict:
    pid = project.id
    business_id = project.client_business_id
    run_ids = select(RankingRun.id).where(RankingRun.project_id == pid)
    competitor_ids = select(Competitor.id).where(Competitor.project_id == pid)
    counts = {
        "ranking_results": db.execute(
            delete(RankingResult).where(RankingResult.run_id.in_(run_ids))
        ).rowcount,
        "ranking_runs": db.execute(delete(RankingRun).where(RankingRun.project_id == pid)).rowcount,
        "keywords": db.execute(delete(Keyword).where(Keyword.project_id == pid)).rowcount,
    }
    db.execute(delete(CompetitorMetric).where(CompetitorMetric.competitor_id.in_(competitor_ids)))
    counts["competitors"] = db.execute(delete(Competitor).where(Competitor.project_id == pid)).rowcount
    counts["gaps"] = db.execute(delete(GapRecommendation).where(GapRecommendation.project_id == pid)).rowcount
    db.execute(delete(ProjectService).where(ProjectService.project_id == pid))
    db.execute(delete(Service).where(Service.project_id == pid))
    db.execute(delete(WebsiteProfile).where(WebsiteProfile.project_id == pid))
    counts["jobs"] = db.execute(delete(AuditJob).where(AuditJob.project_id == pid)).rowcount
    project.client_business_id = None
    db.flush()
    db.delete(project)
    db.flush()

    counts["google_business_removed"] = False
    if business_id is not None:
        still_used = db.scalar(
            select(Project.id).where(Project.client_business_id == business_id).limit(1)
        ) or db.scalar(select(Competitor.id).where(Competitor.business_id == business_id).limit(1))
        if not still_used:
            review_ids = select(GbpReview.id).where(GbpReview.business_id == business_id)
            db.execute(delete(ReviewTag).where(ReviewTag.review_id.in_(review_ids)))
            db.execute(delete(GbpReview).where(GbpReview.business_id == business_id))
            db.execute(delete(GbpProfile).where(GbpProfile.business_id == business_id))
            db.execute(delete(BusinessLocation).where(BusinessLocation.business_id == business_id))
            db.execute(delete(Business).where(Business.id == business_id))
            counts["google_business_removed"] = True
    db.commit()
    return counts
