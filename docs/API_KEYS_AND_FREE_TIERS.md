# Implementation Guide — Plan Explained + Free Resources per Phase

This guide explains each phase in plain language, lists the **free** APIs and tools each phase uses, and gives step-by-step instructions to get every API key.

- Task checklists and progress: [ROADMAP.md](ROADMAP.md)
- Architecture and algorithms: [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)

> Free-tier limits and console menus change over time. Each step below links to the official page; check it if a screen looks different.

---

## Part A — How the system works

```
You (or a client) ──▶ API (FastAPI) ──▶ Job queue (Redis) ──▶ Worker (Celery)
                         │                                       │
                         ▼                                       ▼
                    PostgreSQL  ◀──────── results ──────── calls free APIs:
                    (all data +                              • Google Places API (New)
                     where it                                • Google Geocoding / OpenStreetMap Nominatim
                     came from)                              • SerpApi (rankings)
                                                             • Google Business Profile API (only your own listings)
                                                             • the client's website (direct crawl)
                                                             • local AI models (review sentiment/tags)
```

1. **The API** receives requests ("audit this business") and returns results.
2. Audits are slow (many API calls), so the API **puts a job in a queue** and immediately returns a job ID.
3. A **worker** picks up the job and runs the steps one by one: website → find GBP → profile → reviews → keywords → rankings → competitors → gaps → report.
4. Every piece of data is saved in **PostgreSQL**, together with *where it came from and when*, because the brief requires provenance.
5. You poll `GET /v1/jobs/{id}` and, when it finishes, download the report.

Everything runs in **Docker on one Ubuntu server**; no paid hosting services are needed.

---

## Part B — Phase by phase

### Phase 0 — Setup (70% done)
**What happens:** get the free API keys and decide the folder layout. Analysis and planning are already done.

| Free resource | Used for |
|---|---|
| Google Cloud account | Places API + Geocoding API keys (see C1) |
| SerpApi free account | Ranking data (see C2) |
| Google Business Profile API access (optional) | Full reviews for listings you own (see C3) |
| GitHub (free) | Code hosting + CI (2,000 free Action minutes/month on private repos, unlimited on public) |

---

### Phase 1 — Foundation
**What happens:** build the empty "house" every feature lives in. It contains:
- the API server
- the database with all 15 tables from the brief
- the job queue and worker
- one `docker compose up` command to start everything
- a **quota guard** that counts every paid-tier API call and stops before the free limit, so you can never be charged
- a **cache** so the same business is never fetched twice in a short period

| Free resource | Used for |
|---|---|
| Docker Engine + Compose plugin (Ubuntu) | Runs everything |
| PostgreSQL 16 (Docker image) | Database |
| Redis (Docker image) | Job queue |
| Python libs: FastAPI, Pydantic, SQLAlchemy, Alembic, Celery, pytest, ruff | API, DB, jobs, tests |
| **No external API** | — |

---

### Phase 2 — Website extraction
**What happens:** read the client's own website and pull out:
- business **name, address, phone** (NAP)
- the **services** they offer (from menus, page titles, service pages)
- structured data (schema.org `LocalBusiness` / `Service`)
- **social media links** (reused from the old code)
- the website address converted to map coordinates (used later to check the GBP pin)

Most sites are read with a fast plain HTTP request. Playwright (a real browser) is used only when a site needs JavaScript to show its content.

| Free resource | Used for | Free limit |
|---|---|---|
| httpx, selectolax/BeautifulSoup | Download + parse pages | Unlimited |
| Playwright | Fallback for JavaScript sites | Unlimited |
| extruct | Read schema.org data | Unlimited |
| phonenumbers, tldextract | Clean phones and domains | Unlimited |
| **OpenStreetMap Nominatim** (recommended first) | Address → coordinates | Free, max 1 request/second, needs a User-Agent; no key |
| **Google Geocoding API** (fallback) | Address → coordinates | 10,000 free/month (Essentials) |

---

### Phase 3 — Find & verify the GBP
**What happens:** search Google Places for "business name + address". Each candidate is compared with what the website says (name, address, phone, domain, distance) and scored from 0 to 1.
- Score **≥ 0.85**: the match is accepted automatically and its `place_id` saved.
- Score **< 0.85**: the top 5 candidates are returned and you pick the right one.

| Free resource | Used for | Free limit |
|---|---|---|
| **Places API (New) — Text Search** | Candidate businesses | ~5,000 free/month (Pro fields) |
| **Places API (New) — Place Details** | Phone/website of each candidate for scoring | 1,000–10,000 free/month depending on fields |
| rapidfuzz | Fuzzy name/address matching | Unlimited |

