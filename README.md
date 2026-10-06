# Local SEO Audit — deploy with Docker on Ubuntu

Audits a local business's **Google Business Profile**:
- **Google profile:** address, phone, website, hours, rating, categories, map pin
- **Website check:** the business's own website compared with Google (name, address, phone, map location), plus its social links and services
- **Reviews:** sentiment, plus what customers praise and complain about
- **Correct listing:** confirms the right Google listing with a confidence score

Everything runs in **Docker** on one **Ubuntu** server, using the **free tiers** of Google and SerpApi. Built-in limits stop the app before any free allowance is used up.

---

## Contents
1. [Requirements](#1-requirements)
2. [Install Docker](#2-install-docker)
3. [Get the code](#3-get-the-code)
4. [Get the API keys](#4-get-the-api-keys)
5. [Configure](#5-configure)
6. [Start](#6-start)
7. [Open the web app](#7-open-the-web-app)
8. [Check the setup](#8-check-the-setup)
9. [Operations: logs, stop, update, backup](#9-operations)
10. [Costs and free limits](#10-costs-and-free-limits)
11. [Troubleshooting](#11-troubleshooting)
12. [Security checklist](#12-security-checklist)

---

## 1. Requirements

| | Minimum | Recommended |
|---|---|---|
| OS | **Ubuntu 22.04 or 24.04 LTS**, 64-bit (amd64) | Ubuntu 24.04 LTS |
| CPU / RAM | 2 vCPU / 4 GB | 2–4 vCPU / 8 GB |
| Disk | 10 GB free | 20 GB |
| Network | Outbound internet (Google, SerpApi, business websites) | — |
| Access | A user with `sudo` | — |

You also need:
- a **Google Cloud** account (for the Places API key; a card is required, but usage stays within the free tier)
- optionally, a free **SerpApi** account

> arm64 servers should work, but they are untested.

---

## 2. Install Docker

These are the official Docker Engine and Compose plugin install commands for Ubuntu ([docs.docker.com](https://docs.docker.com/engine/install/ubuntu/)):

```bash
# Prerequisites
sudo apt-get update
sudo apt-get install -y ca-certificates curl git openssl

# Docker's apt repository
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# Docker Engine + Compose plugin
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Run docker without sudo (log out and back in afterwards)
sudo usermod -aG docker "$USER"
```

**Log out and back in**, then check:

```bash
docker --version
docker compose version
docker run --rm hello-world
```

Docker starts automatically on boot (`systemctl is-enabled docker` prints `enabled`).

---

## 3. Get the code

```bash
cd ~
git clone https://github.com/Gautam4424/Google-business-listing.git local-seo-audit
cd local-seo-audit
git checkout feature/local-seo-audit-v1   # until this branch is merged into main
```

---

## 4. Get the API keys

### Google Places API key (required)
1. Open **https://console.cloud.google.com** and create a project, for example `local-seo-audit`.
2. **Billing:** link a billing account. This is required even for free usage.
3. **APIs & Services, then Library:** enable **Places API (New)**. Make sure it says *(New)*.
   - *Optional:* also enable **Geocoding API**, which places a website's address on the map.
4. **APIs & Services, then Credentials, then Create credentials, then API key:** copy the key (`AIza…`).
5. **Restrict the key** (strongly recommended):
   - *API restrictions:* Places API (New), plus Geocoding API if enabled.
   - *Application restrictions:* **IP addresses**, with your server's public IP.
6. **Billing, then Budgets & alerts:** create a **$1** budget alert as a safety net.

### SerpApi key (optional)
Sign up at **https://serpapi.com** and choose the **Free Plan** (250 searches/month). Copy the key from **https://serpapi.com/manage-api-key**; it is about 64 characters. Without it, the app still works, but ranking checks and the optional top-10 reviews are unavailable.

More detail: [docs/API_KEYS_AND_FREE_TIERS.md](docs/API_KEYS_AND_FREE_TIERS.md).

---

## 5. Configure

Create the settings file from the template, generate a database password, and protect the file:

```bash
cp .env.example .env
sed -i "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$(openssl rand -hex 24)/" .env
chmod 600 .env
nano .env          # paste your keys, then Ctrl+O, Enter, Ctrl+X
```

The settings in `.env`:

| Setting | Required | Meaning |
|---|---|---|
| `GOOGLE_API_KEY` | **yes** | Google Places API key |
| `SERPAPI_KEY` | no | SerpApi key (ranking checks, optional top-10 reviews) |
| `POSTGRES_PASSWORD` | **yes** | Database password (generated above) |
| `APP_BIND` | no | `127.0.0.1` (default): only reachable from the server itself. See [step 7](#7-open-the-web-app). |
| `APP_PORT` | no | Port on the server, default `8000` |
| `NOMINATIM_USER_AGENT` | no | Put your email here, e.g. `local-seo-audit/0.1 (you@example.com)`, to use free OpenStreetMap geocoding |
| `KEYWORD_CAP` | no | Max active keywords per project (default `10`). Each costs 2 SerpApi searches per ranking check. |
| `SERPAPI_REVIEWS_ENABLED` | no | `false` (default) uses Google's 5 free reviews; `true` uses 2 SerpApi credits per audit for the top 10 |
| `QUOTA_*` | no | Free-tier safety limits. The app refuses calls above these. |

> Never commit `.env` or share it; it holds your keys. It is already listed in `.gitignore`.

---

## 6. Start

```bash
docker compose up -d --build
```

- **The first build takes 5–15 minutes.** It downloads Python, PostgreSQL, Redis and a headless Chromium (about 1.5 GB).
- **Later starts take seconds.**
- **Database migrations** run automatically.

Check that it is running:

```bash
docker compose ps                      # api, worker, postgres, redis: all "Up", api "healthy"
curl -s http://127.0.0.1:8000/health   # {"status":"ok","database":"ok"}
```

| Container | Role |
|---|---|
| `api` | Web app and REST API (port 8000) |
| `worker` | Runs audits in the background |
| `postgres` | Database (data in the Docker volume `local-seo-audit_pgdata`) |
| `redis` | Job queue |

All containers restart automatically after a crash or a server reboot.

---

## 7. Open the web app

The app has **no login yet**, so by default it only listens on `127.0.0.1` (the server itself). Choose one of these ways to reach it:

### A. SSH tunnel (private, simplest; for one person)

From **your own computer**:
```bash
ssh -L 8000:127.0.0.1:8000 youruser@YOUR_SERVER_IP
```
Keep that window open and browse to **http://localhost:8000**.

### B. Domain + HTTPS + password with Caddy (recommended for a team)

1. Point a DNS **A record** (for example `audit.example.com`) to the server's IP.
2. Install Caddy ([caddyserver.com/docs/install](https://caddyserver.com/docs/install#debian-ubuntu-raspbian)):
   ```bash
   sudo apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl
   curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
   curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
   sudo apt-get update && sudo apt-get install -y caddy
   ```
3. Create a password hash:
   ```bash
   caddy hash-password        # type the password; copy the $2a$... output
   ```
4. Replace `/etc/caddy/Caddyfile` with:
   ```
   audit.example.com {
       basic_auth {
           admin PASTE_THE_HASH_HERE
       }
       reverse_proxy 127.0.0.1:8000
   }
   ```
5. Reload Caddy and open the firewall:
   ```bash
   sudo systemctl reload caddy
   sudo ufw allow OpenSSH && sudo ufw allow 80,443/tcp && sudo ufw enable
   ```
6. Browse to **https://audit.example.com** and log in as `admin`. Caddy gets the HTTPS certificate automatically.

### C. Direct on the server's IP (only on a private network or VPN)

Set `APP_BIND=0.0.0.0` in `.env`, then run `docker compose up -d`, and browse to `http://SERVER_IP:8000`.

> ⚠ **Docker-published ports bypass `ufw`.** On a public server this exposes the app, and your API credits, to anyone. Use A or B instead.

---

## 8. Check the setup

In the web app, open **Overview** and click **Run diagnostic**. The **Setup check** should show green for the **Database**, **Google Places API key** and **SerpApi key**.

The same check from the command line:
```bash
curl -s -X POST http://127.0.0.1:8000/v1/jobs -H "Content-Type: application/json" -d '{"job_type":"diagnostic"}'
# then: curl -s http://127.0.0.1:8000/v1/jobs/<id from the output>
```

**First audit:**
1. Go to **Projects**, then **+ New project**.
2. Paste one line into **Quick fill**, for example `Astaneh Construction 3080 Yonge St Ste 6060, Toronto, ON M4N 1S1, Canada`, and press **Enter**.
3. Click **Create project**. The audit runs by itself (about 30 s).
4. Scroll to **Services & keywords**: tick the core services, add the areas you serve, and adjust which keywords are on (the line above the table shows how many SerpApi credits a ranking check will use).

API reference: **http://localhost:8000/docs** (through your tunnel or proxy).

---

## 9. Operations

Run these from the app folder (`cd ~/local-seo-audit`):

| Task | Command |
|---|---|
| Status | `docker compose ps` |
| Live logs | `docker compose logs -f api worker` |
| Stop (data kept) | `docker compose down` |
| Start | `docker compose up -d` |
| Apply `.env` changes | `docker compose up -d` (recreates the containers that changed) |
| Update to the latest code | `git pull && docker compose up -d --build` (migrations run automatically) |
| Free disk after updates | `docker image prune -f` |

**Backup and restore the database:**
```bash
# backup
docker compose exec -T postgres pg_dump -U seo -d seo | gzip > backup-$(date +%F).sql.gz
# restore (into a running stack)
gunzip -c backup-YYYY-MM-DD.sql.gz | docker compose exec -T postgres psql -U seo -d seo
```

A daily backup at 03:00, kept for 14 days. Add it with `crontab -e`:
```
0 3 * * * cd ~/local-seo-audit && docker compose exec -T postgres pg_dump -U seo -d seo | gzip > ~/backups/seo-$(date +\%F).sql.gz && find ~/backups -name 'seo-*.sql.gz' -mtime +14 -delete
```
Run `mkdir -p ~/backups` first.

**Remove everything, including all data:** `docker compose down -v`. ⚠ This cannot be undone.

---

## 10. Costs and free limits

| Service | Free per month | Used by |
|---|---|---|
| Google Places, Place Details | ~900 (app limit) | 1 per audit |
| Google Places, business search | ~900 (app limit) | 1 per new project (cached 7 days) |
| Google Geocoding | ~9,000 | only for websites without map coordinates |
| SerpApi | 250 searches (app stops at 240) | optional top-10 reviews (2 per audit); ranking checks in a later version |
| Website reading, review analysis | unlimited | runs on your server |

The **Overview** page shows this month's usage. Limits are in `.env` (`QUOTA_*`) and reset monthly.

---

## 11. Troubleshooting

| Symptom | Fix |
|---|---|
| `permission denied ... /var/run/docker.sock` | `sudo usermod -aG docker $USER`, then log out and back in |
| `port is already allocated` | Set another port in `.env`: `APP_PORT=8080`, then `docker compose up -d` |
| Build stops / `Killed` during build | Not enough RAM. Add swap: `sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile` |
| `api` stays `unhealthy` | `docker compose logs api`: usually a wrong `POSTGRES_PASSWORD` after the database was first created. Set back the original, or reset with `docker compose down -v` (deletes data). |
| Diagnostic: Google key red | Places API (New) not enabled, key restricted to the wrong APIs or IP, or billing not linked |
| Diagnostic: SerpApi red | Key incomplete (it is about 64 characters) or not verified |
| "Map pin vs site: Not available" | Enable the **Geocoding API** on your key, or set `NOMINATIM_USER_AGENT` with your email, then `docker compose up -d` |
| Caddy shows 502 | The app is not running: `docker compose ps`, `docker compose logs api` |
| Anything else | `docker compose logs --tail=200 api worker` |

---

## 12. Security checklist

- [ ] `.env` has `chmod 600` and is never committed or shared
- [ ] `APP_BIND=127.0.0.1`, with access through an SSH tunnel or Caddy with a password ([step 7](#7-open-the-web-app))
- [ ] Google key restricted to **Places API (New)** (+ Geocoding) and to the **server IP**
- [ ] `$1` billing alert set in Google Cloud
- [ ] `ufw` enabled (OpenSSH, 80, 443 only)
- [ ] Ubuntu security updates: `sudo apt-get update && sudo apt-get upgrade -y` (or enable `unattended-upgrades`)
- [ ] Regular database backups ([step 9](#9-operations))

---

## For developers

Architecture, API endpoints and tests are in [docs/DEVELOPERS.md](docs/DEVELOPERS.md). The plan and progress are in [docs/ROADMAP.md](docs/ROADMAP.md).
