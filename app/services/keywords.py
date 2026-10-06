"""Phase 6: service areas and keyword generation (brief §2 "Keyword creation").

Formats: "{service} in {city}", "{service} near me", "{service} {city}", plus the user's own keywords.
Every keyword is stored with its search settings (location, coordinates, language, country, device).
Only *active* keywords are rank-checked in Phase 7, each costing 2 SerpApi searches, so active keywords are
capped (KEYWORD_CAP, default 10).
"""

from datetime import timedelta

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import BusinessLocation, Keyword, Project, ProjectService, RankingRun
from app.providers import get_places_client
from app.providers.base import ProviderError
from app.providers.google_places import PROVIDER as PLACES
from app.services import quota
from app.services.provider_cache import get_cached, store_response

PATTERNS = [
    ("in_city", "{service} in {city}"),
    ("near_me", "{service} near me"),
    ("city", "{service} {city}"),
]
AREA_FIELDS = "places.location,places.formattedAddress,places.displayName"


class KeywordCapReached(Exception):
    pass


def city_label(area_name: str) -> str:
    return area_name.split(",")[0].strip()


def service_phrase(name: str) -> str:
    return name.strip().lower()


# ---------- service areas ----------


def resolve_area(db: Session, name: str, country: str | None) -> tuple[float, float, str | None] | None:
    """City/suburb -> coordinates with one cheap Places Text Search (cached 30 days)."""
    client = get_places_client()
    if client is None:
        return None
    params = {"textQuery": name, "fieldMask": AREA_FIELDS, "regionCode": (country or "").upper()}
    cached = get_cached(db, PLACES, "places:searchText:area", params, timedelta(days=30))
    if cached is not None:
        data = cached.response or {}
    else:
        try:
            quota.consume(db, "google_places_text_search")
            extra = {"regionCode": country.upper()} if country else {}
            data = client.search_text(name, field_mask=AREA_FIELDS, page_size=1, **extra)
        except (quota.QuotaExceeded, ProviderError, httpx.HTTPError):
            db.rollback()
            return None
        store_response(db, PLACES, "places:searchText:area", params, data, 200, ttl=timedelta(days=30))
    places = data.get("places") or []
    if not places or not places[0].get("location"):
        return None
    loc = places[0]["location"]
    return loc["latitude"], loc["longitude"], places[0].get("formattedAddress")


def ensure_area_coordinates(db: Session, project: Project) -> list[str]:
    """Fill missing coordinates. The first area falls back to the business's own map pin."""
    notes: list[str] = []
    areas = [dict(a) for a in project.service_areas or []]
    pin = None
    if project.client_business_id:
        pin = db.scalar(
            select(BusinessLocation).where(BusinessLocation.business_id == project.client_business_id)
        )
    for i, area in enumerate(areas):
        if area.get("latitude") is not None and area.get("longitude") is not None:
            continue
        found = resolve_area(db, area["name"], project.country)
        if found:
            area["latitude"], area["longitude"] = found[0], found[1]
        elif i == 0 and pin and pin.latitude is not None:
            area["latitude"], area["longitude"] = pin.latitude, pin.longitude
            notes.append(f"'{area['name']}': used the business's map pin")
        else:
            notes.append(
                f"'{area['name']}': no coordinates found ('near me' searches will use the city name)"
            )
    if not areas and pin and pin.latitude is not None and project.client_business:
        areas = [{"name": project.client_business.name, "latitude": pin.latitude, "longitude": pin.longitude}]
        notes.append("No service area set: used the business location")
    project.service_areas = areas
    db.commit()
    return notes


# ---------- keywords ----------


def _active_count(db: Session, project: Project) -> int:
    return db.scalar(
        select(func.count())
        .select_from(Keyword)
        .where(Keyword.project_id == project.id, Keyword.active.is_(True))
    )


