"""Settings that can be changed on the Settings page (stored in `app_settings`, override .env).

Infrastructure (database, Redis, POSTGRES_PASSWORD, APP_BIND/APP_PORT) stays in .env only: changing it
needs the containers recreated. A few values are read once at start-up (`restart: True`); they are saved
here but apply after `docker compose restart api worker`.
"""

from dataclasses import dataclass, field

from pydantic import SecretStr, TypeAdapter
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, env_value, get_settings, refresh_overrides
from app.models import AppSetting, SettingChange


class SettingError(ValueError):
    pass


class CostConfirmationNeeded(SettingError):
    pass


@dataclass(frozen=True)
class Editable:
    key: str  # .env name
    group: str
    label: str
    help: str
    min: int | None = None
    max: int | None = None
    choices: tuple = ()
    secret: bool = False
    restart: bool = False
    free_max: int | None = None  # above this the provider may start charging
    extra: dict = field(default_factory=dict)

    @property
    def attr(self) -> str:
        return self.key.lower()


E = Editable
EDITABLE: list[Editable] = [
    E("GOOGLE_API_KEY", "API keys", "Google Places API key", "Places API (New). Never shown again after saving.",
      secret=True),
    E("SERPAPI_KEY", "API keys", "SerpApi key", "Used for ranking checks (about 64 characters).", secret=True),
    E("QUOTA_PLACES_DETAILS_DAILY", "Free-tier limits", "Google profile lookups per day",
      "Audits use 1, competitors up to 1 each. 0 = no daily cap.", 0, 1000),
    E("QUOTA_PLACES_DETAILS_MONTHLY", "Free-tier limits", "Google profile lookups per month",
      "Google gives about 1,000 free per month.", 0, 100000, free_max=1000),
    E("QUOTA_PLACES_TEXTSEARCH_ENTERPRISE_DAILY", "Free-tier limits", "Quick fill / find business per day",
      "0 = no daily cap.", 0, 1000),
    E("QUOTA_PLACES_TEXTSEARCH_ENTERPRISE_MONTHLY", "Free-tier limits", "Quick fill / find business per month",
      "Google gives about 1,000 free per month.", 0, 100000, free_max=1000),
    E("QUOTA_PLACES_TEXTSEARCH_DAILY", "Free-tier limits", "Basic business search per day", "0 = no daily cap.",
      0, 5000),
    E("QUOTA_PLACES_TEXTSEARCH_MONTHLY", "Free-tier limits", "Basic business search per month",
      "Google gives about 5,000 free per month.", 0, 100000, free_max=5000),
    E("QUOTA_GEOCODING_DAILY", "Free-tier limits", "Google address lookups per day", "0 = no daily cap.", 0, 10000),
    E("QUOTA_GEOCODING_MONTHLY", "Free-tier limits", "Google address lookups per month",
      "Google gives about 10,000 free per month.", 0, 100000, free_max=10000),
    E("QUOTA_SERPAPI_DAILY", "Free-tier limits", "SerpApi searches per day",
      "A full 10-keyword ranking check uses 20. 0 = no daily cap.", 0, 5000),
    E("QUOTA_SERPAPI_MONTHLY", "Free-tier limits", "SerpApi searches per month",
      "The free plan has 250 per month. Raise only with a paid plan.", 0, 100000, free_max=250),
    E("RANKING_MODE", "Rankings", "Ranking mode",
      "full = Local Pack + Maps (2 searches per keyword); maps_only = 1 per keyword, Local Pack estimated.",
      choices=("full", "maps_only")),
    E("KEYWORD_CAP", "Rankings", "Keywords that can be On per project", "Each costs SerpApi searches per check.",
      1, 50),
    E("RANKING_CACHE_HOURS", "Rankings", "Free re-check window (hours)",
      "The same search within this time reuses the saved result.", 0, 168),
    E("MAPS_ZOOM", "Rankings", "Maps search area (zoom)", "Higher = smaller area around the keyword.", 10, 18),
    E("SERPAPI_REVIEWS_ENABLED", "Reviews", "Top 10 reviews on every audit",
      "On = 2 SerpApi credits per audit. Off = Google's 5 reviews (free).", choices=(True, False)),
    E("COMPETITOR_MAX", "Competitors", "Competitors analysed per project", "", 1, 20),
    E("COMPETITOR_DETAILS", "Competitors", "Look up competitors' review topics",
      "1 Google profile lookup per competitor (cached).", choices=(True, False)),
    E("COMPETITOR_DETAILS_CACHE_DAYS", "Competitors", "Keep competitor lookups for (days)", "", 1, 30),
    E("COMPETITOR_DETAILS_RESERVE", "Competitors", "Lookups kept for client audits",
      "Competitor lookups stop when only this many are left.", 0, 500),
    E("BROWSER_FALLBACK", "Website reading", "Use a browser for JavaScript-only websites", "",
      choices=(True, False)),
    E("NOMINATIM_USER_AGENT", "Website reading", "OpenStreetMap contact",
      "Your email, e.g. local-seo-audit/0.1 (you@example.com), for free address lookups."),
    E("PROVIDER_RETRIES", "Reliability", "Retries for temporary Google/SerpApi errors", "", 0, 3),
    E("JOB_STALE_MINUTES", "Reliability", "Release a stuck job after (minutes)", "", 15, 1440),
    E("GOOGLE_DATA_TTL_DAYS", "Clean-up", "Keep Google content for (days)",
      "Google's terms allow at most 30 days.", 1, 30),
    E("CLEANUP_HOUR_UTC", "Clean-up", "Nightly clean-up hour (UTC)", "", 0, 23, restart=True),
    E("DEBUG", "App", "Detailed logs (DEBUG)", "For troubleshooting only. API keys are never logged.",
      choices=(True, False), restart=True),
]  # fmt: skip
BY_KEY = {e.key: e for e in EDITABLE}


