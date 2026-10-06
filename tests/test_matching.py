from types import SimpleNamespace

import httpx
import pytest

from app.models import Business, Project
from app.providers.google_places import GooglePlacesClient
from app.services import business_lookup, matching, website, website_discovery
from app.services.business_lookup import Candidate
from app.services.geocode import GeocodeResult
from app.workers.pipeline import run_job

REF = matching.Reference(
    name="Acme Build",
    address="1 King St W, Toronto, ON M5H 1A1",
    phone_e164="+14165550100",
    domain="acme.example.com",
    latitude=43.6487,
    longitude=-79.3817,
    region="CA",
)
RIGHT = Candidate(
    business_name="Acme Build Inc.",
    address="1 King Street West, Toronto, ON M5H 1A1, Canada",
    phone="+1 416-555-0100",
    website_url="https://www.acme.example.com/?utm_source=gbp",
    latitude=43.6488,
    longitude=-79.3818,
    place_id="ChIJright",
)
WRONG = Candidate(
    business_name="Acme Plumbing",
    address="99 Queen St E, Toronto, ON M5C 2M6, Canada",
    phone="+1 416-555-9999",
    website_url="https://acmeplumbing.example.org/",
    latitude=43.6530,
    longitude=-79.3770,
    place_id="ChIJwrong",
)


# ---------- scoring ----------


def test_right_business_scores_high_with_reasons():
    s = matching.score_candidate(REF, RIGHT)
    assert s.match_confidence >= 0.95
    assert "Phone matches" in s.match_reasons
    assert "Website domain matches" in s.match_reasons
    assert any(r.startswith("Address matches") for r in s.match_reasons)
    assert any(r.startswith("Map pin") for r in s.match_reasons)
    assert s.mismatch_reasons == []


def test_wrong_business_scores_low():
    s = matching.score_candidate(REF, WRONG)
    assert s.match_confidence < 0.5
    assert any("Phone differs" in r for r in s.mismatch_reasons)
    assert any("Different website" in r for r in s.mismatch_reasons)


def test_missing_signals_are_redistributed_not_penalised():
    ref = matching.Reference(name="Acme Build", phone_e164="+14165550100", region="CA")
    s = matching.score_candidate(ref, RIGHT)
    assert set(s.signals) == {"name", "phone"}
    assert s.evidence_weight == 0.5
    assert s.match_confidence >= 0.95  # name + phone both match -> no penalty for the missing ones


def test_name_only_is_never_enough():
    s = matching.score_candidate(matching.Reference(name="Acme Build", region="CA"), RIGHT)
    assert s.match_confidence == matching.WEAK_EVIDENCE_CAP
    assert "Not enough information to confirm automatically" in s.mismatch_reasons


def test_same_street_different_postcode_is_partial():
    cand = Candidate(**{**RIGHT.__dict__, "address": "1 King St W, Toronto, ON M5H 9Z9"})
    s = matching.score_candidate(REF, cand)
    assert s.signals["address"]["score"] == 0.4
    assert any("different postcode" in r for r in s.mismatch_reasons)


def test_street_without_number_is_weak_evidence():
    ref = matching.Reference(name="Starbucks", address="Yonge St, Toronto", region="CA")
    cand = Candidate(
        business_name="Starbucks Coffee Company", address="6355 Yonge St, North York, ON M2M 3X8"
    )
    s = matching.score_candidate(ref, cand)
    assert s.signals["address"]["score"] == 0.5
    assert s.match_confidence < matching.AUTO_SELECT
    numbered = matching.Reference(address="220 Yonge St, Toronto", region="CA")
    assert matching.score_candidate(numbered, cand).signals["address"]["score"] == 0.2  # 220 vs 6355


def test_chain_website_reference_matches_any_location():
    ref = matching.Reference(
        name="Acme Build",
        address="99 Queen St E, Toronto, ON M5C 2M6",
        phone_e164="+14165559999",
        domain="acme.example.com",
        region="CA",
        other_addresses=["1 King St W, Toronto, ON M5H 1A1"],
        other_phones=["+14165550100"],
    )
    s = matching.score_candidate(ref, RIGHT)
    assert "Phone matches" in s.match_reasons
    assert any("one of 2 locations" in r for r in s.match_reasons)
    assert s.match_confidence >= 0.95


def test_decide_auto_selects_clear_winner():
    d = matching.decide(REF, [WRONG, RIGHT])
    assert d.status == "auto_selected" and d.place_id == "ChIJright"
    assert [c["place_id"] for c in d.candidates] == ["ChIJright", "ChIJwrong"]  # best first


def test_decide_flags_possible_duplicates():
    twin = Candidate(**{**RIGHT.__dict__, "place_id": "ChIJtwin"})
    d = matching.decide(REF, [RIGHT, twin])
    assert d.status == "manual_review_required"
    assert "match equally well" in d.reason


def test_decide_low_confidence_needs_review():
    d = matching.decide(REF, [WRONG])
    assert d.status == "manual_review_required" and d.place_id is None
    assert matching.decide(REF, []).status == "not_found"


