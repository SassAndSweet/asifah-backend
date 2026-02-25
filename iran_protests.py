"""
iran_stability.py - Iran Stability Index & Protests Analytics
Asifah Analytics v3.0.0 - February 2026

Consolidated module handling ALL Iran dashboard data:
- Regime Stability Index (multi-component scoring)
- Protest analytics with HRANA/IranWire/GDELT/Reddit
- Oil prices, OPEC reserves, production status
- USD/IRR exchange rate tracking
- Casualty extraction and tracking
- Government status card
- Upstash Redis caching (persistent across Render cold starts)

Replaces: iran_protests.py + Iran-specific code from app.py
"""

import requests
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from flask import jsonify, request

# ============================================
# CONFIGURATION
# ============================================
NEWSAPI_KEY = os.environ.get('NEWSAPI_KEY')
ALPHA_VANTAGE_KEY = os.environ.get('ALPHA_VANTAGE_KEY', 'NUW8NKIRMXNMRTD9')
GDELT_BASE_URL = "https://api.gdeltproject.org/api/v2/doc/doc"

# Upstash Redis (persistent cache across Render cold starts)
UPSTASH_REDIS_URL = os.environ.get('UPSTASH_REDIS_URL')
UPSTASH_REDIS_TOKEN = os.environ.get('UPSTASH_REDIS_TOKEN')

REDDIT_USER_AGENT = "AsifahAnalytics/3.0.0 (OSINT monitoring tool)"

# ============================================
# UPSTASH REDIS CACHE (same pattern as Lebanon)
# ============================================
def load_cache():
    """Load cache from Upstash Redis"""
    if not UPSTASH_REDIS_URL or not UPSTASH_REDIS_TOKEN:
        print("[Iran Cache] No Upstash credentials, using empty cache")
        return {}
    try:
        resp = requests.get(
            f"{UPSTASH_REDIS_URL}/get/iran_cache",
            headers={"Authorization": f"Bearer {UPSTASH_REDIS_TOKEN}"},
            timeout=5
        )
        data = resp.json()
        if data.get("result"):
            cache = json.loads(data["result"])
            print(f"[Iran Cache] Loaded from Redis ({len(cache)} keys)")
            return cache
        print("[Iran Cache] No existing cache in Redis")
        return {}
    except Exception as e:
        print(f"[Iran Cache] Redis load error: {e}")
        return {}


def save_cache(cache_data):
    """Save cache to Upstash Redis"""
    if not UPSTASH_REDIS_URL or not UPSTASH_REDIS_TOKEN:
        return
    try:
        requests.post(
            f"{UPSTASH_REDIS_URL}/set/iran_cache",
            headers={
                "Authorization": f"Bearer {UPSTASH_REDIS_TOKEN}",
                "Content-Type": "application/json"
            },
            json={"value": json.dumps(cache_data)},
            timeout=5
        )
        print("[Iran Cache] Saved to Redis")
    except Exception as e:
        print(f"[Iran Cache] Redis save error: {e}")


# ============================================
# GOVERNMENT STATUS (static, update as needed)
# ============================================
def get_government_status():
    """Current Iranian government leadership"""
    return {
        "supreme_leader": {
            "name": "Ayatollah Ali Khamenei",
            "title": "Supreme Leader",
            "since": "1989-06-04",
            "age": 86,
            "note": "Ultimate authority over military, judiciary, state media"
        },
        "president": {
            "name": "Masoud Pezeshkian",
            "title": "President",
            "since": "2024-07-28",
            "faction": "Reformist",
            "note": "9th President; physician; reform-oriented but constrained by Supreme Leader"
        },
        "speaker_of_parliament": {
            "name": "Mohammad Bagher Ghalibaf",
            "title": "Speaker of the Islamic Consultative Assembly",
            "since": "2024-05-28",
            "note": "Former Tehran mayor; conservative faction"
        },
        "irgc_commander": {
            "name": "Major General Hossein Salami",
            "title": "Commander-in-Chief, IRGC",
            "since": "2019-04-21",
            "note": "190,000 active personnel; parallel military force"
        },
        "quds_force_commander": {
            "name": "Brigadier General Esmail Qaani",
            "title": "Commander, IRGC Quds Force",
            "since": "2020-01-03",
            "note": "Succeeded Qasem Soleimani; reported killed Jun 2025 but resurfaced alive"
        },
        "days_pezeshkian_in_office": (datetime.now() - datetime(2024, 7, 28)).days
    }


