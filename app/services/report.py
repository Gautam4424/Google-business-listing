"""Phase 9: the audit report (brief §4 `GET /report`) as HTML, PDF and CSV.

Built only from data already stored: no API calls, no credits. Every section names its source, and Google
content keeps its attribution (review authors + links, "Google" as the source of profile data).
PDF is printed by the headless Chromium already in the image (no extra libraries).
"""

import csv
import io
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import GapRecommendation, Keyword, Project
from app.services import competitors as competitors_service
from app.services import rankings as rankings_service
from app.services.gaps import PRIORITY_ORDER, TYPE_ORDER, gap_out
from app.services.service_catalog import services_by_kind

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
CSV_SECTIONS = ("profile", "nap_check", "reviews", "keywords", "rankings", "results", "competitors", "gaps")


class ReportUnavailable(RuntimeError):
    pass


def _env() -> Environment:
    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]))
    env.filters["num"] = lambda v: "—" if v is None else f"{v:,}" if isinstance(v, int) else v
    env.filters["date"] = lambda v: _date(v)
    env.filters["pct"] = lambda v: "—" if v is None else f"{round(v * 100)}%"
    return env


def _date(value) -> str:
    if not value:
        return "—"
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return value
    return value.strftime("%d %b %Y")


def report_data(db: Session, project: Project) -> dict:
    from app.api.v1.projects import get_profile  # the same data the project page shows

    profile = get_profile(project.id, db).model_dump(mode="json")
    kinds = services_by_kind(db, project)
    keywords = db.scalars(
        select(Keyword)
        .where(Keyword.project_id == project.id)
        .order_by(Keyword.active.desc(), Keyword.keyword)
    ).all()
    checks = rankings_service.ranking_checks(db, project)
    ranking = None
    history = []
    if checks:
        ranking = rankings_service.check_report(
            db, project, checks[0], checks[1] if len(checks) > 1 else None
        )
        for i, job in enumerate(checks):
            prev = checks[i + 1] if i + 1 < len(checks) else None
            s = rankings_service.check_report(db, project, job, prev)["summary"]
            history.append(
                {"checked_at": job.created_at.isoformat(), "visibility_score": s["visibility_score"]}
            )
    comp = competitors_service.competitors_report(db, project)
    gap_rows = db.scalars(select(GapRecommendation).where(GapRecommendation.project_id == project.id)).all()
    gaps = sorted(
        (gap_out(g) for g in gap_rows),
        key=lambda g: (PRIORITY_ORDER.get(g["priority"], 9), TYPE_ORDER.get(g["gap_type"], 9)),
    )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "project": profile["project"],
        "profile": profile["profile"],
        "website": profile["website"],
        "nap_check": profile["nap_check"],
        "location": profile["location"],
        "social_profiles": profile["social_profiles"],
        "reviews": profile["reviews"],
        "review_summary": profile["review_summary"],
        "last_audit": profile["last_audit"],
        "services": {
            "core": [s.name for s in kinds.get("service", []) if s.selected],
            "other": [s.name for s in kinds.get("service", []) if not s.selected],
            "customer_types": [s.name for s in kinds.get("customer_type", [])],
        },
        "keywords": [
            {
                "keyword": k.keyword,
                "service": k.service,
                "location_name": k.location_name,
                "pattern": k.pattern,
                "active": k.active,
            }
            for k in keywords
        ],  # fmt: skip
        "rankings": ranking,
        "ranking_history": history,
        # every business found in the latest check: Local Pack top 3 + Local Finder top 20 per keyword
        "ranking_results": rankings_service.all_results(db, checks[0]) if checks else [],
        "competitors": comp,
        "gaps": gaps,
    }


def summary_tiles(data: dict) -> list[dict]:
    p, r, comp = data["profile"] or {}, data["rankings"], data["competitors"]
    conf = data["project"].get("match_confidence")
    return [
        {"label": "Listing match", "value": f"{round(conf * 100)}%" if conf is not None else "—"},
        {"label": "Google rating", "value": f"{p['rating']} ★" if p.get("rating") is not None else "—"},
        {"label": "Reviews", "value": f"{p['review_count']:,}" if p.get("review_count") is not None else "—"},
        {"label": "Visibility score", "value": f"{r['summary']['visibility_score']}/100" if r else "—"},
        {"label": "Competitors", "value": str(len(comp["competitors"])) if comp.get("analysis") else "—"},
        {"label": "Gaps to review", "value": str(len(data["gaps"]))},
    ]


def render_html(data: dict) -> str:
    return _env().get_template("report.html").render(d=data, tiles=summary_tiles(data))


