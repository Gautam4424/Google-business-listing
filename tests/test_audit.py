import json

import httpx
import pytest

from app.models import AuditJob
from app.providers.google_places import GooglePlacesClient
from app.providers.serpapi import SerpApiClient
from app.services import place_profile, website, website_discovery
from app.workers.jobs import gbp_audit
from app.workers.pipeline import run_job

# ---------- website crawler ----------

HOME = """<html><head>
<script type="application/ld+json">{"@context":"https://schema.org","@type":"GeneralContractor",
 "sameAs":["https://www.instagram.com/acmebuild/"],
 "hasOfferCatalog":{"@type":"OfferCatalog","itemListElement":[
   {"@type":"Offer","itemOffered":{"@type":"Service","name":"Basement Finishing"}}]}}</script></head>
<body><nav>
 <a href="/services/">Services</a>
 <a href="/services/kitchen-renovation">Kitchen Renovation</a>
 <a href="/services/bathroom-remodel">Bathroom Remodel</a>
 <a href="/services/kitchen-renovation#quote">Learn more</a>
 <a href="/private/admin">Admin</a>
</nav><footer>
 <a href="https://www.facebook.com/AcmeBuild/">Facebook</a>
 <a href="https://www.facebook.com/sharer/sharer.php?u=x">Share</a>
 <a href="https://twitter.com/intent/tweet?text=hi">Tweet</a>
 <a href="https://ca.linkedin.com/company/acme-build">LinkedIn</a>
 <a href="https://www.linkedin.com/feed/">feed</a>
</footer></body></html>"""

SERVICES = """<html><body><h2>Additions</h2><h2>Why choose us</h2>
<a href="/services/home-additions">Home Additions</a></body></html>"""


