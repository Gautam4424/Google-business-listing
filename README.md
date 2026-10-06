# Local SEO Audit API

Audits a local business's Google Business Profile (GBP), tracks Local Pack / Local Finder rankings for service + location keywords, and compares the business against competitors. It uses only official or licensed data sources (Google Places API (New), Business Profile API, SerpApi) and runs within their free tiers.

- Plan: [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)
- Phases and progress: [ROADMAP.md](ROADMAP.md)
- API keys and free resources: [IMPLEMENTATION_GUIDE.md](IMPLEMENTATION_GUIDE.md)
- Gaps vs the brief: [GAP_ANALYSIS.md](GAP_ANALYSIS.md)
- Old Selenium scraper (being replaced): [legacy/](legacy/)

## Quick start

Requires Docker Desktop.

```bash
cp .env.example .env        # then fill in GOOGLE_API_KEY, SERPAPI_KEY, POSTGRES_PASSWORD
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
legacy/          old Flask + Selenium scraper (reference only)
```
