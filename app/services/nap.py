"""NAP (name, address, phone) from a web page, and NAP consistency against the Google profile."""

import json
import math
import re
from dataclasses import dataclass, field

import phonenumbers
from bs4 import BeautifulSoup
from rapidfuzz import fuzz

# A field found on a page: (value, source, confidence, page_url)
Found = tuple[str, str, float, str]

POSTCODE = {
    "CA": r"\b[A-CEGHJ-NPR-TVXY]\d[A-CEGHJ-NPR-TV-Z] ?\d[A-CEGHJ-NPR-TV-Z]\d\b",
    "US": r"\b[A-Z]{2},? \d{5}(?:-\d{4})?\b",
    "GB": r"\b[A-Z]{1,2}\d[A-Z\d]? ?\d[A-Z]{2}\b",
    "AU": r"\b(?:NSW|VIC|QLD|SA|WA|TAS|NT|ACT),? \d{4}\b",
}  # bare-number postcodes (e.g. India's 6 digits) are left out: they match phone fragments
GENERIC_POSTCODE = r"|".join(f"(?:{p})" for p in POSTCODE.values())
NON_BUSINESS_TYPES = {
    "WebSite", "WebPage", "BreadcrumbList", "ImageObject", "Person", "Article", "BlogPosting",
    "SiteNavigationElement", "ListItem", "SearchAction", "VideoObject", "FAQPage", "Question", "Answer",
    "Review", "AggregateRating", "Offer",
}  # fmt: skip
LEGAL_SUFFIX = re.compile(r"\b(inc|ltd|llc|limited|corp|corporation|co|company|plc|pty|gmbh)\b\.?", re.I)


@dataclass
class Nap:
    name: str | None = None
    phone: str | None = None
    phone_e164: str | None = None
    address: str | None = None
    address_parts: dict | None = None
    latitude: float | None = None
    longitude: float | None = None
    sources: dict = field(default_factory=dict)  # field -> {"source", "url", "confidence"}
    issues: list[str] = field(default_factory=list)  # problems in the site's own data (an SEO finding)
    # Every distinct address / phone found on the site (chains list many locations).
    all_addresses: list[str] = field(default_factory=list)
    all_phones_e164: list[str] = field(default_factory=list)

    @property
    def multi_location(self) -> bool:
        # Different postcodes = different places (two phones alone can be office + mobile).
        return len({pc for a in self.all_addresses if (pc := postcode(a))}) > 1


def normalize_phone(value: str, region: str | None) -> tuple[str, str] | None:
    """Return (E.164, international format) or None if it is not a valid number."""
    try:
        number = phonenumbers.parse(value, (region or "US").upper())
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(number):
        return None
    return (
        phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164),
        phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.INTERNATIONAL),
    )


def _text(value) -> str | None:
    if isinstance(value, str):
        return re.sub(r"\s+", " ", value).strip() or None
    if isinstance(value, dict):
        return _text(value.get("name") or value.get("@id"))
    if isinstance(value, list) and value:
        return _text(value[0])
    return None


def _join_address(parts: dict) -> str | None:
    order = ("streetAddress", "addressLocality", "addressRegion", "postalCode", "addressCountry")
    region_post = " ".join(p for p in (parts.get("addressRegion"), parts.get("postalCode")) if p)
    pieces = [
        parts.get("streetAddress"),
        parts.get("addressLocality"),
        region_post or None,
        parts.get("addressCountry"),
    ]
    pieces = [p for p in pieces if p]
    return ", ".join(pieces) if pieces and any(parts.get(k) for k in order[:2]) else None


def _business_nodes(node, out: list[dict]) -> None:
    if isinstance(node, list):
        for n in node:
            _business_nodes(n, out)
        return
    if not isinstance(node, dict):
        return
    types = node.get("@type")
    types = {types} if isinstance(types, str) else set(types or [])
    if (node.get("address") or node.get("telephone")) and not types & NON_BUSINESS_TYPES:
        out.append(node)
    for key in ("@graph", "mainEntity", "publisher", "provider", "author", "about", "brand"):
        if key in node:
            _business_nodes(node[key], out)


def _address_from_schema(addr) -> tuple[str | None, dict | None]:
    if isinstance(addr, list) and addr:
        addr = addr[0]
    if isinstance(addr, str):
        return _text(addr), None
    if isinstance(addr, dict):
        parts = {
            k: _text(addr.get(k))
            for k in ("streetAddress", "addressLocality", "addressRegion", "postalCode", "addressCountry")
            if _text(addr.get(k))
        }
        return _join_address(parts), parts or None
    return None, None


