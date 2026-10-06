import httpx
import pytest

from app.models import WebsiteProfile
from app.services import geocode as geo
from app.services import nap, website, website_discovery
from app.services.geocode import GeocodeResult
from app.services.quota import get_usage
from app.workers.pipeline import run_job


def quota_usage(db, sku):
    return get_usage(db, sku).month_count


# ---------- NAP extraction ----------

SCHEMA_PAGE = """<html><head><title>Acme Build | Toronto Renovations</title>
<meta property="og:site_name" content="Acme Build Inc.">
<script type="application/ld+json">{"@context":"https://schema.org","@graph":[
 {"@type":"WebSite","name":"Acme site"},
 {"@type":"GeneralContractor","name":"Acme Build Inc.","telephone":"(416) 555-0100",
  "address":{"@type":"PostalAddress","streetAddress":"1 King St W","addressLocality":"Toronto",
             "addressRegion":"ON","postalCode":"M5H 1A1","addressCountry":"CA"},
  "geo":{"@type":"GeoCoordinates","latitude":43.6487,"longitude":-79.3817}}]}</script></head>
<body><a href="tel:+14165550199">Call</a></body></html>"""

TEXT_PAGE = """<html><head><title>Contact – Acme</title></head><body>
<div itemprop="telephone">416-555-0123</div>
<p>Visit us</p><p>1 King St W</p><p>Toronto, ON M5H 1A1</p>
<a href="tel:12">bad</a></body></html>"""


def test_schema_nap_wins():
    found = nap.extract_nap(SCHEMA_PAGE, "https://acme.example.com/")
    merged = nap.merge_nap([found], "CA")
    assert merged.name == "Acme Build Inc."
    assert merged.phone_e164 == "+14165550100"  # schema beats the tel: link
    assert merged.address == "1 King St W, Toronto, ON M5H 1A1, CA"
    assert merged.address_parts["postalCode"] == "M5H 1A1"
    assert (merged.latitude, merged.longitude) == (43.6487, -79.3817)
    assert merged.sources["phone"]["source"] == "schema"


def test_invalid_schema_coordinates_are_rejected_and_reported():
    page = SCHEMA_PAGE.replace(
        '"latitude":43.6487,"longitude":-79.3817', '"latitude":43.7982,"longitude":43.7982'
    )
    merged = nap.merge_nap([nap.extract_nap(page, "https://acme.example.com/")], "CA")
    assert merged.latitude is None and merged.longitude is None
    assert "latitude and longitude are the same number" in merged.issues[0]
    assert nap.coordinate_problem(0.0, 0.0) == "placeholder 0,0"
    assert nap.coordinate_problem(43.6, -79.3) is None


def test_text_and_microdata_fallbacks():
    merged = nap.merge_nap([nap.extract_nap(TEXT_PAGE, "https://acme.example.com/contact")], "CA")
    assert merged.phone == "+1 416-555-0123"  # microdata; the invalid tel:12 is ignored
    assert merged.address == "1 King St W, Toronto, ON M5H 1A1"  # street joined from the previous line
    assert merged.sources["address"]["source"] == "page_text"
    assert merged.name == "Contact"  # only the page title is available; low confidence


def test_compare_nap():
    site = nap.Nap(
        name="Acme Build",
        phone="+1 416-555-0100",
        phone_e164="+14165550100",
        address="1 King St W, Toronto ON M5H1A1",
    )
    result = nap.compare_nap(
        site, "Acme Build Inc.", "+1 416-555-0100", "1 King St W, Toronto, ON M5H 1A1", "CA"
    )
    assert {k: v["status"] for k, v in result.items()} == {
        "name": "match",
        "phone": "match",
        "address": "match",
    }

    other = nap.compare_nap(
        site, "Zeta Plumbing", "+1 416-555-9999", "99 Queen St E, Toronto, ON M5C 2M6", "CA"
    )
    assert {k: v["status"] for k, v in other.items()} == {
        "name": "mismatch",
        "phone": "mismatch",
        "address": "mismatch",
    }
    assert nap.compare_nap(None, "Acme", None, None, "CA")["phone"]["status"] == "missing"


