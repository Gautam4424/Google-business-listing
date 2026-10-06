"""`gbp_audit` job for a project: GBP profile, top reviews, and website social links + offerings.

Cost per run:
  - 1 Google Place Details call (incl. reviews)            -> google_places_details quota
  - 0 SerpApi searches by default; 2 only when top 10 reviews are requested (params.top10_reviews)
  - 0-1 Google Text Search, only if the project has no place_id yet
  - website crawl: free (+ 1 geocode if the site has an address but no coordinates)
"""

from datetime import timedelta
from urllib.parse import urlsplit

from sqlalchemy import delete, select

from app.models import BusinessLocation, GbpProfile, GbpReview, Project, Service
from app.models.base import utcnow
from app.providers import get_places_client, get_serpapi_client
from app.providers.base import ProviderError
from app.providers.google_places import PROVIDER as PLACES
from app.providers.serpapi import PROVIDER as SERPAPI
from app.services import quota
from app.services.business_lookup import clean_website
from app.services.discovery import ManualReviewRequired, discover, verify_linked
from app.services.matching import AUTO_SELECT
from app.services.place_profile import (
    DETAILS_FIELD_MASK,
    category_offerings,
    places_reviews,
    profile_fields,
    serpapi_reviews,
)
from app.services.provider_cache import store_response
from app.services.review_analysis import analyze_project_reviews
from app.services.website_discovery import discover_website, latest_website_profile
from app.workers.pipeline import Step, StepContext, StepSkipped, register

TOP_REVIEWS = 10


def _project(ctx: StepContext) -> Project:
    project = ctx.db.get(Project, ctx.job.project_id) if ctx.job.project_id else None
    if project is None:
        raise ValueError("gbp_audit needs a project_id")
    return project


def _domain(url: str | None) -> str | None:
    host = urlsplit(url).hostname if url else None
    return host.removeprefix("www.") if host else None


def resolve_place(ctx: StepContext) -> dict:
    """Use the linked GBP, or find and verify it (Phase 3). Never audits an unverified guess."""
    project = _project(ctx)
    if project.client_business and project.client_business.place_id:
        return {"place_id": project.client_business.place_id, "how": "already linked"}

    if project.website_url and latest_website_profile(ctx.db, project.id) is None:
        try:  # the website is the independent evidence the match is scored against
            discover_website(ctx.db, project, project.website_url)
        except Exception as exc:
            ctx.db.rollback()
            ctx.data["website_note"] = f"Website not read before matching: {exc}"
    decision = discover(ctx.db, project)
    if decision.status == "not_found":
        raise LookupError(f"Business not found on Google ({decision.reason})")
    if decision.status == "manual_review_required":
        raise ManualReviewRequired(
            f"Manual review required: {decision.reason}. Choose the right business on the project page."
        )
    return {
        "place_id": decision.place_id,
        "how": "found and verified automatically",
        "match_confidence": decision.match_confidence,
        "match_reasons": decision.match_reasons,
    }


def fetch_profile(ctx: StepContext) -> dict:
    project = _project(ctx)
    business = project.client_business
    client = get_places_client()
    if client is None:
        raise RuntimeError("GOOGLE_API_KEY is not set")

    quota.consume(ctx.db, "google_places_details")
    place = client.place_details(business.place_id, DETAILS_FIELD_MASK)
    raw = store_response(
        ctx.db, PLACES, "places:get", {"place_id": business.place_id, "fieldMask": DETAILS_FIELD_MASK},
        place, 200, ttl=timedelta(days=ctx.settings.google_data_ttl_days),
    )  # fmt: skip
    ctx.data["place"] = place
    now = utcnow()
    provenance = {
        "source": "google_places", "source_url": "places:get", "raw_response_id": raw.id,
        "collected_at": now, "last_verified_at": now,
    }  # fmt: skip

    fields = profile_fields(place)
    fields["website_url"] = clean_website(fields["website_url"])
    ctx.db.add(GbpProfile(business_id=business.id, last_checked_at=now, **fields, **provenance))

    business.name = fields["business_name"] or business.name
    business.domain = _domain(fields["website_url"]) or business.domain
    location = ctx.db.scalar(select(BusinessLocation).where(BusinessLocation.business_id == business.id))
    if location is None:
        location = BusinessLocation(business_id=business.id, **provenance)
        ctx.db.add(location)
    loc = place.get("location") or {}
    location.formatted_address = place.get("formattedAddress")
    location.latitude, location.longitude = loc.get("latitude"), loc.get("longitude")
    location.last_verified_at = now

    ctx.db.execute(delete(Service).where(Service.project_id == project.id, Service.source == "gbp_category"))
    for name, confidence in category_offerings(place):
        ctx.db.add(
            Service(
                project_id=project.id, business_id=business.id, service_name=name,
                normalized_name=name.lower(), confidence_score=confidence,
                **{**provenance, "source": "gbp_category"},
            )
        )  # fmt: skip
    ctx.db.commit()
    return {
        "name": fields["business_name"],
        "category": fields["primary_category"],
        "rating": fields["rating"],
        "review_count": fields["review_count"],
        "website": fields["website_url"],
        "reviews_from_google_api": len(place.get("reviews", [])),
    }