def extract_nap(html: str, page_url: str) -> dict[str, list[Found]]:
    """All NAP candidates on one page, by field. Merged across pages by `merge_nap`."""
    soup = BeautifulSoup(html, "html.parser")
    found: dict[str, list[Found]] = {"name": [], "phone": [], "address": [], "geo": []}

    # 1) JSON-LD LocalBusiness / Organization
    nodes: list[dict] = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            _business_nodes(json.loads(tag.string or ""), nodes)
        except (json.JSONDecodeError, TypeError):
            continue
    for node in nodes:
        if name := _text(node.get("name")):
            found["name"].append((name, "schema", 0.95, page_url))
        if phone := _text(node.get("telephone")):
            found["phone"].append((phone, "schema", 0.95, page_url))
        address, parts = _address_from_schema(node.get("address"))
        if address:
            found["address"].append((address, "schema", 0.95, page_url))
            if parts:
                found.setdefault("address_parts", []).append((json.dumps(parts), "schema", 0.95, page_url))
        geo = node.get("geo") or {}
        if isinstance(geo, dict) and geo.get("latitude") and geo.get("longitude"):
            found["geo"].append((f"{geo['latitude']},{geo['longitude']}", "schema", 0.95, page_url))

    # 2) Microdata (itemprop)
    def prop(name: str) -> str | None:
        el = soup.find(attrs={"itemprop": name})
        if not el:
            return None
        return _text(el.get("content") or el.get_text(" ", strip=True))

    if phone := prop("telephone"):
        found["phone"].append((phone, "microdata", 0.9, page_url))
    md_parts = {
        k: v for k in ("streetAddress", "addressLocality", "addressRegion", "postalCode") if (v := prop(k))
    }
    if address := _join_address(md_parts):
        found["address"].append((address, "microdata", 0.9, page_url))

    # 3) tel: links
    for a in soup.select('a[href^="tel:"]'):
        found["phone"].append((a["href"][4:].strip(), "tel_link", 0.85, page_url))

    # 4) <address> tag, then text lines with a postcode
    for tag in soup.find_all("address"):
        text = re.sub(r"\s+", " ", tag.get_text(", ", strip=True)).strip(", ")
        if re.search(GENERIC_POSTCODE, text) and 10 <= len(text) <= 160:
            found["address"].append((text, "address_tag", 0.8, page_url))
    lines = [re.sub(r"\s+", " ", t).strip() for t in soup.get_text("\n").split("\n")]
    lines = [t for t in lines if t]
    for i, line in enumerate(lines):
        if not re.search(GENERIC_POSTCODE, line) or len(line) > 140:
            continue
        candidate = line
        if not re.match(r"^\d+\w*\s+\w", line) and i > 0 and re.match(r"^\d+\w*\s+\w", lines[i - 1]):
            candidate = f"{lines[i - 1]}, {line}"
        if re.search(r"\d+\w*\s+[A-Za-z]", candidate):
            found["address"].append((candidate.strip(" ,"), "page_text", 0.6, page_url))

    # 5) Site name
    og = soup.find("meta", attrs={"property": "og:site_name"})
    if og and _text(og.get("content")):
        found["name"].append((_text(og["content"]), "og_site_name", 0.7, page_url))
    if soup.title and soup.title.string:
        title = re.split(r"\s[|–\-:•]\s", soup.title.string.strip())[0]
        if 2 <= len(title) <= 80:
            found["name"].append((title, "page_title", 0.4, page_url))
    return found


def merge_nap(pages: list[dict[str, list[Found]]], region: str | None) -> Nap:
    nap = Nap()
    pooled: dict[str, list[Found]] = {}
    for page in pages:
        for key, items in page.items():
            pooled.setdefault(key, []).extend(items)

    def best(key: str) -> Found | None:
        items = sorted(pooled.get(key, []), key=lambda f: -f[2])
        return items[0] if items else None

    def note(key: str, f: Found) -> None:
        nap.sources[key] = {"source": f[1], "url": f[3], "confidence": f[2]}

    if f := best("name"):
        nap.name = f[0]
        note("name", f)
    for f in sorted(pooled.get("phone", []), key=lambda f: -f[2]):
        if normalized := normalize_phone(f[0], region):
            nap.phone_e164, nap.phone = normalized
            note("phone", f)
            break
    if f := best("address"):
        nap.address = f[0]
        note("address", f)
        parts = [p for p in pooled.get("address_parts", []) if p[1] == f[1] and p[3] == f[3]]
        nap.address_parts = json.loads(parts[0][0]) if parts else None
    for f in sorted(pooled.get("geo", []), key=lambda f: -f[2]):
        try:
            lat, lng = (float(x) for x in f[0].split(","))
        except ValueError:
            continue
        if problem := coordinate_problem(lat, lng):
            nap.issues.append(f"Website structured data has invalid coordinates ({lat}, {lng}): {problem}")
            continue
        nap.latitude, nap.longitude = lat, lng
        note("coordinates", f)
        break

    seen_addr: set[str] = set()
    for f in sorted(pooled.get("address", []), key=lambda f: -f[2]):
        key = postcode(f[0]) or street_key(f[0])
        if key not in seen_addr and len(nap.all_addresses) < 50:
            seen_addr.add(key)
            nap.all_addresses.append(f[0])
    for f in sorted(pooled.get("phone", []), key=lambda f: -f[2]):
        normalized = normalize_phone(f[0], region)
        if normalized and normalized[0] not in nap.all_phones_e164 and len(nap.all_phones_e164) < 50:
            nap.all_phones_e164.append(normalized[0])
    return nap


