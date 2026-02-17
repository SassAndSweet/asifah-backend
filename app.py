"""
Asifah Analytics Backend v2.8.0
February 8, 2026

All endpoints working:
- /api/threat/<target> (hezbollah, iran, houthis, syria)
- /scan-iran-protests (with HRANA data + Regime Stability)
- /api/syria-conflicts
- /api/iran-strike-probability (with caching)
- /api/hezbollah-activity (with caching)
- /api/houthis-threat (with caching)
- /api/syria-conflict (with caching)
"""

# ========================================
# IMPORTS
# ========================================
# Standard library imports first
from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
import os
import time
import re
import math
import json
from pathlib import Path
from iran_protests import get_iran_oil_data

# Local imports last
from rss_monitor import (
    fetch_all_rss,
    enhance_article_with_leadership,
    apply_leadership_multiplier,
    fetch_airline_disruptions
)

# ========================================
# CACHING SYSTEM
# ========================================

# Cache file location (persistent across restarts)
CACHE_FILE = '/tmp/threat_cache.json'

def load_cache():
    """Load cached threat data from file"""
    try:
        if Path(CACHE_FILE).exists():
            with open(CACHE_FILE, 'r') as f:
                cache = json.load(f)
                print(f"[Cache] Loaded cache with {len(cache)} entries")
                return cache
        print("[Cache] No cache file found, starting fresh")
        return {}
    except Exception as e:
        print(f"[Cache] Error loading cache: {e}")
        return {}

def save_cache(cache_data):
    """Save threat data to cache file"""
    try:
        with open(CACHE_FILE, 'w') as f:
            json.dump(cache_data, f, indent=2)
        print(f"[Cache] Saved cache with {len(cache_data)} entries")
        return True
    except Exception as e:
        print(f"[Cache] Error saving cache: {e}")
        return False

def get_cached_result(target):
    """Get cached result for a target"""
    cache = load_cache()
    return cache.get(target)

def update_cache(target, data):
    """Update cache for a specific target"""
    cache = load_cache()
    cache[target] = {
        **data,
        'cached_at': datetime.now(timezone.utc).isoformat(),
        'cached': True
    }
    save_cache(cache)
    print(f"[Cache] Updated {target} cache")

def is_cache_fresh(cached_data, max_age_hours=6):
    """Check if cached data is still fresh (default: 6 hours)"""
    if not cached_data:
        return False
    
    try:
        cached_at = datetime.fromisoformat(cached_data.get('cached_at', ''))
        age = datetime.now(timezone.utc) - cached_at
        is_fresh = age.total_seconds() < (max_age_hours * 3600)
        
        if is_fresh:
            hours_old = age.total_seconds() / 3600
            print(f"[Cache] Data is {hours_old:.1f} hours old (fresh)")
        else:
            print(f"[Cache] Data is stale (>{max_age_hours} hours old)")
        
        return is_fresh
    except Exception as e:
        print(f"[Cache] Error checking freshness: {e}")
        return False

# ========================================
# FLASK APP INITIALIZATION
# ========================================

app = Flask(__name__)
CORS(app, resources={
    r"/api/*": {
        "origins": [
            "https://asifahanalytics.com",
            "https://www.asifahanalytics.com",
            "http://localhost:*"
        ],
        "methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["Content-Type"]
    },
    r"/flight-cancellations": {
        "origins": [
            "https://asifahanalytics.com",
            "https://www.asifahanalytics.com",
            "http://localhost:*"
        ],
        "methods": ["GET", "OPTIONS"],
        "allow_headers": ["Content-Type"]
    },
    r"/rate-limit": {
        "origins": [
            "https://asifahanalytics.com",
            "https://www.asifahanalytics.com",
            "http://localhost:*"
        ],
        "methods": ["GET", "OPTIONS"],
        "allow_headers": ["Content-Type"]
    },
    r"/api/jordan-threat": {
        "origins": [
            "https://asifahanalytics.com",
            "https://www.asifahanalytics.com",
            "http://localhost:*"
        ],
        "methods": ["GET", "OPTIONS"],
        "allow_headers": ["Content-Type"]
    },
    r"/scan-iran-protests": {
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

NEWSAPI_KEY = os.environ.get('NEWSAPI_KEY')
EODHD_API_KEY = os.environ.get('EODHD_API_KEY', '697925068da530.81277377')
ALPHA_VANTAGE_KEY = os.environ.get('ALPHA_VANTAGE_KEY', '6V1C73D5FYVIDWM5')
GDELT_BASE_URL = "http://api.gdeltproject.org/api/v2/doc/doc"

# Reddit User Agent
REDDIT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

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
# OSINT DEFENDER INSTAGRAM FEED
# ========================================
OSINT_INSTAGRAM_HANDLE = 'osintdefender'

# Middle East country keyword detection
MIDDLE_EAST_COUNTRIES = {
    'IRAN': ['iran', 'iranian', 'tehran', 'irgc', 'khamenei', 'revolutionary guard'],
    'SYRIA': ['syria', 'syrian', 'damascus', 'aleppo', 'assad', 'hts'],
    'LEBANON': ['lebanon', 'lebanese', 'beirut', 'hezbollah', 'nasrallah'],
    'ISRAEL': ['israel', 'israeli', 'idf', 'tel aviv', 'jerusalem', 'netanyahu'],
    'YEMEN': ['yemen', 'yemeni', 'houthi', 'houthis', 'sanaa', 'ansarallah'],
    'SAUDI ARABIA': ['saudi', 'riyadh', 'mbs', 'kingdom'],
    'UAE': ['uae', 'dubai', 'abu dhabi', 'emirates'],
    'JORDAN': ['jordan', 'jordanian', 'amman'],
    'EGYPT': ['egypt', 'egyptian', 'cairo', 'sisi'],
    'IRAQ': ['iraq', 'iraqi', 'baghdad', 'erbil'],
    'TURKEY': ['turkey', 'turkish', 'ankara', 'erdogan', 'istanbul'],
    'QATAR': ['qatar', 'doha'],
    'KUWAIT': ['kuwait'],
    'BAHRAIN': ['bahrain', 'manama'],
    'OMAN': ['oman', 'muscat'],
    'GAZA': ['gaza', 'hamas', 'palestinian'],
    'WEST BANK': ['west bank', 'jenin', 'ramallah']
}

# Cache for Instagram feed (30 minute TTL)
instagram_feed_cache = {
    'data': None,
    'expires_at': 0
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
    },
    'jordan': {
        'base_adjustment': +3,
        'description': 'Stable US ally, elevated due to regional spillover risk'
    }
}

REDDIT_SUBREDDITS = {
    "hezbollah": [
        "ForbiddenBromance", "Israel", "Lebanon",
        "geopolitics", "anime_titties", "CredibleDefense"
    ],
    "iran": [
        "Iran", "Israel", "geopolitics",
        "iranpolitics", "CredibleDefense", "anime_titties"
    ],
    "houthis": [
        "Yemen", "Israel", "geopolitics",
        "CredibleDefense", "YemeniCrisis", "anime_titties"
    ],
    "syria": [
        "syriancivilwar", "Syria", "geopolitics",
        "CredibleDefense", "anime_titties"
    ],
    "jordan": [
        "jordan", "geopolitics", "CredibleDefense",
        "anime_titties", "Israel", "syriancivilwar"
    ]
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
    },
    'jordan': {
        'keywords': [
            'jordan', 'jordanian', 'amman', 'king abdullah', 'hashemite',
            'jordan border', 'jordan airspace', 'jordan intercept',
            'jordan military', 'jordan air defense', 'jordan drone',
            'jordan syria border', 'rukban', 'captagon jordan',
            'jordan protest', 'amman protest', 'jordan palestinian',
            'tower 22', 'tanf jordan', 'jordan us base',
            'jordan muslim brotherhood', 'east bank', 'jordan refugee',
            'jordan economic', 'jordan imf', 'zarqa', 'irbid', 'mafraq'
        ],
        'reddit_keywords': [
            'Jordan', 'Amman', 'Jordanian', 'King Abdullah', 'Hashemite',
            'Tower 22', 'Captagon', 'Jordan border', 'Jordan airspace',
            'Jordan protest', 'Palestinian', 'ISIS Jordan'
        ]
    }
}

# ========================================
# SYRIA CONFLICT KEYWORDS
# ========================================
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

# ========================================
# CASUALTY KEYWORDS (for Iran protests)
# ========================================
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

# ========================================
# MAIN THREAT PROBABILITY CALCULATOR
# ========================================
def calculate_threat_probability(articles, days_analyzed=7, target='iran'):
    """
    Calculate sophisticated threat probability score
    
    Used by all 4 threat cards (Iran, Hezbollah, Houthis, Syria)
    Supports dynamic time windows (24h, 48h, 7d, 30d)
    
    Returns:
    - probability: 0-100 score
    - momentum: increasing/decreasing/stable
    - breakdown: detailed scoring components
    - top_contributors: articles ranked by impact
    """
    
    # Handle empty articles case
    if not articles:
        baseline_adjustment = TARGET_BASELINES.get(target, {}).get('base_adjustment', 0)
        return {
            'probability': min(25 + baseline_adjustment, 99),
            'momentum': 'stable',
            'breakdown': {
                'base_score': 25,
                'baseline_adjustment': baseline_adjustment,
                'article_count': 0,
                'weighted_score': 0,
                'recent_articles_48h': 0,
                'older_articles': 0
            },
            'top_contributors': []
        }
    
    current_time = datetime.now(timezone.utc)
    
    weighted_score = 0
    deescalation_count = 0
    recent_articles = 0
    older_articles = 0
    
    article_details = []
    
    # ========================================
    # ARTICLE SCORING LOOP
    # ========================================
    for article in articles:
        title = article.get('title', '')
        description = article.get('description', '')
        content = article.get('content', '')
        full_text = f"{title} {description} {content}"
        
        source_name = article.get('source', {}).get('name', 'Unknown')
        published_date = article.get('publishedAt', '')
        
        # Calculate article weight components
        time_decay = calculate_time_decay(published_date, current_time)
        source_weight = get_source_weight(source_name)
        severity_multiplier = detect_keyword_severity(full_text)
        is_deescalation = detect_deescalation(full_text)
        
        # NEW: Apply leadership multiplier if present
        leadership_data = article.get('leadership', {})
        if leadership_data.get('has_leadership', False):
            leadership_multiplier = leadership_data.get('weight_multiplier', 1.0)
            severity_multiplier *= leadership_multiplier
        
        # Calculate article contribution
        if is_deescalation:
            article_contribution = -3 * time_decay * source_weight
            deescalation_count += 1
        else:
            article_contribution = time_decay * source_weight * severity_multiplier
        
        weighted_score += article_contribution
        
        # Track recency
        try:
            pub_dt = datetime.fromisoformat(published_date.replace('Z', '+00:00'))
            age_hours = (current_time - pub_dt).total_seconds() / 3600
            
            if age_hours <= 48:
                recent_articles += 1
            else:
                older_articles += 1
        except:
            older_articles += 1
        
        # Store article details for top contributors
        article_details.append({
            'source': source_name,
            'source_weight': source_weight,
            'time_decay': round(time_decay, 3),
            'severity': severity_multiplier,
            'deescalation': is_deescalation,
            'contribution': round(article_contribution, 2),
            'title': title[:100]  # Truncate for display
        })
    
    # ========================================
    # MOMENTUM CALCULATION
    # ========================================
    if recent_articles > 0 and older_articles > 0:
        recent_density = recent_articles / 2.0  # Articles per day in last 48h
        older_density = older_articles / max((days_analyzed - 2), 1)  # Articles per day before that
        
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
    
    # ========================================
    # FINAL PROBABILITY CALCULATION
    # ========================================
    base_score = 25
    baseline_adjustment = TARGET_BASELINES.get(target, {}).get('base_adjustment', 0)
    
    # Apply weighted score with dampening factor
    if weighted_score < 0:
        # De-escalation scenario
        probability = max(10, base_score + baseline_adjustment + weighted_score)
    else:
        # Escalation scenario (0.8 dampening factor)
        probability = base_score + baseline_adjustment + (weighted_score * 0.8)
    
    # Clamp to valid range
    probability = int(probability)
    probability = max(10, min(probability, 95))
    
    # ========================================
    # BUILD RESPONSE
    # ========================================
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
        'top_contributors': sorted(
            article_details, 
            key=lambda x: abs(x['contribution']), 
            reverse=True
        )[:15]  # Top 15 contributing articles
    }

# ========================================
# US STRIKE PROBABILITY (SPECIALIZED)
# ========================================
def calculate_us_strike_probability(articles, days_analyzed=7, target='iran'):
    """
    Calculate US strike probability with US-specific indicators
    Includes Trump statements, DoD announcements, deployment signals
    """
    
    # US-specific escalation keywords (in addition to base keywords)
    US_ESCALATION_KEYWORDS = {
        # Trump & Administration
        'trump': {'weight': 1.2, 'phrases': [
            'will not hesitate', 'all options', 'maximum pressure', 'fire and fury',
            'locked and loaded', 'military response', 'devastating consequences'
        ]},
        'dod': {'weight': 1.5, 'phrases': [
            'reviewing all options', 'prepared to defend', 'directed to deploy',
            'forces on heightened alert', 'strike options', 'kinetic action',
            'military posture', 'combat operations'
        ]},
        'deployments': {'weight': 2.0, 'phrases': [
            'carrier strike group', 'bomber deployment', 'additional forces',
            'troop surge', 'asset redeployment', 'forward deployment',
            'expeditionary strike', 'amphibious ready group'
        ]},
        # Reverse signals (de-escalation)
        'withdrawal': {'weight': -1.5, 'phrases': [
            'withdrawing forces', 'troop drawdown', 'reducing presence',
            'departing region', 'leaving mediterranean', 'pullback'
        ]}
    }
    
    # Calculate base probability using existing algorithm
    base_result = calculate_threat_probability(articles, days_analyzed, target)
    base_prob = base_result['probability'] / 100.0
    
    # Add US-specific adjustments
    us_bonus = 0
    us_indicators = []
    
    for article in articles:
        title = article.get('title', '').lower()
        content = f"{title} {article.get('description', '')} {article.get('content', '')}".lower()
        
        # Check US-specific keywords
        for category, data in US_ESCALATION_KEYWORDS.items():
            for phrase in data['phrases']:
                if phrase in content:
                    weight = data['weight']
                    us_bonus += weight
                    us_indicators.append({
                        'category': category,
                        'phrase': phrase,
                        'weight': weight,
                        'article': title[:80],
                        'article_url': article.get('url', '')  # ← ADD THIS!
                    })
    
    # Normalize US bonus (cap at ±20%)
    us_adjustment = min(max(us_bonus / 10, -0.20), 0.20)
    
    # Calculate final US probability
    us_prob = min(max(base_prob + us_adjustment, 0.05), 0.95)
    
    return {
        'probability': us_prob,
        'base_probability': base_prob,
        'us_adjustment': us_adjustment,
        'us_indicators': us_indicators[:5],  # Top 5
        'source': 'us_specific_algorithm'
    }

# ========================================
# COORDINATION DETECTION
# ========================================
def detect_coordination_signals(israel_prob, us_prob, all_articles):
    """
    Detect coordination signals between US and Israel
    Returns coordination level and specific indicators
    """
    
    coordination_keywords = {
        'joint_statements': [
            'joint statement', 'coordinated response', 'in coordination with',
            'together with allies', 'unified approach', 'combined action'
        ],
        'military_coordination': [
            'joint exercise', 'combined operations', 'coordinated strike',
            'allied forces', 'joint training', 'integrated operations'
        ],
        'diplomatic_coordination': [
            'biden netanyahu', 'us israel coordination', 'allied planning',
            'strategic coordination', 'defense consultation'
        ],
        'simultaneous_deployments': [
            'both israel and us', 'us and israeli forces', 'parallel deployment',
            'concurrent mobilization'
        ]
    }
    
    signals = 0
    detected_indicators = []
    
    # Signal 1: Both probabilities elevated
    if israel_prob > 0.50 and us_prob > 0.30:
        signals += 1
        detected_indicators.append('Both actors showing elevated strike intent')
    
    # Signal 2-5: Check articles for coordination keywords
    for article in all_articles:
        content = f"{article.get('title', '')} {article.get('description', '')} {article.get('content', '')}".lower()
        
        for category, phrases in coordination_keywords.items():
            for phrase in phrases:
                if phrase in content:
                    signals += 1
                    detected_indicators.append(f"{category}: '{phrase}' detected")
                    break  # One signal per category per article
    
    # Determine coordination level
    if signals >= 4:
        level = 'strong'
        factor = 0.15
    elif signals >= 2:
        level = 'moderate'
        factor = 0.10
    elif signals >= 1:
        level = 'weak'
        factor = 0.05
    else:
        level = 'none'
        factor = 0.00
    
    return {
        'level': level,
        'factor': factor,
        'signals_detected': signals,
        'indicators': detected_indicators[:3]  # Top 3
    }

# ========================================
# COMBINED PROBABILITY
# ========================================
def calculate_combined_probability(israel_prob, us_prob, coordination):
    """
    Calculate combined probability using independent events + coordination
    
    Formula:
    1. Base: P(at least one) = 1 - (1-P1)(1-P2)
    2. Apply coordination bonus
    3. Cap at 95%
    """
    
    # Step 1: Independent events probability
    base_combined = 1 - (1 - israel_prob) * (1 - us_prob)
    
    # Step 2: Apply coordination factor
    coord_factor = coordination.get('factor', 0)
    adjusted_combined = base_combined * (1 + coord_factor)
    
    # Step 3: Cap at 95%
    final_combined = min(adjusted_combined, 0.95)
    
    # Calculate bonus percentage for display
    coord_bonus = (final_combined - base_combined) * 100
    
    return {
        'combined': final_combined,
        'base_independent': base_combined,
        'coordination_bonus': coord_bonus,
        'coordination_level': coordination.get('level', 'none'),
        'formula': 'independent_events_with_coordination'
    }

# ========================================
# REVERSE THREAT CALCULATION
# ========================================
def build_recent_headlines(israel_contributors, us_indicators, reverse_israel_indicators, reverse_us_indicators, all_articles):
    """
    Build unified recent headlines list for threat matrix display
    Combines top articles from all threat calculations with weights
    
    Returns list of articles with:
    - title, url, source, date
    - threat_type (Israel Strike / US Strike / Reverse Threat)
    - weight (contribution score)
    - why_included (reason for inclusion)
    """
    
    headlines = []
    seen_urls = set()
    
    # ========================================
    # 1. ISRAEL STRIKE CONTRIBUTORS
    # ========================================
    for contributor in israel_contributors[:5]:  # Top 5
        # Find matching article
        matching_article = None
        for article in all_articles:
            if article.get('source', {}).get('name', '') == contributor.get('source', ''):
                if article.get('url') not in seen_urls:
                    matching_article = article
                    break
        
        if matching_article:
            seen_urls.add(matching_article.get('url', ''))
            
            headlines.append({
                'title': matching_article.get('title', 'No title')[:120],
                'url': matching_article.get('url', ''),
                'source': contributor.get('source', 'Unknown'),
                'published': matching_article.get('publishedAt', ''),
                'threat_type': 'Israel Strike',
                'weight': abs(contributor.get('contribution', 0)),
                'severity': contributor.get('severity', 1.0),
                'why_included': f"High severity ({contributor.get('severity', 1.0)}x multiplier)",
                'color': 'red'
            })
    
    # ========================================
    # ========================================
    # 2. US STRIKE INDICATORS
    # ========================================
    for indicator in us_indicators[:5]:  # Top 5
        indicator_url = indicator.get('article_url', '')
    
        # Find matching article by URL (more reliable than title)
        matching_article = None
        for article in all_articles:
            if article.get('url') == indicator_url:
                if article.get('url') not in seen_urls:
                    matching_article = article
                    break
        
        if matching_article:
            seen_urls.add(matching_article.get('url', ''))
            
            headlines.append({
                'title': matching_article.get('title', 'No title')[:120],
                'url': matching_article.get('url', ''),
                'source': matching_article.get('source', {}).get('name', 'Unknown'),
                'published': matching_article.get('publishedAt', ''),
                'threat_type': 'US Strike',
                'weight': abs(indicator.get('weight', 0)),
                'phrase': indicator.get('phrase', ''),
                'why_included': f"US-specific signal: '{indicator.get('phrase', '')}'",
                'color': 'blue'
            })
    
    # ========================================
    # 3. REVERSE THREAT INDICATORS (Iran → Israel/US)
    # ========================================
    for indicator in (reverse_israel_indicators + reverse_us_indicators)[:5]:  # Top 5 combined
        indicator_url = indicator.get('article_url', '')
    
        # Find matching article by URL (more reliable than title)
        matching_article = None
        for article in all_articles:
            if article.get('url') == indicator_url:
               if article.get('url') not in seen_urls:
                    matching_article = article
                    break
        
        if matching_article:
            seen_urls.add(matching_article.get('url', ''))
            
            headlines.append({
                'title': matching_article.get('title', 'No title')[:120],
                'url': matching_article.get('url', ''),
                'source': matching_article.get('source', {}).get('name', 'Unknown'),
                'published': matching_article.get('publishedAt', ''),
                'threat_type': 'Reverse Threat',
                'weight': abs(indicator.get('weight', 0)),
                'phrase': indicator.get('phrase', ''),
                'why_included': f"Threat phrase: '{indicator.get('phrase', '')}'",
                'color': 'orange'
            })
    
    # ========================================
    # SORT BY WEIGHT (highest first)
    # ========================================
    headlines.sort(key=lambda x: x['weight'], reverse=True)
    
    # Return top 15 headlines
    return headlines[:15]
    
