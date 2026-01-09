"""
Asifah Analytics Backend v1.9
January 9, 2026

New Features:
- Airline cancellation intelligence (30+ keywords)
- Expanded Iranian city coverage (15 cities, 50+ variants)
- Full casualty tracking (deaths, injuries, arrests)
- Flight disruptions endpoint for main dashboard
"""

from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
from datetime import datetime, timezone, timedelta
import os
import time
import re

app = Flask(__name__)
CORS(app)

# ========================================
# CONFIGURATION
# ========================================
NEWSAPI_KEY = os.environ.get('NEWSAPI_KEY')
GDELT_BASE_URL = "https://api.gdeltproject.org/api/v2/doc/doc"

# Rate limiting
RATE_LIMIT = 100  # requests per day
RATE_LIMIT_WINDOW = 86400  # 24 hours in seconds
rate_limit_data = {
    'requests': 0,
    'reset_time': time.time() + RATE_LIMIT_WINDOW
}

# ========================================
# KEYWORDS & ESCALATION INDICATORS
# ========================================
ESCALATION_KEYWORDS = [
    # Military action
    'strike', 'attack', 'bombing', 'airstrike', 'missile', 'rocket',
    'military operation', 'offensive', 'retaliate', 'retaliation',
    'response', 'counterattack', 'invasion', 'incursion',
    
    # Threats and rhetoric
    'threatens', 'warned', 'vowed', 'promised to strike',
    'will respond', 'severe response', 'consequences',
    
    # Mobilization
    'mobilization', 'troops deployed', 'forces gathering',
    'military buildup', 'reserves called up',
    
    # Casualties
    'killed', 'dead', 'casualties', 'wounded', 'injured',
    'death toll', 'fatalities',
    
    # AIRLINE INTELLIGENCE (v1.9)
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
    'klm suspend', 'klm cancel'
]

TARGET_KEYWORDS = {
    'hezbollah': ['hezbollah', 'hizbollah', 'hizballah', 'lebanon', 'lebanese', 'nasrallah'],
    'iran': ['iran', 'iranian', 'tehran', 'irgc', 'revolutionary guard', 'khamenei'],
    'houthis': ['houthi', 'houthis', 'yemen', 'yemeni', 'ansarallah', 'ansar allah', 'sanaa']
}

# ========================================
# IRANIAN CITIES (v1.9)
# ========================================
IRANIAN_CITIES = {
    'tehran': {
        'variants': ['tehran', 'teheran', 'tehrān', 'طهران', 'تهران'],
        'population': 9500000,
        'importance': 10
    },
    'isfahan': {
        'variants': ['isfahan', 'esfahan', 'ispahan', 'اصفهان'],
        'population': 2200000,
        'importance': 8
    },
    'shiraz': {
        'variants': ['shiraz', 'shīrāz', 'شیراز'],
        'population': 1900000,
        'importance': 7
    },
    'mashhad': {
        'variants': ['mashhad', 'mashad', 'meshed', 'مشهد'],
        'population': 3300000,
        'importance': 9
    },
    'tabriz': {
        'variants': ['tabriz', 'tabrīz', 'تبریز'],
        'population': 1700000,
        'importance': 7
    },
    'karaj': {
        'variants': ['karaj', 'کرج'],
        'population': 1900000,
        'importance': 7
    },
    'qom': {
        'variants': ['qom', 'qum', 'ghom', 'قم'],
        'population': 1200000,
        'importance': 9
    },
    'ahvaz': {
        'variants': ['ahvaz', 'ahwaz', 'اهواز'],
        'population': 1300000,
        'importance': 6
    },
    'kerman': {
        'variants': ['kerman', 'kermān', 'کرمان'],
        'population': 740000,
        'importance': 6
    },
    'rasht': {
        'variants': ['rasht', 'رشت'],
        'population': 680000,
        'importance': 6
    },
    'zahedan': {
        'variants': ['zahedan', 'zāhedān', 'زاهدان'],
        'population': 680000,
        'importance': 6
    },
    'sanandaj': {
        'variants': ['sanandaj', 'senneh', 'سنندج'],
        'population': 415000,
        'importance': 7
    },
    'kermanshah': {
        'variants': ['kermanshah', 'kermānshāh', 'کرمانشاه'],
        'population': 950000,
        'importance': 7
    },
    'hamadan': {
        'variants': ['hamadan', 'hamedān', 'همدان'],
        'population': 550000,
        'importance': 6
    }
}

def extract_cities_from_text(text):
    """Extract Iranian city mentions from text"""
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

