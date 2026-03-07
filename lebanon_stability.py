"""
Lebanon Stability Backend v2.9.0
Standalone microservice for Lebanon Stability Index

CHANGELOG v2.9.0:
- FIXED: Cache now uses Upstash Redis (persistent!) instead of /tmp (ephemeral)
- FIXED: Expanded Hezbollah activity keyword matchingstability 
- FIXED: Currency 24h change now compares to yesterday's cached rate
- FIXED: beautifulsoup4 import moved to top level with graceful fallback
- ADDED: /api/bey-flights stub endpoint for future Aviation Edge integration

Tracks:
- Currency collapse (LBP/USD)
- Bond yields (Eurobonds)
- Hezbollah activity
- Gold reserves valuation
- Political stability
"""

from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
import json
import os
import time
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Rhetoric Tracker (lightweight — reads cached data only, no scan thread)
try:
    from rhetoric_tracker import register_rhetoric_endpoints
    RHETORIC_AVAILABLE = True
    print("[Lebanon] ✅ Rhetoric tracker module loaded")
except ImportError:
    RHETORIC_AVAILABLE = False
    print("[Lebanon] ⚠️ Rhetoric tracker not available (rhetoric_tracker.py not found)")
    
# Optional: BeautifulSoup for bond scraping
try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False
    print("[WARNING] beautifulsoup4 not installed - bond scraping will use fallback")

# ========================================
# FLASK APP INITIALIZATION
# ========================================
app = Flask(__name__)

# CORS Configuration
CORS(app, resources={
    r"/scan-lebanon-stability": {
        "origins": [
            "https://asifahanalytics.com",
            "https://www.asifahanalytics.com",
            "http://localhost:*"
        ],
        "methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["Content-Type"]
    },
    r"/api/*": {
        "origins": [
            "https://asifahanalytics.com",
            "https://www.asifahanalytics.com",
            "http://localhost:*"
        ],
        "methods": ["GET", "OPTIONS"],
        "allow_headers": ["Content-Type"]
    },
    r"/health": {
        "origins": [
            "https://asifahanalytics.com",
            "https://www.asifahanalytics.com",
            "http://localhost:*"
        ],
        "methods": ["GET", "OPTIONS"],
        "allow_headers": ["Content-Type"]
    }
})

# Register rhetoric endpoints (cache-read only — no background scan thread)
if RHETORIC_AVAILABLE:
    register_rhetoric_endpoints(app)
    
# ========================================
# CONFIGURATION
# ========================================

# Upstash Redis (persistent cache - replaces /tmp file!)
UPSTASH_URL = os.environ.get('UPSTASH_REDIS_REST_URL')
UPSTASH_TOKEN = os.environ.get('UPSTASH_REDIS_REST_TOKEN')

REDIS_CACHE_KEY = 'lebanon_cache'

# Fallback to /tmp only if Redis is not configured
CACHE_FILE = '/tmp/lebanon_stability_cache.json'

# ========================================
# RATE LIMITING
# ========================================

RATE_LIMIT = 100
RATE_LIMIT_WINDOW = 86400  # 24 hours
rate_limit_data = {
    'requests': 0,
    'reset_time': time.time() + RATE_LIMIT_WINDOW
}

def check_rate_limit():
    """Check if rate limit has been exceeded"""
    global rate_limit_data
    
    current_time = time.time()
    
    if current_time >= rate_limit_data['reset_time']:
        rate_limit_data['requests'] = 0
        rate_limit_data['reset_time'] = current_time + RATE_LIMIT_WINDOW
    
    if rate_limit_data['requests'] >= RATE_LIMIT:
        return False
    
    rate_limit_data['requests'] += 1
    return True

# ========================================
# GOLD PRICE FETCHING (CASCADING FALLBACK)
# ========================================

