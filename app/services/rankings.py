"""Phase 7: Local Pack + Local Finder rank tracking and visibility metrics (brief §2).

Per active keyword and check, searched from the chosen point ("search from"):
  city     -> the city/suburb centre (Google's point for that place)        [default]
  country  -> the whole country (a named place, no map point)
  business -> the business's own map pin
  Local Pack   -> SerpApi engine=google (mobile, gl/hl, uule = point, or `location` = named place)   1 search
  Local Finder -> SerpApi engine=google_maps around the point (`ll=@lat,lng,zoom`);
                  for the whole country: engine=google_local (Google's "More places" list)        1 search
"maps_only" mode skips the Local Pack search and estimates it from the Maps top 3.
Every business returned is stored (Phase 8 builds competitors from it).
Re-checks within RANKING_CACHE_HOURS reuse the saved response for free.
"""

import base64
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urlsplit

import httpx
from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import AuditJob, BusinessLocation, GbpProfile, Keyword, Project, RankingResult, RankingRun
from app.models.base import utcnow
from app.providers import get_places_client, get_serpapi_client
from app.providers.base import ProviderError
from app.providers.google_places import PROVIDER as PLACES
from app.services import quota
from app.services.keywords import city_label
from app.services.nap import clean_name, normalize_phone, postcode
from app.services.provider_cache import get_cached, store_response

PACK_POINTS = {1: 100, 2: 70, 3: 50}
MAX_POINTS = 140  # best case: Local Pack #1 (100) + Local Finder top 3 (40)
RESULT_TTL = timedelta(days=30)  # Google-derived content retention


class NotEnoughCredits(Exception):
    pass


def finder_points(rank: int | None) -> int:
    if rank is None:
        return 0
    return 40 if rank <= 3 else 20 if rank <= 10 else 10 if rank <= 20 else 0


# ---------- who is the client ----------


@dataclass
class ClientIdentity:
    place_id: str | None
    cid: str | None
    name: str | None
    address: str | None
    phone_e164: str | None
    domain: str | None


def _domain(url: str | None) -> str | None:
    if not url:
        return None
    host = urlsplit(url if "//" in url else "https://" + url).hostname or ""
    return host.lower().removeprefix("www.") or None


def client_identity(db: Session, project: Project) -> ClientIdentity | None:
    business = project.client_business
    if business is None:
        return None
    profile = db.scalar(
        select(GbpProfile).where(GbpProfile.business_id == business.id).order_by(GbpProfile.created_at.desc())
    )
    cid = None
    if profile and profile.maps_url and (m := re.search(r"[?&]cid=(\d+)", profile.maps_url)):
        cid = m.group(1)
    phone = (
        normalize_phone(profile.phone_number, project.country) if profile and profile.phone_number else None
    )
    return ClientIdentity(
        place_id=business.place_id,
        cid=cid,
        name=(profile.business_name if profile else None) or business.name,
        address=profile.formatted_address if profile else project.input_address,
        phone_e164=phone[0] if phone else None,
        domain=business.domain or _domain(profile.website_url if profile else project.website_url),
    )


def is_client(row: dict, ident: ClientIdentity | None, region: str | None) -> bool:
    if ident is None:
        return False
    if ident.place_id and row.get("place_id") == ident.place_id:
        return True
    if ident.cid and row.get("cid") == ident.cid:
        return True
    if not (ident.name and row.get("business_name")):
        return False
    if fuzz.token_set_ratio(clean_name(ident.name), clean_name(row["business_name"])) < 90:
        return False
    phone = normalize_phone(row["phone"], region) if row.get("phone") else None
    same_phone = bool(phone and ident.phone_e164 and phone[0] == ident.phone_e164)
    same_site = bool(ident.domain and _domain(row.get("website_url")) == ident.domain)
    same_pc = bool(
        ident.address and row.get("address") and postcode(ident.address) == postcode(row["address"])
    )
    return same_phone or same_site or same_pc


# ---------- parsing SerpApi responses ----------


def _num(value) -> str | None:
    value = str(value or "")
    return value if value.isdigit() else None


