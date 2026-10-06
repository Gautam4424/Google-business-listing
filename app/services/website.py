"""Read the business's own website: NAP, social profile links and offerings (services/products).

Small and polite: honours robots.txt, uses sitemap.xml to find pages, fetches the homepage, the contact page
and a few service pages, public hosts only. Pages that only render with JavaScript are re-rendered with
headless Chromium when available.
"""

import ipaddress
import json
import logging
import re
import socket
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from app.services.nap import Nap, extract_nap, merge_nap

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; LocalSEOAudit/0.1)"
MAX_PAGES = 6
MAX_SITEMAPS = 3
MAX_BYTES = 2_000_000
TIMEOUT = 12.0

SOCIAL_DOMAINS = {
    "facebook": ("facebook.com", "fb.com"),
    "instagram": ("instagram.com",),
    "linkedin": ("linkedin.com",),
    "x": ("twitter.com", "x.com"),
    "youtube": ("youtube.com",),
    "tiktok": ("tiktok.com",),
    "pinterest": ("pinterest.com", "pinterest.ca", "pinterest.co.uk"),
    "houzz": ("houzz.com", "houzz.ca", "houzz.co.uk"),
    "yelp": ("yelp.com", "yelp.ca", "yelp.co.uk"),
}
# Links that are share buttons, posts or embeds rather than the business's profile.
NOT_A_PROFILE = re.compile(
    r"(sharer|/share|intent/|/status/|home\?status|dialog/|/plugins/|/embed|watch\?v=|/p/|/posts?/|/reel/|/hashtag/)",
    re.IGNORECASE,
)
SERVICE_HUB = re.compile(
    r"/(services?|what-we-do|our-work|solutions|offerings|products?|menu|treatments|practice-areas|specialties)(/|$)",
    re.IGNORECASE,
)
GENERIC_LABELS = {
    "services", "our services", "service", "all services", "learn more", "read more", "view all", "view more",
    "home", "contact", "contact us", "about", "about us", "get a quote", "free quote", "request a quote",
    "book now", "call now", "more", "menu", "products", "our work", "portfolio", "gallery", "blog", "faq",
    "why choose us", "testimonials", "reviews", "careers", "privacy policy", "terms", "click here",
}  # fmt: skip


@dataclass
class WebsiteResult:
    url: str
    pages_fetched: list[str] = field(default_factory=list)
    social_profiles: dict[str, str] = field(default_factory=dict)
    # (name, source, source_url, confidence)
    offerings: list[tuple[str, str, str, float]] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    nap: Nap = field(default_factory=Nap)
    rendered_with_browser: bool = False
    sitemap_urls: int = 0
    notes: list[str] = field(default_factory=list)


class UnsafeUrl(ValueError):
    pass


@lru_cache(maxsize=512)
def _host_problem(hostname: str) -> str | None:
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return f"Cannot resolve {hostname}"
    for info in infos:
        if not ipaddress.ip_address(info[4][0]).is_global:
            return f"{hostname} resolves to a non-public address"
    return None