def preview(db: Session, project: Project) -> dict:
    active = _active_count(db, project)
    total = db.scalar(select(func.count()).select_from(Keyword).where(Keyword.project_id == project.id))
    serp = quota.get_usage(db, "serpapi_search")
    return {
        "keywords": total,
        "active": active,
        "cap": get_settings().keyword_cap,
        "serpapi_credits_per_ranking_run": active * 2,
        "serpapi_remaining_this_month": serp.remaining,
        "ranking_runs_possible": (serp.remaining // (active * 2)) if active else None,
    }


def generate(db: Session, project: Project, device: str = "mobile") -> dict:
    notes = ensure_area_coordinates(db, project)
    areas = project.service_areas or []
    if not areas:
        raise ValueError("Add at least one service area (city) first")
    primary = areas[0]
    services = db.scalars(
        select(ProjectService)
        .where(ProjectService.project_id == project.id, ProjectService.selected.is_(True))
        .where(ProjectService.kind == "service")
        .order_by(ProjectService.score.desc())
    ).all()

    # Desired keywords in priority order: the user's own, then each pattern across services and areas,
    # so the cap covers every core service with "in {city}" before adding variants.
    desired: list[dict] = []
    for text in project.user_keywords or []:
        desired.append(
            {"keyword": text.strip(), "service": None, "pattern": "user", "area": primary, "svc": None}
        )
    for pattern, template in PATTERNS:
        for svc in services:
            for area in areas:
                text = template.format(service=service_phrase(svc.name), city=city_label(area["name"]))
                desired.append(
                    {"keyword": text, "service": svc.name, "pattern": pattern, "area": area, "svc": svc}
                )

    cap = get_settings().keyword_cap
    existing = {
        (k.keyword.lower(), k.location_name, k.device): k
        for k in db.scalars(select(Keyword).where(Keyword.project_id == project.id))
    }
    seen, created = set(), 0
    active_now = sum(1 for k in existing.values() if k.active)
    for d in desired:
        key = (d["keyword"].lower(), d["area"]["name"], device)
        if key in seen:
            continue
        seen.add(key)
        row = existing.get(key)
        if row is None:
            row = Keyword(
                project_id=project.id, keyword=d["keyword"], location_name=d["area"]["name"], device=device,
                source="user" if d["pattern"] == "user" else "generated", active=False,
            )  # fmt: skip
            db.add(row)
            created += 1
            row.active = active_now < cap
            active_now += row.active
        row.service = d["service"]
        row.pattern = d["pattern"]
        row.project_service_id = d["svc"].id if d["svc"] else None
        row.latitude, row.longitude = d["area"].get("latitude"), d["area"].get("longitude")
        row.language, row.country = project.language, project.country

    removed = 0
    for key, row in existing.items():
        if key in seen or row.source == "user":
            continue
        has_history = db.scalar(
            select(func.count()).select_from(RankingRun).where(RankingRun.keyword_id == row.id)
        )
        if has_history:
            row.active = False  # keep its ranking history, just stop checking it
        else:
            db.delete(row)
            removed += 1
    db.commit()
    return {"created": created, "removed": removed, "notes": notes, **preview(db, project)}


def add_user_keyword(db: Session, project: Project, text: str, location_name: str | None = None) -> Keyword:
    areas = project.service_areas or []
    area = next((a for a in areas if a["name"] == location_name), None) or (areas[0] if areas else None)
    if area is None and not location_name:
        raise ValueError("Add a service area first, or give location_name")
    area = area or {"name": location_name}
    row = Keyword(
        project_id=project.id, keyword=text.strip(), location_name=area["name"], device="mobile",
        source="user", pattern="user", latitude=area.get("latitude"), longitude=area.get("longitude"),
        language=project.language, country=project.country,
        active=_active_count(db, project) < get_settings().keyword_cap,
    )  # fmt: skip
    db.add(row)
    db.commit()
    return row


def set_active(db: Session, project: Project, keyword: Keyword, active: bool) -> Keyword:
    if active and not keyword.active and _active_count(db, project) >= get_settings().keyword_cap:
        raise KeywordCapReached(
            f"Keyword limit ({get_settings().keyword_cap} active) reached: switch another keyword off first, "
            "or raise KEYWORD_CAP in .env"
        )
    keyword.active = active
    db.commit()
    return keyword
