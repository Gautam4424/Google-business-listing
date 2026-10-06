import uuid

import httpx
import pytest

from app.models import Keyword, Project, ProjectService, RankingRun, Service
from app.providers.google_places import GooglePlacesClient
from app.services import keywords as kw
from app.services import service_catalog as sc

AREA = {"name": "Point Piper, NSW", "latitude": -33.867, "longitude": 151.25}
OFFERINGS = [
    ("Plumber", "gbp_category", 0.95),
    ("Home goods store", "gbp_category", 0.7),
    ("General Plumbing", "website_service_page", 0.8),
    ("General Plumbing Sydney", "website_service_page", 0.8),
    ("Gas Fitting", "website_service_page", 0.8),
    ("Gas Fittings", "website_service_page", 0.8),
    ("Blocked Drains", "website_service_page", 0.8),
    ("Blocked Kitchen Sink Sydney", "website_service_page", 0.8),
    ("Gas Leak Detection Sydney", "website_service_page", 0.8),
    ("Shower Repairs Sydney", "website_service_page", 0.8),
    ("Schools & Day Care Centres", "website_service_page", 0.8),
    ("Schools In Sydney", "website_service_page", 0.8),
    ("Hospitality", "website_service_page", 0.8),
    ("Burst pipe repairs", "review_topic", 0.5),
    ("Emergency Plumbing", "website_service_page", 0.8),
    ("Emergency plumbing", "review_topic", 0.5),
]


def _project(db, areas=None, keywords=None) -> Project:
    p = Project(
        name="Proximity audit",
        input_business_name="Proximity Plumbing",
        input_address="2A Wentworth St, Point Piper NSW 2027",
        country="AU",
        language="en",
        service_areas=[AREA] if areas is None else areas,
        user_keywords=keywords or [],
    )
    db.add(p)
    db.commit()
    for name, source, conf in OFFERINGS:
        db.add(Service(project_id=p.id, service_name=name, normalized_name=name.lower(), source=source,
                       confidence_score=conf))  # fmt: skip
    db.commit()
    return p


def _names(db, project, kind="service"):
    return {s.name for s in sc.services_by_kind(db, project).get(kind, [])}


# ---------- service catalog ----------


def test_catalog_merges_cleans_and_classifies(db):
    p = _project(db)
    summary = sc.build_catalog(db, p)
    services = _names(db, p)
    assert "General plumbing" in services and "General Plumbing" in services or "General Plumbing" in services
    assert not any("Sydney" in s for s in services)  # repeated trailing place name stripped
    assert len([s for s in services if s.lower().startswith("gas fitting")]) == 1  # singular/plural merged
    assert {"Schools & Day Care Centres", "Hospitality"} <= _names(db, p, "customer_type")
    assert _names(db, p, "generic") == {"Home goods store"}
    assert "sydney" in summary["location_words_removed"]


def test_catalog_scores_agreement_and_preselects(db):
    p = _project(db)
    sc.build_catalog(db, p)
    rows = {s.name: s for s in sc.services_by_kind(db, p)["service"]}
    emergency = next(r for n, r in rows.items() if n.lower() == "emergency plumbing")
    assert {s["source"] for s in emergency.sources} == {"website_service_page", "review_topic"}
    assert emergency.score > rows["Gas Fitting"].score  # found in two source families
    selected = [r.name for r in rows.values() if r.selected]
    assert "Plumber" in selected and len(selected) == sc.DEFAULT_SELECTED  # primary category always ticked


def test_rebuild_keeps_user_choices(db):
    p = _project(db)
    sc.build_catalog(db, p)
    gas = db.query(ProjectService).filter_by(project_id=p.id, normalized_name="gas fitting").one()
    gas.selected, gas.name, gas.user_edited = True, "Gas fitter", True
    sc.add_user_service(db, p, "Hot water systems")
    db.commit()
    sc.build_catalog(db, p)
    gas = db.query(ProjectService).filter_by(project_id=p.id, normalized_name="gas fitting").one()
    assert gas.selected and gas.name == "Gas fitter"
    assert "Hot water systems" in _names(db, p)


def test_quality_rules(db):
    p = _project(db)
    for name, source, conf in [
        ("Pizza Restaurant", "gbp_category", 0.95),
        ("Clearing of Blocked Drains Fast", "website_service_page", 0.8),
    ]:
        db.add(Service(project_id=p.id, service_name=name, normalized_name=name.lower(), source=source,
                       confidence_score=conf))  # fmt: skip
    db.commit()
    sc.build_catalog(db, p)
    assert "Pizza Restaurant" in _names(db, p)  # a Google category is never a "customer type"
    assert "Schools" in _names(db, p, "customer_type")  # "Schools In Sydney" -> "Schools"
    rows = {s.name: s.score for s in sc.services_by_kind(db, p)["service"]}
    assert rows["Blocked Drains"] > rows["Clearing of Blocked Drains Fast"]


# ---------- keywords ----------


def test_generate_brief_patterns_with_location_settings(db, monkeypatch):
    p = _project(db, keywords=["24 hour plumber point piper"])
    sc.build_catalog(db, p)
    monkeypatch.setattr(kw.get_settings(), "keyword_cap", 100)
    result = kw.generate(db, p)
    rows = db.query(Keyword).filter_by(project_id=p.id).all()
    texts = {k.keyword for k in rows}
    assert {"plumber in Point Piper", "plumber near me", "plumber Point Piper"} <= texts
    assert "24 hour plumber point piper" in texts  # the user's own keyword
    k = next(k for k in rows if k.keyword == "plumber in Point Piper")
    assert (k.location_name, k.latitude, k.longitude, k.language, k.country, k.device, k.pattern) == (
        "Point Piper, NSW",
        -33.867,
        151.25,
        "en",
        "AU",
        "mobile",
        "in_city",
    )
    assert result["created"] == len(rows) == 1 + 3 * sc.DEFAULT_SELECTED