def test_address_needs_same_postcode_and_street():
    # Real case: same street (written differently) but a different postcode -> inconsistent NAP.
    site = nap.Nap(address="3080 Yonge St. Suite 6060, Toronto, ON M4N 3N1, CA")
    row = nap.compare_nap(site, None, None, "3080 Yonge St Ste 6060, Toronto, ON M4N 1S1, Canada", "CA")[
        "address"
    ]
    assert row["status"] == "mismatch"
    assert "street similarity 100%" in row["detail"]
    fixed = nap.Nap(address="3080 Yonge Street, Suite 6060, Toronto ON M4N 1S1")
    row = nap.compare_nap(fixed, None, None, "3080 Yonge St Ste 6060, Toronto, ON M4N 1S1, Canada", "CA")[
        "address"
    ]
    assert row["status"] == "match"


def test_chain_website_matches_any_listed_location():
    # Joe's Pizza: the site's /locations page lists Boston first; the Google listing is the NYC branch.
    site = nap.Nap(
        name="Joe's Pizza",
        phone="+1 617-936-4464",
        phone_e164="+16179364464",
        address="1359 Boylston Street, Boston, MA 02215",
        all_addresses=["1359 Boylston Street, Boston, MA 02215", "1435 Broadway, New York, NY 10018"],
        all_phones_e164=["+16179364464", "+16465594878"],
    )
    assert site.multi_location
    result = nap.compare_nap(
        site, "Joe's Pizza Broadway", "+1 646-559-4878", "1435 Broadway, New York, NY 10018, USA", "US"
    )
    assert result["phone"]["status"] == "match" and "one of 2 phone numbers" in result["phone"]["detail"]
    assert result["address"]["status"] == "match" and "one of 2 locations" in result["address"]["detail"]
    assert result["address"]["website"] == "1435 Broadway, New York, NY 10018"
    two_phones = nap.Nap(
        address="1 King St W, Toronto ON M5H 1A1", all_phones_e164=["+14165550100", "+14165550101"]
    )
    assert not two_phones.multi_location  # office + mobile is not a chain


def test_haversine():
    # CN Tower -> Union Station is roughly 500 m
    assert 400 < nap.haversine_m(43.6426, -79.3871, 43.6453, -79.3806) < 650


# ---------- crawler: sitemap, contact page, JavaScript fallback ----------

HOME = (
    """<html><body><h1>Acme</h1><p>"""
    + "We build homes in Toronto. " * 20
    + """</p>
<a href="/contact-us">Contact</a></body></html>"""
)
CONTACT = """<html><body><h1>Contact</h1>
<script type="application/ld+json">{"@type":"LocalBusiness","name":"Acme Build","telephone":"+1 416 555 0100",
"address":{"streetAddress":"1 King St W","addressLocality":"Toronto","addressRegion":"ON",
"postalCode":"M5H 1A1"}}
</script></body></html>"""
SITEMAP_INDEX = """<?xml version="1.0"?><sitemapindex><sitemap><loc>https://acme.example.com/page-sitemap.xml</loc>
</sitemap><sitemap><loc>https://acme.example.com/image-sitemap.xml</loc></sitemap></sitemapindex>"""
PAGE_SITEMAP = """<?xml version="1.0"?><urlset>
<url><loc>https://acme.example.com/</loc></url>
<url><loc>https://acme.example.com/contact-us/</loc></url>
<url><loc>https://acme.example.com/services/deck-building/</loc></url>
<url><loc>https://acme.example.com/services/basement-finishing.html</loc></url>
<url><loc>https://other.example.com/services/not-ours/</loc></url></urlset>"""
SPA_SHELL = """<html><body><div id="root"></div><script src="/app.js"></script></body></html>"""
RENDERED = """<html><body><h1>Acme</h1><nav><a href="/services/roofing">Roofing</a></nav>
<a href="https://www.instagram.com/acmebuild">IG</a></body></html>"""