def fetch_gold_goldprice_org():
    """Try GoldPrice.org JSON endpoint (FREE, no auth required)"""
    try:
        print("[Gold Price] Trying GoldPrice.org...")
        
        url = "https://data-asg.goldprice.org/dbXRates/USD"
        
        response = requests.get(url, timeout=10, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        
        if response.status_code != 200:
            print(f"[Gold Price] GoldPrice.org HTTP error: {response.status_code}")
            return None
        
        data = response.json()
        
        if 'items' in data and len(data['items']) > 0:
            price = float(data['items'][0].get('xauPrice', 0))
            
            if price > 1000 and price < 10000:
                print(f"[Gold Price] ✅ GoldPrice.org: ${price:.2f}/oz")
                
                return {
                    'price': round(price, 2),
                    'currency': 'USD',
                    'unit': 'troy_oz',
                    'source': 'GoldPrice.org',
                    'timestamp': datetime.now(timezone.utc).isoformat()
                }
        
        return None
        
    except Exception as e:
        print(f"[Gold Price] GoldPrice.org error: {str(e)[:100]}")
        return None


def fetch_gold_price():
    """
    Fetch spot gold price with fallback
    Returns price per troy ounce in USD
    """
    print("[Gold Price] Starting cascade...")
    
    # Try GoldPrice.org
    result = fetch_gold_goldprice_org()
    if result:
        return result
    
    # Fallback estimate
    print("[Gold Price] ❌ All APIs failed, using fallback estimate")
    
    fallback_price = 2750
    
    return {
        'price': fallback_price,
        'currency': 'USD',
        'unit': 'troy_oz',
        'source': 'Estimated',
        'estimated': True,
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'note': 'Approximate - gold APIs unavailable'
    }


def calculate_lebanon_gold_reserves():
    """
    Calculate current market value of Lebanon's gold reserves
    
    Lebanon holds 286.8 metric tons (9.22 million troy oz)
    Returns current USD valuation based on spot gold price
    """
    try:
        print("[Lebanon Gold] Calculating reserve value...")
        
        LEBANON_GOLD_TONS = 286.8
        TROY_OZ_PER_METRIC_TON = 32150.7466
        
        total_troy_oz = LEBANON_GOLD_TONS * TROY_OZ_PER_METRIC_TON
        
        print(f"[Lebanon Gold] Holdings: {LEBANON_GOLD_TONS} tons = {total_troy_oz:,.0f} troy oz")
        
        gold_price_data = fetch_gold_price()
        
        if gold_price_data:
            price_per_oz = gold_price_data['price']
            
            total_value_usd = total_troy_oz * price_per_oz
            total_value_billions = total_value_usd / 1_000_000_000
            
            LEBANON_GDP_BILLIONS = 20
            gdp_percentage = (total_value_billions / LEBANON_GDP_BILLIONS) * 100
            
            print(f"[Lebanon Gold] Value: ${total_value_billions:.1f}B @ ${price_per_oz:.2f}/oz")
            
            return {
                'tons': LEBANON_GOLD_TONS,
                'troy_ounces': int(total_troy_oz),
                'price_per_oz': price_per_oz,
                'total_value_usd': int(total_value_usd),
                'total_value_billions': round(total_value_billions, 1),
                'display_value': f"${total_value_billions:.1f}B",
                'gdp_percentage': int(gdp_percentage),
                'source': gold_price_data.get('source', 'Unknown'),
                'last_updated': gold_price_data.get('timestamp', ''),
                'estimated': gold_price_data.get('estimated', False),
                'rank_middle_east': 2,
                'protected_by_law': True,
                'law_year': 1986,
                'held_since': '1960s',
                'note': '60% in Beirut vaults, 40% in USA'
            }
        
        print("[Lebanon Gold] Using estimated value (price fetch failed)")
        
        return {
            'tons': LEBANON_GOLD_TONS,
            'troy_ounces': int(total_troy_oz),
            'total_value_billions': 45,
            'display_value': '~$40-50B',
            'estimated': True,
            'note': 'Value estimated - gold price unavailable',
            'rank_middle_east': 2,
            'protected_by_law': True
        }
        
    except Exception as e:
        print(f"[Lebanon Gold] ❌ Error: {str(e)}")
        
        return {
            'tons': 286.8,
            'display_value': '~$40-50B',
            'estimated': True,
            'error': str(e)[:100]
        }

# ========================================
# CURRENCY FETCHING
# ========================================

def fetch_lebanon_currency():
    """Fetch LBP/USD rate with 24h change from cache"""
    try:
        print("[Lebanon Currency] Fetching LBP/USD...")
        
        url = "https://open.exchangerate-api.com/v6/latest/USD"
        response = requests.get(url, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            current_rate = data.get('rates', {}).get('LBP')
            
            if current_rate:
                print(f"[Lebanon Currency] ✅ Current USD/LBP: {current_rate:,.0f}")
                
                # v2.9.0: Compare to yesterday's cached rate for real 24h change
                yesterday_rate = current_rate  # Default: no change
                try:
                    cache = load_lebanon_cache()
                    history = cache.get('history', {})
                    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
                    cached_yesterday = history.get(yesterday, {})
                    if cached_yesterday.get('currency_rate', 0) > 0:
                        yesterday_rate = cached_yesterday['currency_rate']
                        print(f"[Lebanon Currency] Yesterday's rate from cache: {yesterday_rate:,.0f}")
                except Exception as cache_err:
                    print(f"[Lebanon Currency] Cache lookup failed (using current as baseline): {str(cache_err)[:80]}")
                
                # Calculate actual change
                if yesterday_rate > 0:
                    estimated_change = ((current_rate - yesterday_rate) / yesterday_rate) * 100
                else:
                    estimated_change = 0
                
                if estimated_change > 0.1:
                    trend = "weakening"
                    pressure = "SELLING"
                elif estimated_change < -0.1:
                    trend = "strengthening"
                    pressure = "BUYING"
                else:
                    trend = "stable"
                    pressure = "NEUTRAL"
                
                return {
                    'usd_to_lbp': current_rate,
                    'official_rate': 1500,
                    'devaluation_pct': ((current_rate - 1500) / 1500) * 100,
                    'last_updated': data.get('time_last_update_utc', ''),
                    'source': 'ExchangeRate-API',
                    'change_24h': round(estimated_change, 2),
                    'trend': trend,
                    'pressure': pressure,
                    'yesterday_rate': yesterday_rate
                }
        
        print("[Lebanon Currency] Using estimated rate")
        return {
            'usd_to_lbp': 90000,
            'official_rate': 1500,
            'devaluation_pct': ((90000 - 1500) / 1500) * 100,
            'source': 'Estimated',
            'last_updated': datetime.now(timezone.utc).isoformat(),
            'change_24h': 0,
            'trend': 'stable',
            'pressure': 'NEUTRAL'
        }
        
    except Exception as e:
        print(f"[Lebanon Currency] ❌ Error: {str(e)[:200]}")
        return None

# ========================================
# BOND SCRAPING
# ========================================

def scrape_lebanon_bonds():
    """
    Enhanced Lebanon bond/debt tracking (v2.9.1)
    
    Tracks yield, bond price (cents/$), restructuring status,
    and debt-related news via Google News RSS.
    """
    try:
        print("[Lebanon Bonds] Starting enhanced scan...")
        
        # ── 1. Try to scrape yield from WorldGovernmentBonds.com ──
        bond_yield = None
        yield_source = 'Estimated'
        
        if BS4_AVAILABLE:
            try:
                url = "https://www.worldgovernmentbonds.com/country/lebanon/"
                response = requests.get(url, timeout=10, headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                })
                if response.status_code == 200:
                    soup = BeautifulSoup(response.content, 'html.parser')
                    # Look for yield data in the page
                    for elem in soup.find_all(['td', 'span', 'div']):
                        text = elem.get_text().strip()
                        match = re.search(r'(\d+\.?\d+)\s*%', text)
                        if match:
                            val = float(match.group(1))
                            if 10 <= val <= 200:  # Reasonable range for Lebanon
                                bond_yield = val
                                yield_source = 'WorldGovernmentBonds.com'
                                print(f"[Lebanon Bonds] ✅ WGB yield: {bond_yield}%")
                                break
            except Exception as e:
                print(f"[Lebanon Bonds] WGB scrape failed: {str(e)[:80]}")
        
        # Fallback: try Investing.com
        if bond_yield is None and BS4_AVAILABLE:
            try:
                url = "https://www.investing.com/rates-bonds/lebanon-10-year-bond-yield"
                response = requests.get(url, timeout=10, headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                })
                if response.status_code == 200:
                    soup = BeautifulSoup(response.content, 'html.parser')
                    value_elem = soup.find('span', {'data-test': 'instrument-price-last'})
                    if value_elem:
                        text = value_elem.get_text().strip()
                        match = re.search(r'(\d+\.?\d*)', text)
                        if match:
                            val = float(match.group(1))
                            if 10 <= val <= 200:
                                bond_yield = val
                                yield_source = 'Investing.com'
                                print(f"[Lebanon Bonds] ✅ Investing.com yield: {bond_yield}%")
            except Exception as e:
                print(f"[Lebanon Bonds] Investing.com failed: {str(e)[:80]}")
        
        # Final fallback
        if bond_yield is None:
            bond_yield = 45.0
            yield_source = 'Estimated'
            print("[Lebanon Bonds] Using fallback yield: 45.0%")
        
        # ── 2. Default timeline ──
        default_date = datetime(2020, 3, 9, tzinfo=timezone.utc)
        days_in_default = (datetime.now(timezone.utc) - default_date).days
        
        # ── 3. Bond price from cache comparison ──
        # Track the price trend via cached yield data
        yesterday_yield = bond_yield
        try:
            cache = load_lebanon_cache()
            history = cache.get('history', {})
            yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
            cached = history.get(yesterday, {})
            if cached.get('bond_yield', 0) > 0:
                yesterday_yield = cached['bond_yield']
        except:
            pass
        
        yield_change = round(bond_yield - yesterday_yield, 1)
        if yield_change > 0.5:
            yield_trend = 'rising'
            yield_arrow = '↑'
        elif yield_change < -0.5:
            yield_trend = 'falling'
            yield_arrow = '↓'
        else:
            yield_trend = 'stable'
            yield_arrow = '→'
        
        # ── 4. Restructuring news scanner ──
        restructuring_articles = []
        restructuring_keywords = [
            'Lebanon Eurobond restructuring',
            'Lebanon IMF program',
            'Lebanon creditors debt'
        ]
        
        for keyword in restructuring_keywords:
            try:
                import xml.etree.ElementTree as ET
                query = keyword.replace(' ', '+')
                url = f"https://news.google.com/rss/search?q={query}&hl=en&gl=US&ceid=US:en"
                response = requests.get(url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
                if response.status_code == 200:
                    root = ET.fromstring(response.content)
                    items = root.findall('.//item')
                    for item in items[:3]:
                        title_elem = item.find('title')
                        link_elem = item.find('link')
                        pub_elem = item.find('pubDate')
                        if title_elem is not None:
                            # Filter to last 30 days
                            include = True
                            pub_str = pub_elem.text if pub_elem is not None else ''
                            if pub_str:
                                try:
                                    from email.utils import parsedate_to_datetime
                                    pub_date = parsedate_to_datetime(pub_str)
                                    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
                                    if pub_date < cutoff:
                                        include = False
                                except:
                                    pass
                            if include:
                                restructuring_articles.append({
                                    'title': title_elem.text or '',
                                    'url': link_elem.text if link_elem is not None else '',
                                    'published': pub_str,
                                    'keyword': keyword
                                })
            except:
                continue
        
        # Deduplicate by title
        seen_titles = set()
        unique_articles = []
        for a in restructuring_articles:
            if a['title'] not in seen_titles:
                seen_titles.add(a['title'])
                unique_articles.append(a)
        restructuring_articles = unique_articles[:5]
        
        # ── 5. Determine restructuring status ──
        has_imf_news = any('imf' in a['title'].lower() for a in restructuring_articles)
        has_creditor_news = any('creditor' in a['title'].lower() or 'restructur' in a['title'].lower() for a in restructuring_articles)
        
        if has_imf_news and has_creditor_news:
            restructuring_status = 'Active Talks'
            restructuring_icon = '🟡'
        elif has_imf_news or has_creditor_news:
            restructuring_status = 'Early Stages'
            restructuring_icon = '🟠'
        else:
            restructuring_status = 'Stalled'
            restructuring_icon = '🔴'
        
        print(f"[Lebanon Bonds] Yield: {bond_yield}% | Default: {days_in_default}d | Restructuring: {restructuring_status}")
        print(f"[Lebanon Bonds] Found {len(restructuring_articles)} restructuring articles")
        
        return {
            'yield': bond_yield,
            'source': yield_source,
            'date': datetime.now(timezone.utc).isoformat(),
            'note': 'Distressed debt (defaulted March 2020)',
            # Enhanced fields (v2.9.1)
            'yield_change': yield_change,
            'yield_trend': yield_trend,
            'yield_arrow': yield_arrow,
            'default_date': '2020-03-09',
            'days_in_default': days_in_default,
            'eurobond_price_cents': 28.5,  # Latest known price — TODO: scrape dynamically
            'price_note': 'Last updated from market reports',
            'implied_recovery_pct': 28.5,
            'comparison': {
                'argentina_post_default': 35,
                'sri_lanka': 50,
                'venezuela': 8,
                'lebanon': 28.5
            },
            'restructuring': {
                'status': restructuring_status,
                'icon': restructuring_icon,
                'imf_target': 'End-2026',
                'gs_base_case': '28c recovery',
                'gs_bull_case': '40c recovery',
                'gs_bear_case': '17c recovery',
                'articles': restructuring_articles
            },
            'total_defaulted_debt_billions': 30,
            'litigation_deadline': '2025 (NY courts 5-year limit)'
        }
        
    except Exception as e:
        print(f"[Lebanon Bonds] ❌ Error: {str(e)[:200]}")
        return {
            'yield': 45.0,
            'source': 'Estimated',
            'date': datetime.now(timezone.utc).isoformat(),
            'note': 'Estimated - Lebanon defaulted March 2020',
            'yield_change': 0,
            'yield_trend': 'stable',
            'yield_arrow': '→',
            'default_date': '2020-03-09',
            'days_in_default': (datetime.now(timezone.utc) - datetime(2020, 3, 9, tzinfo=timezone.utc)).days,
            'restructuring': {
                'status': 'Unknown',
                'icon': '⚪'
            }
        }

# ========================================
# HEZBOLLAH ACTIVITY TRACKING
# ========================================

def track_hezbollah_activity(days=7):
    """Track Hezbollah rearmament indicators with expanded keyword matching"""
    try:
        print("[Hezbollah] Scanning activity...")
        
        keywords = [
            'Hezbollah rearmament',
            'Hezbollah weapons',
            'Israeli strike Lebanon',
            'UNIFIL Lebanon',
            'Iran weapons Lebanon'
        ]
        
        all_articles = []
        
        for keyword in keywords:
            try:
                query = keyword.replace(' ', '+')
                url = f"https://news.google.com/rss/search?q={query}&hl=en&gl=US&ceid=US:en"
                
                response = requests.get(url, timeout=10, headers={
                    'User-Agent': 'Mozilla/5.0'
                })
                
                if response.status_code == 200:
                    import xml.etree.ElementTree as ET
                    
                    root = ET.fromstring(response.content)
                    items = root.findall('.//item')
                    
                    for item in items[:5]:
                        title_elem = item.find('title')
                        link_elem = item.find('link')
                        pubDate_elem = item.find('pubDate')
                        
                        if title_elem is not None:
                            # Filter: only include articles within the date range
                            include = True
                            pub_date_str = pubDate_elem.text if pubDate_elem is not None else ''
                            if pub_date_str:
                                try:
                                    from email.utils import parsedate_to_datetime
                                    pub_date = parsedate_to_datetime(pub_date_str)
                                    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
                                    if pub_date < cutoff:
                                        include = False
                                except:
                                    pass  # If we can't parse the date, include it
                            
                            if include:
                                all_articles.append({
                                    'title': title_elem.text or '',
                                    'url': link_elem.text if link_elem is not None else '',
                                    'published': pub_date_str,
                                    'keyword': keyword
                                })
            except:
                continue
        
        # v2.9.0: Expanded keyword matching for more accurate scoring
        rearmament_keywords = [
            'rearm', 'weapon', 'missile', 'rocket', 'arsenal', 'munition',
            'smuggl', 'arms transfer', 'military capabilit', 'drone',
            'precision guided', 'weapons depot', 'arms shipment', 'iranian supply'
        ]
        strike_keywords = [
            'strike', 'bomb', 'attack', 'shell', 'raid', 'airstrike',
            'operation', 'offensive', 'idf', 'incursion', 'bombardment',
            'targeted killing', 'air raid', 'military operation'
        ]
        
        rearmament_count = sum(1 for a in all_articles 
            if any(kw in a['title'].lower() for kw in rearmament_keywords))
        strike_count = sum(1 for a in all_articles 
            if any(kw in a['title'].lower() for kw in strike_keywords))
        
        activity_score = min((rearmament_count * 5 + strike_count * 3), 100)
        
        print(f"[Hezbollah] Rearmament mentions: {rearmament_count}, Strike mentions: {strike_count}")
        print(f"[Hezbollah] Activity score: {activity_score}/100")
        
        return {
            'articles': all_articles[:20],
            'total_articles': len(all_articles),
            'rearmament_mentions': rearmament_count,
            'strike_mentions': strike_count,
            'activity_score': activity_score
        }
        
    except Exception as e:
        print(f"[Hezbollah] Error: {str(e)[:200]}")
        return {
            'articles': [],
            'total_articles': 0,
            'rearmament_mentions': 0,
            'strike_mentions': 0,
            'activity_score': 0
        }

"""
Lebanon Security Situation Scanner v3.0.0
New module for lebanon_stability.py

Adds dynamic scanning for:
1. Israeli Military Presence (tiered)
2. Ceasefire Status (dynamic, replaces hardcoded True)
3. UNIFIL Status
4. Hezbollah Leadership Status

Paste this ENTIRE block into lebanon_stability.py
ABOVE the existing calculate_lebanon_stability() function.
"""

# ========================================
# SECURITY SITUATION SCANNER (v3.0.0)
# ========================================

# Israeli military presence tier keywords
ISRAEL_PRESENCE_TIERS = {
    'full_invasion': {
        'keywords': [
            'full ground invasion lebanon', 'full scale invasion lebanon',
            'idf invades lebanon', 'israel invades lebanon',
            'ground invasion southern lebanon', 'israeli invasion of lebanon',
            'occupation of southern lebanon', 'idf occupies lebanon',
            'israeli troops advance deep into lebanon',
            'idf pushes north of litani', 'litani river crossing',
        ],
        'level': 4,
        'label': 'Full Ground Invasion',
        'color': '#dc2626',
        'badge_color': '#991b1b',
        'description': 'Large-scale IDF ground operations across southern Lebanon'
    },
    'active_ground_ops': {
        'keywords': [
            'idf ground operation lebanon', 'israeli ground troops lebanon',
            'idf operating inside lebanon', 'ground incursion lebanon',
            'israeli forces entered lebanon', 'idf troops in lebanon',
            'idf raids southern lebanon', 'armored vehicles lebanon',
            'idf buffer zone lebanon', 'israeli troops southern lebanon',
            'ground operation south lebanon', 'idf soldiers lebanon',
            'tank lebanon border', 'idf patrol inside lebanon',
            'idf engineering lebanon', 'tunnel operation lebanon',
            'limited ground operation lebanon', 'ground maneuver lebanon',
        ],
        'level': 3,
        'label': 'Active Ground Operations',
        'color': '#ea580c',
        'badge_color': '#9a3412',
        'description': 'IDF conducting ground operations in southern Lebanon border zone'
    },
    'limited_incursions': {
        'keywords': [
            'cross border raid lebanon', 'limited incursion lebanon',
            'idf brief incursion', 'special forces lebanon',
            'commando raid lebanon', 'targeted raid lebanon',
            'idf crossed border lebanon', 'border skirmish lebanon',
        ],
        'level': 2,
        'label': 'Limited Incursions',
        'color': '#f59e0b',
        'badge_color': '#92400e',
        'description': 'Periodic IDF cross-border raids and special operations'
    },
    'no_presence': {
        'keywords': [],
        'level': 1,
        'label': 'No Ground Presence',
        'color': '#10b981',
        'badge_color': '#065f46',
        'description': 'No reported IDF ground forces inside Lebanon'
    }
}

# Ceasefire status keywords
CEASEFIRE_KEYWORDS = {
    'collapsed': [
        'ceasefire collapsed', 'ceasefire broken', 'ceasefire violated',
        'ceasefire ended', 'ceasefire over', 'end of ceasefire',
        'ceasefire falls apart', 'no longer ceasefire',
        'resumed hostilities lebanon', 'resumed fighting lebanon',
        'war resumes lebanon', 'full scale war lebanon israel',
        'israel breaks ceasefire lebanon', 'ceasefire dead',
        'ceasefire in tatters', 'ceasefire crumbled',
    ],
    'deteriorating': [
        'ceasefire violations', 'ceasefire under strain',
        'ceasefire shaky', 'ceasefire fragile', 'ceasefire tested',
        'ceasefire at risk', 'threatens ceasefire',
        'ceasefire breaches', 'repeated violations',
        'ceasefire hanging by thread', 'despite ceasefire',
        'ceasefire increasingly fragile',
    ],
    'holding': [
        'ceasefire holds', 'ceasefire holding', 'ceasefire intact',
        'ceasefire maintained', 'respecting ceasefire',
        'ceasefire in place', 'calm along border',
        'ceasefire stable', 'ceasefire observed',
    ]
}

# UNIFIL status keywords
UNIFIL_KEYWORDS = {
    'withdrawn': [
        'unifil withdrawn', 'unifil leaves', 'unifil departed',
        'unifil pullout complete', 'unifil withdrawal complete',
        'peacekeepers left lebanon', 'unifil mission ended',
    ],
    'withdrawing': [
        'unifil withdrawing', 'unifil drawdown', 'unifil pullout',
        'unifil withdrawal', 'unifil leaving', 'unifil departure',
        'unifil reduces', 'unifil scaling down',
        'peacekeepers withdrawing', 'unifil mandate expires',
        'unifil end of mission', 'unifil scheduled departure',
    ],
    'under_attack': [
        'unifil attacked', 'unifil hit', 'unifil struck',
        'unifil bunker hit', 'unifil base attacked',
        'unifil shelled', 'unifil personnel injured',
        'unifil peacekeepers killed', 'unifil casualties',
        'unifil convoy attacked', 'fired on unifil',
        'unifil position struck', 'attack on peacekeepers',
        'unifil targeted', 'unifil under fire',
    ],
    'operational': [
        'unifil patrol', 'unifil operational', 'unifil monitoring',
        'unifil presence', 'unifil mandate renewed',
        'unifil deployed', 'unifil blue line',
    ]
}

# Hezbollah leadership database
HEZBOLLAH_LEADERSHIP = {
    'naim_qassem': {
        'name': 'Naim Qassem',
        'title': 'Secretary-General',
        'title_ar': 'الأمين العام',
        'since': '2024-10-29',
        'note': 'Replaced Hassan Nasrallah after assassination Sep 27, 2024',
        'keywords_alive': ['naim qassem speech', 'qassem statement', 'qassem addresses',
                           'qassem says', 'qassem warns', 'qassem vows',
                           'hezbollah leader says', 'hezbollah chief'],
        'keywords_killed': ['naim qassem killed', 'qassem assassinated', 'qassem dead',
                            'qassem eliminated', 'hezbollah leader killed',
                            'hezbollah secretary general killed'],
        'keywords_unknown': ['qassem whereabouts', 'qassem missing', 'qassem unconfirmed'],
    },
    'hashem_safieddine': {
        'name': 'Hashem Safieddine',
        'title': 'Head of Executive Council',
        'title_ar': 'رئيس المجلس التنفيذي',
        'since': '2000-01-01',
        'note': 'Reported killed Oct 2024; status disputed',
        'keywords_alive': ['safieddine alive', 'safieddine speech', 'safieddine statement',
                           'safieddine appears', 'safieddine resurfaces'],
        'keywords_killed': ['safieddine killed', 'safieddine dead', 'safieddine eliminated',
                            'safieddine assassinated', 'safieddine struck'],
        'keywords_unknown': ['safieddine fate', 'safieddine status unknown', 'safieddine missing',
                             'safieddine unconfirmed'],
    },
    'ibrahim_aqil_successor': {
        'name': 'Radwan Force Commander',
        'title': 'Radwan Force (Elite Unit)',
        'title_ar': 'قائد قوة الرضوان',
        'since': None,
        'note': 'Ibrahim Aqil killed Sep 2024. Successor identity unclear.',
        'keywords_alive': ['radwan force commander', 'new radwan commander',
                           'radwan force operations', 'radwan force active'],
        'keywords_killed': ['radwan commander killed', 'radwan force decimated',
                            'radwan force destroyed', 'radwan leadership eliminated'],
        'keywords_unknown': ['radwan force status', 'radwan commander unknown',
                             'radwan force reorganizing'],
    },
    'fuad_shukr_successor': {
        'name': 'Senior Military Commander',
        'title': 'Military Operations Chief',
        'title_ar': 'رئيس العمليات العسكرية',
        'since': None,
        'note': 'Fuad Shukr killed Jul 2024. Replacement status unclear.',
        'keywords_alive': ['hezbollah military chief', 'hezbollah military commander',
                           'hezbollah military operations'],
        'keywords_killed': ['hezbollah commander killed', 'senior hezbollah military killed',
                            'hezbollah military chief eliminated'],
        'keywords_unknown': ['hezbollah military command uncertain', 'hezbollah command structure',
                             'hezbollah reorganizing military'],
    }
}


def scan_security_situation(days=7):
    """
    Scan news for Lebanon security situation indicators.
    Returns structured data for:
    - Israeli military presence tier
    - Ceasefire status
    - UNIFIL status
    - Hezbollah leadership status

    Uses Google News RSS (same pattern as existing Hezbollah activity tracker).
    """
    import xml.etree.ElementTree as ET

    print("[Security Situation] Starting scan...")

    # ── 1. Gather articles ──
    search_queries = [
        'Israel ground troops Lebanon',
        'IDF operation southern Lebanon',
        'ceasefire Lebanon Israel',
        'UNIFIL Lebanon',
        'UNIFIL attacked',
        'Hezbollah leadership',
        'Naim Qassem',
        'Lebanon border Israel military',
        'IDF buffer zone Lebanon',
        'Lebanon ceasefire violated OR collapsed OR broken',
    ]

    all_articles = []
    for query in search_queries:
        try:
            q = query.replace(' ', '+')
            url = f"https://news.google.com/rss/search?q={q}&hl=en&gl=US&ceid=US:en"
            response = requests.get(url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})

            if response.status_code == 200:
                root = ET.fromstring(response.content)
                items = root.findall('.//item')

                for item in items[:8]:
                    title_elem = item.find('title')
                    link_elem = item.find('link')
                    pub_elem = item.find('pubDate')

                    if title_elem is not None:
                        include = True
                        pub_str = pub_elem.text if pub_elem is not None else ''
                        if pub_str:
                            try:
                                from email.utils import parsedate_to_datetime
                                pub_date = parsedate_to_datetime(pub_str)
                                cutoff = datetime.now(timezone.utc) - timedelta(days=days)
                                if pub_date < cutoff:
                                    include = False
                            except:
                                pass

                        if include:
                            all_articles.append({
                                'title': title_elem.text or '',
                                'url': link_elem.text if link_elem is not None else '',
                                'published': pub_str,
                                'query': query
                            })
        except Exception as e:
            print(f"[Security Situation] RSS error for '{query}': {str(e)[:80]}")
            continue

    print(f"[Security Situation] Gathered {len(all_articles)} articles")

    # Build combined text corpus for matching
    all_titles_lower = ' '.join(a['title'].lower() for a in all_articles)

    # ── 2. Israeli Military Presence ──
    israel_presence = ISRAEL_PRESENCE_TIERS['no_presence'].copy()
    israel_presence['indicators'] = []

    for tier_key in ['full_invasion', 'active_ground_ops', 'limited_incursions']:
        tier = ISRAEL_PRESENCE_TIERS[tier_key]
        matched = []
        for kw in tier['keywords']:
            if kw in all_titles_lower:
                matched.append(kw)
                # Find the matching article
                for a in all_articles:
                    if kw in a['title'].lower():
                        israel_presence['indicators'].append({
                            'phrase': kw,
                            'title': a['title'][:120],
                            'url': a['url'],
                            'published': a['published']
                        })
                        break
        if matched:
            israel_presence = {
                'level': tier['level'],
                'label': tier['label'],
                'color': tier['color'],
                'badge_color': tier['badge_color'],
                'description': tier['description'],
                'matched_keywords': matched[:5],
                'indicators': israel_presence.get('indicators', [])[:5]
            }
            break  # Take the highest tier that matched

    print(f"[Security Situation] Israeli presence: {israel_presence['label']} (level {israel_presence['level']})")

    # ── 3. Ceasefire Status ──
    ceasefire_scores = {'collapsed': 0, 'deteriorating': 0, 'holding': 0}
    ceasefire_indicators = []

    for status, keywords in CEASEFIRE_KEYWORDS.items():
        for kw in keywords:
            if kw in all_titles_lower:
                ceasefire_scores[status] += 1
                for a in all_articles:
                    if kw in a['title'].lower():
                        ceasefire_indicators.append({
                            'status': status,
                            'phrase': kw,
                            'title': a['title'][:120],
                            'url': a['url']
                        })
                        break

    # Determine ceasefire status (highest score wins, with priority weighting)
    if ceasefire_scores['collapsed'] >= 2 or (ceasefire_scores['collapsed'] >= 1 and ceasefire_scores['holding'] == 0):
        ceasefire_status = 'collapsed'
        ceasefire_label = 'Collapsed'
        ceasefire_color = '#dc2626'
        ceasefire_icon = '💥'
        ceasefire_active = False
    elif ceasefire_scores['deteriorating'] >= 2 or (ceasefire_scores['deteriorating'] >= 1 and ceasefire_scores['holding'] == 0):
        ceasefire_status = 'deteriorating'
        ceasefire_label = 'Deteriorating'
        ceasefire_color = '#ea580c'
        ceasefire_icon = '⚠️'
        ceasefire_active = False  # No bonus if deteriorating
    elif ceasefire_scores['holding'] >= 1:
        ceasefire_status = 'holding'
        ceasefire_label = 'Holding'
        ceasefire_color = '#10b981'
        ceasefire_icon = '✅'
        ceasefire_active = True
    else:
        # Default: if active ground ops or invasion detected, ceasefire is collapsed
        if israel_presence['level'] >= 3:
            ceasefire_status = 'collapsed'
            ceasefire_label = 'Collapsed'
            ceasefire_color = '#dc2626'
            ceasefire_icon = '💥'
            ceasefire_active = False
        else:
            ceasefire_status = 'unknown'
            ceasefire_label = 'Unknown'
            ceasefire_color = '#6b7280'
            ceasefire_icon = '❓'
            ceasefire_active = False  # Conservative: no bonus if unknown

    print(f"[Security Situation] Ceasefire: {ceasefire_label} (scores: {ceasefire_scores})")

    # ── 4. UNIFIL Status ──
    unifil_scores = {'withdrawn': 0, 'withdrawing': 0, 'under_attack': 0, 'operational': 0}
    unifil_indicators = []

    for status, keywords in UNIFIL_KEYWORDS.items():
        for kw in keywords:
            if kw in all_titles_lower:
                unifil_scores[status] += 1
                for a in all_articles:
                    if kw in a['title'].lower():
                        unifil_indicators.append({
                            'status': status,
                            'phrase': kw,
                            'title': a['title'][:120],
                            'url': a['url']
                        })
                        break

    if unifil_scores['withdrawn'] >= 1:
        unifil_status = 'withdrawn'
        unifil_label = 'Withdrawn'
        unifil_color = '#6b7280'
        unifil_icon = '🚫'
    elif unifil_scores['under_attack'] >= 1:
        unifil_status = 'under_attack'
        unifil_label = 'Under Attack'
        unifil_color = '#dc2626'
        unifil_icon = '🔴'
    elif unifil_scores['withdrawing'] >= 1:
        unifil_status = 'withdrawing'
        unifil_label = 'Withdrawing'
        unifil_color = '#f59e0b'
        unifil_icon = '⏳'
    else:
        unifil_status = 'operational'
        unifil_label = 'Operational'
        unifil_color = '#10b981'
        unifil_icon = '🟢'

    print(f"[Security Situation] UNIFIL: {unifil_label} (scores: {unifil_scores})")

    # ── 5. Hezbollah Leadership ──
    leadership_results = {}

    for key, leader in HEZBOLLAH_LEADERSHIP.items():
        status = 'unknown'
        status_label = 'STATUS UNKNOWN'
        status_color = '#6b7280'
        matched_indicator = None

        # Check killed first (highest priority)
        for kw in leader['keywords_killed']:
            if kw in all_titles_lower:
                status = 'killed'
                status_label = 'KILLED'
                status_color = '#dc2626'
                for a in all_articles:
                    if kw in a['title'].lower():
                        matched_indicator = {'phrase': kw, 'title': a['title'][:120], 'url': a['url']}
                        break
                break

        # Then check alive
        if status == 'unknown':
            for kw in leader['keywords_alive']:
                if kw in all_titles_lower:
                    status = 'alive'
                    status_label = 'ACTIVE'
                    status_color = '#10b981'
                    for a in all_articles:
                        if kw in a['title'].lower():
                            matched_indicator = {'phrase': kw, 'title': a['title'][:120], 'url': a['url']}
                            break
                    break

        leadership_results[key] = {
            'name': leader['name'],
            'title': leader['title'],
            'title_ar': leader['title_ar'],
            'since': leader['since'],
            'note': leader['note'],
            'status': status,
            'status_label': status_label,
            'status_color': status_color,
            'indicator': matched_indicator
        }

        print(f"[Security Situation] {leader['name']}: {status_label}")

    # ── 6. Build response ──
    result = {
        'israeli_presence': israel_presence,
        'ceasefire': {
            'status': ceasefire_status,
            'label': ceasefire_label,
            'color': ceasefire_color,
            'icon': ceasefire_icon,
            'active': ceasefire_active,
            'scores': ceasefire_scores,
            'indicators': ceasefire_indicators[:5],
            'original_ceasefire_date': '2024-11-27',
        },
        'unifil': {
            'status': unifil_status,
            'label': unifil_label,
            'color': unifil_color,
            'icon': unifil_icon,
            'scores': unifil_scores,
            'indicators': unifil_indicators[:5],
            'mandate_note': 'UNIFIL mandate renewal pending; scheduled drawdown by end of 2027',
        },
        'hezbollah_leadership': leadership_results,
        'total_articles_scanned': len(all_articles),
        'scan_timestamp': datetime.now(timezone.utc).isoformat()
    }

    print(f"[Security Situation] ✅ Scan complete")
    return result
    