# ============================================
# USD/IRR EXCHANGE RATE
# ============================================
def get_exchange_rate():
    """
    Fetch USD/IRR exchange rate from multiple sources
    Iran is under sanctions so official rates differ wildly from black market.
    Black market rate is the real economic indicator.
    """
    # Fallback: use hardcoded recent data with note
    # Black market rate as of Feb 24, 2026: ~1,641,000 IRR/USD
    # Up ~60% in 6 months (from ~1,020,000 in Aug 2025)
    fallback_rate = {
        "usd_to_irr": 1641000,
        "change_24h": 0.61,
        "change_7d": 0.80,
        "change_6m": 60.96,
        "pressure": "SELLING",
        "source": "alanchand.com/bonbast.com",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "is_fallback": True,
        "note": "Black market rate; official rate (~42,000) is symbolic"
    }

    try:
        # Try bonbast JSON endpoint
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json',
            'Referer': 'https://bonbast.com/'
        }
        resp = requests.get("https://bonbast.com/json", headers=headers, timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            usd_sell = int(data.get("usd_sell", "0").replace(",", "")) * 10  # Toman to Rial
            if usd_sell > 100000:
                fallback_rate["usd_to_irr"] = usd_sell
                fallback_rate["is_fallback"] = False
                fallback_rate["source"] = "bonbast.com (live)"
                print(f"[Exchange Rate] Live bonbast rate: {usd_sell} IRR/USD")
    except Exception as e:
        print(f"[Exchange Rate] Bonbast fetch failed: {e}, using fallback")

    return fallback_rate


# ============================================
# OIL PRICE DATA (BRENT CRUDE)
# ============================================
def get_brent_oil_price():
    """Fetch current Brent crude oil price from Alpha Vantage API"""
    try:
        url = f"https://www.alphavantage.co/query?function=CRUDE_OIL_BRENT&interval=daily&apikey={ALPHA_VANTAGE_KEY}"
        response = requests.get(url, timeout=10)
        data = response.json()

        if "data" in data and len(data["data"]) > 0:
            # Filter out "." values (Alpha Vantage returns "." for missing data)
            valid_data = [d for d in data["data"] if d.get("value", ".") != "."]
            if len(valid_data) < 2:
                return get_fallback_oil_price()

            latest = valid_data[0]
            previous = valid_data[1]

            current_price = float(latest["value"])
            previous_price = float(previous["value"])

            price_change = current_price - previous_price
            percent_change = (price_change / previous_price) * 100

            if price_change > 0.01:
                arrow, trend = "↑", "up"
            elif price_change < -0.01:
                arrow, trend = "↓", "down"
            else:
                arrow, trend = "→", "flat"

            # Build real sparkline from last 90 data points
            sparkline_data = []
            for d in reversed(valid_data[:90]):
                try:
                    sparkline_data.append({
                        "date": d["date"],
                        "price": round(float(d["value"]), 2)
                    })
                except (ValueError, KeyError):
                    continue

            return {
                "success": True,
                "current_price": round(current_price, 2),
                "price_change": round(price_change, 2),
                "percent_change": round(percent_change, 2),
                "arrow": arrow,
                "trend": trend,
                "timestamp": latest["date"],
                "currency": "USD",
                "unit": "bbl",
                "sparkline": sparkline_data
            }
        else:
            return get_fallback_oil_price()

    except Exception as e:
        print(f"[Oil Price API Error]: {e}")
        return get_fallback_oil_price()


def get_fallback_oil_price():
    """Fallback oil price data when API is unavailable"""
    return {
        "success": True,
        "current_price": 74.50,
        "price_change": 0.00,
        "percent_change": 0.00,
        "arrow": "→",
        "trend": "flat",
        "timestamp": datetime.now().strftime("%Y-%m-%d"),
        "currency": "USD",
        "unit": "bbl",
        "source": "fallback",
        "sparkline": []
    }


# ============================================
# OPEC RESERVES (static reference data)
# ============================================
def get_iran_oil_reserves():
    """Iran's proven oil and gas reserves from OPEC ASB 2025"""
    return {
        "success": True,
        "oil_reserves": {
            "amount": 208.6,
            "unit": "billion barrels",
            "global_rank": 3,
            "context": "#3 globally (after Venezuela, Saudi Arabia)"
        },
        "gas_reserves": {
            "amount": 33.988,
            "unit": "trillion cubic meters",
            "global_rank": 2,
            "context": "#2 globally (after Russia)"
        },
        "opec_membership": {
            "member_since": 1960,
            "founding_member": True
        },
        "economic_data": {
            "population": 88.5,
            "population_unit": "million",
            "gdp_per_capita": 4200,
            "currency": "USD",
            "note": "GDP per capita estimated; heavily impacted by sanctions and war"
        },
        "source": {
            "name": "OPEC Annual Statistical Bulletin 2025",
            "date": "July 2, 2025",
            "url": "https://www.opec.org/annual-statistical-bulletin.html"
        }
    }


# ============================================
# OIL PRODUCTION STATUS
# ============================================
def get_iran_oil_production_status(all_articles=None):
    """
    Track Iran's oil production/export status
    Now scans articles for halt/disruption keywords instead of stub
    """
    try:
        # Hardcoded from latest OPEC/EIA reports - UPDATE MONTHLY
        latest_production = {
            "barrels_per_day": 3187000,
            "date": "2025-12-01",
            "source": "OPEC Monthly Oil Market Report",
            "source_url": "https://www.opec.org/opec_web/en/publications/338.htm"
        }

        baseline_production = 2000000

        # Scan articles for oil disruption signals
        halt_news = scan_iran_oil_news(all_articles or [])

        bpd = latest_production["barrels_per_day"]

        if halt_news["halt_detected"]:
            status = "halted"
            status_emoji = "🔴"
            status_text = "EXPORT HALT REPORTED"
            status_detail = halt_news["summary"]
            news_link = halt_news["url"]
        elif bpd >= baseline_production:
            status = "exporting"
            status_emoji = "🟢"
            status_text = "EXPORTING OIL"
            status_detail = f"{round(bpd/1000000, 2)}M bpd (Normal operations)"
            news_link = None
        elif bpd >= 1000000:
            status = "reduced"
            status_emoji = "🟡"
            status_text = "REDUCED EXPORTS"
            status_detail = f"{round(bpd/1000000, 2)}M bpd (Sanctions impact)"
            news_link = None
        else:
            status = "minimal"
            status_emoji = "🔴"
            status_text = "MINIMAL EXPORTS"
            status_detail = f"{round(bpd/1000000, 2)}M bpd (Heavy sanctions)"
            news_link = None

        return {
            "success": True,
            "status": status,
            "emoji": status_emoji,
            "status_text": status_text,
            "status_detail": status_detail,
            "production_bpd": bpd,
            "production_date": latest_production["date"],
            "production_source": latest_production["source"],
            "production_source_url": latest_production["source_url"],
            "news_link": news_link,
            "last_updated": datetime.now().isoformat()
        }

    except Exception as e:
        print(f"[Oil Production Status Error]: {e}")
        return {
            "success": False,
            "status": "unknown",
            "emoji": "⚪",
            "status_text": "STATUS UNKNOWN",
            "status_detail": "Error fetching production data",
            "error": str(e)
        }


def scan_iran_oil_news(articles):
    """
    Scan articles for Iran oil export halts/shutdowns
    NOW ACTUALLY SCANS instead of returning stub
    """
    halt_keywords = [
        "iran oil halt", "iran stops oil exports", "iran oil shutdown",
        "iran suspends oil", "iran oil production cut", "oil embargo iran",
        "iran oil sanctions enforcement", "iran tanker seized",
        "strait of hormuz closed", "hormuz blockade",
        "iran oil exports stopped", "iran oil supply disruption"
    ]

    try:
        for article in articles:
            text = f"{article.get('title', '')} {article.get('description', '')}".lower()
            for keyword in halt_keywords:
                if keyword in text:
                    return {
                        "halt_detected": True,
                        "summary": article.get('title', 'Oil disruption detected'),
                        "url": article.get('url'),
                        "source": article.get('source', {}).get('name', 'Unknown')
                    }

        return {"halt_detected": False, "summary": None, "url": None, "source": None}

    except Exception as e:
        print(f"[Oil News Scan Error]: {e}")
        return {"halt_detected": False, "summary": None, "url": None}


# ============================================
# NEWS FETCHING (GDELT, NewsAPI, Reddit, RSS)
# ============================================
def fetch_newsapi_articles(query, days=7):
    """Fetch articles from NewsAPI"""
    if not NEWSAPI_KEY:
        print("[Iran] NewsAPI: No API key")
        return []

    from_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    url = "https://newsapi.org/v2/everything"
    params = {
        'q': query,
        'from': from_date,
        'sortBy': 'publishedAt',
        'language': 'en',
        'apiKey': NEWSAPI_KEY,
        'pageSize': 100
    }

    try:
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 200:
            articles = response.json().get('articles', [])
            for a in articles:
                a['language'] = 'en'
            print(f"[Iran] NewsAPI: {len(articles)} articles")
            return articles
        print(f"[Iran] NewsAPI: HTTP {response.status_code}")
        return []
    except Exception as e:
        print(f"[Iran] NewsAPI error: {e}")
        return []


def fetch_gdelt_articles(query, days=7, language='eng'):
    """Fetch articles from GDELT"""
    try:
        wrapped_query = f"({query})" if ' OR ' in query else query
        params = {
            'query': wrapped_query,
            'mode': 'artlist',
            'maxrecords': 75,
            'timespan': f'{days}d',
            'format': 'json',
            'sourcelang': language
        }

        response = None
        for attempt in range(2):
            try:
                response = requests.get(GDELT_BASE_URL, params=params, timeout=30)
                if response.status_code == 200:
                    break
            except requests.Timeout:
                if attempt == 0:
                    print(f"[Iran] GDELT {language}: Retry after timeout...")
                    time.sleep(2)
                    continue
                raise

        if not response or response.status_code != 200:
            print(f"[Iran] GDELT {language}: Failed after retries")
            return []

        try:
            data = response.json()
        except (json.JSONDecodeError, ValueError) as e:
            print(f"[Iran] GDELT {language}: JSON parse error: {str(e)[:100]}")
            print(f"[Iran] GDELT {language}: Response starts with: {response.text[:200]}")
            return []
        articles = data.get('articles', [])

        lang_code = {'eng': 'en', 'ara': 'ar', 'heb': 'he', 'fas': 'fa'}.get(language, 'en')
        standardized = []
        for article in articles:
            standardized.append({
                'title': article.get('title', ''),
                'description': article.get('title', ''),
                'url': article.get('url', ''),
                'publishedAt': article.get('seendate', ''),
                'source': {'name': article.get('domain', 'GDELT')},
                'content': article.get('title', ''),
                'language': lang_code
            })

        # Filter out misclassified articles (GDELT sourcelang is unreliable)
        before_count = len(standardized)
        standardized = [a for a in standardized if validate_article_language(a, lang_code)]
        filtered = before_count - len(standardized)
        if filtered > 0:
            print(f"[Iran] GDELT {language}: Filtered {filtered} misclassified articles")

        print(f"[Iran] GDELT {language}: {len(standardized)} articles")
        return standardized

    except Exception as e:
        print(f"[Iran] GDELT {language} error: {e}")
        return []


def validate_article_language(article, expected_lang):
    """
    Basic validation that article content matches expected language.
    GDELT sourcelang filter is unreliable — returns Korean, Chinese, Hindi etc. as 'ara'.
    """
    text = f"{article.get('title', '')} {article.get('description', '')}".strip()
    if not text:
        return False

    # Check for expected script characters
    if expected_lang == 'ar':
        # Arabic: U+0600-U+06FF
        arabic_chars = sum(1 for c in text if '\u0600' <= c <= '\u06FF')
        return arabic_chars >= len(text) * 0.15
    elif expected_lang == 'he':
        # Hebrew: U+0590-U+05FF
        hebrew_chars = sum(1 for c in text if '\u0590' <= c <= '\u05FF')
        return hebrew_chars >= len(text) * 0.15
    elif expected_lang == 'fa':
        # Farsi uses Arabic script (U+0600-U+06FF) plus some extras (U+FB50-U+FDFF, U+FE70-U+FEFF)
        farsi_chars = sum(1 for c in text if '\u0600' <= c <= '\u06FF' or '\uFB50' <= c <= '\uFDFF')
        return farsi_chars >= len(text) * 0.15
    
    if expected_lang == 'en':
        # English: basic Latin chars (A-Z, a-z) should dominate
        latin_chars = sum(1 for c in text if 'A' <= c <= 'Z' or 'a' <= c <= 'z')
        return latin_chars >= len(text) * 0.30
    return True


def fetch_reddit_posts(days=7):
    """Fetch Reddit posts about Iran from relevant subreddits"""
    subreddits = ["Iran", "NewIran", "Israel", "geopolitics"]
    keywords = ['Iran', 'protest', 'IRGC', 'Tehran', 'sanctions', 'nuclear']
    all_posts = []

    time_filter = "week" if days <= 7 else ("month" if days <= 30 else "year")

    for subreddit in subreddits:
        try:
            query = " OR ".join(keywords[:3])
            url = f"https://www.reddit.com/r/{subreddit}/search.json"
            params = {
                "q": query,
                "restrict_sr": "true",
                "sort": "new",
                "t": time_filter,
                "limit": 25
            }
            headers = {"User-Agent": REDDIT_USER_AGENT}

            time.sleep(2)
            response = requests.get(url, params=params, headers=headers, timeout=10)

            if response.status_code == 200:
                data = response.json()
                if "data" in data and "children" in data["data"]:
                    for post in data["data"]["children"]:
                        pd = post.get("data", {})
                        all_posts.append({
                            "title": pd.get("title", "")[:200],
                            "description": pd.get("selftext", "")[:300],
                            "url": f"https://www.reddit.com{pd.get('permalink', '')}",
                            "publishedAt": datetime.fromtimestamp(
                                pd.get("created_utc", 0), tz=timezone.utc
                            ).isoformat(),
                            "source": {"name": f"r/{subreddit}"},
                            "content": pd.get("selftext", ""),
                            "language": "en"
                        })
                    print(f"[Iran] Reddit r/{subreddit}: {len(data['data']['children'])} posts")
        except Exception as e:
            print(f"[Iran] Reddit r/{subreddit} error: {e}")
            continue

    print(f"[Iran] Reddit total: {len(all_posts)} posts")
    return all_posts


def fetch_iranwire_rss():
    """Fetch articles from Iran Wire RSS feeds"""
    articles = []
    feeds = {
        'en': 'https://iranwire.com/en/feed/',
        'fa': 'https://iranwire.com/fa/feed/'
    }

    for lang, feed_url in feeds.items():
        try:
            print(f"[Iran] IranWire {lang}: Fetching RSS...")
            response = requests.get(feed_url, timeout=15, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            })
            if response.status_code != 200:
                print(f"[Iran] IranWire {lang}: HTTP {response.status_code}")
                continue

            root = ET.fromstring(response.content)
            items = root.findall('.//item')

            for item in items[:15]:
                title_elem = item.find('title')
                link_elem = item.find('link')
                pubDate_elem = item.find('pubDate')
                description_elem = item.find('description')
                content_elem = item.find('{http://purl.org/rss/1.0/modules/content/}encoded')

                if title_elem is not None and link_elem is not None:
                    pub_date = pubDate_elem.text if pubDate_elem is not None else datetime.now(timezone.utc).isoformat()
                    description = ''
                    if description_elem is not None and description_elem.text:
                        description = description_elem.text[:500]
                    elif content_elem is not None and content_elem.text:
                        description = content_elem.text[:500]

                    articles.append({
                        'title': title_elem.text or '',
                        'description': description,
                        'url': link_elem.text or '',
                        'publishedAt': pub_date,
                        'source': {'name': 'Iran Wire'},
                        'content': description,
                        'language': lang
                    })

            print(f"[Iran] IranWire {lang}: ✓ {len([a for a in articles if a.get('language') == lang])} articles")
        except Exception as e:
            print(f"[Iran] IranWire {lang} error: {str(e)[:100]}")

    return articles


