"""Review NLP (brief Step 5): language, sentiment, tags, themes, mentioned services, summary.

Runs locally and free. Deliberately small and explainable instead of a large ML model:
  - language: Google's languageCode when present, else `langdetect`
  - sentiment: star rating blended with VADER (English text); rating only for other languages
  - tags + themes: a local-business lexicon matched per sentence; each tag takes the sentiment of its sentence,
    so "the staff were not friendly" is a negative "friendly staff" mention
  - mentioned services: the project's service list (Google categories + website offerings) found in the text
"""

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from functools import lru_cache

from rapidfuzz import fuzz

THEMES = {
    "service_quality": "Service quality",
    "staff": "Staff / customer service",
    "price_value": "Price / value",
    "speed": "Speed",
    "cleanliness": "Cleanliness",
    "communication": "Communication",
    "specific_service": "Specific service",
}

# (pattern, tag, theme). Word-bounded, case-insensitive. Order matters only within one sentence.
LEXICON: list[tuple[str, str, str]] = [
    # staff / customer service
    (r"friendly|welcoming|warm", "friendly staff", "staff"),
    (r"polite|courteous|respectful", "polite staff", "staff"),
    (r"professional(ism|ly)?", "professional", "staff"),
    (r"helpful|above and beyond|went the extra mile", "helpful", "staff"),
    (r"knowledgeable|expertise|experienced|expert", "knowledgeable", "staff"),
    (r"honest|trustworthy|integrity|reliable|dependable", "honest / reliable", "staff"),
    (r"rude|unprofessional|disrespectful|arrogant|condescending", "rude / unprofessional", "staff"),
    # price / value
    (
        r"(fair|reasonable|competitive|affordable|great|good|best) (price|pricing|prices|rates?|quotes?)"
        r"|(reasonably|fairly|well|competitively) priced|affordable",
        "fair pricing",
        "price_value",
    ),
    (
        r"value for money|(good|great|overall|excellent) value|worth (every|the) (penny|money|price)",
        "good value",
        "price_value",
    ),
    (
        r"no hidden (fees|costs|charges)|transparent pricing|on budget|within (the )?budget",
        "transparent pricing",
        "price_value",
    ),
    (r"expensive|overpriced|overcharg\w*|pricey|rip[- ]?off", "expensive", "price_value"),
    # speed
    (r"on time|punctual|on schedule|on-time|ahead of schedule", "on time", "speed"),
    (
        r"fast|quick(ly)?|prompt(ly)?|speedy|swift|same[- ]day|in no time|efficient(ly)?",
        "fast service",
        "speed",
    ),
    (
        r"responsive|response time|responded (quickly|fast|promptly)|got back to (me|us) (quickly|fast)",
        "responsive",
        "speed",
    ),
    (
        r"late|delay(s|ed)?|slow|took (forever|too long|weeks|months)|behind schedule|no[- ]show|didn'?t show( up)?",
        "delays",
        "speed",
    ),
    # cleanliness
    (r"clean(ed)? up|tidy|spotless|clean|neat", "clean / tidy", "cleanliness"),
    (r"messy|left a mess|dirty|debris", "messy", "cleanliness"),
    # communication
    (r"communicat\w*", "communication", "communication"),
    (
        r"kept (me|us) (informed|updated|in the loop)|regular updates|updated (me|us)",
        "kept informed",
        "communication",
    ),
    (
        r"explain\w*|walked (me|us) through|answered (all )?(my|our) questions",
        "clear explanations",
        "communication",
    ),
    (
        r"never (called|got|get) back|didn'?t (call|respond|reply|answer)|no response|unresponsive|hard to (reach|contact)"
        r"|ignored (my|our)",
        "hard to reach",
        "communication",
    ),
    # service quality
    (
        r"quality|workmanship|craftsmanship|attention to detail|detail[- ]oriented|meticulous",
        "quality workmanship",
        "service_quality",
    ),
    (
        r"(great|excellent|amazing|fantastic|outstanding|exceptional|beautiful|perfect|awesome|superb|wonderful) "
        r"(job|work|result|results|service|experience)",
        "great results",
        "service_quality",
    ),
    (
        r"(poor|bad|terrible|shoddy|sloppy|awful|horrible|subpar) (job|work|quality|service|workmanship|experience)",
        "poor workmanship",
        "service_quality",
    ),
    (
        r"exceeded (our|my|all) expectations|beyond (our|my) expectations",
        "exceeded expectations",
        "service_quality",
    ),
    (r"(highly |would |definitely |strongly )?recommend\w*", "recommendation", "service_quality"),
    (
        r"redo|re-do|had to (come back|return|call them back) to fix|still (leaking|broken|not working)",
        "rework needed",
        "service_quality",
    ),
    (r"warranty|guarantee", "warranty", "service_quality"),
]
# Words that carry their own meaning ("fast", "rude") keep it unless negated ("not fast"); context-dependent
# topics ("communication", "quality", "warranty") take the sentence's sentiment.
POLARITY = {
    "friendly staff": "+", "polite staff": "+", "professional": "+", "helpful": "+", "knowledgeable": "+",
    "honest / reliable": "+", "rude / unprofessional": "-", "fair pricing": "+", "good value": "+",
    "transparent pricing": "+", "expensive": "-", "on time": "+", "fast service": "+", "responsive": "+",
    "delays": "-", "clean / tidy": "+", "messy": "-", "kept informed": "+", "clear explanations": "+",
    "hard to reach": "-", "great results": "+", "poor workmanship": "-", "exceeded expectations": "+",
    "recommendation": "+", "rework needed": "-",
}  # fmt: skip
COMPILED = [(re.compile(rf"\b(?:{p})\b", re.IGNORECASE), tag, theme) for p, tag, theme in LEXICON]
NEGATION_BEFORE = re.compile(
    r"\b(not|no|never|n't|wasn't|weren't|isn't|aren't|didn't|don't|doesn't|wouldn't|won't|hardly|barely|nothing)"
    r"\b(?:\W+\w+){0,2}\W*$",
    re.IGNORECASE,
)