def fetch_reviews(ctx: StepContext) -> dict:
    project = _project(ctx)
    business = project.client_business
    reviews, note = [], None

    # SerpApi credits are kept for ranking checks; top 10 reviews only when asked for (2 credits).
    want_top10 = ctx.job.params.get("top10_reviews", ctx.settings.serpapi_reviews_enabled)
    client = get_serpapi_client() if want_top10 else None
    if not want_top10:
        note = "Top 10 via SerpApi is off to save credits"
    elif client is None:
        note = "SERPAPI_KEY not set"
    else:
        try:
            base = {"engine": "google_maps_reviews", "place_id": business.place_id, "hl": project.language,
                    "sort_by": "qualityScore"}  # fmt: skip
            quota.consume(ctx.db, "serpapi_search")
            page = client.search(base)
            store_response(ctx.db, SERPAPI, "google_maps_reviews", base, page, 200, ttl=timedelta(days=30))
            items = page.get("reviews", [])
            token = (page.get("serpapi_pagination") or {}).get("next_page_token")
            if len(items) < TOP_REVIEWS and token:
                more_params = {**base, "next_page_token": token, "num": TOP_REVIEWS - len(items)}
                quota.consume(ctx.db, "serpapi_search")
                more = client.search(more_params)
                store_response(
                    ctx.db, SERPAPI, "google_maps_reviews", more_params, more, 200, ttl=timedelta(days=30)
                )
                items += more.get("reviews", [])
            reviews = serpapi_reviews(items[:TOP_REVIEWS])
        except Exception as exc:  # fall back to the Google API's reviews below
            ctx.db.rollback()
            if isinstance(exc, ProviderError) and exc.status_code in (401, 403):
                quota.release(ctx.db, "serpapi_search")  # rejected key: SerpApi does not charge
            note = f"SerpApi unavailable ({type(exc).__name__}: {exc})"

    if not reviews:
        reviews = places_reviews(ctx.data.get("place", {}))
        if reviews:
            note = (note + "; " if note else "") + "Google's API returns at most 5 reviews"

    used_source = reviews[0]["source"] if reviews else None
    now = utcnow()
    ctx.db.execute(delete(GbpReview).where(GbpReview.business_id == business.id))
    for r in reviews:
        source = r.pop("source")
        ctx.db.add(
            GbpReview(
                business_id=business.id, source=source, collected_at=now, last_verified_at=now,
                source_url="places:get" if source == "google_places" else "google_maps_reviews", **r,
            )
        )  # fmt: skip
    ctx.db.commit()
    return {"count": len(reviews), "source": used_source, "note": note}


def crawl_website(ctx: StepContext) -> dict:
    project = _project(ctx)
    business = project.client_business
    profile = ctx.db.scalar(
        select(GbpProfile).where(GbpProfile.business_id == business.id).order_by(GbpProfile.created_at.desc())
    )
    website = (profile.website_url if profile else None) or project.website_url
    if not website:
        raise StepSkipped("No website on the Google profile or the project")
    _, summary = discover_website(ctx.db, project, website, business)
    return summary


def analyze_reviews(ctx: StepContext) -> dict:
    """Sentiment, tags, themes, mentioned services (after the website so its offerings can be matched)."""
    return analyze_project_reviews(ctx.db, _project(ctx))


def verify_match(ctx: StepContext) -> dict:
    """Score the linked profile against the website now that both are fresh (brief: match_confidence)."""
    project = _project(ctx)
    business = project.client_business
    profile = ctx.db.scalar(
        select(GbpProfile).where(GbpProfile.business_id == business.id).order_by(GbpProfile.created_at.desc())
    )
    if profile is None:
        raise StepSkipped("No Google profile to verify")
    location = ctx.db.scalar(select(BusinessLocation).where(BusinessLocation.business_id == business.id))
    score = verify_linked(ctx.db, project, profile, location)
    result = {
        "match_confidence": score.match_confidence,
        "match_reasons": score.match_reasons,
        "mismatch_reasons": score.mismatch_reasons,
    }
    if score.match_confidence < AUTO_SELECT:
        result["note"] = "Low confidence: check this is the right Google listing"
    return result


register(
    "gbp_audit",
    [
        Step("resolve_place", resolve_place, required=True),
        Step("fetch_profile", fetch_profile, required=True),
        Step("fetch_reviews", fetch_reviews, required=False),
        Step("crawl_website", crawl_website, required=False),
        Step("analyze_reviews", analyze_reviews, required=False),
        Step("verify_match", verify_match, required=False),
    ],
)