def _row(rank: int, p: dict) -> dict:
    gps = p.get("gps_coordinates") or {}
    links = p.get("links") or {}
    raw_pid = p.get("place_id")
    cid = _num(p.get("data_cid")) or _num(raw_pid)
    place_id = raw_pid if raw_pid and not _num(raw_pid) else None
    types = p.get("types") or []
    categories = list(dict.fromkeys(([p["type"]] if p.get("type") else []) + types)) or None
    return {
        "rank": rank,
        "place_id": place_id,
        "cid": cid,
        "business_name": p.get("title") or "",
        "address": p.get("address"),
        "primary_category": p.get("type") or (types[0] if types else None),
        "categories": categories,
        "rating": p.get("rating"),
        "review_count": p.get("reviews"),
        "phone": p.get("phone"),
        "website_url": p.get("website") or links.get("website"),
        "maps_url": f"https://maps.google.com/?cid={cid}" if cid else None,
        "latitude": gps.get("latitude"),
        "longitude": gps.get("longitude"),
    }


def parse_local_pack(data: dict) -> tuple[bool, list[dict]]:
    lr = data.get("local_results")
    places = lr.get("places", []) if isinstance(lr, dict) else lr if isinstance(lr, list) else []
    rows = [_row(p.get("position") or i, p) for i, p in enumerate(places, start=1)]
    return bool(rows), rows


def parse_maps(data: dict) -> list[dict]:
    items = data.get("local_results") or []
    if not items and isinstance(data.get("place_results"), dict):
        items = [data["place_results"]]  # the query matched one exact place
    return [_row(p.get("position") or i, p) for i, p in enumerate(items, start=1)][:20]


# ---------- locations, params, credits ----------


def resolve_location(db: Session, client, area_name: str, country: str | None) -> str | None:
    """Canonical Google location for SerpApi `location=` (free Locations API, cached 90 days)."""
    for query in dict.fromkeys([city_label(area_name), area_name]):
        params = {"q": query}
        cached = get_cached(db, "serpapi_locations", "locations.json", params, timedelta(days=90))
        if cached is not None:
            items = cached.response or []
        else:
            try:
                items = client.locations(query)
            except (ProviderError, httpx.HTTPError):
                return None
            store_response(
                db, "serpapi_locations", "locations.json", params, items, 200, ttl=timedelta(days=90)
            )
        items = [i for i in items if not country or (i.get("country_code") or "").upper() == country.upper()]
        if items:
            return max(items, key=lambda i: i.get("reach") or 0).get("canonical_name")
    return None


UULE_RADIUS = 5000


def coordinate_uule(lat: float, lng: float) -> str:
    """Google's coordinate location code: the search is made "from" this point, like a phone there.

    Works for suburbs missing from Google's named-location list. Fixed timestamp so identical searches
    produce identical parameters (needed for the free re-check window).
    """
    lines = [
        "role:1",
        "producer:12",
        "provenance:6",
        "timestamp:1700000000000000",
        "latlng{",
        f"latitude_e7:{round(lat * 1e7)}",
        f"longitude_e7:{round(lng * 1e7)}",
        "}",
        f"radius:{UULE_RADIUS}",
    ]
    return "a+" + base64.b64encode("\n".join(lines).encode()).decode()


# ---------- where a check searches from ----------

SCOPES = ("city", "country", "business")
SCOPE_LABEL = {"city": "city centre", "country": "whole country", "business": "business location"}
COUNTRY_NAMES = {
    "US": "United States", "CA": "Canada", "GB": "United Kingdom", "AU": "Australia", "NZ": "New Zealand",
    "IN": "India", "IE": "Ireland", "ZA": "South Africa", "AE": "United Arab Emirates", "SG": "Singapore",
    "DE": "Germany", "FR": "France", "ES": "Spain", "IT": "Italy", "NL": "Netherlands", "MX": "Mexico",
    "BR": "Brazil", "PH": "Philippines", "MY": "Malaysia", "PK": "Pakistan", "NG": "Nigeria",
}  # fmt: skip
CITY_FIELDS = "places.location,places.formattedAddress,places.displayName,places.types"


@dataclass
class SearchPoint:
    """Where Google is told the searcher is: a point (city centre / business) or a named place (country)."""

    scope: str
    label: str
    lat: float | None = None
    lng: float | None = None
    location: str | None = None  # SerpApi canonical location name, when searching from a named place


