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
            text_val = heading.text.strip().lower() if heading.text else ""
            if text_val in ['products', 'services']:
                products_header = heading
                break
                
        if products_header:
            blocks_to_search = []
            curr = products_header
            for _ in range(5):
                if curr:
                    blocks_to_search.append(curr)
                    sibling = curr.find_next_sibling('div')
                    if sibling:
                        blocks_to_search.append(sibling)
                    curr = curr.parent

            for block in blocks_to_search:
                for item in block.find_all(['button', 'a', 'div']):
                    if item.name == 'div' and item.get('role') not in ['button', 'link']:
                        continue
                    label = item.get('aria-label', '').strip()
                    img = item.find('img')
                    
                    if label and len(label) > 3 and label.lower() not in ['products', 'next', 'previous', 'view all', 'google apps', 'search', 'menu']:
                        if img:
                            img_src = img['src'] if img.has_attr('src') else ""
                            products.append({
                                "name": label,
                                "price": "",
                                "image": img_src
                            })
                    elif not label and img:
                        texts = [t.strip() for t in item.stripped_strings if len(t.strip()) > 3]
                        if texts and texts[0].lower() not in ['products', 'next', 'previous', 'view all', 'google apps', 'search', 'menu']:
                            products.append({
                                "name": texts[0],
                                "price": texts[1] if len(texts) > 1 else "",
                                "image": img['src'] if img.has_attr('src') else ""
                            })
                if products:
                    break
                    
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
                    if label and len(label) > 3 and label.lower() not in ['google apps', 'products'] and not any(p['name'] == label for p in products):
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
            
    # Deduplicate products by name
    unique_products = []
    seen = set()
    for p in products:
        if p['name'] not in seen:
            unique_products.append(p)
            seen.add(p['name'])
            
    return unique_products


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

            owner_reply_div = block.find('div', class_='CDe7pd')
            text_span = block.find('span', class_='wiI7pd')
            if not text_span:
                continue
            text = text_span.get_text(strip=True)
            if not text:
                continue
                
            owner_reply_text = None
            if owner_reply_div:
                reply_span = owner_reply_div.find('span', class_='wiI7pd')
                if reply_span:
                    owner_reply_text = reply_span.get_text(strip=True)

            reviews.append({
                'reviewer': reviewer,
                'stars': stars,
                'date': date,
                'text': text,
                'has_owner_reply': owner_reply_div is not None,
                'owner_reply_text': owner_reply_text
            })

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
        # Try multiple ways to find the hours expand button
        oh_btns = driver.find_elements(By.XPATH, "//button[@data-item-id='oh'] | //div[@data-item-id='oh'] | //button[contains(@aria-label, 'hours')] | //button[contains(@aria-label, 'Hours')]")
        if not oh_btns:
            return hours

        clicked = False
        for btn in oh_btns:
            try:
                driver.execute_script("arguments[0].click();", btn)
                clicked = True
                break
            except:
                pass
        
        if not clicked:
            return hours

        time.sleep(3)
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        
        tables = soup.find_all('table')
        for table in tables:
            # Check if this table looks like an hours table
            is_hours_table = False
            for row in table.find_all('tr'):
                cells = row.find_all(['td', 'th'])
                if cells:
                    text = cells[0].get_text(strip=True).lower()
                    if 'monday' in text or 'tuesday' in text or 'sunday' in text:
                        is_hours_table = True
                        break
            
            if is_hours_table:
                for row in table.find_all('tr'):
                    cells = row.find_all(['td', 'th'])
                    if len(cells) >= 2:
                        day = cells[0].get_text(strip=True)
                        hrs = cells[1].get_text(strip=True)
                        if day:
                            hours[day] = hrs
                break

        # Fallback to div-based hours if table not found
        if not hours:
            days_of_week = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
            for day in days_of_week:
                elems = soup.find_all(string=lambda t: t and day in t)
                for elem in elems:
                    parent = elem.parent
                    while parent and parent.name not in ["tr", "li"]:
                        if parent.name == "div":
                            texts = [t.strip() for t in parent.stripped_strings if t.strip()]
                            if len(texts) >= 2 and day in texts[0] and any(x in texts[1].lower() for x in ["am", "pm", "closed", "24"]):
                                break
                        parent = parent.parent
                        
                    if parent:
                        texts = [t.strip() for t in parent.stripped_strings if t.strip()]
                        if len(texts) >= 2:
                            d = texts[0]
                            h = " ".join(texts[1:])
                            if day in d:
                                hours[d] = h
                                break

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
        for _ in range(8):
            driver.execute_script("arguments[0].scrollTop += 1200", panel)
            time.sleep(1)
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

    # ── Step 5: Extract products ──────────────────────────────────────────────
    products = []
    try:
        tabs = driver.find_elements(By.XPATH, "//div[@role='tablist']//button")
        products_tab_clicked = False
        for t in tabs:
            if t.text.strip() == "Products" or "Menu" in t.text or "Services" in t.text:
                driver.execute_script("arguments[0].click();", t)
                products_tab_clicked = True
                break
                
        if products_tab_clicked:
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
            
            tabs_again = driver.find_elements(By.XPATH, "//div[@role='tablist']//button")
            for t in tabs_again:
                if "Overview" in t.text:
                    driver.execute_script("arguments[0].click();", t)
                    time.sleep(2)
                    break
        else:
            # Check for a View all products button on the overview page
            view_all_prod_btns = driver.find_elements(By.XPATH, "//*[contains(@aria-label, 'View all products') or contains(text(), 'View all products')]")
            if view_all_prod_btns:
                driver.execute_script("arguments[0].click();", view_all_prod_btns[0])
                time.sleep(3)
                try:
                    panel = driver.find_element(By.XPATH, "//div[@role='main']")
                    for _ in range(4):
                        driver.execute_script("arguments[0].scrollTop += 1500", panel)
                        time.sleep(1)
                except Exception:
                    pass
                prod_soup = BeautifulSoup(driver.page_source, 'html.parser')
                products = extract_products(prod_soup)
                driver.back()
                time.sleep(2)
            else:
                products = extract_products(soup)
    except Exception as e:
        print(f"Error extracting products via tab navigation: {e}")
        products = extract_products(soup)

    # ── Step 6: Expand weekly hours (navigates away + back) ─────────────────
    weekly_hours = get_weekly_hours(driver)

    # ── Step 7: Parse reviews from the Reviews tab or scrolled page ───────────
    reviews = []
    
    def expand_reviews():
        try:
            more_btns = driver.find_elements(By.XPATH, "//button[contains(@aria-label, 'See more') or contains(text(), 'More') or @class='w8nwRe kyuRq']")
            for btn in more_btns:
                try:
                    driver.execute_script("arguments[0].click();", btn)
                    time.sleep(0.5)
                except: pass
        except: pass

    try:
        tabs = driver.find_elements(By.XPATH, "//div[@role='tablist']//button")
        reviews_tab_clicked = False
        for t in tabs:
            if "Reviews" in t.text:
                driver.execute_script("arguments[0].click();", t)
                reviews_tab_clicked = True
                break

        if reviews_tab_clicked:
            time.sleep(3)
            try:
                panel = driver.find_element(By.XPATH, "//div[@role='main']")
                last_height = driver.execute_script("return arguments[0].scrollHeight", panel)
                for _ in range(20):
                    driver.execute_script("arguments[0].scrollTop += 5000", panel)
                    time.sleep(1.5)
                    new_height = driver.execute_script("return arguments[0].scrollHeight", panel)
                    if new_height == last_height:
                        break
                    last_height = new_height
            except Exception:
                pass
            expand_reviews()
            soup_reviews = BeautifulSoup(driver.page_source, 'html.parser')
            reviews = scrape_reviews(soup_reviews)
            
            tabs_again = driver.find_elements(By.XPATH, "//div[@role='tablist']//button")
            for t in tabs_again:
                if "Overview" in t.text:
                    driver.execute_script("arguments[0].click();", t)
                    time.sleep(2)
                    break
    except Exception as e:
        print(f"Error extracting reviews via tab: {e}")
            
    if not reviews:
        try:
            panel = driver.find_element(By.XPATH, "//div[@role='main']")
            for _ in range(3):
                driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight", panel)
                time.sleep(2)
        except Exception:
            pass
        expand_reviews()
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
