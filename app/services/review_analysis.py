"""Run the review NLP over a project's stored reviews and save sentiment, tags and review-topic services."""

from collections import Counter

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import GbpProfile, GbpReview, Project, ReviewTag, Service
from app.models.base import utcnow
from app.services.review_nlp import analyze_review, summarize

SOURCE = "review_nlp"


def project_services(db: Session, project: Project) -> list[str]:
    names = db.scalars(
        select(Service.service_name).where(Service.project_id == project.id, Service.source != "review_topic")
    ).all()
    return sorted(set(names), key=str.lower)


def analyze_project_reviews(db: Session, project: Project) -> dict:
    business = project.client_business
    if business is None:
        raise LookupError("The project is not linked to a Google listing yet")
    reviews = db.scalars(
        select(GbpReview).where(GbpReview.business_id == business.id).order_by(GbpReview.position)
    ).all()
    services = project_services(db, project)
    by_name = {
        s.service_name.lower(): s.id
        for s in db.scalars(select(Service).where(Service.project_id == project.id)).all()
    }

    if reviews:
        db.execute(delete(ReviewTag).where(ReviewTag.review_id.in_([r.id for r in reviews])))
    now = utcnow()
    phrases: Counter = Counter()
    phrase_example: dict[str, str] = {}
    tag_count = 0
    for r in reviews:
        a = analyze_review(r.review_text, r.rating, r.language, services, business.name)
        r.language = a.language or r.language
        r.sentiment = a.sentiment
        r.sentiment_score = a.sentiment_score
        r.mentioned_services = a.mentioned_services or None
        for hit in a.tags:
            db.add(
                ReviewTag(
                    review_id=r.id,
                    tag=hit.tag,
                    theme=hit.theme,
                    sentiment=hit.sentiment,
                    sentence=hit.sentence or None,
                    mentioned_service_id=by_name.get(hit.tag.lower())
                    if hit.theme == "specific_service"
                    else None,
                    source=SOURCE,
                    source_url=r.review_url,
                    collected_at=now,
                    confidence_score=0.7,
                )
            )
            tag_count += 1
        for p in a.service_phrases:
            phrases[p] += 1
            phrase_example.setdefault(p, r.review_url or "")

    # Brief Step 4: review-topic analysis is one of the service sources.
    known = {s.lower() for s in services}
    db.execute(delete(Service).where(Service.project_id == project.id, Service.source == "review_topic"))
    added = 0
    for phrase, count in phrases.most_common(15):
        if phrase in known:
            continue
        db.add(
            Service(
                project_id=project.id,
                business_id=business.id,
                service_name=phrase.capitalize(),
                normalized_name=phrase,
                source="review_topic",
                source_url=phrase_example[phrase] or None,
                confidence_score=min(0.4 + 0.1 * count, 0.7),
                collected_at=now,
            )
        )
        added += 1
    db.commit()

    summary = review_summary(db, project)
    return {
        "reviews_analysed": len(reviews),
        "tags": tag_count,
        "review_topic_services": added,
        "sentiment_distribution": summary["sentiment_distribution"],
        "top_positive_topics": summary["top_positive_topics"],
        "top_negative_topics": summary["top_negative_topics"],
    }


def review_summary(db: Session, project: Project) -> dict | None:
    business = project.client_business
    if business is None:
        return None
    reviews = db.scalars(
        select(GbpReview).where(GbpReview.business_id == business.id).order_by(GbpReview.position)
    ).all()
    tags = db.scalars(select(ReviewTag).where(ReviewTag.review_id.in_([r.id for r in reviews]))).all()
    by_review: dict = {}
    for t in tags:
        by_review.setdefault(t.review_id, []).append(
            {"tag": t.tag, "theme": t.theme, "sentiment": t.sentiment}
        )
    profile = db.scalar(
        select(GbpProfile).where(GbpProfile.business_id == business.id).order_by(GbpProfile.created_at.desc())
    )
    rows = [
        {
            "rating": r.rating,
            "sentiment": r.sentiment,
            "published_at": r.published_at,
            "tags": by_review.get(r.id, []),
        }
        for r in reviews
        if r.sentiment
    ]
    return summarize(rows, profile.review_count if profile else None, profile.rating if profile else None)


def tags_by_review(db: Session, review_ids: list) -> dict:
    if not review_ids:
        return {}
    out: dict = {}
    for t in db.scalars(select(ReviewTag).where(ReviewTag.review_id.in_(review_ids))).all():
        out.setdefault(t.review_id, []).append(
            {"tag": t.tag, "theme": t.theme, "sentiment": t.sentiment, "sentence": t.sentence}
        )
    return out