def fetch_hrana_rss():
    """Fetch articles from HRANA RSS feed"""
    articles = []
    feed_urls = [
        'https://www.en-hrana.org/feed/',
        'https://en-hrana.org/feed/',
        'https://en.hrana.org/feed/',
    ]

    for feed_url in feed_urls:
        try:
            print(f"[Iran] HRANA: Trying {feed_url}...")
            headers = {
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate, br',
                'Connection': 'keep-alive',
                'Cache-Control': 'no-cache',
            }
            response = requests.get(feed_url, headers=headers, timeout=20)

            if response.status_code != 200:
                print(f"[Iran] HRANA: HTTP {response.status_code} on {feed_url}")
                continue

            root = ET.fromstring(response.content)
            items = root.findall('.//item')
            for item in items[:15]:
                title_elem = item.find('title')
                link_elem = item.find('link')
                pubDate_elem = item.find('pubDate')
                description_elem = item.find('description')
                content_elem = item.find('{http://purl.org/rss/1.0/modules/content/}encoded')

                if title_elem is not None and link_elem is not None:
                    pub_date = pubDate_elem.text if pubDate_elem is not None else datetime.now(timezone.utc).isoformat()
                    description = ''
                    if description_elem is not None and description_elem.text:
                        description = description_elem.text[:500]
                    elif content_elem is not None and content_elem.text:
                        description = content_elem.text[:500]

                    articles.append({
                        'title': title_elem.text or '',
                        'description': description,
                        'url': link_elem.text or '',
                        'publishedAt': pub_date,
                        'source': {'name': 'HRANA'},
                        'content': description,
                        'language': 'en'
                    })
            print(f"[Iran] HRANA: ✓ {len(articles)} articles")
            if articles:
                return articles  # Got results, stop trying other URLs

        except requests.Timeout:
            print(f"[Iran] HRANA: Timeout on {feed_url}")
            continue
        except Exception as e:
            print(f"[Iran] HRANA error on {feed_url}: {str(e)[:100]}")
            continue

    if not articles:
        print("[Iran] HRANA: All feed URLs failed")
    return articles


