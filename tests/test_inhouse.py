"""Phase 10 (in-house): no double runs, interrupted jobs, retries, limit messages, delete, settings,
30-day clean-up."""

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.models import (
    AuditJob,
    Business,
    DataSource,
    GbpProfile,
    GbpReview,
    Project,
    ReviewTag,
)
from app.models.base import utcnow
from app.providers.google_places import GooglePlacesClient
from app.providers.serpapi import SerpApiClient
from app.services import jobs as jobs_service
from app.services import quota, retention
from app.workers.pipeline import run_job
from tests.test_audit import _project_with_place, audit_env  # noqa: F401  (fixture)


def _project(db, name="P"):
    p = Project(name=name, input_business_name="Biz", country="AU", language="en")
    db.add(p)
    db.commit()
    return p


# ---------- no double runs ----------


def test_second_job_for_the_same_project_is_refused(client, db, enqueued):
    p = _project(db)
    assert client.post(f"/v1/projects/{p.id}/website-discovery").status_code == 422  # no website: unrelated
    first = client.post("/v1/jobs", json={"job_type": "review_analysis", "project_id": str(p.id)})
    assert first.status_code == 202
    again = client.post(f"/v1/projects/{p.id}/full-audit", json={"rankings": False})
    assert again.status_code == 409 and "already queued" in again.json()["detail"]
    other = _project(db, "Other")  # other projects are not blocked
    assert (
        client.post("/v1/jobs", json={"job_type": "review_analysis", "project_id": str(other.id)}).status_code
        == 202
    )
    assert len(enqueued) == 2


def test_chained_job_and_stale_jobs_are_allowed(db, enqueued):
    p = _project(db)
    parent = jobs_service.start_job(db, "discover_business", p.id, {})
    jobs_service.start_job(db, "gbp_audit", p.id, {}, parent=parent)  # discover -> audit chain
    with pytest.raises(jobs_service.JobAlreadyRunning):
        jobs_service.start_job(db, "ranking_check", p.id, {})
    for j in db.query(AuditJob).filter_by(project_id=p.id):
        j.created_at = utcnow() - timedelta(hours=5)  # older than JOB_STALE_MINUTES
    db.commit()
    assert jobs_service.start_job(db, "ranking_check", p.id, {}).status == "queued"


# ---------- interrupted jobs ----------


def test_interrupted_and_lost_jobs_are_marked_failed(db):
    p = _project(db)
    running = AuditJob(job_type="gbp_audit", project_id=p.id, params={}, status="running",
                       steps=[{"name": "fetch_profile", "status": "running"}])  # fmt: skip
    lost = AuditJob(job_type="gbp_audit", project_id=p.id, params={}, status="queued",
                    created_at=utcnow() - timedelta(hours=3))  # fmt: skip
    fresh = AuditJob(job_type="ranking_check", project_id=p.id, params={}, status="queued")
    db.add_all([running, lost, fresh])
    db.commit()
    assert jobs_service.recover_stale_jobs(db) == 1  # periodic check: only the 3-hour-old queued job
    assert lost.status == "failed" and "Never started" in lost.error
    assert jobs_service.recover_stale_jobs(db, worker_starting=True) == 1  # worker restart: running ones
    assert running.status == "failed" and running.error.startswith("Interrupted")
    assert running.steps[0]["status"] == "skipped"
    assert fresh.status == "queued"


# ---------- retries ----------


def _places(responses):
    calls = []

    def handler(request):
        calls.append(request)
        r = responses[min(len(calls), len(responses)) - 1]
        if isinstance(r, Exception):
            raise r
        return r

    return GooglePlacesClient("k", transport=httpx.MockTransport(handler)), calls


def test_one_retry_on_temporary_errors_only():
    client, calls = _places([httpx.Response(503), httpx.Response(200, json={"id": "x"})])
    assert client.place_details("x", "id") == {"id": "x"} and len(calls) == 2

    client, calls = _places([httpx.Response(403, json={"error": {"message": "denied"}})])
    with pytest.raises(Exception, match="denied"):
        client.place_details("x", "id")
    assert len(calls) == 1  # a rejected key is never retried

    timeout = httpx.ReadTimeout("slow")
    client, calls = _places([timeout, timeout, httpx.Response(200, json={})])
    with pytest.raises(httpx.ReadTimeout):
        client.place_details("x", "id")
    assert len(calls) == 2  # max 1 retry

    serp_calls = []

    def serp(request):
        serp_calls.append(request)
        return httpx.Response(429 if len(serp_calls) == 1 else 200, json={"ok": 1})

    assert SerpApiClient("k", transport=httpx.MockTransport(serp)).search({"q": "x"}) == {"ok": 1}
    assert len(serp_calls) == 2


# ---------- clear limit messages ----------


