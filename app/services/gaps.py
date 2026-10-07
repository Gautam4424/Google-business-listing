"""Phase 8 gap rules (brief §3): category, service, review, review-topic and ranking gaps.

Brief: never recommend adding something only because competitors have it. Category and service
recommendations are phrased as "review whether ... is accurate and eligible" and say whether the
client's own services support it. Every gap carries its evidence (which competitors, which keywords).
"""

import math
import re
from collections import Counter

from rapidfuzz import fuzz

from app.models import Project
from app.services.competitors import CheckData, city_of, median
from app.services.service_catalog import GENERIC_TYPES, normalize

MIN_COMPETITORS = 2  # a pattern needs at least this many competitors
CATEGORY_SHARE = 0.5  # category held by at least half of the competitors (with category data)
MAX_SERVICE_GAPS = 8
GENERIC_WORDS = {
    "service", "services", "contractor", "company", "store", "shop", "supplier", "business", "agency",
    "center", "centre", "specialist", "and", "the", "of", "for", "repair", "repairs",
}  # fmt: skip
# Google labels that are not real business categories (never a gap).
GENERIC_CATEGORIES = {normalize(x) for x in GENERIC_TYPES} | {
    "service establishment", "establishment", "point of interest", "business", "corporate office",
}  # fmt: skip
TOPIC_SHARE = 0.3  # a review topic must be praised for at least this share of sampled competitors
PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}
TYPE_ORDER = {"ranking": 0, "category": 1, "review": 2, "service": 3, "review_topic": 4}


def _tokens(text: str) -> set[str]:
    return {w for w in normalize(text).split() if len(w) > 2 and w not in GENERIC_WORDS}


def _words_related(a: str, b: str) -> bool:
    """Same stem ("drainage"/"drain") or one word starts the other ("gasfitter"/"gas")."""
    return a[:5] == b[:5] or (min(len(a), len(b)) >= 3 and (a.startswith(b) or b.startswith(a)))


def related_services(name: str, services: list[str]) -> list[str]:
    """Client services that plausibly support a category/service ("Drainage service" ~ "Blocked drains")."""
    want = _tokens(name)
    out = []
    for s in services:
        have = _tokens(s)
        if (
            any(_words_related(a, b) for a in want for b in have)
            or fuzz.token_set_ratio(normalize(name), normalize(s)) >= 85
        ):
            out.append(s)
    return out[:3]


def _known(name: str, client_norm: set[str], services_norm: list[str]) -> bool:
    n = normalize(name)
    return n in client_norm or any(fuzz.token_set_ratio(n, s) >= 88 for s in services_norm)


def _names(rows: list[dict], limit: int = 4) -> str:
    names = [r["business_name"] for r in rows]
    more = len(names) - limit
    return ", ".join(names[:limit]) + (f" and {more} more" if more > 0 else "")


def category_gaps(client: dict, comps: list[dict], name: str) -> tuple[list[dict], set[str]]:
    with_cats = [c for c in comps if c["categories"]]
    if len(with_cats) < MIN_COMPETITORS:
        return [], set()
    display: dict[str, str] = {}
    holders: dict[str, list[dict]] = {}
    for c in with_cats:
        for cat in dict.fromkeys(normalize(x) for x in c["categories"]):
            holders.setdefault(cat, []).append(c)
        for x in c["categories"]:
            display.setdefault(normalize(x), x)
    counts = Counter({k: len(v) for k, v in holders.items() if k not in GENERIC_CATEGORIES})
    pattern = [display[k] for k, _ in counts.most_common(6)]
    client_norm = {normalize(x) for x in client["categories"]}
    gaps, flagged = [], set()
    for cat, rows in sorted(holders.items(), key=lambda kv: -len(kv[1])):
        share = len(rows) / len(with_cats)
        if len(rows) < MIN_COMPETITORS or share < CATEGORY_SHARE or cat in client_norm | GENERIC_CATEGORIES:
            continue
        flagged.add(cat)
        label = display[cat]
        support = related_services(label, client["services"])
        text = f"Review whether '{label}' is an accurate and eligible additional category for {name}."
        text += (
            f" The business lists {', '.join(support)}, which may support it."
            if support
            else " Only add it if the business really provides this; never just because competitors use it."
        )
        gaps.append({
            "gap_type": "category",
            "title": f"Category used by {len(rows)} of {len(with_cats)} competitors: {label}",
            "priority": "high" if support else "medium" if share >= 0.75 else "low",
            "client_value": client["categories"],
            "competitor_pattern": pattern,
            "recommendation": text,
            "evidence": {"category": label, "competitors": [r["business_name"] for r in rows],
                         "share": f"{len(rows)} of {len(with_cats)}", "related_client_services": support},
        })  # fmt: skip
    return gaps, flagged