def _site(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/robots.txt":
        return httpx.Response(200, text="User-agent: *\nDisallow: /private/\n")
    if path in ("", "/"):
        return httpx.Response(200, text=HOME, headers={"content-type": "text/html"})
    if path == "/services/":
        return httpx.Response(200, text=SERVICES, headers={"content-type": "text/html"})
    return httpx.Response(404)


def test_crawl_finds_social_profiles_and_offerings():
    result = website.crawl(
        "https://acme.example.com/", transport=httpx.MockTransport(_site), guard=lambda u: None
    )
    assert result.social_profiles == {
        "instagram": "https://www.instagram.com/acmebuild",
        "facebook": "https://www.facebook.com/AcmeBuild",
        "linkedin": "https://ca.linkedin.com/company/acme-build",
    }
    names = {name: source for name, source, _, _ in result.offerings}
    assert names["Basement Finishing"] == "website_schema"
    assert names["Kitchen Renovation"] == "website_service_page"
    assert names["Home Additions"] == "website_service_page"  # from the services hub page
    assert "Learn more" not in names and "Services" not in names
    assert result.pages_fetched == ["https://acme.example.com/", "https://acme.example.com/services/"]


def test_crawl_refuses_private_addresses():
    result = website.crawl("http://127.0.0.1/", transport=httpx.MockTransport(_site))
    assert result.pages_fetched == []
    assert "non-public" in result.errors[0]


@pytest.mark.parametrize(
    "url, platform",
    [
        ("https://www.youtube.com/@acme", "youtube"),
        ("https://www.youtube.com/watch?v=abc", None),
        ("https://www.instagram.com/p/xyz/", None),
        ("https://x.com/acme", "x"),
        ("https://www.facebook.com/", None),
        ("https://www.linkedin.com/in/jane-doe", "linkedin"),
        ("https://example.com/facebook.com/acme", None),
    ],
)
def test_social_platform(url, platform):
    assert website.social_platform(url) == platform


def test_facebook_profile_php_keeps_its_id():
    url = "https://www.facebook.com/profile.php?id=100063&mibextid=abc"
    assert website.normalize_profile_url(url) == "https://www.facebook.com/profile.php?id=100063"
    assert (
        website.normalize_profile_url("https://www.facebook.com/Acme/?ref=page")
        == "https://www.facebook.com/Acme"
    )


def test_clean_label_strips_call_to_action_words():
    assert website.clean_label("Explore Home Additions") == "Home Additions"
    assert website.clean_label("Learn more about Roofing") == "Roofing"
    assert website.clean_label("Learn more") is None
    assert website.clean_label("Call 416-555-0100") is None
    assert website.clean_label("Drain Locator | Water Lines & Gas Pipes​") == "Drain Locator"


# ---------- place details normalisation ----------

PLACE = {
    "id": "ChIJacme",
    "displayName": {"text": "Acme Build"},
    "formattedAddress": "1 King St W, Toronto, ON M5H 1A1, Canada",
    "location": {"latitude": 43.65, "longitude": -79.38},
    "googleMapsUri": "https://maps.google.com/?cid=1",
    "websiteUri": "https://acme.example.com/?utm_source=gbp",
    "internationalPhoneNumber": "+1 416-555-0100",
    "primaryType": "general_contractor",
    "primaryTypeDisplayName": {"text": "General Contractor"},
    "types": ["general_contractor", "roofing_contractor", "point_of_interest", "establishment"],
    "businessStatus": "OPERATIONAL",
    "rating": 4.8,
    "userRatingCount": 57,
    "regularOpeningHours": {"weekdayDescriptions": ["Monday: 9:00 AM – 5:00 PM", "Sunday: Closed"]},
    "currentOpeningHours": {"openNow": True},
    "photos": [{}, {}, {}],
    "accessibilityOptions": {"wheelchairAccessibleEntrance": True, "wheelchairAccessibleParking": False},
    "plusCode": {"compoundCode": "JF2C+2X Toronto"},
    "reviews": [
        {
            "name": f"places/ChIJacme/reviews/r{i}",
            "rating": 5,
            "text": {"text": f"Great job {i}", "languageCode": "en"},
            "authorAttribution": {"displayName": f"Author {i}", "uri": "https://maps.google.com/u"},
            "publishTime": "2026-09-01T10:00:00.123456789Z",
            "relativePublishTimeDescription": "a month ago",
        }
        for i in range(1, 6)
    ],
}


def test_profile_fields_and_nulls():
    f = place_profile.profile_fields(PLACE)
    assert f["primary_category"] == "General Contractor"
    assert f["secondary_categories"] == ["Roofing contractor"]
    assert f["review_count"] == 57
    assert f["photos_count_available"] == 3
    assert f["accessibility_attributes"] == ["Wheelchair-accessible entrance"]
    assert f["service_options"] is None  # not returned -> unknown, never "not offered"
    assert f["opening_hours"]["open_now"] is True
    assert f["map_pin_status"] == "present"
    assert f["plus_code"] == "JF2C+2X Toronto"


def test_parse_time_handles_nanoseconds():
    assert place_profile.parse_time("2026-09-01T10:00:00.123456789Z").microsecond == 123456


# ---------- full gbp_audit job ----------


def _serp(n_first=8, n_second=2, fail=False):
    def handler(request: httpx.Request) -> httpx.Response:
        if fail:
            return httpx.Response(401, json={"error": "Invalid API key."})
        params = request.url.params
        assert params["engine"] == "google_maps_reviews" and params["place_id"] == "ChIJacme"
        start, count = (n_first + 1, n_second) if "next_page_token" in params else (1, n_first)
        reviews = [
            {
                "review_id": f"s{i}",
                "rating": 5.0,
                "snippet": f"Serp review {i}",
                "date": "2 weeks ago",
                "iso_date": "2026-09-20T00:00:00Z",
                "user": {"name": f"User {i}", "link": "https://maps.google.com/u"},
                "link": f"https://maps.google.com/r{i}",
                **({"response": {"snippet": "Thanks!"}} if i == 1 else {}),
            }
            for i in range(start, start + count)
        ]
        body = {"reviews": reviews}
        if "next_page_token" not in params:
            body["serpapi_pagination"] = {"next_page_token": "tok"}
        return httpx.Response(200, json=body)

    return handler


@pytest.fixture
def audit_env(monkeypatch):
    def use(serp_handler):
        monkeypatch.setattr(
            gbp_audit,
            "get_places_client",
            lambda: GooglePlacesClient(
                "test-google-key", transport=httpx.MockTransport(lambda r: httpx.Response(200, json=PLACE))
            ),
        )
        monkeypatch.setattr(
            gbp_audit,
            "get_serpapi_client",
            lambda: SerpApiClient("test-serpapi-key", transport=httpx.MockTransport(serp_handler)),
        )
        monkeypatch.setattr(
            website_discovery,
            "crawl",
            lambda url, renderer=None, region=None: website.crawl(
                url, transport=httpx.MockTransport(_site), guard=lambda u: None, region=region
            ),
        )
        monkeypatch.setattr(
            website_discovery, "geocode", lambda db, address, country: (None, ["no geocoder"])
        )

    return use


def _project_with_place(client):
    return client.post(
        "/v1/projects",
        json={"name": "Acme audit", "business_name": "Acme Build", "country": "CA", "place_id": "ChIJacme"},
    ).json()


def _run_audit(client, db, enqueued, project_id, **options):
    r = client.post(f"/v1/projects/{project_id}/gbp-audit", json=options or None)
    assert r.status_code == 202, r.text
    job = run_job(db, enqueued[-1])
    db.expire_all()
    return job


def test_gbp_audit_end_to_end(client, db, enqueued, audit_env):
    audit_env(_serp())
    project = _project_with_place(client)
    job = _run_audit(client, db, enqueued, project["id"], top10_reviews=True)
    assert job.status == "completed", json.dumps(job.steps, default=str)

    data = client.get(f"/v1/projects/{project['id']}/profile").json()
    assert data["profile"]["review_count"] == 57
    assert data["profile"]["website_url"] == "https://acme.example.com/"  # tracking removed
    assert [r["position"] for r in data["reviews"]] == list(range(1, 11))  # top 10 via SerpApi (8 + 2)
    assert data["reviews"][0]["owner_reply"] == "Thanks!"
    assert data["reviews"][0]["source"] == "serpapi"
    assert set(data["social_profiles"]) == {"instagram", "facebook", "linkedin"}
    sources = {o["name"]: o["source"] for o in data["offerings"]}
    assert sources["General Contractor"] == "gbp_category"
    assert sources["Kitchen Renovation"] == "website_service_page"
    assert data["last_audit"]["status"] == "completed"

    usage = {u["sku"]: u["month_count"] for u in client.get("/v1/usage").json()}
    assert usage["google_places_details"] == 1 and usage["serpapi_search"] == 2

    # Re-running replaces reviews and offerings instead of duplicating them.
    _run_audit(client, db, enqueued, project["id"], top10_reviews=True)
    again = client.get(f"/v1/projects/{project['id']}/profile").json()
    assert len(again["reviews"]) == 10
    assert len(again["offerings"]) == len(data["offerings"])


def test_audit_analyses_reviews_and_reanalysis_needs_no_api(client, db, enqueued, audit_env, monkeypatch):
    audit_env(lambda r: httpx.Response(500))
    project = _project_with_place(client)
    job = _run_audit(client, db, enqueued, project["id"])
    step = next(s for s in job.steps if s["name"] == "analyze_reviews")
    assert step["status"] == "succeeded" and step["result"]["reviews_analysed"] == 5

    data = client.get(f"/v1/projects/{project['id']}/profile").json()
    first = data["reviews"][0]
    assert first["sentiment"] == "positive" and first["language"] == "en"
    assert {"tag": "great results", "theme": "service_quality", "sentiment": "positive"}.items() <= first[
        "tags"
    ][0].items()
    summary = data["review_summary"]
    assert summary["total_review_count"] == 57 and summary["sentiment_distribution"]["positive"] == 5
    assert summary["top_positive_topics"][0] == "great results"

    # Re-analysis: no Google or SerpApi calls at all.
    def no_network(*a, **k):
        raise AssertionError("no API calls allowed")

    monkeypatch.setattr(gbp_audit, "get_places_client", no_network)
    r = client.post(f"/v1/projects/{project['id']}/reviews/analyze")
    assert r.status_code == 202
    assert run_job(db, enqueued[-1]).status == "completed"
    reviews = client.get(f"/v1/projects/{project['id']}/reviews").json()
    assert len(reviews["reviews"]) == 5 and reviews["review_summary"]["analysed_review_count"] == 5


def test_gbp_audit_uses_no_serpapi_credits_by_default(client, db, enqueued, audit_env):
    def must_not_call(request):
        raise AssertionError("SerpApi must not be called by default")

    audit_env(must_not_call)
    project = _project_with_place(client)
    job = _run_audit(client, db, enqueued, project["id"])
    assert job.status == "completed"
    step = next(s for s in job.steps if s["name"] == "fetch_reviews")
    assert step["result"]["source"] == "google_places"
    assert "off to save credits" in step["result"]["note"]
    assert len(client.get(f"/v1/projects/{project['id']}/profile").json()["reviews"]) == 5
    usage = {u["sku"]: u["month_count"] for u in client.get("/v1/usage").json()}
    assert usage["serpapi_search"] == 0


def test_gbp_audit_falls_back_to_google_reviews_without_serpapi(client, db, enqueued, audit_env):
    audit_env(_serp(fail=True))
    project = _project_with_place(client)
    job = _run_audit(client, db, enqueued, project["id"], top10_reviews=True)
    assert job.status == "completed"
    step = next(s for s in job.steps if s["name"] == "fetch_reviews")
    assert step["result"]["source"] == "google_places"
    assert "at most 5" in step["result"]["note"]
    assert "test-serpapi-key" not in json.dumps(job.steps)
    usage = {u["sku"]: u["month_count"] for u in client.get("/v1/usage").json()}
    assert usage["serpapi_search"] == 0  # rejected key is not counted against the free searches

    reviews = client.get(f"/v1/projects/{project['id']}/profile").json()["reviews"]
    assert len(reviews) == 5 and reviews[0]["author_name"] == "Author 1"


def test_gbp_audit_without_place_fails_cleanly(client, db, enqueued, monkeypatch):
    from app.services import business_lookup

    monkeypatch.setattr(business_lookup, "get_places_client", lambda: None)
    project = client.post(
        "/v1/projects", json={"name": "X", "business_name": "Nowhere Inc", "country": "CA"}
    ).json()
    job = _run_audit(client, db, enqueued, project["id"])
    assert job.status == "failed"
    assert "not found on Google" in job.error
    assert db.query(AuditJob).count() == 1


def test_profile_404(client):
    assert client.get("/v1/projects/00000000-0000-0000-0000-000000000000/profile").status_code == 404