def _site(pages: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        body = pages.get(request.url.path)
        if body is None:
            return httpx.Response(404)
        ctype = "application/xml" if request.url.path.endswith(".xml") else "text/html"
        return httpx.Response(200, text=body, headers={"content-type": ctype})

    return handler


def _crawl(pages, renderer=None):
    return website.crawl(
        "https://acme.example.com/",
        transport=httpx.MockTransport(_site(pages)),
        guard=lambda u: None,
        renderer=renderer,
        region="CA",
    )


def test_crawl_uses_sitemap_and_contact_page():
    result = _crawl(
        {
            "/": HOME,
            "/sitemap_index.xml": SITEMAP_INDEX,
            "/page-sitemap.xml": PAGE_SITEMAP,
            "/contact-us/": CONTACT,
        }
    )
    assert result.sitemap_urls == 4  # other host dropped; image sitemap skipped
    assert "https://acme.example.com/contact-us/" in result.pages_fetched
    assert result.nap.name == "Acme Build"
    assert result.nap.phone_e164 == "+14165550100"
    assert result.nap.sources["address"]["url"] == "https://acme.example.com/contact-us/"
    names = {n: s for n, s, _, _ in result.offerings}
    assert names == {"Deck building": "website_sitemap", "Basement finishing": "website_sitemap"}
    assert not result.rendered_with_browser


def test_javascript_site_is_rendered_with_browser():
    calls = []

    def renderer(url):
        calls.append(url)
        return RENDERED

    result = _crawl({"/": SPA_SHELL}, renderer=renderer)
    assert calls == ["https://acme.example.com/"]
    assert result.rendered_with_browser
    assert result.social_profiles == {"instagram": "https://www.instagram.com/acmebuild"}
    assert [n for n, *_ in result.offerings] == ["Roofing"]


def test_javascript_site_without_browser_is_reported():
    result = _crawl({"/": SPA_SHELL}, renderer=None)
    assert not result.rendered_with_browser
    assert "JavaScript-rendered" in result.notes[0]


def test_needs_browser():
    assert website.needs_browser(SPA_SHELL)
    assert not website.needs_browser(HOME)


# ---------- geocoding ----------


def test_geocode_uses_nominatim_when_configured_and_caches(db, monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        assert request.headers["User-Agent"]
        return httpx.Response(
            200, json=[{"lat": "43.6487", "lon": "-79.3817", "display_name": "1 King St W"}]
        )

    monkeypatch.setattr(geo, "nominatim_configured", lambda: True)
    transport = httpx.MockTransport(handler)
    result, notes = geo.geocode(db, "1 King St W, Toronto", "CA", transport=transport)
    assert (result.source, result.latitude) == ("nominatim", 43.6487)
    again, _ = geo.geocode(db, "1 King St W, Toronto", "CA", transport=transport)
    assert again.latitude == 43.6487 and len(calls) == 1


def test_geocode_falls_back_to_google(db, client, monkeypatch):
    def handler(request):
        assert request.url.host == "maps.googleapis.com"
        return httpx.Response(
            200,
            json={"status": "OK", "results": [{"geometry": {"location": {"lat": 1.5, "lng": 2.5}}}]},
        )

    monkeypatch.setattr(geo, "nominatim_configured", lambda: False)
    result, notes = geo.geocode(db, "1 King St W, Toronto", "CA", transport=httpx.MockTransport(handler))
    assert (result.source, result.longitude) == ("google_geocoding", 2.5)
    assert "NOMINATIM_USER_AGENT" in notes[0]
    usage = {u["sku"]: u["month_count"] for u in client.get("/v1/usage").json()}
    assert usage["google_geocoding"] == 1


def test_geocode_google_denied_is_a_note_not_a_crash(db, monkeypatch):
    def handler(request):
        return httpx.Response(200, json={"status": "REQUEST_DENIED", "error_message": "API not enabled"})

    monkeypatch.setattr(geo, "nominatim_configured", lambda: False)
    result, notes = geo.geocode(db, "somewhere", "CA", transport=httpx.MockTransport(handler))
    assert result is None
    assert any("REQUEST_DENIED" in n for n in notes)
    assert quota_usage(db, "google_geocoding") == 0  # denied calls are not billed


# ---------- website_discovery job + pin distance ----------


@pytest.fixture
def site_env(monkeypatch):
    pages = {"/": HOME, "/contact-us": CONTACT}
    monkeypatch.setattr(
        website_discovery,
        "crawl",
        lambda url, renderer=None, region=None: website.crawl(
            url, transport=httpx.MockTransport(_site(pages)), guard=lambda u: None, region=region
        ),
    )
    monkeypatch.setattr(
        website_discovery,
        "geocode",
        lambda db, address, country: (GeocodeResult(43.6490, -79.3820, "nominatim"), []),
    )


def test_website_discovery_job_without_google(client, db, enqueued, site_env):
    project = client.post(
        "/v1/projects",
        json={
            "name": "Acme",
            "business_name": "Acme Build",
            "country": "CA",
            "website_url": "https://acme.example.com/",
        },
    ).json()
    r = client.post(f"/v1/projects/{project['id']}/website-discovery")
    assert r.status_code == 202, r.text
    job = run_job(db, enqueued[-1])
    assert job.status == "completed", job.steps
    summary = job.steps[0]["result"]
    assert summary["phone"] == "+1 416-555-0100" and summary["coordinates_from"] == "nominatim"

    data = client.get(f"/v1/projects/{project['id']}/profile").json()
    assert data["website"]["business_name"] == "Acme Build"
    assert data["website"]["latitude"] == 43.6490
    assert data["profile"] is None and data["nap_check"] is None  # no Google profile yet
    assert db.query(WebsiteProfile).count() == 1


def test_website_discovery_needs_a_website(client):
    project = client.post("/v1/projects", json={"name": "A", "business_name": "A", "country": "CA"}).json()
    assert client.post(f"/v1/projects/{project['id']}/website-discovery").status_code == 422


def test_audit_fills_nap_check_and_pin_distance(client, db, enqueued, site_env, monkeypatch):
    from app.providers.google_places import GooglePlacesClient
    from app.providers.serpapi import SerpApiClient
    from app.workers.jobs import gbp_audit
    from tests.test_audit import PLACE, _serp

    place = {
        **PLACE,
        "internationalPhoneNumber": "+1 416-555-0100",
        "location": {"latitude": 43.6487, "longitude": -79.3817},
    }
    monkeypatch.setattr(
        gbp_audit,
        "get_places_client",
        lambda: GooglePlacesClient(
            "k", transport=httpx.MockTransport(lambda r: httpx.Response(200, json=place))
        ),
    )
    monkeypatch.setattr(
        gbp_audit, "get_serpapi_client", lambda: SerpApiClient("k", transport=httpx.MockTransport(_serp()))
    )
    project = client.post(
        "/v1/projects",
        json={"name": "Acme", "business_name": "Acme Build", "country": "CA", "place_id": "ChIJacme"},
    ).json()
    client.post(f"/v1/projects/{project['id']}/gbp-audit")
    job = run_job(db, enqueued[-1])
    assert job.status == "completed", job.steps

    data = client.get(f"/v1/projects/{project['id']}/profile").json()
    check = {k: v["status"] for k, v in data["nap_check"].items()}
    assert check == {"name": "match", "phone": "match", "address": "match"}
    distance = data["location"]["pin_vs_website_address_distance_meters"]
    assert 20 < distance < 60  # 43.6487,-79.3817 -> 43.6490,-79.3820
