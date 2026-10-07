"""Settings editable in the app: saved in the database, override .env, apply without a restart."""

from app.core import config
from app.core.config import get_settings
from app.models import AppSetting, SettingChange
from app.providers import get_places_client


def _setting(data, key):
    return next(x for g in data["groups"] for x in g["settings"] if x["key"] == key)


def test_change_applies_at_once_and_reset_restores_env(client, db):
    assert get_settings().keyword_cap == 10
    r = client.patch("/v1/settings", json={"values": {"KEYWORD_CAP": 8, "RANKING_MODE": "maps_only"}})
    assert r.status_code == 200 and sorted(r.json()["changed"]) == ["KEYWORD_CAP", "RANKING_MODE"]
    assert get_settings().keyword_cap == 8 and get_settings().ranking_mode == "maps_only"

    data = client.get("/v1/settings/editable").json()
    cap = _setting(data, "KEYWORD_CAP")
    assert cap["value"] == 8 and cap["env_value"] == 10 and cap["source"] == "app"
    assert data["history"][0]["key"] in ("KEYWORD_CAP", "RANKING_MODE")

    assert client.delete("/v1/settings/KEYWORD_CAP").json() == {"key": "KEYWORD_CAP", "reset": True}
    assert get_settings().keyword_cap == 10
    assert _setting(client.get("/v1/settings/editable").json(), "KEYWORD_CAP")["source"] == ".env"
    assert db.query(SettingChange).filter_by(key="KEYWORD_CAP").count() == 2  # changed, then reset


def test_same_value_is_not_a_change(client):
    assert client.patch("/v1/settings", json={"values": {"KEYWORD_CAP": 10}}).json()["changed"] == []


def test_invalid_values_are_refused(client):
    bad = [
        {"KEYWORD_CAP": 0},  # below the minimum
        {"KEYWORD_CAP": "ten"},
        {"RANKING_MODE": "fastest"},
        {"GOOGLE_DATA_TTL_DAYS": 45},  # Google allows at most 30 days
        {"DATABASE_URL": "sqlite://"},  # infrastructure stays in .env
        {"SERPAPI_KEY": "short"},
    ]
    for values in bad:
        r = client.patch("/v1/settings", json={"values": values})
        assert r.status_code == 422, values
    assert get_settings().keyword_cap == 10


def test_monthly_limit_above_free_tier_needs_confirmation(client):
    r = client.patch("/v1/settings", json={"values": {"QUOTA_SERPAPI_MONTHLY": 1000}})
    assert r.status_code == 409 and "may charge you" in r.json()["detail"]
    assert get_settings().quota_serpapi_monthly == 240
    ok = client.patch(
        "/v1/settings", json={"values": {"QUOTA_SERPAPI_MONTHLY": 1000}, "accept_charges": True}
    )
    assert ok.status_code == 200 and get_settings().quota_serpapi_monthly == 1000
    usage = next(u for u in client.get("/v1/usage").json() if u["sku"] == "serpapi_search")
    assert usage["monthly_limit"] == 1000  # the free-tier guard uses the new limit straight away


def test_api_keys_are_write_only(client, db):
    new_key = "AIzaNEWKEY-1234567890-xyz"
    r = client.patch("/v1/settings", json={"values": {"GOOGLE_API_KEY": new_key}})
    assert r.status_code == 200
    assert get_places_client()._api_key == new_key  # used right away
    everything = str(client.get("/v1/settings/editable").json()) + str(client.get("/v1/settings").json())
    assert new_key not in everything and "AIza…yz" in everything
    assert all(new_key not in (c.new_value or "") for c in db.query(SettingChange))  # history is masked
    client.delete("/v1/settings/GOOGLE_API_KEY")
    assert get_places_client()._api_key == "test-google-key"


def test_other_processes_pick_up_changes(db):
    """The worker reads the table on its own (here: a refresh after a direct database write)."""
    db.add(AppSetting(key="COMPETITOR_MAX", value="5"))
    db.commit()
    config.refresh_overrides()
    assert get_settings().competitor_max == 5
    db.add(
        AppSetting(key="PROVIDER_RETRIES", value="not a number")
    )  # a bad stored value never breaks the app
    db.commit()
    config.refresh_overrides()
    assert get_settings().provider_retries == 1


def test_restart_settings_are_flagged(client):
    r = client.patch("/v1/settings", json={"values": {"DEBUG": True, "COMPETITOR_DETAILS": False}})
    assert r.json()["restart_needed"] == ["DEBUG"]
    assert get_settings().competitor_details is False