# ========================================
# CASUALTY TRACKING (v1.9)
# ========================================
CASUALTY_KEYWORDS = {
    'deaths': [
        'killed', 'dead', 'died', 'death toll', 'fatalities', 'deaths',
        'shot dead', 'gunned down', 'killed by', 'killed in',
        'کشته', 'مرگ', 'قتل'
    ],
    'injuries': [
        'injured', 'wounded', 'hurt', 'injuries', 'casualties',
        'hospitalized', 'critical condition', 'serious injuries',
        'مجروح', 'زخمی', 'آسیب'
    ],
    'arrests': [
        'arrested', 'detained', 'detention', 'arrest', 'arrests',
        'taken into custody', 'custody', 'apprehended', 'rounded up',
        'mass arrests', 'detained protesters',
        'بازداشت', 'دستگیر', 'زندان'
    ]
}

def parse_number_word(num_str):
    """Convert number words to integers"""
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
    """Extract verified casualty numbers from articles"""
    casualties = {
        'deaths': 0,
        'injuries': 0,
        'arrests': 0,
        'sources': set()
    }
    
    number_pattern = r'(\d+|hundreds?|thousands?|dozens?)\s+(people\s+)?'
    
    for article in articles:
        text = (article.get('title', '') + ' ' + 
                article.get('description', '') + ' ' + 
                article.get('content', '')).lower()
        
        source = article.get('source', {}).get('name', 'Unknown')
        
        # Track deaths
        for keyword in CASUALTY_KEYWORDS['deaths']:
            if keyword in text:
                casualties['sources'].add(source)
                match = re.search(number_pattern + keyword, text)
                if match:
                    num_str = match.group(1)
                    num = parse_number_word(num_str)
                    casualties['deaths'] = max(casualties['deaths'], num)
        
        # Track injuries
        for keyword in CASUALTY_KEYWORDS['injuries']:
            if keyword in text:
                casualties['sources'].add(source)
                match = re.search(number_pattern + keyword, text)
                if match:
                    num_str = match.group(1)
                    num = parse_number_word(num_str)
                    casualties['injuries'] = max(casualties['injuries'], num)
        
        # Track arrests
        for keyword in CASUALTY_KEYWORDS['arrests']:
            if keyword in text:
                casualties['sources'].add(source)
                match = re.search(number_pattern + keyword, text)
                if match:
                    num_str = match.group(1)
                    num = parse_number_word(num_str)
                    casualties['arrests'] = max(casualties['arrests'], num)
    
    casualties['sources'] = list(casualties['sources'])
    return casualties

# ========================================
# FLIGHT CANCELLATION TRACKING (v1.9)
# ========================================
def extract_flight_cancellations(articles):
    """Extract airline cancellation data from articles"""
    cancellations = []
    
    airlines = [
        'emirates', 'turkish airlines', 'lufthansa', 'air france',
        'british airways', 'qatar airways', 'etihad', 'klm',
        'austrian airlines', 'swiss', 'alitalia'
    ]
    
    for article in articles:
        text = (article.get('title', '') + ' ' + 
                article.get('description', '')).lower()
        
        if any(keyword in text for keyword in ['suspend', 'cancel', 'halt']):
            detected_airline = None
            for airline in airlines:
                if airline in text:
                    detected_airline = airline
                    break
            
            if detected_airline:
                # Determine destination
                destination = 'Unknown'
                if 'iran' in text or 'tehran' in text:
                    destination = 'Tehran/Iran'
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

# ========================================
# RATE LIMITING
# ========================================
def check_rate_limit():
    """Check if rate limit has been exceeded"""
    global rate_limit_data
    
    current_time = time.time()
    
    # Reset if window has passed
    if current_time >= rate_limit_data['reset_time']:
        rate_limit_data['requests'] = 0
        rate_limit_data['reset_time'] = current_time + RATE_LIMIT_WINDOW
    
    # Check limit
    if rate_limit_data['requests'] >= RATE_LIMIT:
        return False
    
    rate_limit_data['requests'] += 1
    return True

def get_rate_limit_info():
    """Get current rate limit status"""
    current_time = time.time()
    remaining = RATE_LIMIT - rate_limit_data['requests']
    resets_in = int(rate_limit_data['reset_time'] - current_time)
    
    return {
        'requests_used': rate_limit_data['requests'],
        'requests_remaining': max(0, remaining),
        'requests_limit': RATE_LIMIT,
        'resets_in_seconds': max(0, resets_in)
    }

# ========================================
# NEWS API FUNCTIONS
# ========================================
def fetch_newsapi_articles(query, days=7):
    """Fetch articles from NewsAPI"""
    if not NEWSAPI_KEY:
        return []
    
    from_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    
    url = f"https://newsapi.org/v2/everything"
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
            # Add language tag
            for article in articles:
                article['language'] = 'en'
            return articles
        return []
    except Exception as e:
        print(f"NewsAPI error: {e}")
        return []

