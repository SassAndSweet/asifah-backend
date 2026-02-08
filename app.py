"""
Asifah Analytics Backend v2.7.0
February 3, 2026

Changes from v2.6.7:
- NEW: Lebanon Stability Index (/scan-lebanon-stability) 🇱🇧
  * Multi-factor stability scoring (Political, Economic, Security, Hezbollah)
  * Eurobond yield scraping (Trading Economics)
  * LBP/USD black market rate tracking
  * Hezbollah rearmament indicators
  * Parliamentary elections countdown (May 3, 2026)
  * Presidential vacancy tracking (since Oct 2022)
  * Color-coded risk levels (Green/Yellow/Orange/Red)

Changes from v2.6.6:
- REBALANCED: Regime Stability formula - reduced Rial devaluation weight
  * Changed from 0.3 to 0.15 multiplier
  * Reflects that regime has survived extreme currency collapse
  * Should show ~33/100 instead of 0/100 for current conditions
  * More realistic "Critical Risk" vs "Imminent Collapse" distinction

Changes from v2.6.5:
- FIXED: Oil price cascade with 4 reliable FREE APIs!
  * US EIA (Energy Information Administration) - No key required
  * OilPriceAPI Demo - 20 requests/hour, no auth
  * FRED (Federal Reserve) - CSV download, no key
  * Alpha Vantage - Backup (25/day)
  * Should fix Regime Stability stuck at 2/100

Changes from v2.6.4:
- ENHANCED: Flight Cancellations Monitor - Massive upgrade!
  * Added 40+ airlines (regional carriers, airline groups)
  * Expanded to ALL Middle East destinations (Iraq, Jordan, Saudi, UAE, Egypt, Turkey, Bahrain, Kuwait, Qatar, Oman)
  * Improved airline extraction with group recognition (Lufthansa Group, IAG, Air France-KLM)
  * Better parsing patterns for complex headlines
  * Total coverage: 45+ destinations across 15 countries

Changes from v2.6.3:
- NEW: Flight Cancellations Monitor (/flight-cancellations)
- Automated Google News scraping for airline disruptions
- Tracks Israel, Lebanon, Syria, Iran, Yemen destinations
- Returns airline, route, date, duration, status, source link
- 30-day rolling window with auto-deduplication

Changes from v2.6.2:
- FIXED: Alpha Vantage oil price parsing bug
- Better error handling for API responses
- More detailed logging for debugging
- Validates data structure before parsing

Changes from v2.6.1:
- NEW: Oil price integration via cascading fallback (Alpha Vantage → Commodities-API → OilPriceAPI)
- Oil prices now factor into Regime Stability calculation (±5 points)
- Higher oil = more Iran revenue = higher stability (despite sanctions)
- Baseline: $75/barrel, $10 deviation = ±0.5 stability points

Changes from v2.6.0:
- FIXED: Regime Stability formula now includes +30 military strength baseline
- Accounts for IRGC operational effectiveness preventing immediate collapse
- Reweighted economic factors (×0.3 instead of ×1.5) to be less catastrophic
- Realistic scores: ~30-35 (High Risk) instead of 0 (Critical)

"""
Asifah Analytics Backend v2.8.0
February 8, 2026

All endpoints working:
- /api/threat/<target> (hezbollah, iran, houthis, syria)
- /scan-iran-protests (with HRANA data + Regime Stability! ✅)
- /api/syria-conflicts
- /api/iran-strike-probability (with caching! ✅)
- /api/hezbollah-activity (with caching! ✅)
- /api/houthis-threat (with caching! ✅)
- /api/syria-conflict (with caching! ✅)
"""

# ========================================
# IMPORTS
# ========================================

# Standard library imports first
from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
from datetime import datetime, timezone, timedelta
import os
import time
import re
import math
import json
from pathlib import Path

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
CORS(app)

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
                        'article': title[:80]
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


def calculate_combined_probability(israel_prob, us_prob, coordination):
    """
    Calculate combined probability using independent events + coordination
    
    Formula:
    1. Base: P(at least one) = 1 - (1-P₁)(1-P₂)
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


