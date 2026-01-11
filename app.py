"""
Asifah Analytics Backend v1.9.2
January 11, 2026

NEW IN v1.9.2:
- FIXED: GDELT integration (HTTP instead of HTTPS, proper language codes)
- IMPROVED: Error handling and logging for all data sources
- FIXED: Empty articles issue resolved
"""

from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
import feedparser
from datetime import datetime, timezone, timedelta
import os
import time
import re

app = Flask(__name__)
CORS(app)

NEWSAPI_KEY = os.environ.get('NEWSAPI_KEY')
GDELT_BASE_URL = "http://api.gdeltproject.org/api/v2/doc/doc"

RATE_LIMIT = 100
RATE_LIMIT_WINDOW = 86400
rate_limit_data = {'requests': 0, 'reset_time': time.time() + RATE_LIMIT_WINDOW}

ESCALATION_KEYWORDS = [
    'strike', 'attack', 'bombing', 'airstrike', 'missile', 'rocket',
    'military operation', 'offensive', 'retaliate', 'retaliation',
    'response', 'counterattack', 'invasion', 'incursion',
    'threatens', 'warned', 'vowed', 'promised to strike',
    'will respond', 'severe response', 'consequences',
    'mobilization', 'troops deployed', 'forces gathering',
    'military buildup', 'reserves called up',
    'killed', 'dead', 'casualties', 'wounded', 'injured',
    'death toll', 'fatalities',
    'flight cancellations', 'cancelled flights', 'suspend flights', 'suspended flights',
    'airline suspends', 'suspended service to', 'halted flights', 'halt flights',
    'grounded flights', 'airspace closed', 'no-fly zone', 'travel advisory',
    'do not travel', 'avoid all travel', 'reconsider travel',
    'emirates suspend', 'emirates cancel', 'emirates halt',
    'turkish airlines suspend', 'turkish airlines cancel', 'turkish airlines halt',
    'lufthansa suspend', 'lufthansa cancel',
    'air france suspend', 'air france cancel',
    'british airways suspend', 'british airways cancel',
    'qatar airways suspend', 'qatar airways cancel',
    'etihad suspend', 'etihad cancel',
    'klm suspend', 'klm cancel',
    'pegasus airlines suspend', 'pegasus airlines cancel'
]

TARGET_KEYWORDS = {
    'hezbollah': ['hezbollah', 'hizbollah', 'hizballah', 'lebanon', 'lebanese', 'nasrallah'],
    'iran': ['iran', 'iranian', 'tehran', 'irgc', 'revolutionary guard', 'khamenei'],
    'houthis': ['houthi', 'houthis', 'yemen', 'yemeni', 'ansarallah', 'ansar allah', 'sanaa']
}

IRANIAN_CITIES = {
    'tehran': {'variants': ['tehran', 'teheran', 'tehrān', 'طهران', 'تهران'], 'population': 9500000, 'importance': 10},
    'isfahan': {'variants': ['isfahan', 'esfahan', 'ispahan', 'اصفهان'], 'population': 2200000, 'importance': 8},
    'shiraz': {'variants': ['shiraz', 'shīrāz', 'شیراز'], 'population': 1900000, 'importance': 7},
    'mashhad': {'variants': ['mashhad', 'mashad', 'meshed', 'مشهد'], 'population': 3300000, 'importance': 9},
    'tabriz': {'variants': ['tabriz', 'tabrīz', 'تبریز'], 'population': 1700000, 'importance': 7},
    'karaj': {'variants': ['karaj', 'کرج'], 'population': 1900000, 'importance': 7},
    'qom': {'variants': ['qom', 'qum', 'ghom', 'قم'], 'population': 1200000, 'importance': 9},
    'ahvaz': {'variants': ['ahvaz', 'ahwaz', 'اهواز'], 'population': 1300000, 'importance': 6},
    'kerman': {'variants': ['kerman', 'kermān', 'کرمان'], 'population': 740000, 'importance': 6},
    'rasht': {'variants': ['rasht', 'رشت'], 'population': 680000, 'importance': 6},
    'zahedan': {'variants': ['zahedan', 'zāhedān', 'زاهدان'], 'population': 680000, 'importance': 6},
    'sanandaj': {'variants': ['sanandaj', 'senneh', 'سنندج'], 'population': 415000, 'importance': 7},
    'kermanshah': {'variants': ['kermanshah', 'kermānshāh', 'کرمانشاه'], 'population': 950000, 'importance': 7},
    'hamadan': {'variants': ['hamadan', 'hamedān', 'همدان'], 'population': 550000, 'importance': 6}
}

