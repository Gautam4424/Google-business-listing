from datetime import UTC, datetime

from app.services.review_nlp import analyze_review, summarize


def _tags(a):
    return {(t.tag, t.sentiment) for t in a.tags}


def test_positive_review_tags_and_sentiment():
    a = analyze_review("Fast, friendly and fair prices! Highly recommend.", 5, "en")
    assert a.sentiment == "positive" and a.sentiment_score > 0.5
    assert {("fast service", "positive"), ("friendly staff", "positive"), ("fair pricing", "positive"),
            ("recommendation", "positive")} <= _tags(a)  # fmt: skip
    assert {t.theme for t in a.tags} >= {"speed", "staff", "price_value", "service_quality"}


def test_negation_makes_the_topic_negative():
    a = analyze_review("The staff were not friendly. They left a mess in the kitchen.", 2, "en")
    assert a.sentiment == "negative"
    assert ("friendly staff", "negative") in _tags(a)
    assert ("messy", "negative") in _tags(a)


def test_mixed_review_scores_each_sentence():
    a = analyze_review("Great work on the bathroom. Communication was poor and they were late.", 4, "en")
    tags = _tags(a)
    assert ("great results", "positive") in tags
    assert ("communication", "negative") in tags
    assert ("delays", "negative") in tags


def test_other_languages_use_the_rating():
    a = analyze_review("Excelente servicio, muy rápido y profesional", 5, None)
    assert a.language == "es"
    assert a.sentiment == "positive" and a.tags == []  # the English lexicon is not applied


def test_rating_only_review():
    a = analyze_review(None, 1, None)
    assert a.sentiment == "negative" and a.tags == []


def test_mentioned_services_and_service_phrases():
    a = analyze_review(
        "They did our kitchen renovation and a boiler repair. Excellent job.",
        5,
        "en",
        services=["Kitchen Renovation", "Roofing", "Gas"],
    )
    assert a.mentioned_services == ["Kitchen Renovation"]
    assert ("Kitchen Renovation", "positive") in _tags(a)
    assert "boiler repair" in a.service_phrases


def test_inherent_words_keep_their_meaning_in_negative_sentences():
    a = analyze_review("Our old plumber let us down badly, these guys came fast and fixed it.", 5, "en")
    assert ("fast service", "positive") in _tags(a)


def test_recommendation_is_negative_only_when_negated():
    assert ("recommendation", "negative") in _tags(analyze_review("I would not recommend them.", 1, "en"))
    assert ("recommendation", "positive") in _tags(
        analyze_review("Sadly the first company failed us, so I recommend these guys.", 5, "en")
    )


def test_price_phrasings():
    tags = _tags(analyze_review("A very efficient and reasonably priced job. Great overall value.", 5, "en"))
    assert ("fair pricing", "positive") in tags and ("good value", "positive") in tags


def test_negated_complaint_is_not_a_complaint():
    tags = _tags(analyze_review("They were never late and very tidy.", 5, "en"))
    assert not any(t == "delays" for t, _ in tags)
    assert ("clean / tidy", "positive") in tags


def test_service_phrases_need_a_real_object_and_skip_the_business_name():
    a = analyze_review(
        "Luxe did our kitchen renovation. Future renovation plans too. Luxe home renovation team rocks.",
        5,
        "en",
        business_name="Luxe Home Renovation",
    )
    assert a.service_phrases == ["kitchen renovation"]


def test_summary_shape_and_counts():
    may, june = datetime(2026, 5, 3, tzinfo=UTC), datetime(2026, 6, 9, tzinfo=UTC)
    rows = [
        {"rating": 5, "sentiment": "positive", "published_at": may,
         "tags": [{"tag": "fast service", "theme": "speed", "sentiment": "positive"},
                  {"tag": "friendly staff", "theme": "staff", "sentiment": "positive"}]},
        {"rating": 5, "sentiment": "positive", "published_at": june,
         "tags": [{"tag": "fast service", "theme": "speed", "sentiment": "positive"}]},
        {"rating": 2, "sentiment": "negative", "published_at": june,
         "tags": [{"tag": "delays", "theme": "speed", "sentiment": "negative"}]},
    ]  # fmt: skip
    s = summarize(rows, total_review_count=182, average_rating=4.7)
    assert s["total_review_count"] == 182 and s["average_rating"] == 4.7
    assert s["sentiment_distribution"] == {"positive": 2, "neutral": 0, "negative": 1}
    assert s["top_positive_topics"][0] == "fast service"
    assert s["top_negative_topics"] == ["delays"]
    assert s["themes"]["speed"] == {"label": "Speed", "positive": 2, "neutral": 0, "negative": 1}
    assert [m["month"] for m in s["monthly"]] == ["2026-05", "2026-06"]
    assert s["monthly"][1]["average_rating"] == 3.5
    assert "3 of 182" in s["note"]