# ============================================
# HRANA STRUCTURED DATA EXTRACTION
# ============================================
def extract_hrana_structured_data(articles):
    """Extract structured protest statistics from HRANA articles"""
    structured_data = {
        'confirmed_deaths': 0,
        'deaths_under_investigation': 0,
        'seriously_injured': 0,
        'total_arrests': 0,
        'cities_affected': 0,
        'provinces_affected': 0,
        'protest_gatherings': 0,
        'source_article': None,
        'last_updated': None,
        'is_hrana_verified': False
    }

    patterns = {
        'confirmed_deaths': [
            r'confirmed\s+deaths?\s*:?\s*(\d{1,3}(?:,\d{3})*)',
            r'number\s+of\s+confirmed\s+deaths?\s*:?\s*(\d{1,3}(?:,\d{3})*)',
            r'(\d{1,3}(?:,\d{3})*)\s+(?:people\s+)?(?:have\s+been\s+)?killed'
        ],
        'deaths_under_investigation': [
            r'deaths?\s+under\s+(?:review|investigation)\s*:?\s*(\d{1,3}(?:,\d{3})*)',
        ],
        'seriously_injured': [
            r'seriously?\s+injured\s*:?\s*(\d{1,3}(?:,\d{3})*)',
            r'(\d{1,3}(?:,\d{3})*)\s+people\s+have\s+sustained\s+serious\s+injuries'
        ],
        'total_arrests': [
            r'total\s+(?:number\s+of\s+)?arrests?\s*(?:has\s+risen\s+to)?\s*:?\s*(\d{1,3}(?:,\d{3})*)',
            r'(\d{1,3}(?:,\d{3})*)\s+people?\s+(?:have\s+been\s+)?(?:arrested|detained)'
        ],
        'cities_affected': [
            r'(\d{1,3})\s+cities\s+(?:affected|involved)',
            r'protests?\s+in\s+(\d{1,3})\s+cities'
        ],
        'provinces_affected': [
            r'(?:all\s+)?(\d{1,2})\s+provinces',
        ],
        'protest_gatherings': [
            r'(\d{1,3}(?:,\d{3})*)\s+protest\s+gatherings?',
        ]
    }

    hrana_articles = [a for a in articles if a.get('source', {}).get('name') == 'HRANA']

    for article in hrana_articles:
        title = article.get('title', '').lower()
        content = article.get('content', '').lower()
        is_summary = 'day ' in title and ('protest' in title or 'aggregated data' in content)

        if is_summary or 'aggregated data' in content or 'killed' in content or 'arrested' in content:
            full_text = f"{title} {content}"
            for key, pattern_list in patterns.items():
                for pattern in pattern_list:
                    match = re.search(pattern, full_text, re.IGNORECASE)
                    if match:
                        number_str = match.group(1).replace(',', '')
                        try:
                            number = int(number_str)
                            if number > structured_data[key]:
                                structured_data[key] = number
                                structured_data['source_article'] = article.get('url')
                                structured_data['last_updated'] = article.get('publishedAt')
                                structured_data['is_hrana_verified'] = True
                        except:
                            pass

    if structured_data['is_hrana_verified']:
        print(f"[Iran] HRANA Structured: deaths={structured_data['confirmed_deaths']}, "
              f"injured={structured_data['seriously_injured']}, arrests={structured_data['total_arrests']}")

    return structured_data


# ============================================
# CASUALTY EXTRACTION (from article text)
# ============================================

# Max plausible numbers for a 7-day window of Iran protests
# These caps prevent historical references and unrelated large numbers
# from polluting the extraction (e.g., "7,000 killed in Iran-Iraq war")
CASUALTY_CAPS = {
    'deaths': 500,      # Single-week cap; Mahsa Amini peak was ~50/week
    'injuries': 2000,
    'arrests': 5000
}