CASUALTY_KEYWORDS = {
    'deaths': ['killed', 'dead', 'died', 'death toll', 'fatalities', 'deaths', 'shot dead', 'gunned down', 'killed by', 'killed in', 'کشته', 'مرگ', 'قتل'],
    'injuries': ['injured', 'wounded', 'hurt', 'injuries', 'casualties', 'hospitalized', 'critical condition', 'serious injuries', 'مجروح', 'زخمی', 'آسیب'],
    'arrests': ['arrested', 'detained', 'detention', 'arrest', 'arrests', 'taken into custody', 'custody', 'apprehended', 'rounded up', 'mass arrests', 'detained protesters', 'بازداشت', 'دستگیر', 'زندان']
}

def extract_cities_from_text(text):
    if not text:
        return []
    text_lower = text.lower()
    cities_found = []
    for city, data in IRANIAN_CITIES.items():
        for variant in data['variants']:
            if variant.lower() in text_lower or variant in text:
                cities_found.append((city, data['importance']))
                break
    return cities_found

def parse_number_word(num_str):
    num_str = num_str.lower()
    if num_str.isdigit():
        return int(num_str)
    elif 'hundred' in num_str:
        return 100
    elif 'thousand' in num_str:
        return 1000
    elif 'dozen' in num_str:
        return 12
    return 0

def extract_casualty_data(articles):
    casualties = {'deaths': 0, 'injuries': 0, 'arrests': 0, 'sources': set()}
    number_pattern = r'(\d+|hundreds?|thousands?|dozens?)\s+(people\s+)?'
    for article in articles:
        text = (article.get('title', '') + ' ' + article.get('description', '') + ' ' + article.get('content', '')).lower()
        source = article.get('source', {}).get('name', 'Unknown')
        for keyword in CASUALTY_KEYWORDS['deaths']:
            if keyword in text:
                casualties['sources'].add(source)
                match = re.search(number_pattern + keyword, text)
                if match:
                    num = parse_number_word(match.group(1))
                    casualties['deaths'] = max(casualties['deaths'], num)
        for keyword in CASUALTY_KEYWORDS['injuries']:
            if keyword in text:
                casualties['sources'].add(source)
                match = re.search(number_pattern + keyword, text)
                if match:
                    num = parse_number_word(match.group(1))
                    casualties['injuries'] = max(casualties['injuries'], num)
        for keyword in CASUALTY_KEYWORDS['arrests']:
            if keyword in text:
                casualties['sources'].add(source)
                match = re.search(number_pattern + keyword, text)
                if match:
                    num = parse_number_word(match.group(1))
                    casualties['arrests'] = max(casualties['arrests'], num)
    casualties['sources'] = list(casualties['sources'])
    return casualties

