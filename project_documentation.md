# Google Business Profile (GBP) Scraper

This project contains a web application and a Selenium-based scraper for extracting data from Google Business Profiles on Google Maps.

## Project Structure

- `src/app.py`: The Flask backend that serves the UI and exposes the `/api/scrape` endpoint.
- `src/scraper.py`: The Selenium-based scraper that extracts business info, social profiles, reviews, and products.
- `static/index.html`: The frontend UI.
- `data/data.json`: The database where scraped data is stored.
- `Dockerfile`: Environment definition for running the scraper headlessly with Chromium.
- `requirements.txt`: Python dependencies.

## Source Code

### `app.py`
`python
from flask import Flask, request, jsonify
import json
import os
from scraper import run_scraper

app = Flask(__name__, static_folder='.', static_url_path='')

@app.route('/')
def index():
    return app.send_static_file('index.html')

@app.route('/api/data', methods=['GET'])
def get_data():
    filename = "data.json"
    if os.path.exists(filename):
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return jsonify(data)
        except Exception:
            return jsonify([])
    return jsonify([])

@app.route('/api/scrape', methods=['POST'])
def scrape():
    data = request.json
    query = data.get('query')
    
    if not query:
        return jsonify({"error": "Query is required"}), 400
        
    print(f"Received scrape request for: {query}")
    
    # Run the scraper
    results = run_scraper(query)
    
    # Load existing data to append/upsert
    filename = "data.json"
    existing_data = []
    if os.path.exists(filename):
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                existing_data = json.load(f)
        except Exception:
            pass
            
    # Upsert logic based on Website or Company Name
    existing_dict = {item.get('Website', item.get('Company_Name')): idx for idx, item in enumerate(existing_data)}
    
    for item in results:
        key = item.get('Website')
        if key == 'N/A' or not key:
            key = item.get('Company_Name')
            
        if key in existing_dict:
            existing_data[existing_dict[key]] = item
        else:
            existing_data.append(item)
            existing_dict[key] = len(existing_data) - 1
            
    # Save back to json
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(existing_data, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print("Error saving data:", e)
        
    # Return ONLY the new results for this search (as requested)
    return jsonify({
        "message": "Scraping completed", 
        "new_results": len(results),
        "total_results": len(existing_data),
        "data": results
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)

`