# False positive phrases: sentences containing these are skipped
FALSE_POSITIVE_PATTERNS = [
    r'iran[- ]iraq war',
    r'world war',
    r'earthquake',
    r'flood',
    r'covid',
    r'pandemic',
    r'historical',
    r'since \d{4}',          # "7,000 killed since 1988"
    r'over the past \d+ years',
    r'in the \d{4}s',        # "in the 1980s"
    r'decades? ago',
    r'anniversary',
    r'commemorate',
]

CASUALTY_KEYWORDS = {
    'deaths': [
        'killed', 'dead', 'died', 'death toll', 'fatalities', 'deaths',
        'shot dead', 'gunned down', 'protesters killed',
        'کشته', 'مرگ', 'قتل'
    ],
    'injuries': [
        'injured', 'wounded', 'hurt', 'injuries', 'casualties',
        'hospitalized', 'gunshot wounds', 'injured protesters',
        'مجروح', 'زخمی', 'آسیب'
    ],
    'arrests': [
        'arrested', 'detained', 'detention', 'arrest', 'arrests',
        'taken into custody', 'apprehended', 'rounded up',
        'imprisoned', 'people have been arrested',
        'بازداشت', 'دستگیر', 'زندان'
    ]
}


def is_false_positive(sentence):
    """Check if a sentence is a historical/unrelated reference"""
    for pattern in FALSE_POSITIVE_PATTERNS:
        if re.search(pattern, sentence, re.IGNORECASE):
            return True
    return False


def extract_casualty_data(articles):
    """
    Extract casualty numbers from articles using multiple regex strategies.

    Strategy 1: "NUMBER keyword" - e.g., "3 killed", "20 people arrested"
    Strategy 2: "keyword NUMBER" - e.g., "killed 3", "death toll: 45"
    Strategy 3: "NUMBER were/have been keyword" - e.g., "3 were killed"

    Includes:
    - False positive filtering (historical refs, wars, earthquakes)
    - Sanity caps (no single 7-day extraction > 500 deaths)
    - Logging when caps are hit (so you can see what triggered it)
    """
    casualties = {
        'deaths': 0,
        'injuries': 0,
        'arrests': 0,
        'sources': set(),
        'details': []
    }

    # --- Pattern templates: {keyword} is replaced per keyword ---

    # Number BEFORE keyword (most common: "3 killed", "20 people arrested")
    number_before_kw = [
        r'(\d{1,6})\s+(?:people\s+)?(?:were\s+|have\s+been\s+)?{keyword}',
        r'(?:more than|over|at least|nearly|approximately|about|around)\s+(\d{1,6})\s+(?:people\s+)?(?:were\s+|have\s+been\s+)?{keyword}',
        r'(\d{1,6})\s+(?:people|protesters?|demonstrators?|citizens?|iranians?)\s+(?:were\s+|have\s+been\s+)?{keyword}',
    ]

    # Keyword BEFORE number ("death toll: 45", "arrested 120 people")
    kw_before_number = [
        r'{keyword}\s+(?:of\s+)?(?:more than\s+|at least\s+|over\s+)?(\d{1,6})',
        r'{keyword}\s*(?::|to|rose to|reached|climbed to|stands at)\s*(?:more than\s+|at least\s+)?(\d{1,6})',
    ]

    for article in articles:
        title = article.get('title') or ''
        description = article.get('description') or ''
        content = article.get('content') or ''
        text = (title + ' ' + description + ' ' + content).lower()
        source = article.get('source', {}).get('name', 'Unknown')
        url = article.get('url', '')

        # Split into sentences for context-aware matching
        sentences = re.split(r'[.!?]\s+', text)

        for sentence in sentences:
            # Skip historical / unrelated references
            if is_false_positive(sentence):
                continue

            for category, keywords in CASUALTY_KEYWORDS.items():
                for keyword in keywords:
                    if keyword not in sentence:
                        continue

                    best_num = 0

                    # Try number-before-keyword patterns
                    for tmpl in number_before_kw:
                        pattern = tmpl.format(keyword=re.escape(keyword))
                        match = re.search(pattern, sentence, re.IGNORECASE)
                        if match:
                            try:
                                num = int(match.group(1).replace(',', ''))
                                if num > best_num:
                                    best_num = num
                            except (ValueError, IndexError):
                                pass

                    # Try keyword-before-number patterns
                    for tmpl in kw_before_number:
                        pattern = tmpl.format(keyword=re.escape(keyword))
                        match = re.search(pattern, sentence, re.IGNORECASE)
                        if match:
                            try:
                                num = int(match.group(1).replace(',', ''))
                                if num > best_num:
                                    best_num = num
                            except (ValueError, IndexError):
                                pass

                    # Apply per-match sanity cap — skip if over threshold
                    cap = CASUALTY_CAPS.get(category, 5000)
                    if best_num > cap:
                        print(f"[Iran] Casualty cap hit: {category}={best_num} from '{source}' "
                              f"(cap={cap}), sentence: '{sentence[:150]}...'")
                        continue

                    # Update if this is the highest credible number we've seen
                    if best_num > 0 and best_num > casualties[category]:
                        casualties[category] = best_num
                        casualties['sources'].add(source)
                        casualties['details'].append({
                            'type': category,
                            'count': best_num,
                            'source': source,
                            'url': url,
                            'sentence': sentence[:200]
                        })

    # --- Post-loop sanity caps: reset to 0 rather than report false data ---
    if casualties['deaths'] > 500:
        print(f"[Iran] Casualty sanity cap: deaths {casualties['deaths']} -> reset, likely false positive")
        casualties['deaths'] = 0
    if casualties['injuries'] > 2000:
        print(f"[Iran] Casualty sanity cap: injuries {casualties['injuries']} -> reset, likely false positive")
        casualties['injuries'] = 0
    if casualties['arrests'] > 5000:
        print(f"[Iran] Casualty sanity cap: arrests {casualties['arrests']} -> reset, likely false positive")
        casualties['arrests'] = 0

    casualties['sources'] = list(casualties['sources'])
    print(f"[Iran] Casualties: deaths={casualties['deaths']}, "
          f"injuries={casualties['injuries']}, arrests={casualties['arrests']}")
    return casualties


# ============================================
# CITY EXTRACTION (dynamic, not hardcoded)
# ============================================
IRAN_CITIES = [
    'tehran', 'isfahan', 'shiraz', 'tabriz', 'mashhad', 'ahvaz', 'kerman',
    'rasht', 'hamadan', 'yazd', 'sanandaj', 'zahedan', 'bandar abbas',
    'qom', 'arak', 'zanjan', 'gorgan', 'birjand', 'bushehr', 'karaj',
    'urmia', 'ardabil', 'ilam', 'khorramabad', 'bojnurd', 'semnan',
    'sari', 'yasuj', 'shahr-e kord', 'mahabad', 'marivan', 'piranshahr',
    'bukan', 'saqqez', 'javanrud', 'oshnavieh', 'divandarreh',
    'abadan', 'qazvin', 'babol', 'kish', 'chabahar'
]