def test_cap_activates_core_services_first(db, monkeypatch):
    p = _project(db, keywords=["my own keyword"])
    sc.build_catalog(db, p)
    monkeypatch.setattr(kw.get_settings(), "keyword_cap", 6)
    preview = kw.generate(db, p)
    active = db.query(Keyword).filter_by(project_id=p.id, active=True).all()
    assert len(active) == 6 == preview["active"]
    assert preview["serpapi_credits_per_ranking_run"] == 12
    assert {k.pattern for k in active} == {"user", "in_city"}  # own keyword + "in {city}" for 5 services

    extra = db.query(Keyword).filter_by(project_id=p.id, active=False).first()
    with pytest.raises(kw.KeywordCapReached):
        kw.set_active(db, p, extra, True)
    kw.set_active(db, p, active[-1], False)
    kw.set_active(db, p, extra, True)  # room again


def test_regenerate_keeps_flags_and_history(db, monkeypatch):
    p = _project(db)
    sc.build_catalog(db, p)
    kw.generate(db, p)
    k = db.query(Keyword).filter_by(project_id=p.id, keyword="plumber in Point Piper").one()
    kw.set_active(db, p, k, False)
    gas = (
        db.query(ProjectService)
        .filter_by(project_id=p.id, selected=True)
        .filter(ProjectService.name != "Plumber")
        .first()
    )
    old = db.query(Keyword).filter_by(project_id=p.id, project_service_id=gas.id, pattern="in_city").one()
    db.add(RankingRun(project_id=p.id, keyword_id=old.id, provider="serpapi", result_type="local_pack",
                      country="AU", language="en", device="mobile"))  # fmt: skip
    gas.selected = False
    db.commit()

    kw.generate(db, p)
    assert db.get(Keyword, k.id).active is False  # user's switch kept
    kept = db.get(Keyword, old.id)
    assert kept is not None and kept.active is False  # deselected but has history: kept, inactive
    assert (
        db.query(Keyword).filter_by(project_id=p.id, project_service_id=gas.id, pattern="near_me").count()
        == 0
    )


def test_area_coordinates_from_places_then_pin(db, monkeypatch):
    calls = []

    def places(request):
        calls.append(request)
        return httpx.Response(200, json={"places": [{"location": {"latitude": -33.89, "longitude": 151.27},
                                                     "formattedAddress": "Bondi NSW 2026"}]})  # fmt: skip

    monkeypatch.setattr(
        kw, "get_places_client", lambda: GooglePlacesClient("k", transport=httpx.MockTransport(places))
    )
    p = _project(db, areas=[AREA, {"name": "Bondi, NSW"}])
    kw.ensure_area_coordinates(db, p)
    kw.ensure_area_coordinates(db, p)
    bondi = p.service_areas[1]
    assert (bondi["latitude"], bondi["longitude"]) == (-33.89, 151.27)
    assert len(calls) == 1  # first area already had coordinates; second call was cached / not needed


# ---------- endpoints ----------


def test_endpoints(client, db):
    project = client.post(
        "/v1/projects",
        json={"name": "Acme", "business_name": "Acme Plumbing", "country": "AU",
              "service_areas": [AREA], "keywords": ["acme plumbing"]},
    ).json()  # fmt: skip
    pid = uuid.UUID(project["id"])
    for name, source, conf in OFFERINGS[:8]:
        db.add(Service(project_id=pid, service_name=name, normalized_name=name.lower(), source=source,
                       confidence_score=conf))  # fmt: skip
    db.commit()

    services = client.post(f"/v1/projects/{pid}/services/refresh").json()
    assert services["generic"][0]["name"] == "Home goods store"
    sid = next(s["id"] for s in services["services"] if s["name"] == "Blocked Drains")
    assert client.patch(f"/v1/projects/{pid}/services/{sid}", json={"selected": True}).json()["selected"]
    assert client.post(f"/v1/projects/{pid}/services", json={"name": "Hot water"}).status_code == 201
    gid = services["generic"][0]["id"]
    assert client.patch(f"/v1/projects/{pid}/services/{gid}", json={"selected": True}).status_code == 422

    out = client.post(f"/v1/projects/{pid}/keywords/generate").json()
    assert out["preview"]["active"] <= out["preview"]["cap"] == 10
    assert any(k["keyword"] == "blocked drains in Point Piper" for k in out["keywords"])
    new = client.post(f"/v1/projects/{pid}/keywords", json={"keyword": "drain unblocking bondi"}).json()
    assert new["source"] == "user" and new["location_name"] == "Point Piper, NSW"
    assert client.delete(f"/v1/projects/{pid}/keywords/{new['id']}").status_code == 204

    on = [k for k in client.get(f"/v1/projects/{pid}/keywords").json()["keywords"] if not k["active"]]
    if out["preview"]["active"] == 10 and on:
        r = client.patch(f"/v1/projects/{pid}/keywords/{on[0]['id']}", json={"active": True})
        assert r.status_code == 422 and "limit" in r.json()["detail"]

    areas = client.put(f"/v1/projects/{pid}/service-areas", json=[AREA]).json()
    assert areas[0]["latitude"] == -33.867
    assert client.put(f"/v1/projects/{pid}/service-areas", json=[]).status_code == 422