def test_reference_prefers_website_over_input():
    project = SimpleNamespace(
        country="CA",
        input_business_name="Typed Name",
        input_address="typed address",
        input_phone="416 555 0123",
        website_url="https://typed.example.com",
    )
    site = SimpleNamespace(
        business_name="Site Name",
        address=None,
        phone_e164="+14165550100",
        url="https://www.site.example.com/",
        latitude=None,
        longitude=None,
    )
    ref = matching.build_reference(project, site)
    assert (ref.name, ref.address, ref.phone_e164, ref.domain) == (
        "Site Name",
        "typed address",
        "+14165550100",
        "site.example.com",
    )
    assert ref.sources == {
        "name": "website",
        "address": "your input",
        "phone": "website",
        "domain": "website",
    }


# ---------- discover-business endpoints + job ----------


def _place(c: Candidate) -> dict:
    return {
        "id": c.place_id,
        "displayName": {"text": c.business_name},
        "formattedAddress": c.address,
        "location": {"latitude": c.latitude, "longitude": c.longitude},
        "internationalPhoneNumber": c.phone,
        "websiteUri": c.website_url,
        "googleMapsUri": f"https://maps.google.com/?cid={c.place_id}",
    }


SITE = """<html><body><script type="application/ld+json">{"@type":"LocalBusiness","name":"Acme Build",
"telephone":"+1 416 555 0100","address":{"streetAddress":"1 King St W","addressLocality":"Toronto",
"addressRegion":"ON","postalCode":"M5H 1A1"},"geo":{"latitude":43.6487,"longitude":-79.3817}}</script>
<p>Renovations in Toronto.</p></body></html>"""


@pytest.fixture
def google(monkeypatch):
    def use(*candidates: Candidate):
        places = {"places": [_place(c) for c in candidates]}
        monkeypatch.setattr(
            business_lookup,
            "get_places_client",
            lambda: GooglePlacesClient(
                "k", transport=httpx.MockTransport(lambda r: httpx.Response(200, json=places))
            ),
        )

    monkeypatch.setattr(
        website_discovery,
        "crawl",
        lambda url, renderer=None, region=None: website.crawl(
            url,
            transport=httpx.MockTransport(
                lambda r: (
                    httpx.Response(200, text=SITE, headers={"content-type": "text/html"})
                    if r.url.path in ("", "/")
                    else httpx.Response(404)
                )
            ),
            guard=lambda u: None,
            region=region,
        ),
    )
    monkeypatch.setattr(website_discovery, "geocode", lambda db, a, c: (GeocodeResult(0, 0, "x"), []))
    return use


def _project(client, **extra):
    body = {
        "name": "Acme audit",
        "business_name": "Acme Build",
        "address": "1 King St W, Toronto, ON",
        "country": "CA",
        "website_url": "https://acme.example.com/",
        **extra,
    }
    return client.post("/v1/projects", json=body).json()


def test_discover_auto_selects_links_and_starts_audit(client, db, enqueued, google):
    google(WRONG, RIGHT)
    project = _project(client)
    r = client.post(f"/v1/projects/{project['id']}/discover-business")
    assert r.status_code == 202
    job = run_job(db, enqueued[-1])
    assert job.status == "completed", job.steps
    assert [s["name"] for s in job.steps] == ["read_website", "match_business"]
    result = job.steps[1]["result"]
    assert result["status"] == "auto_selected" and result["manual_review_required"] is False
    assert "audit_job_id" in result and len(enqueued) == 2  # gbp_audit queued next

    d = client.get(f"/v1/projects/{project['id']}/discover-business").json()
    assert d["place_id"] == "ChIJright" and d["match_confidence"] >= 0.95
    assert d["candidates"][0]["place_id"] == "ChIJright"
    assert db.query(Business).filter_by(place_id="ChIJright").one().is_client


def test_discover_needs_review_then_user_selects(client, db, enqueued, google):
    google(WRONG)
    project = _project(client)
    client.post(f"/v1/projects/{project['id']}/discover-business", json={"then_audit": False})
    job = run_job(db, enqueued[-1])
    assert job.status == "completed"
    d = client.get(f"/v1/projects/{project['id']}/discover-business").json()
    assert d["status"] == "manual_review_required" and d["manual_review_required"] is True
    assert d["place_id"] is None and len(d["candidates"]) == 1

    bad = client.post(f"/v1/projects/{project['id']}/discover-business/select", json={"place_id": "ChIJnope"})
    assert bad.status_code == 422
    ok = client.post(
        f"/v1/projects/{project['id']}/discover-business/select",
        json={"place_id": "ChIJwrong", "then_audit": True},
    ).json()
    assert ok["status"] == "manually_selected" and ok["place_id"] == "ChIJwrong"
    assert len(enqueued) == 2  # audit started after the choice
    assert client.get(f"/v1/projects/{project['id']}").json()["place_id"] == "ChIJwrong"


def test_audit_refuses_to_guess(client, db, enqueued, google):
    google(WRONG)
    project = _project(client)
    client.post(f"/v1/projects/{project['id']}/gbp-audit")
    job = run_job(db, enqueued[-1])
    assert job.status == "failed"
    assert "Manual review required" in job.error
    assert db.get(Project, job.project_id).client_business is None  # nothing linked


def test_not_found_fails_discovery(client, db, enqueued, google):
    google()
    project = _project(client)
    client.post(f"/v1/projects/{project['id']}/discover-business")
    job = run_job(db, enqueued[-1])
    assert job.status == "failed"
    assert client.get(f"/v1/projects/{project['id']}/discover-business").json()["status"] == "not_found"
