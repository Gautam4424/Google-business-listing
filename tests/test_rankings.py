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
                if p["q"] == "Australia":
                    return httpx.Response(200, json=[{"canonical_name": "Australia", "country_code": "AU",
                                                      "target_type": "Country", "reach": 10**7}])  # fmt: skip
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


def _places_centre(monkeypatch, lat, lng):
    """Google Places answers 'Point Piper, NSW' with the suburb's own point (a place, not a business)."""
    from app.providers.google_places import GooglePlacesClient

    body = {"places": [{"location": {"latitude": lat, "longitude": lng}, "types": ["locality", "political"],
                        "formattedAddress": "Point Piper NSW 2027, Australia"}]}  # fmt: skip
    monkeypatch.setattr(rankings, "get_places_client",
                        lambda: GooglePlacesClient("g", transport=httpx.MockTransport(
                            lambda r: httpx.Response(200, json=body))))  # fmt: skip


def _decoded_uule(call):
    return base64.b64decode(call["uule"][2:]).decode()


def test_search_from_city_centre(db, setup, monkeypatch):
    project, use, calls = setup
    use()
    _places_centre(monkeypatch, -33.8700, 151.2400)  # suburb centre, not the business pin (-33.867, 151.25)
    job = run_job(db, _job(db, project, mode="full", search_from="city").id)
    pack = next(c for c in calls if c["engine"] == "google")
    assert "latitude_e7:-338700000" in _decoded_uule(pack) and "longitude_e7:1512400000" in _decoded_uule(
        pack
    )
    maps = next(c for c in calls if c["engine"] == "google_maps")
    assert maps["ll"].startswith("@-33.870000,151.240000,")
    run = db.query(RankingRun).filter_by(audit_job_id=job.id).first()
    assert run.search_scope == "city" and "city centre" in run.search_location


def test_search_from_business_location(db, setup, monkeypatch):
    project, use, calls = setup
    use()
    _places_centre(monkeypatch, -33.8700, 151.2400)
    for k in db.query(Keyword).filter_by(project_id=project.id):  # the area's point is elsewhere
        k.latitude, k.longitude = -33.80, 151.10
    db.commit()
    run_job(db, _job(db, project, mode="full", search_from="business").id)
    pack = next(c for c in calls if c["engine"] == "google")
    assert "latitude_e7:-338670000" in _decoded_uule(pack)  # the business's own pin (-33.867, 151.25)


def test_search_from_whole_country(db, setup):
    project, use, calls = setup
    use()
    job = run_job(db, _job(db, project, mode="full", search_from="country").id)
    assert job.status == "completed", job.steps
    pack = next(c for c in calls if c["engine"] == "google")
    assert pack["location"] == "Australia" and "uule" not in pack
    finder = next(c for c in calls if c["engine"] != "google")
    assert finder["engine"] == "google_local" and finder["location"] == "Australia"  # Maps needs a point
    run = db.query(RankingRun).filter_by(audit_job_id=job.id).first()
    assert run.search_scope == "country" and run.latitude is None


def test_choice_is_remembered_and_changes_compare_like_for_like(client, db, setup, enqueued):
    project, use, _ = setup
    use()
    r = client.post(
        f"/v1/projects/{project.id}/rankings/run", json={"mode": "full", "search_from": "country"}
    )
    assert r.status_code == 202 and r.json()["params"]["search_from"] == "country"
    db.refresh(project)
    assert project.search_from == "country"
    assert client.get(f"/v1/projects/{project.id}/rankings/estimate").json()["search_from"] == "country"
    run_job(db, enqueued[-1])
    run_job(db, _job(db, project, mode="full", search_from="city").id)
    data = client.get(f"/v1/projects/{project.id}/rankings").json()
    assert data["latest"]["summary"]["search_from"] == "city"
    assert data["latest"]["summary"].get("visibility_change") is None  # no earlier city check to compare
    assert [h["search_from"] for h in data["history"]] == ["city", "country"]


def test_search_from_my_current_location(client, db, setup, enqueued):
    project, use, calls = setup
    use()
    url = f"/v1/projects/{project.id}/rankings"
    # needs the browser's location; never becomes the project's default
    assert client.post(f"{url}/run", json={"search_from": "current"}).status_code == 422
    assert (
        client.post(f"{url}/run", json={"search_from": "current", "here": {"lat": 120, "lng": 0}}).status_code
        == 422
    )
    est = client.get(f"{url}/estimate?search_from=current&lat=28.6692&lng=77.4538").json()
    assert est["search_from"] == "current" and est["search_points"] == [
        "your current location (28.6692, 77.4538)"
    ]
    r = client.post(f"{url}/run", json={"search_from": "current", "here": {"lat": 28.6692, "lng": 77.4538}})
    assert r.status_code == 202 and r.json()["params"]["here"] == {"lat": 28.6692, "lng": 77.4538}
    db.refresh(project)
    assert project.search_from == "city"
    run_job(db, enqueued[-1])
    pack = next(c for c in calls if c["engine"] == "google")
    assert "latitude_e7:286692000" in _decoded_uule(pack) and pack["gl"] == "au"  # business's country kept
    maps = next(c for c in calls if c["engine"] == "google_maps")
    assert maps["ll"].startswith("@28.669200,77.453800,")
    run = db.query(RankingRun).filter_by(audit_job_id=uuid.UUID(r.json()["id"])).first()
    assert run.search_scope == "current" and "your current location" in run.search_location


