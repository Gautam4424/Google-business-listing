"""`diagnostic` job: checks the database and that each configured API key works.

Google Places check costs 1 Text Search call (counted by the quota guard).
SerpApi check uses the free account endpoint (no search used).
"""

from datetime import timedelta

from sqlalchemy import text

from app.providers import get_places_client, get_serpapi_client
from app.providers.google_places import PROVIDER as PLACES
from app.providers.serpapi import PROVIDER as SERPAPI
from app.services import quota
from app.services.provider_cache import store_response
from app.workers.pipeline import Step, StepContext, StepSkipped, register

DEFAULT_PLACES_QUERY = "Googleplex 1600 Amphitheatre Parkway Mountain View"


def check_database(ctx: StepContext) -> dict:
    ctx.db.execute(text("SELECT 1"))
    return {"database": "ok"}


def check_google_places(ctx: StepContext) -> dict:
    client = get_places_client()
    if client is None:
        raise StepSkipped("GOOGLE_API_KEY is not set")
    query = ctx.job.params.get("places_query", DEFAULT_PLACES_QUERY)
    field_mask = "places.id,places.displayName"

    quota.consume(ctx.db, "google_places_text_search")
    data = client.search_text(query, field_mask=field_mask, page_size=1)
    store_response(
        ctx.db,
        PLACES,
        "places:searchText",
        {"textQuery": query, "fieldMask": field_mask},
        data,
        200,
        ttl=timedelta(days=ctx.settings.google_data_ttl_days),
    )
    places = data.get("places", [])
    if not places:
        return {"key": "valid", "query": query, "match": None}
    top = places[0]
    return {
        "key": "valid",
        "query": query,
        "place_id": top["id"],
        "name": top.get("displayName", {}).get("text"),
    }


def check_serpapi(ctx: StepContext) -> dict:
    client = get_serpapi_client()
    if client is None:
        raise StepSkipped("SERPAPI_KEY is not set")
    data = client.account()
    store_response(ctx.db, SERPAPI, "account.json", {}, _account_summary(data), 200, ttl=None)
    return {"key": "valid", **_account_summary(data)}


def _account_summary(data: dict) -> dict:
    keys = ("plan_name", "searches_per_month", "plan_searches_left", "this_month_usage")
    return {k: data.get(k) for k in keys}


register(
    "diagnostic",
    [
        Step("check_database", check_database, required=True),
        Step("check_google_places", check_google_places, required=False),
        Step("check_serpapi", check_serpapi, required=False),
    ],
)
