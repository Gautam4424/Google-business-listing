"""Phase 6: one clean service list per project, merged from every source (brief Step 4).

Sources (rows in `services`): Google categories, website (schema / service pages / sitemap / headings), review
topics. They are merged into `project_services`:
  - location words removed ("General Plumbing Sydney" -> "General Plumbing"), singular/plural merged
  - classified: service | customer_type ("Schools & Day Care Centres") | generic (Google's "Home goods store")
  - scored: best source confidence + agreement between source families + review mentions
  - the user's choices (selected, renamed, added, kind) survive every rebuild
"""

import re
from collections import Counter, defaultdict

from rapidfuzz import fuzz
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import GbpProfile, GbpReview, Project, ProjectService, ReviewTag, Service

DEFAULT_SELECTED = 5
FAMILY = {
    "gbp_category": "google",
    "website_schema": "website",
    "website_service_page": "website",
    "website_sitemap": "website",
    "website_heading": "website",
    "review_topic": "reviews",
    "user": "you",
}
GENERIC_TYPES = {
    "home goods store", "supplier", "store", "building materials store", "hardware store", "shopping mall",
    "point of interest", "establishment", "service", "corporate office", "consultant",
}  # fmt: skip
AUDIENCE = re.compile(
    r"\b(schools?|day care|childcare|hospitality|strata|associations?|owners?|industr(y|ies)|facilities"
    r"|property manage\w*|real estate|pubs?|clubs?|restaurants?|councils?|government|hotels?|retail"
    r"|landlords?|tenants?|builders?|developers?|agents?|homeowners?|businesses|commercial assets|offices)\b",
    re.IGNORECASE,
)
SERVICE_HINT = re.compile(
    r"(plumb|drain|repair|install|replac|renovat|remodel|clean|inspect|paint|gas\b|hot water|leak|pipe|roof"
    r"|mainten|fitting|design|construct|build|permit|drawing|electric|heat|cool|hvac|boiler|tile|floor|kitchen"
    r"|bathroom|basement|addition|landscap|removal|pest|lock|glass|window|door|fenc|deck|concrete|carpent"
    r"|toilet|shower|tap\b|sewer|septic|water|tank|detect|jet|cctv|treatment|massage|cut|dental|clinic)",
    re.IGNORECASE,
)
STOP_EDGE = {
    "the",
    "a",
    "an",
    "and",
    "our",
    "your",
    "best",
    "top",
    "professional",
    "expert",
    "local",
    "affordable",
}


# ---------- normalisation ----------


def _singular(word: str) -> str:
    if len(word) <= 3 or word.endswith(("ss", "us", "is")):
        return word
    if word.endswith("ies"):
        return word[:-3] + "y"
    if word.endswith(("ches", "shes", "xes", "zes", "sses")):
        return word[:-2]
    return word[:-1] if word.endswith("s") else word


def location_words(project: Project, profile: GbpProfile | None, names: list[str]) -> set[str]:
    """Place names to strip from service names: areas, addresses, and words that end many offerings."""

    def letters(text: str) -> set[str]:
        return {w.lower() for w in re.findall(r"[A-Za-z]{3,}", text)}

    words: set[str] = set()
    for area in project.service_areas or []:
        words |= letters(area.get("name", ""))
    addresses = [project.input_address, profile.formatted_address if profile else None]
    for address in filter(None, addresses):
        for part in address.split(",")[1:]:  # skip the street ("Water St" must not remove "water")
            words |= letters(part)
    # "Gas Leak Detection Sydney", "Shower Repairs Sydney"...: a capitalised last word repeated 3+ times.
    endings = Counter(n.split()[-1] for n in names if n.split() and n.split()[-1][:1].isupper())
    words |= {w.lower() for w, c in endings.items() if c >= 3 and not SERVICE_HINT.search(w)}
    return {w for w in words if not SERVICE_HINT.search(w)} - {"services", "service", "plumber", "near"}


def clean_display(name: str, places: set[str]) -> str:
    text = re.sub(r"&amp;", "&", name)
    text = re.sub(r"\s+(?i:in|near|around|across)\s+[A-Z][\w\s,]*$", "", text.strip())  # "Schools In Sydney"
    words = [w for w in re.split(r"\s+", text) if w and w.lower().strip(",.") not in places]
    while words and words[0].lower() in STOP_EDGE:
        words.pop(0)
    while words and words[-1].lower() in STOP_EDGE | {
        "-",
        "|",
        "in",
        "near",
        "around",
        "across",
        "of",
        "for",
    }:
        words.pop()
    out = " ".join(words).strip(" ,.-|")
    return out[:1].upper() + out[1:] if out else ""


def normalize(name: str) -> str:
    text = name.lower().replace("&", " and ")
    words = re.findall(r"[a-z0-9]+", text)
    if words:
        words[-1] = _singular(words[-1])
    return " ".join(words)


def classify(name: str, families: set[str]) -> str:
    key = name.lower()
    if key in GENERIC_TYPES:
        return "generic"
    if "google" in families:  # "Pizza restaurant" is what the business is, not who it serves
        return "service"
    if AUDIENCE.search(name) and not SERVICE_HINT.search(name):
        return "customer_type"
    return "service"


