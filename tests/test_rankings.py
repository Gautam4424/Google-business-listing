import base64
import uuid

import httpx
import pytest

from app.models import (
    AuditJob,
    Business,
    BusinessLocation,
    GbpProfile,
    Keyword,
    Project,
    RankingResult,
    RankingRun,
)
from app.providers.serpapi import SerpApiClient
from app.services import rankings
from app.workers.pipeline import run_job

CLIENT_PID, CLIENT_CID = "ChIJclient", "1111"

PACK = {"local_results": {"places": [
    {"position": 1, "title": "Rival Plumbing", "place_id": "2222", "rating": 4.8, "reviews": 300,
     "type": "Plumber", "address": "1 Rival St"},
    {"position": 2, "title": "Proximity Plumbing", "place_id": CLIENT_CID, "rating": 4.9, "reviews": 2828,
     "type": "Plumber", "links": {"website": "https://proximityplumbing.com.au/"}},
    {"position": 3, "title": "Third Plumbing", "place_id": "3333"},
]}}  # fmt: skip
MAPS = {"local_results": [
    {"position": 1, "title": "Rival Plumbing", "place_id": "ChIJrival", "data_cid": "2222", "rating": 4.8,
     "reviews": 300, "type": "Plumber", "gps_coordinates": {"latitude": -33.87, "longitude": 151.25}},
    {"position": 2, "title": "Other Plumbing", "place_id": "ChIJother", "data_cid": "4444"},
    {"position": 3, "title": "Proximity Plumbing Pty Ltd", "place_id": CLIENT_PID, "data_cid": CLIENT_CID},
]}  # fmt: skip
LOCATIONS = [
    {"canonical_name": "Point Piper,New South Wales,Australia", "country_code": "AU", "reach": 5000},
    {"canonical_name": "Point Piper,Somewhere,United States", "country_code": "US", "reach": 99999},
]


@pytest.fixture
def setup(db, monkeypatch):
    business = Business(place_id=CLIENT_PID, name="Proximity Plumbing", source="google_places",
                        is_client=True, domain="proximityplumbing.com.au")  # fmt: skip
    db.add(business)
    db.flush()
    db.add(
        GbpProfile(
            business_id=business.id,
            place_id=CLIENT_PID,
            business_name="Proximity Plumbing",
            maps_url=f"https://maps.google.com/?cid={CLIENT_CID}&g_mp=x",
            phone_number="+61 420 102 394",
            formatted_address="2A Wentworth St, Point Piper NSW 2027",
            source="google_places",
        )
    )
    db.add(
        BusinessLocation(business_id=business.id, latitude=-33.867, longitude=151.25, source="google_places")
    )
    project = Project(name="P", input_business_name="Proximity Plumbing", country="AU", language="en",
                      service_areas=[{"name": "Point Piper, NSW", "latitude": -33.867, "longitude": 151.25}],
                      client_business=business)  # fmt: skip
    db.add(project)
    db.flush()
    for text in ("plumber in Point Piper", "blocked drains near me"):
        db.add(
            Keyword(
                project_id=project.id,
                keyword=text,
                location_name="Point Piper, NSW",
                latitude=-33.867,
                longitude=151.25,
                language="en",
                country="AU",
                device="mobile",
                active=True,
            )
        )
    db.add(
        Keyword(
            project_id=project.id,
            keyword="inactive one",
            location_name="Point Piper, NSW",
            language="en",
            country="AU",
            device="mobile",
            active=False,
        )
    )
    db.commit()

    calls = []

    def handler(fail_maps=False, searches_left=246):
        def h(request: httpx.Request) -> httpx.Response:
            path, p = request.url.path, request.url.params
            if path == "/account.json":
                return httpx.Response(200, json={"total_searches_left": searches_left,
                                                 "plan_renewal_date": "2026-11-06"})  # fmt: skip
            if path == "/locations.json":
                return httpx.Response(200, json=LOCATIONS)
            calls.append(dict(p))
            if p["engine"] == "google":
                return httpx.Response(200, json=PACK)
            if fail_maps:
                return httpx.Response(500, json={"error": "Google hiccup"})
            return httpx.Response(200, json=MAPS)

        return h

    def use(**kw):
        monkeypatch.setattr(
            rankings, "get_serpapi_client",
            lambda: SerpApiClient("test-serpapi-key", transport=httpx.MockTransport(handler(**kw))),
        )  # fmt: skip

    return project, use, calls


