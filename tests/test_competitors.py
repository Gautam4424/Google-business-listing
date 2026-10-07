import uuid
from datetime import timedelta

import httpx
import pytest

from app.models import (
    AuditJob,
    Business,
    BusinessLocation,
    Competitor,
    CompetitorMetric,
    GapRecommendation,
    GbpProfile,
    GbpReview,
    Keyword,
    Project,
    ProjectService,
    ReviewTag,
)
from app.models.base import utcnow
from app.providers.google_places import GooglePlacesClient
from app.providers.serpapi import SerpApiClient
from app.services import competitors, gaps, rankings
from app.workers.pipeline import run_job

CLIENT_PID, CLIENT_CID = "ChIJclient", "1111"
KW = ["plumber in Point Piper", "blocked drains in Point Piper", "gas plumber in Point Piper"]


def place(title, cid, pid=None, rating=None, reviews=None, types=None, position=1):
    p = {"position": position, "title": title, "data_cid": cid, "rating": rating, "reviews": reviews}
    if pid:
        p["place_id"] = pid
    if types:
        p["type"], p["types"] = types[0], types
    return p


RIVAL = dict(
    title="Rival Plumbing",
    cid="2222",
    pid="ChIJrival",
    rating=4.8,
    reviews=300,
    types=["Plumber", "Drainage service", "Gas fitter"],
)
MINT = dict(
    title="Mint Plumbing",
    cid="5555",
    pid="ChIJmint",
    rating=4.7,
    reviews=500,
    types=["Plumber", "Drainage service", "Hot water system supplier"],
)
CLIENT = dict(
    title="Proximity Plumbing", cid=CLIENT_CID, pid=CLIENT_PID, rating=4.9, reviews=100, types=["Plumber"]
)
OTHER = dict(
    title="Sparky Electrics", cid="4444", pid="ChIJother", rating=4.2, reviews=40, types=["Electrician"]
)


def pack(*rows):
    return {
        "local_results": {
            "places": [
                {
                    "position": i,
                    "title": r["title"],
                    "place_id": r["cid"],
                    "type": "Plumber",
                    "rating": r["rating"],
                    "reviews": r["reviews"],
                }
                for i, r in enumerate(rows, start=1)
            ]
        }
    }


THIRD = dict(title="Third Plumbing", cid="3333", rating=4.5, reviews=120)
PACKS = {KW[0]: pack(RIVAL, CLIENT, MINT), KW[1]: pack(RIVAL, MINT, THIRD), KW[2]: pack(RIVAL, CLIENT, THIRD)}
MAPS = {
    "local_results": [place(position=i, **r) for i, r in enumerate([RIVAL, MINT, CLIENT, OTHER], start=1)]
}
DETAILS = {
    "ChIJrival": {
        "id": "ChIJrival",
        "rating": 4.8,
        "userRatingCount": 300,
        "websiteUri": "https://rival.example/",
        "reviews": [
            {
                "rating": 5,
                "text": {
                    "text": "Great job on our gas fitting. Very punctual and on time.",
                    "languageCode": "en",
                },
            }
        ],
    },
    "ChIJmint": {
        "id": "ChIJmint",
        "rating": 4.7,
        "userRatingCount": 500,
        "reviews": [
            {
                "rating": 5,
                "text": {"text": "They did the gas fitting and arrived on time.", "languageCode": "en"},
            }
        ],
    },
    "ChIJother": {"id": "ChIJother", "rating": 4.2, "userRatingCount": 40, "reviews": []},
}


@pytest.fixture
def setup(db, monkeypatch):
    business = Business(
        place_id=CLIENT_PID,
        name="Proximity Plumbing",
        source="google_places",
        is_client=True,
        domain="proximityplumbing.com.au",
    )
    db.add(business)
    db.flush()
    db.add(
        GbpProfile(
            business_id=business.id,
            place_id=CLIENT_PID,
            business_name="Proximity Plumbing",
            maps_url=f"https://maps.google.com/?cid={CLIENT_CID}",
            primary_category="Plumber",
            rating=4.9,
            review_count=100,
            source="google_places",
        )
    )
    db.add(
        BusinessLocation(business_id=business.id, latitude=-33.867, longitude=151.25, source="google_places")
    )
    project = Project(
        name="P",
        input_business_name="Proximity Plumbing",
        country="AU",
        language="en",
        service_areas=[{"name": "Point Piper, NSW", "latitude": -33.867, "longitude": 151.25}],
        client_business=business,
    )
    db.add(project)
    db.flush()
    db.add(
        ProjectService(
            project_id=project.id,
            name="Blocked drains",
            normalized_name="blocked drain",
            kind="service",
            selected=True,
        )
    )
    review = GbpReview(
        business_id=business.id,
        review_id="r1",
        rating=5,
        review_text="Friendly",
        position=1,
        sentiment="positive",
        source="google_places",
    )
    db.add(review)
    db.flush()
    db.add(
        ReviewTag(review_id=review.id, tag="friendly staff", theme="staff", sentiment="positive", source="t")
    )
    for text in KW:
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
    db.commit()

    serp_calls, places_calls = [], []

    def serp(request: httpx.Request) -> httpx.Response:
        p = request.url.params
        if request.url.path == "/account.json":
            return httpx.Response(200, json={"total_searches_left": 200, "plan_renewal_date": "2026-11-06"})
        serp_calls.append(dict(p))
        return httpx.Response(200, json=PACKS[p["q"]] if p["engine"] == "google" else MAPS)

    def places(request: httpx.Request) -> httpx.Response:
        pid = request.url.path.rsplit("/", 1)[-1]
        places_calls.append(pid)
        return httpx.Response(200, json=DETAILS[pid])

    monkeypatch.setattr(
        rankings, "get_serpapi_client", lambda: SerpApiClient("k", transport=httpx.MockTransport(serp))
    )
    monkeypatch.setattr(
        competitors,
        "get_places_client",
        lambda: GooglePlacesClient("g", transport=httpx.MockTransport(places)),
    )
    return project, serp_calls, places_calls