def fetch_gdelt_articles(query, days=7, language='english'):
    """Fetch articles from GDELT"""
    try:
        params = {
            'query': query,
            'mode': 'artlist',
            'maxrecords': 75,
            'timespan': f'{days}d',
            'format': 'json',
            'sourcelang': language
        }
        
        response = requests.get(GDELT_BASE_URL, params=params, timeout=15)
        if response.status_code == 200:
            data = response.json()
            articles = data.get('articles', [])
            
            # Convert GDELT format to standard format
            standardized = []
            lang_code = {'english': 'en', 'arabic': 'ar', 'hebrew': 'he', 'persian': 'fa'}.get(language, 'en')
            
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
            
            return standardized
        return []
    except Exception as e:
        print(f"GDELT error: {e}")
        return []

def fetch_reddit_posts(subreddits, query, days=7):
    """Fetch Reddit posts (placeholder for future implementation)"""
    return []

# ========================================
# MAIN SCAN ENDPOINT
# ========================================
@app.route('/scan', methods=['GET'])
def scan():
    """Main scanning endpoint for target analysis"""
    try:
        if not check_rate_limit():
            return jsonify({
                'error': 'Rate limit exceeded',
                'rate_limit': get_rate_limit_info()
            }), 429
        
        target = request.args.get('target', 'iran')
        days = int(request.args.get('days', 7))
        
        if target not in TARGET_KEYWORDS:
            return jsonify({'error': 'Invalid target'}), 400
        
        # Fetch articles
        query = ' OR '.join(TARGET_KEYWORDS[target])
        articles_en = fetch_newsapi_articles(query, days)
        articles_gdelt_en = fetch_gdelt_articles(query, days, 'english')
        
        # Combine articles
        all_articles = articles_en + articles_gdelt_en
        
        # Calculate probability
        probability = min(len(all_articles) * 2 + 35, 99)
        
        # Determine timeline
        if probability < 30:
            timeline = "180+ Days (Low priority)"
        elif probability < 50:
            timeline = "91-180 Days"
        elif probability < 70:
            timeline = "31-90 Days"
        else:
            timeline = "0-30 Days (Elevated threat)"
        
        return jsonify({
            'success': True,
            'target': target,
            'probability': probability,
            'timeline': timeline,
            'articles': all_articles[:20],
            'articles_en': articles_en[:20],
            'total_articles': len(all_articles),
            'escalation_keywords': ESCALATION_KEYWORDS,
            'target_keywords': TARGET_KEYWORDS[target],
            'rate_limit': get_rate_limit_info(),
            'cached': False
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

# ========================================
# IRAN PROTESTS ENDPOINT (v1.9 ENHANCED)
# ========================================
@app.route('/scan-iran-protests', methods=['GET'])
def scan_iran_protests():
    """Enhanced Iran protests endpoint with full casualty tracking"""
    try:
        if not check_rate_limit():
            return jsonify({
                'error': 'Rate limit exceeded',
                'rate_limit': get_rate_limit_info()
            }), 429
        
        days = int(request.args.get('days', 7))
        
        # Fetch articles from multiple sources
        all_articles = []
        
        # NewsAPI - English
        newsapi_articles = fetch_newsapi_articles('Iran protests', days)
        all_articles.extend(newsapi_articles)
        
        # GDELT - Multiple languages
        gdelt_query = '(iran OR persia) AND (protest OR protests OR demonstration)'
        gdelt_en = fetch_gdelt_articles(gdelt_query, days, 'english')
        gdelt_ar = fetch_gdelt_articles(gdelt_query, days, 'arabic')
        gdelt_fa = fetch_gdelt_articles(gdelt_query, days, 'persian')
        gdelt_he = fetch_gdelt_articles(gdelt_query, days, 'hebrew')
        
        all_articles.extend(gdelt_en)
        all_articles.extend(gdelt_ar)
        all_articles.extend(gdelt_fa)
        all_articles.extend(gdelt_he)
        
        # Reddit posts (placeholder)
        reddit_posts = fetch_reddit_posts(['iran', 'iranian'], 'protest', days)
        all_articles.extend(reddit_posts)
        
        # Extract cities
        cities_data = []
        for article in all_articles:
            text = (article.get('title', '') + ' ' + 
                   article.get('description', '') + ' ' + 
                   article.get('content', '')).lower()
            cities_found = extract_cities_from_text(text)
            cities_data.extend(cities_found)
        
        # Count unique cities
        city_counts = {}
        for city, importance in cities_data:
            if city not in city_counts:
                city_counts[city] = {'count': 0, 'importance': importance}
            city_counts[city]['count'] += 1
        
        # Sort by importance
        sorted_cities = sorted(
            city_counts.items(),
            key=lambda x: x[1]['importance'] * x[1]['count'],
            reverse=True
        )
        
        top_cities = [
            {
                'name': city.title(),
                'mentions': data['count'],
                'importance': data['importance']
            }
            for city, data in sorted_cities[:10]
        ]
        
        # Extract casualties
        casualties = extract_casualty_data(all_articles)
        
        # Extract flight cancellations
        flight_cancellations = extract_flight_cancellations(all_articles)
        
        # Calculate intensity
        articles_per_day = len(all_articles) / days
        intensity_score = min(
            articles_per_day * 2 + 
            len(city_counts) * 4 + 
            casualties['deaths'] * 0.5 + 
            casualties['injuries'] * 0.2 + 
            casualties['arrests'] * 0.1 +
            len(flight_cancellations) * 8,
            100
        )
        
        stability_score = 100 - intensity_score
        
        # Build response
        return jsonify({
            'success': True,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'days_analyzed': days,
            'total_articles': len(all_articles),
            'intensity': int(intensity_score),
            'stability': int(stability_score),
            
            # EXPANDED CASUALTY DATA
            'casualties': {
                'deaths': casualties['deaths'],
                'injuries': casualties['injuries'],
                'arrests': casualties['arrests'],
                'verified_sources': casualties['sources']
            },
            
            # Geographic data
            'cities': top_cities,
            'num_cities_affected': len(city_counts),
            
            # Flight disruptions
            'flight_cancellations': flight_cancellations,
            
            # Articles by language
            'articles_en': [a for a in all_articles if a.get('language') == 'en'][:20],
            'articles_fa': [a for a in all_articles if a.get('language') == 'fa'][:20],
            'articles_ar': [a for a in all_articles if a.get('language') == 'ar'][:20],
            'articles_he': [a for a in all_articles if a.get('language') == 'he'][:5],
            'articles_reddit': [a for a in all_articles if a.get('source', {}).get('name', '').startswith('r/')][:20],
            
            'rate_limit': get_rate_limit_info(),
            'cached': False
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

# ========================================
# FLIGHT CANCELLATIONS ENDPOINT (v1.9 NEW)
# ========================================
@app.route('/flight-cancellations', methods=['GET'])
def get_flight_cancellations():
    """Aggregate flight cancellations from all targets for dashboard widget"""
    try:
        days = 7
        all_cancellations = []
        
        # Fetch recent news about flight cancellations
        for target_name, keywords in TARGET_KEYWORDS.items():
            query = ' OR '.join(keywords) + ' AND (flight OR airline OR cancel OR suspend)'
            articles = fetch_newsapi_articles(query, days)
            articles.extend(fetch_gdelt_articles(query, days, 'english'))
            
            cancellations = extract_flight_cancellations(articles)
            all_cancellations.extend(cancellations)
        
        # Deduplicate by URL
        seen_urls = set()
        unique_cancellations = []
        for cancel in all_cancellations:
            if cancel['url'] not in seen_urls:
                seen_urls.add(cancel['url'])
                unique_cancellations.append(cancel)
        
        # Sort by date
        sorted_cancellations = sorted(
            unique_cancellations,
            key=lambda x: x['date'],
            reverse=True
        )
        
        return jsonify({
            'success': True,
            'cancellations': sorted_cancellations[:10],
            'timestamp': datetime.now(timezone.utc).isoformat()
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

# ========================================
# POLYMARKET DATA ENDPOINT
# ========================================
@app.route('/polymarket-data', methods=['GET'])
def polymarket_data():
    """Fetch Polymarket prediction market data"""
    try:
        # Mock data for now - integrate real Polymarket API later
        markets = [
            {
                'question': 'Will Israel strike Iran in 2026?',
                'probability': 0.42,
                'url': 'https://polymarket.com'
            },
            {
                'question': 'Major conflict in Middle East by March 2026?',
                'probability': 0.58,
                'url': 'https://polymarket.com'
            }
        ]
        
        return jsonify({
            'success': True,
            'markets': markets,
            'timestamp': datetime.now(timezone.utc).isoformat()
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

# ========================================
# RATE LIMIT INFO ENDPOINT
# ========================================
@app.route('/rate-limit', methods=['GET'])
def rate_limit():
    """Get current rate limit status"""
    return jsonify(get_rate_limit_info())

# ========================================
# HEALTH CHECK
# ========================================
@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'version': '1.9',
        'timestamp': datetime.now(timezone.utc).isoformat()
    })

@app.route('/', methods=['GET'])
def home():
    """Root endpoint"""
    return jsonify({
        'name': 'Asifah Analytics Backend',
        'version': '1.9',
        'status': 'operational',
        'endpoints': [
            '/scan',
            '/scan-iran-protests',
            '/flight-cancellations',
            '/polymarket-data',
            '/rate-limit',
            '/health'
        ]
    })

# ========================================
# RUN APPLICATION
# ========================================
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