def calculate_reverse_threat(articles, source_actor='iran', target_actor='israel', israel_prob=0, us_prob=0):
    """
    Calculate probability of source actor attacking target
    (e.g., Iran -> Israel, Hezbollah -> Israel)
    
    Enhanced with:
    - 40+ keywords across 6 categories
    - Weighted scoring by threat type
    - Proxy warfare detection
    - Asymmetric threat detection
    - Retaliation trigger bonus (when Israel/US prob high)
    """
    
    # ========================================
    # EXPANDED REVERSE THREAT KEYWORDS (40+)
    # ========================================
    REVERSE_THREAT_KEYWORDS = {
        # Direct threats from state actors
        'iran_direct_threats': [
            'iran threatens', 'iran warns', 'iran vows', 'tehran threatens',
            'irgc warns', 'irgc threatens', 'khamenei threatens', 'khamenei warns',
            'revolutionary guard threatens', 'quds force threatens', 'tehran vows',
            'iran pledges retaliation', 'iran promises revenge', 'iran will strike',
            'supreme leader warns', 'ayatollah threatens'
        ],
        
        # Retaliation language (key for reverse threats)
        'retaliation_threats': [
            'retaliation', 'retaliate', 'revenge attack', 'avenge', 'payback',
            'severe response', 'crushing response', 'devastating response',
            'will respond', 'will strike back', 'will pay the price',
            'consequences', 'severe consequences', 'pay dearly', 'answer for',
            'punishment', 'punish', 'will not go unanswered'
        ],
        
        # Military posturing & signals
        'military_signals': [
            'missile test', 'ballistic missile', 'cruise missile launch',
            'military drill', 'military exercise', 'naval drill', 'war games',
            'weapons test', 'combat readiness', 'forces on alert', 'high alert',
            'irgc exercise', 'naval exercise', 'air defense drill',
            'mobilization', 'troop deployment', 'forces deployed'
        ],
        
        # Proxy mobilization (Hezbollah, Houthis, Iraqi militias)
        'proxy_threats': [
            'hezbollah warns', 'hezbollah threatens', 'nasrallah warns', 'nasrallah threatens',
            'houthi attack', 'houthi strike', 'houthi threatens', 'ansarallah warns',
            'militia deployment', 'proxy forces', 'armed groups mobilize',
            'shiite militias', 'hashd forces', 'iraqi militia', 'pmu forces',
            'resistance axis', 'axis of resistance', 'rocket fire', 'cross-border attack'
        ],
        
        # Asymmetric warfare indicators
        'asymmetric_threats': [
            'strait of hormuz', 'close strait', 'block shipping', 'shipping lanes',
            'cyber attack', 'cyberattack', 'hack', 'infrastructure attack',
            'oil facilities', 'tanker attack', 'shipping disruption', 'pipeline',
            'refinery attack', 'energy infrastructure', 'sabotage'
        ],
        
        # Specific target mentions (highest weight when combined)
        'target_specific': [
            'target israel', 'strike israel', 'attack israel', 'hit israel',
            'target us', 'strike us', 'attack us', 'american bases',
            'us interests', 'israeli targets', 'tel aviv', 'haifa',
            'us forces', 'american troops', 'zionist regime', 'occupation forces'
        ]
    }
    
    # ========================================
    # SCORE CALCULATION WITH WEIGHTED CATEGORIES
    # ========================================
    threat_score = 0
    indicators = []
    proxy_detected = False
    asymmetric_detected = False
    target_mentioned_count = 0
    
    for article in articles:
        content = f"{article.get('title', '')} {article.get('description', '')} {article.get('content', '')}".lower()
        
        # Track if target is mentioned in this article
        target_in_content = target_actor.lower() in content
        
        # Check ALL keyword categories
        for category, phrases in REVERSE_THREAT_KEYWORDS.items():
            category_matched = False
            
            for phrase in phrases:
                if phrase in content:
                    # ========================================
                    # CATEGORY-BASED WEIGHTING
                    # ========================================
                    if 'direct_threats' in category:
                        weight = 3  # Highest weight for state threats
                    elif 'target_specific' in category and target_in_content:
                        weight = 4  # HIGHEST when target explicitly mentioned
                        target_mentioned_count += 1
                    elif 'retaliation' in category:
                        weight = 2.5  # High weight for retaliation language
                    elif 'military_signals' in category:
                        weight = 2  # Medium-high for military posturing
                    elif 'proxy' in category:
                        weight = 2.5  # High weight for proxy threats
                        proxy_detected = True
                    elif 'asymmetric' in category:
                        weight = 2  # Medium-high for asymmetric warfare
                        asymmetric_detected = True
                    else:
                        weight = 1  # Base weight
                    
                    # BONUS: If target mentioned in article, increase weight
                    if target_in_content and 'target_specific' not in category:
                        weight *= 1.3
                    
                    threat_score += weight
                    
                    indicators.append({
                        'type': category.replace('_', ' ').title(),
                        'phrase': phrase,
                        'weight': weight,
                        'article': article.get('title', '')[:80],
                        'article_url': article.get('url', ''),  # ← ADD THIS!
                        'target_mentioned': target_in_content
                    })
                    
                    category_matched = True
                    break  # One match per category per article
            
            if category_matched:
                continue  # Move to next category
    
    # ========================================
    # CALCULATE BASE PROBABILITY
    # ========================================
    # Adjusted formula: More lenient scoring (divide by 40 instead of 50)
    base_probability = min(threat_score / 40.0, 0.60)
    
    # ========================================
    # RETALIATION TRIGGER CALCULATION (INLINE)
    # ========================================
    retaliation_bonus = 0
    
    if israel_prob > 0.60:
        retaliation_bonus += 0.15
    elif israel_prob > 0.40:
        retaliation_bonus += 0.08
    elif israel_prob > 0.20:
        retaliation_bonus += 0.03
    
    if us_prob > 0.60:
        retaliation_bonus += 0.15
    elif us_prob > 0.40:
        retaliation_bonus += 0.08
    elif us_prob > 0.20:
        retaliation_bonus += 0.03
    
    retaliation_bonus = min(retaliation_bonus, 0.25)  # Cap at +25%
    
    # ========================================
    # FINAL PROBABILITY
    # ========================================
    final_probability = min(base_probability + retaliation_bonus, 0.65)  # Cap at 65%
    
    # ========================================
    # BUILD RESPONSE
    # ========================================
    return {
        'probability': final_probability,
        'source': source_actor,
        'target': target_actor,
        'threat_score': round(threat_score, 1),
        'base_probability': round(base_probability, 3),
        'retaliation_bonus': round(retaliation_bonus, 3),
        'indicators': sorted(indicators, key=lambda x: x['weight'], reverse=True)[:5],  # Top 5 weighted
        'total_indicators': len(indicators),
        'target_explicitly_mentioned': target_mentioned_count,
        'proxy_warfare_detected': proxy_detected,
        'asymmetric_warfare_detected': asymmetric_detected,
        'risk_level': 'high' if final_probability > 0.40 else 'moderate' if final_probability > 0.20 else 'low',
        'calculation_method': 'weighted_keywords_with_retaliation_trigger'
    }

# ========================================
# JORDAN-SPECIFIC THREAT CALCULATIONS
# ========================================

JORDAN_INCOMING_THREAT_KEYWORDS = {
    'iran_militia_strike': {
        'weight': 3.5,
        'phrases': [
            'iran strike jordan', 'iranian drones jordan', 'jordan intercept iranian',
            'militia attack jordan', 'pmu jordan', 'hashd jordan',
            'iran threatens jordan', 'irgc jordan', 'proxy attack jordan',
            'iraqi militia jordan', 'shiite militia jordan border',
            'jordan air defense iranian', 'drone incursion jordan'
        ]
    },
    'syrian_border_isis': {
        'weight': 3.0,
        'phrases': [
            'isis jordan', 'daesh jordan', 'islamic state jordan',
            'jordan syria border incursion', 'jordan border attack',
            'jordan border security', 'smuggling jordan syria',
            'captagon jordan', 'drug smuggling jordan', 'rukban camp',
            'border infiltration jordan', 'jordan border clashes',
            'isis sleeper cell jordan', 'isis threat jordan'
        ]
    },
    'palestinian_unrest': {
        'weight': 2.5,
        'phrases': [
            'jordan palestinian uprising', 'amman protests palestine',
            'jordan riots', 'east bank west bank tensions',
            'refugee camp unrest jordan', 'palestinian protests amman',
            'jordan destabilization', 'hashemite stability',
            'jordan internal unrest', 'tribal tensions jordan',
            'jordan protests', 'bread riots jordan', 'amman riots'
        ]
    },
    'us_base_targeting': {
        'weight': 4.0,
        'phrases': [
            'tower 22', 'tower 22 jordan', 'jordan us base attack',
            'tanf jordan', 'us base jordan drone', 'american troops jordan',
            'us forces jordan attacked', 'jordan base drone strike',
            'centcom jordan', 'muwaffaq salti', 'jordan military base attack'
        ]
    }
}

JORDAN_DEFENSIVE_KEYWORDS = {
    'coalition_air_defense': {
        'weight': 2.0,
        'phrases': [
            'jordan intercept', 'jordan shoots down', 'jordan air defense',
            'jordan intercepts drone', 'jordan intercepts missile',
            'jordan airspace defense', 'jordan coalition defense',
            'jordan shoots down iranian', 'jordan air force intercept',
            'us jordan air defense', 'jordan patriot', 'jordan radar',
            'joint air defense jordan', 'jordan allied intercept'
        ]
    },
    'border_operations': {
        'weight': 1.5,
        'phrases': [
            'jordan border operation', 'jordan counter smuggling',
            'jordan anti drug operation', 'jordan border patrol',
            'jordan captagon seizure', 'jordan border interdiction',
            'jordan military border', 'jordan border security operation',
            'jordan customs seizure', 'jordan border crackdown'
        ]
    }
}


def calculate_jordan_incoming_threats(articles, days_analyzed=7):
    """
    Calculate probability of kinetic action AGAINST Jordan
    
    Threat vectors:
    1. Iranian/militia strike (demonstrated April 2024 precedent)
    2. Syrian border incursion / ISIS spillover
    3. Palestinian uprising / internal destabilization
    4. US base targeting by Iran-aligned groups
    
    Returns individual threat probabilities + combined score
    """
    
    threat_scores = {
        'iran_militia': {'score': 0, 'indicators': [], 'articles': 0},
        'syria_isis': {'score': 0, 'indicators': [], 'articles': 0},
        'palestinian_unrest': {'score': 0, 'indicators': [], 'articles': 0},
        'us_base': {'score': 0, 'indicators': [], 'articles': 0}
    }
    
    category_map = {
        'iran_militia_strike': 'iran_militia',
        'syrian_border_isis': 'syria_isis',
        'palestinian_unrest': 'palestinian_unrest',
        'us_base_targeting': 'us_base'
    }
    
    for article in articles:
        content = f"{article.get('title', '')} {article.get('description', '')} {article.get('content', '')}".lower()
        
        for category, data in JORDAN_INCOMING_THREAT_KEYWORDS.items():
            mapped = category_map.get(category)
            if not mapped:
                continue
            
            for phrase in data['phrases']:
                if phrase in content:
                    threat_scores[mapped]['score'] += data['weight']
                    threat_scores[mapped]['articles'] += 1
                    threat_scores[mapped]['indicators'].append({
                        'phrase': phrase,
                        'weight': data['weight'],
                        'article': article.get('title', '')[:80],
                        'article_url': article.get('url', '')
                    })
                    break  # One match per category per article
    
    # Convert scores to probabilities (cap each at 60%)
    results = {}
    for key, data in threat_scores.items():
        prob = min(data['score'] / 35.0, 0.60)
        
        results[key] = {
            'probability': prob,
            'risk_level': (
                'very_high' if prob > 0.50 else
                'high' if prob > 0.35 else
                'moderate' if prob > 0.20 else
                'low'
            ),
            'indicators': sorted(data['indicators'], key=lambda x: x['weight'], reverse=True)[:5],
            'total_indicators': len(data['indicators'])
        }
    
    # Combined incoming threat (independent events formula)
    probs = [results[k]['probability'] for k in results]
    combined = 1.0
    for p in probs:
        combined *= (1 - p)
    combined = 1 - combined
    combined = min(combined, 0.85)
    
    return {
        'iran_militia': results['iran_militia'],
        'syria_isis': results['syria_isis'],
        'palestinian_unrest': results['palestinian_unrest'],
        'us_base': results['us_base'],
        'combined': {
            'probability': combined,
            'risk_level': (
                'very_high' if combined > 0.60 else
                'high' if combined > 0.40 else
                'moderate' if combined > 0.20 else
                'low'
            )
        }
    }


def calculate_jordan_defensive_posture(articles, iran_israel_tension=0.0):
    """
    Calculate Jordan's defensive activation probability
    
    Two outgoing/defensive vectors:
    1. Coalition Air Defense — probability of active intercept ops
       (boosted when Iran-Israel tensions are elevated)
    2. Border Security Operations — counter-smuggling, counter-ISIS
    
    Returns probabilities for each defensive posture
    """
    
    defense_scores = {
        'coalition_air_defense': {'score': 0, 'indicators': []},
        'border_operations': {'score': 0, 'indicators': []}
    }
    
    for article in articles:
        content = f"{article.get('title', '')} {article.get('description', '')} {article.get('content', '')}".lower()
        
        for category, data in JORDAN_DEFENSIVE_KEYWORDS.items():
            for phrase in data['phrases']:
                if phrase in content:
                    defense_scores[category]['score'] += data['weight']
                    defense_scores[category]['indicators'].append({
                        'phrase': phrase,
                        'weight': data['weight'],
                        'article': article.get('title', '')[:80],
                        'article_url': article.get('url', '')
                    })
                    break
    
    # Coalition Air Defense gets a BOOST from Iran-Israel tension
    # If Iran-Israel probability is high, Jordan is more likely to activate air defense
    tension_boost = 0.0
    if iran_israel_tension > 0.60:
        tension_boost = 0.25
    elif iran_israel_tension > 0.40:
        tension_boost = 0.15
    elif iran_israel_tension > 0.20:
        tension_boost = 0.05
    
    air_defense_base = min(defense_scores['coalition_air_defense']['score'] / 20.0, 0.50)
    air_defense_prob = min(air_defense_base + tension_boost, 0.75)
    
    border_ops_prob = min(defense_scores['border_operations']['score'] / 20.0, 0.50)
    
    return {
        'coalition_air_defense': {
            'probability': air_defense_prob,
            'base_probability': air_defense_base,
            'iran_israel_tension_boost': tension_boost,
            'risk_level': (
                'very_high' if air_defense_prob > 0.50 else
                'high' if air_defense_prob > 0.35 else
                'moderate' if air_defense_prob > 0.20 else
                'low'
            ),
            'indicators': sorted(defense_scores['coalition_air_defense']['indicators'],
                               key=lambda x: x['weight'], reverse=True)[:5],
            'tooltip': 'Probability of active intercept operations against hostile aerial threats transiting Jordanian airspace'
        },
        'border_operations': {
            'probability': border_ops_prob,
            'risk_level': (
                'very_high' if border_ops_prob > 0.50 else
                'high' if border_ops_prob > 0.35 else
                'moderate' if border_ops_prob > 0.20 else
                'low'
            ),
            'indicators': sorted(defense_scores['border_operations']['indicators'],
                               key=lambda x: x['weight'], reverse=True)[:5],
            'tooltip': 'Probability of active border security operations against smuggling and ISIS infiltration'
        }
    }


def build_jordan_headlines(incoming_threats, defensive_posture, all_articles):
    """Build unified headlines list for Jordan threat display"""
    
    headlines = []
    seen_urls = set()
    
    # Collect all indicators from incoming threats
    for threat_key in ['iran_militia', 'syria_isis', 'palestinian_unrest', 'us_base']:
        threat = incoming_threats.get(threat_key, {})
        threat_labels = {
            'iran_militia': 'Iran/Militia Threat',
            'syria_isis': 'Syria Border/ISIS',
            'palestinian_unrest': 'Internal Unrest',
            'us_base': 'US Base Targeting'
        }
        
        for indicator in threat.get('indicators', [])[:3]:
            url = indicator.get('article_url', '')
            if url and url not in seen_urls:
                seen_urls.add(url)
                
                # Find matching article for full data
                matching = None
                for article in all_articles:
                    if article.get('url') == url:
                        matching = article
                        break
                
                headlines.append({
                    'title': matching.get('title', indicator.get('article', ''))[:120] if matching else indicator.get('article', '')[:120],
                    'url': url,
                    'source': matching.get('source', {}).get('name', 'Unknown') if matching else 'Unknown',
                    'published': matching.get('publishedAt', '') if matching else '',
                    'threat_type': threat_labels.get(threat_key, 'Unknown'),
                    'weight': indicator.get('weight', 0),
                    'phrase': indicator.get('phrase', ''),
                    'why_included': f"Matched: '{indicator.get('phrase', '')}'",
                    'color': 'red' if threat_key == 'us_base' else 'orange' if threat_key == 'iran_militia' else 'blue'
                })
    
    # Add defensive posture indicators
    for def_key in ['coalition_air_defense', 'border_operations']:
        defense = defensive_posture.get(def_key, {})
        def_labels = {
            'coalition_air_defense': 'Coalition Air Defense',
            'border_operations': 'Border Operations'
        }
        
        for indicator in defense.get('indicators', [])[:2]:
            url = indicator.get('article_url', '')
            if url and url not in seen_urls:
                seen_urls.add(url)
                
                matching = None
                for article in all_articles:
                    if article.get('url') == url:
                        matching = article
                        break
                
                headlines.append({
                    'title': matching.get('title', indicator.get('article', ''))[:120] if matching else indicator.get('article', '')[:120],
                    'url': url,
                    'source': matching.get('source', {}).get('name', 'Unknown') if matching else 'Unknown',
                    'published': matching.get('publishedAt', '') if matching else '',
                    'threat_type': def_labels.get(def_key, 'Defensive'),
                    'weight': indicator.get('weight', 0),
                    'phrase': indicator.get('phrase', ''),
                    'why_included': f"Defensive signal: '{indicator.get('phrase', '')}'",
                    'color': 'green'
                })
    
    headlines.sort(key=lambda x: x['weight'], reverse=True)
    return headlines[:15]
    
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
    """Fetch articles from HRANA RSS feed via RSS2JSON proxy"""
    
    articles = []
    feed_url = 'https://en-hrana.org/feed/'
    
    try:
        print(f"[HRANA] Fetching RSS via RSS2JSON proxy...")
        
        # RSS2JSON free API - no auth required, 10k requests/day
        rss2json_url = f'https://api.rss2json.com/v1/api.json?rss_url={feed_url}'
        
        response = requests.get(rss2json_url, timeout=20)
        
        print(f"[HRANA] RSS2JSON Response status: {response.status_code}")
        
        if response.status_code != 200:
            print(f"[HRANA] ❌ RSS2JSON failed with status {response.status_code}")
            return []
        
        data = response.json()
        
        if data.get('status') != 'ok':
            print(f"[HRANA] ❌ RSS2JSON returned status: {data.get('status')}")
            print(f"[HRANA] Message: {data.get('message', 'No message')}")
            return []
        
        items = data.get('items', [])
        print(f"[HRANA] Found {len(items)} items in feed")
        
        for item in items[:15]:
            title = item.get('title', '')
            link = item.get('link', '')
            pub_date = item.get('pubDate', '')
            description = item.get('description', '')
            content = item.get('content', '')
            
            if title and link:
                text_content = content if len(content) > len(description) else description
                
                articles.append({
                    'title': title,
                    'description': text_content[:500],
                    'url': link,
                    'publishedAt': pub_date if pub_date else datetime.now(timezone.utc).isoformat(),
                    'source': {'name': 'HRANA'},
                    'content': text_content[:500],
                    'language': 'en'
                })
        
        print(f"[HRANA] ✅ Successfully fetched {len(articles)} articles via RSS2JSON")
        return articles
        
    except requests.Timeout:
        print(f"[HRANA] ❌ Request timeout after 20s")
        return []
    except requests.ConnectionError as e:
        print(f"[HRANA] ❌ Connection error: {str(e)[:200]}")
        return []
    except Exception as e:
        print(f"[HRANA] ❌ Unexpected error: {str(e)[:200]}")
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
        print(f"[Syria Direct] Fetching RSS...")
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/rss+xml, application/xml, text/xml, */*'
        }
        
        response = requests.get(feed_url, headers=headers, timeout=20)
        
        if response.status_code != 200:
            print(f"[Syria Direct] HTTP {response.status_code}")
            return []
        
        try:
            root = ET.fromstring(response.content)
        except ET.ParseError as e:
            print(f"[Syria Direct] XML parse error: {e}")
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
        
        print(f"[Syria Direct] ✓ Fetched {len(articles)} articles")
        return articles
        
    except Exception as e:
        print(f"[Syria Direct] Error: {str(e)[:100]}")
        return []

def fetch_sohr_rss():
    """Fetch articles from SOHR RSS feed"""
    import xml.etree.ElementTree as ET
    
    articles = []
    feed_url = 'https://www.syriahr.com/en/feed/'
    
    try:
        print(f"[SOHR] Fetching RSS...")
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/rss+xml, application/xml, text/xml, */*'
        }
        
        response = requests.get(feed_url, headers=headers, timeout=20)
        
        if response.status_code != 200:
            print(f"[SOHR] HTTP {response.status_code}")
            return []
        
        try:
            root = ET.fromstring(response.content)
        except ET.ParseError as e:
            print(f"[SOHR] XML parse error: {e}")
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
        
        print(f"[SOHR] ✓ Fetched {len(articles)} articles")
        return articles
        
    except Exception as e:
        print(f"[SOHR] Error: {str(e)[:100]}")
        return []

# ========================================
# SYRIA CONFLICT DATA EXTRACTION
# ========================================
def extract_syria_conflict_data(articles):
    """Extract conflict statistics from Syria articles"""
    
    conflict_data = {
        'deaths': 0,
        'displaced': 0,
        'factional_clashes': 0,
        'clash_locations': {},
        'active_factions': set(),
        'sources': set(),
        'details': []
    }
    
    number_patterns = [
        r'(\d+(?:,\d{3})*)\s+(?:people\s+)?',
        r'(?:more than|over|at least)\s+(\d+(?:,\d{3})*)',
        r'(\d+(?:,\d{3})*)\s+(?:have been|were|are)',
        r'(?:roughly|approximately|around)\s+(\d+(?:,\d{3})*)',
        r'(hundreds?|thousands?|tens of thousands)',
    ]
    
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
                                conflict_data['sources'].add(source)
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
                                conflict_data['sources'].add(source)
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
    conflict_data['sources'] = list(conflict_data['sources'])
    conflict_data['num_factions'] = len(conflict_data['active_factions'])
    
    print(f"[Syria Conflict Data] Deaths: {conflict_data['deaths']}, Displaced: {conflict_data['displaced']}, Clashes: {conflict_data['factional_clashes']}")
    
    return conflict_data

# ========================================
# SYRIA DISPLACEMENT DATA SOURCES (NEW v2.8.1)
# ========================================

def fetch_unhcr_syria_data():
    """
    Fetch displacement data from UNHCR Syria data portal
    
    UNHCR provides:
    - Total IDPs (Internally Displaced Persons)
    - Refugee populations
    - Camp populations (including Al-Hol)
    - Recent displacement figures
    
    Returns structured displacement data
    """
    try:
        print("[UNHCR] Fetching Syria displacement data...")
        
        # UNHCR Syria data portal API
        # Note: This is a simplified example - actual UNHCR API may require different approach
        url = "https://data.unhcr.org/api/population/get/sublocation"
        
        params = {
            'widget_id': 'syria',
            'sv_id': '54',  # Syria country code
            'population_group': '5460',  # IDPs
            'format': 'json'
        }
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        response = requests.get(url, params=params, headers=headers, timeout=15)
        
        if response.status_code != 200:
            print(f"[UNHCR] HTTP error: {response.status_code}")
            return fetch_unhcr_fallback()
        
        data = response.json()
        
        # Parse UNHCR response
        # Structure varies, this is a template
        total_idps = 0
        camp_population = 0
        recent_displacement = 0
        
        if isinstance(data, dict):
            total_idps = data.get('total', 0)
            camp_population = data.get('camp_total', 0)
            recent_displacement = data.get('recent', 0)
        elif isinstance(data, list):
            for region in data:
                total_idps += region.get('individuals', 0)
        
        print(f"[UNHCR] ✅ IDPs: {total_idps:,}, Camps: {camp_population:,}")
        
        return {
            'total_idps': total_idps,
            'camp_population': camp_population,
            'recent_displacement': recent_displacement,
            'source': 'UNHCR',
            'last_updated': datetime.now(timezone.utc).isoformat()
        }
        
    except Exception as e:
        print(f"[UNHCR] Error: {str(e)[:200]}")
        return fetch_unhcr_fallback()


def fetch_unhcr_fallback():
    """
    Fallback: Use known estimates from recent UNHCR reports
    
    As of February 2025, UNHCR reported:
    - ~7.2 million IDPs in Syria
    - ~1.8 million in camps
    - ~500k recent displacements (post-Assad)
    
    Source: UNHCR Syria Flash Update (Jan 2025)
    """
    print("[UNHCR] Using fallback estimates from recent reports")
    
    return {
        'total_idps': 7200000,  # 7.2M
        'camp_population': 1800000,  # 1.8M
        'recent_displacement': 500000,  # 500k (post-Assad period)
        'source': 'UNHCR Estimate',
        'estimated': True,
        'last_updated': datetime.now(timezone.utc).isoformat(),
        'note': 'Based on UNHCR Flash Update January 2025'
    }