def extract_flight_cancellations(articles):
    cancellations = []
    airlines = ['emirates', 'turkish airlines', 'lufthansa', 'air france', 'british airways', 'qatar airways', 'etihad', 'klm', 'austrian airlines', 'swiss', 'alitalia', 'pegasus airlines']
    for article in articles:
        text = (article.get('title', '') + ' ' + article.get('description', '')).lower()
        if any(keyword in text for keyword in ['suspend', 'cancel', 'halt', 'turned back']):
            detected_airline = None
            for airline in airlines:
                if airline in text:
                    detected_airline = airline
                    break
            if detected_airline:
                destination = 'Unknown'
                if 'iran' in text or 'tehran' in text:
                    destination = 'Tehran/Iran'
                elif 'shiraz' in text:
                    destination = 'Shiraz'
                elif 'mashhad' in text:
                    destination = 'Mashhad'
                elif 'lebanon' in text or 'beirut' in text:
                    destination = 'Beirut/Lebanon'
                elif 'yemen' in text or 'sanaa' in text:
                    destination = 'Sanaa/Yemen'
                if destination != 'Unknown':
                    cancellations.append({
                        'airline': detected_airline.title(),
                        'destination': destination,
                        'date': article.get('publishedAt', 'Unknown date')[:10],
                        'source': article.get('source', {}).get('name', 'Unknown'),
                        'url': article.get('url', '#'),
                        'headline': article.get('title', '')
                    })
    return cancellations

def fetch_iranwire_articles(days=7):
    articles = []
    feeds = [('https://iranwire.com/en/feed/', 'en'), ('https://iranwire.com/fa/feed/', 'fa')]
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)
    for feed_url, language in feeds:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries:
                try:
                    if hasattr(entry, 'published_parsed') and entry.published_parsed:
                        pub_date = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                    elif hasattr(entry, 'updated_parsed') and entry.updated_parsed:
                        pub_date = datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)
                    else:
                        pub_date = datetime.now(timezone.utc)
                except Exception:
                    pub_date = datetime.now(timezone.utc)
                if pub_date >= cutoff_date:
                    description = ''
                    if hasattr(entry, 'summary'):
                        description = entry.summary
                    elif hasattr(entry, 'description'):
                        description = entry.description
                    articles.append({
                        'title': entry.title,
                        'description': description or entry.title,
                        'url': entry.link,
                        'publishedAt': pub_date.isoformat(),
                        'source': {'name': 'Iran Wire'},
                        'content': description or entry.title,
                        'language': language
                    })
        except Exception as e:
            print(f"[v1.9.2] Iran Wire RSS error ({feed_url}): {e}")
    print(f"[v1.9.2] Iran Wire: Fetched {len(articles)} articles")
    return articles

def check_rate_limit():
    global rate_limit_data
    current_time = time.time()
    if current_time >= rate_limit_data['reset_time']:
        rate_limit_data['requests'] = 0
        rate_limit_data['reset_time'] = current_time + RATE_LIMIT_WINDOW
    if rate_limit_data['requests'] >= RATE_LIMIT:
        return False
    rate_limit_data['requests'] += 1
    return True

def get_rate_limit_info():
    current_time = time.time()
    remaining = RATE_LIMIT - rate_limit_data['requests']
    resets_in = int(rate_limit_data['reset_time'] - current_time)
    return {
        'requests_used': rate_limit_data['requests'],
        'requests_remaining': max(0, remaining),
        'requests_limit': RATE_LIMIT,
        'resets_in_seconds': max(0, resets_in)
    }

def fetch_newsapi_articles(query, days=7):
    if not NEWSAPI_KEY:
        print("[v1.9.2] NewsAPI: No API key configured")
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
            data = response.json()
            articles = data.get('articles', [])
            for article in articles:
                article['language'] = 'en'
            print(f"[v1.9.2] NewsAPI: Fetched {len(articles)} articles for '{query}'")
            return articles
        else:
            print(f"[v1.9.2] NewsAPI error: HTTP {response.status_code}")
            return []
    except Exception as e:
        print(f"[v1.9.2] NewsAPI error: {e}")
        return []

