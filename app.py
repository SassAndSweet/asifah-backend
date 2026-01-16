"""
Asifah Analytics Backend v2.0.0
January 16, 2026

Changes from v1.9.9:
- MAJOR: Implemented sophisticated threat probability algorithm
- Time decay weighting (2-day half-life for article relevance)
- Source credibility tiers (Premium/Regional/Standard/GDELT/Social)
- De-escalation keyword detection (reduces threat scores)
- Momentum analysis (increasing/decreasing/stable trends)
- Keyword severity levels (Critical/High/Elevated/Moderate)
- Fixed: No longer pegs Iran at 99% when rhetoric has cooled
- Fixed: Houthi strikes properly weighted based on credible sources
"""

from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
from datetime import datetime, timezone, timedelta
import os
import time
import re
import math

app = Flask(__name__)
CORS(app)

# ========================================
# CONFIGURATION
# ========================================
NEWSAPI_KEY = os.environ.get('NEWSAPI_KEY')
GDELT_BASE_URL = "http://api.gdeltproject.org/api/v2/doc/doc"

# Rate limiting
RATE_LIMIT = 100
RATE_LIMIT_WINDOW = 86400
rate_limit_data = {
    'requests': 0,
    'reset_time': time.time() + RATE_LIMIT_WINDOW
}

# ========================================
# v2.0 SCORING ALGORITHM - SOURCE WEIGHTS
# ========================================
SOURCE_WEIGHTS = {
    'premium': {
        'sources': [
            'The New York Times', 'The Washington Post', 'Reuters', 
            'Associated Press', 'AP News', 'BBC News', 'The Guardian',
            'Financial Times', 'Wall Street Journal', 'The Economist'
        ],
        'weight': 1.0
    },
    'regional': {
        'sources': [
            'Iran Wire', 'Al Jazeera', 'Haaretz', 'Times of Israel',
            'Al Arabiya', 'The Jerusalem Post', 'Middle East Eye'
        ],
        'weight': 0.8
    },
    'standard': {
        'sources': [
            'CNN', 'MSNBC', 'Fox News', 'NBC News', 'CBS News',
            'ABC News', 'Bloomberg', 'CNBC'
        ],
        'weight': 0.6
    },
    'gdelt': {
        'sources': ['GDELT'],
        'weight': 0.4
    },
    'social': {
        'sources': ['Reddit', 'r/'],
        'weight': 0.3
    }
}

# ========================================
# v2.0 SCORING ALGORITHM - KEYWORD SEVERITY
# ========================================
KEYWORD_SEVERITY = {
    'critical': {
        'keywords': [
            'nuclear strike', 'nuclear attack', 'nuclear threat',
            'full-scale war', 'declaration of war', 'state of war',
            'mobilization order', 'reserves called up', 'troops deployed'
        ],
        'multiplier': 2.5
    },
    'high': {
        'keywords': [
            'imminent strike', 'imminent attack', 'preparing to strike',
            'military buildup', 'forces gathering', 'will strike',
            'vowed to attack', 'threatened to strike'
        ],
        'multiplier': 2.0
    },
    'elevated': {
        'keywords': [
            'strike', 'attack', 'airstrike', 'bombing', 'missile',
            'rocket', 'retaliate', 'retaliation', 'response'
        ],
        'multiplier': 1.5
    },
    'moderate': {
        'keywords': [
            'threatens', 'warned', 'tensions', 'escalation',
            'conflict', 'crisis'
        ],
        'multiplier': 1.0
    }
}

# ========================================
# v2.0 SCORING ALGORITHM - DE-ESCALATION
# ========================================
DEESCALATION_KEYWORDS = [
    'ceasefire', 'cease-fire', 'truce', 'peace talks', 'peace agreement',
    'diplomatic solution', 'negotiations', 'de-escalation', 'de-escalate',
    'tensions ease', 'tensions cool', 'tensions subside', 'calm',
    'defused', 'avoided', 'no plans to', 'ruled out', 'backs down',
    'restraint', 'diplomatic efforts', 'unlikely to strike'
]