def city_centre(db: Session, area_name: str, country: str | None) -> tuple[float, float, str] | None:
    """The centre of a city/suburb (Google's own point for that place), not a business inside it.

    1 Places Text Search per area, cached 30 days (0 SerpApi). Prefers a result that is a place
    (locality / sublocality / neighbourhood), never a business.
    """
    client = get_places_client()
    if client is None:
        return None
    params = {"textQuery": area_name, "fieldMask": CITY_FIELDS, "regionCode": (country or "").upper()}
    cached = get_cached(db, PLACES, "places:searchText:city", params, timedelta(days=30))
    if cached is not None:
        data = cached.response or {}
    else:
        try:
            quota.consume(db, "google_places_text_search")
            extra = {"regionCode": country.upper()} if country else {}
            data = client.search_text(area_name, field_mask=CITY_FIELDS, page_size=3, **extra)
        except (quota.QuotaExceeded, ProviderError, httpx.HTTPError):
            db.rollback()
            return None
        store_response(db, PLACES, "places:searchText:city", params, data, 200, ttl=timedelta(days=30))
    place_types = {"locality", "sublocality", "sublocality_level_1", "neighborhood", "postal_town",
                   "administrative_area_level_2", "administrative_area_level_3", "political"}  # fmt: skip
    for p in data.get("places") or []:
        if p.get("location") and place_types & set(p.get("types") or []):
            loc = p["location"]
            return loc["latitude"], loc["longitude"], p.get("formattedAddress") or area_name
    return None


def _business_pin(db: Session, project: Project) -> tuple[float, float] | None:
    if project.client_business_id is None:
        return None
    pin = db.scalar(
        select(BusinessLocation).where(BusinessLocation.business_id == project.client_business_id)
    )
    return (pin.latitude, pin.longitude) if pin and pin.latitude is not None else None


def search_points(db: Session, project: Project, keywords: list[Keyword], scope: str, client) -> dict:
    """keyword_id -> SearchPoint for this check. Cached lookups only (city: Places; country: free list)."""
    scope = scope if scope in SCOPES else "city"
    out: dict = {}
    cities: dict[str, SearchPoint] = {}
    pin = _business_pin(db, project) if scope == "business" else None
    for kw in keywords:
        country = (kw.country or project.country or "").upper()
        if scope == "country":
            name = COUNTRY_NAMES.get(country, country)
            canonical = resolve_location(db, client, name, country) if client else None
            out[kw.id] = SearchPoint(
                "country", f"{canonical or name} (whole country)", location=canonical or name
            )
            continue
        if scope == "business" and pin:
            out[kw.id] = SearchPoint(
                "business", f"business location ({pin[0]:.4f}, {pin[1]:.4f})", pin[0], pin[1]
            )
            continue
        # city centre (also the fallback when a business has no map pin)
        if kw.location_name not in cities:
            centre = city_centre(db, kw.location_name, country)
            if centre:
                cities[kw.location_name] = SearchPoint(
                    "city",
                    f"{kw.location_name} city centre ({centre[0]:.4f}, {centre[1]:.4f})",
                    centre[0],
                    centre[1],
                )
            elif kw.latitude is not None:  # no city found: the area's saved point
                cities[kw.location_name] = SearchPoint(
                    "city",
                    f"{kw.location_name} ({kw.latitude:.4f}, {kw.longitude:.4f})",
                    kw.latitude,
                    kw.longitude,
                )
            else:
                named = resolve_location(db, client, kw.location_name, country) if client else None
                cities[kw.location_name] = SearchPoint("city", named or kw.location_name, location=named)
        out[kw.id] = cities[kw.location_name]
    return out


def search_params(kind: str, kw: Keyword, point: SearchPoint) -> dict:
    has_coords = point.lat is not None and point.lng is not None
    if kind == "local_pack":
        params = {"engine": "google", "q": kw.keyword, "gl": kw.country.lower(), "hl": kw.language,
                  "device": kw.device}  # fmt: skip
        if has_coords:
            params["uule"] = coordinate_uule(point.lat, point.lng)
        elif point.location:
            params["location"] = point.location
        return params
    if not has_coords:
        # No map point (whole country): Google's Local Finder list ("More places") searched from that place
        params = {"engine": "google_local", "q": kw.keyword, "gl": kw.country.lower(), "hl": kw.language}
        if point.location:
            params["location"] = point.location
        return params
    ll = f"@{point.lat:.6f},{point.lng:.6f},{get_settings().maps_zoom}z"
    return {"engine": "google_maps", "type": "search", "q": kw.keyword, "hl": kw.language,
            "gl": kw.country.lower(), "ll": ll}  # fmt: skip