def scrape_acaps_syria():
    """
    Scrape ACAPS (Assessment Capacities Project) for Syria analysis
    
    ACAPS provides:
    - Humanitarian access constraints
    - Crisis severity ratings
    - Displacement trends
    - Camp conditions
    """
    try:
        print("[ACAPS] Scraping Syria analysis...")
        
        url = "https://www.acaps.org/countries/syria"
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        response = requests.get(url, headers=headers, timeout=15)
        
        if response.status_code != 200:
            print(f"[ACAPS] HTTP error: {response.status_code}")
            return None
        
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Look for key statistics
        # ACAPS structure may vary - this is a template
        stats = {}
        
        # Try to find crisis severity rating
        severity_elem = soup.find('div', class_=lambda x: x and 'severity' in x.lower())
        if severity_elem:
            stats['crisis_severity'] = severity_elem.get_text(strip=True)
        
        # Look for humanitarian access percentage
        access_elem = soup.find(string=re.compile(r'\d+%.*access', re.IGNORECASE))
        if access_elem:
            match = re.search(r'(\d+)%', access_elem)
            if match:
                stats['humanitarian_access_pct'] = int(match.group(1))
        
        # Look for affected population figures
        affected_elem = soup.find(string=re.compile(r'affected', re.IGNORECASE))
        if affected_elem:
            # Try to extract numbers
            numbers = re.findall(r'(\d+(?:\.\d+)?)\s*(?:million|M)', affected_elem, re.IGNORECASE)
            if numbers:
                stats['affected_population'] = float(numbers[0]) * 1000000
        
        print(f"[ACAPS] ✅ Extracted {len(stats)} statistics")
        
        return {
            'statistics': stats,
            'source': 'ACAPS',
            'url': url,
            'last_updated': datetime.now(timezone.utc).isoformat()
        }
        
    except Exception as e:
        print(f"[ACAPS] Error: {str(e)[:200]}")
        return None


def fetch_alhol_camp_data():
    """
    Fetch specific data about Al-Hol camp
    
    Al-Hol is the largest camp in northeast Syria
    Housing ~56,000 people (mostly ISIS families)
    
    Sources: AP News, UNHCR, local reports
    """
    try:
        print("[Al-Hol] Searching for camp updates...")
        
        # Search Google News for Al-Hol camp
        query = "Al-Hol camp Syria"
        url = f"https://news.google.com/rss/search?q={query.replace(' ', '+')}&hl=en&gl=US&ceid=US:en"
        
        response = requests.get(url, timeout=10, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        
        articles = []
        
        if response.status_code == 200:
            import xml.etree.ElementTree as ET
            
            root = ET.fromstring(response.content)
            items = root.findall('.//item')
            
            for item in items[:5]:  # Top 5 recent articles
                title_elem = item.find('title')
                link_elem = item.find('link')
                pubDate_elem = item.find('pubDate')
                
                if title_elem is not None:
                    articles.append({
                        'title': title_elem.text or '',
                        'url': link_elem.text if link_elem is not None else '',
                        'published': pubDate_elem.text if pubDate_elem is not None else ''
                    })
        
        # Known baseline from UNHCR reports
        camp_population = 56000  # As of Jan 2025
        
        # Look for population changes in recent articles
        for article in articles:
            text = article['title'].lower()
            # Try to extract numbers
            numbers = re.findall(r'(\d+,?\d*)\s*(?:people|residents)', text)
            if numbers:
                try:
                    num = int(numbers[0].replace(',', ''))
                    if 30000 < num < 100000:  # Sanity check
                        camp_population = num
                        break
                except:
                    pass
        
        print(f"[Al-Hol] ✅ Population: ~{camp_population:,}")
        
        return {
            'camp_name': 'Al-Hol',
            'population': camp_population,
            'location': 'Hasakah Governorate, Northeast Syria',
            'managed_by': 'Kurdish-led SDF',
            'conditions': 'Overcrowded, security concerns',
            'recent_articles': articles,
            'source': 'News aggregation + UNHCR baseline',
            'last_updated': datetime.now(timezone.utc).isoformat()
        }
        
    except Exception as e:
        print(f"[Al-Hol] Error: {str(e)[:200]}")
        return {
            'camp_name': 'Al-Hol',
            'population': 56000,  # Fallback estimate
            'location': 'Hasakah Governorate, Northeast Syria',
            'source': 'Estimate',
            'estimated': True,
            'last_updated': datetime.now(timezone.utc).isoformat()
        }


def extract_displacement_from_articles(articles):
    """
    Extract displacement statistics from Syria Direct and SOHR articles
    
    Looks for mentions of:
    - IDP movements
    - Camp evacuations
    - Displacement waves
    - Return movements
    """
    displacement_data = {
        'recent_movements': [],
        'total_extracted': 0,
        'sources': set(),
        'details': []
    }
    
    # Displacement keywords
    displacement_keywords = [
        'displaced', 'fled', 'evacuated', 'forced to leave',
        'abandoned homes', 'refugees', 'idp', 'internally displaced'
    ]
    
    # Number extraction patterns
    number_patterns = [
        r'(\d+(?:,\d{3})*)\s+(?:people|civilians|residents|families)',
        r'(?:more than|over|at least)\s+(\d+(?:,\d{3})*)',
        r'(\d+(?:,\d{3})*)\s+(?:have been|were|are)\s+displaced'
    ]
    
    for article in articles:
        title = (article.get('title') or '').lower()
        description = (article.get('description') or '').lower()
        content = (article.get('content') or '').lower()
        text = f"{title} {description} {content}"
        
        source = article.get('source', {}).get('name', 'Unknown')
        url = article.get('url', '')
        
        # Check if article mentions displacement
        if not any(keyword in text for keyword in displacement_keywords):
            continue
        
        # Try to extract numbers
        for pattern in number_patterns:
            matches = re.finditer(pattern, text, re.IGNORECASE)
            for match in matches:
                num_str = match.group(1).replace(',', '')
                try:
                    num = int(num_str)
                    
                    if num > 100:  # Minimum threshold
                        displacement_data['total_extracted'] += num
                        displacement_data['sources'].add(source)
                        displacement_data['details'].append({
                            'count': num,
                            'source': source,
                            'url': url,
                            'context': match.group(0)
                        })
                        
                        print(f"[Displacement Extract] {num:,} from {source}")
                except:
                    pass
    
    displacement_data['sources'] = list(displacement_data['sources'])
    
    return displacement_data


def enhance_syria_conflict_data_with_displacement(conflict_data, unhcr_data, alhol_data, article_extracts):
    """
    Combine all displacement sources into comprehensive report
    
    Priority:
    1. UNHCR official data (most authoritative)
    2. Article extractions (recent movements)
    3. Al-Hol camp specifics
    """
    
    enhanced = {
        'total_idps': unhcr_data.get('total_idps', conflict_data.get('displaced', 0)),
        'camp_population': unhcr_data.get('camp_population', 0),
        'recent_displacement': unhcr_data.get('recent_displacement', article_extracts.get('total_extracted', 0)),
        
        # Breakdown
        'idp_breakdown': {
            'in_camps': unhcr_data.get('camp_population', 0),
            'with_host_communities': unhcr_data.get('total_idps', 0) - unhcr_data.get('camp_population', 0),
            'recent_movements': article_extracts.get('total_extracted', 0)
        },
        
        # Specific camps
        'major_camps': [
            {
                'name': alhol_data.get('camp_name', 'Al-Hol'),
                'population': alhol_data.get('population', 56000),
                'location': alhol_data.get('location', 'Northeast Syria')
            }
            # Add more camps as needed
        ],
        
        # Sources
        'sources': {
            'unhcr': unhcr_data.get('source', 'Unknown'),
            'alhol': alhol_data.get('source', 'Unknown'),
            'article_sources': article_extracts.get('sources', [])
        },
        
        # Links
        'humanitarian_links': [
            {
                'name': 'UNHCR Syria Portal',
                'url': 'https://data.unhcr.org/en/country/syr',
                'description': 'Official displacement data'
            },
            {
                'name': 'ACAPS Syria',
                'url': 'https://www.acaps.org/countries/syria',
                'description': 'Crisis analysis'
            },
            {
                'name': 'AP News - Al-Hol',
                'url': 'https://apnews.com/hub/syria',
                'description': 'Camp reporting'
            },
            {
                'name': 'ReliefWeb',
                'url': 'https://reliefweb.int/country/syr',
                'description': 'UN coordination'
            }
        ],
        
        'last_updated': datetime.now(timezone.utc).isoformat()
    }
    
    return enhanced

# ========================================
# OIL & GOLD PRICE FETCHING - CASCADING FALLBACK SYSTEM
# ========================================
def fetch_oil_eia():
    """
    Try US EIA (Energy Information Administration) - FREE, NO KEY REQUIRED!
    Most reliable government source
    """
    try:
        # EIA provides free Brent crude data via their open data API
        url = "https://api.eia.gov/v2/petroleum/pri/spt/data/?frequency=daily&data[0]=value&facets[series][]=RBRTE&sort[0][column]=period&sort[0][direction]=desc&offset=0&length=1"
        
        response = requests.get(url, timeout=10)
        
        if response.status_code != 200:
            print(f"[Oil Price] EIA HTTP error: {response.status_code}")
            return None
        
        data = response.json()
        
        # Navigate the EIA response structure
        if 'response' in data and 'data' in data['response']:
            records = data['response']['data']
            
            if records and len(records) > 0:
                latest = records[0]
                price = float(latest.get('value', 0))
                
                if price > 0:
                    print(f"[Oil Price] ✅ EIA: Brent ${price:.2f}")
                    
                    return {
                        'price': round(price, 2),
                        'change': 0,
                        'change_percent': 0,
                        'currency': 'USD',
                        'commodity': 'Brent Crude',
                        'source': 'US EIA'
                    }
        
        print(f"[Oil Price] EIA: No valid data in response")
        return None
        
    except Exception as e:
        print(f"[Oil Price] EIA failed: {str(e)[:100]}")
        return None


def fetch_oil_demo_api():
    """
    Try OilPriceAPI demo endpoint - FREE, NO AUTH, 20 req/hour
    """
    try:
        url = "https://api.oilpriceapi.com/v1/demo/prices"
        
        response = requests.get(url, timeout=10, headers={
            'Content-Type': 'application/json'
        })
        
        if response.status_code != 200:
            print(f"[Oil Price] OilPriceAPI Demo HTTP error: {response.status_code}")
            return None
        
        data = response.json()
        
        if data.get('status') == 'success' and 'data' in data:
            price_data = data['data']
            
            # Demo endpoint gives WTI, we need to approximate Brent (usually ~$5 higher)
            if 'price' in price_data:
                wti_price = float(price_data['price'])
                brent_approx = wti_price + 5  # Typical Brent premium over WTI
                
                print(f"[Oil Price] ✅ OilPriceAPI Demo: WTI ${wti_price:.2f} → Brent ~${brent_approx:.2f}")
                
                return {
                    'price': round(brent_approx, 2),
                    'change': 0,
                    'change_percent': 0,
                    'currency': 'USD',
                    'commodity': 'Brent Crude (approx)',
                    'source': 'OilPriceAPI Demo'
                }
        
        return None
        
    except Exception as e:
        print(f"[Oil Price] OilPriceAPI Demo failed: {str(e)[:100]}")
        return None


def fetch_oil_alpha_vantage():
    """Try Alpha Vantage API (750 requests/month = 25/day)"""
    try:
        url = f"https://www.alphavantage.co/query?function=BRENT&interval=daily&apikey={ALPHA_VANTAGE_KEY}"
        
        response = requests.get(url, timeout=10)
        
        if response.status_code != 200:
            print(f"[Oil Price] Alpha Vantage HTTP error: {response.status_code}")
            return None
        
        data = response.json()
        
        # Check for rate limit messages
        if 'Note' in data:
            print(f"[Oil Price] Alpha Vantage rate limit: {data['Note']}")
            return None
        
        if 'Information' in data:
            print(f"[Oil Price] Alpha Vantage info: {data['Information']}")
            return None
        
        if 'Error Message' in data:
            print(f"[Oil Price] Alpha Vantage error: {data['Error Message']}")
            return None
        
        # Extract data array
        time_series = data.get('data')
        
        # Validate we got actual price data
        if not time_series:
            print(f"[Oil Price] No 'data' field. Keys: {list(data.keys())}")
            return None
        
        if not isinstance(time_series, list) or len(time_series) == 0:
            print(f"[Oil Price] Empty data array")
            return None
        
        # Get latest price
        latest = time_series[0]
        
        if 'value' not in latest:
            print(f"[Oil Price] No 'value' in latest entry. Keys: {list(latest.keys())}")
            return None
        
        price = float(latest['value'])
        
        if price <= 0:
            print(f"[Oil Price] Invalid price: {price}")
            return None
        
        print(f"[Oil Price] ✅ Alpha Vantage: Brent ${price:.2f}")
        
        return {
            'price': round(price, 2),
            'change': 0,
            'change_percent': 0,
            'currency': 'USD',
            'commodity': 'Brent Crude',
            'source': 'Alpha Vantage'
        }
        
    except KeyError as e:
        print(f"[Oil Price] Alpha Vantage missing key: {e}")
        return None
    except ValueError as e:
        print(f"[Oil Price] Alpha Vantage value error: {e}")
        return None
    except Exception as e:
        print(f"[Oil Price] Alpha Vantage failed: {str(e)[:100]}")
        return None


def fetch_oil_fred():
    """
    Try FRED (Federal Reserve Economic Data) - FREE, NO KEY REQUIRED!
    Reliable government source, updates daily
    """
    try:
        # FRED doesn't have a public API without key, but they have CSV download
        # This is a fallback option
        url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DCOILBRENTEU"
        
        response = requests.get(url, timeout=10)
        
        if response.status_code != 200:
            print(f"[Oil Price] FRED HTTP error: {response.status_code}")
            return None
        
        # Parse CSV (last line has latest price)
        lines = response.text.strip().split('\n')
        if len(lines) < 2:
            return None
        
        last_line = lines[-1]
        parts = last_line.split(',')
        
        if len(parts) >= 2:
            price_str = parts[1].strip()
            if price_str and price_str != '.':
                price = float(price_str)
                
                if price > 0:
                    print(f"[Oil Price] ✅ FRED: Brent ${price:.2f}")
                    
                    return {
                        'price': round(price, 2),
                        'change': 0,
                        'change_percent': 0,
                        'currency': 'USD',
                        'commodity': 'Brent Crude',
                        'source': 'FRED'
                    }
        
        return None
        
    except Exception as e:
        print(f"[Oil Price] FRED failed: {str(e)[:100]}")
        return None


def fetch_oil_price():
    """
    Fetch Brent Crude oil price using IMPROVED cascading fallback system
    
    Tries 4 free APIs in order of reliability:
    1. US EIA (Energy Information Administration) - Government, FREE, no key
    2. OilPriceAPI Demo - FREE, 20 req/hour
    3. FRED (Federal Reserve) - Government, FREE CSV
    4. Alpha Vantage - 750/month = 25/day (backup)
    
    All completely free with high limits!
    """
    print("[Oil Price] Starting improved cascade...")
    
    # Try EIA first (most reliable government source)
    result = fetch_oil_eia()
    if result:
        return result
    
    print("[Oil Price] Trying fallback: OilPriceAPI Demo...")
    
    # Try OilPriceAPI demo (free, no auth)
    result = fetch_oil_demo_api()
    if result:
        return result
    
    print("[Oil Price] Trying fallback: FRED...")
    
    # Try FRED CSV download
    result = fetch_oil_fred()
    if result:
        return result
    
    print("[Oil Price] Trying fallback: Alpha Vantage...")
    
    # Try Alpha Vantage last (rate limited but reliable)
    result = fetch_oil_alpha_vantage()
    if result:
        return result
    
    # All APIs failed
    print("[Oil Price] ❌ All APIs failed")
    return None


# ========================================
# GOLD PRICE FETCHING - CASCADING FALLBACK SYSTEM  
# ========================================
def fetch_gold_goldapi():
    """
    Try GoldAPI.io (Free tier: 1000 requests/month)
    Most reliable for spot gold prices
    """
    try:
        print("[Gold Price] Trying GoldAPI.io...")
        
        # GoldAPI free endpoint
        url = "https://www.goldapi.io/api/XAU/USD"
        
        # Demo key works for testing (limited requests)
        # Get free key at: https://www.goldapi.io/
        headers = {
            'x-access-token': 'goldapi-demo',
            'Content-Type': 'application/json'
        }
        
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code != 200:
            print(f"[Gold Price] GoldAPI HTTP error: {response.status_code}")
            return None
        
        data = response.json()
        
        # GoldAPI returns: {"price": 2650.50, "timestamp": ...}
        price = float(data.get('price', 0))
        
        if price > 1000 and price < 10000:  # Sanity check
            print(f"[Gold Price] ✅ GoldAPI.io: ${price:.2f}/oz")
            
            return {
                'price': round(price, 2),
                'currency': 'USD',
                'unit': 'troy_oz',
                'source': 'GoldAPI.io',
                'timestamp': datetime.now(timezone.utc).isoformat()
            }
        
        print(f"[Gold Price] GoldAPI suspicious price: {price}")
        return None
        
    except Exception as e:
        print(f"[Gold Price] GoldAPI error: {str(e)[:100]}")
        return None


def fetch_gold_goldprice_org():
    """
    Try GoldPrice.org JSON endpoint (FREE, no auth required)
    Very reliable, no API key needed
    """
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
        
        # GoldPrice.org structure: {"items": [{"xauPrice": 2650.50, ...}]}
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
        
        print(f"[Gold Price] GoldPrice.org: No valid data")
        return None
        
    except Exception as e:
        print(f"[Gold Price] GoldPrice.org error: {str(e)[:100]}")
        return None


def fetch_gold_metals_api():
    """
    Try Metals-API (Free tier: 50 requests/month)
    Backup option, requires API key
    """
    try:
        print("[Gold Price] Trying Metals-API...")
        
        # Get free key at: https://metals-api.com/
        # For demo, using public endpoint
        url = "https://api.metals.dev/v1/latest?api_key=demo&currency=USD&unit=toz"
        
        response = requests.get(url, timeout=10)
        
        if response.status_code != 200:
            print(f"[Gold Price] Metals-API HTTP error: {response.status_code}")
            return None
        
        data = response.json()
        
        # Metals-API returns: {"rates": {"XAU": 0.000377}, ...}
        # This is inverted (USD per XAU), so we need 1/rate
        if 'rates' in data and 'XAU' in data['rates']:
            rate = float(data['rates']['XAU'])
            price = 1 / rate if rate > 0 else 0
            
            if price > 1000 and price < 10000:
                print(f"[Gold Price] ✅ Metals-API: ${price:.2f}/oz")
                
                return {
                    'price': round(price, 2),
                    'currency': 'USD',
                    'unit': 'troy_oz',
                    'source': 'Metals-API',
                    'timestamp': datetime.now(timezone.utc).isoformat()
                }
        
        print(f"[Gold Price] Metals-API: Invalid data structure")
        return None
        
    except Exception as e:
        print(f"[Gold Price] Metals-API error: {str(e)[:100]}")
        return None


def fetch_gold_price():
    """
    Fetch spot gold price (XAU/USD) using cascading free APIs
    
    Tries 3 free APIs in order of reliability:
    1. GoldPrice.org - FREE, no auth, very reliable
    2. GoldAPI.io - FREE tier 1000/month (demo key works)
    3. Metals-API - FREE tier 50/month
    4. Fallback - Recent market estimate
    
    Returns price per troy ounce in USD
    """
    print("[Gold Price] Starting cascade...")
    
    # Try GoldPrice.org first (most reliable, no auth)
    result = fetch_gold_goldprice_org()
    if result:
        return result
    
    print("[Gold Price] Trying fallback: GoldAPI.io...")
    
    # Try GoldAPI
    result = fetch_gold_goldapi()
    if result:
        return result
    
    print("[Gold Price] Trying fallback: Metals-API...")
    
    # Try Metals-API
    result = fetch_gold_metals_api()
    if result:
        return result
    
    # All APIs failed - use fallback estimate
    print("[Gold Price] ❌ All APIs failed, using fallback estimate")
    
    # As of Feb 2026, gold trading around $2,650-2,850/oz
    # Use conservative midpoint
    fallback_price = 2750
    
    return {
        'price': fallback_price,
        'currency': 'USD',
        'unit': 'troy_oz',
        'source': 'Estimated',
        'estimated': True,
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'note': 'Approximate - all gold APIs unavailable'
    }


def calculate_lebanon_gold_reserves():
    """
    Calculate current market value of Lebanon's gold reserves
    
    Lebanon holds 286.8 metric tons (9.22 million troy oz)
    - Held since 1960s, protected by 1986 law
    - 2nd largest reserves in Middle East after Saudi Arabia
    - Never been touched despite multiple wars & economic collapse
    
    Returns current USD valuation based on spot gold price
    """
    try:
        print("[Lebanon Gold] Calculating reserve value...")
        
        # Lebanon's fixed gold holdings (unchanged since 1960s)
        LEBANON_GOLD_TONS = 286.8
        TROY_OZ_PER_METRIC_TON = 32150.7466  # Exact conversion
        
        total_troy_oz = LEBANON_GOLD_TONS * TROY_OZ_PER_METRIC_TON
        
        print(f"[Lebanon Gold] Holdings: {LEBANON_GOLD_TONS} tons = {total_troy_oz:,.0f} troy oz")
        
        # Fetch current gold price
        gold_price_data = fetch_gold_price()
        
        if gold_price_data:
            price_per_oz = gold_price_data['price']
            
            # Calculate total value
            total_value_usd = total_troy_oz * price_per_oz
            total_value_billions = total_value_usd / 1_000_000_000
            
            # Calculate as % of Lebanon GDP (~$20B in 2026)
            LEBANON_GDP_BILLIONS = 20
            gdp_percentage = (total_value_billions / LEBANON_GDP_BILLIONS) * 100
            
            print(f"[Lebanon Gold] Value: ${total_value_billions:.1f}B @ ${price_per_oz:.2f}/oz")
            print(f"[Lebanon Gold] = {gdp_percentage:.0f}% of GDP")
            
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
        
        # Fallback if gold price fetch failed
        print("[Lebanon Gold] Using estimated value (price fetch failed)")
        
        return {
            'tons': LEBANON_GOLD_TONS,
            'troy_ounces': int(total_troy_oz),
            'total_value_billions': 45,  # Conservative midpoint
            'display_value': '~$40-50B',
            'estimated': True,
            'note': 'Value estimated - gold price unavailable',
            'rank_middle_east': 2,
            'protected_by_law': True
        }
        
    except Exception as e:
        print(f"[Lebanon Gold] ❌ Error: {str(e)}")
        
        # Minimal fallback
        return {
            'tons': 286.8,
            'display_value': '~$40-50B',
            'estimated': True,
            'error': str(e)[:100]
        }

# ========================================
# IRAN REGIME STABILITY TRACKER
# ========================================

def extract_iran_cities(articles):
    """
    Extract Iranian cities mentioned in protest articles with source links
    
    Only includes articles from the last 7 days to keep data fresh
    Returns list of cities with counts, source URLs, and source names
    """
    from datetime import datetime, timedelta, timezone
    
    # List of major Iranian cities to look for
    iranian_cities = [
        'Tehran', 'Mashhad', 'Isfahan', 'Karaj', 'Shiraz', 'Tabriz',
        'Qom', 'Ahvaz', 'Kermanshah', 'Urmia', 'Rasht', 'Kerman',
        'Zahedan', 'Hamadan', 'Yazd', 'Ardabil', 'Bandar Abbas', 'Arak',
        'Eslamshahr', 'Zanjan', 'Sanandaj', 'Qazvin', 'Khorramabad', 'Gorgan',
        'Sari', 'Dezful', 'Najafabad', 'Sabzevar', 'Khomeini Shahr', 'Neyshabur',
        'Babol', 'Amol', 'Birjand', 'Bojnurd', 'Ilam', 'Yasuj', 'Maragheh'
    ]
    
    city_data = {}
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=7)
    
    for article in articles:
        # Filter by recency (last 7 days only)
        pub_date = article.get('publishedAt') or article.get('published_date') or article.get('date')
        
        if pub_date:
            try:
                if isinstance(pub_date, str):
                    # Try parsing ISO format
                    try:
                        article_date = datetime.fromisoformat(pub_date.replace('Z', '+00:00'))
                    except:
                        # Try RFC 2822 format (from RSS)
                        from email.utils import parsedate_to_datetime
                        article_date = parsedate_to_datetime(pub_date)
                    
                    # Skip articles older than 7 days
                    if article_date < cutoff_date:
                        continue
            except:
                pass  # If date parsing fails, include the article anyway
        
        title = article.get('title', '')
        description = article.get('description', '')
        url = article.get('url', article.get('link', ''))
        
        # Extract source name from domain or source field
        source_name = article.get('source', {})
        if isinstance(source_name, dict):
            source_name = source_name.get('name', '')
        
        if not source_name:
            # Extract from URL domain
            try:
                from urllib.parse import urlparse
                domain = urlparse(url).netloc
                # Clean up domain
                source_name = domain.replace('www.', '').split('.')[0].title()
            except:
                source_name = 'Unknown'
        
        # Handle special sources
        if 'hrana' in url.lower():
            source_name = 'HRANA'
        elif 'iranwire' in url.lower():
            source_name = 'IranWire'
        elif 'reddit' in url.lower():
            source_name = 'Reddit'
        
        text = f"{title} {description}".lower()
        
        for city in iranian_cities:
            if city.lower() in text:
                if city not in city_data:
                    city_data[city] = {
                        'name': city,
                        'count': 0,
                        'sources': []
                    }
                
                city_data[city]['count'] += 1
                
                # Add source with name and URL (limit to 3 per city)
                if url and len(city_data[city]['sources']) < 3:
                    source_obj = {
                        'url': url,
                        'name': source_name
                    }
                    
                    # Avoid duplicate URLs
                    if not any(s['url'] == url for s in city_data[city]['sources']):
                        city_data[city]['sources'].append(source_obj)
    
    # Sort by count descending
    sorted_cities = sorted(city_data.values(), key=lambda x: x['count'], reverse=True)
    
    print(f"[Iran Cities] Found {len(sorted_cities)} cities mentioned in recent articles (7-day window)")
    for city in sorted_cities[:5]:
        print(f"[Iran Cities]   {city['name']}: {city['count']} mentions")
    
    return sorted_cities


