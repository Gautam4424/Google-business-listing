"""End to end with every provider mocked: create project -> full audit (with rankings) -> report.

Covers the `completed` path and the `partial_success` path (Maps searches failing).
"""

import io
import json
import zipfile

import httpx
import pytest

from app.providers.google_places import GooglePlacesClient
from app.providers.serpapi import SerpApiClient
from app.services import competitors, rankings
from app.workers.pipeline import run_job
from tests.test_audit import audit_env  # noqa: F401  (fixture)

CLIENT = {"title": "Acme Build", "place_id": "ChIJacme", "data_cid": "1", "rating": 4.8, "reviews": 57,
          "type": "General contractor", "types": ["General contractor"]}  # fmt: skip
RIVALS = [
    {"title": f"Rival {i}", "place_id": f"ChIJr{i}", "data_cid": str(100 + i), "rating": 4.9,
     "reviews": 200 + i, "type": "General contractor",
     "types": ["General contractor", "Kitchen remodeler", "Deck builder"]}
    for i in range(1, 4)
]  # fmt: skip


@pytest.fixture
def providers(monkeypatch, audit_env):  # noqa: F811
    audit_env(lambda r: httpx.Response(500))  # Google profile + website; SerpApi reviews not used
    state = {"fail_maps": False, "searches": 0, "details": 0}

    def serp(request: httpx.Request) -> httpx.Response:
        p = request.url.params
        if request.url.path == "/account.json":
            return httpx.Response(200, json={"total_searches_left": 200, "plan_renewal_date": "2026-11-06"})
        state["searches"] += 1
        if p["engine"] == "google":
            places = [{"position": i, "title": x["title"], "place_id": x["data_cid"], "type": x["type"],
                       "rating": x["rating"], "reviews": x["reviews"]}
                      for i, x in enumerate([RIVALS[0], CLIENT, RIVALS[1]], start=1)]  # fmt: skip
            return httpx.Response(200, json={"local_results": {"places": places}})
        if state["fail_maps"]:
            return httpx.Response(500, json={"error": "Google hiccup"})
        rows = [{**x, "position": i} for i, x in enumerate([*RIVALS, CLIENT], start=1)]
        return httpx.Response(200, json={"local_results": rows})

    def details(request: httpx.Request) -> httpx.Response:
        state["details"] += 1
        pid = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(
            200,
            json={
                "id": pid,
                "rating": 4.9,
                "userRatingCount": 210,
                "reviews": [
                    {"rating": 5, "text": {"text": "They were on time and very tidy.", "languageCode": "en"}}
                ],
            },
        )

    monkeypatch.setattr(rankings, "get_serpapi_client",
                        lambda: SerpApiClient("k", transport=httpx.MockTransport(serp)))  # fmt: skip
    monkeypatch.setattr(competitors, "get_places_client",
                        lambda: GooglePlacesClient("g", transport=httpx.MockTransport(details)))  # fmt: skip
    return state


def _create(client):
    r = client.post("/v1/projects", json={
        "name": "Acme e2e", "business_name": "Acme Build", "country": "CA", "place_id": "ChIJacme",
        "service_areas": [{"name": "Toronto, ON", "latitude": 43.65, "longitude": -79.38}],
    })  # fmt: skip
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _steps(job):
    return {s["name"]: s["status"] for s in job.steps}


def test_create_full_audit_report(client, db, enqueued, providers):
    pid = _create(client)
    r = client.post(f"/v1/projects/{pid}/full-audit", json={"rankings": True, "mode": "full"})
    assert r.status_code == 202
    assert client.post(f"/v1/projects/{pid}/full-audit").status_code == 409  # double click: refused
    job = run_job(db, enqueued[-1])
    assert job.status == "completed", json.dumps(job.steps, default=str)
    assert set(_steps(job).values()) == {"succeeded"}

    active = sum(1 for k in client.get(f"/v1/projects/{pid}/keywords").json()["keywords"] if k["active"])
    assert active and providers["searches"] == active * 2  # exactly what the estimate promised
    ranks = client.get(f"/v1/projects/{pid}/rankings").json()
    assert ranks["latest"]["summary"]["in_local_pack"] == active  # client is #2 in every Local Pack
    comp = client.get(f"/v1/projects/{pid}/competitors").json()
    assert [c["business_name"] for c in comp["competitors"]][:1] == ["Rival 1"]
    assert providers["details"] == 3
    gaps = client.get(f"/v1/projects/{pid}/gaps").json()["gaps"]
    assert any(g["gap_type"] == "category" and "Kitchen remodeler" in g["title"] for g in gaps)

    html = client.get(f"/v1/projects/{pid}/report").text
    assert "Acme Build" in html and "Visibility score" in html and "Rival 1" in html
    z = zipfile.ZipFile(io.BytesIO(client.get(f"/v1/projects/{pid}/report?format=csv").content))
    assert len(z.read("rankings.csv").decode("utf-8-sig").splitlines()) == active + 1


def test_full_audit_partial_success_keeps_everything_else(client, db, enqueued, providers):
    providers["fail_maps"] = True
    pid = _create(client)
    client.post(f"/v1/projects/{pid}/full-audit", json={"rankings": True})
    job = run_job(db, enqueued[-1])
    steps = _steps(job)
    assert job.status == "partial_success"
    assert steps["collect_rankings"] == "partial" and steps["fetch_profile"] == "succeeded"
    assert steps["build_report"] == "succeeded"
    usage = {u["sku"]: u["month_count"] for u in client.get("/v1/usage").json()}
    active = sum(1 for k in client.get(f"/v1/projects/{pid}/keywords").json()["keywords"] if k["active"])
    assert usage["serpapi_search"] == active  # failed Maps searches (retried once) are not counted
    assert client.get(f"/v1/projects/{pid}/report").status_code == 200