def _job(db, project, **params):
    job = AuditJob(job_type="ranking_check", project_id=project.id, params=params, status="queued")
    db.add(job)
    db.commit()
    return job


def test_points_and_parsing():
    assert [rankings.finder_points(r) for r in (1, 3, 4, 10, 11, 20, 21, None)] == [
        40,
        40,
        20,
        20,
        10,
        10,
        0,
        0,
    ]
    shown, rows = rankings.parse_local_pack(PACK)
    assert shown and rows[1]["cid"] == CLIENT_CID and rows[1]["place_id"] is None
    assert rows[1]["website_url"] == "https://proximityplumbing.com.au/"
    maps = rankings.parse_maps(MAPS)
    assert maps[0]["place_id"] == "ChIJrival" and maps[0]["cid"] == "2222" and maps[0]["latitude"] == -33.87
    assert rankings.parse_local_pack({})[0] is False  # Google showed no Local Pack
    assert rankings.parse_maps({"place_results": {"title": "Only one", "place_id": "ChIJx"}})[0]["rank"] == 1


def test_full_check_end_to_end(db, setup):
    project, use, calls = setup
    use()
    job = run_job(db, _job(db, project, mode="full").id)
    assert job.status == "completed", job.steps
    assert len(calls) == 4  # 2 active keywords x (Local Pack + Local Finder); the inactive one costs nothing
    pack_call = next(c for c in calls if c["engine"] == "google")
    decoded = base64.b64decode(pack_call["uule"][2:]).decode()
    assert "latitude_e7:-338670000" in decoded and pack_call["device"] == "mobile"  # searched from the suburb
    maps_call = next(c for c in calls if c["engine"] == "google_maps")
    assert maps_call["ll"].startswith("@-33.867000,151.250000,")

    report = rankings.check_report(db, project, job, None)
    row = report["keywords"][0]
    assert (row["local_pack_rank"], row["local_finder_rank"]) == (2, 3)  # client found by CID and place_id
    assert row["points"] == 70 + 40
    s = report["summary"]
    assert s["visibility_score"] == round(110 / 140 * 100, 1)
    assert (s["in_local_pack"], s["in_local_finder"], s["top_3"], s["not_found"]) == (2, 2, 2, 0)
    run = db.query(RankingRun).filter_by(audit_job_id=job.id, result_type="local_pack").first()
    assert run.pack_shown and run.search_location and run.country == "AU" and run.device == "mobile"
    assert db.query(RankingResult).count() == 12  # every business stored (3 per search)
    assert {u["sku"]: u["month_count"] for u in _usage(db)}["serpapi_search"] == 4


def _usage(db):
    from app.services.quota import usage_report

    return [{"sku": u.sku, "month_count": u.month_count} for u in usage_report(db)]


def test_recheck_within_24h_is_free_and_shows_change(db, setup):
    project, use, calls = setup
    use()
    run_job(db, _job(db, project, mode="full").id)
    est = rankings.estimate(db, project, "full")
    assert est["searches_needed"] == 0 and est["searches_from_cache"] == 4
    second = run_job(db, _job(db, project, mode="full").id)
    assert len(calls) == 4  # nothing new spent
    assert db.query(RankingRun).filter_by(audit_job_id=second.id, from_cache=True).count() == 4
    first = rankings.ranking_checks(db, project)[1]
    report = rankings.check_report(db, project, second, first)
    assert report["summary"]["visibility_change"] == 0.0
    assert report["keywords"][0]["previous_local_pack_rank"] == 2