def fetch_iran_exchange_rate():
    """Fetch USD/IRR exchange rate with 24h trend from ExchangeRate-API"""
    try:
        print("[Regime Stability] Fetching USD/IRR exchange rate with trend...")
        
        url = "https://open.exchangerate-api.com/v6/latest/USD"
        
        response = requests.get(url, timeout=10)
        
        if response.status_code != 200:
            print(f"[Regime Stability] ❌ Exchange API failed: {response.status_code}")
            return None
        
        data = response.json()
        
        irr_rate = data.get('rates', {}).get('IRR')
        
        if not irr_rate:
            print("[Regime Stability] ❌ IRR rate not found in response")
            return None
        
        print(f"[Regime Stability] ✅ Current USD/IRR: {irr_rate:,.0f}")
        
        # Estimate 24h change (placeholder - would use historical API in production)
        # For now, assume stable with slight weakening trend typical for IRR
        estimated_change = 0  # Will be updated with real historical data
        
        # Calculate trend
        if estimated_change > 0.1:
            trend = "weakening"  # Rate going up = currency weakening
            pressure = "SELLING"
        elif estimated_change < -0.1:
            trend = "strengthening"  # Rate going down = currency strengthening
            pressure = "BUYING"
        else:
            trend = "stable"
            pressure = "NEUTRAL"
        
        return {
            'usd_to_irr': irr_rate,
            'last_updated': data.get('time_last_update_utc', ''),
            'source': 'ExchangeRate-API',
            'change_24h': estimated_change,
            'trend': trend,
            'pressure': pressure,
            'yesterday_rate': irr_rate * (1 - estimated_change/100) if estimated_change != 0 else irr_rate
        }
        
    except Exception as e:
        print(f"[Regime Stability] ❌ Error: {str(e)[:200]}")
        return None

def calculate_regime_stability(exchange_data, protest_data, oil_data=None):
    """
    Calculate Iran regime stability score (0-100)
    
    Formula v2.9.0 (FIXED):
    Stability = Base(50)
                + Military Strength Baseline(+30)
                + Low Protest Bonus (when intensity < 20%)  # ← NEW!
                - (Rial Devaluation Impact × 0.15)
                - (Protest Intensity × 0.3)
                - (Arrest Rate Impact × 0.2)
                + (Oil Price Impact ±5)
                + (Time Decay Bonus)
    
    Lower scores = Higher instability/regime stress
    """
    
    base_score = 50
    military_strength_baseline = 30
    
    print(f"[Regime Stability] Military strength baseline: +{military_strength_baseline}")
    
    # ========================================
    # OIL PRICE IMPACT (±5 points)
    # ========================================
    oil_price_impact = 0
    
    if oil_data:
        oil_price = oil_data.get('price', 75)
        baseline_oil = 75
        
        oil_deviation = oil_price - baseline_oil
        oil_price_impact = (oil_deviation / 10) * 0.5
        oil_price_impact = max(-5, min(5, oil_price_impact))
        
        print(f"[Regime Stability] Oil price: ${oil_price:.2f} → Impact: {oil_price_impact:+.1f}")
    
    # ========================================
    # CURRENCY DEVALUATION IMPACT
    # ========================================
    rial_devaluation_impact = 0
    
    if exchange_data:
        current_rate = exchange_data.get('usd_to_irr', 42000)
        baseline_rate = 42000
        
        devaluation_pct = ((current_rate - baseline_rate) / baseline_rate) * 100
        rial_devaluation_impact = (devaluation_pct / 10) * 0.15
        
        print(f"[Regime Stability] Rial devaluation: {devaluation_pct:.1f}% → Impact: -{rial_devaluation_impact:.1f}")
    
    # ========================================
    # PROTEST INTENSITY IMPACT (WITH LOW-PROTEST BONUS)
    # ========================================
    protest_intensity_impact = 0
    low_protest_bonus = 0  # ← NEW!
    
    if protest_data:
        intensity = protest_data.get('intensity', 0)
        
        # ← NEW: When protests are VERY low (< 20%), give a stability BONUS
        if intensity < 20:
            low_protest_bonus = 10  # +10 points for quiet period
            print(f"[Regime Stability] ⭐ Low protest bonus: +{low_protest_bonus}")
        
        protest_intensity_impact = (intensity / 10) * 0.3
        
        arrests = protest_data.get('casualties', {}).get('arrests', 0)
        arrest_rate_impact = (arrests / 100) * 0.2
        
        print(f"[Regime Stability] Protest intensity: {intensity}/100 → Impact: -{protest_intensity_impact:.1f}")
        print(f"[Regime Stability] Arrests: {arrests} → Impact: -{arrest_rate_impact:.1f}")
    else:
        arrest_rate_impact = 0
    
    # ========================================
    # TIME DECAY BONUS
    # ========================================
    time_decay_bonus = 0
    
    if protest_data:
        days_analyzed = protest_data.get('days_analyzed', 7)
        total_articles = protest_data.get('total_articles', 0)
        
        articles_per_day = total_articles / days_analyzed if days_analyzed > 0 else 0
        
        if articles_per_day < 5:
            time_decay_bonus = 2
            print(f"[Regime Stability] Quiet period detected → Bonus: +{time_decay_bonus}")
    
    # ========================================
    # FINAL SCORE CALCULATION (FIXED)
    # ========================================
    stability_score = (base_score + military_strength_baseline + oil_price_impact +
                      low_protest_bonus +  # ← NEW!
                      time_decay_bonus -
                      rial_devaluation_impact - protest_intensity_impact - 
                      arrest_rate_impact)
    
    stability_score = max(0, min(100, stability_score))
    stability_score = int(stability_score)
    
    # ========================================
    # TREND CALCULATION
    # ========================================
    trend = "stable"
    
    if protest_data:
        intensity = protest_data.get('intensity', 0)
        
        if intensity > 40:
            trend = "decreasing"
        elif intensity < 20:
            trend = "increasing"  # Low protests = increasing stability!
        else:
            trend = "stable"
    
    # ========================================
    # RISK LEVEL
    # ========================================
    if stability_score >= 70:
        risk_level = "Low Risk"
        risk_color = "green"
    elif stability_score >= 50:
        risk_level = "Moderate Risk"
        risk_color = "yellow"
    elif stability_score >= 30:
        risk_level = "High Risk"
        risk_color = "orange"
    else:
        risk_level = "Critical Risk"
        risk_color = "red"
    
    print(f"[Regime Stability] ✅ Final Score: {stability_score}/100 ({risk_level})")
    
    return {
        'stability_score': stability_score,
        'trend': trend,
        'risk_level': risk_level,
        'risk_color': risk_color,
        'breakdown': {
            'base_score': base_score,
            'military_strength_baseline': military_strength_baseline,
            'oil_price_impact': round(oil_price_impact, 2),
            'low_protest_bonus': low_protest_bonus,  # ← NEW!
            'rial_devaluation_impact': round(-rial_devaluation_impact, 2),
            'protest_intensity_impact': round(-protest_intensity_impact, 2),
            'arrest_rate_impact': round(-arrest_rate_impact, 2),
            'time_decay_bonus': round(time_decay_bonus, 2),
            'formula': 'Base(50) + Military(+30) + LowProtestBonus + Oil - Rial - Protest - Arrest + Time'
        }
    }

# ========================================
# LEBANON STABILITY TRACKER
# ========================================

def scrape_lebanon_bonds():
    """
    Scrape Lebanon 10Y Eurobond yield from Investing.com
    High yields = economic stress/default risk
    
    Note: Lebanon defaulted on sovereign debt in March 2020
    Eurobonds now trading as distressed debt with very high yields
    """
    try:
        print("[Lebanon Bonds] Scraping Investing.com for Lebanon Eurobond data...")
        
        # Try primary URL for Lebanon 10Y
        url = "https://www.investing.com/rates-bonds/lebanon-10-year-bond-yield"
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
        }
        
        response = requests.get(url, timeout=10, headers=headers)
        
        if response.status_code != 200:
            print(f"[Lebanon Bonds] HTTP error from Investing.com: {response.status_code}")
            return scrape_lebanon_bonds_fallback()
        
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Investing.com stores the current value in various places
        # Try multiple selectors
        
        # Method 1: Look for data-test attribute (most reliable)
        value_elem = soup.find('span', {'data-test': 'instrument-price-last'})
        
        # Method 2: Look for common class names
        if not value_elem:
            value_elem = soup.find('span', class_=lambda x: x and 'text-2xl' in x)
        
        # Method 3: Look for any span with large text that's a number
        if not value_elem:
            for span in soup.find_all('span'):
                text = span.get_text().strip()
                if text and any(char.isdigit() for char in text) and '%' not in text:
                    import re
                    if re.match(r'^\d+\.?\d*$', text):
                        value_elem = span
                        break
        
        if value_elem:
            text = value_elem.get_text().strip()
            # Extract number
            import re
            match = re.search(r'(\d+\.?\d*)', text)
            if match:
                yield_pct = float(match.group(1))
                
                # Sanity check: Lebanon yields should be 20-100% (distressed debt)
                if 10 <= yield_pct <= 200:
                    print(f"[Lebanon Bonds] ✅ Investing.com: 10Y yield: {yield_pct}%")
                    
                    return {
                        'yield': yield_pct,
                        'source': 'Investing.com',
                        'date': datetime.now(timezone.utc).isoformat(),
                        'note': 'Distressed debt (defaulted March 2020)'
                    }
                else:
                    print(f"[Lebanon Bonds] ⚠️ Suspicious yield value: {yield_pct}% (out of expected range)")
        
        print("[Lebanon Bonds] Could not extract yield from Investing.com, trying fallback...")
        return scrape_lebanon_bonds_fallback()
        
    except Exception as e:
        print(f"[Lebanon Bonds] Investing.com error: {str(e)[:150]}")
        return scrape_lebanon_bonds_fallback()


def scrape_lebanon_bonds_fallback():
    """
    Fallback bond data using known estimates
    Lebanon has been in default since March 2020
    """
    try:
        print("[Lebanon Bonds] Using estimated fallback data...")
        
        # Lebanon Eurobonds trade as distressed debt
        # Typical yields: 40-60% (varies by maturity and market conditions)
        # This is an estimate based on recent market data
        
        estimated_yield = 45.0  # Conservative mid-range estimate
        
        print(f"[Lebanon Bonds] ⚠️ Using estimated yield: {estimated_yield}% (Lebanon in default since 2020)")
        
        return {
            'yield': estimated_yield,
            'source': 'Estimated',
            'date': datetime.now(timezone.utc).isoformat(),
            'note': 'Estimated - Lebanon defaulted March 2020. Update manually for accuracy.'
        }
        
    except Exception as e:
        print(f"[Lebanon Bonds] Fallback error: {str(e)[:100]}")
        return None


def fetch_lebanon_currency():
    """
    Fetch LBP/USD black market rate with 24h trend
    Official rate ~1,500, black market rate ~90,000+ (massive collapse)
    """
    try:
        print("[Lebanon Currency] Fetching LBP/USD black market rate with trend...")
        
        # Get current rate
        url_current = "https://open.exchangerate-api.com/v6/latest/USD"
        response = requests.get(url_current, timeout=10)
        
        current_rate = None
        yesterday_rate = None
        
        if response.status_code == 200:
            data = response.json()
            current_rate = data.get('rates', {}).get('LBP')
            
            if current_rate:
                print(f"[Lebanon Currency] ✅ Current USD/LBP: {current_rate:,.0f}")
                
                # Try to get historical rate (24h ago)
                # ExchangeRate-API free tier doesn't have historical, so we'll estimate
                # For production, could use paid tier or alternative API
                
                # Estimate 24h change based on typical volatility (0.1-1% daily for LBP)
                # This is a placeholder - in production, fetch real historical data
                estimated_change = 0  # Assume stable for now, update with real API later
                
                # Calculate trend
                change_24h = estimated_change
                if change_24h > 0.1:
                    trend = "weakening"  # Rate going up = currency weakening
                    pressure = "SELLING"
                elif change_24h < -0.1:
                    trend = "strengthening"  # Rate going down = currency strengthening  
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
                    'change_24h': change_24h,
                    'trend': trend,
                    'pressure': pressure,
                    'yesterday_rate': current_rate * (1 - change_24h/100) if change_24h != 0 else current_rate
                }
        
        # Fallback: estimate based on known collapse
        print("[Lebanon Currency] Using estimated black market rate")
        return {
            'usd_to_lbp': 90000,
            'official_rate': 1500,
            'devaluation_pct': ((90000 - 1500) / 1500) * 100,
            'source': 'Estimated',
            'last_updated': datetime.now(timezone.utc).isoformat(),
            'change_24h': 0,
            'trend': 'stable',
            'pressure': 'NEUTRAL',
            'yesterday_rate': 90000
        }
        
    except Exception as e:
        print(f"[Lebanon Currency] ❌ Error: {str(e)[:200]}")
        return None


def track_hezbollah_activity(days=7):
    """
    Track Hezbollah rearmament and activity indicators
    
    Looks for:
    - Rearmament mentions
    - Israeli strikes on Hezbollah
    - UNIFIL reports
    - Iranian weapons shipments
    """
    try:
        print("[Hezbollah] Scanning news for activity indicators...")
        
        # Search multiple keywords
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
                # Google News RSS
                query = keyword.replace(' ', '+')
                url = f"https://news.google.com/rss/search?q={query}&hl=en&gl=US&ceid=US:en"
                
                response = requests.get(url, timeout=10, headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                })
                
                if response.status_code == 200:
                    import xml.etree.ElementTree as ET
                    
                    root = ET.fromstring(response.content)
                    items = root.findall('.//item')
                    
                    for item in items[:5]:  # Top 5 per keyword
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
        
        # Count indicators
        rearmament_count = sum(1 for a in all_articles if 'rearm' in a['title'].lower())
        strike_count = sum(1 for a in all_articles if 'strike' in a['title'].lower() or 'attack' in a['title'].lower())
        
        activity_score = min((rearmament_count * 5 + strike_count * 3), 100)
        
        print(f"[Hezbollah] Found {len(all_articles)} articles, activity score: {activity_score}/100")
        
        return {
            'articles': all_articles[:20],  # Top 20
            'total_articles': len(all_articles),
            'rearmament_mentions': rearmament_count,
            'strike_mentions': strike_count,
            'activity_score': activity_score
        }
        
    except Exception as e:
        print(f"[Hezbollah] ❌ Error: {str(e)[:200]}")
        return {
            'articles': [],
            'total_articles': 0,
            'rearmament_mentions': 0,
            'strike_mentions': 0,
            'activity_score': 0
        }


def calculate_lebanon_stability(currency_data, bond_data, hezbollah_data):
    """
    Calculate Lebanon stability score (0-100)
    
    Formula v2.7.0:
    Stability = Base(50)
                - Currency Collapse Impact (-10 to -30)
                - Bond Yield Stress (-5 to -25)
                - Hezbollah Activity (-0 to -20)
                + Presidential Leadership (+10) [Joseph Aoun elected Jan 9, 2025]
                + Election Proximity Bonus (+5 if within 90 days)
    
    Lower scores = Higher instability
    
    Color codes:
    - 70-100: Stable (Green)
    - 40-69: Moderate Risk (Yellow)
    - 20-39: High Risk (Orange)  
    - 0-19: Critical (Red)
    """
    
    base_score = 50
    
    print("[Lebanon Stability] Calculating overall stability score...")
    
    # ========================================
    # CURRENCY COLLAPSE IMPACT
    # ========================================
    currency_impact = 0
    
    if currency_data:
        current_rate = currency_data.get('usd_to_lbp', 90000)
        official_rate = 1500
        
        # Massive devaluation (90,000/1500 = 60x collapse)
        devaluation_pct = ((current_rate - official_rate) / official_rate) * 100
        
        # Scale: 0-3000% devaluation → 0-30 points penalty
        currency_impact = min((devaluation_pct / 100), 30)
        
        print(f"[Lebanon Stability] Currency: {current_rate:,.0f} LBP/USD ({devaluation_pct:.0f}% devaluation) → Impact: -{currency_impact:.1f}")
    
    # ========================================
    # BOND YIELD STRESS
    # ========================================
    bond_impact = 0
    
    if bond_data:
        bond_yield = bond_data.get('yield', 0)
        
        # Lebanon defaulted in 2020, yields are extremely high
        # Normal bonds: 2-5%, Lebanon: 30-50%+
        # Scale: 0-50% yield → 0-25 points penalty
        bond_impact = min((bond_yield / 2), 25)
        
        print(f"[Lebanon Stability] Bond yield: {bond_yield}% → Impact: -{bond_impact:.1f}")
    
    # ========================================
    # HEZBOLLAH ACTIVITY IMPACT
    # ========================================
    hezbollah_impact = 0
    
    if hezbollah_data:
        activity_score = hezbollah_data.get('activity_score', 0)
        
        # High activity = instability
        # 0-100 activity → 0-20 points penalty
        hezbollah_impact = (activity_score / 100) * 20
        
        print(f"[Lebanon Stability] Hezbollah activity: {activity_score}/100 → Impact: -{hezbollah_impact:.1f}")
    
    # ========================================
    # PRESIDENTIAL LEADERSHIP BONUS
    # ========================================
    # Joseph Aoun elected president on January 9, 2025
    # This is a POSITIVE development after 2+ years of vacancy
    presidential_bonus = 10
    president_elected_date = datetime(2025, 1, 9, tzinfo=timezone.utc)
    days_with_president = (datetime.now(timezone.utc) - president_elected_date).days
    
    print(f"[Lebanon Stability] President Joseph Aoun ({days_with_president} days in office) → Bonus: +{presidential_bonus}")
    
    # ========================================
    # ELECTION PROXIMITY BONUS
    # ========================================
    election_bonus = 0
    
    # Parliamentary elections scheduled for May 10, 2026
    election_date = datetime(2026, 5, 10, tzinfo=timezone.utc)
    days_until_election = (election_date - datetime.now(timezone.utc)).days
    
    if 0 <= days_until_election <= 90:
        election_bonus = 5
        print(f"[Lebanon Stability] Elections in {days_until_election} days → Bonus: +{election_bonus}")
    
    # ========================================
    # FINAL SCORE CALCULATION
    # ========================================
    stability_score = (base_score - currency_impact - bond_impact - 
                      hezbollah_impact + presidential_bonus + election_bonus)
    
    stability_score = max(0, min(100, stability_score))
    stability_score = int(stability_score)
    
    # ========================================
    # RISK LEVEL & COLOR
    # ========================================
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
    
    print(f"[Lebanon Stability] ✅ Final Score: {stability_score}/100 ({risk_level})")
    
    # ========================================
    # TREND CALCULATION
    # ========================================
    # Based on recent activity
    trend = "stable"
    
    if hezbollah_data and hezbollah_data.get('activity_score', 0) > 50:
        trend = "worsening"
    elif days_with_president < 60:
        trend = "improving"  # New president is positive
    elif days_until_election < 60:
        trend = "improving"  # Elections approaching
    
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

def load_casualty_cache():
    """Load daily casualty cache for trend calculation"""
    cache_file = 'cache_iran_casualties.json'
    try:
        if os.path.exists(cache_file):
            with open(cache_file, 'r') as f:
                return json.load(f)
        else:
            # Create initial cache
            initial_cache = {
                'last_updated': datetime.now(timezone.utc).isoformat(),
                'history': {},
                'metadata': {
                    'description': 'Daily snapshots of Iran protest casualties',
                    'data_source': 'HRANA RSS + multi-source aggregation',
                    'started': datetime.now(timezone.utc).date().isoformat()
                }
            }
            with open(cache_file, 'w') as f:
                json.dump(initial_cache, f, indent=2)
            return initial_cache
    except Exception as e:
        print(f"[Cache] Error loading cache: {str(e)}")
        return {'history': {}, 'last_updated': '', 'metadata': {}}


def save_casualty_cache(cache_data):
    """Save daily casualty cache"""
    cache_file = 'cache_iran_casualties.json'
    try:
        cache_data['last_updated'] = datetime.now(timezone.utc).isoformat()
        with open(cache_file, 'w') as f:
            json.dump(cache_data, f, indent=2)
        print(f"[Cache] Saved casualty data for {len(cache_data.get('history', {}))} days")
    except Exception as e:
        print(f"[Cache] Error saving cache: {str(e)}")


def update_casualty_cache(casualties):
    """Update cache with today's casualty data"""
    try:
        cache = load_casualty_cache()
        today = datetime.now(timezone.utc).date().isoformat()
        
        # Store today's 7-day snapshot
        cache['history'][today] = {
            'arrests_7d': casualties.get('arrests', 0),
            'deaths_7d': casualties.get('deaths', 0),
            'injuries_7d': casualties.get('injuries', 0),
            'timestamp': datetime.now(timezone.utc).isoformat()
        }
        
        # Keep only last 90 days
        if len(cache['history']) > 90:
            sorted_dates = sorted(cache['history'].keys())
            for old_date in sorted_dates[:-90]:
                del cache['history'][old_date]
        
        save_casualty_cache(cache)
        print(f"[Cache] Updated casualties for {today}")
        
    except Exception as e:
        print(f"[Cache] Error updating cache: {str(e)}")


