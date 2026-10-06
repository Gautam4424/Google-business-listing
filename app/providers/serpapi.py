"""SerpApi. Docs: https://serpapi.com/search-api"""

import httpx

from app.providers.base import ProviderError

BASE_URL = "https://serpapi.com"
PROVIDER = "serpapi"


class SerpApiClient:
    def __init__(self, api_key: str, timeout: float = 30.0, transport: httpx.BaseTransport | None = None):
        self._client = httpx.Client(base_url=BASE_URL, timeout=timeout, transport=transport)
        self._api_key = api_key

    def account(self) -> dict:
        """Plan and remaining searches. Free: does not use a search."""
        return self._get("/account.json", {})

    def locations(self, query: str, limit: int = 10) -> list[dict]:
        """SerpApi's Google locations list (canonical names for `location=`). Free: no search used."""
        response = self._client.get("/locations.json", params={"q": query, "limit": limit})
        if response.status_code >= 400:
            raise ProviderError(PROVIDER, response.text[:200], response.status_code)
        return response.json()

    def search(self, params: dict) -> dict:
        """One search (uses 1 of the monthly searches)."""
        return self._get("/search.json", params)

    def _get(self, path: str, params: dict) -> dict:
        response = self._client.get(path, params={**params, "api_key": self._api_key})
        data = response.json() if response.content else {}
        if response.status_code >= 400 or (isinstance(data, dict) and data.get("error")):
            message = (
                data.get("error", response.text[:200]) if isinstance(data, dict) else response.text[:200]
            )
            raise ProviderError(PROVIDER, message, response.status_code)
        return data
