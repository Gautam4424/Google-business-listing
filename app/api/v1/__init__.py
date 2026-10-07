from fastapi import APIRouter

from app.api.v1 import competitors, jobs, keywords, lookup, projects, rankings, report, usage

router = APIRouter(prefix="/v1")
router.include_router(projects.router)
router.include_router(lookup.router)
router.include_router(keywords.router)
router.include_router(rankings.router)
router.include_router(competitors.router)
router.include_router(report.router)
router.include_router(jobs.router)
router.include_router(usage.router)
