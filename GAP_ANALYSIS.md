# Gap Analysis — End Goal vs Current State

- **End goal:** "Local SEO Audit API — Product & Workflow Brief", v1 scope.
- **Current state:** the Flask + Selenium Google Maps scraper, tested on 2026-10-07 with 11 real listings (1 × "Joe's Pizza 1435 Broadway New York", 10 × "plumber in Manchester").
- **Plan:** see [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md).

Legend:
- ✅ works
- ⚠️ partial / needs fixing
- ❌ wrong or broken
- ⬜ not built

Source column:
- **Places** = Google Places API (New), free tier
- **GBP API** = Business Profile API, owned/managed listings only
- **SERP** = SerpApi / DataForSEO
- **Own** = our own code

---

## 1. Find & verify the GBP (brief §1)

| Field / step | End goal | Current | Status | Improvement | Source |
|---|---|---|---|---|---|
| Search input | business name + exact address | free-text query | ⚠️ | Split into `name` + `address` inputs | Own |
| Candidate search | Places Text Search | Selenium types into Maps | ❌ | Replace with Text Search | Places |
| `place_id` | stored as stable ID | not captured | ⬜ | Store from Text Search | Places |
| `match_confidence` | 0–1 score | none (takes whatever Maps shows) | ⬜ | Weighted score: name 0.30, address 0.25, phone 0.20, domain 0.15, distance 0.10 | Own |
| `match_reasons` | list of reasons | none | ⬜ | Emit one reason per matched signal | Own |
| `manual_review_required` | true if < 0.85 | none | ⬜ | Return top 5 candidates + `/select` endpoint | Own |
| Website ↔ GBP distance | used in score | none | ⬜ | Geocode website address, haversine to GBP lat/lng | Places Geocoding |

---

## 2. GBP profile fields (brief §1 "Collect GBP profile data")

| Brief field | Current field | Test result (11 listings) | Status | Improvement | Source |
|---|---|---|---|---|---|
| `place_id` | — | — | ⬜ | Add | Places `id` |
| `business_name` | `name` | 11/11 | ✅ | Rename only | Places `displayName` |
| `formatted_address` | `address` | 4/11 (7 are service-area businesses with hidden address) | ⚠️ | Store `null` + `is_service_area_business=true` instead of `"N/A"` | Places `formattedAddress` |
| `map_pin_status` | — | — | ⬜ | `present` if coordinates + Maps URL exist | Places `location`, `googleMapsUri` |
| `pin_vs_website_address_distance_meters` | — | — | ⬜ | Geocode website NAP address → distance | Own + Geocoding |
| coordinates (lat/lng) | — | — | ⬜ | Add | Places `location` |
| `website_url` | `website` | 11/11 | ✅ | Keep UTM stripping | Places `websiteUri` |
| `primary_category` | `category` | 11/11 | ✅ | Rename | Places `primaryTypeDisplayName` |
| `secondary_categories` | — | — | ⬜ | Places `types` (Google types, *not* exact GBP category names; exact names only via GBP API for managed listings) | Places / GBP API |
| `phone_number` | `phone` | 11/11 | ✅ | Store E.164 + national format | Places `internationalPhoneNumber` |
| `opening_hours` | `weekly_hours` | 11/11 | ⚠️ | Use structured periods (`open`/`close` per day) instead of scraped text; strip ` `, holiday labels ("Monday(Columbus Day)… Hours might differ") into a separate `special_hours` | Places `regularOpeningHours`, `currentOpeningHours` |
| current open/closed | `current_hours_status` | **1/11** | ❌ | Use `currentOpeningHours.openNow` | Places |
| `business_status` | — | — | ⬜ | OPERATIONAL / CLOSED_TEMPORARILY / CLOSED_PERMANENTLY | Places `businessStatus` |
| `service_options` | — | — | ⬜ | delivery, dineIn, takeout, curbsidePickup… (`null` if not returned) | Places |
| `accessibility_attributes` | — | — | ⬜ | wheelchair entrance/parking/restroom/seating | Places `accessibilityOptions` |
| `rating` | `rating` | 11/11 | ✅ | — | Places `rating` |
| `review_count` | `review_count` | **0/11 correct** | ❌ | **Bug:** XPath matches review-topic buttons ("mentioned in N reviews"). Replace with `userRatingCount` | Places |
| `photos_count_available` | `photo_count` ("56 Photos") | 11/11 | ⚠️ | Places returns max 10 photo refs, not the total. Store `photos_count_available` (≤10) as int; drop the scraped total | Places `photos` |
| `last_checked_at` | — | — | ⬜ | Timestamp every fetch | Own |
| plus code | `plus_code` | 4/11 | ⚠️ | Not in brief; free via Places `plusCode` | Places |
| social profiles | `social_profiles` | 5/11, 1 false positive | ⚠️ | Not in brief; keep as part of website extraction; reject `?status=`, `/sharer`, `/intent`, `/home?` links | Own |
| products | `products` | **0/11** | ❌ | Not available publicly; use website service extraction (§3) and the GBP API for managed listings | Own / GBP API |

