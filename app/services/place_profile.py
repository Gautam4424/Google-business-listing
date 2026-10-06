"""Normalise Google Places (New) Place Details and SerpApi review data into our tables' shape.

Unavailable data stays None (brief: never "not offered").
"""

import re
from datetime import UTC, datetime

# Boolean "service option" fields on the Place resource -> label shown to users.
SERVICE_OPTION_FIELDS = {
    "delivery": "Delivery",
    "dineIn": "Dine-in",
    "takeout": "Takeout",
    "curbsidePickup": "Curbside pickup",
    "reservable": "Reservations",
    "servesBreakfast": "Breakfast",
    "servesBrunch": "Brunch",
    "servesLunch": "Lunch",
    "servesDinner": "Dinner",
    "servesBeer": "Beer",
    "servesWine": "Wine",
    "servesCocktails": "Cocktails",
    "servesCoffee": "Coffee",
    "servesDessert": "Dessert",
    "servesVegetarianFood": "Vegetarian food",
    "outdoorSeating": "Outdoor seating",
    "liveMusic": "Live music",
    "menuForChildren": "Kids' menu",
    "goodForChildren": "Good for kids",
    "goodForGroups": "Good for groups",
    "allowsDogs": "Dogs allowed",
    "restroom": "Restroom",
}

DETAILS_FIELDS = [
    "id",
    "displayName",
    "formattedAddress",
    "addressComponents",
    "plusCode",
    "location",
    "googleMapsUri",
    "websiteUri",
    "internationalPhoneNumber",
    "nationalPhoneNumber",
    "primaryType",
    "primaryTypeDisplayName",
    "types",
    "businessStatus",
    "rating",
    "userRatingCount",
    "regularOpeningHours",
    "currentOpeningHours",
    "photos",
    "reviews",
    "editorialSummary",
    "accessibilityOptions",
    *SERVICE_OPTION_FIELDS,
]
DETAILS_FIELD_MASK = ",".join(DETAILS_FIELDS)

# Google types that say nothing about what the business offers.
GENERIC_TYPES = {
    "point_of_interest", "establishment", "premise", "political", "store", "food", "health", "service",
}  # fmt: skip

ACCESSIBILITY_LABELS = {
    "wheelchairAccessibleParking": "Wheelchair-accessible parking",
    "wheelchairAccessibleEntrance": "Wheelchair-accessible entrance",
    "wheelchairAccessibleRestroom": "Wheelchair-accessible restroom",
    "wheelchairAccessibleSeating": "Wheelchair-accessible seating",
}


def humanize_type(t: str) -> str:
    return t.replace("_", " ").capitalize()


def parse_time(value: str | None) -> datetime | None:
    """Google timestamps can have nanoseconds, which datetime.fromisoformat rejects."""
    if not value:
        return None
    value = re.sub(r"(\.\d{6})\d+", r"\1", value.replace("Z", "+00:00"))
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def profile_fields(place: dict) -> dict:
    location = place.get("location") or {}
    regular = place.get("regularOpeningHours")
    current = place.get("currentOpeningHours") or {}
    opening_hours = None
    if regular:
        opening_hours = {
            "weekday_descriptions": regular.get("weekdayDescriptions", []),
            "periods": regular.get("periods", []),
            "open_now": current.get("openNow"),
        }
    special = [d for d in current.get("specialDays", [])] or None

    returned_options = {k: v for k, v in place.items() if k in SERVICE_OPTION_FIELDS}
    service_options = (
        [SERVICE_OPTION_FIELDS[k] for k, v in returned_options.items() if v] if returned_options else None
    )
    accessibility = place.get("accessibilityOptions")
    accessibility_list = (
        [ACCESSIBILITY_LABELS.get(k, humanize_type(k)) for k, v in accessibility.items() if v]
        if accessibility
        else None
    )

    primary = (place.get("primaryTypeDisplayName") or {}).get("text")
    secondary = [
        humanize_type(t)
        for t in place.get("types", [])
        if t not in GENERIC_TYPES and t != place.get("primaryType")
    ]
    return {
        "place_id": place.get("id"),
        "business_name": (place.get("displayName") or {}).get("text"),
        "formatted_address": place.get("formattedAddress"),
        "map_pin_status": "present"
        if location.get("latitude") is not None and place.get("googleMapsUri")
        else "missing",
        "maps_url": place.get("googleMapsUri"),
        "website_url": place.get("websiteUri"),
        "primary_category": primary,
        "secondary_categories": secondary or None,
        "phone_number": place.get("internationalPhoneNumber") or place.get("nationalPhoneNumber"),
        "opening_hours": opening_hours,
        "special_hours": special,
        "business_status": place.get("businessStatus"),
        "service_options": service_options,
        "accessibility_attributes": accessibility_list,
        "rating": place.get("rating"),
        "review_count": place.get("userRatingCount"),
        "photos_count_available": len(place["photos"]) if "photos" in place else None,
        "editorial_summary": (place.get("editorialSummary") or {}).get("text"),
        "plus_code": (place.get("plusCode") or {}).get("compoundCode")
        or (place.get("plusCode") or {}).get("globalCode"),
    }


def category_offerings(place: dict) -> list[tuple[str, float]]:
    """GBP categories as offerings: primary category first (Google types are not exact GBP category names)."""
    out: list[tuple[str, float]] = []
    primary = (place.get("primaryTypeDisplayName") or {}).get("text")
    if primary:
        out.append((primary, 0.95))
    for t in place.get("types", []):
        if t in GENERIC_TYPES or t == place.get("primaryType"):
            continue
        out.append((humanize_type(t), 0.7))
    return out


def places_reviews(place: dict) -> list[dict]:
    reviews = []
    for i, r in enumerate(place.get("reviews", []), start=1):
        text = r.get("text") or r.get("originalText") or {}
        original = r.get("originalText") or text
        author = r.get("authorAttribution") or {}
        reviews.append(
            {
                "review_id": r.get("name") or f"places-{i}",
                "author_name": author.get("displayName"),
                "author_url": author.get("uri"),
                "rating": r.get("rating"),
                "review_text": text.get("text"),
                "published_at": parse_time(r.get("publishTime")),
                "relative_publish_time": r.get("relativePublishTimeDescription"),
                "owner_reply": None,  # not provided by the Places API
                "review_url": r.get("googleMapsUri"),
                "language": original.get("languageCode"),
                "position": i,
                "source": "google_places",
            }
        )
    return reviews


def serpapi_reviews(items: list[dict], start: int = 1) -> list[dict]:
    reviews = []
    for i, r in enumerate(items, start=start):
        user = r.get("user") or {}
        response = r.get("response") or {}
        text = r.get("snippet") or (r.get("extracted_snippet") or {}).get("original")
        rating = r.get("rating")
        reviews.append(
            {
                "review_id": r.get("review_id") or f"serpapi-{i}",
                "author_name": user.get("name"),
                "author_url": user.get("link"),
                "rating": int(rating) if rating is not None else None,
                "review_text": text,
                "published_at": parse_time(r.get("iso_date")),
                "relative_publish_time": r.get("date"),
                "owner_reply": response.get("snippet")
                or (response.get("extracted_snippet") or {}).get("original"),
                "review_url": r.get("link"),
                "language": None,
                "position": i,
                "source": "serpapi",
            }
        )
    return reviews
