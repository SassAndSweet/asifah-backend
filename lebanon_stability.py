"""
Lebanon Stability Backend v2.9.0
Standalone microservice for Lebanon Stability Index

CHANGELOG v2.9.0:
- FIXED: Cache now uses Upstash Redis (persistent!) instead of /tmp (ephemeral)
- FIXED: Expanded Hezbollah activity keyword matching
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
    """Scrape Lebanon 10Y Eurobond yield"""
    try:
        print("[Lebanon Bonds] Scraping...")
        
        if not BS4_AVAILABLE:
            print("[Lebanon Bonds] BeautifulSoup not available, using fallback")
            return scrape_lebanon_bonds_fallback()
        
        url = "https://www.investing.com/rates-bonds/lebanon-10-year-bond-yield"
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        response = requests.get(url, timeout=10, headers=headers)
        
        if response.status_code != 200:
            return scrape_lebanon_bonds_fallback()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        
        value_elem = soup.find('span', {'data-test': 'instrument-price-last'})
        
        if value_elem:
            text = value_elem.get_text().strip()
            match = re.search(r'(\d+\.?\d*)', text)
            if match:
                yield_pct = float(match.group(1))
                
                if 10 <= yield_pct <= 200:
                    print(f"[Lebanon Bonds] ✅ Yield: {yield_pct}%")
                    
                    return {
                        'yield': yield_pct,
                        'source': 'Investing.com',
                        'date': datetime.now(timezone.utc).isoformat(),
                        'note': 'Distressed debt (defaulted March 2020)'
                    }
        
        return scrape_lebanon_bonds_fallback()
        
    except Exception as e:
        print(f"[Lebanon Bonds] Error: {str(e)[:150]}")
        return scrape_lebanon_bonds_fallback()


def scrape_lebanon_bonds_fallback():
    """Fallback bond data"""
    print("[Lebanon Bonds] Using fallback estimate")
    
    return {
        'yield': 45.0,
        'source': 'Estimated',
        'date': datetime.now(timezone.utc).isoformat(),
        'note': 'Estimated - Lebanon defaulted March 2020'
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
                            all_articles.append({
                                'title': title_elem.text or '',
                                'url': link_elem.text if link_elem is not None else '',
                                'published': pubDate_elem.text if pubDate_elem is not None else '',
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

# ========================================
# STABILITY CALCULATION
# ========================================

def calculate_lebanon_stability(currency_data, bond_data, hezbollah_data):
    """Calculate Lebanon stability score (0-100)"""
    
    base_score = 50
    
    print("[Lebanon Stability] Calculating score...")
    
    # Currency collapse impact
    currency_impact = 0
    if currency_data:
        current_rate = currency_data.get('usd_to_lbp', 90000)
        devaluation_pct = ((current_rate - 1500) / 1500) * 100
        currency_impact = min((devaluation_pct / 100), 30)
        print(f"[Lebanon Stability] Currency impact: -{currency_impact:.1f}")
    
    # Bond yield stress
    bond_impact = 0
    if bond_data:
        bond_yield = bond_data.get('yield', 0)
        bond_impact = min((bond_yield / 2), 25)
        print(f"[Lebanon Stability] Bond impact: -{bond_impact:.1f}")
    
    # Hezbollah activity
    hezbollah_impact = 0
    if hezbollah_data:
        activity_score = hezbollah_data.get('activity_score', 0)
        hezbollah_impact = (activity_score / 100) * 20
        print(f"[Lebanon Stability] Hezbollah impact: -{hezbollah_impact:.1f}")
    
    # Presidential bonus
    presidential_bonus = 10
    president_elected_date = datetime(2025, 1, 9, tzinfo=timezone.utc)
    days_with_president = (datetime.now(timezone.utc) - president_elected_date).days
    
    # Election proximity bonus
    election_bonus = 0
    election_date = datetime(2026, 5, 10, tzinfo=timezone.utc)
    days_until_election = (election_date - datetime.now(timezone.utc)).days
    
    if 0 <= days_until_election <= 90:
        election_bonus = 5
    
    # Final score
    stability_score = (base_score - currency_impact - bond_impact - 
                      hezbollah_impact + presidential_bonus + election_bonus)
    
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
    
    # Trend
    trend = "stable"
    if hezbollah_data and hezbollah_data.get('activity_score', 0) > 50:
        trend = "worsening"
    elif days_with_president < 60:
        trend = "improving"
    
    print(f"[Lebanon Stability] ✅ Score: {stability_score}/100 ({risk_level})")
    
    return {
        'score': stability_score,
        'risk_level': risk_level,
        'risk_color': risk_color,
        'trend': trend,
        'components': {
            'base': base_score,
            'currency_impact': -currency_impact,
            'bond_impact': -bond_impact,
            'hezbollah_impact': -hezbollah_impact,
            'presidential_bonus': presidential_bonus,
            'election_bonus': election_bonus
        },
        'days_until_election': days_until_election if days_until_election > 0 else 0,
        'days_with_president': days_with_president
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

@app.route('/scan-lebanon-stability', methods=['GET'])
def scan_lebanon_stability():
    """Main stability endpoint"""
    try:
        if not check_rate_limit():
            return jsonify({'error': 'Rate limit exceeded'}), 429
        
        print("[Lebanon] Starting scan...")
        
        currency_data = fetch_lebanon_currency()
        bond_data = scrape_lebanon_bonds()
        hezbollah_data = track_hezbollah_activity(days=7)
        
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