# ========================================
# STABILITY CALCULATION
# ========================================

def calculate_lebanon_stability(currency_data, bond_data, hezbollah_data, security_data=None):
    """
    Calculate Lebanon stability score (0-100)
    
    v2.9.1 RECALIBRATED:
    - Currency: penalizes RATE OF CHANGE, not absolute level (cap -15)
    - Bonds: reduced cap to -10 (default is priced in since 2020)
    - NEW: Ceasefire bonus +8
    - NEW: Humanitarian floor -5 (chronic crisis baseline)
    - President +10, Election +5, Hezbollah 0 to -20 unchanged
    """
    
    base_score = 50
    
    print("[Lebanon Stability] Calculating score (v2.9.1 recalibrated)...")
    
    # ── Currency: rate of CHANGE, not absolute level ──
    # Lebanon's 5900% devaluation is structural, not an ongoing shock.
    # Penalize based on whether it's getting worse.
    currency_impact = 0
    if currency_data:
        current_rate = currency_data.get('usd_to_lbp', 89500)
        change_24h = abs(currency_data.get('change_24h', 0))
        
        if change_24h > 5:
            currency_impact = 15   # Rapid deterioration
        elif change_24h > 2:
            currency_impact = 10   # Moderate deterioration
        elif change_24h > 0.5:
            currency_impact = 5    # Slow drift
        else:
            currency_impact = 2    # Stable (still some drag — it's Lebanon)
        
        print(f"[Lebanon Stability] Currency impact: -{currency_impact} (24h change: {change_24h:.2f}%)")
    
    # ── Bonds: reduced weight — default priced in since 2020 ──
    bond_impact = 0
    if bond_data:
        bond_yield = bond_data.get('yield', 0)
        if bond_yield > 80:
            bond_impact = 10       # Extreme distress
        elif bond_yield > 40:
            bond_impact = 7        # Severe (current ~45%)
        elif bond_yield > 20:
            bond_impact = 4        # Elevated
        else:
            bond_impact = 2        # Recovering
        print(f"[Lebanon Stability] Bond impact: -{bond_impact} (yield: {bond_yield}%)")
    
    # ── Hezbollah activity (unchanged) ──
    hezbollah_impact = 0
    if hezbollah_data:
        activity_score = hezbollah_data.get('activity_score', 0)
        hezbollah_impact = (activity_score / 100) * 20
        print(f"[Lebanon Stability] Hezbollah impact: -{hezbollah_impact:.1f}")
    
    # ── Presidential bonus ──
    presidential_bonus = 10
    president_elected_date = datetime(2025, 1, 9, tzinfo=timezone.utc)
    days_with_president = (datetime.now(timezone.utc) - president_elected_date).days
    
    # ── Election proximity bonus ──
    election_bonus = 0
    election_date = datetime(2026, 5, 10, tzinfo=timezone.utc)
    days_until_election = (election_date - datetime.now(timezone.utc)).days
    
    if 0 <= days_until_election <= 90:
        election_bonus = 5
    
    # ── Ceasefire bonus (v3.0.0 — dynamic!) ──
    if security_data and 'ceasefire' in security_data:
        ceasefire_active = security_data['ceasefire'].get('active', False)
        ceasefire_status = security_data['ceasefire'].get('status', 'unknown')
    else:
        ceasefire_active = False
        ceasefire_status = 'unknown'
    
    if ceasefire_active:
        ceasefire_bonus = 8
    elif ceasefire_status == 'deteriorating':
        ceasefire_bonus = 3  # Partial credit — still somewhat restraining
    else:
        ceasefire_bonus = 0  # Collapsed or unknown — no bonus
    
    # ── Israeli ground presence penalty (v3.0.0) ──
    ground_ops_penalty = 0
    if security_data and 'israeli_presence' in security_data:
        presence_level = security_data['israeli_presence'].get('level', 1)
        if presence_level >= 4:
            ground_ops_penalty = 15  # Full invasion
        elif presence_level >= 3:
            ground_ops_penalty = 10  # Active ground ops
        elif presence_level >= 2:
            ground_ops_penalty = 5   # Limited incursions
    
    # ── UNIFIL penalty (v3.0.0) ──
    unifil_penalty = 0
    if security_data and 'unifil' in security_data:
        unifil_status_val = security_data['unifil'].get('status', 'operational')
        if unifil_status_val == 'withdrawn':
            unifil_penalty = 8
        elif unifil_status_val == 'under_attack':
            unifil_penalty = 6
        elif unifil_status_val == 'withdrawing':
            unifil_penalty = 4
    
    # ── NEW: Humanitarian floor ──
    # Chronic crisis drag: power, water, healthcare, brain drain
    humanitarian_drag = -5
    
    # ── Final score (v3.0.0 — includes security situation) ──
    stability_score = (base_score 
                      - currency_impact 
                      - bond_impact 
                      - hezbollah_impact 
                      - ground_ops_penalty
                      - unifil_penalty
                      + presidential_bonus 
                      + election_bonus
                      + ceasefire_bonus
                      + humanitarian_drag)
    
    stability_score = max(0, min(100, stability_score))
    stability_score = int(stability_score)
    
    # Risk level
    if stability_score >= 70:
        risk_level = "Stable"
        risk_color = "green"
    elif stability_score >= 40:
        risk_level = "Moderate Risk"
        risk_color = "yellow"
    elif stability_score >= 20:
        risk_level = "High Risk"
        risk_color = "orange"
    else:
        risk_level = "Critical"
        risk_color = "red"
    
    # Trend — smarter logic (v3.0.0)
    trend = "stable"
    if security_data and security_data.get('israeli_presence', {}).get('level', 1) >= 3:
        trend = "worsening"
    elif security_data and security_data.get('ceasefire', {}).get('status') == 'collapsed':
        trend = "worsening"
    elif hezbollah_data and hezbollah_data.get('activity_score', 0) > 50:
        trend = "worsening"
    elif currency_data and currency_data.get('change_24h', 0) > 2:
        trend = "worsening"
    elif days_until_election <= 90 and days_with_president > 180:
        trend = "improving"<= 90 and days_with_president > 180:
        trend = "improving"
    
    print(f"[Lebanon Stability] ✅ Score: {stability_score}/100 ({risk_level})")
    print(f"[Lebanon Stability] Components: base={base_score}, currency=-{currency_impact}, bonds=-{bond_impact}, hez=-{hezbollah_impact:.0f}, ground_ops=-{ground_ops_penalty}, unifil=-{unifil_penalty}, president=+{presidential_bonus}, election=+{election_bonus}, ceasefire=+{ceasefire_bonus}, humanitarian={humanitarian_drag}")

    return {
        'score': stability_score,
        'risk_level': risk_level,
        'risk_color': risk_color,
        'trend': trend,
        'components': {
            'base': base_score,
            'currency_impact': -currency_impact,
            'bond_impact': -bond_impact,
            'hezbollah_impact': round(-hezbollah_impact, 1),
            'ground_ops_penalty': -ground_ops_penalty,
            'unifil_penalty': -unifil_penalty,
            'presidential_bonus': presidential_bonus,
            'election_bonus': election_bonus,
            'ceasefire_bonus': ceasefire_bonus,
            'humanitarian_drag': humanitarian_drag
        },
        'days_with_president': days_with_president,
        'days_until_election': days_until_election,
        'ceasefire_active': ceasefire_active,
        'version': '2.9.1-recalibrated'
    }

