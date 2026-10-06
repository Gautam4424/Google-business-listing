import logging
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.v1 import router as v1_router
from app.core.db import get_db
from app.workers import jobs as _jobs  # noqa: F401  (registers pipelines)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
# httpx logs full request URLs at INFO, and SerpApi URLs contain the API key.
logging.getLogger("httpx").setLevel(logging.WARNING)

app = FastAPI(
    title="Local SEO Audit API",
    version="0.1.0",
    description="GBP audit, local rank tracking and competitor gap analysis.",
)
app.include_router(v1_router)
app.mount("/ui", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="ui")


@app.middleware("http")
async def revalidate_ui(request: Request, call_next):
    """Browsers must re-check UI files on every load, so a new version shows without a hard refresh."""
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/ui"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/ui/")


@app.get("/health", tags=["system"])
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse({"status": "error", "database": "unavailable"}, status_code=503)
    return {"status": "ok", "database": "ok"}