def _kinds(mode: str) -> list[str]:
    return ["local_finder"] if mode == "maps_only" else ["local_pack", "local_finder"]


def active_keywords(db: Session, project: Project) -> list[Keyword]:
    return db.scalars(
        select(Keyword)
        .where(Keyword.project_id == project.id, Keyword.active.is_(True))
        .order_by(Keyword.created_at)
    ).all()


def _cache_age() -> timedelta:
    return timedelta(hours=get_settings().ranking_cache_hours)


def scope_of(project: Project, requested: str | None = None) -> str:
    """The "search from" choice: the one asked for, else the project's last choice, else city centre."""
    for value in (requested, getattr(project, "search_from", None)):
        if value in SCOPES:
            return value
    return "city"


def estimate(
    db: Session, project: Project, mode: str | None = None, force: bool = False, scope: str | None = None
) -> dict:
    """Credits a check would use (cached searches are free) vs what is really left on SerpApi."""
    mode = mode or get_settings().ranking_mode
    scope = scope_of(project, scope)
    client = get_serpapi_client()
    keywords = active_keywords(db, project)
    points = search_points(db, project, keywords, scope, client)
    needed = cached = 0
    for kw in keywords:
        for kind in _kinds(mode):
            params = search_params(kind, kw, points[kw.id])
            if not force and get_cached(db, "serpapi", f"search:{params['engine']}", params, _cache_age()):
                cached += 1
            else:
                needed += 1
    account = {}
    if client is not None:
        try:
            account = client.account()
        except (ProviderError, httpx.HTTPError):
            account = {}
    app_left = quota.get_usage(db, "serpapi_search").remaining
    real_left = account.get("total_searches_left", account.get("plan_searches_left"))
    available = min(app_left, real_left) if real_left is not None else app_left
    return {
        "mode": mode,
        "search_from": scope,
        "search_points": sorted({p.label for p in points.values()}),
        "active_keywords": len(keywords),
        "searches_needed": needed,
        "searches_from_cache": cached,
        "credits_left": available,
        "serpapi_left": real_left,
        "app_limit_left": app_left,
        "renews_on": account.get("plan_renewal_date"),
        "enough": needed <= available,
        # fewer left than needed: the check still runs keyword by keyword and stops at the limit
        "can_start": needed == 0 or available > 0,
        "limit_message": limit_notice(db)
        or (
            f"SerpApi account: 0 searches left (renews {account.get('plan_renewal_date') or 'next month'})"
            if real_left == 0
            else None
        ),
        "serpapi_configured": client is not None,
    }


# ---------- running a check ----------


def _search(db: Session, client, params: dict, force: bool) -> tuple[dict, bool, uuid.UUID | None]:
    endpoint = f"search:{params['engine']}"
    cached = None if force else get_cached(db, "serpapi", endpoint, params, _cache_age())
    if cached is not None:
        return cached.response or {}, True, cached.id
    quota.consume(db, "serpapi_search")
    try:
        data = client.search(params)
    except (ProviderError, httpx.HTTPError):
        quota.release(db, "serpapi_search")  # SerpApi does not charge searches that error
        raise
    raw = store_response(db, "serpapi", endpoint, params, data, 200, ttl=RESULT_TTL)
    return data, False, raw.id