**Profile score today:** 7 of 17 brief fields reliable (~40%).

---

## 3. Products, services & categories (brief Step 4)

| Requirement | Current | Status | Improvement | Source |
|---|---|---|---|---|
| GBP primary + secondary categories → services | primary only | ⚠️ | Add secondary (types) | Places |
| GBP products/services (managed profiles) | Products tab scraping, 0/11 | ❌ | GBP API | GBP API |
| Client website service pages | — | ⬜ | Crawl sitemap + nav; detect service pages | Own (httpx → Playwright fallback) |
| Website headings + `LocalBusiness`/`Service` schema | — | ⬜ | `extruct` JSON-LD/microdata | Own |
| Review-topic analysis | `review_topics` chips (10/11) | ⚠️ | Derive from our own NLP on reviews (Places does not return Google's topic chips) | Own |
| Each service has `service_name`, `source`, `source_url`, `confidence` | — | ⬜ | Add `services` table | Own |
| Unavailable GBP data = `null`, never "not offered" | uses `"N/A"` / `[]` | ❌ | Use `null` everywhere | Own |

---

## 4. Reviews & tags (brief Step 5)

| Brief field | Current field | Status | Improvement | Source |
|---|---|---|---|---|
| `review_id` | — | ⬜ | Review resource `name` | Places / GBP API |
| `author_name` | `reviewer` | ⚠️ | Rename; keep author attribution URL (required for display) | Places `authorAttribution` |
| `rating` | `stars` | ⚠️ | Rename | Places |
| `review_text` | `text` | ⚠️ | Rename | Places `text` / `originalText` |
| `published_at` | `date` ("a month ago") | ❌ | ISO timestamp, not relative text | Places `publishTime` |
| `owner_reply` | `has_owner_reply` (bool) | ⚠️ | Reply **text**; only available via GBP API for managed listings, `null` for competitors | GBP API |
| `review_url` | — | ⬜ | Link to the review on Google Maps (required by Google attribution policy) | Places `googleMapsUri` |
| `language` | — | ⬜ | Detect / use `originalText.languageCode` | Places + Own |
| `sentiment` | — | ⬜ | positive / neutral / negative | Own NLP |
| `tags` | — | ⬜ | Phrase extraction | Own NLP |
| `mentioned_services` | — | ⬜ | Match against services list | Own NLP |
| Theme categories (quality, staff, price, speed, cleanliness, communication, service) | — | ⬜ | Fixed taxonomy classifier | Own NLP |
| Monthly theme/sentiment aggregation | — | ⬜ | Aggregate by `published_at` month | Own |
| `review_summary` (total, average, sentiment distribution, top +/− topics) | — | ⬜ | Build | Own |
| Review collection itself | 5 reviews for 1/11, **0 for 10/11** | ❌ | Places: up to 5 per place (all public data allows). GBP API: full history for managed listings | Places / GBP API |
| Never claim "all reviews" for competitors | — | ⬜ | Label as "sample (max 5)" | Own |

---

## 5. Keywords (brief §2)

| Requirement | Current | Status | Improvement |
|---|---|---|---|
| Build from client services, service areas, user keywords, competitor patterns | — | ⬜ | Keyword generator |
| Formats `{service} in {city}`, `{service} near me`, `{service} {city}` | — | ⬜ | Templates |
| Store `keyword, service, location_name, latitude, longitude, language, country, device` | — | ⬜ | `keywords` table |

---

## 6. Rank tracking (brief §2)

| Requirement | Current | Status | Improvement | Source |
|---|---|---|---|---|
| Local Pack results (top 3) | — | ⬜ | Per keyword | SERP |
| Local Finder / Maps results (10+) | the category search *does* list 10 Maps results, but rank isn't stored | ⚠️ | Store `rank` per result through a licensed provider | SERP |
| Per-result fields: `keyword_id, checked_at, result_type, rank, place_id, business_name, address, primary_category, rating, review_count, website_url, maps_url, is_client_business` | partial (name, address, category, rating, website) | ⚠️ | Full record per result | SERP |
| Search context: date/time, country, language, device, city/lat/lng, radius, provider, result type | — | ⬜ | `ranking_runs` table | Own |

---

## 7. Visibility metrics (brief §2)

All ⬜:
- Local Pack rank per keyword
- Local Finder rank per keyword
- Pack and Finder appearance counts
- Average ranks
- Top-3 / Top-10 / not-found counts
- Visibility score (0–100)
- Change since previous audit

---

## 8. Competitors & gaps (brief §3)

| Requirement | Status | Improvement |
|---|---|---|
| Competitor rule: ≥20% of keywords OR ≥3 Local Pack appearances | ⬜ | Compute from ranking runs |
| Competitor profile data (same fields as client) | ⬜ | Reuse profile pipeline (§2) |
| Compare categories, review count, rating, review velocity, domain, services, Pack/Finder appearance, keyword overlap | ⬜ | `competitor_metrics` snapshots |
| Gaps: service, category, review-topic | ⬜ | Rules, "review eligibility" wording only |

---

## 9. API & jobs (brief §4)

| Required | Current | Status |
|---|---|---|
| `POST /v1/projects` | — | ⬜ |
| `POST /v1/projects/{id}/discover-business` | — | ⬜ |
| `POST /v1/projects/{id}/gbp-audit` | `POST /api/scrape` (closest equivalent) | ❌ replace |
| `POST /v1/projects/{id}/keywords/generate` | — | ⬜ |
| `POST /v1/projects/{id}/rankings/run` | — | ⬜ |
| `GET /v1/projects/{id}/rankings` | — | ⬜ |
| `GET /v1/projects/{id}/competitors` | — | ⬜ |
| `GET /v1/projects/{id}/gaps` | — | ⬜ |
| `GET /v1/projects/{id}/report` | — | ⬜ |
| `POST /v1/jobs`, `GET /v1/jobs/{id}` | — | ⬜ |
| Async background jobs | Synchronous; one request blocks **~30 s per listing (5 min for 10)** | ❌ |
| Statuses `queued → running → partial_success → completed / failed` | — | ⬜ |
| `GET /api/data` | exists (not in brief) | — keep as debug only |

---

## 10. Storage (brief §5)

| Requirement | Current | Status | Improvement |
|---|---|---|---|
| 15 relational tables | one `data.json` file | ❌ | PostgreSQL + migrations |
| Upsert / dedupe | **Bug: only the last result is kept** (keys `Website`/`Company_Name` vs `website`/`name`, so every item has key `None`) | ❌ | Dedupe on `place_id` |
| Provenance on every record: `source`, `source_url`/provider, `collected_at`, `last_verified_at`, `raw_response_reference`, `confidence_score` | none | ⬜ | Columns on every collected-data table + `data_sources` table |
| Google content retention rules | none | ⬜ | `expires_at` + cleanup task |

---

## 11. Data-source rules (brief §6)

| Rule | Current | Status |
|---|---|---|
| GBP APIs for owned/managed profiles | not used | ⬜ |
| Places API for public discovery/details | not used | ⬜ |
| Licensed SERP provider for rankings | not used | ⬜ |
| **No browser bot scraping Google Search/Maps** | **the entire app is a Maps scraping bot** | ❌ **violates** |
| Don't claim "all reviews" for competitors | n/a | ⬜ |
| Attribution + link to Google for reviews | none | ⬜ |

---

## 12. v1 scope checklist (brief §7)

| v1 item | Status | Approx. done |
|---|---|---|
| Website NAP and service extraction | ⚠️ only social links | 5% |
| GBP matching with manual-review fallback | ⬜ | 0% |
| GBP audit (name, address, categories, coordinates, ratings, review count, website, hours, available reviews) | ⚠️ 5 of 9 items reliable via a disallowed method | 35% (logic), reusable: 0% |
| Review tags and sentiment summary | ⬜ (Google topic chips only) | 5% |
| Service + location keyword generator | ⬜ | 0% |
| Local Pack and Local Finder rank tracking | ⬜ | 0% |
| Automatic competitor list | ⬜ | 0% |
| Basic gap report | ⬜ | 0% |
| CSV and PDF/HTML export | ⬜ | 0% |
| **Overall v1** | | **≈ 5–10%** |

---

## 13. Improvement list, prioritised

### P0 — foundation (must happen first)
1. Replace Selenium Maps scraping with **Places API Text Search + Place Details**. This fixes review count, open-now, business status, coordinates and `place_id` at once.
2. Replace `data.json` with **PostgreSQL**, keyed on `place_id`. This fixes the overwrite bug.
3. Replace Flask sync endpoint with **FastAPI + background jobs**.
4. Use `null` for unavailable data instead of `"N/A"`; numbers as numbers (photo count, ratings).

### P1 — complete the GBP audit
5. Match confidence + `match_reasons` + manual review (< 0.85).
6. Website crawler: NAP + schema + service pages (+ keep social link extraction, with the share-link filter fixed).
7. Pin-vs-website distance.
8. Structured opening hours + special/holiday hours separated.
9. Secondary categories, service options, accessibility.

### P2 — reviews
10. Reviews with id, ISO date, URL, author attribution, language.
11. NLP: sentiment, tags, themes, mentioned services; monthly aggregation; `review_summary`.
12. Owner-reply text via GBP API for managed listings.

### P3 — visibility & competition
13. Keyword generator.
14. SERP provider adapter → Local Pack + Local Finder with full search context.
15. Visibility metrics + score + delta.
16. Competitor discovery + metrics + gap rules.

### P4 — output
17. `/report` HTML/PDF/CSV with source attribution.
18. Provenance fields + retention cleanup.

### Quick fixes if the old scraper must keep running meanwhile
- `review_count`: read from the rating element's aria-label (e.g. "1,234 reviews") and exclude buttons containing "mentioned in".
- Upsert: key on lowercase `website` / `name`.
- Social links: reject `?status=`, `/home?`, `/sharer`, `/intent`.
- Hours: replace ` ` with a space and split holiday labels out of the day name.