# ========================================
# CACHE MANAGEMENT (v2.9.0: Upstash Redis!)
# ========================================

def _redis_available():
    """Check if Upstash Redis is configured"""
    return bool(UPSTASH_URL and UPSTASH_TOKEN)


def _redis_get(key):
    """GET from Upstash Redis REST API"""
    try:
        response = requests.get(
            f"{UPSTASH_URL}/get/{key}",
            headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"},
            timeout=5
        )
        data = response.json()
        if data.get('result'):
            return json.loads(data['result'])
        return None
    except Exception as e:
        print(f"[Redis] GET error: {str(e)[:100]}")
        return None


def _redis_set(key, value):
    """SET to Upstash Redis REST API"""
    try:
        response = requests.post(
            f"{UPSTASH_URL}",
            headers={
                "Authorization": f"Bearer {UPSTASH_TOKEN}",
                "Content-Type": "application/json"
            },
            json=["SET", key, json.dumps(value)],
            timeout=5
        )
        result = response.json()
        if result.get('result') == 'OK':
            print(f"[Redis] ✅ Saved key: {key}")
            return True
        else:
            print(f"[Redis] SET response: {result}")
            return False
    except Exception as e:
        print(f"[Redis] SET error: {str(e)[:100]}")
        return False


def load_lebanon_cache():
    """Load cache from Redis (preferred) or /tmp (fallback)"""
    
    # Try Redis first
    if _redis_available():
        print("[Cache] Loading from Upstash Redis...")
        data = _redis_get(REDIS_CACHE_KEY)
        if data:
            days = len(data.get('history', {}))
            print(f"[Cache] ✅ Loaded from Redis ({days} days of history)")
            return data
        else:
            print("[Cache] Redis returned empty - starting fresh")
            initial_cache = {
                'last_updated': datetime.now(timezone.utc).isoformat(),
                'history': {},
                'metadata': {
                    'description': 'Daily Lebanon stability snapshots',
                    'started': datetime.now(timezone.utc).date().isoformat(),
                    'storage': 'upstash_redis'
                }
            }
            _redis_set(REDIS_CACHE_KEY, initial_cache)
            return initial_cache
    
    # Fallback to /tmp file (WARNING: ephemeral on Render!)
    print("[Cache] ⚠️ Redis not configured, using /tmp (data will not persist!)")
    try:
        if Path(CACHE_FILE).exists():
            with open(CACHE_FILE, 'r') as f:
                return json.load(f)
        else:
            initial_cache = {
                'last_updated': datetime.now(timezone.utc).isoformat(),
                'history': {},
                'metadata': {
                    'description': 'Daily Lebanon stability snapshots',
                    'started': datetime.now(timezone.utc).date().isoformat(),
                    'storage': 'tmp_file_WARNING_ephemeral'
                }
            }
            with open(CACHE_FILE, 'w') as f:
                json.dump(initial_cache, f, indent=2)
            return initial_cache
    except Exception as e:
        print(f"[Cache] Error: {str(e)}")
        return {'history': {}, 'last_updated': '', 'metadata': {}}


