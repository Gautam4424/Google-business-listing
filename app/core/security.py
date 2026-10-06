from app.core.config import get_settings


def _secret_values() -> list[str]:
    s = get_settings()
    values = []
    for secret in (s.google_api_key, s.serpapi_key):
        if secret is not None and secret.get_secret_value():
            values.append(secret.get_secret_value())
    return values


def redact(text: str) -> str:
    """Remove API keys from text that may be stored or returned (errors often contain request URLs)."""
    for value in _secret_values():
        text = text.replace(value, "***")
    return text
