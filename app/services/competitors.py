"""Phase 8: competitors from the stored ranking results (brief §3). Uses 0 SerpApi searches.

Rule (brief): a business is a competitor if, in the latest ranking check, it appears
  - for at least 20% of the tracked keywords (Local Pack or Local Finder), or
  - in the Local Pack for at least 3 keywords.
The client is excluded; the COMPETITOR_MAX most visible competitors are analysed.

Profile data comes from the search results (categories, rating, reviews, website, phone). With
COMPETITOR_DETAILS on, 1 Google Place Details call per competitor (cached COMPETITOR_DETAILS_CACHE_DAYS)
adds its public review sample (max 5), used for review-topic gaps. Review texts are not stored, only
the topics found in them.
"""

import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import (
    AuditJob,
    Business,
    Competitor,
    CompetitorMetric,
    DataSource,
    GapRecommendation,
    GbpProfile,
    GbpReview,
    Project,
    ProjectService,
    RankingResult,
    RankingRun,
    ReviewTag,
)
from app.models.base import utcnow
from app.providers import get_places_client
from app.providers.base import ProviderError
from app.providers.google_places import PROVIDER as PLACES
from app.services import quota
from app.services.nap import clean_name
from app.services.provider_cache import get_cached, store_response
from app.services.rankings import _domain, ranking_checks
from app.services.review_nlp import analyze_review

COMPETITOR_SHARE = 0.20
COMPETITOR_PACK = 3
VELOCITY_MIN_DAYS = 7  # review velocity needs two snapshots at least this far apart
DETAILS_FIELDS = (
    "id,displayName,formattedAddress,googleMapsUri,websiteUri,internationalPhoneNumber,primaryType,"
    "primaryTypeDisplayName,rating,userRatingCount,reviews"
)
RULE = "Appears for at least 20% of tracked keywords, or in the Local Pack for at least 3 keywords"


class NoRankingCheck(Exception):
    pass


@dataclass
class Seen:
    """One business across all searches of a ranking check."""

    key: str
    names: Counter = field(default_factory=Counter)
    place_id: str | None = None
    cid: str | None = None
    address: str | None = None
    phone: str | None = None
    website_url: str | None = None
    maps_url: str | None = None
    rating: float | None = None
    review_count: int | None = None
    primary_category: str | None = None
    categories: list = field(default_factory=list)
    keywords: dict = field(
        default_factory=dict
    )  # keyword_id -> {keyword, local_pack_rank, local_finder_rank}

    @property
    def name(self) -> str:
        return self.names.most_common(1)[0][0] if self.names else ""


def _key(row: RankingResult) -> str:
    return row.cid or row.place_id or "name:" + clean_name(row.business_name)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _raw_categories(db: Session, runs: list[RankingRun]) -> dict[str, list]:
    """Categories for results saved before ranking_results.categories existed (from the raw response)."""
    out: dict[str, list] = {}
    ids = [r.raw_response_id for r in runs if r.raw_response_id and r.result_type == "local_finder"]
    for raw in db.scalars(select(DataSource).where(DataSource.id.in_(ids))).all() if ids else []:
        for p in (raw.response or {}).get("local_results") or []:
            cid = str(p.get("data_cid") or "")
            cats = list(dict.fromkeys(([p["type"]] if p.get("type") else []) + (p.get("types") or [])))
            if cid and cats:
                out[cid] = cats
    return out


@dataclass
class CheckData:
    job: AuditJob
    mode: str
    # keyword_id -> {"keyword", "location_name", "client_local_pack_rank", "client_local_finder_rank",
    #                "local_pack_shown"}
    keywords: dict
    seen: dict  # key -> Seen (client excluded)
    client_categories: list