def test_limit_messages_say_when_they_reset(client, db, monkeypatch):
    monkeypatch.setattr(quota.get_settings(), "quota_serpapi_daily", 2)
    quota.consume(db, "serpapi_search", 2)
    with pytest.raises(quota.QuotaExceeded) as exc:
        quota.consume(db, "serpapi_search")
    msg = str(exc.value)
    assert msg.startswith("SerpApi searches: daily limit reached (2/2)")
    assert "Resets at 00:00 UTC, in" in msg and "QUOTA_SERPAPI_DAILY" in msg
    serp = next(u for u in client.get("/v1/usage").json() if u["sku"] == "serpapi_search")
    assert serp["blocked"].startswith("SerpApi searches: daily limit reached")
    noon = datetime(2026, 10, 7, 12, 30, tzinfo=UTC)
    assert "in 11 h 30 min" in quota.limit_message("serpapi_search", "daily", 2, 2, noon)
    assert "Resets on 01 Nov" in quota.limit_message("google_places_details", "monthly", 900, 900, noon)


# ---------- delete project ----------


def test_delete_project_removes_everything(client, db, enqueued, audit_env):  # noqa: F811
    audit_env(lambda r: httpx.Response(500))
    project = _project_with_place(client)
    client.post(f"/v1/projects/{project['id']}/gbp-audit")
    assert client.delete(f"/v1/projects/{project['id']}").status_code == 409  # audit still queued
    run_job(db, enqueued[-1])
    assert db.query(GbpReview).count() == 5
    r = client.delete(f"/v1/projects/{project['id']}")
    assert (
        r.status_code == 200 and r.json()["deleted"] == "Acme audit" and r.json()["google_business_removed"]
    )
    db.expire_all()
    assert db.get(Project, uuid.UUID(project["id"])) is None
    assert db.query(GbpReview).count() == 0 and db.query(GbpProfile).count() == 0
    assert db.query(Business).count() == 0 and db.query(AuditJob).count() == 0
    assert client.get(f"/v1/projects/{project['id']}").status_code == 404


# ---------- settings page ----------


def test_settings_mask_keys_and_show_limits(client):
    data = client.get("/v1/settings").json()
    assert data["keys"]["GOOGLE_API_KEY"] == "test…ey" and data["keys"]["SERPAPI_KEY"] == "test…ey"
    assert "test-google-key" not in str(data) and "test-serpapi-key" not in str(data)
    labels = {limit["sku"]: limit for limit in data["limits"]}
    assert labels["serpapi_search"]["daily_setting"] == "QUOTA_SERPAPI_DAILY"
    assert data["app"]["debug"] is False and data["retention"]["GOOGLE_DATA_TTL_DAYS"] == 30
    assert data["worker_online"] in (True, False, None)  # None = queue unreachable (answers, never hangs)


# ---------- 30-day clean-up ----------


def test_cleanup_removes_old_google_content_and_keeps_results(db):
    now = utcnow()
    old, recent = now - timedelta(days=40), now - timedelta(days=2)
    db.add_all([
        DataSource(provider="google_places", endpoint="places:get", request_hash="a", fetched_at=old),
        DataSource(provider="serpapi", endpoint="search", request_hash="b", fetched_at=recent,
                   expires_at=now + timedelta(days=20)),
    ])  # fmt: skip
    b = Business(place_id="ChIJx", name="Biz", source="google_places")
    db.add(b)
    db.flush()
    db.add_all([
        GbpProfile(business_id=b.id, place_id="ChIJx", business_name="Biz", formatted_address="1 St",
                   rating=4.5, review_count=100, source="google_places", created_at=old),
        GbpProfile(business_id=b.id, place_id="ChIJx", business_name="Biz", formatted_address="1 St",
                   rating=4.6, review_count=120, source="google_places", created_at=old + timedelta(days=1)),
    ])  # fmt: skip
    review = GbpReview(business_id=b.id, review_id="r", author_name="Ann", review_text="Fast and tidy",
                       rating=5, sentiment="positive", source="google_places", collected_at=old)  # fmt: skip
    db.add(review)
    db.flush()
    db.add(ReviewTag(review_id=review.id, tag="fast service", theme="speed", sentiment="positive",
                     sentence="Fast and tidy", source="t"))  # fmt: skip
    db.commit()

    result = retention.cleanup(db, now)
    assert result == {"older_than_days": 30, "raw_responses_deleted": 1, "review_texts_removed": 1,
                      "old_profile_snapshots_trimmed": 1}  # fmt: skip
    db.expire_all()
    assert db.query(DataSource).count() == 1
    r = db.query(GbpReview).one()
    assert r.review_text is None and r.author_name is None and r.rating == 5 and r.sentiment == "positive"
    tag = db.query(ReviewTag).one()
    assert tag.tag == "fast service" and tag.sentence is None  # topic kept, quoted text removed
    first, latest = db.query(GbpProfile).order_by(GbpProfile.created_at).all()
    assert first.formatted_address is None and first.review_count == 100 and first.place_id == "ChIJx"
    assert latest.formatted_address == "1 St"  # the latest snapshot stays until the next audit
    assert retention.cleanup(db, now)["review_texts_removed"] == 0  # nothing left to do


def test_cleanup_job_is_registered(db):
    job = AuditJob(job_type="retention_cleanup", params={}, status="queued")
    db.add(job)
    db.commit()
    job = run_job(db, job.id)
    assert job.status == "completed" and job.steps[0]["result"]["older_than_days"] == 30
