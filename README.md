# Local SEO Audit

Audit any local business's **Google Business Profile** in a few clicks:

- **Google profile check:** address, phone, website, hours, rating, review count, categories, map pin
- **Website check:** the business's own website compared with its Google profile (name, address, phone, map location)
- **Social links and services** found on the website
- **Reviews:** what customers praise and what they complain about
- **Correct listing:** confirms the right Google listing was found, with a confidence score

The app runs **on your own computer** and uses **free tiers** of Google and SerpApi, with built-in limits so you are never charged.

> **How long does setup take?** About 30 minutes the first time, mostly waiting for downloads. No programming knowledge needed.

---

## What you need

| | |
|---|---|
| A computer | **Windows 10/11** (64-bit) or a **Mac** with a recent macOS (one of the last three versions), with at least **8 GB RAM** and **5 GB free disk space** |
| Internet | To download the app and to look up businesses |
| A Google account | To get a free Google Maps key. Google asks for a payment card, but the app stays inside the free monthly allowance, so **you are not charged**. |
| An email address | For a free SerpApi account (250 free searches per month, no card needed) |

---

## Step 1: Install Docker Desktop (one time)

Docker is a free program that runs the app in the background, a bit like an app store for server programs.

1. Go to **https://www.docker.com/products/docker-desktop/**
2. Click **Download** for your computer (Windows, Mac with Apple chip, or Mac with Intel chip).
3. Open the downloaded file and follow the installer. Keep all default options.
4. **Restart your computer** when asked.
5. Open **Docker Desktop** from the Start menu (Windows) or Applications (Mac).
   - Accept the terms. You can skip signing in.
   - Wait until the bottom-left corner shows **"Engine running"** (a green light).

> **Windows:** if Docker says it needs **WSL**, click the button it shows to install it, then restart again.

---

## Step 2: Download the app

1. Download the app as a ZIP file:
   **https://github.com/Gautam4424/Google-business-listing/archive/refs/heads/feature/local-seo-audit-v1.zip**
2. Find the ZIP in your **Downloads** folder.
   - **Windows:** right-click it, choose **Extract All…**, then **Extract**.
   - **Mac:** double-click it.
3. Move the extracted folder somewhere easy to find, for example **Documents**.

You should see files like `start.bat`, `start.command` and `README.md` inside the folder.

---

## Step 3: Get your Google key (one time, about 10 minutes)

1. Go to **https://console.cloud.google.com** and sign in with your Google account.
2. **Create a project:**
   - Click the project selector at the top (it may say "Select a project").
   - Click **New Project**, name it `local-seo-audit`, then click **Create**.
   - Make sure it is selected at the top.
3. **Add billing** (needed even for free use):
   - Open the menu ☰, then **Billing**, then **Link a billing account**, then **Create billing account**.
   - Enter your details and card.
   - New accounts usually get free trial credit too.
4. **Turn on the Places API:**
   - Open the menu ☰, then **APIs & Services**, then **Library**.
   - Search **"Places API (New)"**, open it, and click **Enable**.
   - Make sure it says **(New)**.
   - *Optional:* also enable **"Geocoding API"**. It is used to place a website's address on the map.
5. **Create the key:**
   - Open the menu ☰, then **APIs & Services**, then **Credentials**.
   - Click **+ Create credentials**, then **API key**.
   - **Copy** the key. It starts with `AIza…`.
6. **Recommended, to protect the key:**
   - Click the key's name.
   - Under **API restrictions**, choose **Restrict key** and tick **Places API (New)**, plus **Geocoding API** if you enabled it.
   - Click **Save**.
7. **Recommended, as a safety net:**
   - Open the menu ☰, then **Billing**, then **Budgets & alerts**, then **Create budget**.
   - Set the amount to **$1** so Google emails you if anything is ever charged.

Keep the key somewhere safe for Step 5. **Never share it publicly.**

---

## Step 4: Get your SerpApi key (one time, about 3 minutes)

SerpApi is used for ranking checks and, optionally, the top 10 reviews.

1. Sign up at **https://serpapi.com/users/sign_up** and confirm your email.
2. Choose the **Free Plan** (250 searches per month).
3. Open **https://serpapi.com/manage-api-key** and **copy** "Your Private API Key".
   - It is a long code of about 64 characters. Copy all of it.

> The app also works without SerpApi; you just won't have ranking checks or the top-10 reviews.

---

## Step 5: Start the app

Make sure **Docker Desktop is open** (Step 1).

### Windows
1. Open the app folder and **double-click `start.bat`**.
   - If Windows shows *"Windows protected your PC"*, click **More info**, then **Run anyway**.
2. **The first time only**, Notepad opens a settings file:
   - Paste your Google key right after `GOOGLE_API_KEY=`
   - Paste your SerpApi key right after `SERPAPI_KEY=`
   - Example: `GOOGLE_API_KEY=AIzaSyA...your key...`, with no spaces and no quotes
   - **Save** (Ctrl+S) and **close Notepad**.
3. Wait.
   - The **first start takes 5–15 minutes** while everything downloads and builds.
   - Later starts take under a minute.
4. Your browser opens **http://localhost:8000** automatically. 🎉

### Mac
1. Open the app folder and **double-click `start.command`**.
   - If the Mac says it *"can't be opened because it is from an unidentified developer"*, **right-click** (or Control-click) `start.command`, choose **Open**, then **Open** again.
