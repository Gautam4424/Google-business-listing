"""Nightly clean-up (Google terms: Google content may be cached for at most 30 days; place_id may be kept).

Removed after GOOGLE_DATA_TTL_DAYS (default 30):
  - raw provider responses (data_sources), Google and SerpApi
  - review texts, author names/links and owner replies (rating, sentiment and topics are kept)
  - the topic evidence sentences quoted from those reviews
  - older Google profile snapshots: everything except place_id, rating and review count (kept for
    review velocity); the latest snapshot of each business is kept until the next audit refreshes it
Kept: scores, ranks, history, topics, gaps, competitors, your own website data, and place_id.
"""

from datetime import timedelta

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import DataSource, GbpProfile, GbpReview, ReviewTag
from app.models.base import utcnow

PROFILE_CONTENT = [
    "business_name", "formatted_address", "map_pin_status", "maps_url", "website_url", "primary_category",
    "secondary_categories", "phone_number", "opening_hours", "special_hours", "business_status",
    "editorial_summary", "plus_code", "service_options", "accessibility_attributes", "photos_count_available",
]  # fmt: skip


def cleanup(db: Session, now=None) -> dict:
    now = now or utcnow()
    days = get_settings().google_data_ttl_days
    cutoff = now - timedelta(days=days)

    raw = db.execute(
        delete(DataSource).where(or_(DataSource.expires_at <= now, DataSource.fetched_at < cutoff))
    ).rowcount

    old_reviews = select(GbpReview.id).where(
        GbpReview.collected_at < cutoff,
        or_(GbpReview.review_text.is_not(None), GbpReview.author_name.is_not(None),
            GbpReview.owner_reply.is_not(None)),
    )  # fmt: skip
    review_ids = list(db.scalars(old_reviews))
    if review_ids:
        db.execute(
            update(GbpReview)
            .where(GbpReview.id.in_(review_ids))
            .values(review_text=None, author_name=None, author_url=None, owner_reply=None)
        )
        db.execute(update(ReviewTag).where(ReviewTag.review_id.in_(review_ids)).values(sentence=None))

    latest = (
        select(GbpProfile.business_id, func.max(GbpProfile.created_at).label("latest"))
        .group_by(GbpProfile.business_id)
        .subquery()
    )
    old_profiles = db.scalars(
        select(GbpProfile.id)
        .join(latest, latest.c.business_id == GbpProfile.business_id)
        .where(GbpProfile.created_at < latest.c.latest, GbpProfile.created_at < cutoff)
        .where(or_(GbpProfile.formatted_address.is_not(None), GbpProfile.business_name.is_not(None)))
    ).all()
    if old_profiles:
        db.execute(
            update(GbpProfile)
            .where(GbpProfile.id.in_(old_profiles))
            .values({c: None for c in PROFILE_CONTENT})
        )
    db.commit()
    return {
        "older_than_days": days,
        "raw_responses_deleted": raw,
        "review_texts_removed": len(review_ids),
        "old_profile_snapshots_trimmed": len(old_profiles),
    }