def run_check(
    db: Session,
    project: Project,
    job: AuditJob,
    mode: str | None = None,
    force: bool = False,
    scope: str | None = None,
) -> dict:
    mode = mode or get_settings().ranking_mode
    scope = scope_of(project, scope)
    client = get_serpapi_client()
    if client is None:
        raise RuntimeError("SERPAPI_KEY is not set")
    ident = client_identity(db, project)
    keywords = active_keywords(db, project)
    if not keywords:
        raise ValueError("No active keywords: generate keywords and switch some on first")

    points = search_points(db, project, keywords, scope, client)
    made = reused = 0
    errors: list[str] = []
    stopped: str | None = None  # set when a free-tier limit is reached: the remaining searches are skipped
    done_keywords = 0
    # Keyword by keyword, Local Pack first: each result is committed (and shown live) as soon as it
    # arrives, so a limit reached on keyword 2 keeps keyword 1's results.
    for kw in keywords:
        if stopped:
            break
        for kind in _kinds(mode):
            point = points[kw.id]
            params = search_params(kind, kw, point)
            radius = None if point.lat is None else UULE_RADIUS if kind == "local_pack" else _zoom_radius(
                get_settings().maps_zoom)  # fmt: skip
            run = RankingRun(
                project_id=project.id, keyword_id=kw.id, audit_job_id=job.id, checked_at=utcnow(),
                provider="serpapi", result_type=kind, country=kw.country, language=kw.language,
                device=kw.device, location_name=kw.location_name, latitude=point.lat, longitude=point.lng,
                keyword=kw.keyword, search_location=point.label[:300], search_scope=point.scope,
                search_radius_meters=radius,
            )  # fmt: skip
            try:
                data, from_cache, raw_id = _search(db, client, params, force)
            except quota.QuotaExceeded as exc:
                db.rollback()
                stopped = str(exc)
                break  # never keep trying past a limit
            except (ProviderError, httpx.HTTPError) as exc:
                db.rollback()
                run.status, run.error = "failed", f"{type(exc).__name__}: {exc}"
                db.add(run)
                db.commit()
                errors.append(f"{kw.keyword} ({kind}): {exc}")
                continue
            made += not from_cache
            reused += from_cache
            run.from_cache, run.raw_response_id = from_cache, raw_id
            if kind == "local_pack":
                run.pack_shown, rows = parse_local_pack(data)
            else:
                rows = parse_maps(data)
            db.add(run)
            db.flush()
            for r in rows:
                client_hit = is_client(r, ident, kw.country)
                db.add(RankingResult(run_id=run.id, is_client_business=client_hit, **r))
                if client_hit and run.client_rank is None:
                    run.client_rank = r["rank"]
            db.commit()
        else:
            done_keywords += 1
    return {
        "mode": mode,
        "keywords": len(keywords),
        "keywords_checked": done_keywords,
        "searches_made": made,
        "searches_from_cache": reused,
        "failed": len(errors),
        "errors": errors[:10],
        "stopped_by_limit": stopped,
    }


def live_check(db: Session, project: Project) -> dict | None:
    """The ranking check running right now, with the results collected so far (shown live in the UI)."""
    job = db.scalar(
        select(AuditJob)
        .where(AuditJob.project_id == project.id, AuditJob.job_type.in_(RANKING_JOB_TYPES))
        .where(AuditJob.status.in_(("queued", "running")))
        .order_by(AuditJob.created_at.desc())
        .limit(1)
    )
    if job is None or (job.job_type == "full_audit" and not (job.params or {}).get("rankings", True)):
        return None
    mode = (job.params or {}).get("mode") or get_settings().ranking_mode
    rows = {r["keyword_id"]: r for r in keyword_rows(db, _runs_for_job(db, job.id), mode)}
    runs_done = len(_runs_for_job(db, job.id))
    keywords = active_keywords(db, project)
    step = next((s["name"] for s in job.steps or [] if s.get("status") == "running"), None)
    items = []
    for kw in keywords:
        pending = {"keyword_id": str(kw.id), "keyword": kw.keyword, "location_name": kw.location_name,
                   "pending": True}  # fmt: skip
        items.append(rows.get(str(kw.id)) or pending)
    return {
        "job_id": str(job.id),
        "status": job.status,
        "step": step,
        "mode": mode,
        "search_from": job_scope(job),
        "keywords_total": len(keywords),
        "keywords_done": sum(1 for i in items if not i.get("pending")),
        "searches_done": runs_done,
        "keywords": items,
    }


def last_failed_check(db: Session, project: Project, latest: AuditJob | None) -> dict | None:
    """A ranking check that failed after the latest good one (e.g. a limit on the first search)."""
    job = db.scalar(
        select(AuditJob)
        .where(AuditJob.project_id == project.id, AuditJob.job_type == "ranking_check")
        .order_by(AuditJob.created_at.desc())
        .limit(1)
    )
    if job is None or job.status != "failed" or (latest is not None and job.created_at <= latest.created_at):
        return None
    return {"at": job.created_at.isoformat(), "error": job.error}


def limit_notice(db: Session) -> str | None:
    """Why no SerpApi search can run right now (daily/monthly limit), with the reset time."""
    return quota.get_usage(db, "serpapi_search").blocked_message


def _zoom_radius(zoom: int) -> int:
    """Rough visible radius of a Google Maps viewport at this zoom (phone-sized screen)."""
    return int(40_000_000 / (2**zoom) / 2)