def fetch_gdelt_articles(query, days=7, language_code='en'):
    try:
        gdelt_lang_map = {'en': 'eng', 'ar': 'ara', 'he': 'heb', 'fa': 'fas'}
        gdelt_lang = gdelt_lang_map.get(language_code, 'eng')
        params = {
            'query': query,
            'mode': 'artlist',
            'maxrecords': 75,
            'timespan': f'{days}d',
            'format': 'json',
            'sourcelang': gdelt_lang
        }
        print(f"[v1.9.2] GDELT: Querying with lang={gdelt_lang}, query='{query}'")
        response = requests.get(GDELT_BASE_URL, params=params, timeout=15)
        print(f"[v1.9.2] GDELT: HTTP {response.status_code}")
        if response.status_code == 200:
            content_type = response.headers.get('Content-Type', '')
            if 'json' not in content_type.lower():
                print(f"[v1.9.2] GDELT warning: Response is not JSON (Content-Type: {content_type})")
                print(f"[v1.9.2] GDELT response preview: {response.text[:200]}")
                return []
            try:
                data = response.json()
            except ValueError as e:
                print(f"[v1.9.2] GDELT JSON parse error: {e}")
                print(f"[v1.9.2] GDELT response preview: {response.text[:200]}")
                return []
            articles = data.get('articles', [])
            if not articles:
                print(f"[v1.9.2] GDELT: No articles found for lang={gdelt_lang}")
                return []
            standardized = []
            for article in articles:
                standardized.append({
                    'title': article.get('title', ''),
                    'description': article.get('title', ''),
                    'url': article.get('url', ''),
                    'publishedAt': article.get('seendate', ''),
                    'source': {'name': article.get('domain', 'GDELT')},
                    'content': article.get('title', ''),
                    'language': language_code
                })
            print(f"[v1.9.2] GDELT: Fetched {len(standardized)} articles for lang={gdelt_lang}")
            return standardized
        else:
            print(f"[v1.9.2] GDELT HTTP error: {response.status_code}")
            return []
    except requests.exceptions.Timeout:
        print("[v1.9.2] GDELT error: Request timeout")
        return []
    except requests.exceptions.RequestException as e:
        print(f"[v1.9.2] GDELT request error: {e}")
        return []
    except Exception as e:
        print(f"[v1.9.2] GDELT unexpected error: {e}")
        return []

def fetch_reddit_posts(subreddits, query, days=7):
    return []