# Phrases that name a service in review text ("boiler repair", "kitchen renovation").
SERVICE_WORDS = (
    r"repairs?|installations?|install|replacements?|renovations?|remodel(ing)?|cleaning|inspections?|painting"
    r"|removal|maintenance|fitting|detailing|design|treatments?|haircut|massage|delivery|catering|consultation"
)
SERVICE_PHRASE = re.compile(rf"\b((?:[a-z]+ ){{1,2}}(?:{SERVICE_WORDS}))\b", re.IGNORECASE)
SERVICE_OBJECTS = {
    "kitchen", "bathroom", "basement", "roof", "roofing", "boiler", "furnace", "heater", "hvac", "ac", "air",
    "pipe", "pipes", "drain", "drains", "sewer", "water", "gas", "toilet", "sink", "tap", "faucet", "shower",
    "bath", "tub", "deck", "fence", "window", "windows", "door", "doors", "floor", "flooring", "carpet", "tile",
    "tiles", "cabinet", "cabinets", "wall", "walls", "ceiling", "interior", "exterior", "house", "home", "attic",
    "garage", "driveway", "patio", "pool", "garden", "lawn", "tree", "gutter", "gutters", "chimney", "brick",
    "concrete", "plumbing", "electrical", "wiring", "panel", "lighting", "solar", "appliance", "dishwasher",
    "washer", "dryer", "fridge", "oven", "engine", "brake", "brakes", "tire", "tires", "oil", "car", "auto",
    "transmission", "dental", "teeth", "tooth", "skin", "facial", "hair", "nail", "nails", "pest", "mold", "mould",
    "upholstery", "office", "commercial", "residential", "emergency", "burst", "blocked", "leak",
    "countertop", "stair", "staircase", "condo", "apartment", "pump", "septic", "duct", "vent",
}  # fmt: skip
PHRASE_STOP = {
    "the", "a", "an", "and", "our", "my", "their", "his", "her", "this", "that", "for", "with", "of", "to",
    "great", "good", "excellent", "amazing", "fast", "quick", "full", "whole", "entire", "new", "some", "any",
    "did", "do", "does", "was", "were", "is", "are", "be", "had", "have", "has", "also", "very", "really", "they",
    "we", "i", "you", "it", "its", "which", "who", "after", "before", "on", "in", "at", "from", "by", "your",
}  # fmt: skip


@dataclass
class TagHit:
    tag: str
    theme: str
    sentiment: str  # positive | neutral | negative
    sentence: str


@dataclass
class Analysis:
    language: str | None
    sentiment: str
    sentiment_score: float  # -1 .. 1
    tags: list[TagHit] = field(default_factory=list)
    mentioned_services: list[str] = field(default_factory=list)
    service_phrases: list[str] = field(default_factory=list)


@lru_cache(maxsize=1)
def _vader():
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

    return SentimentIntensityAnalyzer()


def detect_language(text: str, given: str | None = None) -> str | None:
    if given:
        return given.split("-")[0].lower()
    if not text or len(text.strip()) < 12:
        return None
    try:
        from langdetect import DetectorFactory, detect

        DetectorFactory.seed = 0  # deterministic
        return detect(text)
    except Exception:
        return None


def _label(score: float, threshold: float = 0.2) -> str:
    return "positive" if score >= threshold else "negative" if score <= -threshold else "neutral"


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text or "") if s.strip()]


def _service_in_text(service: str, text_lower: str) -> bool:
    name = service.lower()
    if len(name) < 4:
        return False
    if re.search(rf"\b{re.escape(name)}s?\b", text_lower):
        return True
    words = name.split()
    # multi-word services: all main words present close together (e.g. "renovated the kitchen" ~ no)
    return len(words) > 1 and fuzz.partial_ratio(name, text_lower) >= 92


def _service_phrases(text: str, exclude: set[str] | None = None) -> list[str]:
    """'kitchen renovation', 'burst pipe repairs': the word before the service word must be a thing worked on."""
    exclude = exclude or set()
    out = []
    for m in SERVICE_PHRASE.finditer(text):
        words = [w for w in m.group(1).lower().split() if w not in PHRASE_STOP]
        if len(words) < 2 or words[-2] not in SERVICE_OBJECTS or exclude & set(words[:-1]):
            continue
        out.append(" ".join(words))
    return out


