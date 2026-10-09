"""Phase 9 endpoints (brief §4): one-click full audit, and the report as HTML / PDF / CSV."""

import re
import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.v1.jobs import start_job
from app.core.db import get_db
from app.models import AuditJob, Project
from app.schemas.job import JobOut
from app.services import rankings, report

router = APIRouter(prefix="/projects/{project_id}", tags=["report"])


class FullAuditOptions(BaseModel):
    rankings: bool = Field(True, description="Include a ranking check (SerpApi: 2 per active keyword)")
    mode: Literal["full", "maps_only"] | None = None
    top10_reviews: bool | None = Field(None, description="Top 10 reviews via SerpApi (2 credits)")
    search_from: Literal["city", "area", "country", "business"] | None = Field(
        None, description="Where the ranking check searches from (default: the project's last choice)"
    )


def _project(db: Session, project_id: uuid.UUID) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    return project


@router.post("/full-audit", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def run_full_audit(
    project_id: uuid.UUID, body: FullAuditOptions | None = None, db: Session = Depends(get_db)
) -> AuditJob:
    """Everything in one job (brief §4 flow): website, GBP, profile, reviews, services/keywords, rankings,
    competitors, gaps, report.

    If a ranking check is included and keywords already exist, it is refused (422) up front when there are
    not enough SerpApi searches left. Keywords created during the audit are checked again before any search.
    A failed optional step gives `partial_success`; the rest is kept.
    """
    project = _project(db, project_id)
    opts = body or FullAuditOptions()
    params: dict = {"rankings": opts.rankings}
    if opts.top10_reviews is not None:
        params["top10_reviews"] = opts.top10_reviews
    if opts.rankings:
        est = rankings.estimate(db, project, opts.mode, scope=opts.search_from)
        params["mode"] = est["mode"]
        params["search_from"] = est["search_from"]
        if est["active_keywords"] and not est["can_start"]:
            raise HTTPException(
                422,
                f"{est['limit_message'] or 'No SerpApi searches left'}. Run the full audit without the "
                "ranking check, or wait for the reset.",
            )
    job = start_job(db, "full_audit", project.id, params)
    if opts.rankings:
        project.search_from = params["search_from"]
        db.commit()
    return job


def _filename(data: dict, ext: str) -> str:
    name = (data["profile"] or {}).get("business_name") or data["project"]["business_name"]
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60] or "report"
    return f"local-seo-audit-{slug}-{datetime.now(UTC):%Y-%m-%d}.{ext}"


@router.get("/report", response_model=None)
def get_report(
    project_id: uuid.UUID,
    format: Literal["html", "pdf", "csv", "json"] = "html",
    section: str | None = None,
    download: bool = False,
    db: Session = Depends(get_db),
) -> Response | dict:
    """The audit report from stored data (no API calls).

    - `html`: printable page; `pdf`: the same page as A4 PDF
    - `csv`: a .zip with profile, nap_check, reviews, keywords, rankings, competitors, gaps;
      or one file with `section=` (e.g. `section=gaps`)
    - `json`: the data behind the report
    """
    project = _project(db, project_id)
    data = report.report_data(db, project)  # sync endpoint: FastAPI runs it in a worker thread
    if format == "json":
        return data
    if format == "csv":
        if section:
            try:
                body = report.to_csv(data, section).encode("utf-8-sig")  # BOM: opens cleanly in Excel
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            name = _filename(data, "csv").replace(".csv", f"-{section}.csv")
            return Response(body, media_type="text/csv; charset=utf-8", headers=_attachment(name))
        return Response(
            report.csv_zip(data), media_type="application/zip", headers=_attachment(_filename(data, "zip"))
        )
    html = report.render_html(data)
    if format == "pdf":
        try:
            pdf = report.render_pdf(html)
        except report.ReportUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc
        return Response(pdf, media_type="application/pdf", headers=_attachment(_filename(data, "pdf")))
    return HTMLResponse(html, headers=_attachment(_filename(data, "html")) if download else {})


def _attachment(name: str) -> dict:
    return {"Content-Disposition": f'attachment; filename="{name}"'}
