"""Google Places API (New). Docs: https://developers.google.com/maps/documentation/places/web-service/op-overview"""

import httpx

from app.providers.base import ProviderError, send_with_retry

BASE_URL = "https://places.googleapis.com/v1"
PROVIDER = "google_places"


class GooglePlacesClient:
    def __init__(self, api_key: str, timeout: float = 20.0, transport: httpx.BaseTransport | None = None):
        self._client = httpx.Client(base_url=BASE_URL, timeout=timeout, transport=transport)
        self._api_key = api_key

    def _headers(self, field_mask: str) -> dict:
        # The field mask decides which billing SKU applies; always request only what is needed.
        return {"X-Goog-Api-Key": self._api_key, "X-Goog-FieldMask": field_mask}

    def search_text(self, text_query: str, field_mask: str, page_size: int = 5, **extra) -> dict:
        body = {"textQuery": text_query, "pageSize": page_size, **extra}
        response = send_with_retry(
            PROVIDER,
            lambda: self._client.post("/places:searchText", json=body, headers=self._headers(field_mask)),
        )
        return self._parse(response)

    def place_details(self, place_id: str, field_mask: str) -> dict:
        response = send_with_retry(
            PROVIDER, lambda: self._client.get(f"/places/{place_id}", headers=self._headers(field_mask))
        )
        return self._parse(response)

    @staticmethod
    def _parse(response: httpx.Response) -> dict:
        data = response.json() if response.content else {}
        if response.status_code >= 400:
            message = data.get("error", {}).get("message", response.text[:200])
            raise ProviderError(PROVIDER, message, response.status_code)
        return data
