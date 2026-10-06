# Local SEO Audit API — Implementation Plan

Reference: "Local SEO Audit API — Product & Workflow Brief" (v1 scope, section 7).

---

## 1. Where the current repo stands

The current project is a Flask app that drives headless Chromium with Selenium. It types a query into Google Maps, opens up to 10 listings, scrapes their HTML and appends the results to `data/data.json`.

### Brief requirement vs. current state

| Brief requirement | Current state | Verdict |
|---|---|---|
| Find GBP by name + address, confidence score, `place_id`, manual review below 0.85 | Free-text Maps search; scrapes whatever the top 10 results are. No matching, no confidence, no `place_id` | **Redo** |
| GBP profile fields (address, coordinates, categories, phone, hours, status, rating, review count…) | Scrapes name, primary category, rating, review count, address, website, phone, plus code, hours, photo count. No `place_id`, coordinates, secondary categories, business status, service options or accessibility | **Redo via Places API** |
| `pin_vs_website_address_distance_meters` | Missing | **New** |
| Unified service list (categories + website pages + schema + review topics), each with source/confidence | Products tab scraping only (fragile); no website service extraction | **New** (keep the website-fetch idea) |
| Reviews with id, URL, language, sentiment, tags, mentioned services | Up to 10 scraped reviews: reviewer, stars, date, text, owner-reply flag | **Redo** (source + NLP pipeline) |
| Review summary (sentiment distribution, top topics, monthly aggregation) | Google's "mentioned in N reviews" chips only | **New** |
| Keyword generator `{service} in {city}` etc., with location/device settings | Missing | **New** |
| Local Pack + Local Finder rank tracking with full search context | Missing | **New** |
| Visibility score + change since previous audit | Missing | **New** |
| Competitor discovery (≥20% of keywords or ≥3 Local Pack appearances) | Missing | **New** |
| Gap analysis (category, service, review-topic, ranking) with "review eligibility" wording | Missing | **New** |
| `/v1/...` REST endpoints + async jobs (`queued → running → partial_success/completed/failed`) | One synchronous `POST /api/scrape` that blocks for minutes | **Redo** |
| Relational tables with source/provenance on every record | Single JSON file; upsert keys (`Website`, `Company_Name`) don't match scraper output keys (`website`, `name`), so dedupe is broken | **Redo** |
| CSV + PDF/HTML export | Missing | **New** |
| **"Do not build a browser bot that scrapes Google Search or Maps HTML"** | The whole scraper does exactly this | **Must be removed** |