def _check(db, project):
    job = AuditJob(job_type="ranking_check", project_id=project.id, params={"mode": "full"}, status="queued")
    db.add(job)
    db.commit()
    return run_job(db, job.id)


def test_competitors_found_after_ranking_check(db, setup):
    project, serp_calls, places_calls = setup
    job = _check(db, project)
    assert job.status == "completed", job.steps
    step = next(s for s in job.steps if s["name"] == "find_competitors")
    assert step["status"] == "succeeded" and step["result"]["serpapi_searches"] == 0
    assert len(serp_calls) == 6  # only the ranking searches themselves

    report = competitors.competitors_report(db, project)
    names = [c["business_name"] for c in report["competitors"]]
    assert "Proximity Plumbing" not in names  # the client is never its own competitor
    assert names[0] == "Rival Plumbing"  # Local Pack for all 3 keywords
    rival = report["competitors"][0]
    assert rival["local_pack_count"] == 3 and rival["keyword_share"] == 1.0
    assert rival["categories"] == ["Plumber", "Drainage service", "Gas fitter"]
    assert rival["profile_source"] == "google_places" and rival["review_sample_size"] == 1
    assert rival["website_domain"] == "rival.example" and rival["review_velocity_30d"] is None
    third = next(c for c in report["competitors"] if c["business_name"] == "Third Plumbing")
    assert third["profile_source"] == "search_results"  # Local Pack only: no place_id, no Places call
    assert set(places_calls) == {"ChIJrival", "ChIJmint", "ChIJother"}
    assert report["client"]["review_count"] == 100 and "Plumber" in report["client"]["categories"]
    assert report["analysis"]["keywords"] == 3


def test_rule_share_or_three_local_packs():
    data = competitors.CheckData(
        job=None, mode="full", client_categories=[], keywords={i: {} for i in range(10)}, seen={}
    )
    many = competitors.Seen(
        key="a", keywords={i: {"local_pack_rank": None, "local_finder_rank": 5} for i in range(2)}
    )
    packs = competitors.Seen(
        key="b", keywords={i: {"local_pack_rank": 1, "local_finder_rank": None} for i in range(3)}
    )
    rare = competitors.Seen(key="c", keywords={0: {"local_pack_rank": 2, "local_finder_rank": 4}})
    for s in (many, packs, rare):
        s.names["x"] += 1
        data.seen[s.key] = s
    picked = [s.key for s, _ in competitors.select_competitors(data, 10)]
    assert picked == ["b", "a"]  # 3 Local Packs; 20% of keywords; 1 of 10 is neither


def test_gaps_follow_the_brief(db, setup):
    project, _, _ = setup
    _check(db, project)
    rows = db.query(GapRecommendation).filter_by(project_id=project.id).all()
    by_type = {}
    for g in rows:
        by_type.setdefault(g.gap_type, []).append(g)

    cat = next(g for g in by_type["category"] if g.evidence["category"] == "Drainage service")
    assert cat.recommendation.startswith("Review whether 'Drainage service' is an accurate and eligible")
    assert cat.client_value == ["Plumber"] and "Drainage service" in cat.competitor_pattern
    assert cat.evidence["related_client_services"] == ["Blocked drains"] and cat.priority == "high"
    assert all("Review whether" in g.recommendation for g in by_type["category"])

    svc = next(g for g in by_type["service"] if g.evidence["service"].lower() == "gas fitting")
    assert svc.evidence["suggested_keyword"] == "gas fitting in Point Piper"
    assert "their reviews" in svc.evidence["found_in"]

    review = by_type["review"][0]  # 100 reviews vs competitor median
    assert review.client_value == {"review_count": 100} and "never offer incentives" in review.recommendation

    topic = next(g for g in by_type["review_topic"] if g.evidence["topic"] == "on time")
    assert "up to 5 reviews" in topic.evidence["basis"]

    ranking = by_type["ranking"]
    assert (
        len(ranking) == 1 and ranking[0].client_value["keyword"] == KW[1]
    )  # the only keyword without the client
    assert ranking[0].client_value["local_finder_rank"] == 3 and ranking[0].priority == "medium"


