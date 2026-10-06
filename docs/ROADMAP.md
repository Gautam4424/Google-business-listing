# Local SEO Audit API — Phased Roadmap

- **End goal:** "Local SEO Audit API — Product & Workflow Brief" (all sections, including the brief's phase-two features).
- **Constraint:** zero spend; free tiers only (Google Places free monthly usage, SerpApi 250 searches/month, local NLP models, self-hosted Docker).
- **Detail:** architecture and algorithms are in [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md); API keys and free tiers in [API_KEYS_AND_FREE_TIERS.md](API_KEYS_AND_FREE_TIERS.md).

Status legend:
- `[x]` done
- `[~]` partially done / exists in old code but must be redone
- `[ ]` not started

Effort estimates are rough, for one developer working with Claude, in working days.

---

## Progress overview

| Phase | Name | Brief section | Status | Progress | Effort |
|---|---|---|---|---|---|
| 0 | Discovery & setup | — | **Done** (Google + SerpApi keys verified) | 100% | — |
| 1 | Foundation (API, DB, jobs) | §4, §5 | **Done** | 100% | 4 d |
| 2 | Website extraction | §1, Step 4 | **Done** | 100% | — |
| 3 | GBP discovery & matching | §1 | **Done** | 100% | — |
| 4 | GBP profile audit | §1 | **Done** (`gbp_audit` job + pin distance) | 100% | — |
| 5 | Reviews & NLP | Step 5 | **Done** (GBP API for managed listings optional) | 100% | — |
| 6 | Service list & keyword generator | Step 4, §2 | Not started | 0% | 2 d |
| 7 | Rank tracking & visibility | §2 | Not started | 0% | 4 d |
| 8 | Competitors & gap analysis | §3 | Not started | 0% | 3 d |
| 9 | Full pipeline, reports & UI | §4, §7 | Partial (new UI shell done) | 20% | 3 d |
| 10 | Hardening & compliance | §6 | Not started | 0% | 3 d |
| **v1 total (0–10)** | | | | **≈ 60%** | **≈ 16 d left (~3 weeks)** |
| 11 | Advanced features (brief "phase two") | §7 | Not started | 0% | 10–15 d |

---

## Phase 0 — Discovery & setup

**Goal:** understand the old project, agree the plan, get free API access.

- [x] Clone repo, read `project_documentation.md`
- [x] Build and run the old app in Docker (`gbp-scraper` container, http://localhost:5000)
- [x] Remove DuckDuckGo social-profile fallback (latency) in the old scraper
- [x] Live test of old scraper (11 listings), bug list written
- [x] Gap analysis vs brief (since removed: superseded by this roadmap)
- [x] Architecture + algorithms (`IMPLEMENTATION_PLAN.md`)
- [x] Free-tier research (Places, GBP API, SerpApi, DataForSEO)
- [x] **You:** Google Cloud project + Places API (New) key, verified working on 2026-10-07 (diagnostic job found the Googleplex place_id)
- [x] **You:** SerpApi key (Free Plan, 250/month) verified 2026-10-07; diagnostic job fully green
- [ ] **You:** decide whether any GBP listings are owned/managed. If yes, apply for Business Profile API access (approval can take weeks, so apply early).
- [x] Layout: new code in `app/`; old Selenium scraper removed (still on the `main` branch history)

**Done when:** API keys are available in a local `.env` file (never committed).

---

## Phase 1 — Foundation

**Goal:** the skeleton every other phase plugs into.

- [x] Move old Flask/Selenium code to `legacy/`; remove Selenium from the new stack
- [x] `docker-compose.yml`: `api` (FastAPI), `worker` (Celery), `postgres`, `redis`
- [x] Config via `.env` (`GOOGLE_API_KEY`, `SERPAPI_KEY`, DB URL) with `.env.example`
- [x] SQLAlchemy models + Alembic migration for all 15 brief tables (+ `api_usage`)
- [x] Shared provenance columns: `source`, `source_url_or_provider`, `collected_at`, `last_verified_at`, `raw_response_id`, `confidence_score`
- [x] `data_sources` table storing raw provider responses (JSONB) + `expires_at`
- [x] Job framework: `audit_jobs` with `queued → running → partial_success / completed / failed` and per-step status
- [x] `POST /v1/jobs`, `GET /v1/jobs/{id}`, `POST /v1/projects`, `GET /health`
- [x] **Free-tier guard:** `api_usage` counter per provider/month; refuse calls above a configurable cap (e.g. Places Enterprise 900/1000, SerpApi 240/250)
- [x] Response cache (same request within N days returns the cached result, so quota isn't spent)
- [x] Test setup (pytest), lint (ruff), GitHub Actions CI
- [x] Extra: `diagnostic` job (checks DB + each API key), `GET /v1/usage`, `GET /v1/projects`, API keys redacted from errors/logs

**Done when:** `docker compose up` gives working `/docs`, a project can be created, and a dummy job moves through all statuses.

✅ **Verified 2026-10-07:** stack up, migration applied, project created, diagnostic job `queued → running → partial_success` (SerpApi key invalid), 19 tests passing, ruff clean.

---

## Phase 2 — Website extraction

**Goal:** the client's own website as a data source (NAP, services, schema, social links).

- [x] Fetch website + extract social links: `app/services/website.py` (9 platforms, share/post/intent links rejected, schema.org `sameAs`)
- [x] URL cleaning: `clean_website()` drops utm/gclid/fbclid; social URLs normalised
- [x] Crawler: robots.txt, public-address (SSRF) guard incl. redirects + cached DNS, page/size limits (homepage + contact + up to 4 service pages), **sitemap.xml + sitemap index**, **contact page discovery**, **Playwright/Chromium fallback** for JavaScript-only sites (every browser request guarded; images/fonts blocked)
- [x] NAP extraction (`app/services/nap.py`): JSON-LD LocalBusiness → microdata → `tel:` links → `<address>` → postcode text lines; name from schema / og:site_name / title; phones validated + E.164 via `phonenumbers`; each field records source, page and confidence
- [x] Schema extraction: JSON-LD services/products/offer catalogs + LocalBusiness NAP + geo; microdata `itemprop` (own parser instead of `extruct`, to keep the image small; RDFa not covered)
- [x] Service-page detection: menu links, service hub pages, **sitemap service URLs**, heading fallback; "Explore …" prefixes, "… | …" suffixes and zero-width characters stripped
- [x] Geocode website address → lat/lng (`app/services/geocode.py`): schema `geo` first, then OpenStreetMap Nominatim (needs contact email in `NOMINATIM_USER_AGENT`), then Google Geocoding (needs the Geocoding API allowed on the key); cached 30 days
- [x] Standalone `website_discovery` job + `POST /v1/projects/{id}/website-discovery`; also runs inside `gbp_audit`
- [x] Extra: NAP consistency Google vs website (name fuzzy match, phone E.164, address = postcode + normalised street) and **`pin_vs_website_address_distance_meters`** (Phase 4 item) on `business_locations`; UI "Website vs Google" card

**Done when:** given a URL, the API returns NAP + services + social links, each with a source.

✅ **Verified 2026-10-07** on 3 real sites (astanehconstruction.com, proximityplumbing.com.au, allaustralianplumbing.com.au): NAP from schema on all three, 4–5 social profiles each, 13–40 offerings, a real postcode inconsistency found for Astaneh (Google M4N 1S1 vs website M4N 3N1), pin distances 18 m and 1.6 m. 62 tests passing.

---

## Phase 3 — GBP discovery & matching

**Goal:** find the right GBP and its `place_id`, or ask a human.

- [x] **Quick fill (early):** `POST /v1/lookup/business` turns one pasted line into all project fields via Places Text Search; the user picks the candidate; `place_id` linked
- [x] Places Text Search (`name + address`, else name + first service area); the Enterprise field mask already returns phone/website/location, so **no extra Place Details per candidate**; cached 7 days
- [x] Candidate normalisation: phone E.164, domain, postcode, street key (abbreviations), street number, coordinates (`app/services/matching.py`)
- [x] Confidence score: name 0.30, address 0.25, phone 0.20, domain 0.15, distance 0.10; missing signals redistributed; < 0.5 evidence weight capped at 0.80 (never auto-selected on a name alone); street without a number = weak evidence
- [x] Reference data prefers the **business website** (independent evidence) over the user's input
- [x] `match_reasons` (✓) and `mismatch_reasons` (✗) per candidate
- [x] ≥ 0.85 and a clear winner → auto-select; otherwise `manual_review_required` with up to 5 candidates (also when several listings match equally well: branches or duplicates)
- [x] `POST /v1/projects/{id}/discover-business` (job: read website → match → optionally start the audit), `GET …/discover-business`, `POST …/discover-business/select`
- [x] `gbp_audit` never audits an unverified guess (fails with "Manual review required"); new `verify_match` step scores every linked listing (incl. Quick-fill picks) against the website
- [x] UI: "Google listing match" card (confidence + reasons), "Choose the right business" screen, "Find on Google & audit" for unlinked projects, match % on project cards
- [x] Tests: scoring, decisions, endpoints, job chaining, audit refusal (78 passing)

**Done when:** output matches the brief's example (`place_id`, `match_confidence`, `match_reasons`, `manual_review_required`).

✅ **Verified 2026-10-07:** Joe's Pizza found & auto-selected at 1.00 and audited automatically; ambiguous "Starbucks, Yonge St" → manual review with 5 candidates, selection in the UI → audit → verified at 0.82 with a low-confidence warning; existing projects verified: All Australian 1.00, OVO 1.00, Astaneh 0.85, Proximity 0.72 (website postcode differs from Google).

---

## Phase 4 — GBP profile audit

**Goal:** all brief profile fields, correct and normalised.

- [~] Name, category, rating, phone, website, hours, photo count (old scraper gets these, but by a disallowed method; redo via Places)
- [ ] Place Details with field mask: `id, displayName, formattedAddress, location, googleMapsUri, websiteUri, primaryType(DisplayName), types, internationalPhoneNumber, regularOpeningHours, currentOpeningHours, businessStatus, rating, userRatingCount, photos, accessibilityOptions` + service-option fields
- [ ] `review_count` from `userRatingCount` (fixes the old bug where every count was wrong)
- [ ] `map_pin_status` + `pin_vs_website_address_distance_meters`
- [ ] Structured `opening_hours` + separate `special_hours`
- [ ] `null` for unavailable fields (never "N/A" / "not offered")
- [ ] `last_checked_at`, provenance, raw response saved
- [ ] `POST /v1/projects/{id}/gbp-audit`

**Done when:** all 17 profile fields from the brief are populated or explicitly `null`, for both test businesses.

---

## Phase 5 — Reviews & NLP

**Goal:** reviews with tags, sentiment and a summary.

- [x] Review fetching: Google Places (max 5, free, default) with `review_id`, `author_name`, attribution URL, `rating`, `review_text`, ISO `published_at`, `review_url`, `language`; optional top 10 + `owner_reply` via SerpApi (2 credits, "Load top 10" button)
- [ ] Business Profile API reviews + `owner_reply` for **managed** listings (needs Google's API approval; not requested)
- [x] Language: Google's `languageCode`, else `langdetect` (deterministic seed) instead of `lingua` (smaller)
- [x] Sentiment: star rating blended with VADER for English text (rating only for other languages). Chosen over a multilingual transformer model to keep the image small and CPU-fast; can be swapped later
- [x] Tags + themes (`app/services/review_nlp.py`): local-business lexicon per sentence; inherent-polarity words keep their meaning unless negated ("not friendly" = negative, "never late" = no complaint); fixed taxonomy: service quality, staff, price/value, speed, cleanliness, communication, specific service; each tag stores its evidence sentence
- [x] `mentioned_services` matched to the project's service list (Google categories + website offerings)
- [x] Review-topic services (brief Step 4): "<thing> <service word>" phrases ("burst pipe repairs"), business name excluded → `services` with source `review_topic`
- [x] Monthly aggregation + `review_summary` (total_review_count, average_rating, sentiment_distribution, top_positive_topics, top_negative_topics, themes, monthly, sample note)
- [x] "Based on 5 of N reviews" labelling for public profiles
- [x] `analyze_reviews` step in `gbp_audit`; `review_analysis` job + `POST /v1/projects/{id}/reviews/analyze` (no API calls); `GET /v1/projects/{id}/reviews`
- [x] UI: insight panel (sentiment bar, customers praise / complain about, themes table, sample note), sentiment badge + coloured topic tags per review (hover = evidence sentence), "Re-analyse · free"

**Done when:** `review_summary` JSON matches the brief's example shape.

✅ **Verified 2026-10-07** on 6 real businesses (35 real reviews), 0 API credits: e.g. Proximity Plumbing praise = fast service, professional, recommendation, honest/reliable, good value; complaint = expensive (from a 2★ review); review-topic services such as "Burst pipe repairs", "Kitchen renovation". 91 tests passing.

---

## Phase 6 — Service list & keyword generator

**Goal:** a unified service list and keyword sets per location.

- [ ] Merge services from GBP categories (Phase 4) + website (Phase 2) + review topics (Phase 5) + user input; de-duplicate, keep every source
- [ ] Project service areas (city, lat/lng, country, language)
- [ ] Generator: `{service} in {city}`, `{service} {city}`, `{service} near me`
- [ ] User-entered keywords; de-duplication
- [ ] Store `keyword, service, location_name, latitude, longitude, language, country, device`
- [ ] `POST /v1/projects/{id}/keywords/generate`
- [ ] Keyword cap per project to protect the SerpApi quota (each keyword = 2 searches)

**Done when:** a plumber in Manchester project produces keywords like the brief's examples, each stored with full location settings.

---

## Phase 7 — Rank tracking & visibility

**Goal:** Local Pack + Local Finder positions and the visibility score.

- [ ] `SerpProvider` interface + SerpApi adapter (DataForSEO adapter later)
- [ ] Local Pack collection (Google Search, top 3)
- [ ] Local Finder / Maps collection (10–20 results)
- [ ] Every result: `rank, place_id, business_name, address, primary_category, rating, review_count, website_url, maps_url, is_client_business`
- [ ] `ranking_runs` with full search context (time, country, language, device, lat/lng, radius, provider, result type)
- [ ] Metrics: Pack/Finder rank per keyword, appearance counts, averages, top-3/top-10/not-found
- [ ] Visibility score: Pack 100/70/50; Finder 40/20/10; `Σ / (140 × N) × 100`
- [ ] Change since previous run (same search context)
- [ ] `POST /v1/projects/{id}/rankings/run`, `GET /v1/projects/{id}/rankings`

**Done when:** a rankings run returns per-keyword ranks + a 0–100 score, and a second run shows deltas.

---

## Phase 8 — Competitors & gap analysis

**Goal:** who the real competitors are and where the client falls short.

- [ ] Competitor rule: appears for ≥ 20% of keywords **or** in the Local Pack for ≥ 3 keywords
- [ ] Competitor profiles via the Phase 4 pipeline (watch the Places quota; reuse the cache)
- [ ] `competitor_metrics` snapshots: categories, review count, rating, review velocity (`null` until 2 snapshots), domain, services, Pack/Finder appearances, keyword overlap
- [ ] Gap rules: category, service, review count/rating, review-topic, ranking
- [ ] Recommendation wording always "review whether … is accurate and eligible"
- [ ] `GET /v1/projects/{id}/competitors`, `GET /v1/projects/{id}/gaps`

**Done when:** gap output matches the brief's example shape, with evidence attached.

---

## Phase 9 — Full pipeline, reports & UI

**Goal:** one click/one call runs the whole audit and produces a report.

- [x] Web UI shell (`app/static/`, served at http://localhost:8000): overview (health, stat tiles, free-tier usage meters, setup check from the latest diagnostic), projects (cards + New project dialog with validation), jobs (list, status filter, live job page with step timeline). Light/dark mode, phone layout. Verified in Chrome on 2026-10-07.
- [ ] Extend the UI as each phase lands (audit results, rankings, competitors, gaps, report download)
- [ ] Pipeline job: website → discover → profile → services/keywords → rankings → competitors → gaps → report
- [ ] `partial_success` when an optional step fails (e.g. one SERP call)
- [ ] HTML report (Jinja2) with source attribution + Google review links
- [ ] PDF export (WeasyPrint)
- [ ] CSV export (profile, rankings, competitors, gaps)
- [ ] `GET /v1/projects/{id}/report?format=html|pdf|csv`
- [~] Simple UI: create project ✅ → watch job progress ✅ → view/download report ⬜; manual GBP selection screen ⬜ (Phase 3)

**Done when:** a single `POST /v1/jobs` produces a downloadable report end to end.

---

## Phase 10 — Hardening & compliance

**Goal:** safe to show to real clients.

- [ ] Retention cleanup task (`data_sources.expires_at`; only `place_id` is stored indefinitely)
- [ ] Attribution checks in UI + report
- [ ] API-key auth for `/v1` endpoints; rate limiting
- [ ] Retry/backoff for providers; clear error messages when a quota is reached
- [ ] Structured logging; job failure details visible
- [ ] End-to-end tests with mocked providers (`completed` and `partial_success` paths)
- [x] Remove the old Selenium scraper (`legacy/`)
- [ ] Update README + replace `project_documentation.md`

**Done when:** CI is green, there are no secrets in the repo, and retention rules are enforced.

---

## Phase 11 — Advanced features (brief "phase two")

Each item is independent and can be picked in any order after v1. ⚠️ marks items that are expensive on free tiers.

- [ ] Scheduled weekly/monthly audits (Celery beat) ⚠️ uses quota every run
- [ ] Geo-grid rank tracking (e.g. 5×5 or 7×7 points per keyword) ⚠️ 25–49 searches per keyword
- [ ] Review-response monitoring (GBP API, managed listings)
- [ ] GBP post monitoring (GBP API, managed listings)
- [ ] Citation audit (NAP consistency across directories)
- [ ] Backlink analysis (needs a backlink data provider)
- [ ] AI-generated recommendations (LLM over gap data, still "review eligibility" framing)

---

## What you need to do next

1. Optional: set `NOMINATIM_USER_AGENT` (your email) or enable the Geocoding API, for map-pin distances on sites without coordinates.
2. Next build phases: 6 (keywords) → 7 (rankings) → 8 (competitors & gaps) → 9 (reports) → 10 (hardening).