def extract_cities_from_articles(articles):
    """Dynamically extract mentioned Iranian cities from articles"""
    city_counts = {}
    city_sources = {}

    for article in articles:
        text = f"{article.get('title', '')} {article.get('description', '')} {article.get('content', '')}".lower()
        source = article.get('source', {}).get('name', 'Unknown')
        url = article.get('url', '')

        for city in IRAN_CITIES:
            if city in text:
                city_counts[city] = city_counts.get(city, 0) + 1
                if city not in city_sources:
                    city_sources[city] = []
                if len(city_sources[city]) < 3:
                    city_sources[city].append({'name': source, 'url': url})

    # Sort by mention count, return formatted
    sorted_cities = sorted(city_counts.items(), key=lambda x: x[1], reverse=True)
    cities = []
    for city_name, count in sorted_cities[:15]:
        cities.append({
            'name': city_name.title(),
            'count': count,
            'sources': city_sources.get(city_name, [])
        })

    return cities


# ============================================
# REGIME STABILITY INDEX (Multi-Component)
# ============================================
def calculate_regime_stability(all_articles, casualties, exchange_rate, oil_price,
                                num_cities, hrana_data, cache):
    """
    Multi-component Regime Stability Index for Iran
    100 = Stable Regime | 0 = Imminent Collapse

    Components (weighted):
    1. Protest Activity (30%) - article volume, intensity keywords
    2. Economic Pressure (25%) - rial depreciation, sanctions impact
    3. Security Response (20%) - casualty levels, crackdown intensity
    4. Geopolitical Tension (15%) - US/Israel posture, military activity
    5. Internal Cohesion (10%) - elite fractures, IRGC vs reformists
    """
    scores = {}

    # --- 1. PROTEST ACTIVITY (30%) ---
    protest_keywords = [
        'protest', 'demonstration', 'rally', 'strike', 'uprising',
        'unrest', 'riot', 'bazaar strike', 'shutdown', 'civil disobedience',
        'اعتراض', 'تظاهرات', 'اعتصاب'
    ]
    protest_articles = 0
    for a in all_articles:
        text = f"{a.get('title', '')} {a.get('description', '')}".lower()
        if any(kw in text for kw in protest_keywords):
            protest_articles += 1

    # More protest coverage = less stable
    if protest_articles >= 50:
        protest_score = 15
    elif protest_articles >= 30:
        protest_score = 30
    elif protest_articles >= 15:
        protest_score = 45
    elif protest_articles >= 5:
        protest_score = 60
    else:
        protest_score = 80

    # City spread penalty
    if num_cities >= 20:
        protest_score -= 15
    elif num_cities >= 10:
        protest_score -= 10
    elif num_cities >= 5:
        protest_score -= 5

    scores['protest_activity'] = max(0, min(100, protest_score))

    # --- 2. ECONOMIC PRESSURE (25%) ---
    irr_rate = exchange_rate.get('usd_to_irr', 1641000)
    # Baseline: 42,000 official; 820,000 a year ago; 1,641,000 now
    if irr_rate >= 2000000:
        econ_score = 10  # Hyperinflation territory
    elif irr_rate >= 1500000:
        econ_score = 25  # Severe depreciation (current)
    elif irr_rate >= 1000000:
        econ_score = 40
    elif irr_rate >= 700000:
        econ_score = 55
    elif irr_rate >= 500000:
        econ_score = 70
    else:
        econ_score = 85

    # Oil price factor: higher oil = more revenue = more stable
    current_oil = oil_price.get('current_price', 74)
    if current_oil >= 90:
        econ_score += 10
    elif current_oil >= 75:
        econ_score += 5
    elif current_oil < 60:
        econ_score -= 10

    scores['economic_pressure'] = max(0, min(100, econ_score))

    # --- 3. SECURITY RESPONSE (20%) ---
    total_casualties = (casualties.get('deaths', 0) +
                        casualties.get('injuries', 0))
    total_arrests = casualties.get('arrests', 0)

    # High casualties = regime using force = unstable but not collapsing
    if total_casualties >= 100:
        security_score = 20  # Massive crackdown = very fragile
    elif total_casualties >= 30:
        security_score = 35
    elif total_casualties >= 10:
        security_score = 50
    else:
        security_score = 65

    if total_arrests >= 1000:
        security_score -= 10
    elif total_arrests >= 100:
        security_score -= 5

    scores['security_response'] = max(0, min(100, security_score))

    # --- 4. GEOPOLITICAL TENSION (15%) ---
    geo_keywords_critical = [
        'war with iran', 'strike iran', 'attack iran', 'bomb iran',
        'carrier group', 'uss abraham lincoln', 'centcom',
        'strait of hormuz', 'hormuz drill', 'nuclear strike',
        'total war', 'military option'
    ]
    geo_keywords_elevated = [
        'sanctions iran', 'iran threat', 'iran tensions',
        'iran nuclear', 'enrichment', 'iaea',
        'iran israel', 'iran us'
    ]

    geo_critical = 0
    geo_elevated = 0
    for a in all_articles:
        text = f"{a.get('title', '')} {a.get('description', '')}".lower()
        if any(kw in text for kw in geo_keywords_critical):
            geo_critical += 1
        elif any(kw in text for kw in geo_keywords_elevated):
            geo_elevated += 1

    if geo_critical >= 10:
        geo_score = 15  # Active war posture
    elif geo_critical >= 5:
        geo_score = 25
    elif geo_critical >= 2:
        geo_score = 40
    elif geo_elevated >= 10:
        geo_score = 45
    elif geo_elevated >= 5:
        geo_score = 55
    else:
        geo_score = 70

    scores['geopolitical_tension'] = max(0, min(100, geo_score))

    # --- 5. INTERNAL COHESION (10%) ---
    fracture_keywords = [
        'irgc vs', 'reformist', 'pezeshkian criticized', 'resign',
        'power struggle', 'factional', 'infighting', 'split',
        'khamenei successor', 'succession crisis', 'elite divide'
    ]
    fracture_count = 0
    for a in all_articles:
        text = f"{a.get('title', '')} {a.get('description', '')}".lower()
        if any(kw in text for kw in fracture_keywords):
            fracture_count += 1

    if fracture_count >= 10:
        cohesion_score = 25
    elif fracture_count >= 5:
        cohesion_score = 40
    elif fracture_count >= 2:
        cohesion_score = 55
    else:
        cohesion_score = 70

    scores['internal_cohesion'] = max(0, min(100, cohesion_score))

    # --- WEIGHTED COMPOSITE ---
    weights = {
        'protest_activity': 0.30,
        'economic_pressure': 0.25,
        'security_response': 0.20,
        'geopolitical_tension': 0.15,
        'internal_cohesion': 0.10
    }

    composite = sum(scores[k] * weights[k] for k in weights)
    stability_score = int(max(5, min(95, composite)))

    # Determine risk level
    if stability_score >= 70:
        risk_level = "STABLE - Low Risk"
    elif stability_score >= 50:
        risk_level = "MODERATE RISK - Elevated Tensions"
    elif stability_score >= 30:
        risk_level = "HIGH RISK - Significant Instability"
    else:
        risk_level = "CRITICAL - Severe Instability"

    # --- TREND TRACKING (via cache) ---
    today = datetime.now().strftime("%Y-%m-%d")
    trend_history = cache.get("stability_trend", [])

    # Add today's score if not already recorded
    if not trend_history or trend_history[-1].get("date") != today:
        trend_history.append({"date": today, "score": stability_score})
        # Keep last 30 days
        trend_history = trend_history[-30:]
        cache["stability_trend"] = trend_history

    # Calculate trend
    if len(trend_history) >= 2:
        recent = trend_history[-1]["score"]
        prev = trend_history[-2]["score"]
        diff = recent - prev
        if diff > 3:
            trend = "increasing"
        elif diff < -3:
            trend = "decreasing"
        else:
            trend = "stable"
    else:
        trend = "stable"

    trend_day_count = len(trend_history)

    return {
        "stability_score": stability_score,
        "risk_level": risk_level,
        "trend": trend,
        "trend_day_count": trend_day_count,
        "components": scores,
        "weights": weights,
        "composite_raw": round(composite, 1),
        "methodology": "Multi-component: Protest(30%) + Economic(25%) + Security(20%) + Geopolitical(15%) + Cohesion(10%)"
    }