# ---------- metrics ----------


def _runs_for_job(db: Session, job_id) -> list[RankingRun]:
    return db.scalars(select(RankingRun).where(RankingRun.audit_job_id == job_id)).all()


def keyword_rows(db: Session, runs: list[RankingRun], mode: str) -> list[dict]:
    by_kw: dict = defaultdict(dict)
    for r in runs:
        by_kw[r.keyword_id][r.result_type] = r
    rows = []
    for kid, kinds in by_kw.items():
        pack, finder = kinds.get("local_pack"), kinds.get("local_finder")
        finder_rank = finder.client_rank if finder and finder.status == "succeeded" else None
        estimated = pack is None
        if pack is not None and pack.status == "succeeded":
            pack_rank, pack_shown = pack.client_rank, pack.pack_shown
        elif estimated:  # maps_only: the Local Pack is (usually) the Maps top 3
            pack_rank = finder_rank if finder_rank and finder_rank <= 3 else None
            pack_shown = None
        else:
            pack_rank, pack_shown = None, None
        points = PACK_POINTS.get(pack_rank, 0) + finder_points(finder_rank)
        ref = finder or pack
        rows.append({
            "keyword_id": str(kid),
            "keyword": ref.keyword,
            "location_name": ref.location_name,
            "local_pack_rank": pack_rank,
            "local_pack_shown": pack_shown,
            "local_pack_estimated": estimated,
            "local_finder_rank": finder_rank,
            # False = that search was not made (the check stopped at a limit after the Local Pack)
            "local_finder_checked": finder is not None,
            "search_from": ref.search_location,
            "search_scope": ref.search_scope,
            "points": points,
            "visibility": round(points / MAX_POINTS * 100, 1),
            "failed": any(k.status != "succeeded" for k in kinds.values()),
        })  # fmt: skip
    return sorted(rows, key=lambda r: (-r["points"], r["keyword"] or ""))


def summarize(rows: list[dict]) -> dict:
    n = len(rows)
    pack = [r["local_pack_rank"] for r in rows if r["local_pack_rank"]]
    finder = [r["local_finder_rank"] for r in rows if r["local_finder_rank"]]
    best = [min(x for x in (r["local_pack_rank"], r["local_finder_rank"]) if x) for r in rows
            if r["local_pack_rank"] or r["local_finder_rank"]]  # fmt: skip
    return {
        "keywords": n,
        "visibility_score": round(sum(r["points"] for r in rows) / (MAX_POINTS * n) * 100, 1) if n else 0.0,
        "in_local_pack": len(pack),
        "in_local_finder": len(finder),
        "average_local_pack_rank": round(sum(pack) / len(pack), 2) if pack else None,
        "average_local_finder_rank": round(sum(finder) / len(finder), 2) if finder else None,
        "top_3": sum(1 for b in best if b <= 3),
        "top_10": sum(1 for b in best if b <= 10),
        "not_found": n - len(best),
    }


RANKING_JOB_TYPES = ("ranking_check", "full_audit")


def ranking_checks(db: Session, project: Project, limit: int = 12) -> list[AuditJob]:
    """Finished jobs that collected rankings (a ranking check, or a full audit that included one)."""
    has_runs = select(RankingRun.id).where(RankingRun.audit_job_id == AuditJob.id).exists()
    return db.scalars(
        select(AuditJob)
        .where(AuditJob.project_id == project.id, AuditJob.job_type.in_(RANKING_JOB_TYPES), has_runs)
        .where(AuditJob.status.in_(("completed", "partial_success")))
        .order_by(AuditJob.created_at.desc())
        .limit(limit)
    ).all()


def job_scope(job: AuditJob) -> str | None:
    """Where a check searched from (None: checks made before the choice existed)."""
    return (job.params or {}).get("search_from")


def previous_check(checks: list[AuditJob], job: AuditJob) -> AuditJob | None:
    """The check before `job` made from the same place: changes are only measured like for like."""
    older = [j for j in checks if j.created_at < job.created_at and j.id != job.id]
    return next((j for j in older if job_scope(j) == job_scope(job)), None)