def collect(db: Session, job: AuditJob) -> CheckData:
    mode = (job.params or {}).get("mode") or get_settings().ranking_mode
    runs = db.scalars(
        select(RankingRun).where(RankingRun.audit_job_id == job.id, RankingRun.status == "succeeded")
    ).all()
    keywords: dict = {}
    for run in runs:
        k = keywords.setdefault(
            run.keyword_id,
            {
                "keyword": run.keyword,
                "location_name": run.location_name,
                "client_local_pack_rank": None,
                "client_local_finder_rank": None,
                "local_pack_shown": None,
            },
        )
        if run.result_type == "local_pack":
            k["client_local_pack_rank"], k["local_pack_shown"] = run.client_rank, run.pack_shown
        else:
            k["client_local_finder_rank"] = run.client_rank
    if mode == "maps_only":  # Local Pack estimated from the Maps top 3
        for k in keywords.values():
            r = k["client_local_finder_rank"]
            k["client_local_pack_rank"] = r if r and r <= 3 else None

    run_by_id = {r.id: r for r in runs}
    results = (
        db.scalars(select(RankingResult).where(RankingResult.run_id.in_(list(run_by_id)))).all()
        if runs
        else []
    )
    raw_cats = _raw_categories(db, runs) if any(r.categories is None for r in results) else {}
    seen: dict[str, Seen] = {}
    client_categories: list = []
    for res in results:
        run = run_by_id[res.run_id]
        cats = (
            res.categories
            or raw_cats.get(res.cid or "")
            or ([res.primary_category] if res.primary_category else [])
        )
        if res.is_client_business:
            if len(cats) > len(client_categories):
                client_categories = list(cats)
            continue
        s = seen.setdefault(_key(res), Seen(key=_key(res)))
        s.names[res.business_name] += 1
        s.place_id = s.place_id or res.place_id
        s.cid = s.cid or res.cid
        s.address = s.address or res.address
        s.phone = s.phone or res.phone
        s.website_url = s.website_url or res.website_url
        s.maps_url = s.maps_url or res.maps_url
        s.rating = res.rating if res.rating is not None else s.rating
        s.review_count = max(filter(None, [s.review_count, res.review_count]), default=None)
        s.primary_category = s.primary_category or res.primary_category
        if len(cats) > len(s.categories):
            s.categories = list(cats)
        k = s.keywords.setdefault(
            run.keyword_id, {"keyword": run.keyword, "local_pack_rank": None, "local_finder_rank": None}
        )
        field_name = "local_pack_rank" if run.result_type == "local_pack" else "local_finder_rank"
        k[field_name] = min(filter(None, [k[field_name], res.rank]))
    if mode == "maps_only":
        for s in seen.values():
            for k in s.keywords.values():
                r = k["local_finder_rank"]
                k["local_pack_rank"] = r if r and r <= 3 else None
    return CheckData(job=job, mode=mode, keywords=keywords, seen=seen, client_categories=client_categories)


def select_competitors(data: CheckData, limit: int) -> list[tuple[Seen, dict]]:
    """Apply the brief's rule; keep the `limit` most visible (Local Pack first, then keyword share)."""
    n = len(data.keywords)
    picked = []
    for s in data.seen.values():
        share = len(s.keywords) / n if n else 0.0
        pack = sum(1 for k in s.keywords.values() if k["local_pack_rank"])
        finder = sum(1 for k in s.keywords.values() if k["local_finder_rank"])
        if not (share >= COMPETITOR_SHARE or pack >= COMPETITOR_PACK):
            continue
        ranks = [
            min(filter(None, [k["local_pack_rank"], k["local_finder_rank"]])) for k in s.keywords.values()
        ]
        reason = f"Appears for {len(s.keywords)} of {n} keywords ({share:.0%})"
        if pack:
            reason += f", in the Local Pack for {pack}"
        picked.append(
            (
                s,
                {
                    "keyword_share": round(share, 3),
                    "local_pack_count": pack,
                    "local_finder_count": finder,
                    "average_rank": round(sum(ranks) / len(ranks), 1),
                    "reason": reason,
                },
            )
        )
    picked.sort(key=lambda p: (-p[1]["local_pack_count"], -p[1]["keyword_share"], p[1]["average_rank"]))
    return picked[:limit]


# ---------- competitor profiles ----------


def _business_for(db: Session, s: Seen) -> Business:
    business = None
    if s.place_id:
        business = db.scalar(select(Business).where(Business.place_id == s.place_id))
    if business is None and s.cid:
        business = db.scalar(select(Business).where(Business.cid == s.cid))
    if business is None:
        business = Business(name=s.name, source="serpapi", is_client=False)
        db.add(business)
    business.place_id = business.place_id or s.place_id
    business.cid = business.cid or s.cid
    business.name = s.name or business.name
    business.domain = _domain(s.website_url) or business.domain
    db.flush()
    return business