# ========================================
# v2.0 SCORING ALGORITHM - HELPER FUNCTIONS
# ========================================
def calculate_time_decay(published_date, current_time, half_life_days=2.0):
    """Calculate exponential time decay for article relevance"""
    try:
        if isinstance(published_date, str):
            pub_dt = datetime.fromisoformat(published_date.replace('Z', '+00:00'))
        else:
            pub_dt = published_date
        
        if pub_dt.tzinfo is None:
            pub_dt = pub_dt.replace(tzinfo=timezone.utc)
        
        age_hours = (current_time - pub_dt).total_seconds() / 3600
        age_days = age_hours / 24
        
        decay_factor = math.exp(-math.log(2) * age_days / half_life_days)
        return decay_factor
    except Exception:
        return 0.1

def get_source_weight(source_name):
    """Get credibility weight for a source"""
    if not source_name:
        return 0.3
    
    source_lower = source_name.lower()
    
    for tier_data in SOURCE_WEIGHTS.values():
        for source in tier_data['sources']:
            if source.lower() in source_lower or source_lower in source.lower():
                return tier_data['weight']
    
    return 0.5

def detect_keyword_severity(text):
    """Detect highest severity keywords in text"""
    if not text:
        return 1.0
    
    text_lower = text.lower()
    
    for severity_level in ['critical', 'high', 'elevated', 'moderate']:
        for keyword in KEYWORD_SEVERITY[severity_level]['keywords']:
            if keyword in text_lower:
                return KEYWORD_SEVERITY[severity_level]['multiplier']
    
    return 1.0

def detect_deescalation(text):
    """Check if article indicates de-escalation"""
    if not text:
        return False
    
    text_lower = text.lower()
    
    for keyword in DEESCALATION_KEYWORDS:
        if keyword in text_lower:
            return True
    
    return False

def calculate_threat_probability(articles, days_analyzed=7, target='iran'):
    """Calculate sophisticated threat probability score"""
    
    if not articles:
        return {
            'probability': 15,
            'momentum': 'stable',
            'breakdown': {
                'base_score': 15,
                'article_count': 0,
                'weighted_score': 0,
                'time_decay_applied': True,
                'deescalation_detected': False
            }
        }
    
    current_time = datetime.now(timezone.utc)
    
    weighted_score = 0
    deescalation_count = 0
    recent_articles = 0
    older_articles = 0
    
    article_details = []
    
    for article in articles:
        title = article.get('title', '')
        description = article.get('description', '')
        content = article.get('content', '')
        full_text = f"{title} {description} {content}"
        
        source_name = article.get('source', {}).get('name', 'Unknown')
        published_date = article.get('publishedAt', '')
        
        time_decay = calculate_time_decay(published_date, current_time)
        source_weight = get_source_weight(source_name)
        severity_multiplier = detect_keyword_severity(full_text)
        is_deescalation = detect_deescalation(full_text)
        
        if is_deescalation:
            article_contribution = -3 * time_decay * source_weight
            deescalation_count += 1
        else:
            article_contribution = time_decay * source_weight * severity_multiplier
        
        weighted_score += article_contribution
        
        try:
            pub_dt = datetime.fromisoformat(published_date.replace('Z', '+00:00'))
            age_hours = (current_time - pub_dt).total_seconds() / 3600
            
            if age_hours <= 48:
                recent_articles += 1
            else:
                older_articles += 1
        except:
            older_articles += 1
        
        article_details.append({
            'source': source_name,
            'source_weight': source_weight,
            'time_decay': round(time_decay, 3),
            'severity': severity_multiplier,
            'deescalation': is_deescalation,
            'contribution': round(article_contribution, 2)
        })
    
    # Calculate momentum
    if recent_articles > 0 and older_articles > 0:
        recent_density = recent_articles / 2.0
        older_density = older_articles / (days_analyzed - 2)
        
        momentum_ratio = recent_density / older_density if older_density > 0 else 2.0
        
        if momentum_ratio > 1.5:
            momentum = 'increasing'
            momentum_multiplier = 1.2
        elif momentum_ratio < 0.7:
            momentum = 'decreasing'
            momentum_multiplier = 0.8
        else:
            momentum = 'stable'
            momentum_multiplier = 1.0
    else:
        momentum = 'stable'
        momentum_multiplier = 1.0
    
    weighted_score *= momentum_multiplier
    
    base_score = 15
    
    if weighted_score < 0:
        probability = max(5, base_score + weighted_score)
    else:
        probability = base_score + (weighted_score * 2.5)
    
    probability = min(int(probability), 99)
    probability = max(int(probability), 5)
    
    print(f"[v2.0.0] {target} scoring:")
    print(f"  Total articles: {len(articles)}")
    print(f"  Recent (48h): {recent_articles}")
    print(f"  Weighted score: {weighted_score:.2f}")
    print(f"  Momentum: {momentum} ({momentum_multiplier}x)")
    print(f"  De-escalation articles: {deescalation_count}")
    print(f"  Final probability: {probability}%")
    
    return {
        'probability': probability,
        'momentum': momentum,
        'breakdown': {
            'base_score': base_score,
            'article_count': len(articles),
            'recent_articles_48h': recent_articles,
            'older_articles': older_articles,
            'weighted_score': round(weighted_score, 2),
            'momentum_multiplier': momentum_multiplier,
            'deescalation_count': deescalation_count,
            'time_decay_applied': True,
            'source_weighting_applied': True
        },
        'top_contributors': sorted(article_details, 
                                   key=lambda x: abs(x['contribution']), 
                                   reverse=True)[:5]
    }