def calculate_casualty_trends(current_casualties):
    """
    Calculate trends and estimates with HRANA extraction + cache fallback
    
    Priority:
    1. Try to extract cumulative from HRANA articles (Option 2)
    2. Fallback to cache history calculation (Option 3)
    3. Show "unavailable" if insufficient data
    """
    try:
        cache = load_casualty_cache()
        history = cache.get('history', {})
        
        # Get yesterday's data for trend calculation
        yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
        yesterday_data = history.get(yesterday, {})
        
        # Get cache start date
        cache_start_date = cache.get('metadata', {}).get('started', datetime.now(timezone.utc).date().isoformat())
        
        # ========================================
        # STEP 1: Try to get cumulative from HRANA (Option 2)
        # ========================================
        hrana_cumulative_deaths = current_casualties.get('hrana_cumulative_deaths')
        hrana_cumulative_arrests = current_casualties.get('hrana_cumulative_arrests')
        hrana_cumulative_injuries = current_casualties.get('hrana_cumulative_injuries')
        
        print(f"[Casualty Trends] HRANA cumulative - Deaths: {hrana_cumulative_deaths}, Arrests: {hrana_cumulative_arrests}, Injuries: {hrana_cumulative_injuries}")
        
        # ========================================
        # STEP 2: Fallback to cache calculation (Option 3)
        # ========================================
        cache_cumulative_deaths = None
        cache_cumulative_arrests = None
        cache_cumulative_injuries = None
        
        if len(history) >= 7:  # Need at least 1 week of data
            # Sum all 7-day snapshots from cache (will have overlap, but gives rough cumulative)
            cache_cumulative_deaths = sum(day.get('deaths_7d', 0) for day in history.values())
            cache_cumulative_arrests = sum(day.get('arrests_7d', 0) for day in history.values())
            cache_cumulative_injuries = sum(day.get('injuries_7d', 0) for day in history.values())
            
            print(f"[Casualty Trends] Cache-based cumulative - Deaths: {cache_cumulative_deaths}, Arrests: {cache_cumulative_arrests}")
        
        # ========================================
        # STEP 3: Choose best cumulative source
        # ========================================
        cumulative_deaths = hrana_cumulative_deaths if hrana_cumulative_deaths else cache_cumulative_deaths
        cumulative_arrests = hrana_cumulative_arrests if hrana_cumulative_arrests else cache_cumulative_arrests
        cumulative_injuries = hrana_cumulative_injuries if hrana_cumulative_injuries else cache_cumulative_injuries
        
        # Determine data source
        if hrana_cumulative_deaths or hrana_cumulative_arrests:
            cumulative_source = 'HRANA reports'
            cumulative_since = 'Since 2022 (Mahsa Amini protests)'
        elif cumulative_deaths or cumulative_arrests:
            cumulative_source = 'Cache tracking'
            cumulative_since = f'Since {cache_start_date} (tracking began)'
        else:
            cumulative_source = 'Unavailable'
            cumulative_since = 'Insufficient data'
        
        print(f"[Casualty Trends] Using cumulative source: {cumulative_source}")
        
        # ========================================
        # Current 7-day values (with sanity check)
        # ========================================
        arrests_7d = current_casualties.get('arrests', 0)
        deaths_7d = current_casualties.get('deaths', 0)
        injuries_7d = current_casualties.get('injuries', 0)
        
        # SANITY CHECK: 7-day can't exceed cumulative
        if cumulative_deaths and deaths_7d > cumulative_deaths:
            print(f"[Casualty Trends] ⚠️ 7-day deaths ({deaths_7d}) > cumulative ({cumulative_deaths}). Likely parsing error, setting to 0.")
            deaths_7d = 0
        
        if cumulative_arrests and arrests_7d > cumulative_arrests * 0.5:  # 7d shouldn't be >50% of cumulative
            print(f"[Casualty Trends] ⚠️ 7-day arrests ({arrests_7d}) suspiciously high vs cumulative ({cumulative_arrests}). Capping.")
            arrests_7d = min(arrests_7d, 1000)
        
        # Use 7-day as-is for 30-day estimate (no multiplication)
        arrests_30d = arrests_7d
        deaths_30d = deaths_7d
        injuries_30d = injuries_7d
        
        # ========================================
        # Calculate trends
        # ========================================
        def calc_trend(current, previous):
            if previous and previous > 0:
                return ((current - previous) / previous) * 100
            elif current > 0:
                return 10.0
            return 0
        
        arrests_trend = calc_trend(arrests_7d, yesterday_data.get('arrests_7d', 0))
        deaths_trend = calc_trend(deaths_7d, yesterday_data.get('deaths_7d', 0))
        injuries_trend = calc_trend(injuries_7d, yesterday_data.get('injuries_7d', 0))
        
        # ========================================
        # Calculate weekly averages
        # ========================================
        if cumulative_deaths and cumulative_source == 'HRANA reports':
            # Calculate from Sept 2022 to now
            start_date = datetime(2022, 9, 16, tzinfo=timezone.utc)
            weeks_since_start = (datetime.now(timezone.utc) - start_date).days / 7
            
            avg_deaths_week = int(cumulative_deaths / weeks_since_start) if weeks_since_start > 0 else 0
            avg_arrests_week = int(cumulative_arrests / weeks_since_start) if (cumulative_arrests and weeks_since_start > 0) else 0
            avg_injuries_week = int(cumulative_injuries / weeks_since_start) if (cumulative_injuries and weeks_since_start > 0) else 0
        
        elif cumulative_deaths and len(history) > 0:
            # Calculate from cache start
            avg_deaths_week = int(cumulative_deaths / len(history)) if len(history) > 0 else 0
            avg_arrests_week = int(cumulative_arrests / len(history)) if len(history) > 0 else 0
            avg_injuries_week = int(cumulative_injuries / len(history)) if len(history) > 0 else 0
        
        else:
            avg_deaths_week = 0
            avg_arrests_week = 0
            avg_injuries_week = 0
        
        # ========================================
        # Build enhanced response
        # ========================================
        enhanced = {
            'recent_7d': {
                'arrests': arrests_7d,
                'deaths': deaths_7d,
                'injuries': injuries_7d
            },
            'recent_30d': {
                'arrests': arrests_30d,
                'deaths': deaths_30d,
                'injuries': injuries_30d,
                'estimated': True
            },
            'cumulative': {
                'arrests': f"~{cumulative_arrests:,}+" if cumulative_arrests else 'Data unavailable',
                'deaths': cumulative_deaths if cumulative_deaths else 'Data unavailable',
                'injuries': f"~{cumulative_injuries:,}+" if cumulative_injuries else 'Data unavailable',
                'source': cumulative_source,
                'since': cumulative_since,
                'estimated': cumulative_source != 'HRANA reports'
            },
            'averages': {
                'arrests_per_week': avg_arrests_week,
                'deaths_per_week': avg_deaths_week,
                'injuries_per_week': avg_injuries_week
            },
            'trends': {
                'arrests': round(arrests_trend, 1),
                'deaths': round(deaths_trend, 1),
                'injuries': round(injuries_trend, 1),
                'has_historical_data': len(history) > 1
            },
            'sources': current_casualties.get('sources', []),
            'hrana_verified': current_casualties.get('hrana_verified', False)
        }
        
        # ← NEW: Fallback when NO data extracted at all
        arrests_7d = enhanced['recent_7d'].get('arrests', 0)
        deaths_7d = enhanced['recent_7d'].get('deaths', 0)
        injuries_7d = enhanced['recent_7d'].get('injuries', 0)
        
        if (arrests_7d == 0 and deaths_7d == 0 and injuries_7d == 0 and
            enhanced['cumulative'].get('deaths') == 'Data unavailable' and
            enhanced['cumulative'].get('arrests') == 'Data unavailable'):
            
            print("[Casualty Trends] ⚠️ No data found - using 'No recent data' placeholders")
            enhanced['recent_7d'] = {
                'arrests': 'No recent data',
                'deaths': 'No recent data',
                'injuries': 'No recent data'
            }
        
        # Update cache with today's data
        update_casualty_cache(current_casualties)
        
        return enhanced
        
    except Exception as e:
        print(f"[Trends] Error calculating trends: {str(e)}")
        import traceback
        traceback.print_exc()
        
        # Return basic data if calculation fails
        return {
            'recent_7d': current_casualties,
            'recent_30d': {'estimated': True},
            'cumulative': {
                'arrests': 'Data unavailable',
                'deaths': 'Data unavailable',
                'injuries': 'Data unavailable',
                'source': 'Error',
                'since': 'N/A'
            },
            'averages': {},
            'trends': {},
            'sources': current_casualties.get('sources', []),
            'hrana_verified': current_casualties.get('hrana_verified', False)
        }
     # ========================================
# INSTAGRAM FEED SCRAPER
# ========================================
def scrape_osint_instagram():
    """
    Scrape OSINTDefender's Instagram feed
    Returns last 3 posts with images, captions, timestamps, URLs
    """
    try:
        print(f"[Instagram] Scraping @{OSINT_INSTAGRAM_HANDLE}...")
        
        # Instagram public profile URL
        url = f"https://www.instagram.com/{OSINT_INSTAGRAM_HANDLE}/"
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Cache-Control': 'max-age=0'
        }
        
        response = requests.get(url, headers=headers, timeout=15)
        
        if response.status_code != 200:
            print(f"[Instagram] HTTP error: {response.status_code}")
            return scrape_osint_instagram_fallback()
        
        html = response.text
        
        # Instagram embeds post data in JavaScript as JSON
        # Look for window._sharedData pattern
        import re
        
        # Try to find JSON data embedded in script tags
        # Pattern 1: window._sharedData
        pattern1 = r'window\._sharedData\s*=\s*(\{.+?\});'
        match1 = re.search(pattern1, html)
        
        # Pattern 2: Static JSON (newer Instagram)
        pattern2 = r'<script type="application/ld\+json">(.+?)</script>'
        matches2 = re.findall(pattern2, html, re.DOTALL)
        
        posts = []
        
        # Try Pattern 1 first
        if match1:
            try:
                shared_data = json.loads(match1.group(1))
                
                # Navigate to posts
                user_data = shared_data.get('entry_data', {}).get('ProfilePage', [{}])[0]
                user_info = user_data.get('graphql', {}).get('user', {})
                edges = user_info.get('edge_owner_to_timeline_media', {}).get('edges', [])
                
                for edge in edges[:3]:  # First 3 posts
                    node = edge.get('node', {})
                    
                    post_id = node.get('shortcode', '')
                    caption_edges = node.get('edge_media_to_caption', {}).get('edges', [])
                    caption = caption_edges[0].get('node', {}).get('text', '') if caption_edges else ''
                    timestamp = node.get('taken_at_timestamp', 0)
                    image_url = node.get('display_url', '')
                    
                    if post_id and image_url:
                        posts.append({
                            'post_id': post_id,
                            'caption': caption[:300],  # Truncate long captions
                            'image_url': image_url,
                            'post_url': f"https://www.instagram.com/p/{post_id}/",
                            'timestamp': datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat() if timestamp else ''
                        })
                
                print(f"[Instagram] ✅ Scraped {len(posts)} posts via Pattern 1")
                
            except json.JSONDecodeError:
                print("[Instagram] JSON decode error on Pattern 1")
        
        # Try Pattern 2 if Pattern 1 failed
        if not posts and matches2:
            for json_str in matches2:
                try:
                    data = json.loads(json_str)
                    
                    # Look for article or social media posting schemas
                    if data.get('@type') in ['SocialMediaPosting', 'ImageObject']:
                        # Extract what we can
                        print("[Instagram] Found structured data, but format varies")
                        # This pattern is less reliable, mostly for metadata
                        
                except:
                    continue
        
        # If scraping failed, try RSS-to-JSON fallback
        if not posts:
            print("[Instagram] Direct scraping failed, trying fallback...")
            return scrape_osint_instagram_fallback()
        
        # Detect countries in captions
        for post in posts:
            post['countries'] = detect_countries_in_text(post['caption'])
        
        return {
            'success': True,
            'posts': posts,
            'count': len(posts),
            'handle': OSINT_INSTAGRAM_HANDLE,
            'source': 'instagram_scrape'
        }
        
    except Exception as e:
        print(f"[Instagram] Scraping error: {str(e)[:200]}")
        return scrape_osint_instagram_fallback()


def scrape_osint_instagram_fallback():
    """
    Fallback: Use Picuki (Instagram viewer) or return placeholder
    """
    try:
        print("[Instagram Fallback] Trying Picuki...")
        
        # Picuki is an Instagram viewer that doesn't require auth
        url = f"https://www.picuki.com/profile/{OSINT_INSTAGRAM_HANDLE}"
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code != 200:
            print(f"[Instagram Fallback] Picuki failed: {response.status_code}")
            return generate_instagram_placeholder()
        
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(response.content, 'html.parser')
        
        posts = []
        
        # Picuki uses .box-photos class for posts
        photo_boxes = soup.find_all('div', class_='box-photo')[:3]
        
        for box in photo_boxes:
            try:
                # Extract image
                img_tag = box.find('img')
                image_url = img_tag.get('src') if img_tag else ''
                
                # Extract caption
                caption_div = box.find('div', class_='photo-description')
                caption = caption_div.get_text(strip=True) if caption_div else ''
                
                # Extract post link
                link_tag = box.find('a', href=True)
                post_url = link_tag['href'] if link_tag else ''
                if post_url and not post_url.startswith('http'):
                    post_url = f"https://www.picuki.com{post_url}"
                
                # Extract timestamp
                time_tag = box.find('time')
                timestamp = time_tag.get('datetime', '') if time_tag else ''
                
                if image_url:
                    posts.append({
                        'post_id': post_url.split('/')[-1] if post_url else '',
                        'caption': caption[:300],
                        'image_url': image_url,
                        'post_url': post_url.replace('picuki.com', 'instagram.com'),  # Convert to IG link
                        'timestamp': timestamp,
                        'countries': detect_countries_in_text(caption)
                    })
            except:
                continue
        
        if posts:
            print(f"[Instagram Fallback] ✅ Got {len(posts)} posts via Picuki")
            return {
                'success': True,
                'posts': posts,
                'count': len(posts),
                'handle': OSINT_INSTAGRAM_HANDLE,
                'source': 'picuki_fallback'
            }
        
        print("[Instagram Fallback] Picuki parsing failed")
        return generate_instagram_placeholder()
        
    except Exception as e:
        print(f"[Instagram Fallback] Error: {str(e)[:100]}")
        return generate_instagram_placeholder()


def generate_instagram_placeholder():
    """
    Generate placeholder data if all scraping fails
    Shows "Unable to load feed" message
    """
    return {
        'success': False,
        'posts': [],
        'count': 0,
        'handle': OSINT_INSTAGRAM_HANDLE,
        'message': 'Unable to load Instagram feed. Visit @osintdefender directly.',
        'source': 'placeholder'
    }


def detect_countries_in_text(text):
    """
    Detect Middle East countries mentioned in text
    Returns list of country names (for RED badges)
    """
    if not text:
        return []
    
    text_lower = text.lower()
    detected = []
    
    for country, keywords in MIDDLE_EAST_COUNTRIES.items():
        for keyword in keywords:
            if keyword in text_lower:
                if country not in detected:
                    detected.append(country)
                break
    
    return detected[:2]  # Max 2 country tags per post
        
# ========================================
# LEBANON STABILITY CACHE FUNCTIONS
# ========================================
def load_lebanon_cache():
    """Load daily Lebanon stability cache for trend calculation"""
    cache_file = 'cache_lebanon_stability.json'
    try:
        if os.path.exists(cache_file):
            with open(cache_file, 'r') as f:
                return json.load(f)
        else:
            # Create initial cache
            initial_cache = {
                'last_updated': datetime.now(timezone.utc).isoformat(),
                'history': {},
                'metadata': {
                    'description': 'Daily snapshots of Lebanon stability metrics',
                    'data_source': 'Currency API + Bond scraping + Hezbollah activity',
                    'started': datetime.now(timezone.utc).date().isoformat()
                }
            }
            with open(cache_file, 'w') as f:
                json.dump(initial_cache, f, indent=2)
            print("[Lebanon Cache] Created new cache file")
            return initial_cache
    except Exception as e:
        print(f"[Lebanon Cache] Error loading cache: {str(e)}")
        return {'history': {}, 'last_updated': '', 'metadata': {}}


def save_lebanon_cache(cache_data):
    """Save daily Lebanon stability cache"""
    cache_file = 'cache_lebanon_stability.json'
    try:
        cache_data['last_updated'] = datetime.now(timezone.utc).isoformat()
        with open(cache_file, 'w') as f:
            json.dump(cache_data, f, indent=2)
        print(f"[Lebanon Cache] Saved stability data for {len(cache_data.get('history', {}))} days")
    except Exception as e:
        print(f"[Lebanon Cache] Error saving cache: {str(e)}")


def update_lebanon_cache(currency_data, bond_data, hezbollah_data, stability_score, gold_data=None):
    """Update cache with today's Lebanon stability snapshot"""
    try:
        cache = load_lebanon_cache()
        today = datetime.now(timezone.utc).date().isoformat()
        
        # Store today's snapshot
        cache['history'][today] = {
            'currency_rate': currency_data.get('usd_to_lbp', 0) if currency_data else 0,
            'bond_yield': bond_data.get('yield', 0) if bond_data else 0,
            'hezbollah_activity': hezbollah_data.get('activity_score', 0) if hezbollah_data else 0,
            'stability_score': stability_score,
            'gold_price_per_oz': gold_data.get('price_per_oz', 0) if gold_data else 0,  # ← NEW!
            'gold_value_billions': gold_data.get('total_value_billions', 0) if gold_data else 0,  # ← NEW!
            'timestamp': datetime.now(timezone.utc).isoformat()
        }
        
        # Keep only last 90 days
        if len(cache['history']) > 90:
            sorted_dates = sorted(cache['history'].keys())
            for old_date in sorted_dates[:-90]:
                del cache['history'][old_date]
        
        save_lebanon_cache(cache)
        print(f"[Lebanon Cache] Updated stability data for {today}")
        
    except Exception as e:
        print(f"[Lebanon Cache] Error updating cache: {str(e)}")

def get_lebanon_trends(days=30):
    """
    Get trend data for Lebanon stability sparklines
    
    Returns last N days of:
    - Currency rate (LBP/USD)
    - Bond yield (%)
    - Hezbollah activity score
    - Overall stability score
    - Gold price per oz (NEW!)
    - Gold reserves value in billions (NEW!)
    """
    try:
        cache = load_lebanon_cache()
        history = cache.get('history', {})
        
        if not history:
            return {
                'success': False,
                'message': 'No historical data yet. Building trend data...',
                'days_collected': 0
            }
        
        # Get last N days
        sorted_dates = sorted(history.keys(), reverse=True)[:days]
        sorted_dates.reverse()  # Chronological order for charts
        
        trends = {
            'dates': [],
            'currency': [],
            'bonds': [],
            'hezbollah': [],
            'stability': [],
            'gold_price': [],      # ← NEW!
            'gold_value': []       # ← NEW!
        }
        
        for date in sorted_dates:
            day_data = history[date]
            trends['dates'].append(date)
            trends['currency'].append(day_data.get('currency_rate', 0))
            trends['bonds'].append(day_data.get('bond_yield', 0))
            trends['hezbollah'].append(day_data.get('hezbollah_activity', 0))
            trends['stability'].append(day_data.get('stability_score', 0))
            trends['gold_price'].append(day_data.get('gold_price_per_oz', 0))       # ← NEW!
            trends['gold_value'].append(day_data.get('gold_value_billions', 0))     # ← NEW!
        
        return {
            'success': True,
            'days_collected': len(sorted_dates),
            'trends': trends,
            'latest': history[sorted_dates[-1]] if sorted_dates else {}
        }
        
    except Exception as e:
        print(f"[Lebanon Trends] Error: {str(e)}")
        return {
            'success': False,
            'message': str(e),
            'days_collected': 0
        }
        
# ========================================
# AIRLINE DISRUPTIONS TRACKER
# ========================================

def fetch_airline_disruptions():
    """
    Fetch airline disruptions from Google News RSS
    Searches for flight cancellations to Middle East destinations
    
    Returns list of disruption objects with:
    - airline, route, origin, destination, date, duration, status, source_url, headline
    """
    
    print("[RSS Monitor - Airline Disruptions] Starting scan...")
    
    # Middle East destinations to monitor
    destinations = [
        # High priority (conflict zones)
        'Tel Aviv', 'Israel', 'Beirut', 'Lebanon', 'Damascus', 'Syria',
        'Tehran', 'Iran', 'Baghdad', 'Iraq',
        # Regional capitals
        'Amman', 'Jordan', 'Dubai', 'UAE', 'Riyadh', 'Saudi Arabia',
        'Cairo', 'Egypt', 'Istanbul', 'Turkey', 'Doha', 'Qatar'
    ]
    
    keywords = [
        'airline suspended flights',
        'airline cancelled flights',
        'flight cancellation',
        'suspend service',
        'cancel flights'
    ]
    
    all_disruptions = []
    seen_urls = set()
    
    # Search each destination (limit to prevent slowness)
    for destination in destinations[:8]:  # Top 8 destinations
        for keyword in keywords[:2]:  # Top 2 keywords per destination
            query = f'{keyword} {destination}'
            
            try:
                # Google News RSS endpoint
                url = f"https://news.google.com/rss/search?q={query.replace(' ', '+')}&hl=en&gl=US&ceid=US:en"
                
                response = requests.get(url, timeout=10, headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                })
                
                if response.status_code != 200:
                    continue
                
                # Parse RSS XML
                import xml.etree.ElementTree as ET
                
                try:
                    root = ET.fromstring(response.content)
                except ET.ParseError:
                    continue
                
                items = root.findall('.//item')
                
                # Process top 3 results per query
                for item in items[:3]:
                    title_elem = item.find('title')
                    link_elem = item.find('link')
                    pubDate_elem = item.find('pubDate')
                    
                    if title_elem is None or link_elem is None:
                        continue
                    
                    title = title_elem.text or ''
                    link = link_elem.text or ''
                    pub_date = pubDate_elem.text if pubDate_elem is not None else ''
                    
                    # Skip duplicates
                    if link in seen_urls:
                        continue
                    
                    seen_urls.add(link)
                    
                    # Parse disruption details
                    disruption = {
                        'airline': extract_airline_from_title(title),
                        'route': f"Various → {destination}",
                        'origin': 'Various',
                        'destination': destination,
                        'date': parse_rss_date(pub_date),
                        'duration': extract_duration_from_title(title),
                        'status': extract_status_from_title(title),
                        'source_url': link,
                        'headline': title[:150]  # Truncate long headlines
                    }
                    
                    all_disruptions.append(disruption)
                
            except Exception as e:
                print(f"[RSS Monitor] Error fetching {destination}: {str(e)[:100]}")
                continue
    
    print(f"[RSS Monitor - Airline Disruptions] ✅ Found {len(all_disruptions)} disruptions")
    return all_disruptions


