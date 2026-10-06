"""Render a JavaScript-only page with headless Chromium (Playwright). Used only as a fallback.

Every request the page makes goes through the same public-address guard as the crawler, and images,
fonts and media are not downloaded.
"""

import logging

log = logging.getLogger(__name__)

BLOCKED_RESOURCES = {"image", "media", "font"}


class BrowserUnavailable(RuntimeError):
    pass


def render(url: str, guard, user_agent: str, timeout_ms: int = 25_000) -> str:
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - depends on the image
        raise BrowserUnavailable("Playwright is not installed") from exc

    def route(r):
        request = r.request
        if request.resource_type in BLOCKED_RESOURCES:
            return r.abort()
        try:
            guard(request.url)
        except ValueError:
            return r.abort()
        return r.continue_()

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page(user_agent=user_agent)
                page.route("**/*", route)
                try:
                    page.goto(url, wait_until="networkidle", timeout=timeout_ms)
                except PlaywrightError:
                    page.wait_for_timeout(1500)  # slow sites: use whatever has rendered so far
                return page.content()
            finally:
                browser.close()
    except PlaywrightError as exc:
        raise BrowserUnavailable(str(exc).splitlines()[0]) from exc