# ---------- build ----------


def _review_mentions(db: Session, project: Project) -> Counter:
    if project.client_business is None:
        return Counter()
    rows = db.execute(
        select(ReviewTag.tag)
        .join(GbpReview, GbpReview.id == ReviewTag.review_id)
        .where(GbpReview.business_id == project.client_business_id, ReviewTag.theme == "specific_service")
    ).scalars()
    return Counter(normalize(t) for t in rows)


def build_catalog(db: Session, project: Project) -> dict:
    rows = db.scalars(select(Service).where(Service.project_id == project.id)).all()
    profile = None
    if project.client_business_id:
        profile = db.scalar(
            select(GbpProfile)
            .where(GbpProfile.business_id == project.client_business_id)
            .order_by(GbpProfile.created_at.desc())
        )
    places = location_words(project, profile, [r.service_name for r in rows])

    groups: dict[str, dict] = {}
    for r in rows:
        display = clean_display(r.service_name, places)
        if len(display) < 3:
            continue
        key = normalize(display)
        # fuzzy merge with an existing group ("CCTV Drain Inspections" vs "CCTV Drain Inspection")
        match = next((k for k in groups if fuzz.token_sort_ratio(k, key) >= 92), None)
        g = groups.setdefault(
            match or key, {"names": Counter(), "sources": [], "best": 0.0, "primary": False}
        )
        g["names"][display] += 1
        conf = r.confidence_score or 0.5
        g["best"] = max(g["best"], conf)
        g["primary"] |= r.source == "gbp_category" and conf >= 0.95
        g["sources"].append(
            {"source": r.source, "name": r.service_name, "source_url": r.source_url, "confidence": conf}
        )

    mentions = _review_mentions(db, project)
    existing = {s.normalized_name: s for s in db.scalars(
        select(ProjectService).where(ProjectService.project_id == project.id)
    )}  # fmt: skip
    first_build = not existing
    built: dict[str, ProjectService] = {}
    for key, g in groups.items():
        name = sorted(g["names"].items(), key=lambda kv: (-kv[1], len(kv[0])))[0][0]
        families = {FAMILY.get(s["source"], "other") for s in g["sources"]}
        n_mentions = mentions.get(key, 0)
        long_penalty = 0.05 * max(
            0, len(name.split()) - 3
        )  # "blocked drains" beats "clearing of blocked drains"
        score = (
            g["best"] + 0.1 * (len(families) - 1) + min(0.2, 0.05 * n_mentions) + (0.1 if g["primary"] else 0)
        ) - long_penalty
        row = existing.get(key) or ProjectService(project_id=project.id, normalized_name=key, selected=False)
        if not row.user_edited:
            row.name = name
            row.kind = classify(name, families)
        row.score = round(score, 3)
        row.review_mentions = n_mentions
        row.sources = g["sources"]
        db.add(row)
        built[key] = row

    # Keep services the user added or edited even if no source mentions them any more.
    for key, row in existing.items():
        if key not in built:
            if row.user_added or row.user_edited:
                row.sources = [s for s in row.sources if s["source"] == "user"]
                built[key] = row
            else:
                db.delete(row)

    if first_build:
        ranked = sorted(
            (r for r in built.values() if r.kind == "service"),
            key=lambda r: (
                -(any(s["source"] == "gbp_category" and s["confidence"] >= 0.95 for s in r.sources)),
                -r.score,
            ),
        )
        for r in ranked[:DEFAULT_SELECTED]:
            r.selected = True
    db.commit()
    by_kind = Counter(r.kind for r in built.values())
    return {
        "services": by_kind.get("service", 0),
        "customer_types": by_kind.get("customer_type", 0),
        "generic": by_kind.get("generic", 0),
        "selected": sum(1 for r in built.values() if r.selected),
        "location_words_removed": sorted(places)[:20],
    }


def add_user_service(db: Session, project: Project, name: str) -> ProjectService:
    display = name.strip()
    key = normalize(display)
    row = db.scalar(
        select(ProjectService).where(
            ProjectService.project_id == project.id, ProjectService.normalized_name == key
        )
    )
    if row is None:
        row = ProjectService(project_id=project.id, normalized_name=key, name=display, score=1.0, sources=[])
        db.add(row)
    row.name, row.kind, row.selected, row.user_added, row.user_edited = display, "service", True, True, True
    row.sources = [
        *(row.sources or []),
        {"source": "user", "name": display, "source_url": None, "confidence": 1.0},
    ]
    db.commit()
    return row


def services_by_kind(db: Session, project: Project) -> dict[str, list[ProjectService]]:
    out: dict[str, list] = defaultdict(list)
    for r in db.scalars(
        select(ProjectService)
        .where(ProjectService.project_id == project.id)
        .order_by(ProjectService.selected.desc(), ProjectService.score.desc(), ProjectService.name)
    ):
        out[r.kind].append(r)
    return out


def clear_catalog(db: Session, project: Project) -> None:
    db.execute(delete(ProjectService).where(ProjectService.project_id == project.id))
    db.commit()