---

### Phase 4 — GBP profile audit
**What happens:** for the matched `place_id`, fetch the full profile once:
- name, address, coordinates, map link
- website, phone
- primary type and other types
- hours (normal + today's), open/closed status, business status
- rating and **correct review count**
- photos, accessibility, service options (delivery, dine-in…)

Anything Google doesn't return is stored as `null`, never "not offered".

| Free resource | Used for | Free limit |
|---|---|---|
| **Places API (New) — Place Details** (field mask) | All profile fields | Rating/hours/website fields fall in the Enterprise tier, **~1,000 free/month** |

> Tip: ask only for the fields you need (field mask). Google charges by the most expensive field requested.

---

### Phase 5 — Reviews & NLP
**What happens:**
1. Fetch reviews.
   - Public businesses: Google gives **max 5** reviews. That is Google's limit, not a cost issue.
   - Your own managed listings: the GBP API gives **all** reviews plus owner replies.
2. For each review, detect the language, sentiment (positive/neutral/negative), tags ("fast service") and themes (price, staff, speed…), and which of the client's services it mentions.
3. Build the summary: sentiment counts, top positive/negative topics, monthly trends.

All the AI runs **locally and free**: no AI API key, no cost.

| Free resource | Used for | Free limit |
|---|---|---|
| **Places API (New) — Place Details `reviews` field** | Up to 5 reviews per business | Enterprise + Atmosphere tier, ~1,000 free/month |
| **Google Business Profile API** (optional) | All reviews + owner replies for your own listings | Free, approval needed (C3) |
| lingua-language-detector | Language | Unlimited, local |
| Hugging Face model `cardiffnlp/twitter-xlm-roberta-base-sentiment` | Multilingual sentiment | Free download, no account |
| KeyBERT or YAKE | Tag/phrase extraction | Local |
| sentence-transformers (`all-MiniLM-L6-v2`) | Theme classification + service matching | Local |

---

### Phase 6 — Service list & keywords
**What happens:**
1. Merge services from GBP categories, the website and review topics into one list, each with its source.
2. Combine every service with every city the client serves to generate keywords: "emergency plumber in Manchester", "boiler repair Manchester", "plumber near me".

The number of keywords is capped per project because each keyword uses 2 SerpApi searches.

| Free resource | Used for | Free limit |
|---|---|---|
| Nominatim / Geocoding | City → lat/lng for each keyword | as Phase 2 |
| **No other external API** | — | — |

---

### Phase 7 — Rank tracking & visibility
**What happens:** for each keyword, ask SerpApi (a legal, licensed provider) for:
- the **Local Pack**: the 3 map results on normal Google Search
- the **Local Finder**: the longer list you get from "More places"

The rank of every business is saved, with the exact search settings (city, device, language, time). The visibility score (0–100) and the change since the last run are then calculated.

| Free resource | Used for | Free limit |
|---|---|---|
| **SerpApi — `engine=google`** (`local_results`) | Local Pack | Free plan 250 searches/month total |
| **SerpApi — `engine=google_local`** | Local Finder | (shares the 250) |
| DataForSEO (optional backup) | Same data | $1 trial credit + free Sandbox for testing |

> 250 searches ≈ 125 keywords per month (2 searches per keyword).

---

### Phase 8 — Competitors & gap analysis
**What happens:** from the ranking data, find businesses that keep showing up. A business is a competitor if it appears for ≥ 20% of keywords, or in the Local Pack ≥ 3 times. Their public profiles are then fetched and compared with the client's:
- categories, services
- review count, rating, review topics
- rankings

Gaps are listed with careful wording ("review whether this category is accurate for you", never "add it").

| Free resource | Used for | Free limit |
|---|---|---|
| SerpApi results (already collected in Phase 7) | Competitor list | No extra calls |
| **Places API (New) — Place Details** | Competitor profiles | Shares the ~1,000/month Enterprise allowance (cache helps) |

---

### Phase 9 — Full pipeline, reports & UI
**What happens:** one call (`POST /v1/jobs`) runs Phases 2–8 in order.
- If one optional step fails (e.g. one SerpApi call), the job ends as `partial_success` instead of failing.
- At the end it creates an **HTML report**, a **PDF** and **CSV** files, with sources and Google review links.
- A simple web page lets you start audits, watch progress and download reports.

| Free resource | Used for |
|---|---|
| Jinja2 | HTML report templates |
| WeasyPrint | HTML → PDF |
| Python `csv` | CSV export |
| **No external API** | — |

---

### Phase 10 — Hardening & compliance
**What happens:**
- delete Google data after the allowed caching period (only `place_id` is kept forever)
- show Google attribution
- protect your API with keys and rate limits
- add end-to-end tests
- remove the old scraper

| Free resource | Used for |
|---|---|
| Celery beat | Nightly cleanup task |
| GitHub Actions | CI tests |
| **No external API** | — |

---

### Phase 11 — Advanced (brief "phase two")

| Feature | Free resource | Free-tier reality |
|---|---|---|
| Scheduled audits | Celery beat | Each run uses quota |
| Geo-grid ranking | SerpApi | 1 keyword × 7×7 grid = 49 searches, so only practical with a paid plan |
| Review-response & post monitoring | GBP API | Free, own listings only |
| Citation audit | Your own crawler over directory sites | Free, slow to build |
| Backlinks | Ahrefs Webmaster Tools (own verified sites only) / Common Crawl | Limited |
| AI recommendations | Ollama + an open model (e.g. Llama / Qwen) running locally | Free, needs a decent PC (≥ 16 GB RAM) |

---

## Part C — How to get each API (step by step)

### C1. Google Places API (New) + Geocoding API

**Cost:** $0 if you stay within the free monthly usage. A card is required to create a billing account. New accounts usually also get a **$300 / 90-day** trial credit.

1. Go to **https://console.cloud.google.com** and sign in with your Google account.
2. **Create a project:** top bar → project dropdown → **New Project** → name it `local-seo-audit` → **Create**. Make sure it is selected.
3. **Set up billing:** menu ☰ → **Billing** → **Link a billing account** → **Create billing account** → enter country, card details → accept the free trial if offered.
4. **Enable the APIs:** menu ☰ → **APIs & Services → Library**:
   - search **"Places API (New)"** → open it → **Enable**
   - search **"Geocoding API"** → **Enable**

   Do *not* enable the old "Places API" (legacy). We use the New one.
5. **Create the key:** **APIs & Services → Credentials → + Create credentials → API key**. Copy it.
6. **Restrict the key** (important for security): click the key →
   - *API restrictions* → **Restrict key** → tick **Places API (New)** and **Geocoding API**
   - *Application restrictions* → **IP addresses** → add your public IP (optional while developing locally)
   - **Save**
7. **Budget alert (protects against charges):** ☰ → **Billing → Budgets & alerts → Create budget**.
   - Amount **$1**, alerts at 50%, 90% and 100%.
   - Optionally untick credits so you see real cost.
8. **Per-minute quota caps (safety net):** **APIs & Services → Places API (New) → Quotas & System Limits**. Places API (New) only has *per-minute* quotas (no per-day ones), so a daily cap of 30 can't be set here. Tick the row → **Edit quota** (pencil) → enter a lower value → **Submit**:
   - *GetPlace requests per minute* (Place Details): **5**
   - *SearchText requests per minute* (Text Search): **10**
   - Geocoding API does have a *requests per day* quota: set it to **100**.
   - The real daily/monthly hard stop is the app's **quota guard** (`QUOTA_*` in `.env`), which refuses calls before the free usage runs out.
9. Put the key in the project's `.env` file (never commit it):
   ```
   GOOGLE_API_KEY=AIza...
   ```
10. **Quick test** (on the server):
    ```bash
    curl -s -X POST https://places.googleapis.com/v1/places:searchText \
      -H "Content-Type: application/json" \
      -H "X-Goog-Api-Key: $GOOGLE_API_KEY" \
      -H "X-Goog-FieldMask: places.id,places.displayName,places.formattedAddress" \
      -d '{"textQuery":"Joe Pizza 1435 Broadway New York"}'
    ```

Official docs:
- Pricing / free usage: https://developers.google.com/maps/billing-and-pricing/pricing
- Places API (New): https://developers.google.com/maps/documentation/places/web-service/op-overview

---

### C2. SerpApi (rankings)

**Cost:** free plan, **250 searches/month**, no card.

1. Go to **https://serpapi.com/users/sign_up** and sign up (email or Google/GitHub).
2. Verify your email (SerpApi may also ask for phone verification).
3. Choose the **Free** plan.
4. Open **https://serpapi.com/manage-api-key** and copy your **API key**.
5. Add it to `.env`:
   ```
   SERPAPI_KEY=...
   ```
6. **Quick test** (free: checks the key and shows searches left):
   ```bash
   curl -s "https://serpapi.com/account.json?api_key=$SERPAPI_KEY"
   ```
7. Track usage at **https://serpapi.com/dashboard**. The app's quota guard will also stop at 240.

Engines we use:
- `google`: the `local_results` block is the Local Pack
- `google_local`: Local Finder

Docs: https://serpapi.com/search-api

---

### C3. Google Business Profile API (optional — only for listings you own/manage)

**Cost:** free. **Requires approval**, and only works for profiles your Google account manages. It is not needed for competitors.

Requirements (per Google):
- a **verified** Business Profile that has been **active for 60+ days**
- a website that represents the business
- a Google Cloud project (use the one from C1)

1. In Google Cloud (C1 project), note the **Project number**: ☰ → **Cloud overview → Dashboard**.
2. Apply for access using Google's **Business Profile API access request form**. The link is on https://developers.google.com/my-business/content/prereqs. Choose *Application for Basic API Access*, enter the project number and your business details.
3. Wait for approval (usually days to a few weeks). Before approval the API quota shows **0**.
4. After approval, in **APIs & Services → Library** enable:
   - *My Business Account Management API*
   - *My Business Business Information API*
   - *Google My Business API* (v4: reviews + owner replies)
5. **OAuth setup** (the API acts on behalf of the listing owner):
   - **APIs & Services → OAuth consent screen** → External → app name, your email → add scope `https://www.googleapis.com/auth/business.manage` → add yourself as a test user.
   - **Credentials → Create credentials → OAuth client ID** → *Web application* → redirect URI `http://localhost:8000/v1/oauth/google/callback`.
   - Copy the **Client ID** and **Client secret** into `.env`:
     ```
     GBP_OAUTH_CLIENT_ID=...
     GBP_OAUTH_CLIENT_SECRET=...
     ```
6. In the app (Phase 5), click **Connect Google Business Profile**, log in as the listing owner and allow access.

Docs: https://developers.google.com/my-business

---

### C4. DataForSEO (optional backup for rankings)

**Cost:** $1 free credit + free Sandbox (fake but correctly shaped data, unlimited). Real use later needs a $50 minimum top-up.

1. Register at **https://app.dataforseo.com/register** and verify your email.
2. Dashboard → **API Access**: copy the **API login** and **API password** (different from your account password).
3. Add to `.env`:
   ```
   DATAFORSEO_LOGIN=...
   DATAFORSEO_PASSWORD=...
   ```
4. For development, point the adapter at the Sandbox (`https://sandbox.dataforseo.com`) so tests cost nothing.

Docs: https://docs.dataforseo.com/v3/

---

### C5. OpenStreetMap Nominatim (free geocoding, no key)

No sign-up. Rules from the usage policy:
- max **1 request per second**
- send a real **User-Agent** with contact info
- cache results

The app does this automatically. Policy: https://operations.osmfoundation.org/policies/nominatim/

---

### C6. Hugging Face models (free local AI)

No account needed for public models. They download automatically the first time the worker runs (a few hundred MB, cached in a Docker volume). Optional: create a free account at https://huggingface.co and set `HF_TOKEN` for faster downloads.

---

## Part D — Free-tier budget at a glance

| Resource | Monthly free | Used by phases | Roughly enough for |
|---|---|---|---|
| Places Text Search (Pro) | ~5,000 | 3 | thousands of lookups |
| Places Details (Enterprise / + Atmosphere) | ~1,000 | 3, 4, 5, 8 | ~60 audits (client + ~10 competitors + candidates) |
| Geocoding (Essentials) | 10,000 | 2, 6 | plenty (Nominatim first) |
| SerpApi | 250 searches | 7, 8 | **~125 keywords → ~10 audits of 12 keywords**, the bottleneck |
| GBP API | free | 5, 11 | own listings only |
| Local AI, crawler, DB, Docker | unlimited | all | — |

---

## Part E — `.env` template (create in Phase 1)

```
# Google Cloud (C1)
GOOGLE_API_KEY=
# SerpApi (C2)
SERPAPI_KEY=
# Optional: GBP API OAuth (C3)
GBP_OAUTH_CLIENT_ID=
GBP_OAUTH_CLIENT_SECRET=
# Optional: DataForSEO (C4)
DATAFORSEO_LOGIN=
DATAFORSEO_PASSWORD=
# Free-tier guards
QUOTA_PLACES_DETAILS_MONTHLY=900
QUOTA_PLACES_TEXTSEARCH_MONTHLY=4500
QUOTA_SERPAPI_MONTHLY=240
NOMINATIM_USER_AGENT=local-seo-audit/0.1 (your-contact-email@example.com)
```