def fetch_details(db: Session, place_id: str) -> tuple[dict | None, str | None]:
    """Place Details for a competitor's review sample (cached). Returns (place, note)."""
    settings = get_settings()
    params = {"place_id": place_id, "fieldMask": DETAILS_FIELDS}
    cached = get_cached(
        db, PLACES, "places:get:competitor", params, timedelta(days=settings.competitor_details_cache_days)
    )
    if cached is not None:
        return cached.response or {}, None
    client = get_places_client()
    if client is None:
        return None, "GOOGLE_API_KEY not set"
    try:
        quota.consume(db, "google_places_details")
    except quota.QuotaExceeded as exc:
        return None, str(exc)
    try:
        place = client.place_details(place_id, DETAILS_FIELDS)
    except (ProviderError, httpx.HTTPError) as exc:
        db.rollback()
        if isinstance(exc, ProviderError) and exc.status_code in (400, 401, 403, 404):
            quota.release(db, "google_places_details")
        return None, f"{type(exc).__name__}: {exc}"
    store_response(
        db,
        PLACES,
        "places:get:competitor",
        params,
        place,
        200,
        ttl=timedelta(days=settings.google_data_ttl_days),
    )
    return place, None


def review_sample_topics(place: dict, business_name: str | None) -> tuple[dict, list[str], int]:
    """Topics praised / criticised in the public review sample, and service phrases mentioned."""
    pos, neg, phrases = Counter(), Counter(), Counter()
    reviews = place.get("reviews") or []
    for r in reviews:
        text = (r.get("text") or r.get("originalText") or {}).get("text")
        lang = (r.get("originalText") or r.get("text") or {}).get("languageCode")
        a = analyze_review(text, r.get("rating"), lang, None, business_name)
        for t in a.tags:
            if t.theme == "specific_service":
                continue
            if t.sentiment == "positive":
                pos[t.tag] += 1
            elif t.sentiment == "negative":
                neg[t.tag] += 1
        for p in set(a.service_phrases):
            phrases[p] += 1
    return (
        {"positive": dict(pos), "negative": dict(neg)},
        [p for p, _ in phrases.most_common(10)],
        len(reviews),
    )


def _velocity(
    old_count: int | None, old_at: datetime | None, new_count: int | None, new_at: datetime
) -> float | None:
    if old_count is None or new_count is None or old_at is None:
        return None
    days = (_aware(new_at) - _aware(old_at)).total_seconds() / 86400
    if days < VELOCITY_MIN_DAYS:
        return None
    return round((new_count - old_count) / days * 30, 1)


def _previous_metric(db: Session, competitor: Competitor, before: datetime) -> CompetitorMetric | None:
    rows = db.scalars(
        select(CompetitorMetric)
        .where(CompetitorMetric.competitor_id == competitor.id)
        .order_by(CompetitorMetric.snapshot_at.desc())
    ).all()
    limit = _aware(before) - timedelta(days=VELOCITY_MIN_DAYS)
    return next((m for m in rows if _aware(m.snapshot_at) <= limit), None)


# ---------- the client, measured the same way ----------


def client_snapshot(db: Session, project: Project, data: CheckData | None) -> dict:
    business = project.client_business
    profiles = (
        db.scalars(
            select(GbpProfile)
            .where(GbpProfile.business_id == business.id)
            .order_by(GbpProfile.created_at.desc())
        ).all()
        if business
        else []
    )
    latest = profiles[0] if profiles else None
    velocity = None
    if latest is not None:
        limit = _aware(latest.created_at) - timedelta(days=VELOCITY_MIN_DAYS)
        old = next((p for p in profiles[1:] if _aware(p.created_at) <= limit), None)
        if old is not None:
            velocity = _velocity(old.review_count, old.created_at, latest.review_count, latest.created_at)
    categories = list(data.client_categories) if data else []
    if latest is not None:
        for c in [latest.primary_category, *(latest.secondary_categories or [])]:
            if c and c.lower() not in {x.lower() for x in categories}:
                categories.append(c)
    services = db.scalars(
        select(ProjectService.name).where(
            ProjectService.project_id == project.id, ProjectService.kind == "service"
        )
    ).all()
    pos, neg = Counter(), Counter()
    if business is not None:
        tags = db.execute(
            select(ReviewTag.tag, ReviewTag.sentiment)
            .join(GbpReview, GbpReview.id == ReviewTag.review_id)
            .where(GbpReview.business_id == business.id, ReviewTag.theme != "specific_service")
        ).all()
        for tag, sentiment in tags:
            (pos if sentiment == "positive" else neg if sentiment == "negative" else Counter())[tag] += 1
    sample = (
        db.scalar(select(GbpReview.id).where(GbpReview.business_id == business.id).limit(1))
        if business
        else None
    )
    keywords = data.keywords if data else {}
    return {
        "business_name": (latest.business_name if latest else None) or (business.name if business else None),
        "place_id": business.place_id if business else None,
        "primary_category": latest.primary_category if latest else None,
        "categories": categories,
        "rating": latest.rating if latest else None,
        "review_count": latest.review_count if latest else None,
        "review_velocity_30d": velocity,
        "website_domain": business.domain if business else None,
        "services": list(services),
        "review_topics": {"positive": dict(pos), "negative": dict(neg)},
        "has_reviews": sample is not None,
        "local_pack_count": sum(1 for k in keywords.values() if k["client_local_pack_rank"]),
        "local_finder_count": sum(1 for k in keywords.values() if k["client_local_finder_rank"]),
        "keywords": len(keywords),
    }