2. **The first time only**, TextEdit opens a settings file:
   - Paste your Google key after `GOOGLE_API_KEY=` and your SerpApi key after `SERPAPI_KEY=`.
   - **Save** (⌘S) and close TextEdit.
   - Go back to the black Terminal window and press **Enter**.
3. Wait.
   - The first start takes 5–15 minutes.
   - The browser then opens **http://localhost:8000**.

> If double-clicking does not work on Mac, open **Terminal**, type `bash ` (with a space), drag the `scripts/start.sh` file into the window, and press Enter.

### Opening the app later
While Docker Desktop is running, just go to **http://localhost:8000** in your browser. Bookmark it.

---

## How to use it

1. Click **Projects**, then **+ New project**.
2. In **Quick fill**, paste the business name and address in one line, for example:
   `Astaneh Construction 3080 Yonge St Ste 6060, Toronto, ON M4N 1S1, Canada`
   Then press **Enter**. All fields fill in automatically from Google.
3. Check the fields, then click **Create project**. The audit **starts automatically** and takes about 30 seconds.
4. Open the project to see the results:
   - **Google listing match:** how sure the app is that it found the right listing, and why
   - **Business profile:** everything Google shows
   - **Website vs Google:** ✓ / ✗ for name, phone and address, plus how far the map pin is from the website's address
   - **Social profiles** and **Offerings** (services) found on the website
   - **Top reviews** with **Customers praise** and **Customers complain about**
5. If the app is not sure which business is correct (for example a chain with many branches), it shows **"Choose the right business"**. Click **This is the business** on the right one.

**Other buttons:**
- **Re-run audit:** refreshes the data.
- **Read website only:** checks only the website and uses no Google credits.
- **Load top 10 reviews:** uses 2 SerpApi credits.
- **Re-analyse:** free.

The **Overview** page shows how much of each free allowance you have used this month.

---

## Stopping the app

- **Windows:** double-click **`stop.bat`**.
- **Mac:** double-click **`stop.command`**.

Your projects and results are **kept**. Start again with `start.bat` / `start.command`.

Quitting Docker Desktop also stops the app.

---

## Costs and free limits

| Service | Free per month | What uses it |
|---|---|---|
| Google, Place Details | about 900 | 1 per audit |
| Google, business search | about 900 | 1 per new project (repeats within 7 days are free) |
| Google, Geocoding | about 9,000 | only for websites without map coordinates |
| SerpApi | 250 searches | **only** the optional "Load top 10 reviews" (2 per click), plus ranking checks in a future version |
| Reading websites, review analysis | unlimited | runs on your computer |

The app **stops itself before any free limit is reached**, so you cannot be charged by accident. Limits reset every month.

---

## Troubleshooting

| What you see | What to do |
|---|---|
| *"Docker Desktop is not installed"* | Do Step 1, restart the computer, then try again. |
| *"Docker Desktop is not running"* | Open Docker Desktop and wait for **"Engine running"**, then try again. |
| *"GOOGLE_API_KEY is empty"* | Open the file named `.env` in the app folder with Notepad or TextEdit, paste your key after `GOOGLE_API_KEY=`, save, and start again. *(On Mac, press ⌘⇧. in Finder to show hidden files like `.env`.)* |
| On the **Overview** page, **Run diagnostic** shows a red key | The key was copied incompletely or the API is not enabled. Re-copy it into `.env`, then run **start** again. For Google, check that **Places API (New)** is enabled (Step 3). |
| *"port is already allocated"* / port 8000 in use | Another program uses port 8000. Close it or restart the computer, then start again. |
| The first start seems stuck | The first build downloads about 1–2 GB. Wait up to 15 minutes on slower internet. |
| Browser shows *"This site can't be reached"* | Docker Desktop is not running, or the app is still starting. Wait a minute, or run **start** again. |
| "Map pin vs site: Not available" | The website has no map coordinates. Enable the **Geocoding API** on your Google key (Step 3), **or** in `.env` set `NOMINATIM_USER_AGENT=local-seo-audit/0.1 (your-email@example.com)` with your real email (free OpenStreetMap service). Then run **start** again. |
| Something else | In Docker Desktop, open **Containers → local-seo-audit** to see each part's logs. |

---

## Updating to a new version

1. Run **stop**.
2. Download the new ZIP (Step 2) and extract it.
3. **Copy your `.env` file** from the old folder into the new folder. It holds your keys. On Mac, show hidden files with ⌘⇧. in Finder.
4. Run **start** in the new folder. Your projects are kept, because the data is stored by Docker, not in the folder.

---

## Keep it private

- **Where it runs:** on your computer at `http://localhost:8000`. Only you can open it.
- **The `.env` file contains your keys.** Do not share it, email it, or upload it anywhere.
- **No login yet:** do **not** put the app on a public server or open it to the internet. That will come in a later version.

---

## For developers

Technical documentation (architecture, API endpoints, tests) is in [docs/DEVELOPERS.md](docs/DEVELOPERS.md). Plans and progress are in [docs/ROADMAP.md](docs/ROADMAP.md), and API keys and free tiers in detail are in [docs/API_KEYS_AND_FREE_TIERS.md](docs/API_KEYS_AND_FREE_TIERS.md).