def extract_airline_from_title(title):
    """Extract airline name from news headline"""
    
    # Comprehensive airline list (major carriers + Middle East carriers)
    airlines = [
        # Star Alliance
        'Lufthansa', 'United Airlines', 'United', 'Air Canada', 'Turkish Airlines',
        'Swiss', 'SWISS', 'Austrian Airlines', 'Austrian', 'Singapore Airlines',
        
        # SkyTeam
        'Air France', 'KLM', 'Delta', 'Delta Airlines', 'Korean Air',
        
        # Oneworld
        'British Airways', 'American Airlines', 'American', 'Cathay Pacific',
        'Qantas', 'Qatar Airways', 'Iberia',
        
        # Middle East carriers
        'Emirates', 'Etihad', 'flydubai', 'Air Arabia', 'Saudia',
        'Gulf Air', 'Kuwait Airways', 'Royal Jordanian', 'Oman Air',
        'Middle East Airlines', 'MEA',
        
        # Israeli carriers
        'El Al', 'Arkia', 'Israir',
        
        # Low-cost carriers
        'Wizz Air', 'Ryanair', 'EasyJet', 'Pegasus Airlines',
        
        # Other major carriers
        'Air New Zealand', 'Ethiopian Airlines', 'EgyptAir', 'Egypt Air'
    ]
    
    title_lower = title.lower()
    
    # Check each airline
    for airline in airlines:
        if airline.lower() in title_lower:
            return airline
    
    # Try to extract from sentence structure
    # Pattern: "[Airline] suspends/cancels/resumes"
    import re
    pattern = re.search(r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+(?:suspend|cancel|halt|resume)', title, re.IGNORECASE)
    if pattern:
        potential = pattern.group(1)
        if len(potential) > 3 and potential not in ['United States', 'Middle East']:
            return potential
    
    return "Unknown Airline"


def extract_status_from_title(title):
    """Extract flight status (Suspended/Cancelled/Resumed)"""
    
    title_lower = title.lower()
    
    if 'resume' in title_lower or 'restart' in title_lower or 'return' in title_lower:
        return 'Resumed'
    elif 'cancel' in title_lower:
        return 'Cancelled'
    elif 'suspend' in title_lower or 'halt' in title_lower or 'stop' in title_lower:
        return 'Suspended'
    else:
        return 'Disrupted'


def extract_duration_from_title(title):
    """Extract duration (Until March, For 3 weeks, Indefinite, etc.)"""
    
    import re
    
    # Look for "until [date]"
    until_match = re.search(r'until\s+([A-Za-z]+\s+\d{1,2}(?:,?\s+\d{4})?)', title, re.IGNORECASE)
    if until_match:
        return f"Until {until_match.group(1)}"
    
    # Look for specific months
    months = ['January', 'February', 'March', 'April', 'May', 'June',
              'July', 'August', 'September', 'October', 'November', 'December']
    for month in months:
        if month.lower() in title.lower():
            year_match = re.search(r'\b(202[4-9])\b', title)
            if year_match:
                return f"Until {month} {year_match.group(1)}"
            else:
                return f"Until {month}"
    
    # Look for "for X days/weeks/months"
    for_match = re.search(r'for\s+(\d+)\s+(day|week|month)s?', title, re.IGNORECASE)
    if for_match:
        num = for_match.group(1)
        unit = for_match.group(2)
        return f"For {num} {unit}{'s' if int(num) > 1 else ''}"
    
    # Look for "indefinite"
    if 'indefinite' in title.lower():
        return 'Indefinite'
    
    return 'Unknown'


def parse_rss_date(pub_date):
    """Parse RSS pub date to ISO format"""
    
    try:
        if pub_date:
            # RSS uses RFC 2822 format
            from email.utils import parsedate_to_datetime
            date_obj = parsedate_to_datetime(pub_date)
            return date_obj.isoformat()
    except:
        pass
    
    # Fallback to current time
    return datetime.now(timezone.utc).isoformat()
    
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
        
        # NEW: Fetch ALL RSS feeds (leadership rhetoric + Israeli news)
        print(f"[RSS] Fetching RSS feeds...")
        rss_articles = fetch_all_rss()  # Includes: MEMRI, Al-Manar, Iran Wire, Ynet, ToI, JPost, i24NEWS, Haaretz
        
        # Add leadership detection to all articles
        print(f"[RSS] Analyzing {len(all_articles) + len(rss_articles)} articles for leadership quotes...")
        for article in all_articles + rss_articles:
            article['leadership'] = enhance_article_with_leadership(article)
        
        # Merge RSS articles into main pool
        all_articles.extend(rss_articles)
        print(f"[RSS] Total articles with RSS feeds: {len(all_articles)}")
        
        # Calculate Israel strike probability (main algorithm)
        scoring_result = calculate_threat_probability(all_articles, days, target)
        probability = scoring_result['probability']
        momentum = scoring_result['momentum']
        breakdown = scoring_result['breakdown']
        
        # Calculate US strike probability
        us_result = calculate_us_strike_probability(all_articles, days, target)
        us_prob = us_result['probability']
        
        # Calculate reverse threats for headlines
        israel_prob = probability / 100.0
        reverse_israel = calculate_reverse_threat(all_articles, target, 'israel', israel_prob, us_prob)
        reverse_us = calculate_reverse_threat(all_articles, target, 'us', israel_prob, us_prob)
        
        # Build recent headlines
        recent_headlines = build_recent_headlines(
            scoring_result.get('top_contributors', []),
            us_result.get('us_indicators', []),
            reverse_israel.get('indicators', []),
            reverse_us.get('indicators', []),
            all_articles
        )
        
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
                article_data = {
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
                }
                
                # NEW: Add leadership data if present
                if 'leadership' in matching_article and matching_article['leadership']['has_leadership']:
                    leadership = matching_article['leadership']
                    article_data['leadership'] = {
                        'leader': leadership['leader_name'],
                        'context': leadership['context'],
                        'threat_level': leadership['threat_level'],
                        'weight_multiplier': leadership['weight_multiplier']
                    }
                
                top_articles.append(article_data)
        
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
            'recent_headlines': recent_headlines,  # ← NEW: Added headlines
            'escalation_keywords': ESCALATION_KEYWORDS,
            'target_keywords': TARGET_KEYWORDS[target]['keywords'],
            'cached': False,
            'version': '2.8.0'
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'probability': 0
        }), 500

@app.route('/api/threat-matrix/<target>', methods=['GET'])
def api_threat_matrix(target):
    """
    Enhanced threat matrix with multi-directional probabilities
    Returns: Israel strike, US strike, reverse threats, combined probability
    """
    try:
        days = int(request.args.get('days', 7))
        
        if not check_rate_limit():
            return jsonify({
                'success': False,
                'error': 'Rate limit reached',
                'rate_limited': True
            }), 200
        
        if target not in TARGET_KEYWORDS:
            return jsonify({
                'success': False,
                'error': f"Invalid target: {target}"
            }), 400
        
        # Fetch articles (same as existing endpoint)
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
        
        # Calculate Israel strike probability (existing algorithm)
        israel_result = calculate_threat_probability(all_articles, days, target)
        israel_prob = israel_result['probability'] / 100.0
        
        # Calculate US strike probability (new algorithm)
        us_result = calculate_us_strike_probability(all_articles, days, target)
        us_prob = us_result['probability']
        
        # Detect coordination between US and Israel
        coordination = detect_coordination_signals(israel_prob, us_prob, all_articles)
        
        # Calculate combined probability
        combined_result = calculate_combined_probability(israel_prob, us_prob, coordination)
        
        # Calculate reverse threats (target → Israel, target → US)
        # Pass Israel/US probabilities for retaliation trigger bonus
        reverse_israel = calculate_reverse_threat(all_articles, target, 'israel', israel_prob, us_prob)
        reverse_us = calculate_reverse_threat(all_articles, target, 'us', israel_prob, us_prob)

        # Build response
        response = {
            'success': True,
            'target': target,
            'target_flag': {
                'iran': '🇮🇷',
                'hezbollah': '🇱🇧',
                'houthis': '🇾🇪',
                'syria': '🇸🇾',
                'jordan': '🇯🇴'
            }.get(target, '🏴'),
            'days_analyzed': days,
            'total_articles': len(all_articles),
            
            # Combined probability (headline number)
            'combined_probability': {
                'value': round(combined_result['combined'] * 100, 1),
                'base': round(combined_result['base_independent'] * 100, 1),
                'coordination_bonus': round(combined_result['coordination_bonus'], 1),
                'coordination_level': combined_result['coordination_level'],
                'risk_level': (
                    'very_high' if combined_result['combined'] > 0.70 else
                    'high' if combined_result['combined'] > 0.50 else
                    'moderate' if combined_result['combined'] > 0.30 else
                    'low'
                )
            },
            
            # Incoming threats
            'incoming_threats': {
                'israel': {
                    'probability': round(israel_prob * 100, 1),
                    'risk_level': (
                        'very_high' if israel_prob > 0.70 else
                        'high' if israel_prob > 0.50 else
                        'moderate' if israel_prob > 0.30 else
                        'low'
                    ),
                    'flag': '🇮🇱',
                    'indicators': israel_result.get('breakdown', {}).get('top_articles', [])[:3]
                },
                'us': {
                    'probability': round(us_prob * 100, 1),
                    'risk_level': (
                        'very_high' if us_prob > 0.70 else
                        'high' if us_prob > 0.50 else
                        'moderate' if us_prob > 0.30 else
                        'low'
                    ),
                    'flag': '🇺🇸',
                    'indicators': us_result.get('us_indicators', [])[:3],
                    'adjustment': round(us_result.get('us_adjustment', 0) * 100, 1)
                }
            },
            
            # Outgoing threats
            'outgoing_threats': {
                'vs_israel': {
                    'probability': round(reverse_israel['probability'] * 100, 1),
                    'risk_level': reverse_israel['risk_level'],
                    'target_flag': '🇮🇱',
                    'indicators': reverse_israel.get('indicators', [])[:3]
                },
                'vs_us': {
                    'probability': round(reverse_us['probability'] * 100, 1),
                    'risk_level': reverse_us['risk_level'],
                    'target_flag': '🇺🇸',
                    'indicators': reverse_us.get('indicators', [])[:3]
                }
            },
            
            # Coordination details
            'coordination': {
                'level': coordination['level'],
                'factor': coordination['factor'],
                'signals_detected': coordination['signals_detected'],
                'indicators': coordination.get('indicators', [])
            },
            
            # ========================================
            # TOP ARTICLES FOR "RECENT HEADLINES" DISPLAY
            # ========================================
            'recent_headlines': build_recent_headlines(
                israel_result.get('top_contributors', []),
                us_result.get('us_indicators', []),
                reverse_israel.get('indicators', []),
                reverse_us.get('indicators', []),
                all_articles
            ),
            
            'version': '2.8.0-multi-actor'
        }
        
        return jsonify(response)
        
    except Exception as e:
        print(f"[Threat Matrix] Error: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

# ========================================
# INDIVIDUAL COUNTRY ENDPOINTS WITH CACHING
# ========================================

@app.route('/api/iran-strike-probability', methods=['GET'])
def api_iran_strike_probability():
    """
    Iran Strike Probability Endpoint
    Returns cached data by default, only scans when refresh=true
    """
    try:
        # Check if user requested a fresh scan
        refresh = request.args.get('refresh', 'false').lower() == 'true'
        days = int(request.args.get('days', 7))
        
        # If not refreshing, try to return cached data
        if not refresh:
            cached = get_cached_result('iran')
            if cached and is_cache_fresh(cached, max_age_hours=6):
                print("[Iran] Returning cached data")
                return jsonify(cached)
        
        # User requested refresh OR cache is stale
        print("[Iran] Performing fresh scan...")
        
        if not check_rate_limit():
            # If rate limited, return stale cache if available
            cached = get_cached_result('iran')
            if cached:
                print("[Iran] Rate limited, returning stale cache")
                cached['stale_cache'] = True
                return jsonify(cached)
            
            return jsonify({
                'success': False,
                'error': 'Rate limit exceeded',
                'probability': 0,
                'rate_limited': True
            }), 429
        
        # Fetch fresh data
        query = ' OR '.join(TARGET_KEYWORDS['iran']['keywords'])
        
        articles_en = fetch_newsapi_articles(query, days)
        articles_gdelt_en = fetch_gdelt_articles(query, days, 'eng')
        articles_gdelt_ar = fetch_gdelt_articles(query, days, 'ara')
        articles_gdelt_he = fetch_gdelt_articles(query, days, 'heb')
        articles_gdelt_fa = fetch_gdelt_articles(query, days, 'fas')
        
        articles_reddit = fetch_reddit_posts(
            'iran',
            TARGET_KEYWORDS['iran']['reddit_keywords'],
            days
        )
        
        all_articles = (articles_en + articles_gdelt_en + articles_gdelt_ar + 
                       articles_gdelt_he + articles_gdelt_fa + articles_reddit)
        
        # Calculate probability
        scoring_result = calculate_threat_probability(all_articles, days, 'iran')
        probability = scoring_result['probability']
        momentum = scoring_result['momentum']
        
        # Timeline
        if probability < 30:
            timeline = "180+ Days"
        elif probability < 50:
            timeline = "91-180 Days"
        elif probability < 70:
            timeline = "31-90 Days"
        else:
            timeline = "0-30 Days"
        
        # Confidence
        unique_sources = len(set(a.get('source', {}).get('name', 'Unknown') for a in all_articles))
        if len(all_articles) >= 20 and unique_sources >= 8:
            confidence = "High"
        elif len(all_articles) >= 10 and unique_sources >= 5:
            confidence = "Medium"
        else:
            confidence = "Low"
        
        # Calculate US strike + reverse threats for headlines
        israel_prob = probability / 100.0
        us_result = calculate_us_strike_probability(all_articles, days, 'iran')
        us_prob = us_result['probability']
        
        reverse_israel = calculate_reverse_threat(all_articles, 'iran', 'israel', israel_prob, us_prob)
        reverse_us = calculate_reverse_threat(all_articles, 'iran', 'us', israel_prob, us_prob)
        
        # Build recent headlines
        recent_headlines = build_recent_headlines(
            scoring_result.get('top_contributors', []),
            us_result.get('us_indicators', []),
            reverse_israel.get('indicators', []),
            reverse_us.get('indicators', []),
            all_articles
        )
        
        # Build response
        result = {
            'success': True,
            'probability': probability,
            'timeline': timeline,
            'confidence': confidence,
            'momentum': momentum,
            'total_articles': len(all_articles),
            'unique_sources': unique_sources,
            'recent_articles_48h': scoring_result['breakdown']['recent_articles_48h'],
            'top_scoring_articles': scoring_result.get('top_scoring_articles', []),
            'recent_headlines': recent_headlines,
            'last_updated': datetime.now(timezone.utc).isoformat(),
            'cached': False,
            'version': '2.8.0'
        }
        
        # Update cache
        update_cache('iran', result)
        
        print(f"[Iran] Fresh scan complete: {probability}%")
        return jsonify(result)
        
    except Exception as e:
        print(f"Error in /api/iran-strike-probability: {e}")
        import traceback
        traceback.print_exc()
        
        # Try to return cached data on error
        cached = get_cached_result('iran')
        if cached:
            print("[Iran] Error occurred, returning cached data")
            cached['error_fallback'] = True
            return jsonify(cached)
        
        return jsonify({
            'success': False,
            'error': str(e),
            'probability': 0,
            'timeline': 'Unknown',
            'confidence': 'Low'
        }), 500


@app.route('/api/hezbollah-activity', methods=['GET'])
def api_hezbollah_activity():
    """
    Hezbollah Strike Probability Endpoint
    Returns cached data by default, only scans when refresh=true
    """
    try:
        refresh = request.args.get('refresh', 'false').lower() == 'true'
        days = int(request.args.get('days', 7))
        
        # Try cached data first
        if not refresh:
            cached = get_cached_result('hezbollah')
            if cached and is_cache_fresh(cached, max_age_hours=6):
                print("[Hezbollah] Returning cached data")
                return jsonify(cached)
        
        print("[Hezbollah] Performing fresh scan...")
        
        if not check_rate_limit():
            cached = get_cached_result('hezbollah')
            if cached:
                print("[Hezbollah] Rate limited, returning stale cache")
                cached['stale_cache'] = True
                return jsonify(cached)
            
            return jsonify({
                'success': False,
                'error': 'Rate limit exceeded',
                'probability': 0,
                'rate_limited': True
            }), 429
        
        # Fetch fresh data
        query = ' OR '.join(TARGET_KEYWORDS['hezbollah']['keywords'])
        
        articles_en = fetch_newsapi_articles(query, days)
        articles_gdelt_en = fetch_gdelt_articles(query, days, 'eng')
        articles_gdelt_ar = fetch_gdelt_articles(query, days, 'ara')
        articles_gdelt_he = fetch_gdelt_articles(query, days, 'heb')
        
        articles_reddit = fetch_reddit_posts(
            'hezbollah',
            TARGET_KEYWORDS['hezbollah']['reddit_keywords'],
            days
        )
        
        all_articles = (articles_en + articles_gdelt_en + articles_gdelt_ar + 
                       articles_gdelt_he + articles_reddit)
        
        # Calculate strike probability
        scoring_result = calculate_threat_probability(all_articles, days, 'hezbollah')
        probability = scoring_result['probability']
        momentum = scoring_result['momentum']
        
        # Activity level as secondary metric
        activity_level = min(100, (len(all_articles) * 2) + scoring_result['breakdown']['recent_articles_48h'] * 3)
        
        if activity_level >= 75:
            activity_desc = "Very High"
        elif activity_level >= 50:
            activity_desc = "High"
        elif activity_level >= 25:
            activity_desc = "Moderate"
        else:
            activity_desc = "Low"
        
        # Calculate US strike + reverse threats for headlines
        israel_prob = probability / 100.0
        us_result = calculate_us_strike_probability(all_articles, days, 'hezbollah')
        us_prob = us_result['probability']
        
        reverse_israel = calculate_reverse_threat(all_articles, 'hezbollah', 'israel', israel_prob, us_prob)
        reverse_us = calculate_reverse_threat(all_articles, 'hezbollah', 'us', israel_prob, us_prob)
        
        # Build recent headlines
        recent_headlines = build_recent_headlines(
            scoring_result.get('top_contributors', []),
            us_result.get('us_indicators', []),
            reverse_israel.get('indicators', []),
            reverse_us.get('indicators', []),
            all_articles
        )
        
        result = {
            'success': True,
            'probability': probability,
            'activity_level': int(activity_level),
            'activity_description': activity_desc,
            'momentum': momentum,
            'total_articles': len(all_articles),
            'recent_articles_48h': scoring_result['breakdown']['recent_articles_48h'],
            'top_scoring_articles': scoring_result.get('top_scoring_articles', []),
            'recent_headlines': recent_headlines,
            'last_updated': datetime.now(timezone.utc).isoformat(),
            'cached': False,
            'version': '2.8.0'
        }
        
        update_cache('hezbollah', result)
        
        print(f"[Hezbollah] Fresh scan complete: {probability}%")
        return jsonify(result)
        
    except Exception as e:
        print(f"Error in /api/hezbollah-activity: {e}")
        import traceback
        traceback.print_exc()
        
        cached = get_cached_result('hezbollah')
        if cached:
            print("[Hezbollah] Error occurred, returning cached data")
            cached['error_fallback'] = True
            return jsonify(cached)
        
        return jsonify({
            'success': False,
            'error': str(e),
            'probability': 0,
            'activity_description': 'Unknown'
        }), 500


@app.route('/api/houthis-threat', methods=['GET'])
def api_houthis_threat():
    """
    Houthis Strike Probability Endpoint
    Returns cached data by default, only scans when refresh=true
    """
    try:
        refresh = request.args.get('refresh', 'false').lower() == 'true'
        days = int(request.args.get('days', 7))
        
        # Try cached data first
        if not refresh:
            cached = get_cached_result('houthis')
            if cached and is_cache_fresh(cached, max_age_hours=6):
                print("[Houthis] Returning cached data")
                return jsonify(cached)
        
        print("[Houthis] Performing fresh scan...")
        
        if not check_rate_limit():
            cached = get_cached_result('houthis')
            if cached:
                print("[Houthis] Rate limited, returning stale cache")
                cached['stale_cache'] = True
                return jsonify(cached)
            
            return jsonify({
                'success': False,
                'error': 'Rate limit exceeded',
                'probability': 0,
                'rate_limited': True
            }), 429
        
        # Fetch fresh data
        query = ' OR '.join(TARGET_KEYWORDS['houthis']['keywords'])
        
        articles_en = fetch_newsapi_articles(query, days)
        articles_gdelt_en = fetch_gdelt_articles(query, days, 'eng')
        articles_gdelt_ar = fetch_gdelt_articles(query, days, 'ara')
        
        articles_reddit = fetch_reddit_posts(
            'houthis',
            TARGET_KEYWORDS['houthis']['reddit_keywords'],
            days
        )
        
        all_articles = (articles_en + articles_gdelt_en + articles_gdelt_ar + articles_reddit)
        
        # Calculate strike probability
        scoring_result = calculate_threat_probability(all_articles, days, 'houthis')
        probability = scoring_result['probability']
        momentum = scoring_result['momentum']
        
        # Shipping incidents as secondary metric
        shipping_incidents = 0
        for article in all_articles:
            text = f"{article.get('title', '')} {article.get('description', '')}".lower()
            if any(word in text for word in ['shipping', 'red sea', 'attacked', 'strike', 'missile', 'drone']):
                shipping_incidents += 1
        
        # Threat description based on probability
        if probability >= 75:
            threat_desc = "Critical"
        elif probability >= 50:
            threat_desc = "High"
        elif probability >= 25:
            threat_desc = "Moderate"
        else:
            threat_desc = "Low"
        
        # Calculate US strike + reverse threats for headlines
        israel_prob = probability / 100.0
        us_result = calculate_us_strike_probability(all_articles, days, 'houthis')
        us_prob = us_result['probability']
        
        reverse_israel = calculate_reverse_threat(all_articles, 'houthis', 'israel', israel_prob, us_prob)
        reverse_us = calculate_reverse_threat(all_articles, 'houthis', 'us', israel_prob, us_prob)
        
        # Build recent headlines
        recent_headlines = build_recent_headlines(
            scoring_result.get('top_contributors', []),
            us_result.get('us_indicators', []),
            reverse_israel.get('indicators', []),
            reverse_us.get('indicators', []),
            all_articles
        )
        
        result = {
            'success': True,
            'probability': probability,
            'threat_description': threat_desc,
            'momentum': momentum,
            'shipping_incidents': shipping_incidents,
            'total_articles': len(all_articles),
            'recent_articles_48h': scoring_result['breakdown']['recent_articles_48h'],
            'top_scoring_articles': scoring_result.get('top_scoring_articles', []),
            'recent_headlines': recent_headlines,
            'last_updated': datetime.now(timezone.utc).isoformat(),
            'cached': False,
            'version': '2.8.0'
        }
        
        update_cache('houthis', result)
        
        print(f"[Houthis] Fresh scan complete: {probability}%")
        return jsonify(result)
        
    except Exception as e:
        print(f"Error in /api/houthis-threat: {e}")
        import traceback
        traceback.print_exc()
        
        cached = get_cached_result('houthis')
        if cached:
            print("[Houthis] Error occurred, returning cached data")
            cached['error_fallback'] = True
            return jsonify(cached)
        
        return jsonify({
            'success': False,
            'error': str(e),
            'probability': 0,
            'threat_description': 'Unknown'
        }), 500


@app.route('/api/syria-conflict', methods=['GET'])
def api_syria_conflict():
    """
    Syria Strike Probability Endpoint
    Returns cached data by default, only scans when refresh=true
    """
    try:
        refresh = request.args.get('refresh', 'false').lower() == 'true'
        days = int(request.args.get('days', 7))
        
        # Try cached data first
        if not refresh:
            cached = get_cached_result('syria')
            if cached and is_cache_fresh(cached, max_age_hours=6):
                print("[Syria] Returning cached data")
                return jsonify(cached)
        
        print("[Syria] Performing fresh scan...")
        
        if not check_rate_limit():
            cached = get_cached_result('syria')
            if cached:
                print("[Syria] Rate limited, returning stale cache")
                cached['stale_cache'] = True
                return jsonify(cached)
            
            return jsonify({
                'success': False,
                'error': 'Rate limit exceeded',
                'probability': 0,
                'rate_limited': True
            }), 429
        
        # Fetch fresh data
        syria_keywords = ['syria', 'syrian', 'damascus', 'assad', 'aleppo', 'idlib']
        query = ' OR '.join(syria_keywords)
        
        articles_en = fetch_newsapi_articles(query, days)
        articles_gdelt_en = fetch_gdelt_articles(query, days, 'eng')
        articles_gdelt_ar = fetch_gdelt_articles(query, days, 'ara')
        
        articles_reddit = fetch_reddit_posts(
            'syria',
            ['Syria', 'Assad', 'Damascus', 'conflict', 'strike'],
            days
        )
        
        all_articles = (articles_en + articles_gdelt_en + articles_gdelt_ar + articles_reddit)
        
        # Calculate strike probability
        scoring_result = calculate_threat_probability(all_articles, days, 'syria')
        probability = scoring_result['probability']
        momentum = scoring_result['momentum']
        
        # Calculate conflict intensity as secondary metric
        intensity_score = 0
        escalation_articles = 0
        
        for article in all_articles:
            text = f"{article.get('title', '')} {article.get('description', '')}".lower()
            
            if any(word in text for word in ['strike', 'attack', 'bombing', 'airstrike', 'killed', 'casualties']):
                escalation_articles += 1
                intensity_score += 3
            
            try:
                pub_date = article.get('publishedAt', '')
                if pub_date:
                    pub_dt = datetime.fromisoformat(pub_date.replace('Z', '+00:00'))
                    age_hours = (datetime.now(timezone.utc) - pub_dt).total_seconds() / 3600
                    if age_hours <= 48:
                        intensity_score += 2
            except:
                pass
        
        intensity = min(100, int(intensity_score / max(len(all_articles), 1) * 10))
        
        if intensity >= 75:
            intensity_desc = "Very High"
        elif intensity >= 50:
            intensity_desc = "High"
        elif intensity >= 25:
            intensity_desc = "Moderate"
        else:
            intensity_desc = "Low"
        
        # Calculate US strike + reverse threats for headlines
        israel_prob = probability / 100.0
        us_result = calculate_us_strike_probability(all_articles, days, 'syria')
        us_prob = us_result['probability']
        
        reverse_israel = calculate_reverse_threat(all_articles, 'syria', 'israel', israel_prob, us_prob)
        reverse_us = calculate_reverse_threat(all_articles, 'syria', 'us', israel_prob, us_prob)
        
        # Build recent headlines
        recent_headlines = build_recent_headlines(
            scoring_result.get('top_contributors', []),
            us_result.get('us_indicators', []),
            reverse_israel.get('indicators', []),
            reverse_us.get('indicators', []),
            all_articles
        )
        
        result = {
            'success': True,
            'probability': probability,
            'intensity': intensity,
            'intensity_description': intensity_desc,
            'momentum': momentum,
            'total_articles': len(all_articles),
            'escalation_articles': escalation_articles,
            'top_scoring_articles': scoring_result.get('top_scoring_articles', []),
            'recent_headlines': recent_headlines,
            'last_updated': datetime.now(timezone.utc).isoformat(),
            'cached': False,
            'version': '2.8.0'
        }
        
        update_cache('syria', result)
        
        print(f"[Syria] Fresh scan complete: {probability}%")
        return jsonify(result)
        
    except Exception as e:
        print(f"Error in /api/syria-conflict: {e}")
        import traceback
        traceback.print_exc()
        
        cached = get_cached_result('syria')
        if cached:
            print("[Syria] Error occurred, returning cached data")
            cached['error_fallback'] = True
            return jsonify(cached)
        
        return jsonify({
            'success': False,
            'error': str(e),
            'probability': 0,
            'intensity_description': 'Unknown'
        }), 500

# ========================================
# JORDAN THREAT ENDPOINT
# ========================================

@app.route('/api/jordan-threat', methods=['GET'])
def api_jordan_threat():
    """
    Jordan Kinetic Activity Probability Endpoint
    
    Unique threat model:
    - Incoming: Iran/militia, Syria/ISIS, Palestinian unrest, US base targeting
    - Defensive: Coalition air defense activation, border operations
    - Cross-references Iran-Israel tension for air defense boost
    """
    try:
        refresh = request.args.get('refresh', 'false').lower() == 'true'
        days = int(request.args.get('days', 7))
        
        # Try cached data first
        if not refresh:
            cached = get_cached_result('jordan')
            if cached and is_cache_fresh(cached, max_age_hours=6):
                print("[Jordan] Returning cached data")
                return jsonify(cached)
        
        print("[Jordan] Performing fresh scan...")
        
        if not check_rate_limit():
            cached = get_cached_result('jordan')
            if cached:
                cached['stale_cache'] = True
                return jsonify(cached)
            return jsonify({
                'success': False, 'error': 'Rate limit exceeded',
                'probability': 0, 'rate_limited': True
            }), 429
        
        # Fetch articles from all sources
        query = ' OR '.join(TARGET_KEYWORDS['jordan']['keywords'])
        
        articles_en = fetch_newsapi_articles(query, days)
        articles_gdelt_en = fetch_gdelt_articles(query, days, 'eng')
        articles_gdelt_ar = fetch_gdelt_articles(query, days, 'ara')
        articles_gdelt_he = fetch_gdelt_articles(query, days, 'heb')
        articles_gdelt_fa = fetch_gdelt_articles(query, days, 'fas')
        
        articles_reddit = fetch_reddit_posts(
            'jordan',
            TARGET_KEYWORDS['jordan']['reddit_keywords'],
            days
        )
        
        # Also fetch Jordanian news RSS feeds
        jordan_rss = fetch_jordan_news_rss()
        
        all_articles = (articles_en + articles_gdelt_en + articles_gdelt_ar +
                       articles_gdelt_he + articles_gdelt_fa + articles_reddit +
                       jordan_rss)
        
        print(f"[Jordan] Total articles: {len(all_articles)}")
        
        # Calculate base strike probability using standard algorithm
        scoring_result = calculate_threat_probability(all_articles, days, 'jordan')
        probability = scoring_result['probability']
        momentum = scoring_result['momentum']
        breakdown = scoring_result['breakdown']
        
        # Calculate Jordan-specific incoming threats
        incoming = calculate_jordan_incoming_threats(all_articles, days)
        
        # Get Iran-Israel tension level for air defense boost
        iran_cache = get_cached_result('iran')
        iran_israel_tension = 0.0
        if iran_cache:
            iran_israel_tension = iran_cache.get('probability', 0) / 100.0
        
        # Calculate defensive posture
        defensive = calculate_jordan_defensive_posture(all_articles, iran_israel_tension)
        
        # Build headlines
        recent_headlines = build_jordan_headlines(incoming, defensive, all_articles)
        
        # Timeline
        if probability < 30:
            timeline = "180+ Days"
        elif probability < 50:
            timeline = "91-180 Days"
        elif probability < 70:
            timeline = "31-90 Days"
        else:
            timeline = "0-30 Days"
        
        # Confidence
        unique_sources = len(set(a.get('source', {}).get('name', 'Unknown') for a in all_articles))
        if len(all_articles) >= 20 and unique_sources >= 8:
            confidence = "High"
        elif len(all_articles) >= 10 and unique_sources >= 5:
            confidence = "Medium"
        else:
            confidence = "Low"
        
        result = {
            'success': True,
            'probability': probability,
            'timeline': timeline,
            'confidence': confidence,
            'momentum': momentum,
            'total_articles': len(all_articles),
            'unique_sources': unique_sources,
            'recent_articles_48h': breakdown['recent_articles_48h'],
            'scoring_breakdown': breakdown,
            'incoming_threats': {
                'iran_militia': {
                    'probability': round(incoming['iran_militia']['probability'] * 100, 1),
                    'risk_level': incoming['iran_militia']['risk_level'],
                    'indicators': incoming['iran_militia']['indicators'][:3]
                },
                'syria_isis': {
                    'probability': round(incoming['syria_isis']['probability'] * 100, 1),
                    'risk_level': incoming['syria_isis']['risk_level'],
                    'indicators': incoming['syria_isis']['indicators'][:3]
                },
                'palestinian_unrest': {
                    'probability': round(incoming['palestinian_unrest']['probability'] * 100, 1),
                    'risk_level': incoming['palestinian_unrest']['risk_level'],
                    'indicators': incoming['palestinian_unrest']['indicators'][:3]
                },
                'us_base': {
                    'probability': round(incoming['us_base']['probability'] * 100, 1),
                    'risk_level': incoming['us_base']['risk_level'],
                    'indicators': incoming['us_base']['indicators'][:3]
                },
                'combined': {
                    'probability': round(incoming['combined']['probability'] * 100, 1),
                    'risk_level': incoming['combined']['risk_level']
                }
            },
            'defensive_posture': {
                'coalition_air_defense': {
                    'probability': round(defensive['coalition_air_defense']['probability'] * 100, 1),
                    'base_probability': round(defensive['coalition_air_defense']['base_probability'] * 100, 1),
                    'iran_israel_tension_boost': round(defensive['coalition_air_defense']['iran_israel_tension_boost'] * 100, 1),
                    'risk_level': defensive['coalition_air_defense']['risk_level'],
                    'tooltip': defensive['coalition_air_defense']['tooltip'],
                    'indicators': defensive['coalition_air_defense']['indicators'][:3]
                },
                'border_operations': {
                    'probability': round(defensive['border_operations']['probability'] * 100, 1),
                    'risk_level': defensive['border_operations']['risk_level'],
                    'tooltip': defensive['border_operations']['tooltip'],
                    'indicators': defensive['border_operations']['indicators'][:3]
                }
            },
            'recent_headlines': recent_headlines,
            'last_updated': datetime.now(timezone.utc).isoformat(),
            'cached': False,
            'version': '2.9.0-jordan'
        }
        
        update_cache('jordan', result)
        
        print(f"[Jordan] Fresh scan complete: {probability}%")
        return jsonify(result)
        
    except Exception as e:
        print(f"Error in /api/jordan-threat: {e}")
        import traceback
        traceback.print_exc()
        
        cached = get_cached_result('jordan')
        if cached:
            cached['error_fallback'] = True
            return jsonify(cached)
        
        return jsonify({
            'success': False, 'error': str(e),
            'probability': 0, 'timeline': 'Unknown'
        }), 500


def fetch_jordan_news_rss():
    """Fetch articles from Jordanian news RSS feeds"""
    articles = []
    
    feeds = {
        'Jordan Times': 'https://www.jordantimes.com/feed',
        'Roya News': 'https://en.royanews.tv/feed',
    }
    
    for source_name, feed_url in feeds.items():
        try:
            response = requests.get(feed_url, timeout=15, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            })
            
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
                
                if title_elem is not None and link_elem is not None:
                    pub_date = pubDate_elem.text if pubDate_elem is not None else datetime.now(timezone.utc).isoformat()
                    description = description_elem.text[:500] if description_elem is not None and description_elem.text else ''
                    
                    articles.append({
                        'title': title_elem.text or '',
                        'description': description,
                        'url': link_elem.text or '',
                        'publishedAt': pub_date,
                        'source': {'name': source_name},
                        'content': description,
                        'language': 'en'
                    })
            
            print(f"[{source_name}] ✓ Fetched {len([a for a in articles if a['source']['name'] == source_name])} articles")
            
        except Exception as e:
            print(f"[{source_name}] Error: {str(e)[:100]}")
            continue
    
    return articles
    
