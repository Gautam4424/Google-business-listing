"""Phase 7: Local Pack + Local Finder rank tracking and visibility metrics (brief §2).

Per active keyword and check:
  Local Pack   -> SerpApi engine=google (device, gl/hl, canonical `location` of the city)   1 search
  Local Finder -> SerpApi engine=google_maps (`ll=@lat,lng,zoom` = keyword coordinates)     1 search
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
from app.models import AuditJob, GbpProfile, Keyword, Project, RankingResult, RankingRun
from app.models.base import utcnow
from app.providers import get_serpapi_client
from app.providers.base import ProviderError
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
    return {
        "rank": rank,
        "place_id": place_id,
        "cid": cid,
        "business_name": p.get("title") or "",
        "address": p.get("address"),
        "primary_category": p.get("type") or (types[0] if types else None),
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


def search_params(kind: str, kw: Keyword, location: str | None) -> dict:
    has_coords = kw.latitude is not None and kw.longitude is not None
    if kind == "local_pack":
        params = {"engine": "google", "q": kw.keyword, "gl": kw.country.lower(), "hl": kw.language,
                  "device": kw.device}  # fmt: skip
        if has_coords:
            params["uule"] = coordinate_uule(kw.latitude, kw.longitude)
        elif location:
            params["location"] = location
        return params
    params = {"engine": "google_maps", "type": "search", "q": kw.keyword, "hl": kw.language,
              "gl": kw.country.lower()}  # fmt: skip
    if has_coords:
        params["ll"] = f"@{kw.latitude:.6f},{kw.longitude:.6f},{get_settings().maps_zoom}z"
    elif "near me" not in kw.keyword.lower():
        params["q"] = f"{kw.keyword} {city_label(kw.location_name)}"
    return params


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


def estimate(db: Session, project: Project, mode: str | None = None, force: bool = False) -> dict:
    """Credits a check would use (cached searches are free) vs what is really left on SerpApi."""
    mode = mode or get_settings().ranking_mode
    client = get_serpapi_client()
    keywords = active_keywords(db, project)
    locations: dict[str, str | None] = {}
    needed = cached = 0
    for kw in keywords:
        for kind in _kinds(mode):
            loc = None
            if kind == "local_pack" and client is not None and kw.latitude is None:
                if kw.location_name not in locations:
                    locations[kw.location_name] = resolve_location(db, client, kw.location_name, kw.country)
                loc = locations[kw.location_name]
            params = search_params(kind, kw, loc)
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
        "active_keywords": len(keywords),
        "searches_needed": needed,
        "searches_from_cache": cached,
        "credits_left": available,
        "serpapi_left": real_left,
        "app_limit_left": app_left,
        "renews_on": account.get("plan_renewal_date"),
        "enough": needed <= available,
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
    db: Session, project: Project, job: AuditJob, mode: str | None = None, force: bool = False
) -> dict:
    mode = mode or get_settings().ranking_mode
    client = get_serpapi_client()
    if client is None:
        raise RuntimeError("SERPAPI_KEY is not set")
    ident = client_identity(db, project)
    keywords = active_keywords(db, project)
    if not keywords:
        raise ValueError("No active keywords: generate keywords and switch some on first")

    locations: dict[str, str | None] = {}
    made = reused = 0
    errors: list[str] = []
    for kw in keywords:
        for kind in _kinds(mode):
            loc = None
            if kind == "local_pack" and kw.latitude is None:  # named location only without coordinates
                if kw.location_name not in locations:
                    locations[kw.location_name] = resolve_location(db, client, kw.location_name, kw.country)
                loc = locations[kw.location_name]
            params = search_params(kind, kw, loc)
            run = RankingRun(
                project_id=project.id, keyword_id=kw.id, audit_job_id=job.id, checked_at=utcnow(),
                provider="serpapi", result_type=kind, country=kw.country, language=kw.language,
                device=kw.device, location_name=kw.location_name, latitude=kw.latitude,
                longitude=kw.longitude, keyword=kw.keyword,
                search_location=params.get("location") or params.get("ll")
                or (f"@{kw.latitude:.6f},{kw.longitude:.6f}" if "uule" in params else None),
                search_radius_meters=None if kind == "local_pack" else _zoom_radius(get_settings().maps_zoom),
            )  # fmt: skip
            try:
                data, from_cache, raw_id = _search(db, client, params, force)
            except (ProviderError, httpx.HTTPError, quota.QuotaExceeded) as exc:
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
    return {
        "mode": mode,
        "keywords": len(keywords),
        "searches_made": made,
        "searches_from_cache": reused,
        "failed": len(errors),
        "errors": errors[:10],
    }


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


def ranking_checks(db: Session, project: Project, limit: int = 12) -> list[AuditJob]:
    return db.scalars(
        select(AuditJob)
        .where(AuditJob.project_id == project.id, AuditJob.job_type == "ranking_check")
        .where(AuditJob.status.in_(("completed", "partial_success")))
        .order_by(AuditJob.created_at.desc())
        .limit(limit)
    ).all()


def check_report(db: Session, project: Project, job: AuditJob, previous: AuditJob | None) -> dict:
    mode = (job.params or {}).get("mode") or get_settings().ranking_mode
    rows = keyword_rows(db, _runs_for_job(db, job.id), mode)
    summary = summarize(rows)
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