def save_lebanon_cache(cache_data):
    """Save cache to Redis (preferred) or /tmp (fallback)"""
    cache_data['last_updated'] = datetime.now(timezone.utc).isoformat()
    
    # Try Redis first
    if _redis_available():
        success = _redis_set(REDIS_CACHE_KEY, cache_data)
        if success:
            return
        print("[Cache] ⚠️ Redis save failed, falling back to /tmp")
    
    # Fallback to /tmp
    try:
        with open(CACHE_FILE, 'w') as f:
            json.dump(cache_data, f, indent=2)
        print(f"[Cache] Saved to /tmp ({len(cache_data.get('history', {}))} days)")
    except Exception as e:
        print(f"[Cache] Error: {str(e)}")


def update_lebanon_cache(currency_data, bond_data, hezbollah_data, stability_score, gold_data=None):
    """Update cache with today's snapshot"""
    try:
        cache = load_lebanon_cache()
        today = datetime.now(timezone.utc).date().isoformat()
        
        cache['history'][today] = {
            'currency_rate': currency_data.get('usd_to_lbp', 0) if currency_data else 0,
            'bond_yield': bond_data.get('yield', 0) if bond_data else 0,
            'hezbollah_activity': hezbollah_data.get('activity_score', 0) if hezbollah_data else 0,
            'stability_score': stability_score,
            'gold_price_per_oz': gold_data.get('price_per_oz', 0) if gold_data else 0,
            'gold_value_billions': gold_data.get('total_value_billions', 0) if gold_data else 0,
            'timestamp': datetime.now(timezone.utc).isoformat()
        }
        
        # Keep last 90 days
        if len(cache['history']) > 90:
            sorted_dates = sorted(cache['history'].keys())
            for old_date in sorted_dates[:-90]:
                del cache['history'][old_date]
        
        save_lebanon_cache(cache)
        print(f"[Cache] ✅ Updated for {today} ({len(cache['history'])} total days)")
        
    except Exception as e:
        print(f"[Cache] Error: {str(e)}")


