"""Phase 3: score Google candidates against what we know about the business (brief §1, plan §7.1).

Reference ("what we know") comes from the business's own website first (independent evidence) and falls back
to what the user entered. Each candidate gets a 0..1 confidence from weighted signals; signals that cannot be
compared (missing on either side) are left out and their weight is shared by the others.
"""

import re
from dataclasses import asdict, dataclass, field
from urllib.parse import urlsplit

from rapidfuzz import fuzz

from app.services.nap import clean_name, haversine_m, normalize_phone, postcode, street_key

WEIGHTS = {"name": 0.30, "address": 0.25, "phone": 0.20, "domain": 0.15, "distance": 0.10}
AUTO_SELECT = 0.85
# With too little evidence (e.g. only a name), never auto-select.
MIN_EVIDENCE_WEIGHT = 0.5
WEAK_EVIDENCE_CAP = 0.80
DUPLICATE_MARGIN = 0.05


@dataclass
class Reference:
    """What we know about the business, with where each value came from."""

    name: str | None = None
    address: str | None = None
    phone_e164: str | None = None
    domain: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    region: str | None = None
    sources: dict = field(default_factory=dict)
    other_addresses: list[str] = field(default_factory=list)  # chains: every location on the website
    other_phones: list[str] = field(default_factory=list)


@dataclass
class Score:
    place_id: str | None
    match_confidence: float
    match_reasons: list[str]
    mismatch_reasons: list[str]
    signals: dict  # signal -> {"score", "weight", "detail"} (only compared signals)
    evidence_weight: float


def domain_of(url: str | None) -> str | None:
    if not url:
        return None
    if "//" not in url:
        url = "https://" + url
    host = (urlsplit(url).hostname or "").lower()
    return host.removeprefix("www.") or None


def same_site(a: str, b: str) -> bool:
    return a == b or a.endswith("." + b) or b.endswith("." + a)


def build_reference(project, website=None) -> Reference:
    """Website values first (independent of Google), project inputs as fallback."""
    ref = Reference(region=project.country)

    def pick(key: str, site_value, input_value):
        if site_value:
            ref.sources[key] = "website"
            return site_value
        if input_value:
            ref.sources[key] = "your input"
            return input_value
        return None

    ref.name = pick("name", website.business_name if website else None, project.input_business_name)
    ref.address = pick("address", website.address if website else None, project.input_address)
    input_phone = normalize_phone(project.input_phone, project.country) if project.input_phone else None
    ref.phone_e164 = pick(
        "phone", website.phone_e164 if website else None, input_phone[0] if input_phone else None
    )
    ref.domain = pick("domain", domain_of(website.url) if website else None, domain_of(project.website_url))
    lists = (
        ((website.nap_sources or {}).get("all") or {}) if website and hasattr(website, "nap_sources") else {}
    )
    ref.other_addresses = [a for a in lists.get("addresses") or [] if a != ref.address]
    ref.other_phones = [p for p in lists.get("phones_e164") or [] if p != ref.phone_e164]
    if website and website.latitude is not None:
        ref.latitude, ref.longitude = website.latitude, website.longitude
        ref.sources["coordinates"] = "website"
    return ref


def _address_score(ref_address: str, cand_address: str) -> tuple[float, str, bool]:
    """(score, detail, is_positive)."""
    r_pc, c_pc = postcode(ref_address), postcode(cand_address)
    street = fuzz.token_set_ratio(street_key(ref_address), street_key(cand_address))
    if r_pc and c_pc:
        if r_pc == c_pc and street >= 80:
            return 1.0, f"Address matches (postcode {c_pc})", True
        if r_pc == c_pc:
            return 0.6, f"Same postcode {c_pc} but street differs ({street:.0f}%)", False
        if street >= 80:
            return 0.4, f"Same street but different postcode ({r_pc} vs {c_pc})", False
        return 0.0, f"Different address (postcode {r_pc} vs {c_pc})", False
    r_num, c_num = _street_number(ref_address), _street_number(cand_address)
    street_name = fuzz.token_set_ratio(_without_number(ref_address), _without_number(cand_address))
    if street_name >= 90 and r_num and c_num and r_num != c_num:
        return 0.2, f"Same street, different number ({r_num} vs {c_num})", False
    if street_name >= 90 and not r_num:
        # "Yonge St" matches every business on Yonge St: weak evidence on its own.
        return 0.5, "Same street name (no street number to compare)", False
    if street >= 90:
        return 0.9, f"Street address matches ({street:.0f}%)", True
    return (street / 100 if street >= 60 else 0.0), f"Street address differs ({street:.0f}% similar)", False