def coordinate_problem(lat: float, lng: float) -> str | None:
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return "out of range"
    if abs(lat) < 0.01 and abs(lng) < 0.01:
        return "placeholder 0,0"
    if lat == lng:
        return "latitude and longitude are the same number"
    return None


# ---------- consistency vs Google ----------


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def clean_name(name: str) -> str:
    return re.sub(r"\s+", " ", LEGAL_SUFFIX.sub("", name.lower())).strip(" ,.-")


STREET_ABBREVIATIONS = {
    "street": "st", "avenue": "ave", "road": "rd", "boulevard": "blvd", "drive": "dr", "lane": "ln",
    "court": "ct", "place": "pl", "suite": "ste", "unit": "unit", "highway": "hwy", "parkway": "pkwy",
    "north": "n", "south": "s", "east": "e", "west": "w", "#": "ste",
}  # fmt: skip


def street_key(address: str) -> str:
    """First address segment, lower-case, punctuation removed, common words abbreviated."""
    words = re.sub(r"[.,]", " ", address.split(",")[0].lower()).split()
    return " ".join(STREET_ABBREVIATIONS.get(w, w) for w in words)


def postcode(text: str | None) -> str | None:
    m = re.search(GENERIC_POSTCODE, (text or "").upper())
    return re.sub(r"[\s,]", "", m.group(0)) if m else None


def compare_nap(
    website: Nap | None,
    gbp_name: str | None,
    gbp_phone: str | None,
    gbp_address: str | None,
    region: str | None,
) -> dict:
    """Field-by-field: match | mismatch | missing (one side has no value)."""
    out: dict[str, dict] = {}

    def row(key: str, g, w, status: str, detail: str | None = None) -> None:
        out[key] = {"google": g, "website": w, "status": status, "detail": detail}

    w = website or Nap()
    if gbp_name and w.name:
        score = fuzz.token_set_ratio(clean_name(gbp_name), clean_name(w.name))
        row("name", gbp_name, w.name, "match" if score >= 85 else "mismatch", f"similarity {score:.0f}%")
    else:
        row("name", gbp_name, w.name, "missing")

    g_phone = normalize_phone(gbp_phone, region) if gbp_phone else None
    phones = [p for p in [w.phone_e164, *w.all_phones_e164] if p]
    if g_phone and phones:
        if g_phone[0] == w.phone_e164:
            row("phone", gbp_phone, w.phone, "match")
        elif g_phone[0] in phones:
            n = len(set(phones))
            row("phone", gbp_phone, gbp_phone, "match", f"one of {n} phone numbers listed on the website")
        else:
            row("phone", gbp_phone, w.phone, "mismatch")
    else:
        row("phone", gbp_phone, w.phone, "missing")

    addresses = list(dict.fromkeys(a for a in [w.address, *w.all_addresses] if a))
    if gbp_address and addresses:
        results = [(a, *address_agreement(gbp_address, a)) for a in addresses]
        best = next((r for r in results if r[1]), results[0])
        detail = best[2]
        if best[0] != w.address and best[1]:
            detail += f" (one of {len(addresses)} locations listed on the website)"
        row("address", gbp_address, best[0], "match" if best[1] else "mismatch", detail)
    else:
        row("address", gbp_address, w.address, "missing")
    return out


def address_agreement(a: str, b: str) -> tuple[bool, str]:
    a_pc, b_pc = postcode(a), postcode(b)
    # token_set: the suite number may be split into its own address segment
    street_score = fuzz.token_set_ratio(street_key(a), street_key(b))
    # Both must agree when both postcodes are known; otherwise judge by the street alone.
    same = (a_pc == b_pc and street_score >= 80) if (a_pc and b_pc) else street_score >= 90
    return same, f"postcode {a_pc or '?'} vs {b_pc or '?'}, street similarity {street_score:.0f}%"