def get_lebanon_trends(days=30):
    """Get trend data for sparklines"""
    try:
        cache = load_lebanon_cache()
        history = cache.get('history', {})
        
        if not history:
            return {
                'success': False,
                'message': 'Building trend data...',
                'days_collected': 0
            }
        
        sorted_dates = sorted(history.keys(), reverse=True)[:days]
        sorted_dates.reverse()
        
        trends = {
            'dates': [],
            'currency': [],
            'bonds': [],
            'hezbollah': [],
            'stability': [],
            'gold_price': [],
            'gold_value': []
        }
        
        for date in sorted_dates:
            day_data = history[date]
            trends['dates'].append(date)
            trends['currency'].append(day_data.get('currency_rate', 0))
            trends['bonds'].append(day_data.get('bond_yield', 0))
            trends['hezbollah'].append(day_data.get('hezbollah_activity', 0))
            trends['stability'].append(day_data.get('stability_score', 0))
            trends['gold_price'].append(day_data.get('gold_price_per_oz', 0))
            trends['gold_value'].append(day_data.get('gold_value_billions', 0))
        
        return {
            'success': True,
            'days_collected': len(sorted_dates),
            'trends': trends,
            'storage': 'redis' if _redis_available() else 'tmp_file'
        }
        
    except Exception as e:
        return {
            'success': False,
            'message': str(e),
            'days_collected': 0
        }

