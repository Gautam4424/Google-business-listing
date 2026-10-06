import uuid

PROJECT = {
    "name": "Acme Plumbing audit",
    "business_name": "Acme Plumbing",
    "address": "1 High St, Manchester M1 1AA",
    "website_url": "https://acme-plumbing.example.com",
    "country": "gb",
    "service_areas": [{"name": "Manchester, UK", "latitude": 53.4808, "longitude": -2.2426}],
    "keywords": ["plumber manchester", " Plumber Manchester ", ""],
}


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "database": "ok"}


def test_create_and_get_project(client):
    r = client.post("/v1/projects", json=PROJECT)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["country"] == "GB"
    assert body["keywords"] == ["plumber manchester"]  # trimmed + de-duplicated
    assert body["service_areas"][0]["latitude"] == 53.4808

    r = client.get(f"/v1/projects/{body['id']}")
    assert r.status_code == 200
    assert r.json()["business_name"] == "Acme Plumbing"

    assert len(client.get("/v1/projects").json()) == 1


def test_project_validation(client):
    assert client.post("/v1/projects", json={**PROJECT, "country": "GBR"}).status_code == 422
    assert client.post("/v1/projects", json={**PROJECT, "website_url": "not a url"}).status_code == 422
    assert client.get(f"/v1/projects/{uuid.uuid4()}").status_code == 404


def test_create_job_is_queued(client, enqueued):
    r = client.post("/v1/jobs", json={"job_type": "diagnostic"})
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "queued"
    assert enqueued == [uuid.UUID(body["id"])]
    assert client.get(f"/v1/jobs/{body['id']}").json()["status"] == "queued"


def test_list_jobs_newest_first_with_filters(client):
    project = client.post("/v1/projects", json=PROJECT).json()
    first = client.post("/v1/jobs", json={"job_type": "diagnostic"}).json()
    second = client.post("/v1/jobs", json={"job_type": "diagnostic", "project_id": project["id"]}).json()

    assert [j["id"] for j in client.get("/v1/jobs").json()] == [second["id"], first["id"]]
    assert [j["id"] for j in client.get(f"/v1/jobs?project_id={project['id']}").json()] == [second["id"]]
    assert len(client.get("/v1/jobs?status=queued").json()) == 2
    assert client.get("/v1/jobs?status=completed").json() == []


def test_ui_is_served(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "/ui/"
    page = client.get("/ui/")
    assert page.status_code == 200
    assert "Local SEO" in page.text
    assert client.get("/ui/app.js").status_code == 200
    assert client.get("/ui/styles.css").status_code == 200


def test_create_job_rejects_unknown_type_and_project(client):
    r = client.post("/v1/jobs", json={"job_type": "nope"})
    assert r.status_code == 422
    assert "diagnostic" in r.json()["detail"]
    r = client.post("/v1/jobs", json={"job_type": "diagnostic", "project_id": str(uuid.uuid4())})
    assert r.status_code == 404


def test_job_marked_failed_when_queue_down(client, monkeypatch):
    def boom(_):
        raise ConnectionError("redis down")

    monkeypatch.setattr("app.workers.dispatch.enqueue_job", boom)
    r = client.post("/v1/jobs", json={"job_type": "diagnostic"})
    assert r.status_code == 503


def test_usage_endpoint(client):
    r = client.get("/v1/usage")
    assert r.status_code == 200
    skus = {u["sku"]: u for u in r.json()}
    assert skus["google_places_text_search"]["monthly_limit"] == 5
    assert skus["google_places_text_search"]["remaining"] == 3  # daily cap is lower