# ---------- the analysis ----------


def latest_ranking_check(db: Session, project: Project) -> AuditJob:
    checks = ranking_checks(db, project, limit=1)
    if not checks:
        raise NoRankingCheck(
            "No ranking check yet: competitors come from the ranking results (run a check first)"
        )
    return checks[0]


def analyze(db: Session, project: Project, job: AuditJob, ranking_job: AuditJob | None = None) -> dict:
    """Pick competitors, snapshot their profiles, and rebuild the gap list. `job` owns the snapshots."""
    from app.services.gaps import find_gaps  # gap rules live in their own module

    settings = get_settings()
    ranking_job = ranking_job or latest_ranking_check(db, project)
    data = collect(db, ranking_job)
    picked = select_competitors(data, settings.competitor_max)
    now = utcnow()
    details_used = details_cached = 0
    notes: list[str] = []
    competitor_rows: list[dict] = []
    client_kw = {
        kid
        for kid, k in data.keywords.items()
        if k["client_local_pack_rank"] or k["client_local_finder_rank"]
    }
    n = len(data.keywords)

    for s, stats in picked:
        business = _business_for(db, s)
        comp = db.scalar(
            select(Competitor).where(
                Competitor.project_id == project.id, Competitor.business_id == business.id
            )
        )
        if comp is None:
            comp = Competitor(
                project_id=project.id, business_id=business.id, first_seen_at=now, keyword_share=0, reason=""
            )
            db.add(comp)
        comp.keyword_share = stats["keyword_share"]
        comp.local_pack_count = stats["local_pack_count"]
        comp.local_finder_count = stats["local_finder_count"]
        comp.reason = stats["reason"][:200]
        comp.last_seen_at = now
        db.flush()

        topics, phrases, sample, source = None, [], None, "search_results"
        rating, review_count, website = s.rating, s.review_count, s.website_url
        primary = s.primary_category
        if settings.competitor_details and s.place_id:
            was_cached = (
                get_cached(
                    db,
                    PLACES,
                    "places:get:competitor",
                    {"place_id": s.place_id, "fieldMask": DETAILS_FIELDS},
                    timedelta(days=settings.competitor_details_cache_days),
                )
                is not None
            )
            place, note = fetch_details(db, s.place_id)
            if place:
                details_cached += was_cached
                details_used += not was_cached
                source = "google_places"
                topics, phrases, sample = review_sample_topics(place, s.name)
                rating = place.get("rating", rating)
                review_count = place.get("userRatingCount", review_count)
                website = place.get("websiteUri") or website
                primary = primary or (place.get("primaryTypeDisplayName") or {}).get("text")
            elif note:
                notes.append(f"{s.name}: {note}")

        prev = _previous_metric(db, comp, now)
        velocity = _velocity(prev.review_count, prev.snapshot_at, review_count, now) if prev else None
        both = len(client_kw & set(s.keywords))
        keyword_list = [
            {
                **k,
                "client_local_pack_rank": data.keywords[kid]["client_local_pack_rank"],
                "client_local_finder_rank": data.keywords[kid]["client_local_finder_rank"],
            }
            for kid, k in s.keywords.items()
            if kid in data.keywords
        ]
        metric = CompetitorMetric(
            competitor_id=comp.id,
            audit_job_id=job.id,
            snapshot_at=now,
            primary_category=primary,
            rating=rating,
            review_count=review_count,
            review_velocity_30d=velocity,
            categories=s.categories or ([primary] if primary else None),
            services=phrases or None,
            website_domain=_domain(website),
            local_pack_appearances=stats["local_pack_count"],
            local_finder_appearances=stats["local_finder_count"],
            keyword_overlap=round(both / n, 3) if n else None,
            keywords=sorted(keyword_list, key=lambda k: k["keyword"] or ""),
            review_topics=topics,
            review_sample_size=sample,
            profile_source=source,
            source="serpapi_ranking_results",
            source_url=f"ranking_check:{ranking_job.id}",
            collected_at=now,
            last_verified_at=now,
        )
        db.add(metric)
        competitor_rows.append(competitor_out(comp, business, metric, s))
    db.commit()

    client = client_snapshot(db, project, data)
    gaps = find_gaps(project, client, competitor_rows, data)
    db.execute(delete(GapRecommendation).where(GapRecommendation.project_id == project.id))
    for g in gaps:
        db.add(GapRecommendation(project_id=project.id, audit_job_id=job.id, **g))
    db.commit()
    return {
        "ranking_check_id": str(ranking_job.id),
        "keywords": n,
        "businesses_seen": len(data.seen),
        "competitors": len(competitor_rows),
        "competitor_names": [c["business_name"] for c in competitor_rows],
        "place_details_calls": details_used,
        "place_details_from_cache": details_cached,
        "serpapi_searches": 0,
        "gaps": dict(Counter(g["gap_type"] for g in gaps)),
        "notes": notes[:10],
    }


