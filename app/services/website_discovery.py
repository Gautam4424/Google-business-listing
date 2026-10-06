"""Crawl the business website and save what it says: NAP (+ coordinates), social profiles, offerings."""

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Business, BusinessLocation, Project, Service, WebsiteProfile
from app.models.base import utcnow
from app.services import browser
from app.services.geocode import geocode
from app.services.nap import haversine_m
from app.services.website import USER_AGENT, check_public_url, crawl


def _renderer():
    if not get_settings().browser_fallback:
        return None
    return lambda url: browser.render(url, check_public_url, USER_AGENT)


def discover_website(
    db: Session, project: Project, website_url: str, business: Business | None = None
) -> tuple[WebsiteProfile, dict]:
    result = crawl(website_url, renderer=_renderer(), region=project.country)
    if not result.pages_fetched:
        reasons = result.errors or [f"blocked by robots.txt: {u}" for u in result.blocked] or ["no pages"]
        raise RuntimeError("Could not read the website: " + "; ".join(reasons))

    nap = result.nap
    notes = list(result.notes) + nap.issues
    lat, lng, geo_source = nap.latitude, nap.longitude, ("schema" if nap.latitude is not None else None)
    if nap.multi_location:
        notes.append(
            f"The website lists several locations ({len(nap.all_addresses)} addresses, "
            f"{len(nap.all_phones_e164)} phones): the Google listing is compared against all of them"
        )
    if lat is None and nap.address and not nap.multi_location:
        geo, geo_notes = geocode(db, nap.address, project.country)
        notes += geo_notes
        if geo:
            lat, lng, geo_source = geo.latitude, geo.longitude, geo.source

    now = utcnow()
    profile = WebsiteProfile(
        project_id=project.id,
        url=website_url,
        business_name=nap.name,
        phone=nap.phone,
        phone_e164=nap.phone_e164,
        address=nap.address,
        address_parts=nap.address_parts,
        latitude=lat,
        longitude=lng,
        geocode_source=geo_source,
        nap_sources={
            **nap.sources,
            "all": {"addresses": nap.all_addresses, "phones_e164": nap.all_phones_e164},
        },
        social_profiles=result.social_profiles or None,
        pages_fetched=result.pages_fetched,
        rendered_with_browser=result.rendered_with_browser,
        notes=notes or None,
        source="website",
        source_url=website_url,
        collected_at=now,
        last_verified_at=now,
    )
    db.add(profile)

    db.execute(delete(Service).where(Service.project_id == project.id, Service.source.like("website_%")))
    for name, source, url, confidence in result.offerings:
        db.add(
            Service(
                project_id=project.id,
                business_id=business.id if business else None,
                service_name=name,
                normalized_name=name.lower(),
                source=source,
                source_url=url,
                confidence_score=confidence,
                collected_at=now,
                last_verified_at=now,
            )
        )
    if business is not None:
        business.social_profiles = result.social_profiles or None
    db.commit()

    pin = update_pin_distance(db, business, profile) if business else None
    summary = {
        "pages_read": len(result.pages_fetched),
        "rendered_with_browser": result.rendered_with_browser,
        "name": nap.name,
        "phone": nap.phone,
        "address": nap.address,
        "coordinates_from": geo_source,
        "social_profiles": len(result.social_profiles),
        "offerings": len(result.offerings),
        "sitemap_urls": result.sitemap_urls,
        "blocked_by_robots": len(result.blocked),
        "pin_vs_website_address_distance_meters": pin,
    }
    if notes:
        summary["note"] = "; ".join(notes)
    return profile, summary


def update_pin_distance(db: Session, business: Business, website: WebsiteProfile) -> float | None:
    """Brief §1: distance between the GBP map pin and the address on the website."""
    location = db.scalar(select(BusinessLocation).where(BusinessLocation.business_id == business.id))
    if location is None:
        return None
    location.website_address = website.address
    location.website_latitude, location.website_longitude = website.latitude, website.longitude
    distance = None
    if None not in (location.latitude, location.longitude, website.latitude, website.longitude):
        distance = round(
            haversine_m(location.latitude, location.longitude, website.latitude, website.longitude), 1
        )
    location.pin_vs_website_address_distance_meters = distance
    db.commit()
    return distance


def latest_website_profile(db: Session, project_id) -> WebsiteProfile | None:
    return db.scalar(
        select(WebsiteProfile)
        .where(WebsiteProfile.project_id == project_id)
        .order_by(WebsiteProfile.created_at.desc())
        .limit(1)
    )