def test_links_to_check_results_by_hand(client, db, setup, monkeypatch):
    # SerpApi's own link (raw '+' in uule must be encoded) and its saved copy of the page
    url, copy = rankings.serpapi_links({"search_metadata": {
        "google_url": "https://www.google.com/search?q=plumber&uule=a+cm9sZTox==&hl=en&gl=au",
        "raw_html_file": "https://serpapi.com/searches/abc/123.html"}})  # fmt: skip
    assert url == "https://www.google.com/search?q=plumber&uule=a%2Bcm9sZTox%3D%3D&hl=en&gl=au"
    assert copy == "https://serpapi.com/searches/abc/123.html"
    maps_url, _ = rankings.serpapi_links(
        {"search_metadata": {"google_maps_url": "https://www.google.com/maps/x"}}
    )
    assert maps_url == "https://www.google.com/maps/x"

    project, _, _ = setup
    meta = {"google_url": "https://www.google.com/search?q=x&uule=a+AB",
            "raw_html_file": "https://serpapi.com/searches/s/1.html"}  # fmt: skip
    with_meta = {**PACK, "search_metadata": meta}
    calls_seen = []

    def serp(request):
        p = request.url.params
        if request.url.path == "/account.json":
            return httpx.Response(200, json={"total_searches_left": 99})
        calls_seen.append(p["engine"])
        return httpx.Response(200, json=with_meta if p["engine"] == "google" else MAPS)

    monkeypatch.setattr(rankings, "get_serpapi_client",
                        lambda: SerpApiClient("k", transport=httpx.MockTransport(serp)))  # fmt: skip
    run_job(db, _job(db, project, mode="full").id)
    kw = db.query(Keyword).filter_by(project_id=project.id, keyword="plumber in Point Piper").one()
    d = client.get(f"/v1/projects/{project.id}/rankings/keywords/{kw.id}").json()
    pack, finder = d["local_pack"], d["local_finder"]
    # proof = the same search as a plain Google link with the same point built in
    assert pack["proof_url"].startswith(
        "https://www.google.com/search?q=plumber+in+Point+Piper&gl=au&hl=en&uule=a%2B"
    )
    assert pack["snapshot_url"] == "https://serpapi.com/searches/s/1.html"
    assert finder["proof_url"].startswith(
        "https://www.google.com/maps/search/plumber+in+Point+Piper/@-33.867000,151.250000,"
    )
    assert finder["snapshot_url"] is None
    run = db.query(RankingRun).filter_by(keyword="plumber in Point Piper", result_type="local_pack").first()
    assert run.google_url == "https://www.google.com/search?q=x&uule=a%2BAB"  # SerpApi's own link still saved


def test_search_from_city_as_googles_area(client, db, setup):
    """Like Google's 'Choose area': the city as a named place, proof links show the same named area."""
    project, use, calls = setup
    use()
    job = run_job(db, _job(db, project, mode="full", search_from="area").id)
    assert job.status == "completed", job.steps
    pack = next(c for c in calls if c["engine"] == "google")
    assert pack["location"] == "Point Piper,New South Wales,Australia" and "uule" not in pack
    finder = next(c for c in calls if c["engine"] != "google")
    assert (
        finder["engine"] == "google_local" and finder["location"] == "Point Piper,New South Wales,Australia"
    )
    kw = db.query(Keyword).filter_by(project_id=project.id, keyword="plumber in Point Piper").one()
    d = client.get(f"/v1/projects/{project.id}/rankings/keywords/{kw.id}").json()
    assert (
        d["search_scope"] == "area"
        and d["search_from"] == "Point Piper,New South Wales,Australia (Google's area)"
    )
    name = "Point Piper,New South Wales,Australia"
    uule = "w+CAIQICI" + rankings.KEYS[len(name)] + base64.b64encode(name.encode()).decode()
    from urllib.parse import quote

    assert d["local_pack"]["proof_url"].endswith(f"&uule={quote(uule, safe='')}")
    assert d["local_finder"]["proof_url"].endswith(f"&uule={quote(uule, safe='')}&tbm=lcl")  # "More places"