def test_named_location_when_no_coordinates(db, setup):
    project, use, calls = setup
    use()
    for k in db.query(Keyword).filter_by(project_id=project.id, active=True):
        k.latitude = k.longitude = None
    db.commit()
    run_job(db, _job(db, project, mode="full").id)
    pack_call = next(c for c in calls if c["engine"] == "google")
    assert pack_call["location"] == "Point Piper,New South Wales,Australia" and "uule" not in pack_call


def test_change_only_compares_keywords_checked_both_times(db, setup):
    project, use, _ = setup
    use()
    first = run_job(db, _job(db, project, mode="full").id)
    extra = Keyword(
        project_id=project.id,
        keyword="new keyword",
        location_name="Point Piper, NSW",
        latitude=-33.867,
        longitude=151.25,
        language="en",
        country="AU",
        device="mobile",
    )
    db.add(extra)
    db.commit()
    second = run_job(db, _job(db, project, mode="full").id)
    s = rankings.check_report(db, project, second, first)["summary"]
    assert s["keywords"] == 3 and s["compared_keywords"] == 2
    assert s["visibility_change"] == 0.0  # same 2 keywords, same ranks: no fake change from the new one


def test_maps_only_mode_estimates_pack(db, setup):
    project, use, calls = setup
    use()
    job = run_job(db, _job(db, project, mode="maps_only").id)
    assert job.status == "completed"
    assert {c["engine"] for c in calls} == {"google_maps"} and len(calls) == 2  # 1 per keyword
    row = rankings.check_report(db, project, job, None)["keywords"][0]
    assert row["local_pack_estimated"] and row["local_pack_rank"] == 3  # Maps rank 3 -> estimated pack 3


def test_partial_success_when_some_searches_fail(db, setup):
    project, use, _ = setup
    use(fail_maps=True)
    job = run_job(db, _job(db, project, mode="full").id)
    assert job.status == "partial_success"
    step = next(s for s in job.steps if s["name"] == "collect_rankings")
    assert step["status"] == "partial" and step["result"]["failed"] == 2
    assert "test-serpapi-key" not in str(job.steps)
    assert {u["sku"]: u["month_count"] for u in _usage(db)}["serpapi_search"] == 2  # failed searches refunded


def test_not_enough_credits_is_refused(client, db, setup, enqueued):
    project, use, calls = setup
    use(searches_left=3)
    r = client.post(f"/v1/projects/{project.id}/rankings/run", json={"mode": "full"})
    assert (
        r.status_code == 422
        and "needs 4, 3 left" in r.json()["detail"]
        and "2026-11-06" in r.json()["detail"]
    )
    assert calls == [] and enqueued == []
    ok = client.post(f"/v1/projects/{project.id}/rankings/run", json={"mode": "maps_only"})
    assert ok.status_code == 202  # 2 searches fit


def test_endpoints(client, db, setup, enqueued):
    project, use, _ = setup
    use()
    assert client.get(f"/v1/projects/{project.id}/rankings").json() == {
        "latest": None, "history": [], "top_businesses": []
    }  # fmt: skip
    est = client.get(f"/v1/projects/{project.id}/rankings/estimate").json()
    assert est["searches_needed"] == 4 and est["renews_on"] == "2026-11-06"
    r = client.post(f"/v1/projects/{project.id}/rankings/run")
    assert r.status_code == 202
    run_job(db, uuid.UUID(r.json()["id"]))
    data = client.get(f"/v1/projects/{project.id}/rankings").json()
    assert data["latest"]["summary"]["visibility_score"] > 0
    assert data["history"][0]["visibility_score"] == data["latest"]["summary"]["visibility_score"]
    top = data["top_businesses"]
    assert top[0]["appearances"] >= 3 and any(b["is_client"] for b in top)