def check_report(db: Session, project: Project, job: AuditJob, previous: AuditJob | None) -> dict:
    mode = (job.params or {}).get("mode") or get_settings().ranking_mode
    rows = keyword_rows(db, _runs_for_job(db, job.id), mode)
    summary = summarize(rows)
    summary["search_from"] = job_scope(job)
    if previous is not None:
        prev_rows = {r["keyword_id"]: r for r in keyword_rows(db, _runs_for_job(db, previous.id), mode)}
        # Compare like with like: only keywords checked both times.
        common = [r for r in rows if r["keyword_id"] in prev_rows]
        summary["previous_check_at"] = previous.created_at.isoformat()
        summary["compared_keywords"] = len(common)
        if common:
            now_score = summarize(common)["visibility_score"]
            prev_score = summarize([prev_rows[r["keyword_id"]] for r in common])["visibility_score"]
            summary["visibility_change"] = round(now_score - prev_score, 1)
        else:
            summary["visibility_change"] = None
        for r in rows:
            p = prev_rows.get(r["keyword_id"])
            r["previous_local_pack_rank"] = p["local_pack_rank"] if p else None
            r["previous_local_finder_rank"] = p["local_finder_rank"] if p else None
            r["visibility_change"] = round(r["visibility"] - p["visibility"], 1) if p else None
    return {"job_id": str(job.id), "checked_at": job.created_at.isoformat(), "mode": mode, "summary": summary,
            "keywords": rows}  # fmt: skip


def _business_row(res: RankingResult) -> dict:
    return {
        "rank": res.rank, "business_name": res.business_name, "is_client": res.is_client_business,
        "category": res.primary_category, "rating": res.rating, "review_count": res.review_count,
        "address": res.address, "website_url": res.website_url, "phone": res.phone, "maps_url": res.maps_url,
    }  # fmt: skip


def keyword_results(db: Session, job: AuditJob, keyword_id) -> dict | None:
    """Every business a check found for one keyword: Local Pack (3) and the Local Finder list (up to 20)."""
    runs = db.scalars(
        select(RankingRun).where(RankingRun.audit_job_id == job.id, RankingRun.keyword_id == keyword_id)
    ).all()
    if not runs:
        return None
    out = {"keyword_id": str(keyword_id), "job_id": str(job.id), "checked_at": job.created_at.isoformat(),
           "keyword": runs[0].keyword, "location_name": runs[0].location_name,
           "search_from": runs[0].search_location, "search_scope": runs[0].search_scope,
           "local_pack": None, "local_finder": None}  # fmt: skip
    for run in runs:
        rows = db.scalars(
            select(RankingResult).where(RankingResult.run_id == run.id).order_by(RankingResult.rank)
        ).all()
        out[run.result_type] = {
            "status": run.status,
            "error": run.error,
            "shown": run.pack_shown if run.result_type == "local_pack" else None,
            "results": [_business_row(r) for r in rows],
        }
    return out


def all_results(db: Session, job: AuditJob) -> list[dict]:
    """Flat list of every business found in a check (for the CSV)."""
    rows = db.execute(
        select(RankingResult, RankingRun)
        .join(RankingRun, RankingRun.id == RankingResult.run_id)
        .where(RankingRun.audit_job_id == job.id)
        .order_by(RankingRun.keyword, RankingRun.result_type.desc(), RankingResult.rank)
    ).all()
    return [{"keyword": run.keyword, "result_type": run.result_type, "search_from": run.search_location,
             **_business_row(res)} for res, run in rows]  # fmt: skip


def top_businesses(db: Session, job: AuditJob, limit: int = 8) -> list[dict]:
    """Businesses that appear most in this check (preview for Phase 8)."""
    counts: dict[str, dict] = {}
    rows = db.execute(
        select(RankingResult, RankingRun.result_type)
        .join(RankingRun, RankingRun.id == RankingResult.run_id)
        .where(RankingRun.audit_job_id == job.id)
    ).all()
    for res, kind in rows:
        key = res.cid or res.place_id or res.business_name.lower()
        c = counts.setdefault(key, {"business_name": res.business_name, "is_client": False, "appearances": 0,
                                    "local_pack": 0, "best_rank": None, "rating": res.rating,
                                    "review_count": res.review_count})  # fmt: skip
        c["appearances"] += 1
        c["local_pack"] += kind == "local_pack"
        c["is_client"] |= res.is_client_business
        c["best_rank"] = min(filter(None, [c["best_rank"], res.rank]))
    return sorted(counts.values(), key=lambda c: (-c["appearances"], c["best_rank"] or 99))[:limit]