# ========================================
# API ENDPOINTS
# ========================================

import threading

def _background_lebanon_refresh():
    """Run a full Lebanon scan in the background and update Redis cache."""
    try:
        print("[Lebanon BG] Starting background refresh...")
        currency_data = fetch_lebanon_currency()
        bond_data = scrape_lebanon_bonds()
        hezbollah_data = track_hezbollah_activity(days=30)
        try:
            gold_data = calculate_lebanon_gold_reserves()
        except Exception:
            gold_data = None
        # v3.0.0: Security situation scan
        try:
            security_data = scan_security_situation(days=7)
            print(f"[Lebanon BG] ✅ Security: Israeli presence={security_data['israeli_presence']['label']}, Ceasefire={security_data['ceasefire']['label']}")
        except Exception as e:
            print(f"[Lebanon BG] ❌ Security scan failed: {str(e)}")
            security_data = None
        stability = calculate_lebanon_stability(currency_data, bond_data, hezbollah_data, security_data)
        update_lebanon_cache(currency_data, bond_data, hezbollah_data, stability.get('score', 0), gold_data)

        cache = load_lebanon_cache()
        cache_days = len(cache.get('history', {}))
        cache_storage = 'redis' if _redis_available() else 'tmp_file'

        payload = {
            'success': True,
            'stability': stability,
            'currency': currency_data,
            'bonds': bond_data,
            'hezbollah': hezbollah_data,
            'gold_reserves': gold_data,
            'security_situation': security_data,
            'government': {
                'has_president': True,
                'president': 'Joseph Aoun',
                'days_with_president': stability.get('days_with_president', 0),
                'president_elected_date': '2025-01-09',
                'parliamentary_election_date': '2026-05-10',
                'days_until_election': stability.get('days_until_election', 0)
            },
            'cache_status': {
                'storage': cache_storage,
                'days_collected': cache_days,
                'persistent': cache_storage == 'redis'
            },
            'last_updated': datetime.now(timezone.utc).isoformat(),
            'version': '3.0.0-lebanon'
        }

        if _redis_available():
            _redis_set(REDIS_CACHE_KEY, payload)
        print("[Lebanon BG] ✅ Background refresh complete")
    except Exception as e:
        print(f"[Lebanon BG] ❌ Background refresh failed: {str(e)}")