# ============================================
# ENHANCED CASUALTIES (7d/30d/cumulative)
# ============================================
def build_enhanced_casualties(casualties, hrana_data, cache):
    """
    Build the casualties_enhanced object the frontend expects:
    recent_7d, recent_30d, trends, cumulative, averages
    """
    today = datetime.now().strftime("%Y-%m-%d")
    casualty_history = cache.get("casualty_history", [])

    # Record today's data point
    today_entry = {
        "date": today,
        "deaths": casualties.get('deaths', 0),
        "injuries": casualties.get('injuries', 0),
        "arrests": casualties.get('arrests', 0)
    }
    if not casualty_history or casualty_history[-1].get("date") != today:
        casualty_history.append(today_entry)
        casualty_history = casualty_history[-60:]
        cache["casualty_history"] = casualty_history

    # HRANA cumulative (these are since Sept 2022)
    cumulative_deaths = hrana_data.get('confirmed_deaths', 0) or 0
    cumulative_injuries = hrana_data.get('seriously_injured', 0) or 0
    cumulative_arrests = hrana_data.get('total_arrests', 0) or 0

    # Fallback cumulatives if HRANA didn't find structured data
    if cumulative_deaths == 0:
        cumulative_deaths = "1,500+"
    if cumulative_injuries == 0:
        cumulative_injuries = "Unknown"
    if cumulative_arrests == 0:
        cumulative_arrests = "30,000+"

    # Calculate trends from history
    def calc_trend(key):
        if len(casualty_history) < 2:
            return 0
        recent = casualty_history[-1].get(key, 0)
        prev = casualty_history[-2].get(key, 0)
        if prev == 0:
            return 0
        return round(((recent - prev) / prev) * 100, 1) if prev > 0 else 0

    # Weeks since Sept 16, 2022 (Mahsa Amini protests start)
    weeks_since = max(1, (datetime.now() - datetime(2022, 9, 16)).days / 7)

    return {
        "recent_7d": {
            "deaths": casualties.get('deaths', 0),
            "injuries": casualties.get('injuries', 0),
            "arrests": casualties.get('arrests', 0)
        },
        "recent_30d": {
            "deaths": casualties.get('deaths', 0) * 4,
            "injuries": casualties.get('injuries', 0) * 4,
            "arrests": casualties.get('arrests', 0) * 4
        },
        "trends": {
            "deaths": calc_trend('deaths'),
            "injuries": calc_trend('injuries'),
            "arrests": calc_trend('arrests')
        },
        "cumulative": {
            "deaths": cumulative_deaths,
            "injuries": cumulative_injuries,
            "arrests": cumulative_arrests
        },
        "averages": {
            "deaths_per_week": round(cumulative_deaths / weeks_since, 1) if isinstance(cumulative_deaths, (int, float)) else "--",
            "injuries_per_week": "--",
            "arrests_per_week": round(cumulative_arrests / weeks_since, 1) if isinstance(cumulative_arrests, (int, float)) else "--"
        },
        "hrana_verified": hrana_data.get('is_hrana_verified', False)
    }