def test_reanalysis_uses_cache_and_replaces_gaps(client, db, setup, enqueued):
    project, serp_calls, places_calls = setup
    _check(db, project)
    before = db.query(GapRecommendation).count()
    r = client.post(f"/v1/projects/{project.id}/competitors/analyze")
    assert r.status_code == 202
    run_job(db, uuid.UUID(r.json()["id"]))
    assert len(places_calls) == 3  # Place Details reused from the cache
    assert len(serp_calls) == 6  # still no new SerpApi searches
    assert db.query(GapRecommendation).count() == before  # replaced, not duplicated
    assert db.query(Competitor).filter_by(project_id=project.id).count() == len(
        competitors.competitors_report(db, project)["competitors"]
    )


def test_endpoints(client, db, setup, enqueued):
    project, _, _ = setup
    empty = client.get(f"/v1/projects/{project.id}/competitors").json()
    assert empty["analysis"] is None and empty["competitors"] == []
    assert (
        client.post(f"/v1/projects/{project.id}/competitors/analyze").status_code == 422
    )  # no ranking check
    _check(db, project)
    data = client.get(f"/v1/projects/{project.id}/competitors").json()
    assert data["competitors"] and data["client"]["business_name"] == "Proximity Plumbing"
    g = client.get(f"/v1/projects/{project.id}/gaps").json()
    assert g["gaps"][0]["priority"] == "high"
    first = g["gaps"][0]
    assert set(first) >= {"gap_type", "client_value", "competitor_pattern", "recommendation", "evidence"}
    assert g["counts"]["ranking"] == 1


def test_review_velocity_needs_two_snapshots_a_week_apart(db, setup):
    project, _, _ = setup
    now = utcnow()
    assert competitors._velocity(280, now - timedelta(days=3), 300, now) is None  # too close together
    assert competitors._velocity(280, now - timedelta(days=10), 300, now) == 60.0
    _check(db, project)
    metric = db.query(CompetitorMetric).filter_by(review_count=300).one()
    metric.snapshot_at = now - timedelta(days=10)
    metric.review_count = 280
    db.commit()
    job = AuditJob(job_type="competitor_analysis", project_id=project.id, params={}, status="queued")
    db.add(job)
    db.commit()
    run_job(db, job.id)
    rival = next(
        c
        for c in competitors.competitors_report(db, project)["competitors"]
        if c["business_name"] == "Rival Plumbing"
    )
    assert rival["review_velocity_30d"] == pytest.approx(60.0, abs=0.5)


def test_generic_google_labels_are_never_gaps_and_related_services_are_found():
    client = {"categories": ["Plumber"], "services": ["Gas Plumbing", "Gas Leak Detection", "Blocked Drains"]}
    comps = [
        {"business_name": n, "categories": ["Plumber", "Service establishment", "Gasfitter"]} for n in "ABC"
    ]
    found, _ = gaps.category_gaps(client, comps, "X")
    assert [g["evidence"]["category"] for g in found] == ["Gasfitter"]  # not "Service establishment"
    assert found[0]["priority"] == "high"
    assert found[0]["evidence"]["related_client_services"] == ["Gas Plumbing", "Gas Leak Detection"]
    assert "Service establishment" not in found[0]["competitor_pattern"]
    assert gaps.related_services("Roofer", client["services"]) == []


def test_competitor_lookups_leave_a_reserve_for_client_audits(db, setup, monkeypatch):
    project, _, places_calls = setup
    monkeypatch.setattr(competitors.get_settings(), "quota_places_details_daily", 11)
    from app.services import quota

    quota.consume(db, "google_places_details")  # 10 left = the reserve
    _check(db, project)
    assert places_calls == []  # nothing spent on competitors
    report = competitors.competitors_report(db, project)
    assert report["competitors"] and all(
        c["profile_source"] == "search_results" for c in report["competitors"]
    )


def test_no_category_gap_from_a_single_competitor():
    client = {"categories": ["Plumber"], "services": []}
    comps = [
        {"business_name": "A", "categories": ["Plumber", "Roofer"]},
        {"business_name": "B", "categories": ["Plumber"]},
    ]
    found, _ = gaps.category_gaps(client, comps, "X")
    assert found == []  # "Roofer" is held by only 1 competitor