### `scraper.py`
`python
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup
import time
import re
import requests
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse, unquote


# ── Helpers ────────────────────────────────────────────────────────────────

def clean_url(url):
    """Strip Google UTM/tracking params from a URL."""
    if not url or not url.startswith('http'):
        return url
    try:
        parsed = urlparse(url)
        params = parse_qs(parsed.query, keep_blank_values=True)
        for key in list(params.keys()):
            if key.lower().startswith('utm_'):
                del params[key]
        clean_query = urlencode({k: v[0] for k, v in params.items()})
        return urlunparse(parsed._replace(query=clean_query))
    except Exception:
        return url


def extract_aria_value(aria_label, prefix):
    """Strip a known prefix from an aria-label and return clean value."""
    if not aria_label:
        return "N/A"
    val = aria_label.strip().rstrip('\xa0 ')
    if val.lower().startswith(prefix.lower()):
        val = val[len(prefix):].strip(': ').strip()
    return val if val else "N/A"


def extract_social_profiles(website_url):
    """
    Fetch the business website and extract social media profile links.
    Returns a dictionary of platform -> url.
    """
    profiles = {}
    if not website_url or website_url == "N/A":
        return profiles
        
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        response = requests.get(website_url, headers=headers, timeout=5)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        domains = {
            'facebook': ['facebook.com/'],
            'instagram': ['instagram.com/'],
            'linkedin': ['linkedin.com/in/', 'linkedin.com/company/'],
            'twitter': ['twitter.com/', 'x.com/'],
            'youtube': ['youtube.com/', 'youtu.be/'],
            'tiktok': ['tiktok.com/']
        }
        
        for a in soup.find_all('a', href=True):
            href = a['href'].lower()
            for platform, matchers in domains.items():
                if platform not in profiles and any(m in href for m in matchers):
                    # Skip sharing links
                    if 'share' in href or 'post' in href or 'intent' in href:
                        continue
                    profiles[platform] = a['href']
    except Exception as e:
        print(f"Error fetching social profiles from {website_url}: {e}")
        
    return profiles


def fallback_social_profiles(name, address):
    """Fallback to searching DuckDuckGo for social profiles if not on website."""
    profiles = {}
    # Use city or short address for search
    location = address.split(',')[1].strip() if address != "N/A" and ',' in address else address
    if location == "N/A":
        location = ""
        
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    url = "https://html.duckduckgo.com/html/"
    
    for platform in ['facebook', 'instagram', 'linkedin', 'twitter']:
        query = f'"{name}" {location} site:{platform}.com'
        data = {'q': query}
        try:
            response = requests.post(url, data=data, headers=headers, timeout=5)
            soup = BeautifulSoup(response.text, 'html.parser')
            for a in soup.find_all('a', class_='result__url', href=True):
                href = a['href']
                if 'uddg=' in href:
                    href = unquote(href.split('uddg=')[1].split('&')[0])
                href_lower = href.lower()
                
                # Exclude directories, public listings, posts, and generic pages
                invalid_terms = ['share', 'post', 'dir/', '/public/', '/search/']
                if platform in href_lower and not any(term in href_lower for term in invalid_terms):
                    # For linkedin, ensure it's a company or in profile
                    if platform == 'linkedin' and '/company/' not in href_lower and '/in/' not in href_lower:
                        continue
                    profiles[platform] = href
                    break
            time.sleep(1) # Be nice to DDG
        except Exception as e:
            print(f"Fallback search error for {platform}: {e}")
            
    return profiles


def extract_products(soup):
    """
    Safely attempt to extract products from the GBP if they exist.
    """
    products = []
    try:
        products_header = None
        for heading in soup.find_all(['h2', 'h3', 'div']):
            if heading.text and heading.text.strip().lower() == 'products':
                products_header = heading
                break
                
        if products_header:
            container = products_header.find_parent('div')
            if container:
                # Find product cards - they are often buttons or links with aria-labels
                for item in container.find_all(['button', 'a']):
                    label = item.get('aria-label', '').strip()
                    # Filter out non-product interactive elements
                    if label and len(label) > 3 and label.lower() not in ['products', 'next', 'previous']:
                        img = item.find('img')
                        img_src = img['src'] if img else None
                        
                        price = None
                        if '$' in item.text:
                            match = re.search(r'\$\d+(?:,\d{3})*(?:\.\d{2})?', item.text)
                            if match:
                                price = match.group(0)
                                
                        # Clean up label if it includes price
                        if price and price in label:
                            label = label.replace(price, '').strip()
                            
                        if not any(p['name'] == label for p in products):
                            products.append({
                                'name': label,
                                'price': price,
                                'image': img_src
                            })
    except Exception as e:
        print(f"Error extracting products via header: {e}")
        
    # If the standard header extraction failed, look for product links directly
    if not products:
        try:
            for a in soup.find_all('a', href=True):
                href = a['href'].lower()
                if '/product' in href or '/shopping' in href:
                    label = a.get('aria-label', '').strip()
                    if not label:
                        label = a.get_text(strip=True)
                    if label and len(label) > 3 and not any(p['name'] == label for p in products):
                        img = a.find('img')
                        img_src = img['src'] if img else None
                        
                        price = None
                        if '$' in label:
                            match = re.search(r'\$\d+(?:,\d{3})*(?:\.\d{2})?', label)
                            if match:
                                price = match.group(0)
                                label = label.replace(price, '').strip()
                                
                        products.append({
                            'name': label,
                            'price': price,
                            'image': img_src
                        })
        except Exception as e:
            print(f"Error extracting product links: {e}")
            
    return products


# ── Profile scraper ───────────────────────────────────────────────────────

def scrape_profile(soup):
    """
    Extract profile/highlights from the Overview page (already loaded).
    Returns: photo_count, review_topics list.
    """
    profile = {}

    # Photo count — look for text like "2113+ Photos"
    photo_count = "N/A"
    for tag in soup.find_all(string=re.compile(r'\d+\+?\s*Photos', re.I)):
        m = re.search(r'(\d+\+?)\s*Photos', str(tag), re.I)
        if m:
            photo_count = m.group(1) + ' Photos'
            break
    profile['photo_count'] = photo_count

    # Review topics / keywords — from buttons with aria-label "X, mentioned in N reviews"
    topics = []
    topic_btns = soup.find_all(attrs={'aria-label': re.compile(r'mentioned in \d+ reviews', re.I)})
    for btn in topic_btns:
        lbl = btn.get('aria-label', '')
        m = re.match(r'(.+?),\s*mentioned in (\d+) reviews', lbl, re.I)
        if m:
            topics.append({'topic': m.group(1).strip(), 'mentions': int(m.group(2))})
    profile['review_topics'] = topics

    return profile


# ── Reviews scraper ────────────────────────────────────────────────────────

def scrape_reviews(soup):
    """
    Extract up to 10 customer reviews directly from BeautifulSoup of the
    scrolled Overview page (reviews appear inline after scrolling).
    Excludes owner replies (div.CDe7pd).
    Returns list of dicts: reviewer, stars, date, text, has_owner_reply.
    """
    reviews = []
    try:
        blocks = soup.find_all('div', class_=lambda c: c and 'jftiEf' in c)
        print(f"Found {len(blocks)} review blocks in scrolled page")

        for block in blocks:
            name_div = block.find('div', class_=lambda c: c and 'd4r55' in c)
            reviewer = name_div.get_text(strip=True) if name_div else 'Anonymous'

            stars = 0
            star_elem = block.find(attrs={'aria-label': re.compile(r'\d+ stars?', re.I)})
            if star_elem:
                m = re.search(r'(\d+) stars?', star_elem.get('aria-label', ''), re.I)
                if m:
                    stars = int(m.group(1))

            date_span = block.find('span', class_='rsqaWe')
            date = date_span.get_text(strip=True) if date_span else ''

            # Only span.wiI7pd = customer review; div.wiI7pd in div.CDe7pd = owner reply (skip)
            owner_reply_div = block.find('div', class_='CDe7pd')
            text_span = block.find('span', class_='wiI7pd')
            if not text_span:
                continue
            text = text_span.get_text(strip=True)
            if not text:
                continue

            reviews.append({
                'reviewer': reviewer,
                'stars': stars,
                'date': date,
                'text': text,
                'has_owner_reply': owner_reply_div is not None
            })
            if len(reviews) >= 10:
                break

    except Exception as e:
        print(f"Error scraping reviews: {e}")

    return reviews


# ── Hours expander ─────────────────────────────────────────────────────────

def get_weekly_hours(driver):
    """
    Click the hours button (navigates to hours sub-page), parse the table,
    then navigate BACK to the business overview. Returns dict {day: hours}.
    """
    hours = {}
    try:
        oh_btns = driver.find_elements(By.XPATH, "//button[@data-item-id='oh']")
        if not oh_btns:
            return hours

        driver.execute_script("arguments[0].click();", oh_btns[0])
        time.sleep(3)

        soup = BeautifulSoup(driver.page_source, 'html.parser')
        table = soup.find('table')
        if table:
            for row in table.find_all('tr'):
                cells = row.find_all('td')
                if len(cells) >= 2:
                    day = cells[0].get_text(strip=True)
                    hrs = cells[1].get_text(strip=True)
                    if day:
                        hours[day] = hrs

        # Navigate back to the business overview
        driver.back()
        time.sleep(3)

    except Exception as e:
        print(f"Error getting weekly hours: {e}")
        try:
            driver.back()
            time.sleep(2)
        except Exception:
            pass

    return hours


# ── Business page parser ────────────────────────────────────────────────────

def parse_business_page(driver):
    """
    Extract ALL Google Business Profile fields from a Maps business page.
    Order of operations:
      1. Parse ALL static data from the initial page source
      2. Expand hours (navigates away + back)
      3. Scrape reviews (clicks reviews tab)
    """
    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "h1.DUwDvf"))
        )
    except Exception:
        pass

    # ── Step 1: Scroll the panel to load all content (reviews, photos, etc.) ─
    try:
        panel = driver.find_element(By.XPATH, "//div[@role='main']")
        for _ in range(3):
            driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight", panel)
            time.sleep(2)
    except Exception as e:
        print(f"Panel scroll error: {e}")

    # ── Step 2: Parse all static data from the scrolled soup ──────────────────
    soup = BeautifulSoup(driver.page_source, 'html.parser')

    # Name
    h1 = soup.find('h1')
    name = h1.get_text(strip=True) if h1 else "Unknown"

    # Category
    cat_btn = soup.find('button', attrs={'jsaction': re.compile(r'\.category')})
    category = cat_btn.get_text(strip=True) if cat_btn else "N/A"

    # Rating
    rating = 0.0
    f7 = soup.find('div', class_=lambda c: c and 'F7nice' in c)
    if f7:
        rs = f7.find('span', {'aria-hidden': 'true'})
        if rs:
            try:
                rating = float(rs.text.replace(',', '.'))
            except ValueError:
                pass

    # Review count (from Selenium — dynamic element)
    review_count = 0
    try:
        btns = driver.find_elements(By.XPATH,
            "//button[contains(@aria-label,'review') and not(contains(@aria-label,'Write'))]"
        )
        for b in btns:
            lbl = b.get_attribute('aria-label') or ''
            m = re.search(r'([\d,]+)\s+review', lbl, re.I)
            if m:
                review_count = int(m.group(1).replace(',', ''))
                break
    except Exception:
        pass

    # Address
    address = "N/A"
    addr = soup.find(attrs={'data-item-id': 'address'})
    if addr:
        address = extract_aria_value(addr.get('aria-label', ''), prefix='Address:')

    # Current hours status
    current_hours_status = "N/A"
    oh = soup.find(attrs={'data-item-id': 'oh'})
    if oh:
        lbl = oh.get('aria-label', '')
        current_hours_status = re.sub(r'·?\s*See more hours', '', lbl).strip().strip('·').strip()

    # Website — use data-item-id="authority" (the business's own website, not booking link)
    website = "N/A"
    wa = soup.find('a', attrs={'data-item-id': 'authority'})
    if wa:
        website = clean_url(wa.get('href', ''))
    else:
        # Fallback: any <a> with aria-label starting "Website:"
        for a in soup.find_all('a', href=True):
            if (a.get('aria-label', '')).lower().startswith('website:'):
                website = clean_url(a.get('href', ''))
                break

    # Phone — from button data-item-id="phone:tel:..." or tel: link
    phone = "N/A"
    phone_btn = soup.find(attrs={'data-item-id': re.compile(r'^phone:tel:')})
    if phone_btn:
        # Extract from the data-item-id itself: "phone:tel:+12253969383"
        phone_raw = phone_btn.get('data-item-id', '').replace('phone:tel:', '')
        phone = phone_raw if phone_raw else "N/A"
    else:
        tel_a = soup.find('a', href=re.compile(r'^tel:'))
        if tel_a:
            phone = tel_a.get('href', '').replace('tel:', '').strip()

    # Plus code
    plus_code = "N/A"
    oloc = soup.find(attrs={'data-item-id': 'oloc'})
    if oloc:
        plus_code = extract_aria_value(oloc.get('aria-label', ''), prefix='Plus code:')

    # ── Step 3: Extract profile data (topics, photo count) ──────────────────
    profile = scrape_profile(soup)

    # ── Step 4: Extract social media profiles from website or fallback ──────
    social_profiles = extract_social_profiles(website)
    if not social_profiles:
        print("No social profiles on website, using DDG fallback...")
        social_profiles = fallback_social_profiles(name, address)

    # ── Step 5: Extract products if available ───────────────────────────────
    products = []
    try:
        tabs = driver.find_elements(By.XPATH, "//div[@role='tablist']//button")
        overview_tab = None
        products_tab = None
        for t in tabs:
            if "Overview" in t.text:
                overview_tab = t
            elif "Products" in t.text or "Menu" in t.text:
                products_tab = t
                
        if products_tab:
            driver.execute_script("arguments[0].click();", products_tab)
            time.sleep(3) # Wait for products to load
            
            try:
                panel = driver.find_element(By.XPATH, "//div[@role='main']")
                for _ in range(4):
                    driver.execute_script("arguments[0].scrollTop += 1500", panel)
                    time.sleep(1)
            except Exception:
                pass
                
            prod_soup = BeautifulSoup(driver.page_source, 'html.parser')
            products = extract_products(prod_soup)
            
            if overview_tab:
                driver.execute_script("arguments[0].click();", overview_tab)
                time.sleep(2)
        else:
            # If no explicit products tab, maybe they are in overview
            products = extract_products(soup)
    except Exception as e:
        print(f"Error extracting products via tab navigation: {e}")
        products = extract_products(soup)

    # ── Step 6: Expand weekly hours (navigates away + back) ─────────────────
    weekly_hours = get_weekly_hours(driver)

    # ── Step 6: Parse reviews from the scrolled page (already loaded) ─────────
    # After driver.back() from hours, re-scroll and re-parse reviews
    try:
        panel = driver.find_element(By.XPATH, "//div[@role='main']")
        for _ in range(3):
            driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight", panel)
            time.sleep(2)
    except Exception:
        pass
    soup_after = BeautifulSoup(driver.page_source, 'html.parser')
    reviews = scrape_reviews(soup_after)

    return {
        "name": name,
        "category": category,
        "rating": rating,
        "review_count": review_count,
        "address": address,
        "website": website,
        "phone": phone,
        "plus_code": plus_code,
        "current_hours_status": current_hours_status,
        "weekly_hours": weekly_hours,
        "photo_count": profile.get('photo_count', 'N/A'),
        "review_topics": profile.get('review_topics', []),
        "social_profiles": social_profiles,
        "products": products,
        "reviews": reviews
    }


# ── Main scraper ───────────────────────────────────────────────────────────

def run_scraper(query):
    chrome_options = Options()
    chrome_options.add_argument('--headless')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument('--window-size=1920,1080')
    # Real user-agent to avoid limited view
    chrome_options.add_argument(
        '--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
    )

    try:
        driver = webdriver.Chrome(options=chrome_options)
    except Exception as e:
        print("Falling back to explicit chromedriver path:", e)
        service = Service('/usr/bin/chromedriver')
        driver = webdriver.Chrome(service=service, options=chrome_options)

    print(f"Searching Google Maps for: {query}")
    driver.get("https://www.google.com/maps")
    time.sleep(3)

    try:
        driver.find_element("name", "q").send_keys(query + "\n")
    except Exception as e:
        print("Search box error:", e)
        driver.quit()
        return []

    print("Waiting for results...")
    time.sleep(7)

    soup = BeautifulSoup(driver.page_source, 'html.parser')
    all_links = soup.find_all('a', class_="hfpxzc")
    extracted = []

    if not all_links:
        print("No listing links — checking for direct business page...")
        h1 = soup.find('h1')
        if h1 and h1.get_text(strip=True):
            print(f"Direct page: {h1.get_text(strip=True)}")
            data = parse_business_page(driver)
            if data:
                extracted.append(data)
        else:
            print("No business found.")
    else:
        links = [link.get('href') for link in all_links[:10]]
        for i, link in enumerate(links):
            print(f"Scraping listing {i+1}/{len(links)}...")
            driver.get(link)
            time.sleep(5)
            data = parse_business_page(driver)
            if data:
                extracted.append(data)

    driver.quit()
    print(f"Done. {len(extracted)} result(s).")
    return extracted

`

### `Dockerfile`
`dockerfile
FROM python:3.10-slim

# Install system dependencies including chromium and chromedriver
RUN apt-get update && apt-get install -y \
    chromium \
    chromium-driver \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY . .

# Expose port
EXPOSE 5000

# Run the Flask app
CMD ["python", "app.py"]

`

### `requirements.txt`
`	ext
Flask==3.0.0
selenium==4.15.2
beautifulsoup4==4.12.2
requests==2.31.0

`

## How to Run

1. **Using Docker (Recommended)**:
   `ash
   docker build -t gbp-scraper .
   docker run -p 5000:5000 -v "$(pwd)/data:/app/data" gbp-scraper
   `
2. Open `http://localhost:5000` in your browser.
