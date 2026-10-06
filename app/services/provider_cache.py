"""Store every raw provider response (provenance) and reuse recent ones (saves free-tier quota)."""

import hashlib
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import DataSource


def request_hash(provider: str, endpoint: str, params: dict) -> str:
    payload = json.dumps([provider, endpoint, params], sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def get_cached(
    db: Session, provider: str, endpoint: str, params: dict, max_age: timedelta
) -> DataSource | None:
    now = datetime.now(UTC)
    return db.scalar(
        select(DataSource)
        .where(
            DataSource.request_hash == request_hash(provider, endpoint, params),
            DataSource.fetched_at >= now - max_age,
            or_(DataSource.expires_at.is_(None), DataSource.expires_at > now),
            or_(DataSource.status_code.is_(None), DataSource.status_code < 400),
        )
        .order_by(DataSource.fetched_at.desc())
        .limit(1)
    )


def store_response(
    db: Session,
    provider: str,
    endpoint: str,
    params: dict,
    response: dict | list | None,
    status_code: int | None,
    ttl: timedelta | None,
) -> DataSource:
    now = datetime.now(UTC)
    record = DataSource(
        provider=provider,
        endpoint=endpoint,
        request_hash=request_hash(provider, endpoint, params),
        request_params=params,
        response=response,
        status_code=status_code,
        fetched_at=now,
        expires_at=now + ttl if ttl else None,
    )
    db.add(record)
    db.commit()
    return record
