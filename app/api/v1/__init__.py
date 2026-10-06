from fastapi import APIRouter

from app.api.v1 import jobs, lookup, projects, usage

router = APIRouter(prefix="/v1")
router.include_router(projects.router)
router.include_router(lookup.router)
router.include_router(jobs.router)
router.include_router(usage.router)
