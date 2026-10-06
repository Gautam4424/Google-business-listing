import httpx
import pytest

from app.models import AuditJob, DataSource
from app.providers.google_places import GooglePlacesClient
from app.providers.serpapi import SerpApiClient
from app.workers.pipeline import PIPELINES, Step, StepSkipped, run_job


def _places_ok(request: httpx.Request) -> httpx.Response:
    assert request.headers["X-Goog-Api-Key"] == "test-google-key"
    return httpx.Response(200, json={"places": [{"id": "ChIJtest", "displayName": {"text": "Googleplex"}}]})


def _serpapi_ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200, json={"plan_name": "Free Plan", "searches_per_month": 250, "plan_searches_left": 250}
    )


def _serpapi_invalid(request: httpx.Request) -> httpx.Response:
    return httpx.Response(401, json={"error": f"Invalid API key {request.url.params['api_key']}"})


@pytest.fixture
def providers(monkeypatch):
    def use(places=_places_ok, serpapi=_serpapi_ok):
        monkeypatch.setattr(
            "app.workers.jobs.diagnostic.get_places_client",
            lambda: GooglePlacesClient("test-google-key", transport=httpx.MockTransport(places)),
        )
        monkeypatch.setattr(
            "app.workers.jobs.diagnostic.get_serpapi_client",
            lambda: SerpApiClient("test-serpapi-key", transport=httpx.MockTransport(serpapi)),
        )

    return use


def _new_job(db, job_type="diagnostic") -> AuditJob:
    job = AuditJob(job_type=job_type, params={}, status="queued")
    db.add(job)
    db.commit()
    return job


def test_diagnostic_completed(db, providers):
    providers()
    job = run_job(db, _new_job(db).id)
    assert job.status == "completed", job.steps
    assert [s["status"] for s in job.steps] == ["succeeded"] * 3
    assert job.steps[1]["result"]["place_id"] == "ChIJtest"
    assert job.steps[2]["result"]["plan_name"] == "Free Plan"
    assert job.started_at and job.finished_at
    assert db.query(DataSource).count() == 2  # raw responses kept for provenance


def test_optional_failure_gives_partial_success_and_hides_key(db, providers):
    providers(serpapi=_serpapi_invalid)
    job = run_job(db, _new_job(db).id)
    assert job.status == "partial_success"
    serp = job.steps[2]
    assert serp["status"] == "failed"
    assert "Invalid API key" in serp["error"]
    assert "test-serpapi-key" not in serp["error"]  # redacted


def test_missing_key_is_skipped_not_failed(db, monkeypatch, providers):
    providers()
    monkeypatch.setattr("app.workers.jobs.diagnostic.get_serpapi_client", lambda: None)
    job = run_job(db, _new_job(db).id)
    assert job.status == "completed"
    assert job.steps[2]["status"] == "skipped"


def test_required_failure_fails_job_and_skips_rest(db, monkeypatch):
    def broken(ctx):
        raise RuntimeError("db exploded")

    def never(ctx):
        raise AssertionError("must not run")

    monkeypatch.setitem(
        PIPELINES,
        "test_required",
        [Step("first", broken, required=True), Step("second", never, required=False)],
    )
    job = run_job(db, _new_job(db, "test_required").id)
    assert job.status == "failed"
    assert job.error == "RuntimeError: db exploded"
    assert [s["status"] for s in job.steps] == ["failed", "skipped"]


def test_step_results_are_shared(db, monkeypatch):
    def a(ctx):
        return {"value": 2}

    def b(ctx):
        if "a" not in ctx.results:
            raise StepSkipped("no a")
        return {"double": ctx.results["a"]["value"] * 2}

    monkeypatch.setitem(
        PIPELINES,
        "test_chain",
        [Step("a", a), Step("b", b)],
    )
    job = run_job(db, _new_job(db, "test_chain").id)
    assert job.steps[1]["result"] == {"double": 4}


def test_job_not_rerun(db, providers):
    providers()
    job = run_job(db, _new_job(db).id)
    finished = job.finished_at
    assert run_job(db, job.id).finished_at == finished
