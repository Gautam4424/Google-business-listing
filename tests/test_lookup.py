import httpx
import pytest

from app.models import Business
from app.providers.google_places import GooglePlacesClient
from app.services import business_lookup as bl

PASTED = (
    "Astaneh Construction 3080 Yonge St Ste 6060, Toronto, ON M4N 1S1, Canada Map of Astaneh Construction"
)

PLACE = {
    "id": "ChIJastaneh",
    "displayName": {"text": "Astaneh Construction"},
    "formattedAddress": "3080 Yonge St Suite 6060, Toronto, ON M4N 3N1, Canada",
    "addressComponents": [
        {"longText": "Toronto", "shortText": "Toronto", "types": ["locality", "political"]},
        {"longText": "Ontario", "shortText": "ON", "types": ["administrative_area_level_1", "political"]},
        {"longText": "Canada", "shortText": "CA", "types": ["country", "political"]},
    ],
    "location": {"latitude": 43.7280, "longitude": -79.4030},
    "googleMapsUri": "https://maps.google.com/?cid=1",
    "primaryTypeDisplayName": {"text": "General contractor"},
    "internationalPhoneNumber": "+1 416-555-0100",
    "websiteUri": "https://astaneh.example.com/?utm_source=gbp&utm_medium=organic&page=1",
}


@pytest.fixture
def places(monkeypatch):
    calls = []

    def use(handler):
        def wrapped(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return handler(request)

        monkeypatch.setattr(
            bl,
            "get_places_client",
            lambda: GooglePlacesClient("test-google-key", transport=httpx.MockTransport(wrapped)),
        )
        return calls

    return use


def test_clean_query_strips_copy_paste_noise():
    assert (
        bl.clean_query(PASTED) == "Astaneh Construction 3080 Yonge St Ste 6060, Toronto, ON M4N 1S1, Canada"
    )
    assert bl.clean_query("Call Experts 12 Main St") == "Call Experts 12 Main St"


def test_parser_splits_name_address_country_city():
    c = bl.parse_free_text(PASTED)
    assert c.business_name == "Astaneh Construction"
    assert c.address == "3080 Yonge St Ste 6060, Toronto, ON M4N 1S1, Canada"
    assert (c.country, c.city, c.region) == ("CA", "Toronto", "ON")
    assert c.place_id is None


def test_parser_uk_and_names_with_digits():
    c = bl.parse_free_text("7-Eleven Store 12B High Street, Manchester M1 1AA, United Kingdom")
    assert c.business_name == "7-Eleven Store"
    assert c.country == "GB"
    assert c.city == "Manchester M1 1AA"  # two segments: city keeps the postcode; fields stay editable


def test_clean_website_drops_tracking():
    assert bl.clean_website(PLACE["websiteUri"]) == "https://astaneh.example.com/?page=1"


def test_lookup_uses_google_and_caches(client, places):
    calls = places(lambda r: httpx.Response(200, json={"places": [PLACE, {**PLACE, "id": "ChIJother"}]}))

    r = client.post("/v1/lookup/business", json={"query": PASTED})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "google_places" and body["cached"] is False
    top = body["candidates"][0]
    assert top["place_id"] == "ChIJastaneh"
    assert top["country"] == "CA"
    assert top["service_area"] == "Toronto, ON"
    assert top["website_url"] == "https://astaneh.example.com/?page=1"
    assert top["phone"] == "+1 416-555-0100"
    assert len(body["candidates"]) == 2
    # Noise is removed before calling Google; the field mask asks for phone + website.
    assert b"Map of" not in calls[0].content
    assert "websiteUri" in calls[0].headers["X-Goog-FieldMask"]

    again = client.post("/v1/lookup/business", json={"query": PASTED}).json()
    assert again["cached"] is True
    assert len(calls) == 1  # second lookup used no quota

    usage = {u["sku"]: u for u in client.get("/v1/usage").json()}
    assert usage["google_places_text_search_enterprise"]["month_count"] == 1


def test_lookup_falls_back_when_google_finds_nothing(client, places):
    places(lambda r: httpx.Response(200, json={}))
    body = client.post("/v1/lookup/business", json={"query": PASTED}).json()
    assert body["source"] == "parser"
    assert "no matching business" in body["warning"]
    assert body["candidates"][0]["business_name"] == "Astaneh Construction"


def test_lookup_falls_back_on_google_error_without_leaking_key(client, places):
    places(lambda r: httpx.Response(403, json={"error": {"message": "API key test-google-key not valid"}}))
    body = client.post("/v1/lookup/business", json={"query": PASTED}).json()
    assert body["source"] == "parser"
    assert "test-google-key" not in body["warning"]


def test_lookup_falls_back_without_key(client, monkeypatch):
    monkeypatch.setattr(bl, "get_places_client", lambda: None)
    body = client.post("/v1/lookup/business", json={"query": PASTED}).json()
    assert body["source"] == "parser"
    assert body["candidates"][0]["country"] == "CA"


def test_lookup_validates_input(client):
    assert client.post("/v1/lookup/business", json={"query": "ab"}).status_code == 422


def test_project_with_place_id_links_one_business(client, db):
    payload = {
        "name": "Astaneh audit",
        "business_name": "Astaneh Construction",
        "website_url": "https://www.astaneh.example.com/",
        "country": "CA",
        "place_id": "ChIJastaneh",
    }
    first = client.post("/v1/projects", json=payload).json()
    second = client.post("/v1/projects", json={**payload, "name": "Second audit"}).json()

    assert first["place_id"] == second["place_id"] == "ChIJastaneh"
    assert first["client_business_id"] == second["client_business_id"]
    business = db.query(Business).one()
    assert business.is_client and business.domain == "astaneh.example.com"
    assert client.get(f"/v1/projects/{first['id']}").json()["place_id"] == "ChIJastaneh"