def analyze_review(
    text: str | None,
    rating: int | None,
    language: str | None = None,
    services: list[str] | None = None,
    business_name: str | None = None,
) -> Analysis:
    text = text or ""
    lang = detect_language(text, language)
    use_vader = bool(text) and (lang in (None, "en"))

    rating_score = ((rating - 3) / 2) if rating else 0.0  # 1★ -> -1, 3★ -> 0, 5★ -> +1
    if use_vader:
        text_score = _vader().polarity_scores(text)["compound"]
        score = 0.6 * rating_score + 0.4 * text_score if rating else text_score
    else:
        score = rating_score
    analysis = Analysis(language=lang, sentiment=_label(score), sentiment_score=round(score, 3))

    seen: set[str] = set()
    for sentence in _sentences(text) if use_vader else []:
        compound = _vader().polarity_scores(sentence)["compound"]
        polarity = _label(compound, 0.05) if abs(compound) >= 0.05 else _label(rating_score)
        for rx, tag, theme in COMPILED:
            m = rx.search(sentence)
            if tag in seen or not m:
                continue
            inherent = POLARITY.get(tag)
            negated = bool(NEGATION_BEFORE.search(sentence[: m.start()]))
            if inherent is None:
                tag_polarity = polarity
            elif inherent == "+":
                tag_polarity = "negative" if negated else "positive"
            else:
                if negated:  # "never late": no complaint, and not strong enough to claim the opposite
                    continue
                tag_polarity = "negative"
            seen.add(tag)
            analysis.tags.append(TagHit(tag, theme, tag_polarity, sentence[:300]))

    lower = text.lower()
    for name in services or []:
        if name.lower() not in {s.lower() for s in analysis.mentioned_services} and _service_in_text(
            name, lower
        ):
            analysis.mentioned_services.append(name)
            analysis.tags.append(TagHit(name, "specific_service", analysis.sentiment, ""))
    own_name = {
        w for w in re.findall(r"[a-z]+", (business_name or "").lower()) if len(w) > 2
    } - SERVICE_OBJECTS
    analysis.service_phrases = _service_phrases(text, own_name) if use_vader else []
    return analysis


# ---------- aggregation ----------


def summarize(reviews: list[dict], total_review_count: int | None, average_rating: float | None) -> dict:
    """reviews: dicts with rating, sentiment, published_at (datetime|None), tags [{tag, theme, sentiment}]."""
    sentiment = Counter(r.get("sentiment") or "neutral" for r in reviews)
    topic_pos, topic_neg = Counter(), Counter()
    themes: dict[str, Counter] = defaultdict(Counter)
    monthly: dict[str, dict] = {}
    for r in reviews:
        for t in r.get("tags", []):
            if t["theme"] == "specific_service":
                themes["specific_service"][t["sentiment"]] += 1
                continue
            themes[t["theme"]][t["sentiment"]] += 1
            if t["sentiment"] == "positive":
                topic_pos[t["tag"]] += 1
            elif t["sentiment"] == "negative":
                topic_neg[t["tag"]] += 1
        if r.get("published_at"):
            key = r["published_at"].strftime("%Y-%m")
            m = monthly.setdefault(
                key, {"month": key, "reviews": 0, "rating_sum": 0, "rated": 0, "sentiment": Counter(),
                      "themes": Counter()},
            )  # fmt: skip
            m["reviews"] += 1
            if r.get("rating"):
                m["rating_sum"] += r["rating"]
                m["rated"] += 1
            m["sentiment"][r.get("sentiment") or "neutral"] += 1
            for t in r.get("tags", []):
                m["themes"][t["theme"]] += 1

    rated = [r["rating"] for r in reviews if r.get("rating")]
    return {
        "total_review_count": total_review_count,
        "average_rating": average_rating,
        "analysed_review_count": len(reviews),
        "analysed_average_rating": round(sum(rated) / len(rated), 2) if rated else None,
        "sentiment_distribution": {k: sentiment.get(k, 0) for k in ("positive", "neutral", "negative")},
        "top_positive_topics": [t for t, _ in topic_pos.most_common(5)],
        "top_negative_topics": [t for t, _ in topic_neg.most_common(5)],
        "themes": {
            key: {"label": THEMES[key], **{s: c.get(s, 0) for s in ("positive", "neutral", "negative")}}
            for key, c in sorted(themes.items(), key=lambda kv: -sum(kv[1].values()))
        },
        "monthly": [
            {
                "month": m["month"],
                "reviews": m["reviews"],
                "average_rating": round(m["rating_sum"] / m["rated"], 2) if m["rated"] else None,
                "sentiment": {s: m["sentiment"].get(s, 0) for s in ("positive", "neutral", "negative")},
                "themes": dict(m["themes"]),
            }
            for m in sorted(monthly.values(), key=lambda m: m["month"])
        ],
        "note": (
            f"Based on {len(reviews)} of {total_review_count} reviews (the sample Google provides)."
            if total_review_count and total_review_count > len(reviews)
            else None
        ),
    }