def calculate_reverse_threat(articles, source_actor='iran', target_actor='israel'):
    """
    Calculate probability of source actor attacking target
    (e.g., Iran → Israel, Hezbollah → Israel)
    """
    
    REVERSE_THREAT_KEYWORDS = {
        'iran_threats': [
            'iran threatens', 'irgc warns', 'khamenei threatens', 'tehran vows',
            'retaliation', 'revenge attack', 'severe response'
        ],
        'hezbollah_threats': [
            'nasrallah threatens', 'hezbollah warns', 'resistance axis',
            'rocket fire', 'cross-border attack'
        ],
        'proxy_mobilization': [
            'militia deployment', 'proxy forces', 'armed groups mobilize',
            'shiite militias', 'hashd forces'
        ],
        'missile_tests': [
            'missile test', 'ballistic missile', 'cruise missile launch',
            'weapons test', 'military drill'
        ]
    }
    
    threat_score = 0
    indicators = []
    
    for article in articles:
        content = f"{article.get('title', '')} {article.get('description', '')} {article.get('content', '')}".lower()
        
        # Check for threat keywords
        for category, phrases in REVERSE_THREAT_KEYWORDS.items():
            for phrase in phrases:
                if phrase in content and target_actor in content:
                    threat_score += 2
                    indicators.append({
                        'type': category,
                        'phrase': phrase,
                        'article': article.get('title', '')[:80]
                    })
    
    # Convert to probability (cap at 60% - reverse threats typically lower)
    probability = min(threat_score / 50.0, 0.60)
    
    return {
        'probability': probability,
        'source': source_actor,
        'target': target_actor,
        'indicators': indicators[:5],
        'risk_level': 'high' if probability > 0.40 else 'moderate' if probability > 0.20 else 'low'
    }


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
        
        # Apply leadership multiplier if present
        article_contribution = apply_leadership_multiplier(article_contribution, article)
        
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
        # FIX: Prevent division by zero when days_analyzed <= 2
        days_for_older = max(1, days_analyzed - 2)
        older_density = older_articles / days_for_older
        
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
# OIL PRICE FETCHING - CASCADING FALLBACK SYSTEM
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
    
    Formula v2.6.7:
    Stability = Base(50)
                + Military Strength Baseline(+30)
                - (Rial Devaluation Impact × 1.5)  [Rebalanced from ×3 in v2.6.1]
                - (Protest Intensity × 3)
                - (Arrest Rate Impact × 2)
                + (Oil Price Impact ±5)
                + (Time Decay Bonus)
    
    Lower scores = Higher instability/regime stress
    
    Note: Rial weight reduced to 0.15 (from 0.3) to reflect that regime has survived
    extreme currency devaluation - currency collapse is critical but not immediately fatal.
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
        
        print(f"[Regime Stability] Oil price: ${oil_price:.2f} (baseline: ${baseline_oil}) → Impact: {oil_price_impact:+.1f}")
    
    # ========================================
    # CURRENCY DEVALUATION IMPACT
    # ========================================
    rial_devaluation_impact = 0
    
    if exchange_data:
        current_rate = exchange_data.get('usd_to_irr', 42000)
        baseline_rate = 42000
        
        devaluation_pct = ((current_rate - baseline_rate) / baseline_rate) * 100
        rial_devaluation_impact = (devaluation_pct / 10) * 0.15  # Reduced from 0.3 to 0.15 (v2.6.7)
        
        print(f"[Regime Stability] Rial devaluation: {devaluation_pct:.1f}% → Impact: -{rial_devaluation_impact:.1f}")
    
    # ========================================
    # PROTEST INTENSITY IMPACT
    # ========================================
    protest_intensity_impact = 0
    arrest_rate_impact = 0
    
    if protest_data:
        intensity = protest_data.get('intensity', 0)
        protest_intensity_impact = (intensity / 10) * 0.3
        
        arrests = protest_data.get('casualties', {}).get('arrests', 0)
        arrest_rate_impact = (arrests / 100) * 0.2
        
        print(f"[Regime Stability] Protest intensity: {intensity}/100 → Impact: -{protest_intensity_impact:.1f}")
        print(f"[Regime Stability] Arrests: {arrests} → Impact: -{arrest_rate_impact:.1f}")
    
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
    # FINAL SCORE CALCULATION
    # ========================================
    stability_score = (base_score + military_strength_baseline + oil_price_impact - 
                      rial_devaluation_impact - protest_intensity_impact - 
                      arrest_rate_impact + time_decay_bonus)
    
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
            trend = "increasing"
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
            'rial_devaluation_impact': round(-rial_devaluation_impact, 2),
            'protest_intensity_impact': round(-protest_intensity_impact, 2),
            'arrest_rate_impact': round(-arrest_rate_impact, 2),
            'time_decay_bonus': round(time_decay_bonus, 2),
            'formula': 'Base(50) + Military(+30) + Oil(±5) - Rial - Protest - Arrest + Time'
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
    
    # Parliamentary elections scheduled for May 3, 2026
    election_date = datetime(2026, 5, 3, tzinfo=timezone.utc)
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
    Calculate trends and estimates with caching
    Returns enhanced casualty data with recent/cumulative/trends
    """
    try:
        cache = load_casualty_cache()
        history = cache.get('history', {})
        
        # Get yesterday's data for trend calculation
        yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
        yesterday_data = history.get(yesterday, {})
        
        # Known cumulative baselines (from HRANA public reports as of Feb 2026)
        CUMULATIVE_BASELINE = {
            'arrests': 50000,
            'deaths': 551,
            'injuries': 22000,
            'start_date': '2022-09-16'
        }
        
        # Calculate days since September 2022
        start_date = datetime.strptime(CUMULATIVE_BASELINE['start_date'], '%Y-%m-%d').replace(tzinfo=timezone.utc)
        days_since_start = (datetime.now(timezone.utc) - start_date).days
        weeks_since_start = days_since_start / 7
        
        # Current 7-day values
        arrests_7d = current_casualties.get('arrests', 0)
        deaths_7d = current_casualties.get('deaths', 0)
        injuries_7d = current_casualties.get('injuries', 0)
        
        # Estimate 30-day (7d × 4.3 weeks)
        arrests_30d = int(arrests_7d * 4.3)
        deaths_30d = int(deaths_7d * 4.3)
        injuries_30d = int(injuries_7d * 4.3)
        
        # Calculate trends (compare to yesterday if available)
        def calc_trend(current, previous):
            if previous and previous > 0:
                return ((current - previous) / previous) * 100
            elif current > 0:
                # First day estimate: assume 10% increase (will improve tomorrow)
                return 10.0
            return 0
        
        arrests_trend = calc_trend(arrests_7d, yesterday_data.get('arrests_7d', 0))
        deaths_trend = calc_trend(deaths_7d, yesterday_data.get('deaths_7d', 0))
        injuries_trend = calc_trend(injuries_7d, yesterday_data.get('injuries_7d', 0))
        
        # Calculate weekly averages
        avg_arrests_week = int(CUMULATIVE_BASELINE['arrests'] / weeks_since_start)
        avg_deaths_week = int(CUMULATIVE_BASELINE['deaths'] / weeks_since_start)
        avg_injuries_week = int(CUMULATIVE_BASELINE['injuries'] / weeks_since_start)
        
        # Build enhanced response
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
                'estimated': True  # Mark as estimated
            },
            'cumulative': {
                'arrests': f"~{CUMULATIVE_BASELINE['arrests']:,}+",
                'deaths': CUMULATIVE_BASELINE['deaths'],
                'injuries': f"~{CUMULATIVE_BASELINE['injuries']:,}+",
                'estimated': True,
                'since': 'September 16, 2022'
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
        
        # Update cache with today's data
        update_casualty_cache(current_casualties)
        
        return enhanced
        
    except Exception as e:
        print(f"[Trends] Error calculating trends: {str(e)}")
        # Return basic data if calculation fails
        return {
            'recent_7d': current_casualties,
            'recent_30d': {'estimated': True},
            'cumulative': {'estimated': True},
            'averages': {},
            'trends': {},
            'sources': current_casualties.get('sources', []),
            'hrana_verified': current_casualties.get('hrana_verified', False)
        }


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


def update_lebanon_cache(currency_data, bond_data, hezbollah_data, stability_score):
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
            'stability': []
        }
        
        for date in sorted_dates:
            day_data = history[date]
            trends['dates'].append(date)
            trends['currency'].append(day_data.get('currency_rate', 0))
            trends['bonds'].append(day_data.get('bond_yield', 0))
            trends['hezbollah'].append(day_data.get('hezbollah_activity', 0))
            trends['stability'].append(day_data.get('stability_score', 0))
        
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
            'escalation_keywords': ESCALATION_KEYWORDS,
            'target_keywords': TARGET_KEYWORDS[target]['keywords'],
            'cached': False,
            'version': '2.7.0'
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
        reverse_israel = calculate_reverse_threat(all_articles, target, 'israel')
        reverse_us = calculate_reverse_threat(all_articles, target, 'us')
        
        # Build response
        response = {
            'success': True,
            'target': target,
            'target_flag': {
                'iran': '🇮🇷',
                'hezbollah': '🇱🇧',
                'houthis': '🇾🇪',
                'syria': '🇸🇾'
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
# Add these AFTER your /api/threat/<target> endpoint
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
        
        result = {
            'success': True,
            'probability': probability,  # PRIMARY: Strike probability
            'activity_level': int(activity_level),
            'activity_description': activity_desc,
            'momentum': momentum,
            'total_articles': len(all_articles),
            'recent_articles_48h': scoring_result['breakdown']['recent_articles_48h'],
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
        
        # Calculate strike probability (NOT threat_level + probability!)
        scoring_result = calculate_threat_probability(all_articles, days, 'houthis')
        probability = scoring_result['probability']  # JUST USE THIS!
        momentum = scoring_result['momentum']
        
        # Shipping incidents as secondary metric
        shipping_incidents = 0
        for article in all_articles:
            text = f"{article.get('title', '')} {article.get('description', '')}".lower()
            if any(word in text for word in ['shipping', 'red sea', 'attacked', 'strike', 'missile', 'drone']):
                shipping_incidents += 1
        
        # Threat description based on probability (not inflated!)
        if probability >= 75:
            threat_desc = "Critical"
        elif probability >= 50:
            threat_desc = "High"
        elif probability >= 25:
            threat_desc = "Moderate"
        else:
            threat_desc = "Low"
        
        result = {
            'success': True,
            'probability': probability,  # PRIMARY: Just the strike probability!
            'threat_description': threat_desc,
            'momentum': momentum,
            'shipping_incidents': shipping_incidents,
            'total_articles': len(all_articles),
            'recent_articles_48h': scoring_result['breakdown']['recent_articles_48h'],
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
            'iran',  # Use Iran subreddits as they cover Syria
            ['Syria', 'Assad', 'Damascus', 'conflict', 'strike'],
            days
        )
        
        all_articles = (articles_en + articles_gdelt_en + articles_gdelt_ar + articles_reddit)
        
        # Calculate strike probability
        scoring_result = calculate_threat_probability(all_articles, days, 'iran')  # Use Iran baseline
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
        
        result = {
            'success': True,
            'probability': probability,  # PRIMARY: Strike probability
            'intensity': intensity,
            'intensity_description': intensity_desc,
            'momentum': momentum,
            'total_articles': len(all_articles),
            'escalation_articles': escalation_articles,
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

# ========================================
# NEW: INDIVIDUAL COUNTRY ENDPOINTS
# (Added for frontend collapse cards)
# ========================================

@app.route('/api/iran-strike-probability', methods=['GET'])
def api_iran_strike_probability():
    """
    Iran Strike Probability Endpoint
    Returns probability of Israeli strike against Iran
    """
    try:
        days = int(request.args.get('days', 7))
        
        if not check_rate_limit():
            return jsonify({
                'success': False,
                'error': 'Rate limit exceeded',
                'probability': 0,
                'rate_limited': True
            }), 200
        
        # Use your existing TARGET_KEYWORDS for 'iran'
        query = ' OR '.join(TARGET_KEYWORDS['iran']['keywords'])
        
        # Fetch articles using SAME METHOD as /api/threat/iran
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
        
        # IMPORTANT: Fetch RSS feeds if you have fetch_all_rss() function
        try:
            rss_articles = fetch_all_rss()
            all_articles.extend(rss_articles)
            print(f"[Iran Strike] Added {len(rss_articles)} RSS articles")
        except Exception as e:
            print(f"[Iran Strike] RSS fetch failed: {e}")
        
        # Use EXACT SAME scoring algorithm as /api/threat/iran
        scoring_result = calculate_threat_probability(all_articles, days, 'iran')
        probability = scoring_result['probability']  # This should match your main endpoint!
        momentum = scoring_result['momentum']
        
        # Timeline based on probability
        if probability < 30:
            timeline = "180+ Days"
        elif probability < 50:
            timeline = "91-180 Days"
        elif probability < 70:
            timeline = "31-90 Days"
        else:
            timeline = "0-30 Days"
        
        # Confidence based on article count and sources
        unique_sources = len(set(a.get('source', {}).get('name', 'Unknown') for a in all_articles))
        if len(all_articles) >= 20 and unique_sources >= 8:
            confidence = "High"
        elif len(all_articles) >= 10 and unique_sources >= 5:
            confidence = "Medium"
        else:
            confidence = "Low"
        
        print(f"[Iran Strike] Probability: {probability}% from {len(all_articles)} articles")
        
        return jsonify({
            'success': True,
            'probability': probability,  # ← FRONTEND EXPECTS THIS FIELD
            'timeline': timeline,
            'confidence': confidence,
            'momentum': momentum,
            'total_articles': len(all_articles),
            'unique_sources': unique_sources,
            'recent_articles_48h': scoring_result['breakdown']['recent_articles_48h'],
            'last_updated': datetime.now(timezone.utc).isoformat(),
            'cached': False,
            'version': '2.7.1'
        })
        
    except Exception as e:
        print(f"Error in /api/iran-strike-probability: {e}")
        import traceback
        traceback.print_exc()
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
    Returns STRIKE PROBABILITY (not just activity level)
    """
    try:
        days = int(request.args.get('days', 7))
        
        if not check_rate_limit():
            return jsonify({
                'success': False,
                'error': 'Rate limit exceeded',
                'probability': 0,  # Changed from activity_level
                'rate_limited': True
            }), 200
        
        # Use your existing TARGET_KEYWORDS for 'hezbollah'
        query = ' OR '.join(TARGET_KEYWORDS['hezbollah']['keywords'])
        
        # Fetch articles - SAME AS MAIN ENDPOINT
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
        
        # Add RSS feeds if available
        try:
            rss_articles = fetch_all_rss()
            all_articles.extend(rss_articles)
            print(f"[Hezbollah Strike] Added {len(rss_articles)} RSS articles")
        except Exception as e:
            print(f"[Hezbollah Strike] RSS fetch failed: {e}")
        
        # Use EXACT SAME scoring algorithm
        scoring_result = calculate_threat_probability(all_articles, days, 'hezbollah')
        probability = scoring_result['probability']  # This is the STRIKE probability!
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
        
        print(f"[Hezbollah Strike] Probability: {probability}% from {len(all_articles)} articles")
        
        return jsonify({
            'success': True,
            'probability': probability,  # ← CHANGED: Now returns strike probability
            'activity_level': int(activity_level),  # ← Kept as secondary metric
            'activity_description': activity_desc,
            'momentum': momentum,
            'total_articles': len(all_articles),
            'recent_articles_48h': scoring_result['breakdown']['recent_articles_48h'],
            'last_updated': datetime.now(timezone.utc).isoformat(),
            'cached': False,
            'version': '2.7.1'
        })
        
    except Exception as e:
        print(f"Error in /api/hezbollah-activity: {e}")
        import traceback
        traceback.print_exc()
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
    Returns STRIKE PROBABILITY (not just threat level)
    """
    try:
        days = int(request.args.get('days', 7))
        
        if not check_rate_limit():
            return jsonify({
                'success': False,
                'error': 'Rate limit exceeded',
                'probability': 0,  # Changed from threat_level
                'rate_limited': True
            }), 200
        
        # Use your existing TARGET_KEYWORDS for 'houthis'
        query = ' OR '.join(TARGET_KEYWORDS['houthis']['keywords'])
        
        # Fetch articles - SAME AS MAIN ENDPOINT
        articles_en = fetch_newsapi_articles(query, days)
        articles_gdelt_en = fetch_gdelt_articles(query, days, 'eng')
        articles_gdelt_ar = fetch_gdelt_articles(query, days, 'ara')
        
        articles_reddit = fetch_reddit_posts(
            'houthis',
            TARGET_KEYWORDS['houthis']['reddit_keywords'],
            days
        )
        
        all_articles = (articles_en + articles_gdelt_en + articles_gdelt_ar + articles_reddit)
        
        # Add RSS feeds if available
        try:
            rss_articles = fetch_all_rss()
            all_articles.extend(rss_articles)
            print(f"[Houthis Strike] Added {len(rss_articles)} RSS articles")
        except Exception as e:
            print(f"[Houthis Strike] RSS fetch failed: {e}")
        
        # Use EXACT SAME scoring algorithm
        scoring_result = calculate_threat_probability(all_articles, days, 'houthis')
        probability = scoring_result['probability']  # This is the STRIKE probability!
        momentum = scoring_result['momentum']
        
        # Threat level as secondary metric
        threat_level = min(100, probability + (len(all_articles) / 2))
        
        # Check for shipping disruption keywords
        shipping_incidents = 0
        for article in all_articles:
            text = f"{article.get('title', '')} {article.get('description', '')}".lower()
            if any(word in text for word in ['shipping', 'red sea', 'attacked', 'strike', 'missile', 'drone']):
                shipping_incidents += 1
        
        if threat_level >= 75:
            threat_desc = "Critical"
        elif threat_level >= 50:
            threat_desc = "High"
        elif threat_level >= 25:
            threat_desc = "Moderate"
        else:
            threat_desc = "Low"
        
        print(f"[Houthis Strike] Probability: {probability}% from {len(all_articles)} articles")
        
        return jsonify({
            'success': True,
            'probability': probability,  # ← CHANGED: Now returns strike probability
            'threat_level': int(threat_level),  # ← Kept as secondary metric
            'threat_description': threat_desc,
            'momentum': momentum,
            'shipping_incidents': shipping_incidents,
            'total_articles': len(all_articles),
            'recent_articles_48h': scoring_result['breakdown']['recent_articles_48h'],
            'last_updated': datetime.now(timezone.utc).isoformat(),
            'cached': False,
            'version': '2.7.1'
        })
        
    except Exception as e:
        print(f"Error in /api/houthis-threat: {e}")
        import traceback
        traceback.print_exc()
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
    Returns STRIKE PROBABILITY (not just conflict intensity)
    """
    try:
        days = int(request.args.get('days', 7))
        
        if not check_rate_limit():
            return jsonify({
                'success': False,
                'error': 'Rate limit exceeded',
                'probability': 0,  # Changed from intensity
                'rate_limited': True
            }), 200
        
        # Syria-specific keywords - need to add to TARGET_KEYWORDS if not there
        # For now, create a temporary query
        syria_keywords = ['syria', 'syrian', 'damascus', 'assad', 'aleppo', 'idlib']
        query = ' OR '.join(syria_keywords)
        
        # Fetch articles
        articles_en = fetch_newsapi_articles(query, days)
        articles_gdelt_en = fetch_gdelt_articles(query, days, 'eng')
        articles_gdelt_ar = fetch_gdelt_articles(query, days, 'ara')
        
        articles_reddit = fetch_reddit_posts(
            'iran',  # Use Iran subreddits as they often cover Syria
            ['Syria', 'Assad', 'Damascus', 'conflict', 'strike'],
            days
        )
        
        all_articles = (articles_en + articles_gdelt_en + articles_gdelt_ar + articles_reddit)
        
        # Add RSS feeds if available
        try:
            rss_articles = fetch_all_rss()
            all_articles.extend(rss_articles)
            print(f"[Syria Strike] Added {len(rss_articles)} RSS articles")
        except Exception as e:
            print(f"[Syria Strike] RSS fetch failed: {e}")
        
        # Calculate STRIKE PROBABILITY using your scoring algorithm
        # Note: Syria isn't in TARGET_KEYWORDS, so we use 'iran' as baseline
        scoring_result = calculate_threat_probability(all_articles, days, 'iran')
        probability = scoring_result['probability']
        momentum = scoring_result['momentum']
        
        # Calculate conflict intensity as secondary metric
        intensity_score = 0
        escalation_articles = 0
        
        for article in all_articles:
            text = f"{article.get('title', '')} {article.get('description', '')}".lower()
            
            # Check for escalation keywords
            if any(word in text for word in ['strike', 'attack', 'bombing', 'airstrike', 'killed', 'casualties']):
                escalation_articles += 1
                intensity_score += 3
            
            # Weight recent articles more
            try:
                pub_date = article.get('publishedAt', '')
                if pub_date:
                    pub_dt = datetime.fromisoformat(pub_date.replace('Z', '+00:00'))
                    age_hours = (datetime.now(timezone.utc) - pub_dt).total_seconds() / 3600
                    if age_hours <= 48:
                        intensity_score += 2
            except:
                pass
        
        # Normalize to 0-100 scale
        intensity = min(100, int(intensity_score / max(len(all_articles), 1) * 10))
        
        if intensity >= 75:
            intensity_desc = "Very High"
        elif intensity >= 50:
            intensity_desc = "High"
        elif intensity >= 25:
            intensity_desc = "Moderate"
        else:
            intensity_desc = "Low"
        
        print(f"[Syria Strike] Probability: {probability}% from {len(all_articles)} articles")
        
        return jsonify({
            'success': True,
            'probability': probability,  # ← CHANGED: Now returns strike probability
            'intensity': intensity,  # ← Kept as secondary metric
            'intensity_description': intensity_desc,
            'momentum': momentum,
            'total_articles': len(all_articles),
            'escalation_articles': escalation_articles,
            'last_updated': datetime.now(timezone.utc).isoformat(),
            'cached': False,
            'version': '2.7.1'
        })
        
    except Exception as e:
        print(f"Error in /api/syria-conflict: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e),
            'probability': 0,
            'intensity_description': 'Unknown'
        }), 500


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
        
        # Calculate enhanced casualties with trends, cumulative, and estimates
        casualties_enhanced = calculate_casualty_trends(casualties)
        
        articles_per_day = len(all_articles) / days if days > 0 else 0
        intensity_score = min(articles_per_day * 2 + casualties['deaths'] * 0.5, 100)
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
            'version': '2.7.0'
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/scan-lebanon-stability', methods=['GET'])
def scan_lebanon_stability():
    """
    Lebanon Stability Index endpoint
    
    Tracks:
    - Political stability (government formation, elections)
    - Economic stress (bond yields, currency collapse)
    - Hezbollah activity (rearmament, strikes)
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
        
        # Calculate overall stability
        stability = calculate_lebanon_stability(currency_data, bond_data, hezbollah_data)
        
        # NEW: Update cache with today's data
        update_lebanon_cache(
            currency_data, 
            bond_data, 
            hezbollah_data, 
            stability.get('score', 0)
        )
        
        return jsonify({
            'success': True,
            'stability': stability,
            'currency': currency_data,
            'bonds': bond_data,
            'hezbollah': hezbollah_data,
            'government': {
                'has_president': True,
                'president': 'Joseph Aoun',
                'days_with_president': stability.get('days_with_president', 0),
                'president_elected_date': '2025-01-09',
                'parliamentary_election_date': '2026-05-03',
                'days_until_election': stability.get('days_until_election', 0)
            },
            'version': '2.7.0'
        })
        
    except Exception as e:
        print(f"[Lebanon] ❌ Error: {str(e)}")
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
    """Syria conflicts tracker endpoint"""
    try:
        if not check_rate_limit():
            return jsonify({'error': 'Rate limit exceeded'}), 429
        
        days = int(request.args.get('days', 7))
        
        print(f"[Syria Conflicts] Fetching data for {days} days...")
        
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
                'details': conflict_data['details'][:20]
            },
            'articles_syria_direct': syria_direct_articles[:20],
            'articles_sohr': sohr_articles[:20],
            'articles_en': [a for a in all_articles if a.get('language') == 'en'][:20],
            'articles_he': [a for a in all_articles if a.get('language') == 'he'][:20],
            'articles_ar': [a for a in all_articles if a.get('language') == 'ar'][:20],
            'articles_fa': [a for a in all_articles if a.get('language') == 'fa'][:20],
            'articles_reddit': reddit_posts[:20],
            'version': '2.6.3'
        })
        
    except Exception as e:
        print(f"[Syria Conflicts] ERROR: {str(e)}")
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
        
        # NEW: Fetch airline disruptions from RSS monitor (Google News search for specific airlines)
        print("[Flight Cancellations] Calling fetch_airline_disruptions()...")
        rss_disruptions = fetch_airline_disruptions()
        print(f"[Flight Cancellations] RSS monitor returned {len(rss_disruptions)} disruptions")
        
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
