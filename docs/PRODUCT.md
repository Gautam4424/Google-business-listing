# Local SEO Audit: what the app does

An **in-house** tool (no login) that audits a local business on Google and shows where it stands against its competitors. It replaces the old Selenium scraper described in the former `project_documentation.md`. Google is never scraped: the data comes from the **Google Places API** and **SerpApi**, both on their free tiers.

- How to install and run it: [README](../README.md)
- API endpoints and code layout: [DEVELOPERS.md](DEVELOPERS.md)
- Progress against the brief: [ROADMAP.md](ROADMAP.md)

---

## The flow

The project page follows the same four steps. **Full audit** runs them all in one go, and **Report** turns the result into HTML, PDF or CSV.

| Step | What happens | Data from | Cost |
|---|---|---|---|
| **Create project** | Paste one line (name + address) into Quick fill; the app finds the business on Google | Google Places search | 1 Google search (cached 7 days) |
| **1. Listing audit** | Confirms it is the right Google listing (match score + reasons) and reads the full profile: address, phone, website, hours, categories, rating, photos, attributes, map pin | Google Places details | 1 Google lookup |
| | Reads the business's own website: name, address, phone, social links, services; compares them with Google and measures how far the map pin is from the website address | The website itself | Free |
| | Analyses the reviews: sentiment, what customers praise and complain about, services mentioned | Google's 5 most relevant reviews (top 10 with owner replies via SerpApi, optional) | Free (2 SerpApi credits for top 10) |
| **2. Services & keywords** | Merges services from Google, the website and reviews; you tick the core ones and the areas served; keywords are generated ("{service} in {city}", "{service} near me"…); up to 10 are switched on | Stored data | Free |
| **3. Rankings** | For each keyword that is on: position in Google's **Local Pack** (the map box, top 3) and **Local Finder** (Maps list, top 20), searched from that area on a phone; **visibility score** 0–100 and change since last time | SerpApi | 2 SerpApi searches per keyword (1 in Maps-only mode); free re-check within 24 h |
| **4. Competitors & gaps** | Businesses appearing for ≥ 20% of keywords or in ≥ 3 Local Packs; compares categories, reviews, rating, review speed, services, rankings; lists gaps to **review** | Stored ranking results + Google lookups for competitors' review samples | 0 SerpApi; up to 10 Google lookups (cached 7 days) |
| **Report** | Summary, top priorities, every section above, and the sources | Stored data | Free |

**Visibility score:**
- Local Pack: #1 = 100 points, #2 = 70, #3 = 50.
- Local Finder: positions 1–3 = 40 points, 4–10 = 20, 11–20 = 10.
- Each keyword can score up to 140 points; the score is the average across keywords, shown out of 100.

**Gap wording:** the app never says "add this category". It says "Review whether *X* is an accurate and eligible category". It also notes when the business's own services support it. Adding categories or services the business doesn't offer is against Google's rules.

---

## Protecting the free tiers

- **Every billable call is counted.** It is refused before a monthly or daily limit is reached; the limits are in `.env` and shown on **Settings**.
- **SerpApi costs are shown before running.** Ranking checks and full audits show the cost and the real balance first, and are refused when there aren't enough searches.
- **One job per project at a time.** Double clicks never spend twice.
- **Failed calls are free.** They are retried once and never counted.
- **Competitors can't starve client audits.** Competitor lookups stop while 10 Google lookups are still left for client audits.
- **Limits are explained in plain words.** When a limit is reached, the app says which one, when it resets, and which setting raises it.

## Reliability and housekeeping

- **Partial failures keep the rest.** If one part of an audit fails (a search, a credit check), the rest is kept and the job ends as `partial_success`.
- **Interrupted jobs are marked.** Jobs cut off by a restart are marked "Interrupted: please run it again". A job stuck for 2 hours no longer blocks its project.
- **Nightly 30-day clean-up** (Google's terms):
  - Removed: raw Google/SerpApi responses, review texts and author names, and older profile snapshots.
  - Kept: scores, ranks, history, topics, gaps and place IDs.
- **Logs are capped,** and `DEBUG=false` by default. API keys are never logged and are always masked on screen.
- **Deleting a project** removes all its data. API usage counters are kept.

## What it does not do (yet)

- **No login.** It is meant to be reached only from the server, through an SSH tunnel.
- **No scheduled audits or geo-grid rank tracking.** These are the brief's phase-two items; they use many credits.
- **No owner-only Google Business Profile data** (all reviews, posts, insights). That needs Google's Business Profile API approval for listings you manage.
- **No automatic database backups.** Manual commands are in the README.