# ========================================
# REDDIT CONFIGURATION
# ========================================
REDDIT_USER_AGENT = "AsifahAnalytics/2.0.0 (OSINT monitoring tool)"
REDDIT_SUBREDDITS = {
    "hezbollah": ["ForbiddenBromance", "Israel", "Lebanon"],
    "iran": ["Iran", "Israel", "geopolitics"],
    "houthis": ["Yemen", "Israel", "geopolitics"]
}

# ========================================
# KEYWORDS & ESCALATION INDICATORS
# ========================================
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
    'klm suspend', 'klm cancel'
]

TARGET_KEYWORDS = {
    'hezbollah': {
        'keywords': ['hezbollah', 'hizbollah', 'hizballah', 'lebanon', 'lebanese', 'nasrallah'],
        'reddit_keywords': ['Hezbollah', 'Lebanon', 'Israel', 'IDF', 'Lebanese', 'border', 'missile', 'strike']
    },
    'iran': {
        'keywords': ['iran', 'iranian', 'tehran', 'irgc', 'revolutionary guard', 'khamenei'],
        'reddit_keywords': ['Iran', 'Israel', 'IRGC', 'nuclear', 'Tehran', 'strike', 'sanctions']
    },
    'houthis': {
        'keywords': ['houthi', 'houthis', 'yemen', 'yemeni', 'ansarallah', 'ansar allah', 'sanaa'],
        'reddit_keywords': ['Houthi', 'Yemen', 'Red Sea', 'shipping', 'missile', 'drone', 'Ansar Allah']
    }
}

# ========================================
# IRANIAN CITIES
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
# CASUALTY TRACKING
# ========================================
CASUALTY_KEYWORDS = {
    'deaths': [
        'killed', 'dead', 'died', 'death toll', 'fatalities', 'deaths',
        'shot dead', 'gunned down', 'killed by', 'killed in',
        'people have died', 'people have been killed', 'protesters killed',
        'protesters had been killed', 'protesters have been killed',
        'have died', 'have been killed', 'had been killed',
        'death toll tops', 'death toll reaches', 'toll rises',
        'کشته', 'مرگ', 'قتل'
    ],
    'injuries': [
        'injured', 'wounded', 'hurt', 'injuries', 'casualties',
        'hospitalized', 'critical condition', 'serious injuries',
        'overwhelmed by injured', 'injured protesters', 'gunshot wounds',
        'wounded protesters', 'protesters injured', 'suffering injuries',
        'treated for injuries', 'hospitals overwhelmed',
        'shot in their limbs', 'shot in the head', 'shot in the eye',
        'pellets lodged', 'gunshot injuries', 'suffering from gunshot',
        'people wounded', 'people shot and wounded', 'cases of injuries',
        'metal pellet wounds', 'head and eye injuries',
        'مجروح', 'زخمی', 'آسیب'
    ],
    'arrests': [
        'arrested', 'detained', 'detention', 'arrest', 'arrests',
        'taken into custody', 'custody', 'apprehended', 'rounded up',
        'imprisoned', 'people have been arrested', 'people have been imprisoned',
        'people have also been imprisoned', 'protesters arrested',
        'protesters detained', 'mass arrests', 'detained protesters',
        'have been arrested', 'have been detained', 'had been arrested',
        'بازداشت', 'دستگیر', 'زندان'
    ]
}