def test_full_results_per_keyword(client, db, setup):
    project, use, _ = setup
    use()
    job = run_job(db, _job(db, project, mode="full").id)
    kw = db.query(Keyword).filter_by(project_id=project.id, keyword="plumber in Point Piper").one()
    d = client.get(f"/v1/projects/{project.id}/rankings/keywords/{kw.id}").json()
    assert d["job_id"] == str(job.id) and d["keyword"] == "plumber in Point Piper"
    pack = d["local_pack"]["results"]
    assert [r["business_name"] for r in pack] == ["Rival Plumbing", "Proximity Plumbing", "Third Plumbing"]
    assert pack[1]["is_client"] and not pack[0]["is_client"]
    finder = d["local_finder"]["results"]
    assert [r["rank"] for r in finder] == [1, 2, 3] and finder[2]["is_client"]
    other = AuditJob(job_type="ranking_check", project_id=uuid.uuid4(), params={}, status="completed")
    db.add(other)
    db.commit()
    assert (
        client.get(f"/v1/projects/{project.id}/rankings/keywords/{kw.id}?job_id={other.id}").status_code
        == 404
    )
    unknown = client.get(f"/v1/projects/{project.id}/rankings/keywords/{uuid.uuid4()}")
    assert unknown.status_code == 404

    from app.services import report

    data = report.report_data(db, project)
    header, rows = report.csv_rows(data, "results")
    assert (
        header[:4] == ["keyword", "result_type", "rank", "business_name"] and len(rows) == 12
    )  # 2 kw x (3+3)


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


def test_no_searches_left_is_refused_with_a_plain_message(client, db, setup, enqueued):
    project, use, calls = setup
    use(searches_left=0)
    r = client.post(f"/v1/projects/{project.id}/rankings/run", json={"mode": "full"})
    assert (
        r.status_code == 422
        and "0 searches left" in r.json()["detail"]
        and "2026-11-06" in r.json()["detail"]
    )
    assert calls == [] and enqueued == []


def test_fewer_searches_than_needed_still_starts(client, db, setup, enqueued):
    project, use, _ = setup
    use(searches_left=3)  # needs 4
    est = client.get(f"/v1/projects/{project.id}/rankings/estimate").json()
    assert not est["enough"] and est["can_start"]
    assert client.post(f"/v1/projects/{project.id}/rankings/run", json={"mode": "full"}).status_code == 202


def test_limit_on_the_second_keyword_keeps_the_first(db, setup, monkeypatch):
    project, use, calls = setup
    use()
    monkeypatch.setattr(
        rankings.get_settings(), "quota_serpapi_daily", 3
    )  # keyword 1: 2, keyword 2: pack only
    job = run_job(db, _job(db, project, mode="full").id)
    assert job.status == "partial_success"
    step = next(s for s in job.steps if s["name"] == "collect_rankings")
    assert (
        step["result"]["keywords_checked"] == 1
        and "daily limit reached" in step["result"]["stopped_by_limit"]
    )
    assert step["error"].startswith("Limit reached after 1 of 2 keywords; their results are saved.")
    assert len(calls) == 3  # stopped at the limit: no further attempts
    rows = {r["keyword"]: r for r in rankings.check_report(db, project, job, None)["keywords"]}
    first, second = rows["plumber in Point Piper"], rows["blocked drains near me"]
    assert first["local_pack_rank"] == 2 and first["local_finder_rank"] == 3 and first["local_finder_checked"]
    assert second["local_pack_rank"] == 2 and second["local_finder_checked"] is False  # Local Pack came first


def test_live_check_shows_results_so_far(client, db, setup):
    project, use, _ = setup
    use()
    done = run_job(db, _job(db, project, mode="full").id)
    live = AuditJob(job_type="ranking_check", project_id=project.id, params={"mode": "full"},
                    status="running", steps=[{"name": "collect_rankings", "status": "running"}])  # fmt: skip
    db.add(live)
    db.flush()
    cols = ("project_id", "keyword_id", "provider", "result_type", "country", "language", "device", "keyword",
            "location_name", "client_rank")  # fmt: skip
    for run in db.query(RankingRun).filter_by(audit_job_id=done.id).all()[:2]:  # pretend 2 searches are done
        db.add(RankingRun(**{c: getattr(run, c) for c in cols}, audit_job_id=live.id))
    db.commit()
    data = client.get(f"/v1/projects/{project.id}/rankings").json()
    assert data["live"]["keywords_total"] == 2 and data["live"]["step"] == "collect_rankings"
    assert data["latest"]["job_id"] == str(done.id)  # the previous finished check is still shown
    assert any(not k.get("pending") for k in data["live"]["keywords"])
    assert data["limit"] is None


def test_endpoints(client, db, setup, enqueued):
    project, use, _ = setup
    use()
    assert client.get(f"/v1/projects/{project.id}/rankings").json() == {
        "latest": None, "history": [], "top_businesses": [], "live": None, "limit": None, "failed": None,
        "search_from": "city",
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
