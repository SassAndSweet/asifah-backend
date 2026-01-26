"""
Asifah Analytics Backend v2.5.0
January 24, 2026

Changes from v2.4.0:
- ADDED: Syria Conflicts Tracker endpoint (/api/syria-conflicts)
- ADDED: Syria Direct RSS feed integration
- ADDED: SOHR (Syrian Observatory for Human Rights) RSS feed
- ADDED: Faction detection (HTS, SDF, ISIS, Druze, FSA, Al Qaeda)
- ADDED: Geographic location tracking for clashes
- ADDED: Displaced persons counting
- Syria now has both threat monitoring AND conflict tracking
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
# SOURCE WEIGHTS
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
            'Al Arabiya', 'The Jerusalem Post', 'Middle East Eye',
            'Syria Direct', 'SOHR'
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
# KEYWORD SEVERITY
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
# DE-ESCALATION
# ========================================
DEESCALATION_KEYWORDS = [
    'ceasefire', 'cease-fire', 'truce', 'peace talks', 'peace agreement',
    'diplomatic solution', 'negotiations', 'de-escalation', 'de-escalate',
    'tensions ease', 'tensions cool', 'tensions subside', 'calm',
    'defused', 'avoided', 'no plans to', 'ruled out', 'backs down',
    'restraint', 'diplomatic efforts', 'unlikely to strike'
]

# ========================================
# TARGET-SPECIFIC BASELINES
# ========================================
TARGET_BASELINES = {
    'hezbollah': {
        'base_adjustment': +10,
        'description': 'Ongoing Israeli operations in Lebanon'
    },
    'iran': {
        'base_adjustment': +5,
        'description': 'Elevated regional tensions'
    },
    'houthis': {
        'base_adjustment': 0,
        'description': 'Red Sea shipping disruptions ongoing'
    },
    'syria': {
        'base_adjustment': +8,
        'description': 'Post-Assad volatility, opportunistic strikes'
    }
}

# ========================================
# REDDIT CONFIGURATION
# ========================================
REDDIT_USER_AGENT = "AsifahAnalytics/2.5.0 (OSINT monitoring tool)"
REDDIT_SUBREDDITS = {
    "hezbollah": ["ForbiddenBromance", "Israel", "Lebanon"],
    "iran": ["Iran", "Israel", "geopolitics"],
    "houthis": ["Yemen", "Israel", "geopolitics"],
    "syria": ["syriancivilwar", "Syria", "geopolitics"]
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
    'flight cancellations', 'cancelled flights', 'suspend flights'
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
    },
    'syria': {
        'keywords': [
            'syria', 'syrian', 'damascus', 'aleppo', 'idlib', 'homs',
            'isis', 'isil', 'islamic state', 'daesh',
            'al qaeda', 'al-qaeda', 'alqaeda', 'jabhat al-nusra', 'nusra',
            'hts', 'hayat tahrir al-sham', 'tahrir al-sham',
            'sdf', 'syrian democratic forces', 'kurdish forces', 'kurds', 'ypg', 'ypj',
            'druze', 'druze community', 'golan', 'golan heights',
            'assad regime', 'post-assad', 'syria transition'
        ],
        'reddit_keywords': [
            'Syria', 'Damascus', 'ISIS', 'Al Qaeda', 'HTS', 'SDF', 
            'Kurds', 'Druze', 'Golan', 'Israel', 'Assad', 'civil war'
        ]
    }
}

# ========================================
# HELPER FUNCTIONS
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
        baseline_adjustment = TARGET_BASELINES.get(target, {}).get('base_adjustment', 0)
        return {
            'probability': min(25 + baseline_adjustment, 99),
            'momentum': 'stable',
            'breakdown': {
                'base_score': 25,
                'baseline_adjustment': baseline_adjustment,
                'article_count': 0,
                'weighted_score': 0
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
    
    base_score = 25
    baseline_adjustment = TARGET_BASELINES.get(target, {}).get('base_adjustment', 0)
    
    if weighted_score < 0:
        probability = max(10, base_score + baseline_adjustment + weighted_score)
    else:
        probability = base_score + baseline_adjustment + (weighted_score * 0.8)
    
    probability = int(probability)
    probability = max(10, min(probability, 95))
    
    return {
        'probability': probability,
        'momentum': momentum,
        'breakdown': {
            'base_score': base_score,
            'baseline_adjustment': baseline_adjustment,
            'article_count': len(articles),
            'recent_articles_48h': recent_articles,
            'older_articles': older_articles,
            'weighted_score': round(weighted_score, 2),
            'momentum_multiplier': momentum_multiplier,
            'deescalation_count': deescalation_count,
            'adaptive_multiplier': 0.8,
            'time_decay_applied': True,
            'source_weighting_applied': True,
            'formula': 'base(25) + adjustment + (weighted_score * 0.8)'
        },
        'top_contributors': sorted(article_details, 
                                   key=lambda x: abs(x['contribution']), 
                                   reverse=True)[:15]
    }

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
            return articles
        return []
    except Exception:
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
        
        response = requests.get(GDELT_BASE_URL, params=params, timeout=15)
        
        if response.status_code == 200:
            data = response.json()
            articles = data.get('articles', [])
            
            standardized = []
            lang_code = {'eng': 'en', 'ara': 'ar', 'heb': 'he', 'fas': 'fa'}.get(language, 'en')
            
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
    except Exception:
        return []

def fetch_reddit_posts(target, keywords, days=7):
    """Fetch Reddit posts from relevant subreddits"""
    subreddits = REDDIT_SUBREDDITS.get(target, [])
    if not subreddits:
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
            
            headers = {"User-Agent": REDDIT_USER_AGENT}
            
            time.sleep(2)
            
            response = requests.get(url, params=params, headers=headers, timeout=10)
            
            if response.status_code == 200:
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
                            "language": "en"
                        }
                        
                        all_posts.append(normalized_post)
            
        except Exception:
            continue
    
    return all_posts

def fetch_iranwire_rss():
    """Fetch articles from Iran Wire RSS feeds"""
    import xml.etree.ElementTree as ET
    
    articles = []
    
    feeds = {
        'en': 'https://iranwire.com/en/feed/',
        'fa': 'https://iranwire.com/fa/feed/'
    }
    
    for lang, feed_url in feeds.items():
        try:
            response = requests.get(feed_url, timeout=15)
            
            if response.status_code != 200:
                continue
            
            try:
                root = ET.fromstring(response.content)
            except ET.ParseError:
                continue
            
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
            
        except Exception:
            continue
    
    return articles

def fetch_hrana_rss():
    """Fetch articles from HRANA RSS feed"""
    import xml.etree.ElementTree as ET
    
    articles = []
    feed_url = 'https://en-hrana.org/feed/'
    
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/rss+xml, application/xml, text/xml, */*'
        }
        
        response = requests.get(feed_url, headers=headers, timeout=20)
        
        if response.status_code != 200:
            return []
        
        try:
            root = ET.fromstring(response.content)
        except ET.ParseError:
            return []
        
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
        
        return articles
        
    except Exception:
        return []

# ========================================
# SYRIA-SPECIFIC RSS FEEDS
# ========================================
def fetch_syria_direct_rss():
    """Fetch articles from Syria Direct RSS feed"""
    import xml.etree.ElementTree as ET
    
    articles = []
    feed_url = 'https://syriadirect.org/feed/'
    
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/rss+xml, application/xml, text/xml, */*'
        }
        
        response = requests.get(feed_url, headers=headers, timeout=20)
        
        if response.status_code != 200:
            return []
        
        try:
            root = ET.fromstring(response.content)
        except ET.ParseError:
            return []
        
        items = root.findall('.//item')
        
        for item in items[:20]:
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
                    'source': {'name': 'Syria Direct'},
                    'content': description,
                    'language': 'en'
                })
        
        return articles
        
    except Exception:
        return []

def fetch_sohr_rss():
    """Fetch articles from SOHR (Syrian Observatory for Human Rights) RSS feed"""
    import xml.etree.ElementTree as ET
    
    articles = []
    feed_url = 'https://www.syriahr.com/en/feed/'
    
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/rss+xml, application/xml, text/xml, */*'
        }
        
        response = requests.get(feed_url, headers=headers, timeout=20)
        
        if response.status_code != 200:
            return []
        
        try:
            root = ET.fromstring(response.content)
        except ET.ParseError:
            return []
        
        items = root.findall('.//item')
        
        for item in items[:20]:
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
                    'source': {'name': 'SOHR'},
                    'content': description,
                    'language': 'en'
                })
        
        return articles
        
    except Exception:
        return []

# ========================================
# SYRIA CONFLICT DATA EXTRACTION
# ========================================
def extract_syria_conflict_data(articles):
    """Extract conflict data from Syria articles"""
    
    conflict_data = {
        'deaths': 0,
        'displaced': 0,
        'factional_clashes': 0,
        'active_factions': set(),
        'clash_locations': {},
        'sources': set(),
        'details': []
    }
    
    # Faction keywords
    FACTIONS = {
        'HTS': ['hts', 'hayat tahrir al-sham', 'tahrir al-sham'],
        'SDF': ['sdf', 'syrian democratic forces', 'kurdish forces'],
        'ISIS': ['isis', 'isil', 'islamic state', 'daesh'],
        'Druze': ['druze'],
        'FSA': ['free syrian army', 'fsa', 'turkey-backed'],
        'Al Qaeda': ['al qaeda', 'al-qaeda', 'alqaeda']
    }
    
    # Syrian cities
    CITIES = [
        'damascus', 'aleppo', 'idlib', 'homs', 'deir ez-zor', 'deir ezzor',
        'raqqa', 'daraa', 'sweida', 'latakia', 'tartus', 'hama', 'qamishli'
    ]
    
    # Casualty patterns
    death_patterns = [
        r'(\d+)\s+(?:people\s+)?(?:were\s+)?killed',
        r'killed\s+(\d+)',
        r'(\d+)\s+dead',
        r'death\s+toll\s+(?:of\s+)?(\d+)',
        r'(\d+)\s+(?:civilians?\s+)?died'
    ]
    
    displaced_patterns = [
        r'(\d+(?:,\d{3})*)\s+(?:people\s+)?displaced',
        r'(\d+(?:,\d{3})*)\s+(?:people\s+)?fled',
        r'(\d+(?:,\d{3})*)\s+refugees?',
        r'displaced\s+(\d+(?:,\d{3})*)'
    ]
    
    clash_patterns = [
        r'clash(?:es)?',
        r'fighting',
        r'battle',
        r'combat',
        r'attack',
        r'offensive',
        r'strike'
    ]
    
    for article in articles:
        title = article.get('title', '').lower()
        description = article.get('description', '').lower()
        content = article.get('content', '').lower()
        text = f"{title} {description} {content}"
        
        source = article.get('source', {}).get('name', 'Unknown')
        url = article.get('url', '')
        
        # Extract deaths
        for pattern in death_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for match in matches:
                try:
                    num = int(match.replace(',', ''))
                    if num > conflict_data['deaths']:
                        conflict_data['deaths'] = num
                        conflict_data['sources'].add(source)
                        conflict_data['details'].append({
                            'type': 'deaths',
                            'count': num,
                            'source': source,
                            'url': url
                        })
                except:
                    pass
        
        # Extract displaced
        for pattern in displaced_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for match in matches:
                try:
                    num = int(match.replace(',', ''))
                    if num > conflict_data['displaced']:
                        conflict_data['displaced'] = num
                        conflict_data['sources'].add(source)
                        conflict_data['details'].append({
                            'type': 'displaced',
                            'count': num,
                            'source': source,
                            'url': url
                        })
                except:
                    pass
        
        # Count clashes
        for pattern in clash_patterns:
            if re.search(pattern, text, re.IGNORECASE):
                conflict_data['factional_clashes'] += 1
                break
        
        # Detect factions
        for faction_name, keywords in FACTIONS.items():
            for keyword in keywords:
                if keyword in text:
                    conflict_data['active_factions'].add(faction_name)
                    break
        
        # Detect locations
        for city in CITIES:
            if city in text:
                if city not in conflict_data['clash_locations']:
                    conflict_data['clash_locations'][city] = 0
                conflict_data['clash_locations'][city] += 1
    
    # Convert sets to lists
    conflict_data['active_factions'] = list(conflict_data['active_factions'])
    conflict_data['sources'] = list(conflict_data['sources'])
    
    # Sort locations by frequency
    conflict_data['clash_locations'] = dict(
        sorted(conflict_data['clash_locations'].items(), 
               key=lambda x: x[1], 
               reverse=True)[:10]
    )
    
    return conflict_data

# ========================================
# SYRIA CONFLICTS ENDPOINT
# Add this to app.py
# ========================================

# First, add Syria RSS feed fetchers

def fetch_syria_direct_rss():
    """Fetch articles from Syria Direct RSS feed"""
    import xml.etree.ElementTree as ET
    
    articles = []
    feed_url = 'https://syriadirect.org/feed/'
    
    try:
        print(f"[v2.5.0] Syria Direct: Fetching RSS...")
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/rss+xml, application/xml, text/xml, */*',
            'Accept-Language': 'en-US,en;q=0.9'
        }
        
        response = requests.get(feed_url, headers=headers, timeout=20)
        
        if response.status_code != 200:
            print(f"[v2.5.0] Syria Direct: HTTP {response.status_code}")
            return []
        
        try:
            root = ET.fromstring(response.content)
        except ET.ParseError as e:
            print(f"[v2.5.0] Syria Direct: XML parse error: {e}")
            return []
        
        items = root.findall('.//item')
        if not items:
            items = root.findall('.//{http://www.w3.org/2005/Atom}entry')
        
        for item in items[:20]:
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
                    'source': {'name': 'Syria Direct'},
                    'content': description,
                    'language': 'en'
                })
        
        print(f"[v2.5.0] Syria Direct: ✓ Fetched {len(articles)} articles")
        return articles
        
    except requests.Timeout:
        print(f"[v2.5.0] Syria Direct: Timeout after 20s")
        return []
    except requests.ConnectionError:
        print(f"[v2.5.0] Syria Direct: Connection error")
        return []
    except Exception as e:
        print(f"[v2.5.0] Syria Direct: Error: {str(e)[:100]}")
        return []


def fetch_sohr_rss():
    """Fetch articles from Syrian Observatory for Human Rights RSS feed"""
    import xml.etree.ElementTree as ET
    
    articles = []
    feed_url = 'https://www.syriahr.com/en/feed/'
    
    try:
        print(f"[v2.5.0] SOHR: Fetching RSS...")
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/rss+xml, application/xml, text/xml, */*',
            'Accept-Language': 'en-US,en;q=0.9'
        }
        
        response = requests.get(feed_url, headers=headers, timeout=20)
        
        if response.status_code != 200:
            print(f"[v2.5.0] SOHR: HTTP {response.status_code}")
            return []
        
        try:
            root = ET.fromstring(response.content)
        except ET.ParseError as e:
            print(f"[v2.5.0] SOHR: XML parse error: {e}")
            return []
        
        items = root.findall('.//item')
        if not items:
            items = root.findall('.//{http://www.w3.org/2005/Atom}entry')
        
        for item in items[:20]:
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
                    'source': {'name': 'SOHR'},
                    'content': description,
                    'language': 'en'
                })
        
        print(f"[v2.5.0] SOHR: ✓ Fetched {len(articles)} articles")
        return articles
        
    except requests.Timeout:
        print(f"[v2.5.0] SOHR: Timeout after 20s")
        return []
    except requests.ConnectionError:
        print(f"[v2.5.0] SOHR: Connection error")
        return []
    except Exception as e:
        print(f"[v2.5.0] SOHR: Error: {str(e)[:100]}")
        return []


# Syria-specific casualty/conflict extraction keywords
SYRIA_CONFLICT_KEYWORDS = {
    'deaths': [
        'killed', 'dead', 'died', 'death toll', 'fatalities', 'deaths',
        'killed in clashes', 'killed in fighting', 'civilians killed',
        'fighters killed', 'combatants killed'
    ],
    'displaced': [
        'displaced', 'fled', 'refugees', 'internally displaced',
        'evacuated', 'forced to leave', 'abandoned homes'
    ],
    'clashes': [
        'clashes', 'fighting', 'battles', 'combat', 'confrontation',
        'armed conflict', 'skirmishes', 'firefight', 'engagement'
    ]
}

SYRIA_FACTIONS = [
    'SDF', 'Syrian Democratic Forces',
    'HTS', "Hay'at Tahrir al-Sham", 'Tahrir al-Sham',
    'SNA', 'Syrian National Army',
    'FSA', 'Free Syrian Army',
    'ISIS', 'Islamic State', 'ISIL',
    'PKK', 'YPG', 'Kurdish forces',
    'Turkish forces', 'Turkey',
    'Russian forces', 'Russia',
    'Iranian forces', 'Iran',
    'Hezbollah'
]


def extract_syria_conflict_data(articles):
    """Extract conflict statistics from Syria articles"""
    
    conflict_data = {
        'deaths': 0,
        'displaced': 0,
        'factional_clashes': 0,
        'clash_locations': {},
        'active_factions': set(),
        'details': []
    }
    
    # Number patterns (same as casualty extraction)
    number_patterns = [
        r'(\d+(?:,\d{3})*)\s+(?:people\s+)?',
        r'(?:more than|over|at least)\s+(\d+(?:,\d{3})*)',
        r'(\d+(?:,\d{3})*)\s+(?:have been|were|are)',
        r'(?:roughly|approximately|around)\s+(\d+(?:,\d{3})*)',
        r'(hundreds?|thousands?|tens of thousands)',
    ]
    
    # Syrian city patterns
    syrian_cities = [
        'damascus', 'aleppo', 'homs', 'hama', 'latakia', 'deir ez-zor',
        'raqqa', 'idlib', 'daraa', 'kobani', 'manbij', 'afrin', 'qamishli'
    ]
    
    for article in articles:
        title = (article.get('title') or '').lower()
        description = (article.get('description') or '').lower()
        content = (article.get('content') or '').lower()
        text = f"{title} {description} {content}"
        
        source = article.get('source', {}).get('name', 'Unknown')
        url = article.get('url', '')
        
        # Extract deaths
        for keyword in SYRIA_CONFLICT_KEYWORDS['deaths']:
            if keyword in text:
                for pattern in number_patterns:
                    match = re.search(pattern + r'\s*' + re.escape(keyword), text, re.IGNORECASE)
                    if match:
                        num_str = match.group(1).replace(',', '')
                        try:
                            if 'hundred' in num_str.lower():
                                num = 100
                            elif 'thousand' in num_str.lower():
                                if 'tens of' in text:
                                    num = 10000
                                else:
                                    num = 1000
                            else:
                                num = int(num_str)
                            
                            if num > 0:
                                conflict_data['deaths'] += num
                                conflict_data['details'].append({
                                    'type': 'deaths',
                                    'count': num,
                                    'source': source,
                                    'url': url
                                })
                        except:
                            pass
                        break
                break
        
        # Extract displaced
        for keyword in SYRIA_CONFLICT_KEYWORDS['displaced']:
            if keyword in text:
                for pattern in number_patterns:
                    match = re.search(pattern + r'\s*' + re.escape(keyword), text, re.IGNORECASE)
                    if match:
                        num_str = match.group(1).replace(',', '')
                        try:
                            if 'hundred' in num_str.lower():
                                num = 100
                            elif 'thousand' in num_str.lower():
                                if 'tens of' in text or 'hundreds of' in text:
                                    num = 50000
                                else:
                                    num = 1000
                            else:
                                num = int(num_str)
                            
                            if num > 0:
                                conflict_data['displaced'] += num
                                conflict_data['details'].append({
                                    'type': 'displaced',
                                    'count': num,
                                    'source': source,
                                    'url': url
                                })
                        except:
                            pass
                        break
                break
        
        # Count clashes
        for keyword in SYRIA_CONFLICT_KEYWORDS['clashes']:
            if keyword in text:
                conflict_data['factional_clashes'] += 1
                conflict_data['details'].append({
                    'type': 'clashes',
                    'count': 1,
                    'source': source,
                    'url': url
                })
                break
        
        # Identify factions
        for faction in SYRIA_FACTIONS:
            if faction.lower() in text:
                conflict_data['active_factions'].add(faction)
        
        # Identify locations
        for city in syrian_cities:
            if city in text:
                conflict_data['clash_locations'][city] = conflict_data['clash_locations'].get(city, 0) + 1
    
    conflict_data['active_factions'] = list(conflict_data['active_factions'])
    conflict_data['num_factions'] = len(conflict_data['active_factions'])
    
    print(f"[v2.5.0] Syria Conflict Data:")
    print(f"  Deaths: {conflict_data['deaths']}")
    print(f"  Displaced: {conflict_data['displaced']}")
    print(f"  Clashes: {conflict_data['factional_clashes']}")
    print(f"  Active Factions: {conflict_data['num_factions']}")
    
    return conflict_data


# ========================================
# SYRIA CONFLICTS ENDPOINT
# ========================================
@app.route('/api/syria-conflicts', methods=['GET'])
def api_syria_conflicts():
    """Syria post-Assad conflict tracking endpoint"""
    try:
        if not check_rate_limit():
            return jsonify({
                'error': 'Rate limit exceeded',
                'rate_limit': get_rate_limit_info()
            }), 429
        
        days = int(request.args.get('days', 7))
        
        # Fetch Syria-specific sources
        try:
            syria_direct_articles = fetch_syria_direct_rss()
        except Exception as e:
            print(f"Syria Direct error: {e}")
            syria_direct_articles = []
        
        try:
            sohr_articles = fetch_sohr_rss()
        except Exception as e:
            print(f"SOHR error: {e}")
            sohr_articles = []
        
        # Fetch NewsAPI articles
        try:
            newsapi_articles = fetch_newsapi_articles('Syria conflict OR Syria clashes', days)
        except Exception as e:
            print(f"NewsAPI error: {e}")
            newsapi_articles = []
        
        # GDELT query for Syria
        gdelt_query = 'syria OR syrian OR damascus OR aleppo'
        
        try:
            gdelt_en = fetch_gdelt_articles(gdelt_query, days, 'eng')
        except Exception as e:
            print(f"GDELT EN error: {e}")
            gdelt_en = []
        
        try:
            gdelt_ar = fetch_gdelt_articles(gdelt_query, days, 'ara')
        except Exception as e:
            print(f"GDELT AR error: {e}")
            gdelt_ar = []
        
        try:
            gdelt_he = fetch_gdelt_articles(gdelt_query, days, 'heb')
        except Exception as e:
            print(f"GDELT HE error: {e}")
            gdelt_he = []
        
        try:
            gdelt_fa = fetch_gdelt_articles(gdelt_query, days, 'fas')
        except Exception as e:
            print(f"GDELT FA error: {e}")
            gdelt_fa = []
        
        # Reddit posts
        try:
            reddit_posts = fetch_reddit_posts(
                'syria',  # Note: add 'syria' to REDDIT_SUBREDDITS dict
                ['Syria', 'Syrian', 'Damascus', 'Assad', 'conflict'],
                days
            )
        except Exception as e:
            print(f"Reddit error: {e}")
            reddit_posts = []
        
        all_articles = (syria_direct_articles + sohr_articles + newsapi_articles + 
                       gdelt_en + gdelt_ar + gdelt_he + gdelt_fa + reddit_posts)
        
        print(f"[v2.5.0] Total Syria articles fetched: {len(all_articles)}")
        
        # Extract conflict data
        try:
            conflict_data = extract_syria_conflict_data(all_articles)
        except Exception as e:
            print(f"Conflict data extraction error: {e}")
            conflict_data = {
                'deaths': 0,
                'displaced': 0,
                'factional_clashes': 0,
                'clash_locations': {},
                'active_factions': [],
                'num_factions': 0,
                'details': []
            }
        
        return jsonify({
            'success': True,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'days_analyzed': days,
            'total_articles': len(all_articles),
            
            'conflict_data': conflict_data,
            
            # Language-separated articles
            'articles_syria_direct': syria_direct_articles[:20],
            'articles_sohr': sohr_articles[:20],
            'articles_en': [a for a in all_articles if a.get('language') == 'en'][:20],
            'articles_he': [a for a in all_articles if a.get('language') == 'he'][:20],
            'articles_ar': [a for a in all_articles if a.get('language') == 'ar'][:20],
            'articles_fa': [a for a in all_articles if a.get('language') == 'fa'][:20],
            'articles_reddit': [a for a in all_articles if a.get('source', {}).get('name', '').startswith('r/')][:20],
            
            'cached': False,
            'version': '2.5.0-SYRIA'
        })
        
    except Exception as e:
        print(f"Error in /api/syria-conflicts: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e),
            'conflict_data': {
                'deaths': 0,
                'displaced': 0,
                'factional_clashes': 0,
                'clash_locations': {},
                'active_factions': [],
                'num_factions': 0,
                'details': []
            },
            'articles_syria_direct': [],
            'articles_sohr': [],
            'articles_en': [],
            'articles_he': [],
            'articles_ar': [],
            'articles_fa': [],
            'articles_reddit': [],
            'total_articles': 0
        }), 500


# ========================================
# UPDATE: Add Syria to REDDIT_SUBREDDITS
# ========================================
# Find this section in app.py and UPDATE it:

REDDIT_SUBREDDITS = {
    "hezbollah": ["ForbiddenBromance", "Israel", "Lebanon"],
    "iran": ["Iran", "Israel", "geopolitics"],
    "houthis": ["Yemen", "Israel", "geopolitics"],
    "syria": ["Syria", "syriancivilwar", "geopolitics"]  # ADD THIS LINE
}

# ========================================
# IRAN PROTESTS DATA EXTRACTION
# ========================================
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
        return 100
    elif 'thousand' in num_str or 'thousands' in num_str:
        match = re.search(r'(\d+)\s*thousand', num_str)
        if match:
            return int(match.group(1)) * 1000
        return 1000
    elif 'dozen' in num_str or 'dozens' in num_str:
        if 'several' in num_str:
            return 24
        return 12
    
    return 0

CASUALTY_KEYWORDS = {
    'deaths': [
        'killed', 'dead', 'died', 'death toll', 'fatalities', 'deaths',
        'shot dead', 'gunned down', 'killed by', 'killed in'
    ],
    'injuries': [
        'injured', 'wounded', 'hurt', 'injuries', 'casualties',
        'hospitalized', 'critical condition', 'serious injuries'
    ],
    'arrests': [
        'arrested', 'detained', 'detention', 'arrest', 'arrests',
        'taken into custody', 'custody', 'apprehended'
    ]
}

def extract_casualty_data(articles):
    """Extract verified casualty numbers from articles"""
    casualties = {
        'deaths': 0,
        'injuries': 0,
        'arrests': 0,
        'sources': set(),
        'details': [],
        'articles_without_numbers': []
    }
    
    number_patterns = [
        r'(\d+(?:,\d{3})*)\s+(?:people\s+)?.{0,20}?',
        r'(?:more than|over|at least)\s+(\d+(?:,\d{3})*)',
    ]
    
    for article in articles:
        title = article.get('title') or ''
        description = article.get('description') or ''
        content = article.get('content') or ''
        text = (title + ' ' + description + ' ' + content).lower()
        
        source = article.get('source', {}).get('name', 'Unknown')
        url = article.get('url', '')
        
        for casualty_type in ['deaths', 'injuries', 'arrests']:
            for keyword in CASUALTY_KEYWORDS[casualty_type]:
                if keyword in text:
                    casualties['sources'].add(source)
                    
                    for pattern in number_patterns:
                        match = re.search(pattern + re.escape(keyword), text, re.IGNORECASE)
                        if match:
                            num_str = match.group(1).replace(',', '')
                            try:
                                num = int(num_str)
                                if num > casualties[casualty_type]:
                                    casualties[casualty_type] = num
                                    casualties['details'].append({
                                        'type': casualty_type,
                                        'count': num,
                                        'source': source,
                                        'url': url
                                    })
                            except:
                                pass
                            break
                    break
    
    casualties['sources'] = list(casualties['sources'])
    return casualties

def extract_hrana_structured_data(articles):
    """Extract structured protest statistics from HRANA articles"""
    
    structured_data = {
        'confirmed_deaths': 0,
        'deaths_under_investigation': 0,
        'seriously_injured': 0,
        'total_arrests': 0,
        'is_hrana_verified': False
    }
    
    patterns = {
        'confirmed_deaths': [
            r'confirmed\s+deaths?\s*:?\s*(\d{1,3}(?:,\d{3})*)',
        ],
        'seriously_injured': [
            r'seriously?\s+injured\s*:?\s*(\d{1,3}(?:,\d{3})*)',
        ],
        'total_arrests': [
            r'total\s+arrests?\s*:?\s*(\d{1,3}(?:,\d{3})*)',
        ]
    }
    
    hrana_articles = [a for a in articles if a.get('source', {}).get('name') == 'HRANA']
    
    for article in hrana_articles:
        content = article.get('content', '').lower()
        
        for key, pattern_list in patterns.items():
            for pattern in pattern_list:
                match = re.search(pattern, content, re.IGNORECASE)
                if match:
                    number_str = match.group(1).replace(',', '')
                    try:
                        number = int(number_str)
                        if number > structured_data[key]:
                            structured_data[key] = number
                            structured_data['is_hrana_verified'] = True
                    except:
                        pass
    
    return structured_data

# ========================================
# API ENDPOINTS
# ========================================
@app.route('/api/threat/<target>', methods=['GET'])
def api_threat(target):
    """API endpoint compatible with frontend"""
    try:
        days = int(request.args.get('days', 7))
        
        if not check_rate_limit():
            return jsonify({
                'success': False,
                'error': 'Rate limit reached',
                'probability': 0,
                'rate_limited': True
            }), 200
        
        if target not in TARGET_KEYWORDS:
            return jsonify({
                'success': False,
                'error': f"Invalid target"
            }), 400
        
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
        
        scoring_result = calculate_threat_probability(all_articles, days, target)
        probability = scoring_result['probability']
        momentum = scoring_result['momentum']
        breakdown = scoring_result['breakdown']
        
        if probability < 30:
            timeline = "180+ Days"
        elif probability < 50:
            timeline = "91-180 Days"
        elif probability < 70:
            timeline = "31-90 Days"
        else:
            timeline = "0-30 Days"
        
        unique_sources = len(set(a.get('source', {}).get('name', 'Unknown') for a in all_articles))
        if len(all_articles) >= 20 and unique_sources >= 8:
            confidence = "High"
        elif len(all_articles) >= 10 and unique_sources >= 5:
            confidence = "Medium"
        else:
            confidence = "Low"
        
        top_articles = []
        top_contributors = scoring_result.get('top_contributors', [])
        
        for contributor in top_contributors:
            matching_article = None
            for article in all_articles:
                if article.get('source', {}).get('name', '') == contributor['source']:
                    matching_article = article
                    break
            
            if matching_article:
                top_articles.append({
                    'title': matching_article.get('title', 'No title'),
                    'source': contributor['source'],
                    'url': matching_article.get('url', ''),
                    'publishedAt': matching_article.get('publishedAt', ''),
                    'contribution': contributor['contribution'],
                    'contribution_percent': abs(contributor['contribution']) / max(abs(breakdown['weighted_score']), 1) * 100,
                    'severity': contributor['severity'],
                    'source_weight': contributor['source_weight'],
                    'time_decay': contributor['time_decay'],
                    'deescalation': contributor['deescalation']
                })
        
        return jsonify({
            'success': True,
            'target': target,
            'probability': probability,
            'timeline': timeline,
            'confidence': confidence,
            'momentum': momentum,
            'total_articles': len(all_articles),
            'recent_articles_48h': breakdown['recent_articles_48h'],
            'older_articles': breakdown.get('older_articles', 0),
            'deescalation_count': breakdown['deescalation_count'],
            'scoring_breakdown': breakdown,
            'top_scoring_articles': top_articles,
            'escalation_keywords': ESCALATION_KEYWORDS,
            'target_keywords': TARGET_KEYWORDS[target]['keywords'],
            'cached': False,
            'version': '2.5.0'
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'probability': 0
        }), 500

@app.route('/scan-iran-protests', methods=['GET'])
def scan_iran_protests():
    """Iran protests endpoint"""
    try:
        if not check_rate_limit():
            return jsonify({'error': 'Rate limit exceeded'}), 429
        
        days = int(request.args.get('days', 7))
        
        newsapi_articles = fetch_newsapi_articles('Iran protests', days)
        gdelt_query = 'iran OR protest'
        gdelt_en = fetch_gdelt_articles(gdelt_query, days, 'eng')
        gdelt_ar = fetch_gdelt_articles(gdelt_query, days, 'ara')
        gdelt_fa = fetch_gdelt_articles(gdelt_query, days, 'fas')
        gdelt_he = fetch_gdelt_articles(gdelt_query, days, 'heb')
        reddit_posts = fetch_reddit_posts('iran', ['Iran', 'protest'], days)
        iranwire_articles = fetch_iranwire_rss()
        hrana_articles = fetch_hrana_rss()
        
        all_articles = (newsapi_articles + gdelt_en + gdelt_ar + gdelt_fa + 
                       gdelt_he + reddit_posts + iranwire_articles + hrana_articles)
        
        hrana_data = extract_hrana_structured_data(hrana_articles)
        casualties_regex = extract_casualty_data(all_articles)
        
        if hrana_data['is_hrana_verified']:
            casualties = {
                'deaths': max(hrana_data['confirmed_deaths'], casualties_regex['deaths']),
                'injuries': max(hrana_data['seriously_injured'], casualties_regex['injuries']),
                'arrests': max(hrana_data['total_arrests'], casualties_regex['arrests']),
                'sources': list(set(['HRANA'] + casualties_regex['sources'])),
                'details': casualties_regex['details'],
                'hrana_verified': True
            }
        else:
            casualties = casualties_regex
            casualties['hrana_verified'] = False
        
        articles_per_day = len(all_articles) / days if days > 0 else 0
        intensity_score = min(articles_per_day * 2 + casualties['deaths'] * 0.5, 100)
        stability_score = 100 - intensity_score
        
        return jsonify({
            'success': True,
            'days_analyzed': days,
            'total_articles': len(all_articles),
            'intensity': int(intensity_score),
            'stability': int(stability_score),
            'casualties': casualties,
            'cities': [],
            'num_cities_affected': 5,
            'articles_en': [a for a in all_articles if a.get('language') == 'en'][:20],
            'articles_fa': [a for a in all_articles if a.get('language') == 'fa'][:20],
            'articles_ar': [a for a in all_articles if a.get('language') == 'ar'][:20],
            'articles_he': [a for a in all_articles if a.get('language') == 'he'][:20],
            'articles_reddit': reddit_posts[:20],
            'articles_iranwire': iranwire_articles[:20],
            'articles_hrana': hrana_articles[:20],
            'version': '2.5.0'
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/syria-conflicts', methods=['GET'])
def api_syria_conflicts():
    """Syria conflicts endpoint"""
    try:
        if not check_rate_limit():
            return jsonify({'error': 'Rate limit exceeded'}), 429
        
        days = int(request.args.get('days', 7))
        
        syria_direct_articles = fetch_syria_direct_rss()
        sohr_articles = fetch_sohr_rss()
        newsapi_articles = fetch_newsapi_articles('Syria conflict', days)
        
        gdelt_query = 'syria OR damascus OR conflict'
        gdelt_en = fetch_gdelt_articles(gdelt_query, days, 'eng')
        gdelt_ar = fetch_gdelt_articles(gdelt_query, days, 'ara')
        gdelt_he = fetch_gdelt_articles(gdelt_query, days, 'heb')
        gdelt_fa = fetch_gdelt_articles(gdelt_query, days, 'fas')
        
        reddit_posts = fetch_reddit_posts('syria', ['Syria', 'Damascus', 'conflict'], days)
        
        all_articles = (syria_direct_articles + sohr_articles + newsapi_articles + 
                       gdelt_en + gdelt_ar + gdelt_he + gdelt_fa + reddit_posts)
        
        conflict_data = extract_syria_conflict_data(all_articles)
        
        return jsonify({
            'success': True,
            'days_analyzed': days,
            'total_articles': len(all_articles),
            'conflict_data': {
                'deaths': conflict_data['deaths'],
                'displaced': conflict_data['displaced'],
                'factional_clashes': conflict_data['factional_clashes'],
                'active_factions': conflict_data['active_factions'],
                'num_factions': len(conflict_data['active_factions']),
                'clash_locations': conflict_data['clash_locations'],
                'verified_sources': conflict_data['sources'],
                'details': conflict_data['details']
            },
            'articles_syria_direct': syria_direct_articles[:20],
            'articles_sohr': sohr_articles[:20],
            'articles_en': [a for a in all_articles if a.get('language') == 'en'][:20],
            'articles_he': [a for a in all_articles if a.get('language') == 'he'][:20],
            'articles_ar': [a for a in all_articles if a.get('language') == 'ar'][:20],
            'articles_fa': [a for a in all_articles if a.get('language') == 'fa'][:20],
            'articles_reddit': reddit_posts[:20],
            'version': '2.5.0-Syria'
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/', methods=['GET'])
def home():
    """Root endpoint"""
    return jsonify({
        'status': 'Backend is running',
        'version': '2.5.0',
        'endpoints': {
            '/api/threat/<target>': 'Threat assessment for hezbollah, iran, houthis, or syria',
            '/scan-iran-protests': 'Iran protests data',
            '/api/syria-conflicts': 'Syria conflicts tracker',
            '/rate-limit': 'Rate limit status',
            '/health': 'Health check'
        }
    })

@app.route('/rate-limit', methods=['GET'])
def rate_limit_endpoint():
    """Rate limit status"""
    return jsonify(get_rate_limit_info())

@app.route('/health', methods=['GET'])
def health():
    """Health check"""
    return jsonify({
        'status': 'healthy',
        'version': '2.5.0-Syria',
        'timestamp': datetime.now(timezone.utc).isoformat()
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