# ============================================
# MAIN SCAN ENDPOINT
# ============================================
def scan_iran_protests_handler():
    """
    Main handler for /scan-iran-protests endpoint
    Returns all data the Iran dashboard needs
    """
    try:
        days = int(request.args.get('days', 7))

        # Load persistent cache
        cache = load_cache()

        # Check if we have fresh cached response (< 25 min old)
        last_fetch = cache.get("last_full_fetch")
        if last_fetch:
            try:
                fetch_time = datetime.fromisoformat(last_fetch)
                age_minutes = (datetime.now() - fetch_time).total_seconds() / 60
                if age_minutes < 25 and "cached_response" in cache:
                    print(f"[Iran] Serving cached response ({age_minutes:.0f}min old)")
                    cached = cache["cached_response"]
                    cached["cached"] = True
                    cached["cache_age_minutes"] = round(age_minutes, 1)
                    return jsonify(cached)
            except:
                pass

        print("[Iran] === Starting fresh data fetch ===")

        # --- Fetch all data sources ---
        newsapi_articles = []
        try:
            newsapi_articles = fetch_newsapi_articles('Iran protests OR Iran demonstrations OR Iran unrest', days)
        except Exception as e:
            print(f"NewsAPI error: {e}")

        gdelt_query = 'iran protest OR iran demonstrations OR iran unrest OR iran crisis'
        gdelt_en = fetch_gdelt_articles(gdelt_query, days, 'eng')
        gdelt_ar = fetch_gdelt_articles(gdelt_query, days, 'ara')
        gdelt_fa = fetch_gdelt_articles(gdelt_query, days, 'fas')
        gdelt_he = fetch_gdelt_articles(gdelt_query, days, 'heb')

        reddit_posts = fetch_reddit_posts(days)
        iranwire_articles = fetch_iranwire_rss()
        hrana_articles = fetch_hrana_rss()

        all_articles = (newsapi_articles + gdelt_en + gdelt_ar + gdelt_fa +
                        gdelt_he + reddit_posts + iranwire_articles + hrana_articles)
        print(f"[Iran] Total articles: {len(all_articles)}")

        # --- Extract structured data ---
        hrana_data = extract_hrana_structured_data(hrana_articles)
        casualties_regex = extract_casualty_data(all_articles)

        # Merge: HRANA priority over regex
        if hrana_data['is_hrana_verified']:
            casualties = {
                'deaths': max(hrana_data['confirmed_deaths'], casualties_regex['deaths']),
                'deaths_under_investigation': hrana_data['deaths_under_investigation'],
                'injuries': max(hrana_data['seriously_injured'], casualties_regex['injuries']),
                'arrests': max(hrana_data['total_arrests'], casualties_regex['arrests']),
                'sources': list(set(['HRANA (verified)'] + casualties_regex['sources'])),
                'details': casualties_regex['details'],
                'hrana_verified': True,
                'hrana_source': hrana_data['source_article'],
                'hrana_updated': hrana_data['last_updated']
            }
        else:
            casualties = casualties_regex
            casualties['hrana_verified'] = False

        # --- Dynamic city extraction ---
        cities = extract_cities_from_articles(all_articles)
        num_cities = len(cities) if cities else (hrana_data.get('cities_affected') or 0)

        # --- Economic data ---
        exchange_rate = get_exchange_rate()
        oil_data = get_brent_oil_price()
        reserves = get_iran_oil_reserves()
        production_status = get_iran_oil_production_status(all_articles)

        # --- Regime Stability Index ---
        stability = calculate_regime_stability(
            all_articles, casualties, exchange_rate, oil_data,
            num_cities, hrana_data, cache
        )

        # --- Enhanced casualties (7d/30d/cumulative/trends) ---
        casualties_enhanced = build_enhanced_casualties(casualties, hrana_data, cache)

        # --- Government status ---
        government = get_government_status()

        # --- Calculate intensity (simple metric for backward compat) ---
        articles_per_day = len(all_articles) / days if days > 0 else 0
        intensity_score = min(
            articles_per_day * 2 + num_cities * 4 +
            casualties['deaths'] * 0.5 + casualties['injuries'] * 0.2 +
            casualties['arrests'] * 0.1,
            100
        )

        # --- Build response ---
        response_data = {
            'success': True,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'days_analyzed': days,
            'total_articles': len(all_articles),
            'intensity': int(intensity_score),

            # NEW: Multi-component stability
            'regime_stability': stability,

            # Exchange rate (NEW - was missing)
            'exchange_rate': exchange_rate,

            # Casualties (original format for backward compat)
            'casualties': {
                'deaths': casualties.get('deaths', 0),
                'deaths_under_investigation': casualties.get('deaths_under_investigation', 0),
                'injuries': casualties.get('injuries', 0),
                'arrests': casualties.get('arrests', 0),
                'verified_sources': casualties.get('sources', []),
                'details': casualties.get('details', []),
                'hrana_verified': casualties.get('hrana_verified', False),
                'hrana_source': casualties.get('hrana_source'),
                'hrana_updated': casualties.get('hrana_updated')
            },

            # NEW: Enhanced casualties (what frontend expects)
            'casualties_enhanced': casualties_enhanced,

            # Dynamic cities (was hardcoded)
            'cities': cities,
            'num_cities_affected': num_cities,

            # Oil data (moved from separate endpoint)
            'oil_data': {
                'oil_price': oil_data,
                'reserves': reserves,
                'sparkline': {
                    'success': True,
                    'data': oil_data.get('sparkline', []),
                    'source': 'alpha_vantage' if oil_data.get('sparkline') else 'unavailable'
                },
                'production_status': production_status
            },

            # NEW: Government status card
            'government': government,

            # Articles by language
            'articles_en': [a for a in all_articles if a.get('language') == 'en'
                            and not a.get('source', {}).get('name', '').startswith('r/')][:20],
            'articles_fa': [a for a in all_articles if a.get('language') == 'fa'][:20],
            'articles_ar': [a for a in all_articles if a.get('language') == 'ar'][:20],
            'articles_he': [a for a in all_articles if a.get('language') == 'he'][:20],
            'articles_reddit': [a for a in all_articles
                                if a.get('source', {}).get('name', '').startswith('r/')][:20],
            'articles_iranwire': iranwire_articles[:20],
            'articles_hrana': hrana_articles[:20],

            'cached': False,
            'version': '3.0.0'
        }

        # Save to cache
        cache["cached_response"] = response_data
        cache["last_full_fetch"] = datetime.now().isoformat()
        save_cache(cache)

        return jsonify(response_data)

    except Exception as e:
        print(f"[Iran] ERROR in scan_iran_protests: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e),
            'intensity': 0,
            'regime_stability': {'stability_score': 0, 'risk_level': 'Error', 'trend': 'unknown'},
            'casualties': {'deaths': 0, 'injuries': 0, 'arrests': 0, 'sources': [],
                           'details': [], 'hrana_verified': False},
            'casualties_enhanced': None,
            'exchange_rate': {},
            'oil_data': {},
            'government': {},
            'cities': [],
            'num_cities_affected': 0,
            'articles_en': [], 'articles_fa': [], 'articles_ar': [],
            'articles_he': [], 'articles_reddit': [],
            'articles_iranwire': [], 'articles_hrana': [],
            'total_articles': 0
        }), 500


# ============================================
# OIL DATA ENDPOINT (backward compat)
# ============================================
def get_iran_oil_data_handler():
    """Handler for /api/iran-oil-data endpoint (backward compatible)"""
    try:
        oil_price = get_brent_oil_price()
        reserves = get_iran_oil_reserves()
        production_status = get_iran_oil_production_status()

        return jsonify({
            "success": True,
            "oil_price": oil_price,
            "reserves": reserves,
            "sparkline": {
                "success": True,
                "data": oil_price.get("sparkline", []),
                "source": "alpha_vantage" if oil_price.get("sparkline") else "unavailable"
            },
            "production_status": production_status,
            "timestamp": datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ============================================
# FLASK ROUTE REGISTRATION
# ============================================
def register_iran_routes(app):
    """
    Register Iran routes with Flask app.
    Call this from app.py: register_iran_routes(app)
    """
    @app.route('/scan-iran-protests', methods=['GET'])
    def scan_iran_protests():
        return scan_iran_protests_handler()

    @app.route('/api/iran-oil-data', methods=['GET'])
    def iran_oil_data():
        return get_iran_oil_data_handler()

    print("[Iran] Routes registered: /scan-iran-protests, /api/iran-oil-data")