@app.route('/scan-lebanon-stability', methods=['GET'])
def scan_lebanon_stability():
    """Main stability endpoint — stale-while-revalidate pattern."""
    try:
        if not check_rate_limit():
            return jsonify({'error': 'Rate limit exceeded'}), 429

        force_refresh = request.args.get('refresh', '').lower() == 'true'
        STALE_TTL = 14400   # 4 hours — serve cache without revalidating
        REFRESH_TTL = 1800  # 30 min — trigger background refresh but still serve cache

        # ── Stale-while-revalidate cache check ──
        if not force_refresh and _redis_available():
            cached = _redis_get(REDIS_CACHE_KEY)
            if cached and cached.get('last_updated'):
                try:
                    age = (datetime.now(timezone.utc) - datetime.fromisoformat(cached['last_updated'])).total_seconds()
                    if age < STALE_TTL:
                        cached['from_cache'] = True
                        cached['cache_age_minutes'] = int(age / 60)
                        if age > REFRESH_TTL:
                            # Data is getting stale — serve immediately, refresh in background
                            print(f"[Lebanon] Cache is {int(age/60)}m old — serving stale, triggering background refresh")
                            t = threading.Thread(target=_background_lebanon_refresh, daemon=True)
                            t.start()
                        else:
                            print(f"[Lebanon] ✅ Serving fresh cache ({int(age/60)}m old)")
                        return jsonify(cached)
                except Exception:
                    pass  # Age check failed — fall through to live scan

        print("[Lebanon] No fresh cache — running live scan...")

        currency_data = fetch_lebanon_currency()
        bond_data = scrape_lebanon_bonds()
        hezbollah_data = track_hezbollah_activity(days=30)
        
        try:
            gold_data = calculate_lebanon_gold_reserves()
            print(f"[Lebanon] ✅ Gold: {gold_data.get('display_value', 'N/A')}")
        except Exception as e:
            print(f"[Lebanon] ❌ Gold failed: {str(e)}")
            gold_data = None
        
        stability = calculate_lebanon_stability(currency_data, bond_data, hezbollah_data)
        
        update_lebanon_cache(
            currency_data, 
            bond_data, 
            hezbollah_data, 
            stability.get('score', 0),
            gold_data
        )
        
        # Report cache status
        cache = load_lebanon_cache()
        cache_days = len(cache.get('history', {}))
        cache_storage = 'redis' if _redis_available() else 'tmp_file'
        
        return jsonify({
            'success': True,
            'stability': stability,
            'currency': currency_data,
            'bonds': bond_data,
            'hezbollah': hezbollah_data,
            'gold_reserves': gold_data,
            'government': {
                'has_president': True,
                'president': 'Joseph Aoun',
                'days_with_president': stability.get('days_with_president', 0),
                'president_elected_date': '2025-01-09',
                'parliamentary_election_date': '2026-05-10',
                'days_until_election': stability.get('days_until_election', 0)
            },
            'cache_status': {
                'storage': cache_storage,
                'days_collected': cache_days,
                'persistent': cache_storage == 'redis'
            },
            'version': '2.9.0-lebanon'
        })
        
    except Exception as e:
        print(f"[Lebanon] ❌ Error: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/lebanon-trends', methods=['GET'])
def api_lebanon_trends():
    """Trends endpoint for sparklines"""
    try:
        days = int(request.args.get('days', 30))
        days = min(days, 90)
        
        trends_data = get_lebanon_trends(days)
        
        return jsonify(trends_data)
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e),
            'days_collected': 0
        }), 500


@app.route('/api/bey-flights', methods=['GET'])
def api_bey_flights():
    """
    BEY flight data endpoint (stub for future integration)
    Will support Aviation Edge, OpenSky, or ADS-B Exchange
    """
    # TODO: Integrate free flight data source
    return jsonify({
        'success': False,
        'message': 'BEY flight tracking coming soon. Evaluating free data sources.',
        'planned_sources': ['OpenSky Network', 'ADS-B Exchange', 'FlightAware AeroAPI'],
        'airport': 'BEY',
        'icao': 'OLBA'
    })


@app.route('/', methods=['GET'])
def home():
    """Root endpoint"""
    return jsonify({
        'status': 'Lebanon Stability Backend',
        'version': '2.9.0',
        'cache': 'redis' if _redis_available() else 'tmp_file',
        'endpoints': {
            '/scan-lebanon-stability': 'Lebanon stability scan',
            '/api/lebanon-trends': 'Historical trends (30d sparklines)',
            '/api/bey-flights': 'BEY flight data (coming soon)',
            '/health': 'Health check'
        }
    })


@app.route('/health', methods=['GET'])
def health():
    """Health check with Redis status"""
    redis_status = 'not_configured'
    if _redis_available():
        # Quick ping to verify Redis is reachable
        try:
            test = _redis_get('__health_check__')
            redis_status = 'connected'
        except:
            redis_status = 'configured_but_unreachable'
    
    return jsonify({
        'status': 'healthy',
        'service': 'lebanon-stability',
        'version': '2.9.0',
        'cache_backend': redis_status,
        'timestamp': datetime.now(timezone.utc).isoformat()
    })


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port, debug=False)
