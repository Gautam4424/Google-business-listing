import io
import json
import uuid
import zipfile

import httpx

from app.models import AuditJob, RankingRun
from app.providers.serpapi import SerpApiClient
from app.services import rankings, report
from app.workers.pipeline import run_job
from tests.test_audit import _project_with_place, audit_env  # noqa: F401  (fixture)
from tests.test_competitors import _check, setup  # noqa: F401  (fixture)


def _full_audit(client, db, enqueued, project_id, **body):
    r = client.post(f"/v1/projects/{project_id}/full-audit", json=body or None)
    assert r.status_code == 202, r.text
    job = run_job(db, enqueued[-1])
    db.expire_all()
    return job


def _steps(job):
    return {s["name"]: s for s in job.steps}


def test_full_audit_without_rankings_uses_no_serpapi(client, db, enqueued, audit_env):  # noqa: F811
    serp_calls = []
    audit_env(lambda r: serp_calls.append(r) or httpx.Response(500))
    project = _project_with_place(client)
    job = _full_audit(client, db, enqueued, project["id"], rankings=False)
    assert job.job_type == "full_audit" and job.status == "completed", json.dumps(job.steps, default=str)
    steps = _steps(job)
    assert steps["fetch_profile"]["status"] == "succeeded" and steps["crawl_website"]["status"] == "succeeded"
    for name in ("check_budget", "collect_rankings", "compute_visibility", "find_competitors"):
        assert steps[name]["status"] == "skipped", name
    assert steps["build_report"]["result"]["pdf"].endswith("format=pdf")
    assert serp_calls == []


def test_full_audit_never_searches_without_a_passed_credit_check(
    client,
    db,
    enqueued,
    audit_env,  # noqa: F811
    monkeypatch,
):
    audit_env(lambda r: httpx.Response(500))
    searches = []

    def serp(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/account.json":
            return httpx.Response(200, json={"total_searches_left": 0, "plan_renewal_date": "2026-11-06"})
        searches.append(request)
        return httpx.Response(500)

    monkeypatch.setattr(rankings, "get_serpapi_client",
                        lambda: SerpApiClient("k", transport=httpx.MockTransport(serp)))  # fmt: skip
    project = _project_with_place(client)
    job = _full_audit(client, db, enqueued, project["id"])
    steps = _steps(job)
    assert steps["generate_keywords"]["status"] == "succeeded"
    assert steps["check_budget"]["status"] == "failed" and "0 searches left" in steps["check_budget"]["error"]
    assert steps["collect_rankings"]["status"] == "skipped" and searches == []
    assert job.status == "partial_success"  # optional step failed: the audit itself is kept
    assert client.get(f"/v1/projects/{project['id']}/profile").json()["profile"]["review_count"] == 57


def test_full_audit_rankings_count_as_a_ranking_check(db, setup):  # noqa: F811
    project, _, _ = setup
    job = AuditJob(job_type="full_audit", project_id=project.id, params={}, status="completed")
    other = AuditJob(job_type="full_audit", project_id=project.id, params={}, status="completed")
    db.add_all([job, other])
    db.flush()
    kw = project_keyword(db, project)
    db.add(RankingRun(project_id=project.id, keyword_id=kw, audit_job_id=job.id, provider="serpapi",
                      result_type="local_finder", country="AU", language="en", device="mobile"))  # fmt: skip
    db.commit()
    assert [j.id for j in rankings.ranking_checks(db, project)] == [job.id]  # the one without rankings is not


def project_keyword(db, project):
    from app.models import Keyword

    return db.query(Keyword).filter_by(project_id=project.id).first().id


def test_report_formats(client, db, enqueued, audit_env, monkeypatch):  # noqa: F811
    audit_env(lambda r: httpx.Response(500))
    project = _project_with_place(client)
    _full_audit(client, db, enqueued, project["id"], rankings=False)
    base = f"/v1/projects/{project['id']}/report"

    html = client.get(base)
    assert html.status_code == 200 and html.headers["content-type"].startswith("text/html")
    body = html.text
    assert "Acme Build" in body and "Google Business Profile" in body
    assert "Author 1" in body and "https://maps.google.com/u" in body  # review attribution kept
    assert "Sources" in body and "Places API" in body
    assert "<script" not in body  # self-contained, no scripts

    z = zipfile.ZipFile(io.BytesIO(client.get(f"{base}?format=csv").content))
    assert sorted(z.namelist()) == sorted(f"{s}.csv" for s in report.CSV_SECTIONS)
    reviews = z.read("reviews.csv").decode("utf-8-sig").splitlines()
    assert reviews[0].startswith("author,rating") and len(reviews) == 6  # header + 5 reviews

    one = client.get(f"{base}?format=csv&section=profile")
    assert one.headers["content-type"].startswith("text/csv") and "review_count,57" in one.text
    assert client.get(f"{base}?format=csv&section=nope").status_code == 422
    assert client.get(f"{base}?format=json").json()["profile"]["business_name"] == "Acme Build"

    monkeypatch.setattr(report, "render_pdf", lambda html: b"%PDF-1.7 test")
    pdf = client.get(f"{base}?format=pdf")
    assert pdf.content.startswith(b"%PDF") and "attachment" in pdf.headers["content-disposition"]
    assert "acme-build" in pdf.headers["content-disposition"]

    def no_browser(html):
        raise report.ReportUnavailable("PDF needs the browser in the image")

    monkeypatch.setattr(report, "render_pdf", no_browser)
    assert client.get(f"{base}?format=pdf").status_code == 503


def test_report_includes_rankings_competitors_and_gaps(client, db, setup):  # noqa: F811
    project, _, _ = setup
    _check(db, project)
    body = client.get(f"/v1/projects/{project.id}/report").text
    assert "Visibility score" in body and "plumber in Point Piper" in body
    assert "Rival Plumbing" in body
    assert "Review whether &#39;Drainage service&#39; is an accurate and eligible" in body
    assert "Top priorities" in body
    data = client.get(f"/v1/projects/{project.id}/report?format=json").json()
    assert data["rankings"]["summary"]["keywords"] == 3 and data["gaps"]


def test_unknown_project_report_is_404(client):
    assert client.get(f"/v1/projects/{uuid.uuid4()}/report").status_code == 404