### What can be reused
- The idea and code of `extract_social_profiles()` / `clean_url()` (fetching the client's own website). These move into the website-extraction module.
- Docker setup (to be extended to docker-compose).
- Nothing in the Google Maps scraping path. The brief explicitly forbids it, and it is also fragile because it depends on obfuscated CSS classes such as `hfpxzc`, `jftiEf` and `DUwDvf`.

---

## 2. Playwright vs Selenium

**For Google Search / Maps: neither.** The brief requires official/licensed sources:
- **Google Places API (New)** for discovery, matching and public profile details.
- **Google Business Profile APIs** for client-owned/managed locations (full reviews, owner replies).
- **A licensed SERP / local-rank provider** for Local Pack and Local Finder rankings (e.g. DataForSEO, SerpApi, BrightLocal). It sits behind a provider interface so it can be swapped.

**For crawling the client's own website: Playwright, but only as a fallback.**
- Most business sites are server-rendered, so plain HTTP (`httpx`) + an HTML parser is 10–50× faster and much cheaper. Use that first.
- Use Playwright only when a page is JS-rendered (empty body, SPA shell, missing NAP).
- Why Playwright over Selenium:
  - built-in auto-waiting (no `time.sleep` everywhere)
  - native async, which fits an async API/worker
  - bundled browser binaries and an official Docker image, so there is no chromedriver version mismatch
  - request interception, so images and fonts can be blocked for speed
  - parallel browser contexts in one process

**Decision:** remove Selenium entirely. Add Playwright as an optional fallback renderer for client websites.

---

## 3. Target architecture

```
                ┌─────────────┐      ┌──────────────┐
  client ──────▶│  FastAPI    │─────▶│ PostgreSQL   │
                │  /v1/...    │      │ (+ raw JSONB)│
                └──────┬──────┘      └──────▲───────┘
                       │ enqueue            │
                ┌──────▼──────┐             │
                │   Redis     │             │
                └──────┬──────┘             │
                ┌──────▼──────────────────────┴──┐
                │  Worker (Celery)               │
                │  pipeline steps ──▶ providers: │
                │   • Google Places API (New)    │
                │   • Google Business Profile API│
                │   • SERP provider (adapter)    │
                │   • Website crawler (httpx →   │
                │     Playwright fallback)       │
                │   • NLP (lang / sentiment /    │
                │     tags)                      │
                └────────────────────────────────┘
```

### Stack

| Concern | Choice | Why |
|---|---|---|
| API | **FastAPI** + Pydantic v2 | Typed request/response models match the brief's JSON shapes; OpenAPI docs for free |
| DB | **PostgreSQL 16** + SQLAlchemy 2 + Alembic | Relational tables from the brief; JSONB for raw provider responses |
| Jobs | **Celery + Redis** | Retries, chains/groups for the pipeline, per-step status → `partial_success` |
| HTTP | `httpx` (async) + `tenacity` retries | Provider calls and website fetches |
| HTML / schema | `selectolax` or BeautifulSoup, **`extruct`** (JSON-LD / microdata for `LocalBusiness` / `Service`) | Website NAP + services |
| JS rendering | **Playwright** (fallback only) | See section 2 |
| Matching | `rapidfuzz` (names), `phonenumbers` (E.164), `tldextract` (domains), `usaddress` / `libpostal` (addresses), haversine | Confidence score |
| Geocoding | Google Geocoding API | Website address → coordinates for pin-distance check |
| NLP | `lingua` (language) + LLM tagging pass (e.g. `claude-haiku-4-5`) with a fixed theme taxonomy and structured JSON output; transformer sentiment model as a no-LLM fallback | Multilingual tags, themes, service mentions |
| Reports | Jinja2 HTML → **WeasyPrint** PDF; CSV via stdlib | Export |
| Runtime | docker-compose: `api`, `worker`, `postgres`, `redis` | Replaces single Dockerfile |

---

## 4. Project layout

```
app/
  api/v1/            projects.py, jobs.py, rankings.py, competitors.py, gaps.py, report.py
  core/              config.py, db.py, logging.py
  models/            SQLAlchemy models (one per table in section 6)
  schemas/           Pydantic models (brief's JSON shapes)
  providers/
    places.py        Google Places API (New): text search, place details (field masks)
    gbp.py           Business Profile APIs (OAuth, managed locations)
    serp/base.py     SerpProvider interface: local_pack(), local_finder()
    serp/dataforseo.py   first concrete adapter
    geocoding.py
  services/
    website/         crawler.py (httpx → playwright), nap.py, services.py, schema.py
    matching.py      confidence scoring
    profile.py       normalise Place Details → gbp_profiles
    reviews.py       NLP pipeline + summaries
    keywords.py      generator
    rankings.py      run + visibility metrics
    competitors.py   discovery + metrics
    gaps.py          gap rules
    report.py        HTML/PDF/CSV
  workers/
    celery_app.py
    pipeline.py      orchestrates steps, records job status
migrations/
tests/               unit tests with recorded provider fixtures
docker-compose.yml
```

---

## 5. API (from brief)

```
POST /v1/projects                                   create project (website, name, address, service areas, keywords)
POST /v1/projects/{id}/discover-business            → job
POST /v1/projects/{id}/discover-business/select     manual pick when confidence < 0.85
POST /v1/projects/{id}/gbp-audit                    → job
POST /v1/projects/{id}/keywords/generate
POST /v1/projects/{id}/rankings/run                 → job
GET  /v1/projects/{id}/rankings
GET  /v1/projects/{id}/competitors
GET  /v1/projects/{id}/gaps
GET  /v1/projects/{id}/report?format=html|pdf|csv
POST /v1/jobs                                       full pipeline
GET  /v1/jobs/{job_id}                              status + per-step results/errors
```

The only addition to the brief is `discover-business/select`. It is needed to resolve the manual-review fallback.

### Job model
`audit_jobs` stores `status` (`queued | running | partial_success | completed | failed`) plus a `steps` JSONB holding per-step status, timings and errors. A required step failing (GBP not found) gives `failed`. An optional step failing (one SERP request, website crawl) gives `partial_success`.

---

## 6. Data model

These are the brief's tables. Every collected-data table also has `source`, `source_url_or_provider`, `collected_at`, `last_verified_at`, `raw_response_id` (FK to `data_sources`) and `confidence_score`.

- `projects` (id, name, website_url, input_name, input_address, service_areas JSONB, settings)
- `businesses` (id, place_id UNIQUE, name, domain, is_client)
- `business_locations` (business_id, lat, lng, formatted_address, geocoded_website_lat/lng)
- `gbp_profiles` (business_id, all profile fields from the brief, `map_pin_status`, `pin_vs_website_address_distance_meters`, nullable fields = "unavailable")
- `gbp_reviews` (review_id, business_id, author_name, rating, text, published_at, owner_reply, review_url, language, sentiment)
- `review_tags` (review_id, tag, theme, sentiment, mentioned_service_id)
- `services` (project_id, service_name, source, source_url, confidence)
- `keywords` (project_id, keyword, service_id, location_name, lat, lng, language, country, device, radius)
- `ranking_runs` (keyword_id, checked_at, provider, result_type, country, language, device, lat, lng, radius, raw_response_id)
- `ranking_results` (run_id, rank, place_id, business_name, address, primary_category, rating, review_count, website_url, maps_url, is_client_business)
- `competitors` (project_id, business_id, keyword_share, local_pack_count, reason)
- `competitor_metrics` (competitor_id, snapshot_at, rating, review_count, review_velocity_30d, categories, services)
- `gap_recommendations` (project_id, gap_type, client_value, competitor_pattern, recommendation, evidence)
- `audit_jobs` (id, project_id, type, status, steps JSONB, started_at, finished_at)
- `data_sources` (id, provider, endpoint, request_params, response JSONB, fetched_at, expires_at)

**Retention:** Google Maps Platform terms allow storing `place_id` indefinitely. Most other Places content can only be cached for a limited time (e.g. coordinates for up to 30 days). `data_sources.expires_at` plus a cleanup task enforces this. **Check the current terms before launch.**

---

## 7. Core algorithms

### 7.1 GBP match confidence
Candidates come from Places Text Search on `"{name} {address}"`, followed by Place Details per candidate (field mask limited to the fields needed).

| Signal | Weight | Scoring |
|---|---|---|
| Name | 0.30 | `rapidfuzz.token_set_ratio` after stripping legal suffixes (Ltd, LLC…) |
| Address | 0.25 | Normalised components; postcode + street number exact = full credit |
| Phone | 0.20 | E.164 equal = 1, else 0; missing on either side means the weight is redistributed |
| Website domain | 0.15 | Registered domain equal (tldextract) |
| Distance | 0.10 | 1.0 at ≤ 100 m, linear decay to 0 at 2 km (website address geocoded vs GBP lat/lng) |

Weights of missing signals are redistributed proportionally. If the score is ≥ 0.85, the top candidate is auto-selected with its `match_reasons`. Otherwise status becomes `manual_review_required`, the top 5 candidates are returned, and the user picks one via `/select`.

### 7.2 Service list
The service list is merged from these sources, de-duplicated by normalised name or embedding similarity, and keeps every source:
1. GBP primary/secondary categories (`source=gbp_category`)
2. GBP services/products via the Business Profile API, for managed profiles only
3. Website: sitemap + nav links + H1/H2 on service-like pages + `Service`/`LocalBusiness` schema (`source=website_service_page|website_schema|website_nav`)
4. Review topics (`source=review_topic`, lower confidence)

Unavailable GBP data is stored as `null`, never as "not offered".

### 7.3 Review NLP
Per review the pipeline runs these steps:
1. Detect language.
2. Run one LLM call with a strict JSON schema returning `sentiment`, `tags[]`, `themes[]` (from a fixed taxonomy: service quality, staff, price/value, speed, cleanliness, communication, specific service) and `mentioned_services[]` (matched against the project's service list).
3. Batch the calls and cache them by review hash.

The results then feed monthly aggregates and `review_summary`. Public competitor reviews are labelled "sample (max 5 via Places API)" and never presented as all reviews.

### 7.4 Keywords
For each (service × service-area city) the generator produces `{service} in {city}`, `{service} {city}` and `{service} near me` (the last with the city's lat/lng as search origin). User-entered keywords are added, and phrases are de-duplicated. Defaults are language and country from the project and device `mobile`.

### 7.5 Visibility score
For each keyword: `points = pack_points(rank) + finder_points(rank)`.
- Local Pack: 1 → 100, 2 → 70, 3 → 50, absent → 0
- Local Finder: 1–3 → 40, 4–10 → 20, 11–20 → 10, absent → 0

The score is `Σ points / (140 × N_keywords) × 100`, which keeps it in the 0–100 range. The per-keyword maximum is 140.

Also computed:
- pack and finder appearance counts
- average ranks (over found results only)
- top-3, top-10 and not-found counts
- delta vs the previous run with the same search context

### 7.6 Competitors
Across the latest ranking run, a business (by `place_id`, excluding the client) is a competitor if:
- it appears for at least 20% of keywords in either result type, **or**
- it appears in the Local Pack for at least 3 keywords.

Competitor profiles are then fetched via Place Details. Review velocity is the change in review count between snapshots per 30 days. It needs at least 2 audits; until then it is `null`.

### 7.7 Gaps
- **Category:** categories held by at least 50% of competitors that the client lacks. Phrased as "Review whether X is an accurate and eligible category…".
- **Service:** competitor services or keyword themes missing from the client's service list.
- **Review:** client review count or rating below the competitor median; topics competitors are praised for that are absent from or negative in client reviews.
- **Ranking:** keywords where competitors are in the Local Pack and the client is absent.

There is no AI-generated recommendation text in v1 (phase 2). v1 uses templated, evidence-backed wording only.

---

## 8. Delivery phases (v1)

> Superseded by the detailed, status-tracked phases in [ROADMAP.md](ROADMAP.md) (Phases 0–11). The table below is kept as a summary.

| # | Milestone | Deliverables | Done when |
|---|---|---|---|
| 0 | **Foundation** | Remove Selenium/Flask; FastAPI skeleton, docker-compose (api/worker/postgres/redis), Alembic, config, health check, CI with lint + tests | `docker compose up` serves `/docs` |
| 1 | **Website extraction** | httpx crawler (robots.txt, sitemap, depth/page limits), Playwright fallback, NAP + schema + social links (reuse current code), service-page detection | Given a URL, returns NAP + services with sources |
| 2 | **GBP discovery + audit** | Places provider with field masks, matching score, manual-select endpoint, profile normalisation, geocode + pin distance | 0.97-style output from the brief; < 0.85 returns candidates |
| 3 | **Reviews + NLP** | Places reviews (public) + GBP API reviews (managed, OAuth), tagging pipeline, summary + monthly aggregates, attribution fields | `review_summary` JSON matches the brief |
| 4 | **Keywords + rankings** | Generator, SERP provider interface + first adapter, ranking runs with full context, visibility metrics + deltas | `GET /rankings` returns per-keyword ranks + score |
| 5 | **Competitors + gaps** | Discovery rule, competitor snapshots, 4 gap types | `GET /competitors` and `/gaps` populated |
| 6 | **Jobs + report** | Full pipeline job with `partial_success`, HTML/PDF/CSV report with source attribution and Google review links | One `POST /v1/jobs` produces a downloadable report |

Phase 2 (out of v1 scope): geo-grid ranks, review-response monitoring, GBP post monitoring, citation audit, backlinks, AI recommendations, scheduled audits.

---

## 9. Testing
- Unit tests for scoring, keyword generation, visibility math, competitor rule and gap rules (pure functions).
- Provider adapters are tested against recorded JSON fixtures, with no live calls in CI.
- One end-to-end test with all providers mocked asserts the job reaches `completed`, and reaches `partial_success` when one SERP call fails.

## 10. Inputs needed before building
1. **Google Cloud project + API key** with Places API (New) and Geocoding enabled, plus billing.
2. **SERP provider choice + credentials** (DataForSEO / SerpApi / BrightLocal).
3. Whether any **client GBP locations are owned/managed**. If so, Business Profile API access must be requested, plus OAuth setup.
4. **LLM provider/key** for review tagging, or confirmation to use the local-model fallback only.
5. Target countries/languages for v1.
