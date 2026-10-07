# Local SEO Audit API — developer guide

For deploying on an Ubuntu server with Docker, see the main [README](../README.md).

Audits a local business's Google Business Profile (GBP), tracks Local Pack / Local Finder rankings for service + location keywords, and compares the business against competitors. It uses only official or licensed data sources (Google Places API (New), Business Profile API, SerpApi) and runs within their free tiers.

- Plan: [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)
- Phases and progress: [ROADMAP.md](ROADMAP.md)
- API keys and free resources: [API_KEYS_AND_FREE_TIERS.md](API_KEYS_AND_FREE_TIERS.md)

## Quick start

Requires Docker Engine with the Compose plugin (see the README for Ubuntu installation).

```bash
cp .env.example .env        # then fill in GOOGLE_API_KEY, SERPAPI_KEY, POSTGRES_PASSWORD (openssl rand -hex 24)
docker compose up -d --build
```

- **Web UI: http://localhost:8000** (overview, free-tier usage, projects, jobs with live progress)
- API docs: http://localhost:8000/docs
- Health: http://localhost:8000/health

Database migrations run automatically when the `api` container starts.

## Check your setup

```bash
# Runs: database check, 1 Google Places Text Search call, SerpApi account check (no search used)
curl -X POST localhost:8000/v1/jobs -H "Content-Type: application/json" -d '{"job_type":"diagnostic"}'
curl localhost:8000/v1/jobs/<job id>
curl localhost:8000/v1/usage    # free-tier usage this month
```

## Endpoints (Phase 1)

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | API + database status |
| POST | `/v1/projects` | Create an audit project (business name, address, website, country, service areas, keywords, optional `place_id`) |
| GET | `/v1/projects`, `/v1/projects/{id}` | List / get projects |
| POST | `/v1/lookup/business` | Paste one line (name + address) and get all project fields + Google `place_id` (Google Places; local parser fallback; cached 7 days) |
| POST | `/v1/projects/{id}/discover-business` | Find & verify the Google listing: read website → search → score candidates (≥ 0.85 auto-select, else manual review); `{"then_audit": true}` starts the audit |
| GET | `/v1/projects/{id}/discover-business` | Match result: `place_id`, `match_confidence`, `match_reasons`, `manual_review_required`, candidates |
| POST | `/v1/projects/{id}/discover-business/select` | Choose a candidate (`place_id`), optionally start the audit |
| POST | `/v1/projects/{id}/gbp-audit` | Run the GBP audit: Google profile, reviews (Google's 5, free; body `{"top10_reviews": true}` = top 10 + owner replies via SerpApi, 2 credits), website NAP + social links + offerings |
| POST | `/v1/projects/{id}/website-discovery` | Read only the website: NAP + coordinates, social links, offerings (no Google profile calls) |
| GET | `/v1/projects/{id}/profile` | Latest results: Google profile, reviews, website NAP, Google-vs-website NAP check, pin distance, offerings |
| POST | `/v1/projects/{id}/reviews/analyze` | Re-run review sentiment, tags, themes, mentioned services (local, no API calls) |
| GET | `/v1/projects/{id}/reviews` | Reviews with sentiment + tags, and `review_summary` (brief Step 5) |
| POST | `/v1/jobs` | Start a background job (`queued → running → completed / partial_success / failed`) |
| GET/POST | `/v1/projects/{id}/services`, `POST …/services/refresh`, `PATCH …/services/{sid}` | Unified service list (tick core services) |
| PUT | `/v1/projects/{id}/service-areas` | Cities/suburbs served (coordinates looked up) |
| POST | `/v1/projects/{id}/keywords/generate` | Brief §2 keyword generator (no credits used) |
| GET/POST | `/v1/projects/{id}/keywords`, `PATCH/DELETE …/keywords/{kid}` | List, add, switch on/off (capped by `KEYWORD_CAP`), delete |
| GET | `/v1/projects/{id}/rankings/estimate` | SerpApi searches a check needs (cached = free) vs real balance + renewal date |
| POST | `/v1/projects/{id}/rankings/run` | Local Pack + Local Finder check for active keywords (`{"mode": "full"\|"maps_only", "force": false}`) |
| GET | `/v1/projects/{id}/rankings` | Latest check: visibility score, counts, per-keyword ranks, change, history, top businesses |
| GET | `/v1/projects/{id}/competitors` | Competitors from the latest ranking check (brief rule), side by side with the client |
| POST | `/v1/projects/{id}/competitors/analyze` | Re-run competitors + gaps on the latest check (0 SerpApi; ≤ `COMPETITOR_MAX` cached Place Details). Also runs automatically after every ranking check |
| DELETE | `/v1/projects/{id}` | Delete the project and all its data (409 while one of its jobs is queued/running) |
| GET | `/v1/settings` | Settings & status: masked keys, every limit with its `.env` name and usage, resets, worker status, last clean-up |
| GET | `/v1/settings/editable` | Settings that can be changed in the app (grouped): value, `.env` value, source (`app`/`.env`), limits, change history |
| PATCH | `/v1/settings` | `{"values": {"KEYWORD_CAP": 8}, "accept_charges": false}`: saved in `app_settings`, overrides `.env` within ~5 s in api and worker; 422 invalid, 409 above the free tier without `accept_charges` |
| DELETE | `/v1/settings/{KEY}` | Back to the `.env` value |
| POST | `/v1/jobs` `{"job_type":"retention_cleanup"}` | Run the 30-day clean-up now (it also runs nightly from the worker) |
| POST | `/v1/projects/{id}/full-audit` | Everything in one job: audit → keywords → rankings → competitors & gaps → report (`{"rankings": true, "mode": "full"\|"maps_only"}`); 422 up front when keywords exist and credits are short |
| GET | `/v1/projects/{id}/report` | `?format=html` (default), `pdf` (A4, headless Chromium), `csv` (zip of 7 files, or one with `&section=gaps`), `json`; built from stored data, no API calls |
| GET | `/v1/projects/{id}/gaps` | Category, service, review, review-topic and ranking gaps (brief §3 shape + `title`, `priority`, `evidence`) |
| GET | `/v1/jobs` | List jobs, newest first (`?project_id=`, `?status=`) |
| GET | `/v1/jobs/{id}` | Job status with per-step results and errors |
| GET | `/v1/usage` | Free-tier quota usage per billable API |

## Services

| Container | Role |
|---|---|
| `api` | FastAPI on port 8000 |
| `worker` | Celery worker running jobs |
| `postgres` | PostgreSQL 16 (data in the `pgdata` volume) |
| `redis` | Job queue |

## Free-tier guard

Every billable call is counted in the `api_usage` table and refused before the limits set in `.env` (`QUOTA_*`). Raw provider responses are stored in `data_sources` (provenance + cache) and Google data expires after `GOOGLE_DATA_TTL_DAYS`.

## Development

```bash
docker compose run --rm --no-deps api pytest          # tests (SQLite, no real API calls)
docker compose run --rm --no-deps api ruff check .    # lint
# new migration after changing app/models:
docker compose run --rm api alembic revision --autogenerate -m "describe change"
```

## Layout

```
app/
  api/v1/        REST endpoints
  core/          settings, database, secret redaction
  models/        SQLAlchemy models (16 tables)
  providers/     Google Places, SerpApi clients
  schemas/       request/response models
  services/      quota guard, provider cache
  static/        web UI (plain HTML/CSS/JS, served at /ui/)
  workers/       Celery app, pipeline runner, job definitions
migrations/      Alembic
tests/
docs/            plan, roadmap, API keys guide, this file
```