def service_gaps(
    client: dict, comps: list[dict], name: str, flagged: set[str], city: str | None
) -> list[dict]:
    client_norm = {normalize(x) for x in client["categories"]}
    services_norm = [normalize(s) for s in client["services"]]
    display: dict[str, str] = {}
    holders: dict[str, list[tuple[dict, str]]] = {}
    for c in comps:
        items = {normalize(x): (x, "Google category") for x in c["categories"]}
        items.update({normalize(x): (x, "their reviews") for x in c["services"] if normalize(x) not in items})
        for key, (label, where) in items.items():
            holders.setdefault(key, []).append((c, where))
            display.setdefault(key, label[:1].upper() + label[1:])
    gaps = []
    for key, rows in sorted(holders.items(), key=lambda kv: -len(kv[1])):
        if (
            len(rows) < MIN_COMPETITORS
            or key in flagged | GENERIC_CATEGORIES
            or _known(display[key], client_norm, services_norm)
        ):
            continue
        label = display[key]
        keyword = f"{label.lower()} in {city}" if city else f"{label.lower()} near me"
        gaps.append({
            "gap_type": "service",
            "title": f"Service mentioned for {len(rows)} competitors: {label}",
            "priority": "medium" if len(rows) / max(len(comps), 1) >= 0.5 else "low",
            "client_value": client["services"][:12],
            "competitor_pattern": [label],
            "recommendation": f"Review whether {name} offers '{label}'. If it does, describe it on the website and "
                              "in the Google profile, and consider tracking it as a keyword; if not, ignore this.",
            "evidence": {"service": label, "competitors": [r[0]["business_name"] for r in rows],
                         "found_in": sorted({r[1] for r in rows}), "suggested_keyword": keyword},
        })  # fmt: skip
        if len(gaps) >= MAX_SERVICE_GAPS:
            break
    return gaps


def review_gaps(client: dict, comps: list[dict], name: str) -> list[dict]:
    gaps = []
    counts = [c["review_count"] for c in comps if c["review_count"] is not None]
    mid = median(counts)
    mine = client["review_count"]
    if mid is not None and mine is not None and mine < mid:
        gaps.append({
            "gap_type": "review",
            "title": f"Fewer reviews than most competitors ({mine:,} vs median {mid:,.0f})",
            "priority": "high" if mine < mid * 0.5 else "medium",
            "client_value": {"review_count": mine},
            "competitor_pattern": {"median_review_count": mid, "range": [min(counts), max(counts)]},
            "recommendation": f"Competitors have a median of {mid:,.0f} Google reviews; {name} has {mine:,}. Review "
                              "how the business asks customers for reviews: ask every customer, and never offer "
                              "incentives or ask only happy customers (Google's review policy).",
            "evidence": {"competitors": {c["business_name"]: c["review_count"] for c in comps
                                         if c["review_count"] is not None}},
        })  # fmt: skip
    ratings = [c["rating"] for c in comps if c["rating"] is not None]
    mid_rating = median(ratings)
    if mid_rating is not None and client["rating"] is not None and client["rating"] < mid_rating - 0.1:
        gaps.append({
            "gap_type": "review",
            "title": f"Lower rating than most competitors ({client['rating']} vs median {mid_rating:.1f})",
            "priority": "high" if client["rating"] < mid_rating - 0.4 else "medium",
            "client_value": {"rating": client["rating"]},
            "competitor_pattern": {"median_rating": round(mid_rating, 2)},
            "recommendation": "Review the negative themes in the client's reviews (see the review insights) and reply "
                              "to critical reviews; the rating is below the competitors' median.",
            "evidence": {"competitors": {c["business_name"]: c["rating"] for c in comps if c["rating"] is not None}},
        })  # fmt: skip
    speeds = [c["review_velocity_30d"] for c in comps if c["review_velocity_30d"] is not None]
    mid_speed = median(speeds)
    mine_speed = client["review_velocity_30d"]
    if mid_speed is not None and mine_speed is not None and mine_speed < mid_speed:
        gaps.append({
            "gap_type": "review",
            "title": f"Getting reviews more slowly ({mine_speed}/month vs median {mid_speed:.1f})",
            "priority": "medium",
            "client_value": {"review_velocity_30d": mine_speed},
            "competitor_pattern": {"median_review_velocity_30d": mid_speed},
            "recommendation": "Competitors are gaining new reviews faster. Review whether asking for reviews is part "
                              "of every completed job.",
            "evidence": {"competitors": {c["business_name"]: c["review_velocity_30d"] for c in comps
                                         if c["review_velocity_30d"] is not None}},
        })  # fmt: skip
    return gaps