@app.route('/scan', methods=['GET'])
def scan():
    try:
        if not check_rate_limit():
            return jsonify({'error': 'Rate limit exceeded', 'rate_limit': get_rate_limit_info()}), 429
        target = request.args.get('target', 'iran')
        days = int(request.args.get('days', 7))
        if target not in TARGET_KEYWORDS:
            return jsonify({'error': 'Invalid target'}), 400
        print(f"\n[v1.9.2] ===== SCAN REQUEST: {target} ({days} days) =====")
        query = ' OR '.join(TARGET_KEYWORDS[target])
        print("[v1.9.2] Fetching from NewsAPI...")
        articles_en = fetch_newsapi_articles(query, days)
        print("[v1.9.2] Fetching from GDELT (English)...")
        gdelt_en = fetch_gdelt_articles(query, days, 'en')
        print("[v1.9.2] Fetching from GDELT (Arabic)...")
        gdelt_ar = fetch_gdelt_articles(query, days, 'ar')
        print("[v1.9.2] Fetching from GDELT (Hebrew)...")
        gdelt_he = fetch_gdelt_articles(query, days, 'he')
        print("[v1.9.2] Fetching from GDELT (Farsi)...")
        gdelt_fa = fetch_gdelt_articles(query, days, 'fa')
        all_articles_en = articles_en + gdelt_en
        all_articles_ar = gdelt_ar
        all_articles_he = gdelt_he
        all_articles_fa = gdelt_fa
        total_articles = len(all_articles_en) + len(all_articles_ar) + len(all_articles_he) + len(all_articles_fa)
        print(f"[v1.9.2] TOTALS: EN={len(all_articles_en)}, AR={len(all_articles_ar)}, HE={len(all_articles_he)}, FA={len(all_articles_fa)}")
        if total_articles == 0:
            probability = 10
        else:
            probability = min(total_articles * 2 + 15, 99)
        if probability < 30:
            timeline = "180+ Days (Low priority)"
        elif probability < 50:
            timeline = "91-180 Days"
        elif probability < 70:
            timeline = "31-90 Days"
        else:
            timeline = "0-30 Days (Elevated threat)"
        print(f"[v1.9.2] RESULT: probability={probability}%, timeline={timeline}")
        print("[v1.9.2] ===== END SCAN =====\n")
        return jsonify({
            'success': True,
            'target': target,
            'probability': probability,
            'timeline': timeline,
            'articles': all_articles_en[:20],
            'articles_en': all_articles_en[:20],
            'articles_ar': all_articles_ar[:20],
            'articles_he': all_articles_he[:20],
            'articles_fa': all_articles_fa[:20],
            'total_articles': total_articles,
            'escalation_keywords': ESCALATION_KEYWORDS,
            'target_keywords': TARGET_KEYWORDS[target],
            'rate_limit': get_rate_limit_info(),
            'cached': False,
            'version': '1.9.2'
        })
    except Exception as e:
        print(f"[v1.9.2] SCAN ERROR: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/scan-iran-protests', methods=['GET'])
def scan_iran_protests():
    try:
        if not check_rate_limit():
            return jsonify({'error': 'Rate limit exceeded', 'rate_limit': get_rate_limit_info()}), 429
        days = int(request.args.get('days', 7))
        print(f"\n[v1.9.2] ===== IRAN PROTESTS SCAN ({days} days) =====")
        all_articles = []
        print("[v1.9.2] Fetching Iran protests from NewsAPI...")
        newsapi_articles = fetch_newsapi_articles('Iran protests', days)
        all_articles.extend(newsapi_articles)
        gdelt_query = '(iran OR persia) AND (protest OR protests OR demonstration)'
        print("[v1.9.2] Fetching Iran protests from GDELT (multiple languages)...")
        gdelt_en = fetch_gdelt_articles(gdelt_query, days, 'en')
        gdelt_ar = fetch_gdelt_articles(gdelt_query, days, 'ar')
        gdelt_fa = fetch_gdelt_articles(gdelt_query, days, 'fa')
        gdelt_he = fetch_gdelt_articles(gdelt_query, days, 'he')
        all_articles.extend(gdelt_en)
        all_articles.extend(gdelt_ar)
        all_articles.extend(gdelt_fa)
        all_articles.extend(gdelt_he)
        print("[v1.9.2] Fetching from Iran Wire RSS...")
        iranwire_articles = fetch_iranwire_articles(days)
        all_articles.extend(iranwire_articles)
        reddit_posts = fetch_reddit_posts(['iran', 'iranian'], 'protest', days)
        all_articles.extend(reddit_posts)
        print(f"[v1.9.2] Total articles fetched: {len(all_articles)}")
        cities_data = []
        for article in all_articles:
            text = (article.get('title', '') + ' ' + article.get('description', '') + ' ' + article.get('content', '')).lower()
            cities_found = extract_cities_from_text(text)
            cities_data.extend(cities_found)
        city_counts = {}
        for city, importance in cities_data:
            if city not in city_counts:
                city_counts[city] = {'count': 0, 'importance': importance}
            city_counts[city]['count'] += 1
        sorted_cities = sorted(city_counts.items(), key=lambda x: x[1]['importance'] * x[1]['count'], reverse=True)
        top_cities = [{'name': city.title(), 'mentions': data['count'], 'importance': data['importance']} for city, data in sorted_cities[:10]]
        casualties = extract_casualty_data(all_articles)
        flight_cancellations = extract_flight_cancellations(all_articles)
        articles_per_day = len(all_articles) / days if days > 0 else 0
        intensity_score = min(articles_per_day * 2 + len(city_counts) * 4 + casualties['deaths'] * 0.5 + casualties['injuries'] * 0.2 + casualties['arrests'] * 0.1 + len(flight_cancellations) * 8, 100)
        stability_score = 100 - intensity_score
        articles_by_lang = {
            'en': [a for a in all_articles if a.get('language') == 'en'],
            'fa': [a for a in all_articles if a.get('language') == 'fa'],
            'ar': [a for a in all_articles if a.get('language') == 'ar'],
            'he': [a for a in all_articles if a.get('language') == 'he']
        }
        iranwire_only = [a for a in all_articles if a.get('source', {}).get('name') == 'Iran Wire']
        reddit_only = [a for a in all_articles if a.get('source', {}).get('name', '').startswith('r/')]
        print(f"[v1.9.2] Intensity: {int(intensity_score)}, Stability: {int(stability_score)}")
        print(f"[v1.9.2] Casualties: {casualties['deaths']} deaths, {casualties['injuries']} injuries, {casualties['arrests']} arrests")
        print(f"[v1.9.2] Cities affected: {len(city_counts)}")
        print("[v1.9.2] ===== END PROTESTS SCAN =====\n")
        return jsonify({
            'success': True,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'days_analyzed': days,
            'total_articles': len(all_articles),
            'intensity': int(intensity_score),
            'stability': int(stability_score),
            'casualties': {'deaths': casualties['deaths'], 'injuries': casualties['injuries'], 'arrests': casualties['arrests'], 'verified_sources': casualties['sources']},
            'cities': top_cities,
            'num_cities_affected': len(city_counts),
            'flight_cancellations': flight_cancellations,
            'articles_en': articles_by_lang['en'][:20],
            'articles_fa': articles_by_lang['fa'][:20],
            'articles_ar': articles_by_lang['ar'][:20],
            'articles_he': articles_by_lang['he'][:5],
            'articles_iranwire': iranwire_only[:20],
            'articles_reddit': reddit_only[:20],
            'rate_limit': get_rate_limit_info(),
            'cached': False,
            'version': '1.9.2'
        })
    except Exception as e:
        print(f"[v1.9.2] PROTESTS SCAN ERROR: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/flight-cancellations', methods=['GET'])
def get_flight_cancellations():
    try:
        days = 7
        all_cancellations = []
        for target_name, keywords in TARGET_KEYWORDS.items():
            query = ' OR '.join(keywords) + ' AND (flight OR airline OR cancel OR suspend)'
            articles = fetch_newsapi_articles(query, days)
            articles.extend(fetch_gdelt_articles(query, days, 'en'))
            cancellations = extract_flight_cancellations(articles)
            all_cancellations.extend(cancellations)
        iranwire_articles = fetch_iranwire_articles(days)
        iranwire_cancellations = extract_flight_cancellations(iranwire_articles)
        all_cancellations.extend(iranwire_cancellations)
        seen_urls = set()
        unique_cancellations = []
        for cancel in all_cancellations:
            if cancel['url'] not in seen_urls:
                seen_urls.add(cancel['url'])
                unique_cancellations.append(cancel)
        sorted_cancellations = sorted(unique_cancellations, key=lambda x: x['date'], reverse=True)
        return jsonify({'success': True, 'cancellations': sorted_cancellations[:10], 'timestamp': datetime.now(timezone.utc).isoformat()})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/polymarket-data', methods=['GET'])
def polymarket_data():
    try:
        markets = [
            {'question': 'Will Israel strike Iran in 2026?', 'probability': 0.42, 'url': 'https://polymarket.com'},
            {'question': 'Major conflict in Middle East by March 2026?', 'probability': 0.58, 'url': 'https://polymarket.com'}
        ]
        return jsonify({'success': True, 'markets': markets, 'timestamp': datetime.now(timezone.utc).isoformat()})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/rate-limit', methods=['GET'])
def rate_limit():
    return jsonify(get_rate_limit_info())

@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        'status': 'healthy',
        'version': '1.9.2',
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'fixes': ['GDELT HTTP/HTTPS fix', 'GDELT language code mapping', 'Enhanced error handling', 'Detailed logging']
    })

@app.route('/', methods=['GET'])
def home():
    return jsonify({
        'name': 'Asifah Analytics Backend',
        'version': '1.9.2',
        'status': 'operational',
        'new_in_v192': 'Fixed GDELT integration (HTTP, language codes, error handling)',
        'endpoints': ['/scan', '/scan-iran-protests', '/flight-cancellations', '/polymarket-data', '/rate-limit', '/health']
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"[v1.9.2] Starting Asifah Analytics Backend on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