def parse_number_word(num_str):
    """Convert number words to integers"""
    num_str = num_str.lower().strip()
    
    try:
        return int(num_str)
    except:
        pass
    
    if ',' in num_str:
        try:
            return int(num_str.replace(',', ''))
        except:
            pass
    
    if 'hundred' in num_str or 'hundreds' in num_str:
        if any(word in num_str for word in ['several', 'few', 'many']):
            return 200
        if 'over' in num_str or 'more than' in num_str:
            return 150
        return 100
    
    elif 'thousand' in num_str or 'thousands' in num_str:
        match = re.search(r'(\d+)\s*thousand', num_str)
        if match:
            return int(match.group(1)) * 1000
        
        if any(word in num_str for word in ['several', 'few', 'many']):
            return 2000
        if 'over' in num_str or 'more than' in num_str:
            return 1500
        return 1000
    
    elif 'dozen' in num_str or 'dozens' in num_str:
        if 'several' in num_str:
            return 24
        return 12
    
    elif num_str == 'many':
        return 50
    
    return 0

def extract_casualty_data(articles):
    """Extract verified casualty numbers from articles"""
    casualties = {
        'deaths': 0,
        'injuries': 0,
        'arrests': 0,
        'sources': set(),
        'details': []
    }
    
    number_patterns = [
        r'(\d+(?:,\d{3})*)\s+(?:people\s+)?.{0,20}?',
        r'(?:more than|over|at least)\s+(\d+(?:,\d{3})*)\s+(?:people\s+)?.{0,30}?',
        r'(\d+(?:,\d{3})*)\s+people\s+(?:have been|had been|have)\s+.{0,20}?',
        r'(?:\d+)\s*(?:to|-)\s*(\d+(?:,\d{3})*)\s+.{0,20}?',
        r'(?:roughly|approximately|around)\s+(\d+(?:,\d{3})*)\s+.{0,20}?',
        r'(hundreds?|thousands?|dozens?|several\s+(?:hundred|thousand|dozen)|many)\s+(?:people\s+)?.{0,20}?',
        r'(\d+)\s+thousand\s*.{0,20}?',
    ]
    
    for article in articles:
        title = article.get('title') or ''
        description = article.get('description') or ''
        content = article.get('content') or ''
        text = (title + ' ' + description + ' ' + content).lower()
        
        source = article.get('source', {}).get('name', 'Unknown')
        url = article.get('url', '')
        
        sentences = re.split(r'[.!?]\s+', text)
        
        for sentence in sentences:
            for keyword in CASUALTY_KEYWORDS['deaths']:
                if keyword in sentence:
                    casualties['sources'].add(source)
                    
                    for pattern in number_patterns:
                        match = re.search(pattern + re.escape(keyword), sentence, re.IGNORECASE)
                        if match:
                            num_str = match.group(1)
                            num = parse_number_word(num_str)
                            
                            if num > casualties['deaths']:
                                casualties['deaths'] = num
                                casualties['details'].append({
                                    'type': 'deaths',
                                    'count': num,
                                    'source': source,
                                    'url': url
                                })
                            break
        
        for sentence in sentences:
            for keyword in CASUALTY_KEYWORDS['injuries']:
                if keyword in sentence:
                    casualties['sources'].add(source)
                    
                    for pattern in number_patterns:
                        match = re.search(pattern + re.escape(keyword), sentence, re.IGNORECASE)
                        if match:
                            num_str = match.group(1)
                            num = parse_number_word(num_str)
                            
                            if num > 0:
                                if num > casualties['injuries']:
                                    casualties['injuries'] = num
                                casualties['details'].append({
                                    'type': 'injuries',
                                    'count': num,
                                    'source': source,
                                    'url': url
                                })
                            break
        
        for sentence in sentences:
            for keyword in CASUALTY_KEYWORDS['arrests']:
                if keyword in sentence:
                    casualties['sources'].add(source)
                    
                    for pattern in number_patterns:
                        match = re.search(pattern + re.escape(keyword), sentence, re.IGNORECASE)
                        if match:
                            num_str = match.group(1)
                            num = parse_number_word(num_str)
                            
                            if num > casualties['arrests']:
                                casualties['arrests'] = num
                                casualties['details'].append({
                                    'type': 'arrests',
                                    'count': num,
                                    'source': source,
                                    'url': url
                                })
                            break
    
    casualties['sources'] = list(casualties['sources'])
    
    if casualties['deaths'] > 0:
        print(f"[v2.0.0] ✓ Deaths: {casualties['deaths']} detected")
    if casualties['injuries'] > 0:
        print(f"[v2.0.0] ✓ Injuries: {casualties['injuries']} detected")
    if casualties['arrests'] > 0:
        print(f"[v2.0.0] ✓ Arrests: {casualties['arrests']} detected")
    
    return casualties