def check_public_url(url: str) -> None:
    """Only fetch public http(s) hosts, never internal addresses (SSRF guard)."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise UnsafeUrl(f"Not an http(s) URL: {url}")
    if problem := _host_problem(parts.hostname.lower()):
        raise UnsafeUrl(problem)


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


def _matches(host: str, domains: tuple[str, ...]) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def social_platform(url: str) -> str | None:
    host = _host(url)
    if not host or NOT_A_PROFILE.search(url):
        return None
    for platform, domains in SOCIAL_DOMAINS.items():
        if _matches(host, domains):
            path = urlsplit(url).path.strip("/")
            if not path:
                return None  # bare domain, not a profile
            if platform == "linkedin" and not re.match(r"(company|in|school)/", path):
                return None
            return platform
    return None


def normalize_profile_url(url: str) -> str:
    p = urlsplit(url)
    # facebook.com/profile.php?id=123: the id is the profile, so keep just that parameter.
    query = ""
    if p.path.rstrip("/").endswith("profile.php"):
        ids = [kv for kv in p.query.split("&") if kv.startswith("id=")]
        query = ids[0] if ids else ""
    return urlunsplit((p.scheme or "https", p.netloc.lower(), p.path.rstrip("/"), query, ""))


CTA_PREFIX = re.compile(
    r"^(explore|discover|view|see|learn more about|learn about|more about|read about)\s+", re.IGNORECASE
)


def clean_label(text: str) -> str | None:
    text = re.sub(r"[​-‍﻿]", "", text or "")  # zero-width characters
    text = re.split(r"\s[|•]\s", text)[0]  # "Drain Locator | Water Lines" -> "Drain Locator"
    text = re.sub(r"\s+", " ", text).strip(" -–|•›»")
    text = CTA_PREFIX.sub("", text)  # "Explore Home Additions" -> "Home Additions"
    if not (3 <= len(text) <= 60) or text.lower() in GENERIC_LABELS:
        return None
    if re.search(r"[@{}<>]|https?://|\d{3}[- ]\d{3}", text):
        return None
    return text


def _walk_jsonld(node, out_services: list[str], out_same_as: list[str]) -> None:
    if isinstance(node, list):
        for n in node:
            _walk_jsonld(n, out_services, out_same_as)
        return
    if not isinstance(node, dict):
        return
    types = node.get("@type")
    types = [types] if isinstance(types, str) else (types or [])
    if any(t in ("Service", "Product", "MenuItem") for t in types) and isinstance(node.get("name"), str):
        out_services.append(node["name"])
    same_as = node.get("sameAs")
    if isinstance(same_as, str):
        out_same_as.append(same_as)
    elif isinstance(same_as, list):
        out_same_as.extend(s for s in same_as if isinstance(s, str))
    for key in ("knowsAbout", "serviceType"):
        val = node.get(key)
        for v in [val] if isinstance(val, str) else (val if isinstance(val, list) else []):
            if isinstance(v, str):
                out_services.append(v)
    for key, val in node.items():
        if key in (
            "@graph",
            "itemListElement",
            "hasOfferCatalog",
            "makesOffer",
            "itemOffered",
            "offers",
            "mainEntity",
        ):
            _walk_jsonld(val, out_services, out_same_as)


def extract_page(html: str, page_url: str, site_host: str) -> tuple[dict, list, list[str]]:
    """Returns (social_profiles, offerings, service_hub_urls) for one page."""
    soup = BeautifulSoup(html, "html.parser")
    socials: dict[str, str] = {}
    offerings: list[tuple[str, str, str, float]] = []
    hubs: list[str] = []

    schema_services: list[str] = []
    same_as: list[str] = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            _walk_jsonld(json.loads(tag.string or ""), schema_services, same_as)
        except (json.JSONDecodeError, TypeError):
            continue
    for name in schema_services:
        if label := clean_label(name):
            offerings.append((label, "website_schema", page_url, 0.9))

    for href in same_as + [a.get("href", "") for a in soup.find_all("a", href=True)]:
        absolute = urljoin(page_url, href)
        platform = social_platform(absolute)
        if platform and platform not in socials:
            socials[platform] = normalize_profile_url(absolute)

    for a in soup.find_all("a", href=True):
        absolute = urljoin(page_url, a["href"]).split("#")[0]
        if _host(absolute) != site_host:
            continue
        path = urlsplit(absolute).path
        match = SERVICE_HUB.search(path)
        if not match:
            continue
        rest = path[match.end() :].strip("/")
        if rest:  # /services/kitchen-renovation -> a specific offering
            if label := clean_label(a.get_text(" ", strip=True)):
                offerings.append((label, "website_service_page", absolute, 0.8))
        elif absolute.rstrip("/") != page_url.rstrip("/"):
            hubs.append(absolute)
    return socials, offerings, hubs


def _headings(html: str, page_url: str) -> list[tuple[str, str, str, float]]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for h in soup.find_all(["h2", "h3"])[:40]:
        if label := clean_label(h.get_text(" ", strip=True)):
            out.append((label, "website_heading", page_url, 0.5))
    return out[:15]


CONTACT = re.compile(r"contact|get-in-touch|find-us|location", re.IGNORECASE)
SPA_MARKERS = re.compile(r'id="(root|app|__next|__nuxt)"|enable javascript|ng-version|data-reactroot', re.I)


def needs_browser(html: str) -> bool:
    """Page shell with almost no text, typical of sites rendered only by JavaScript."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    text = soup.get_text(" ", strip=True)
    return len(text) < 200 or (len(text) < 600 and bool(SPA_MARKERS.search(html)))


def _robots(client: httpx.Client, base: str) -> RobotFileParser:
    rp = RobotFileParser()
    try:
        r = client.get(urljoin(base, "/robots.txt"))
        rp.parse(r.text.splitlines() if r.status_code == 200 else [])
    except httpx.HTTPError:
        rp.parse([])
    return rp


def _fetch(client: httpx.Client, url: str, guard) -> str | None:
    guard(url)
    r = client.get(url)
    if r.status_code >= 400 or "html" not in r.headers.get("content-type", "html"):
        return None
    guard(str(r.url))  # redirects must stay public too
    return r.text[:MAX_BYTES]


def _sitemap_urls(client: httpx.Client, base: str, robots: RobotFileParser, guard) -> list[str]:
    """Page URLs from sitemap.xml (follows a sitemap index, at most MAX_SITEMAPS files)."""
    queue = list(robots.site_maps() or []) or [
        urljoin(base, "/sitemap.xml"),
        urljoin(base, "/sitemap_index.xml"),
    ]
    pages: list[str] = []
    fetched = 0
    while queue and fetched < MAX_SITEMAPS:
        url = queue.pop(0)
        try:
            guard(url)
            r = client.get(url)
        except (httpx.HTTPError, UnsafeUrl):
            continue
        if r.status_code >= 400 or "<loc>" not in r.text:
            continue
        fetched += 1
        locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", r.text)
        if "<sitemapindex" in r.text:
            # Page/service sitemaps first; skip image, tag and author sitemaps.
            children = sorted(locs, key=lambda u: (not re.search(r"page|service", u, re.I), u))
            queue = [u for u in children if not re.search(r"image|tag|author", u, re.I)] + queue
        else:
            pages.extend(locs)
    return pages[:2000]