def competitor_out(
    comp: Competitor, business: Business, metric: CompetitorMetric, s: Seen | None = None
) -> dict:
    return {
        "competitor_id": str(comp.id),
        "business_name": business.name,
        "place_id": business.place_id,
        "cid": business.cid,
        "maps_url": (s.maps_url if s else None)
        or (f"https://maps.google.com/?cid={business.cid}" if business.cid else None),
        "address": s.address if s else None,
        "website_domain": metric.website_domain,
        "primary_category": metric.primary_category,
        "categories": metric.categories or [],
        "rating": metric.rating,
        "review_count": metric.review_count,
        "review_velocity_30d": metric.review_velocity_30d,
        "keyword_share": comp.keyword_share,
        "local_pack_count": comp.local_pack_count,
        "local_finder_count": comp.local_finder_count,
        "keyword_overlap": metric.keyword_overlap,
        "reason": comp.reason,
        "services": metric.services or [],
        "review_topics": metric.review_topics,
        "review_sample_size": metric.review_sample_size,
        "profile_source": metric.profile_source,
        "keywords": metric.keywords or [],
        "snapshot_at": _aware(metric.snapshot_at).isoformat(),
    }


def latest_analysis_job(db: Session, project: Project) -> AuditJob | None:
    job_id = db.scalar(
        select(CompetitorMetric.audit_job_id)
        .join(Competitor, Competitor.id == CompetitorMetric.competitor_id)
        .where(Competitor.project_id == project.id, CompetitorMetric.audit_job_id.is_not(None))
        .order_by(CompetitorMetric.snapshot_at.desc())
        .limit(1)
    )
    return db.get(AuditJob, job_id) if job_id else None


def competitors_report(db: Session, project: Project) -> dict:
    job = latest_analysis_job(db, project)
    if job is None:
        return {"analysis": None, "client": None, "competitors": [], "rule": RULE}
    rows = db.execute(
        select(CompetitorMetric, Competitor, Business)
        .join(Competitor, Competitor.id == CompetitorMetric.competitor_id)
        .join(Business, Business.id == Competitor.business_id)
        .where(Competitor.project_id == project.id, CompetitorMetric.audit_job_id == job.id)
    ).all()
    comps = [competitor_out(c, b, m) for m, c, b in rows]
    comps.sort(key=lambda c: (-c["local_pack_count"], -c["keyword_share"]))
    ranking_job_id = next(
        (
            s.get("result", {}).get("ranking_check_id")
            for s in job.steps or []
            if s.get("name") == "find_competitors"
        ),
        None,
    ) or (str(job.id) if job.job_type == "ranking_check" else None)
    ranking_job = db.get(AuditJob, _uuid(ranking_job_id)) if ranking_job_id else None
    data = collect(db, ranking_job) if ranking_job else None
    return {
        "analysis": {
            "job_id": str(job.id),
            "analysed_at": _aware(job.created_at).isoformat(),
            "ranking_check_id": ranking_job_id,
            "keywords": len(data.keywords) if data else None,
            "competitors": len(comps),
        },
        "rule": RULE,
        "client": client_snapshot(db, project, data),
        "competitors": comps,
    }


def _uuid(value: str):
    import uuid

    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def city_of(project: Project) -> str | None:
    areas = project.service_areas or []
    name = areas[0].get("name") if areas else None
    return re.split(r",", name)[0].strip() if name else None


def median(values: list) -> float | None:
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None