def render_pdf(html: str) -> bytes:
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - depends on the image
        raise ReportUnavailable("PDF needs the browser in the image (INSTALL_BROWSER=true)") from exc
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page()
                page.route("**/*", lambda r: r.abort())  # the report is self-contained: no network at all
                page.set_content(html, wait_until="load")
                return page.pdf(
                    format="A4",
                    print_background=True,
                    margin={"top": "14mm", "bottom": "14mm", "left": "12mm", "right": "12mm"},
                )
            finally:
                browser.close()
    except PlaywrightError as exc:
        raise ReportUnavailable(f"PDF could not be created: {str(exc).splitlines()[0]}") from exc


# ---------- CSV ----------


def csv_rows(data: dict, section: str) -> tuple[list[str], list[list]]:
    p = data["profile"] or {}
    if section == "profile":
        fields = ["business_name", "formatted_address", "phone_number", "website_url", "primary_category",
                  "secondary_categories", "rating", "review_count", "business_status", "map_pin_status",
                  "plus_code", "maps_url", "place_id", "last_checked_at"]  # fmt: skip
        return ["field", "value"], [[f, _cell(p.get(f))] for f in fields]
    if section == "nap_check":
        rows = [[k, v.get("google"), v.get("website"), v.get("status"), v.get("detail")]
                for k, v in (data["nap_check"] or {}).items()]  # fmt: skip
        return ["field", "google", "website", "status", "detail"], rows
    if section == "reviews":
        header = ["author", "rating", "published", "sentiment", "topics", "text", "owner_reply", "review_url"]
        return header, [
            [r["author_name"], r["rating"], r["published_at"] or r["relative_publish_time"], r["sentiment"],
             "; ".join(f"{t['tag']} ({t['sentiment']})" for t in r["tags"]), r["review_text"],
             r["owner_reply"], r["review_url"]]
            for r in data["reviews"]
        ]  # fmt: skip
    if section == "keywords":
        return ["keyword", "service", "area", "type", "active"], [
            [k["keyword"], k["service"], k["location_name"], k["pattern"], k["active"]]
            for k in data["keywords"]
        ]
    if section == "results":
        cols = ["keyword", "result_type", "rank", "business_name", "is_client", "category", "rating",
                "review_count", "address", "phone", "website_url", "maps_url", "search_from"]  # fmt: skip
        return cols, [[r.get(c) for c in cols] for r in data.get("ranking_results", [])]
    if section == "rankings":
        rows = (data["rankings"] or {}).get("keywords", [])
        return ["keyword", "area", "local_pack_rank", "local_pack_estimated", "local_finder_rank", "points",
                "visibility", "previous_local_pack_rank", "previous_local_finder_rank"], [
            [r["keyword"], r["location_name"], r["local_pack_rank"], r["local_pack_estimated"],
             r["local_finder_rank"], r["points"], r["visibility"], r.get("previous_local_pack_rank"),
             r.get("previous_local_finder_rank")]
            for r in rows
        ]  # fmt: skip
    if section == "competitors":
        return ["business_name", "primary_category", "categories", "rating", "review_count",
                "review_velocity_30d", "website_domain", "keyword_share", "local_pack_count",
                "local_finder_count", "keyword_overlap", "reason", "maps_url"], [
            [c["business_name"], c["primary_category"], "; ".join(c["categories"]), c["rating"],
             c["review_count"], c["review_velocity_30d"], c["website_domain"], c["keyword_share"],
             c["local_pack_count"], c["local_finder_count"], c["keyword_overlap"], c["reason"], c["maps_url"]]
            for c in data["competitors"]["competitors"]
        ]  # fmt: skip
    if section == "gaps":
        return ["priority", "gap_type", "title", "recommendation", "client_value", "competitor_pattern"], [
            [g["priority"], g["gap_type"], g["title"], g["recommendation"], _cell(g["client_value"]),
             _cell(g["competitor_pattern"])]
            for g in data["gaps"]
        ]  # fmt: skip
    raise ValueError(f"Unknown CSV section '{section}'. Use one of: {', '.join(CSV_SECTIONS)}")


def _cell(value) -> str | int | float | None:
    if isinstance(value, list):
        return "; ".join(str(v) for v in value)
    if isinstance(value, dict):
        return "; ".join(f"{k}: {_cell(v)}" for k, v in value.items())
    return value


def to_csv(data: dict, section: str) -> str:
    header, rows = csv_rows(data, section)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    writer.writerows([[_cell(c) for c in row] for row in rows])
    return buf.getvalue()


def csv_zip(data: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for section in CSV_SECTIONS:
            z.writestr(f"{section}.csv", to_csv(data, section).encode("utf-8-sig"))  # BOM: opens in Excel
    return buf.getvalue()