def _slug_label(url: str) -> str | None:
    slug = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
    slug = re.sub(r"\.(html?|php|aspx?)$", "", slug)
    return clean_label(slug.replace("-", " ").replace("_", " ").capitalize()) if slug else None


def _is_hub_root(url: str) -> bool:
    path = urlsplit(url).path
    match = SERVICE_HUB.search(path)
    return bool(match) and not path[match.end() :].strip("/")


Renderer = Callable[[str], str]


def crawl(
    website_url: str,
    transport: httpx.BaseTransport | None = None,
    guard=check_public_url,
    renderer: Renderer | None = None,
    region: str | None = None,
) -> WebsiteResult:
    """renderer(url) -> html is used for JavaScript-only sites (headless browser); None disables it."""
    result = WebsiteResult(url=website_url)
    site_host = _host(website_url)
    try:
        guard(website_url)  # before anything is fetched, robots.txt included
    except UnsafeUrl as exc:
        result.errors.append(str(exc))
        return result

    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"}
    nap_pages: list[dict] = []
    with httpx.Client(timeout=TIMEOUT, follow_redirects=True, headers=headers, transport=transport) as client:
        robots = _robots(client, website_url)
        sitemap = [u for u in _sitemap_urls(client, website_url, robots, guard) if _host(u) == site_host]
        result.sitemap_urls = len(sitemap)
        sitemap_contact = [u for u in sitemap if CONTACT.search(urlsplit(u).path)][:1]
        sitemap_services = [u for u in sitemap if SERVICE_HUB.search(urlsplit(u).path)]

        queue, seen, hub_pages = [website_url], set(), []
        use_browser = False
        while queue and len(result.pages_fetched) < MAX_PAGES:
            url = queue.pop(0)
            if url.rstrip("/") in seen:
                continue
            seen.add(url.rstrip("/"))
            if not robots.can_fetch(USER_AGENT, url):
                result.blocked.append(url)
                continue
            try:
                html = _fetch(client, url, guard)
            except (httpx.HTTPError, UnsafeUrl) as exc:
                result.errors.append(f"{url}: {type(exc).__name__}: {exc}")
                continue
            if html is None:
                continue

            is_home = url == website_url
            if is_home and needs_browser(html):
                if renderer is None:
                    result.notes.append("Site looks JavaScript-rendered; browser fallback is not available")
                else:
                    use_browser = True
            if use_browser:
                try:
                    html = renderer(url)
                    result.rendered_with_browser = True
                except Exception as exc:  # keep the plain HTML
                    result.notes.append(f"Browser render failed for {url}: {type(exc).__name__}: {exc}")

            result.pages_fetched.append(url)
            socials, offerings, hubs = extract_page(html, url, site_host)
            nap_pages.append(extract_nap(html, url))
            for platform, link in socials.items():
                result.social_profiles.setdefault(platform, link)
            result.offerings.extend(offerings)
            if not is_home:
                hub_pages.append((url, html))

            if is_home:
                # Next: the contact page (for NAP), then service hubs from the menu or the sitemap.
                links = [
                    urljoin(url, a["href"]).split("#")[0]
                    for a in BeautifulSoup(html, "html.parser").find_all("a", href=True)
                ]
                contact = (
                    sitemap_contact
                    or [u for u in links if _host(u) == site_host and CONTACT.search(urlsplit(u).path)][:1]
                )
                queue.extend(contact)
                if not hubs:
                    queue.extend([u for u in sitemap_services if _is_hub_root(u)][:2])
            queue.extend(h for h in hubs if h.rstrip("/") not in seen)

        # Service pages listed in the sitemap but not linked in the (possibly JavaScript) menu.
        known = {o[2].rstrip("/") for o in result.offerings}
        for u in sitemap_services:
            if not _is_hub_root(u) and u.rstrip("/") not in known and (label := _slug_label(u)):
                result.offerings.append((label, "website_sitemap", u, 0.6))

        # No specific service links anywhere: fall back to headings on the services page(s).
        specific = {"website_service_page", "website_schema", "website_sitemap"}
        if not any(src in specific for _, src, _, _ in result.offerings):
            for url, html in hub_pages:
                if SERVICE_HUB.search(urlsplit(url).path):
                    result.offerings.extend(_headings(html, url))

    result.nap = merge_nap(nap_pages, region)
    seen_names, unique = set(), []
    for name, source, url, conf in sorted(result.offerings, key=lambda o: -o[3]):
        key = name.lower()
        if key not in seen_names:
            seen_names.add(key)
            unique.append((name, source, url, conf))
    result.offerings = unique[:40]
    return result