# ========================================
# CACHE MANAGEMENT ENDPOINT (OPTIONAL)
# ========================================

@app.route('/api/cache/status', methods=['GET'])
def cache_status():
    """View current cache status"""
    cache = load_cache()
    
    status = {}
    for target, data in cache.items():
        if 'cached_at' in data:
            cached_at = datetime.fromisoformat(data['cached_at'])
            age_hours = (datetime.now(timezone.utc) - cached_at).total_seconds() / 3600
            
            status[target] = {
                'probability': data.get('probability', 0),
                'cached_at': data['cached_at'],
                'age_hours': round(age_hours, 1),
                'is_fresh': age_hours < 6
            }
    
    return jsonify({
        'success': True,
        'cache_file': CACHE_FILE,
        'targets': status,
        'version': '2.8.0'
    })


@app.route('/api/cache/clear', methods=['POST'])
def clear_cache():
    """Clear all cached data (admin only)"""
    try:
        save_cache({})
        return jsonify({
            'success': True,
            'message': 'Cache cleared'
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

def extract_hrana_structured_data(hrana_articles):
    """
    Extract structured casualty data from HRANA articles
    Looks for cumulative totals and verified numbers
    """
    result = {
        'is_hrana_verified': False,
        'confirmed_deaths': 0,
        'seriously_injured': 0,
        'total_arrests': 0,
        'cumulative_deaths': None,
        'cumulative_arrests': None,
        'cumulative_injuries': None
    }
    
    if not hrana_articles:
        return result
    
    for article in hrana_articles:
        title = (article.get('title') or '').lower()
        description = (article.get('description') or '').lower()
        content = (article.get('content') or '').lower()
        text = f"{title} {description} {content}"
        
        # Look for cumulative numbers in HRANA reports
        # HRANA often publishes running totals
        
        # Deaths
        death_patterns = [
            r'(\d+(?:,\d{3})*)\s+(?:people\s+)?(?:killed|dead|deaths)',
            r'(?:killed|death toll|deaths?)[\s:]+(\d+(?:,\d{3})*)',
            r'(\d+(?:,\d{3})*)\s+(?:protesters?\s+)?(?:killed|shot dead)'
        ]
        
        for pattern in death_patterns:
            match = re.search(pattern, text)
            if match:
                num = int(match.group(1).replace(',', ''))
                if num > result['confirmed_deaths']:
                    result['confirmed_deaths'] = num
                    result['is_hrana_verified'] = True
                # Check if this looks like a cumulative total (large number)
                if num > 100 and result['cumulative_deaths'] is None:
                    result['cumulative_deaths'] = num
        
        # Arrests
        arrest_patterns = [
            r'(\d+(?:,\d{3})*)\s+(?:people\s+)?(?:arrested|detained)',
            r'(?:arrested|detained|arrests?)[\s:]+(\d+(?:,\d{3})*)',
            r'(\d+(?:,\d{3})*)\s+(?:protesters?\s+)?(?:arrested|taken into custody)'
        ]
        
        for pattern in arrest_patterns:
            match = re.search(pattern, text)
            if match:
                num = int(match.group(1).replace(',', ''))
                if num > result['total_arrests']:
                    result['total_arrests'] = num
                    result['is_hrana_verified'] = True
                if num > 500 and result['cumulative_arrests'] is None:
                    result['cumulative_arrests'] = num
        
        # Injuries
        injury_patterns = [
            r'(\d+(?:,\d{3})*)\s+(?:people\s+)?(?:injured|wounded)',
            r'(?:injured|wounded|injuries?)[\s:]+(\d+(?:,\d{3})*)'
        ]
        
        for pattern in injury_patterns:
            match = re.search(pattern, text)
            if match:
                num = int(match.group(1).replace(',', ''))
                if num > result['seriously_injured']:
                    result['seriously_injured'] = num
                    result['is_hrana_verified'] = True
                if num > 100 and result['cumulative_injuries'] is None:
                    result['cumulative_injuries'] = num
    
    print(f"[HRANA Structured] Verified: {result['is_hrana_verified']}, Deaths: {result['confirmed_deaths']}, Arrests: {result['total_arrests']}, Injuries: {result['seriously_injured']}")
    
    return result

@app.route('/scan-iran-protests', methods=['GET'])
def scan_iran_protests():
    """Iran protests endpoint with regime stability calculation"""
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
        
        # Diagnostic logging for article sources
        print(f"[Iran Protests] Articles breakdown:")
        print(f"  NewsAPI: {len(newsapi_articles)}")
        print(f"  GDELT EN: {len(gdelt_en)}")
        print(f"  GDELT AR: {len(gdelt_ar)}")
        print(f"  GDELT FA: {len(gdelt_fa)}")
        print(f"  GDELT HE: {len(gdelt_he)}")
        print(f"  Reddit: {len(reddit_posts)}")
        print(f"  Iran Wire: {len(iranwire_articles)}")
        print(f"  HRANA: {len(hrana_articles)}")
        
        # NEW: Fetch ALL RSS feeds (includes Iran Wire which we already have, but adds MEMRI, Al-Manar, Israeli sources)
        print(f"[RSS] Fetching additional RSS feeds for Iran protests...")
        rss_articles = fetch_all_rss()
        
        # Add leadership detection to all articles
        print(f"[RSS] Analyzing {len(all_articles) + len(rss_articles)} articles for leadership quotes...")
        for article in all_articles + rss_articles:
            article['leadership'] = enhance_article_with_leadership(article)
        
        # Merge RSS articles into main pool
        all_articles.extend(rss_articles)
        print(f"[RSS] Total articles for Iran protests: {len(all_articles)}")
        
        # Extract HRANA structured data (includes cumulative if found)
        hrana_data = extract_hrana_structured_data(hrana_articles)
        casualties_regex = extract_casualty_data(all_articles)
        
        if hrana_data['is_hrana_verified']:
            casualties = {
                'deaths': max(hrana_data['confirmed_deaths'], casualties_regex['deaths']),
                'injuries': max(hrana_data['seriously_injured'], casualties_regex['injuries']),
                'arrests': max(hrana_data['total_arrests'], casualties_regex['arrests']),
                'sources': list(set(['HRANA'] + casualties_regex['sources'])),
                'details': casualties_regex['details'],
                'hrana_verified': True,
                # ← NEW: Pass cumulative data from HRANA articles
                'hrana_cumulative_deaths': hrana_data.get('cumulative_deaths'),
                'hrana_cumulative_arrests': hrana_data.get('cumulative_arrests'),
                'hrana_cumulative_injuries': hrana_data.get('cumulative_injuries')
            }
        else:
            casualties = casualties_regex
            casualties['hrana_verified'] = False
            # ← NEW: Set to None if HRANA didn't verify
            casualties['hrana_cumulative_deaths'] = None
            casualties['hrana_cumulative_arrests'] = None
            casualties['hrana_cumulative_injuries'] = None
        
        # Calculate enhanced casualties with HRANA extraction + cache fallback
        casualties_enhanced = calculate_casualty_trends(casualties)
        
        articles_per_day = len(all_articles) / days if days > 0 else 0
        # Reduced sensitivity: reflects current low activity period
        # 50 articles/week = ~7 per day = 3.5% base + deaths impact
        intensity_score = min(articles_per_day * 0.5 + casualties['deaths'] * 0.2, 100)
        stability_score = 100 - intensity_score
        
        exchange_data = fetch_iran_exchange_rate()
        oil_data = fetch_oil_price()
        
        protest_summary = {
            'intensity': int(intensity_score),
            'stability': int(stability_score),
            'casualties': casualties,
            'casualties_enhanced': casualties_enhanced,
            'days_analyzed': days,
            'total_articles': len(all_articles)
        }
        
        regime_stability = calculate_regime_stability(exchange_data, protest_summary, oil_data)
        
        # Extract cities from all articles
        cities = extract_iran_cities(all_articles)
        num_cities = len(cities)
        
        # If no cities found, show zero not hardcoded number
        if num_cities == 0:
            cities = []
            num_cities = 0
        
        return jsonify({
            'success': True,
            'days_analyzed': days,
            'total_articles': len(all_articles),
            'intensity': int(intensity_score),
            'stability': int(stability_score),
            'casualties': casualties,
            'casualties_enhanced': casualties_enhanced,
            'cities': cities,
            'num_cities_affected': num_cities,
            'articles_en': [a for a in all_articles if a.get('language') == 'en'][:20],
            'articles_fa': [a for a in all_articles if a.get('language') == 'fa'][:20],
            'articles_ar': [a for a in all_articles if a.get('language') == 'ar'][:20],
            'articles_he': [a for a in all_articles if a.get('language') == 'he'][:20],
            'articles_reddit': reddit_posts[:20],
            'articles_iranwire': iranwire_articles[:20],
            'articles_hrana': hrana_articles[:20],
            'exchange_rate': exchange_data,
            'oil_price': oil_data,
            'regime_stability': regime_stability,
            'version': '2.8.0'  # ← UPDATED VERSION
        })
        
    except Exception as e:
        print(f"[Iran Protests] ❌ Error: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/iran-oil-data')
def iran_oil_data_endpoint():
    """
    Returns Iran oil price + OPEC reserves data
    Used by Iran Protests page
    """
    try:
        data = get_iran_oil_data()
        return jsonify(data)
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@app.route('/scan-lebanon-stability', methods=['GET'])
def scan_lebanon_stability():
    """
    Lebanon Stability Index endpoint
    
    Tracks:
    - Political stability (government formation, elections)
    - Economic stress (bond yields, currency collapse)
    - Hezbollah activity (rearmament, strikes)
    - Gold reserves value
    - Security situation
    """
    try:
        if not check_rate_limit():
            return jsonify({'error': 'Rate limit exceeded'}), 429
        
        print("[Lebanon] Starting stability scan...")
        
        # Fetch all data sources
        currency_data = fetch_lebanon_currency()
        bond_data = scrape_lebanon_bonds()
        hezbollah_data = track_hezbollah_activity(days=7)
        
        # Calculate gold reserves with error handling
        try:
            gold_data = calculate_lebanon_gold_reserves()
            print(f"[Lebanon] ✅ Gold data calculated: {gold_data.get('display_value', 'N/A')}")
        except Exception as e:
            print(f"[Lebanon] ❌ Gold calculation failed: {str(e)}")
            gold_data = None
        
        # Calculate overall stability
        stability = calculate_lebanon_stability(currency_data, bond_data, hezbollah_data)
        
        # Update cache with today's data (including gold)
        update_lebanon_cache(
            currency_data, 
            bond_data, 
            hezbollah_data, 
            stability.get('score', 0),
            gold_data
        )
        
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
            'version': '2.7.1'
        })
        
    except Exception as e:
        print(f"[Lebanon] ❌ Error: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/lebanon-trends', methods=['GET'])
def api_lebanon_trends():
    """
    Lebanon trends endpoint for sparklines
    Returns historical data for currency, bonds, Hezbollah activity, stability score
    """
    try:
        days = int(request.args.get('days', 30))
        days = min(days, 90)  # Cap at 90 days max
        
        trends_data = get_lebanon_trends(days)
        
        return jsonify(trends_data)
        
    except Exception as e:
        print(f"[Lebanon Trends API] Error: {str(e)}")
        return jsonify({
            'success': False,
            'message': str(e),
            'days_collected': 0
        }), 500

@app.route('/api/syria-conflicts', methods=['GET'])
def api_syria_conflicts():
    """
    Syria conflicts tracker endpoint WITH displacement tracking
    
    Now includes:
    - Deaths, clashes, factions (existing)
    - UNHCR IDP data (NEW)
    - Camp populations including Al-Hol (NEW)
    - Recent displacement movements (NEW)
    - Humanitarian source links (NEW)
    """
    try:
        if not check_rate_limit():
            return jsonify({'error': 'Rate limit exceeded'}), 429
        
        days = int(request.args.get('days', 7))
        
        print(f"[Syria Conflicts] Fetching data for {days} days...")
        
        # ========================================
        # EXISTING: Fetch all article sources
        # ========================================
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
        
        print(f"[Syria Conflicts] Total articles: {len(all_articles)}")
        
        # ========================================
        # EXISTING: Extract basic conflict data
        # ========================================
        conflict_data = extract_syria_conflict_data(all_articles)
        
        # ========================================
        # NEW: Fetch displacement data
        # ========================================
        print("[Syria Conflicts] Fetching displacement data...")
        
        unhcr_data = fetch_unhcr_syria_data()
        alhol_data = fetch_alhol_camp_data()
        article_displacement = extract_displacement_from_articles(all_articles)
        acaps_data = scrape_acaps_syria()
        
        # ========================================
        # NEW: Combine all displacement sources
        # ========================================
        enhanced_displacement = enhance_syria_conflict_data_with_displacement(
            conflict_data,
            unhcr_data,
            alhol_data,
            article_displacement
        )
        
        print(f"[Syria Conflicts] ✅ Total IDPs: {enhanced_displacement['total_idps']:,}")
        print(f"[Syria Conflicts] ✅ In camps: {enhanced_displacement['camp_population']:,}")
        print(f"[Syria Conflicts] ✅ Recent movements: {enhanced_displacement['recent_displacement']:,}")
        
        # ========================================
        # RETURN ENHANCED DATA
        # ========================================
        return jsonify({
            'success': True,
            'days_analyzed': days,
            'total_articles': len(all_articles),
            
            # EXISTING: Basic conflict data
            'conflict_data': {
                'deaths': conflict_data['deaths'],
                'displaced': conflict_data['displaced'],  # Keep for backwards compatibility
                'factional_clashes': conflict_data['factional_clashes'],
                'active_factions': conflict_data['active_factions'],
                'num_factions': len(conflict_data['active_factions']),
                'clash_locations': conflict_data['clash_locations'],
                'verified_sources': conflict_data['sources'],
                'details': conflict_data['details'][:20]
            },
            
            # NEW: Enhanced displacement data
            'displacement_enhanced': enhanced_displacement,
            
            # NEW: Source-specific data
            'unhcr_data': unhcr_data,
            'alhol_camp': alhol_data,
            'acaps_analysis': acaps_data if acaps_data else {'note': 'ACAPS data unavailable'},
            
            # EXISTING: Article feeds by language
            'articles_syria_direct': syria_direct_articles[:20],
            'articles_sohr': sohr_articles[:20],
            'articles_en': [a for a in all_articles if a.get('language') == 'en'][:20],
            'articles_he': [a for a in all_articles if a.get('language') == 'he'][:20],
            'articles_ar': [a for a in all_articles if a.get('language') == 'ar'][:20],
            'articles_fa': [a for a in all_articles if a.get('language') == 'fa'][:20],
            'articles_reddit': reddit_posts[:20],
            
            'version': '2.8.1'
        })
        
    except Exception as e:
        print(f"[Syria Conflicts] ERROR: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

# ========================================
# UTILITY ENDPOINTS
# ========================================
@app.route('/', methods=['GET'])
def home():
    """Root endpoint"""
    return jsonify({
        'status': 'Backend is running',
        'version': '2.7.0',
        'endpoints': {
            '/api/threat/<target>': 'Threat assessment for hezbollah, iran, houthis, syria',
            '/scan-iran-protests': 'Iran protests data + Regime Stability Index ✅',
            '/api/jordan-threat': 'Jordan kinetic activity probability (incoming threats + defensive posture) 🇯🇴 NEW!',
            '/scan-lebanon-stability': 'Lebanon Stability Index (Political, Economic, Security, Hezbollah) 🇱🇧 NEW!',
            '/api/syria-conflicts': 'Syria conflicts tracker ✅',
            '/flight-cancellations': 'Flight disruptions monitor (15 Middle East countries) ✅',
            '/api/polymarket': 'Polymarket prediction markets proxy',
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
        'version': '2.7.0',
        'timestamp': datetime.now(timezone.utc).isoformat()
    })

@app.route('/flight-cancellations', methods=['GET'])
def flight_cancellations():
    """
    Flight cancellations monitor for Middle East destinations
    Scrapes Google News for airline suspensions/cancellations to:
    - Israel (Tel Aviv, Haifa, Eilat)
    - Lebanon (Beirut)
    - Syria (Damascus)
    - Iran (Tehran)
    - Yemen (Sanaa)
    
    Returns last 30 days of disruptions with:
    - Airline name
    - Route (origin → destination)
    - Date announced
    - Duration
    - Status (Suspended/Cancelled/Resumed)
    - Source article link
    """
    try:
        print("[Flight Cancellations] Starting scan...")
        
        # NEW: Fetch airline disruptions from RSS monitor
        print("[Flight Cancellations] Calling fetch_airline_disruptions()...")
        rss_disruptions = fetch_airline_disruptions()
        print(f"[Flight Cancellations] RSS monitor returned {len(rss_disruptions)} disruptions")

        # DEBUG: Print what we got
        if rss_disruptions:
            print(f"[Flight Cancellations] First disruption: {rss_disruptions[0]}")
        else:
            print("[Flight Cancellations] ⚠️ RSS monitor returned empty list!")
        
        # Search queries for Google News - Comprehensive Middle East coverage
        destinations = [
            # Israel
            'Tel Aviv', 'Israel', 'Haifa', 'Eilat',
            # Lebanon
            'Beirut', 'Lebanon',
            # Syria
            'Damascus', 'Syria',
            # Iran
            'Tehran', 'Iran',
            # Yemen
            'Sanaa', 'Yemen',
            # Iraq
            'Baghdad', 'Iraq', 'Erbil',
            # Jordan
            'Amman', 'Jordan',
            # Saudi Arabia
            'Riyadh', 'Saudi Arabia', 'Jeddah', 'Dammam',
            # UAE
            'Dubai', 'UAE', 'Abu Dhabi',
            # Egypt
            'Cairo', 'Egypt',
            # Turkey
            'Istanbul', 'Turkey', 'Ankara',
            # Bahrain
            'Manama', 'Bahrain',
            # Kuwait
            'Kuwait City', 'Kuwait',
            # Qatar
            'Doha', 'Qatar',
            # Oman
            'Muscat', 'Oman'
        ]
        
        keywords = [
            'airline suspended flights',
            'airline cancelled flights',
            'flight cancellation',
            'suspend service',
            'resume flights',
            'flights suspended until'
        ]
        
        all_cancellations = []
        seen_urls = set()
        
        # Search for each destination
        for destination in destinations:
            for keyword in keywords[:2]:  # Limit to 2 keywords per destination to avoid rate limits
                query = f'{keyword} {destination}'
                
                # Use Google News RSS (free, no API key needed)
                try:
                    url = f"https://news.google.com/rss/search?q={query.replace(' ', '+')}&hl=en&gl=US&ceid=US:en"
                    
                    response = requests.get(url, timeout=10, headers={
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                    })
                    
                    if response.status_code != 200:
                        continue
                    
                    # Parse RSS feed
                    import xml.etree.ElementTree as ET
                    
                    try:
                        root = ET.fromstring(response.content)
                    except ET.ParseError:
                        continue
                    
                    items = root.findall('.//item')
                    
                    for item in items[:5]:  # Top 5 results per query
                        title_elem = item.find('title')
                        link_elem = item.find('link')
                        pubDate_elem = item.find('pubDate')
                        
                        if title_elem is None or link_elem is None:
                            continue
                        
                        title = title_elem.text or ''
                        link = link_elem.text or ''
                        pub_date = pubDate_elem.text if pubDate_elem is not None else ''
                        
                        # Skip if already seen
                        if link in seen_urls:
                            continue
                        
                        seen_urls.add(link)
                        
                        # Parse cancellation data from title
                        cancellation = parse_flight_cancellation(title, link, pub_date, destination)
                        
                        if cancellation:
                            all_cancellations.append(cancellation)
                
                except Exception as e:
                    print(f"[Flight Cancellations] Error searching {destination}: {str(e)[:100]}")
                    continue
        
        # NEW: Merge RSS monitor disruptions with Google News results
        print(f"[Flight Cancellations] Merging {len(rss_disruptions)} RSS disruptions with {len(all_cancellations)} Google News results")
        for disruption in rss_disruptions:
            # Add to seen_urls to prevent duplicates
            if disruption.get('url') and disruption['url'] not in seen_urls:
                seen_urls.add(disruption['url'])
                all_cancellations.append(disruption)
        
        print(f"[Flight Cancellations] Total after merge: {len(all_cancellations)}")
        
       # Filter to last 30 days
        thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
        recent_cancellations = []
        
        for cancel in all_cancellations:
            try:
                cancel_date = datetime.fromisoformat(cancel['date'].replace('Z', '+00:00'))
                if cancel_date >= thirty_days_ago:
                    recent_cancellations.append(cancel)
            except:
                recent_cancellations.append(cancel)  # Include if date parsing fails
        
        # Sort by date (newest first) BEFORE deduplication
        recent_cancellations.sort(key=lambda x: x.get('date', ''), reverse=True)
        
        # Remove duplicates by airline + destination (keeping newest for each combo)
        unique_cancellations = []
        seen_combos = set()
        
        for cancel in recent_cancellations:
            combo = f"{cancel['airline']}_{cancel['destination']}"
            if combo not in seen_combos:
                seen_combos.add(combo)
                unique_cancellations.append(cancel)
        
        # Sort again by date to ensure final list is chronological
        unique_cancellations.sort(key=lambda x: x.get('date', ''), reverse=True)
        
        print(f"[Flight Cancellations] Found {len(unique_cancellations)} unique disruptions (after deduplication)")
        
        return jsonify({
            'success': True,
            'cancellations': unique_cancellations[:20],  # Top 20 most recent
            'count': len(unique_cancellations),
            'last_updated': datetime.now(timezone.utc).isoformat()
        })
        
    except Exception as e:
        print(f"[Flight Cancellations] ERROR: {str(e)}")
        return jsonify({
            'success': False,
            'cancellations': [],
            'error': str(e)[:200]
        }), 500


def parse_flight_cancellation(title, link, pub_date, destination):
    """
    Parse flight cancellation details from news headline
    
    Example headlines:
    - "Lufthansa suspends Tel Aviv flights until March"
    - "Air France cancels Beirut service indefinitely"
    - "British Airways resumes Tehran flights"
    """
    title_lower = title.lower()
    
    # Detect status
    status = 'Suspended'
    if 'resume' in title_lower or 'restart' in title_lower or 'return' in title_lower:
        status = 'Resumed'
    elif 'cancel' in title_lower:
        status = 'Cancelled'
    elif 'suspend' in title_lower or 'halt' in title_lower or 'stop' in title_lower:
        status = 'Suspended'
    else:
        return None  # Not a cancellation/resumption article
    
    # Extract airline name - COMPREHENSIVE LIST
    # Major international carriers
    airlines = [
        # Star Alliance
        'Lufthansa', 'United Airlines', 'United', 'Air Canada', 'Turkish Airlines',
        'Swiss', 'SWISS', 'Austrian Airlines', 'Austrian', 'LOT Polish', 'Singapore Airlines',
        'ANA', 'Air India', 'Scandinavian Airlines', 'SAS', 'TAP Air Portugal',
        
        # SkyTeam
        'Air France', 'KLM', 'Delta', 'Delta Airlines', 'Alitalia', 'ITA Airways',
        'Korean Air', 'China Airlines', 'Aeroflot', 'Vietnam Airlines',
        
        # Oneworld
        'British Airways', 'American Airlines', 'American', 'Cathay Pacific', 
        'Qantas', 'Japan Airlines', 'JAL', 'Iberia', 'Finnair', 'Qatar Airways',
        
        # Middle East carriers
        'Emirates', 'Etihad', 'Qatar Airways', 'flydubai', 'Air Arabia',
        'Saudia', 'Saudi Arabian Airlines', 'Gulf Air', 'Kuwait Airways',
        'Royal Jordanian', 'Oman Air', 'Middle East Airlines', 'MEA',
        
        # Low-cost carriers
        'Wizz Air', 'Ryanair', 'EasyJet', 'Pegasus Airlines', 'IndiGo',
        'AirAsia', 'Jetstar', 'Norwegian', 'Vueling', 'Transavia',
        
        # Israeli carriers
        'El Al', 'Arkia', 'Israir',
        
        # Regional carriers
        'Air Astana', 'Azerbaijan Airlines', 'Georgian Airways', 'Belavia',
        'Ukraine International', 'Aegean Airlines', 'Croatia Airlines',
        
        # Other major carriers
        'Air New Zealand', 'South African Airways', 'Ethiopian Airlines',
        'Kenya Airways', 'Egypt Air', 'EgyptAir', 'Royal Air Maroc'
    ]
    
    # Airline group mappings
    airline_groups = {
        'Lufthansa Group': ['Lufthansa', 'SWISS', 'Austrian Airlines'],
        'IAG': ['British Airways', 'Iberia', 'Aer Lingus', 'Vueling'],
        'Air France-KLM': ['Air France', 'KLM'],
    }
    
    airline_found = None
    
    # Check for airline groups first
    for group_name, group_airlines in airline_groups.items():
        if group_name.lower() in title_lower:
            # Return first airline in group as primary
            airline_found = group_airlines[0]
            break
    
    # Check for individual airlines
    if not airline_found:
        for airline in airlines:
            if airline.lower() in title_lower:
                airline_found = airline
                break
    
    # Try extracting from title structure
    if not airline_found:
        # Pattern 1: "Airline suspends/cancels/resumes"
        pattern1 = re.search(r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+(?:suspend|cancel|halt|resume|restart)', title, re.IGNORECASE)
        if pattern1:
            potential_airline = pattern1.group(1)
            # Verify it's not a common word
            if len(potential_airline) > 3 and potential_airline not in ['United States', 'European Union', 'Middle East']:
                airline_found = potential_airline
    
    # Pattern 2: Look for capitalized words before keywords
    if not airline_found:
        words = title.split()
        for i, word in enumerate(words):
            if len(word) > 3 and word[0].isupper():
                if i < len(words) - 1:
                    next_word = words[i + 1].lower()
                    if next_word in ['suspend', 'suspends', 'cancel', 'cancels', 'halt', 'halts', 'resume', 'resumes', 'pause', 'pauses']:
                        airline_found = word
                        break
    
    if not airline_found:
        airline_found = "Unknown Airline"
    
    # Extract duration
    duration = "Indefinite"
    
    # Look for "until [date]"
    until_match = re.search(r'until\s+([A-Za-z]+\s+\d{1,2}(?:,\s+\d{4})?)', title, re.IGNORECASE)
    if until_match:
        duration = f"Until {until_match.group(1)}"
    
    # Look for specific months
    months = ['January', 'February', 'March', 'April', 'May', 'June', 
              'July', 'August', 'September', 'October', 'November', 'December']
    for month in months:
        if month.lower() in title_lower:
            year_match = re.search(r'\b(202[4-9])\b', title)
            if year_match:
                duration = f"Until {month} {year_match.group(1)}"
            else:
                duration = f"Until {month}"
            break
    
    # Look for "for X days/weeks/months"
    for_match = re.search(r'for\s+(\d+)\s+(day|week|month)s?', title, re.IGNORECASE)
    if for_match:
        num = for_match.group(1)
        unit = for_match.group(2)
        duration = f"For {num} {unit}{'s' if int(num) > 1 else ''}"
    
    # Parse date
    try:
        if pub_date:
            # Try to parse RFC 2822 format (RSS standard)
            from email.utils import parsedate_to_datetime
            date_obj = parsedate_to_datetime(pub_date)
            date_str = date_obj.isoformat()
        else:
            date_str = datetime.now(timezone.utc).isoformat()
    except:
        date_str = datetime.now(timezone.utc).isoformat()
    
    # Build route (origin → destination)
    # Try to extract origin from title
    origin = "Various"
    major_cities = [
        'Frankfurt', 'Paris', 'London', 'New York', 'Dubai', 'Istanbul',
        'Munich', 'Vienna', 'Warsaw', 'Amsterdam', 'Madrid', 'Rome',
        'Zurich', 'Geneva', 'Delhi', 'Mumbai', 'Singapore', 'Hong Kong'
    ]
    
    for city in major_cities:
        if city.lower() in title_lower:
            origin = city
            break
    
    route = f"{origin} → {destination}"
    
    return {
        'airline': airline_found,
        'route': route,
        'origin': origin,
        'destination': destination,
        'date': date_str,
        'duration': duration,
        'status': status,
        'source_url': link,
        'headline': title[:150]  # Truncate long headlines
    }

# NOTAM endpoint
@app.route('/api/notams')
def get_notams():
    """Fetch active NOTAMs for Middle East region"""
    
    # Middle East ICAO codes and country mappings
    MIDDLE_EAST_FIRS = {
        'LLLL': {'country': 'Israel', 'flag': '🇮🇱', 'name': 'Tel Aviv FIR'},
        'OLBB': {'country': 'Lebanon', 'flag': '🇱🇧', 'name': 'Beirut FIR'},
        'OSTT': {'country': 'Syria', 'flag': '🇸🇾', 'name': 'Damascus FIR'},
        'OIIX': {'country': 'Iran', 'flag': '🇮🇷', 'name': 'Tehran FIR'},
        'OYSC': {'country': 'Yemen', 'flag': '🇾🇪', 'name': 'Sanaa FIR'},
        'ORBB': {'country': 'Iraq', 'flag': '🇮🇶', 'name': 'Baghdad FIR'},
        'OJAC': {'country': 'Jordan', 'flag': '🇯🇴', 'name': 'Amman FIR'},
        'HECC': {'country': 'Egypt', 'flag': '🇪🇬', 'name': 'Cairo FIR'},
        'OEJD': {'country': 'Saudi Arabia', 'flag': '🇸🇦', 'name': 'Jeddah FIR'},
        'OEDF': {'country': 'Saudi Arabia', 'flag': '🇸🇦', 'name': 'Riyadh FIR'},
        'OMAE': {'country': 'UAE', 'flag': '🇦🇪', 'name': 'Dubai FIR'},
        'OTDF': {'country': 'Qatar', 'flag': '🇶🇦', 'name': 'Doha FIR'},
        'OOMM': {'country': 'Oman', 'flag': '🇴🇲', 'name': 'Muscat FIR'},
        'OBBB': {'country': 'Bahrain', 'flag': '🇧🇭', 'name': 'Bahrain FIR'}
    }
    
    notams = []
    
    try:
        # FAA NOTAM Search API endpoint
        base_url = "https://external-api.faa.gov/notamapi/v1/notams"
        
        for icao_code, info in MIDDLE_EAST_FIRS.items():
            try:
                # Query FAA NOTAM API
                params = {
                    'locationICAOId': icao_code,
                    'responseType': 'application/json'
                }
                
                response = requests.get(base_url, params=params, timeout=5)
                
                if response.status_code == 200:
                    data = response.json()
                    
                    # Parse NOTAMs
                    if data.get('notamList'):
                        for notam in data['notamList']:
                            parsed = parse_notam(notam, info)
                            if parsed:
                                notams.append(parsed)
                
                # Rate limit protection
                time.sleep(0.2)
                
            except Exception as e:
                print(f"Error fetching NOTAM for {icao_code}: {e}")
                continue
        
        # Sort by priority (closures first, then by date)
        priority_order = {
            'AIRSPACE CLOSURE': 1,
            'FLIGHT RESTRICTION': 2,
            'MILITARY ACTIVITY': 3,
            'AIRPORT CLOSURE': 4,
            'NAVAID OUTAGE': 5,
            'HAZARD': 6,
            'OTHER': 7
        }
        
        notams.sort(key=lambda x: (priority_order.get(x['type'], 99), x.get('effective_date', '')))
        
        result = {
            'notams': notams,
            'count': len(notams),
            'last_updated': datetime.now(timezone.utc).isoformat()
        }
        
        return jsonify(result)
    
    except Exception as e:
        print(f"NOTAM fetch error: {e}")
        return jsonify({'notams': [], 'count': 0, 'error': str(e)}), 500


def parse_notam(notam_data, location_info):
    """Parse NOTAM data into structured format"""
    try:
        # Extract NOTAM text
        notam_text = notam_data.get('notamText', '').upper()
        
        # Determine NOTAM type based on keywords
        notam_type = 'OTHER'
        icon = '📋'
        color = 'gray'
        
        if any(word in notam_text for word in ['AIRSPACE CLOSED', 'AIRSPACE CLO', 'FIR CLOSED']):
            notam_type = 'AIRSPACE CLOSURE'
            icon = '⛔'
            color = 'red'
        elif any(word in notam_text for word in ['RESTRICTED', 'PROHIBITED', 'DANGER AREA']):
            notam_type = 'FLIGHT RESTRICTION'
            icon = '🚫'
            color = 'orange'
        elif any(word in notam_text for word in ['MILITARY', 'MIL ACT', 'EXERCISE']):
            notam_type = 'MILITARY ACTIVITY'
            icon = '⚠️'
            color = 'yellow'
        elif any(word in notam_text for word in ['AIRPORT CLOSED', 'AD CLOSED', 'RWY CLOSED']):
            notam_type = 'AIRPORT CLOSURE'
            icon = '🛑'
            color = 'purple'
        elif any(word in notam_text for word in ['NAVAID', 'VOR', 'DME', 'ILS', 'U/S']):
            notam_type = 'NAVAID OUTAGE'
            icon = '📡'
            color = 'blue'
        elif any(word in notam_text for word in ['VOLCANIC', 'ASH', 'HAZARD', 'OBSTRUCTION']):
            notam_type = 'HAZARD'
            icon = '⚠️'
            color = 'gray'
        
        # Extract dates
        effective_date = notam_data.get('effectiveStart', '')
        expiry_date = notam_data.get('effectiveEnd', '')
        
        # Format dates
        effective_formatted = ''
        expiry_formatted = ''
        
        if effective_date:
            try:
                eff_dt = datetime.fromisoformat(effective_date.replace('Z', '+00:00'))
                effective_formatted = eff_dt.strftime('%b %d, %Y')
            except:
                effective_formatted = effective_date[:10]
        
        if expiry_date:
            try:
                exp_dt = datetime.fromisoformat(expiry_date.replace('Z', '+00:00'))
                expiry_formatted = exp_dt.strftime('%b %d, %Y')
            except:
                expiry_formatted = expiry_date[:10]
        
        # Create summary (first 150 chars of NOTAM text)
        summary = notam_text[:150].strip()
        if len(notam_text) > 150:
            summary += '...'
        
        # NOTAM ID
        notam_id = notam_data.get('notamID', 'N/A')
        
        return {
            'id': notam_id,
            'country': location_info['country'],
            'flag': location_info['flag'],
            'fir': location_info['name'],
            'type': notam_type,
            'icon': icon,
            'color': color,
            'summary': summary,
            'effective_date': effective_formatted,
            'expiry_date': expiry_formatted,
            'valid_range': f"{effective_formatted} - {expiry_formatted}" if expiry_formatted else f"From {effective_formatted}",
            'notam_text': notam_text,
            'source_url': f"https://notams.aim.faa.gov/notamSearch/notam.html?id={notam_id}"
        }
    
    except Exception as e:
        print(f"NOTAM parse error: {e}")
        return None
               
@app.route('/api/polymarket', methods=['GET'])
def polymarket_proxy():
    """
    Proxy Polymarket API to fetch geopolitical markets
    Filters by keywords: Iran, Syria, Lebanon, Israel, Yemen, Hezbollah, Houthis, strikes
    """
    try:
        # Try multiple Polymarket API endpoints
        endpoints = [
            'https://gamma-api.polymarket.com/events?active=true&limit=100',
            'https://gamma-api.polymarket.com/markets?limit=100&active=true',
            'https://strapi-matic.poly.market/markets?_limit=100&closed=false'
        ]
        
        markets_data = None
        
        for endpoint in endpoints:
            try:
                print(f"[Polymarket] Trying endpoint: {endpoint}")
                response = requests.get(endpoint, timeout=10, headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                    'Accept': 'application/json'
                })
                
                if response.status_code == 200 and response.text.strip():
                    markets_data = response.json()
                    print(f"[Polymarket] ✅ Success with {endpoint}")
                    break
            except Exception as e:
                print(f"[Polymarket] Failed {endpoint}: {str(e)[:100]}")
                continue
        
        if not markets_data:
            print("[Polymarket] ❌ All endpoints failed")
            return jsonify({
                'success': False,
                'markets': [],
                'message': 'Polymarket API unavailable'
            })
        
        # Keywords to filter for (case-insensitive)
        keywords = [
            'iran', 'iranian', 'tehran', 'khamenei',
            'syria', 'syrian', 'damascus', 'assad',
            'lebanon', 'lebanese', 'beirut',
            'israel', 'israeli', 'idf',
            'yemen', 'yemeni', 'sanaa',
            'hezbollah', 'hizballah', 'hizbollah',
            'houthi', 'houthis', 'ansarallah',
            'strike', 'strikes', 'attack', 'war',
            'gaza', 'hamas', 'palestinian'
        ]
        
        relevant_markets = []
        
        # Handle different API response structures
        events = markets_data if isinstance(markets_data, list) else markets_data.get('data', [])
        
        for event in events:
            # Extract title and description
            title = event.get('title', event.get('question', ''))
            description = event.get('description', '')
            slug = event.get('slug', event.get('id', ''))
            
            # Combine text for keyword matching
            text = (title + ' ' + description).lower()
            
            # Check if any keyword matches
            if not any(keyword in text for keyword in keywords):
                continue
            
            # Extract probability
            prob = None
            
            # Try different probability field names
            if 'outcomePrices' in event and isinstance(event['outcomePrices'], list):
                prob = float(event['outcomePrices'][0])
            elif 'outcome_prices' in event and isinstance(event['outcome_prices'], list):
                prob = float(event['outcome_prices'][0])
            elif 'probability' in event:
                prob = float(event['probability'])
            elif 'price' in event:
                prob = float(event['price'])
            
            if prob is None or not (0 <= prob <= 1):
                continue
            
            relevant_markets.append({
                'question': title[:100],  # Truncate long titles
                'probability': round(prob, 3),
                'url': f"https://polymarket.com/event/{slug}"
            })
        
        print(f"[Polymarket] Found {len(relevant_markets)} relevant markets")
        
        return jsonify({
            'success': True,
            'markets': relevant_markets[:15],  # Limit to top 15
            'count': len(relevant_markets)
        })
        
    except Exception as e:
        print(f"[Polymarket] ❌ Error: {str(e)[:200]}")
        return jsonify({
            'success': False,
            'markets': [],
            'error': str(e)[:200]
        }), 500

@app.route('/api/osint-feed', methods=['GET'])
def api_osint_feed():
    """
    OSINTDefender Instagram feed endpoint
    Returns last 3 posts with country detection
    Cached for 30 minutes
    """
    try:
        current_time = time.time()
        
        # Check cache
        if instagram_feed_cache['data'] and current_time < instagram_feed_cache['expires_at']:
            print("[Instagram API] Returning cached data")
            return jsonify(instagram_feed_cache['data'])
        
        # Scrape fresh data
        feed_data = scrape_osint_instagram()
        
        # Cache for 30 minutes
        instagram_feed_cache['data'] = feed_data
        instagram_feed_cache['expires_at'] = current_time + 1800  # 30 min = 1800 sec
        
        return jsonify(feed_data)
        
    except Exception as e:
        print(f"[Instagram API] Error: {str(e)}")
        return jsonify({
            'success': False,
            'posts': [],
            'error': str(e)[:200]
        }), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