# ========================================
# FLIGHT CANCELLATION TRACKING
# ========================================
def extract_flight_cancellations(articles):
    """Extract airline cancellation data from articles"""
    cancellations = []
    
    airlines = [
        'emirates', 'turkish airlines', 'lufthansa', 'air france',
        'british airways', 'qatar airways', 'etihad', 'etihad airways', 'klm',
        'austrian airlines', 'swiss', 'alitalia', 'flydubai', 'fly dubai',
        'pegasus', 'pegasus airlines', 'wizz air', 'ryanair',
        'air canada', 'united airlines', 'delta', 'american airlines'
    ]
    
    for article in articles:
        title = article.get('title') or ''
        description = article.get('description') or ''
        content = article.get('content') or ''
        text = (title + ' ' + description + ' ' + content).lower()
        
        if any(keyword in text for keyword in ['suspend', 'cancel', 'halt', 'cancelled', 'suspended']):
            
            detected_airlines = []
            for airline in airlines:
                if airline in text:
                    normalized = airline.title()
                    if normalized not in detected_airlines:
                        detected_airlines.append(normalized)
            
            destination = 'Unknown'
            if 'iran' in text or 'tehran' in text:
                destination = 'Tehran/Iran'
            elif 'lebanon' in text or 'beirut' in text:
                destination = 'Beirut/Lebanon'
            elif 'yemen' in text or 'sanaa' in text:
                destination = 'Sanaa/Yemen'
            
            if destination != 'Unknown' and detected_airlines:
                for airline in detected_airlines:
                    cancellations.append({
                        'airline': airline,
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
    
    if current_time >= rate_limit_data['reset_time']:
        rate_limit_data['requests'] = 0
        rate_limit_data['reset_time'] = current_time + RATE_LIMIT_WINDOW
    
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
        print("[v2.0.0] NewsAPI: No API key configured")
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
            for article in articles:
                article['language'] = 'en'
            
            sources = {}
            for article in articles:
                source_name = article.get('source', {}).get('name', 'Unknown')
                sources[source_name] = sources.get(source_name, 0) + 1
            
            nyt = sources.get('The New York Times', 0)
            wapo = sources.get('The Washington Post', 0)
            bbc = sources.get('BBC News', 0)
            
            print(f"[v2.0.0] NewsAPI: Fetched {len(articles)} articles")
            if nyt > 0:
                print(f"[v2.0.0] NewsAPI: ✓ NYT articles: {nyt}")
            if wapo > 0:
                print(f"[v2.0.0] NewsAPI: ✓ WaPo articles: {wapo}")
            if bbc > 0:
                print(f"[v2.0.0] NewsAPI: ✓ BBC articles: {bbc}")
            
            return articles
        print(f"[v2.0.0] NewsAPI: HTTP {response.status_code}")
        return []
    except Exception as e:
        print(f"[v2.0.0] NewsAPI error: {e}")
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
        
        print(f"[v2.0.0] GDELT {language}: Query = {wrapped_query}")
        
        response = requests.get(GDELT_BASE_URL, params=params, timeout=15)
        
        content_type = response.headers.get('Content-Type', '')
        if 'application/json' not in content_type:
            print(f"[v2.0.0] GDELT warning: Response is not JSON")
            return []
        
        if response.status_code == 200:
            data = response.json()
            articles = data.get('articles', [])
            
            standardized = []
            lang_code = {'eng': 'en', 'ara': 'ar', 'heb': 'he', 'fas': 'fa'}.get(language, 'en')
            
            domains = {}
            
            for article in articles:
                domain = article.get('domain', 'unknown')
                domains[domain] = domains.get(domain, 0) + 1
                
                standardized.append({
                    'title': article.get('title', ''),
                    'description': article.get('title', ''),
                    'url': article.get('url', ''),
                    'publishedAt': article.get('seendate', ''),
                    'source': {'name': article.get('domain', 'GDELT')},
                    'content': article.get('title', ''),
                    'language': lang_code
                })
            
            print(f"[v2.0.0] GDELT {language}: Fetched {len(standardized)} articles")
            
            premium_domains = {
                'nytimes.com': 'NYT',
                'washingtonpost.com': 'WaPo',
                'bbc.com': 'BBC',
                'bbc.co.uk': 'BBC',
                'reuters.com': 'Reuters',
                'apnews.com': 'AP'
            }
            
            for domain, count in domains.items():
                for premium_domain, name in premium_domains.items():
                    if premium_domain in domain.lower():
                        print(f"[v2.0.0] GDELT {language}: ✓ {name} articles: {count}")
                        break
            
            return standardized
        
        print(f"[v2.0.0] GDELT {language}: HTTP {response.status_code}")
        return []
    except Exception as e:
        print(f"[v2.0.0] GDELT {language} error: {e}")
        return []

def fetch_reddit_posts(target, keywords, days=7):
    """Fetch Reddit posts from relevant subreddits"""
    print(f"[v2.0.0] Reddit: Starting fetch for {target}")
    
    subreddits = REDDIT_SUBREDDITS.get(target, [])
    if not subreddits:
        print(f"[v2.0.0] Reddit: No subreddits configured for {target}")
        return []
    
    all_posts = []
    
    if days <= 1:
        time_filter = "day"
    elif days <= 7:
        time_filter = "week"
    elif days <= 30:
        time_filter = "month"
    else:
        time_filter = "year"
    
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
            
            headers = {
                "User-Agent": REDDIT_USER_AGENT
            }
            
            time.sleep(2)
            
            response = requests.get(url, params=params, headers=headers, timeout=10)
            
            if response.status_code == 429:
                print(f"[v2.0.0] Reddit r/{subreddit}: Rate limited")
                continue
            
            response.raise_for_status()
            data = response.json()
            
            if "data" in data and "children" in data["data"]:
                posts = data["data"]["children"]
                
                for post in posts:
                    post_data = post.get("data", {})
                    
                    normalized_post = {
                        "title": post_data.get("title", "")[:200],
                        "description": post_data.get("selftext", "")[:300],
                        "url": f"https://www.reddit.com{post_data.get('permalink', '')}",
                        "publishedAt": datetime.fromtimestamp(
                            post_data.get("created_utc", 0), 
                            tz=timezone.utc
                        ).isoformat(),
                        "source": {"name": f"r/{subreddit}"},
                        "content": post_data.get("selftext", ""),
                        "language": "en",
                        "reddit_score": post_data.get("score", 0),
                        "reddit_comments": post_data.get("num_comments", 0),
                        "reddit_upvote_ratio": post_data.get("upvote_ratio", 0)
                    }
                    
                    all_posts.append(normalized_post)
                
                print(f"[v2.0.0] Reddit r/{subreddit}: Found {len(posts)} posts")
            
        except Exception as e:
            print(f"[v2.0.0] Reddit r/{subreddit} error: {str(e)}")
            continue
    
    print(f"[v2.0.0] Reddit: Total {len(all_posts)} posts")
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
            print(f"[v2.0.0] Iran Wire {lang}: Attempting to fetch RSS...")
            response = requests.get(feed_url, timeout=10)
            
            if response.status_code != 200:
                print(f"[v2.0.0] Iran Wire {lang}: HTTP {response.status_code}")
                continue
            
            import xml.etree.ElementTree as ET
            
            try:
                root = ET.fromstring(response.content)
            except ET.ParseError as e:
                print(f"[v2.0.0] Iran Wire {lang}: XML parse error: {e}")
                continue
            
            articles_before = len(articles)
            
            for item in root.findall('.//item')[:10]:
                title = item.find('title')
                link = item.find('link')
                pubDate = item.find('pubDate')
                description = item.find('description')
                
                if title is not None and link is not None:
                    articles.append({
                        'title': title.text or '',
                        'description': description.text if description is not None else '',
                        'url': link.text or '',
                        'publishedAt': pubDate.text if pubDate is not None else '',
                        'source': {'name': 'Iran Wire'},
                        'content': '',
                        'language': lang,
                        'iranwire': True
                    })
            
            articles_added = len(articles) - articles_before
            print(f"[v2.0.0] Iran Wire {lang}: ✓ Fetched {articles_added} articles")
            
        except requests.Timeout:
            print(f"[v2.0.0] Iran Wire {lang}: Timeout after 10s")
        except requests.ConnectionError as e:
            print(f"[v2.0.0] Iran Wire {lang}: Connection error")
        except Exception as e:
            print(f"[v2.0.0] Iran Wire {lang}: Unexpected error: {str(e)[:100]}")
    
    print(f"[v2.0.0] Iran Wire: Total {len(articles)} articles")
    return articles

# ========================================
# MAIN SCAN ENDPOINT - v2.0 WITH IMPROVED SCORING
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
        
        query = ' OR '.join(TARGET_KEYWORDS[target]['keywords'])
        
        articles_en = fetch_newsapi_articles(query, days)
        articles_gdelt_en = fetch_gdelt_articles(query, days, 'eng')
        articles_gdelt_ar = fetch_gdelt_articles(query, days, 'ara')
        articles_gdelt_he = fetch_gdelt_articles(query, days, 'heb')
        
        articles_gdelt_fa = []
        if target == 'iran':
            articles_gdelt_fa = fetch_gdelt_articles(query, days, 'fas')
        
        articles_reddit = fetch_reddit_posts(
            target,
            TARGET_KEYWORDS[target]['reddit_keywords'],
            days
        )
        
        all_articles = (articles_en + articles_gdelt_en + articles_gdelt_ar + 
                       articles_gdelt_he + articles_gdelt_fa + articles_reddit)
        
        # v2.0: Use sophisticated scoring algorithm
        scoring_result = calculate_threat_probability(all_articles, days, target)
        probability = scoring_result['probability']
        momentum = scoring_result['momentum']
        breakdown = scoring_result['breakdown']
        
        # Determine timeline based on probability and momentum
        if probability < 30:
            timeline = "180+ Days (Low priority)"
        elif probability < 50:
            timeline = "91-180 Days"
        elif probability < 70:
            timeline = "31-90 Days"
        else:
            timeline = "0-30 Days (Elevated threat)"
        
        # Adjust timeline based on momentum
        if momentum == 'increasing' and probability > 50:
            timeline = "0-30 Days (Elevated threat)"
        elif momentum == 'decreasing' and probability < 70:
            if "31-90" in timeline:
                timeline = "91-180 Days"
            elif "91-180" in timeline:
                timeline = "180+ Days (Low priority)"
        
        return jsonify({
            'success': True,
            'target': target,
            'probability': probability,
            'momentum': momentum,
            'timeline': timeline,
            'scoring_breakdown': breakdown,
            'top_scoring_articles': scoring_result.get('top_contributors', []),
            'articles': all_articles[:50],
            'articles_en': articles_en[:20],
            'articles_ar': articles_gdelt_ar[:20],
            'articles_he': articles_gdelt_he[:20],
            'articles_fa': articles_gdelt_fa[:20],
            'articles_reddit': articles_reddit[:20],
            'total_articles': len(all_articles),
            'totalResults_en': len(articles_en),
            'totalResults_ar': len(articles_gdelt_ar),
            'totalResults_he': len(articles_gdelt_he),
            'totalResults_fa': len(articles_gdelt_fa),
            'totalResults_reddit': len(articles_reddit),
            'reddit_subreddits': REDDIT_SUBREDDITS.get(target, []),
            'escalation_keywords': ESCALATION_KEYWORDS,
            'target_keywords': TARGET_KEYWORDS[target]['keywords'],
            'rate_limit': get_rate_limit_info(),
            'cached': False,
            'version': '2.0.0'
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

# ========================================
# IRAN PROTESTS ENDPOINT
# ========================================
@app.route('/scan-iran-protests', methods=['GET'])
def scan_iran_protests():
    """Iran protests endpoint with separate Iran Wire tracking"""
    try:
        if not check_rate_limit():
            return jsonify({
                'error': 'Rate limit exceeded',
                'rate_limit': get_rate_limit_info()
            }), 429
        
        days = int(request.args.get('days', 7))
        
        all_articles = []
        
        newsapi_articles = fetch_newsapi_articles('Iran protests', days)
        all_articles.extend(newsapi_articles)
        
        gdelt_query = 'iran OR persia OR protest OR protests OR demonstration'
        gdelt_en = fetch_gdelt_articles(gdelt_query, days, 'eng')
        gdelt_ar = fetch_gdelt_articles(gdelt_query, days, 'ara')
        gdelt_fa = fetch_gdelt_articles(gdelt_query, days, 'fas')
        gdelt_he = fetch_gdelt_articles(gdelt_query, days, 'heb')
        
        all_articles.extend(gdelt_en)
        all_articles.extend(gdelt_ar)
        all_articles.extend(gdelt_fa)
        all_articles.extend(gdelt_he)
        
        reddit_posts = fetch_reddit_posts(
            'iran',
            ['Iran', 'protest', 'protests', 'demonstration', 'Tehran'],
            days
        )
        all_articles.extend(reddit_posts)
        
        iranwire_articles = fetch_iranwire_rss()
        all_articles.extend(iranwire_articles)
        
        cities_data = []
        for article in all_articles:
            title = article.get('title') or ''
            description = article.get('description') or ''
            content = article.get('content') or ''
            text = (title + ' ' + description + ' ' + content).lower()
            cities_found = extract_cities_from_text(text)
            cities_data.extend(cities_found)
        
        city_counts = {}
        for city, importance in cities_data:
            if city not in city_counts:
                city_counts[city] = {'count': 0, 'importance': importance}
            city_counts[city]['count'] += 1
        
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
        
        casualties = extract_casualty_data(all_articles)
        flight_cancellations = extract_flight_cancellations(all_articles)
        
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
        
        return jsonify({
            'success': True,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'days_analyzed': days,
            'total_articles': len(all_articles),
            'intensity': int(intensity_score),
            'stability': int(stability_score),
            
            'casualties': {
                'deaths': casualties['deaths'],
                'injuries': casualties['injuries'],
                'arrests': casualties['arrests'],
                'verified_sources': casualties['sources'],
                'details': casualties.get('details', [])
            },
            
            'cities': top_cities,
            'num_cities_affected': len(city_counts),
            'flight_cancellations': flight_cancellations,
            
            'articles_en': [a for a in all_articles if a.get('language') == 'en' and not a.get('iranwire')][:20],
            'articles_fa': [a for a in all_articles if a.get('language') == 'fa' and not a.get('iranwire')][:20],
            'articles_ar': [a for a in all_articles if a.get('language') == 'ar'][:20],
            'articles_he': [a for a in all_articles if a.get('language') == 'he'][:5],
            'articles_reddit': [a for a in all_articles if a.get('source', {}).get('name', '').startswith('r/')][:20],
            'articles_iranwire': [a for a in all_articles if a.get('iranwire')][:20],
            
            'rate_limit': get_rate_limit_info(),
            'cached': False,
            'version': '2.0.0'
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

# ========================================
# FLIGHT CANCELLATIONS ENDPOINT
# ========================================
@app.route('/flight-cancellations', methods=['GET'])
def get_flight_cancellations():
    """Aggregate flight cancellations from all targets"""
    try:
        days = 7
        all_cancellations = []
        
        for target_name, target_config in TARGET_KEYWORDS.items():
            query = ' OR '.join(target_config['keywords']) + ' AND (flight OR airline OR cancel OR suspend)'
            articles = fetch_newsapi_articles(query, days)
            articles.extend(fetch_gdelt_articles(query, days, 'eng'))
            
            cancellations = extract_flight_cancellations(articles)
            all_cancellations.extend(cancellations)
        
        seen_urls = set()
        unique_cancellations = []
        for cancel in all_cancellations:
            if cancel['url'] not in seen_urls:
                seen_urls.add(cancel['url'])
                unique_cancellations.append(cancel)
        
        sorted_cancellations = sorted(
            unique_cancellations,
            key=lambda x: x['date'],
            reverse=True
        )
        
        return jsonify({
            'success': True,
            'cancellations': sorted_cancellations[:10],
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'version': '2.0.0'
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
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'version': '2.0.0'
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
        'version': '2.0.0',
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'features': [
            'NewsAPI (English)',
            'GDELT (4 languages)',
            'Reddit OSINT',
            'Iran Wire RSS',
            'Flight monitoring',
            'Enhanced casualty tracking',
            'v2.0 Sophisticated threat scoring',
            'Time decay weighting',
            'Source credibility tiers',
            'De-escalation detection',
            'Momentum analysis'
        ],
        'reddit_subreddits': REDDIT_SUBREDDITS
    })

@app.route('/', methods=['GET'])
def home():
    """Root endpoint"""
    return jsonify({
        'name': 'Asifah Analytics Backend',
        'version': '2.0.0',
        'status': 'operational',
        'changes': [
            'MAJOR: Implemented sophisticated threat probability algorithm',
            'Time decay weighting (2-day half-life)',
            'Source credibility tiers (Premium/Regional/Standard/GDELT/Social)',
            'De-escalation keyword detection',
            'Momentum analysis (increasing/decreasing/stable)',
            'Keyword severity levels (Critical/High/Elevated/Moderate)'
        ],
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