def mask(value) -> str | None:
    raw = value.get_secret_value() if isinstance(value, SecretStr) else (value or "")
    if not raw:
        return None
    return f"{raw[:4]}…{raw[-2:]}" if len(raw) > 10 else "set"


def _display(e: Editable, value):
    if e.secret:
        return mask(value)
    return value


def _validate(e: Editable, raw) -> str:
    """Check and normalise a submitted value; returns it as the text stored in the table."""
    annotation = Settings.model_fields[e.attr].annotation
    if isinstance(raw, str):
        raw = raw.strip()
    if e.secret:
        if not raw or len(str(raw)) < 10:
            raise SettingError(f"{e.label}: paste the full key")
        return str(raw)
    try:
        value = TypeAdapter(annotation).validate_python(raw)
    except Exception as exc:
        raise SettingError(f"{e.label}: not a valid value ({raw!r})") from exc
    if e.choices and value not in e.choices:
        raise SettingError(f"{e.label}: choose one of {', '.join(map(str, e.choices))}")
    is_number = isinstance(value, int) and not isinstance(value, bool)
    if is_number and (e.min is not None and value < e.min or e.max is not None and value > e.max):
        raise SettingError(f"{e.label}: must be between {e.min} and {e.max}")
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def update(db: Session, values: dict, accept_charges: bool = False) -> list[str]:
    """Save several settings at once (all or nothing). Returns the keys that changed."""
    unknown = [k for k in values if k not in BY_KEY]
    if unknown:
        raise SettingError(f"Not editable here: {', '.join(unknown)} (change these in .env)")
    checked = {k: _validate(BY_KEY[k], v) for k, v in values.items()}
    over = [
        f"{BY_KEY[k].label} = {v} (free tier ≈ {BY_KEY[k].free_max:,})"
        for k, v in checked.items()
        if BY_KEY[k].free_max is not None and int(v) > BY_KEY[k].free_max
    ]
    if over and not accept_charges:
        raise CostConfirmationNeeded(
            "Above the free tier, the provider may charge you: "
            + "; ".join(over)
            + ". Tick 'I accept possible charges' to save anyway."
        )
    current = {r.key: r for r in db.scalars(select(AppSetting).where(AppSetting.key.in_(list(checked))))}
    s = get_settings()
    changed = []
    for key, text in checked.items():
        e = BY_KEY[key]
        before = getattr(s, e.attr)
        if _as_text(before) == text:
            continue
        row = current.get(key)
        if row is None:
            db.add(AppSetting(key=key, value=text))
        else:
            row.value = text
        db.add(SettingChange(key=key, old_value=_shown(e, before), new_value=_shown(e, text)))
        changed.append(key)
    db.commit()
    refresh_overrides()
    return changed


def _as_text(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, SecretStr):
        return value.get_secret_value()
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _shown(e: Editable, value) -> str | None:
    """How a value is written in the change history (keys masked)."""
    return mask(value) if e.secret and value is not None else _as_text(value)


def reset(db: Session, key: str) -> bool:
    """Back to the .env value. Returns False when it was not overridden."""
    if key not in BY_KEY:
        raise SettingError(f"Not editable here: {key}")
    row = db.get(AppSetting, key)
    if row is None:
        return False
    e = BY_KEY[key]
    db.add(SettingChange(key=key, old_value=_shown(e, row.value), new_value=None))
    db.delete(row)
    db.commit()
    refresh_overrides()
    return True


def listing(db: Session) -> dict:
    overridden = {r.key: r for r in db.scalars(select(AppSetting))}
    s = get_settings()
    groups: dict[str, list] = {}
    for e in EDITABLE:
        row = overridden.get(e.key)
        groups.setdefault(e.group, []).append({
            "key": e.key, "label": e.label, "help": e.help, "type": _type(e),
            "value": _display(e, getattr(s, e.attr)),
            "env_value": _display(e, env_value(e.attr)),
            "source": "app" if row else ".env",
            "updated_at": row.updated_at.isoformat() if row else None,
            "min": e.min, "max": e.max, "choices": list(e.choices) or None,
            "secret": e.secret, "restart": e.restart, "free_max": e.free_max,
        })  # fmt: skip
    history = db.scalars(select(SettingChange).order_by(SettingChange.changed_at.desc()).limit(15)).all()
    return {
        "groups": [{"name": name, "settings": items} for name, items in groups.items()],
        "history": [
            {"key": h.key, "old": h.old_value, "new": h.new_value, "at": h.changed_at.isoformat()}
            for h in history
        ],
    }


def _type(e: Editable) -> str:
    if e.secret:
        return "secret"
    if e.choices and isinstance(e.choices[0], bool):
        return "bool"
    if e.choices:
        return "choice"
    annotation = Settings.model_fields[e.attr].annotation
    return "int" if annotation is int else "text"
