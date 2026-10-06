from datetime import date, timedelta

import pytest

from app.core.security import redact
from app.services import quota
from app.services.provider_cache import get_cached, request_hash, store_response

SKU = "google_places_text_search"  # test limits: 5/month, 3/day (conftest)


def test_quota_daily_cap(db):
    day = date(2026, 10, 7)
    for _ in range(3):
        quota.consume(db, SKU, day=day)
    with pytest.raises(quota.QuotaExceeded, match="daily"):
        quota.consume(db, SKU, day=day)
    assert quota.get_usage(db, SKU, day=day).day_count == 3


def test_quota_monthly_cap_across_days(db):
    quota.consume(db, SKU, n=3, day=date(2026, 10, 1))
    quota.consume(db, SKU, n=2, day=date(2026, 10, 2))
    with pytest.raises(quota.QuotaExceeded, match="monthly"):
        quota.consume(db, SKU, day=date(2026, 10, 3))
    # New month resets.
    quota.consume(db, SKU, day=date(2026, 11, 1))
    assert quota.get_usage(db, SKU, day=date(2026, 11, 1)).month_count == 1


def test_quota_unknown_sku(db):
    with pytest.raises(KeyError):
        quota.consume(db, "nope")


def test_request_hash_ignores_key_order():
    assert request_hash("p", "e", {"a": 1, "b": 2}) == request_hash("p", "e", {"b": 2, "a": 1})
    assert request_hash("p", "e", {"a": 1}) != request_hash("p", "e", {"a": 2})


def test_cache_hit_miss_and_expiry(db):
    params = {"textQuery": "x"}
    store_response(db, "google_places", "searchText", params, {"ok": 1}, 200, ttl=timedelta(days=30))
    assert get_cached(db, "google_places", "searchText", params, max_age=timedelta(days=1)).response == {
        "ok": 1
    }
    assert (
        get_cached(db, "google_places", "searchText", {"textQuery": "y"}, max_age=timedelta(days=1)) is None
    )

    store_response(db, "google_places", "searchText", {"q": "old"}, {"ok": 1}, 200, ttl=timedelta(seconds=-1))
    assert get_cached(db, "google_places", "searchText", {"q": "old"}, max_age=timedelta(days=1)) is None

    store_response(db, "google_places", "searchText", {"q": "err"}, {"error": 1}, 500, ttl=timedelta(days=1))
    assert get_cached(db, "google_places", "searchText", {"q": "err"}, max_age=timedelta(days=1)) is None


def test_redact():
    assert redact("GET https://x?api_key=test-serpapi-key failed") == "GET https://x?api_key=*** failed"
