import logging
import time
from collections.abc import Callable

import httpx

log = logging.getLogger(__name__)

# Temporary provider problems worth one more try. 4xx (bad key, bad request) are never retried.
RETRY_STATUS = {429, 500, 502, 503, 504}


class ProviderError(Exception):
    def __init__(self, provider: str, message: str, status_code: int | None = None):
        super().__init__(f"{provider}: {message}")
        self.provider = provider
        self.status_code = status_code


def send_with_retry(provider: str, send: Callable[[], httpx.Response]) -> httpx.Response:
    """Send; on a timeout, connection error, 429 or 5xx wait and retry (PROVIDER_RETRIES, default 1).

    Providers do not charge for requests that fail this way, so a retry never costs extra.
    """
    from app.core.config import get_settings

    settings = get_settings()
    retries = max(settings.provider_retries, 0)
    for attempt in range(retries + 1):
        last = attempt == retries
        try:
            response = send()
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            if last:
                raise
            reason = type(exc).__name__
        else:
            if response.status_code not in RETRY_STATUS or last:
                return response
            reason = f"HTTP {response.status_code}"
        log.warning("%s: %s, retrying (%d/%d)", provider, reason, attempt + 1, retries)
        time.sleep(settings.provider_retry_delay_seconds * (attempt + 1))
    raise AssertionError("unreachable")  # pragma: no cover