def review_topic_gaps(client: dict, comps: list[dict], name: str) -> list[dict]:
    sampled = [c for c in comps if c["review_topics"] and c["review_sample_size"]]
    if len(sampled) < MIN_COMPETITORS or not client["has_reviews"]:
        return []
    praised: dict[str, list[dict]] = {}
    for c in sampled:
        for tag in c["review_topics"].get("positive") or {}:
            praised.setdefault(tag, []).append(c)
    mine_pos = set(client["review_topics"]["positive"])
    mine_neg = set(client["review_topics"]["negative"])
    gaps = []
    needed = max(MIN_COMPETITORS, math.ceil(TOPIC_SHARE * len(sampled)))
    for tag, rows in sorted(praised.items(), key=lambda kv: -len(kv[1])):
        if len(rows) < needed or (tag in mine_pos and tag not in mine_neg):
            continue
        negative = tag in mine_neg
        gaps.append({
            "gap_type": "review_topic",
            "title": f"Competitors praised for '{tag}'" + (" (a complaint in your reviews)" if negative else ""),
            "priority": "high" if negative else "low",
            "client_value": {"positive_topics": sorted(mine_pos), "negative_topics": sorted(mine_neg)},
            "competitor_pattern": [tag],
            "recommendation": (
                f"Customers praise {len(rows)} competitors for '{tag}', while {name}'s reviews "
                + ("mention it as a complaint." if negative else "don't mention it.")
                + " Review whether the business delivers this and whether customers notice it."
            ),
            "evidence": {"topic": tag, "competitors": [r["business_name"] for r in rows],
                         "basis": "Public review samples (up to 5 reviews per business from Google)"},
        })  # fmt: skip
    return gaps[:6]


def ranking_gaps(client: dict, comps: list[dict], data: CheckData, name: str) -> list[dict]:
    gaps = []
    for k in data.keywords.values():
        if k["client_local_pack_rank"] or k["local_pack_shown"] is False:
            continue
        in_pack = []
        for c in comps:
            for row in c["keywords"]:
                if row["keyword"] == k["keyword"] and row["local_pack_rank"]:
                    in_pack.append((row["local_pack_rank"], c["business_name"]))
        if not in_pack:
            continue
        in_pack.sort()
        finder = k["client_local_finder_rank"]
        where = f"Maps rank #{finder}" if finder else "not in the Maps top 20"
        est = " (Local Pack estimated from Maps)" if data.mode == "maps_only" else ""
        gaps.append({
            "gap_type": "ranking",
            "title": f"Not in the Local Pack for '{k['keyword']}'{est}",
            "priority": "high" if not finder or finder > 10 else "medium",
            "client_value": {"keyword": k["keyword"], "local_pack_rank": None, "local_finder_rank": finder},
            "competitor_pattern": [f"#{r} {n}" for r, n in in_pack],
            "recommendation": f"{_names([{'business_name': n} for _, n in in_pack], 3)} appear in the Local Pack for "
                              f"'{k['keyword']}' but {name} does not ({where}). Review whether the profile's categories, "
                              "services and website clearly cover this search.",
            "evidence": {"keyword": k["keyword"], "location": k["location_name"],
                         "local_pack": [{"rank": r, "business_name": n} for r, n in in_pack]},
        })  # fmt: skip
    return gaps


def find_gaps(project: Project, client: dict, comps: list[dict], data: CheckData) -> list[dict]:
    if not comps:
        return []
    name = client["business_name"] or project.name
    name = re.sub(r"\s+", " ", name).strip()
    cat, flagged = category_gaps(client, comps, name)
    gaps = (
        cat
        + service_gaps(client, comps, name, flagged, city_of(project))
        + review_gaps(client, comps, name)
        + review_topic_gaps(client, comps, name)
        + ranking_gaps(client, comps, data, name)
    )
    gaps.sort(key=lambda g: (PRIORITY_ORDER[g["priority"]], TYPE_ORDER[g["gap_type"]]))
    return gaps


def gap_out(g) -> dict:
    return {
        "gap_type": g.gap_type,
        "title": g.title,
        "priority": g.priority,
        "client_value": g.client_value,
        "competitor_pattern": g.competitor_pattern,
        "recommendation": g.recommendation,
        "evidence": g.evidence,
    }