def _without_number(address: str) -> str:
    return re.sub(r"^\s*\d+[A-Za-z]?\b", "", street_key(address)).strip()


def _street_number(address: str) -> str | None:
    m = re.match(r"\s*(\d+[A-Za-z]?)\b", address)
    return m.group(1).upper() if m else None


def score_candidate(ref: Reference, cand) -> Score:
    """cand needs: business_name, address, phone, website_url, latitude, longitude, place_id."""
    signals: dict[str, dict] = {}
    good: list[str] = []
    bad: list[str] = []

    def add(key: str, score: float, detail: str, positive: bool) -> None:
        signals[key] = {"score": round(score, 3), "weight": WEIGHTS[key], "detail": detail}
        (good if positive else bad).append(detail)

    if ref.name and cand.business_name:
        s = fuzz.token_set_ratio(clean_name(ref.name), clean_name(cand.business_name)) / 100
        if s >= 0.85:
            add("name", s, f"Business name matches ({s:.0%})", True)
        else:
            add("name", s, f"Business name differs ({s:.0%} similar)", False)

    if ref.address and cand.address:
        options = [ref.address, *ref.other_addresses]
        s, detail, positive = max((_address_score(a, cand.address) for a in options), key=lambda r: r[0])
        if positive and len(options) > 1:
            detail += f" (one of {len(options)} locations on the website)"
        add("address", s, detail, positive)

    cand_phone = normalize_phone(cand.phone, ref.region) if cand.phone else None
    if ref.phone_e164 and cand_phone:
        if cand_phone[0] == ref.phone_e164 or cand_phone[0] in ref.other_phones:
            add("phone", 1.0, "Phone matches", True)
        else:
            add("phone", 0.0, f"Phone differs ({cand_phone[1]})", False)

    cand_domain = domain_of(cand.website_url)
    if ref.domain and cand_domain:
        if same_site(ref.domain, cand_domain):
            add("domain", 1.0, "Website domain matches", True)
        else:
            add("domain", 0.0, f"Different website ({cand_domain})", False)

    if ref.latitude is not None and cand.latitude is not None:
        meters = haversine_m(ref.latitude, ref.longitude, cand.latitude, cand.longitude)
        s = 1.0 if meters <= 100 else max(0.0, 1 - (meters - 100) / 1900)
        label = f"{meters:.0f} m" if meters < 1000 else f"{meters / 1000:.1f} km"
        add("distance", s, f"Map pin {label} from the website address", s >= 0.8)

    evidence = sum(v["weight"] for v in signals.values())
    confidence = sum(v["score"] * v["weight"] for v in signals.values()) / evidence if evidence else 0.0
    if evidence < MIN_EVIDENCE_WEIGHT:
        confidence = min(confidence, WEAK_EVIDENCE_CAP)
        bad.append("Not enough information to confirm automatically")
    return Score(
        place_id=cand.place_id,
        match_confidence=round(confidence, 3),
        match_reasons=good,
        mismatch_reasons=bad,
        signals=signals,
        evidence_weight=round(evidence, 2),
    )


@dataclass
class Decision:
    status: str  # auto_selected | manual_review_required | not_found
    place_id: str | None
    match_confidence: float | None
    match_reasons: list[str]
    candidates: list[dict]  # candidate fields + score, best first
    reason: str | None = None


def decide(ref: Reference, candidates: list) -> Decision:
    scored = sorted(
        ((score_candidate(ref, c), c) for c in candidates), key=lambda sc: -sc[0].match_confidence
    )
    rows = [{**asdict(c), "service_area": getattr(c, "service_area", None), **asdict(s)} for s, c in scored]
    if not scored:
        return Decision("not_found", None, None, [], [], "Google returned no candidates")
    best, _ = scored[0]
    strong = [s for s, _ in scored if s.match_confidence >= AUTO_SELECT]
    if best.match_confidence >= AUTO_SELECT and len(strong) == 1:
        return Decision("auto_selected", best.place_id, best.match_confidence, best.match_reasons, rows)
    if len(strong) > 1 and strong[0].match_confidence - strong[1].match_confidence < DUPLICATE_MARGIN:
        reason = "Several listings match equally well (branches or duplicate profiles)"
    elif len(strong) > 1:
        return Decision("auto_selected", best.place_id, best.match_confidence, best.match_reasons, rows)
    else:
        reason = f"Best match is {best.match_confidence:.2f}, below {AUTO_SELECT}"
    return Decision("manual_review_required", None, best.match_confidence, best.match_reasons, rows, reason)
