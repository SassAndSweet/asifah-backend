"""
Asifah Analytics — Military Asset & Deployment Tracker v1.0.0
February 18, 2026

Tracks military asset movements across multiple actors and regions.
Feeds deployment scores into existing threat probability calculations.

ACTORS TRACKED:
  Tier 1 (Direct strike correlation):
    - US / CENTCOM
    - Israel / IDF
  Tier 2 (Adversary / Competitor):
    - Iran / IRGC
    - Russia
    - China / PLAN
  Tier 3 (Regional):
    - Saudi Arabia / UAE
    - Turkey
    - Egypt
  Tier 4 (Alliance):
    - NATO (Europe / Arctic expansion)

REGIONS:
  Primary: CENTCOM AOR (Persian Gulf, Red Sea, Eastern Med, Levant)
  Planned: EUCOM (Europe, Arctic/Greenland), INDOPACOM

OUTPUTS:
  - Per-target military posture scores
  - Regional tension multipliers
  - Alert objects for dashboard integration
  - Standalone page data for military.html

COPYRIGHT © 2025-2026 Asifah Analytics. All rights reserved.
"""

# ========================================
# IMPORTS
# ========================================
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
import re
import json
import time
import math
import os

# ========================================
# CONFIGURATION
# ========================================

GDELT_BASE_URL = "http://api.gdeltproject.org/api/v2/doc/doc"
NEWSAPI_KEY = os.environ.get('NEWSAPI_KEY')

# Cache TTL (4 hours — deployments don't change by the minute)
MILITARY_CACHE_FILE = '/tmp/military_tracker_cache.json'
MILITARY_CACHE_TTL_HOURS = 4

# ========================================
# MILITARY ACTORS
# ========================================

MILITARY_ACTORS = {
    # ------------------------------------------------
    # TIER 1 — Direct strike correlation
    # ------------------------------------------------
    'us': {
        'name': 'United States',
        'flag': '🇺🇸',
        'tier': 1,
        'weight': 1.0,
        'feeds_into': ['strike_probability'],
        'keywords': [
            # Command & institutional
            'centcom', 'us central command', 'pentagon deploys',
            'department of defense deployment', 'us forces middle east',
            # Naval
            'carrier strike group', 'uss ', 'us navy gulf', 'us navy middle east',
            'amphibious ready group', 'us destroyer', 'us cruiser',
            'us submarine mediterranean', 'us submarine gulf',
            # Air
            'bomber task force', 'b-1 lancer', 'b-2 spirit', 'b-52 middle east',
            'f-35 deployment middle east', 'f-22 deployment', 'usaf deploys',
            'kc-135', 'kc-46', 'aerial refueling middle east',
            'mq-9 reaper', 'rq-4 global hawk', 'us isr assets',
            # Ground
            'us troops deployed middle east', 'us forces iraq',
            'us forces syria', 'us forces jordan',
            '82nd airborne', '101st airborne middle east',
            'marine expeditionary', 'us special operations',
            # Air defense / missile
            'patriot battery deployed', 'thaad deployment',
            'iron dome us', 'us air defense middle east',
            # Logistics
            'pre-positioned stocks', 'ammunition shipment',
            'military sealift command', 'us logistics middle east'
        ],
        'rss_feeds': [
            # CENTCOM official
            'https://www.centcom.mil/RSS/',
            # Defense media
            'https://www.defense.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=1&Site=945',
        ]
    },

    'israel': {
        'name': 'Israel',
        'flag': '🇮🇱',
        'tier': 1,
        'weight': 0.9,
        'feeds_into': ['strike_probability', 'regional_tension'],
        'keywords': [
            # Ground forces
            'idf mobilization', 'idf mobilisation', 'israel reservists called',
            'israel reserves mobilized', 'idf northern command',
            'idf southern command', 'idf ground operation',
            'idf troops deployed', 'israel military buildup',
            # Air
            'israeli air force exercise', 'iaf exercise', 'iaf drill',
            'f-35 israel', 'f-15 israel', 'israeli airstrike',
            'israel aerial refueling', 'israeli drone strike',
            # Naval
            'israeli navy', 'israel submarine', 'israeli corvette',
            'israel naval blockade', 'israel red sea',
            # Air defense
            'iron dome deployment', 'david sling', 'arrow battery',
            'israel air defense activation', 'iron dome intercept',
            # Intelligence
            'mossad operation', 'shin bet alert', 'aman intelligence',
            'israel intelligence assessment'
        ],
        'rss_feeds': []
    },

    # ------------------------------------------------
    # TIER 2 — Adversary / Competitor
    # ------------------------------------------------
    'iran': {
        'name': 'Iran',
        'flag': '🇮🇷',
        'tier': 2,
        'weight': 0.8,
        'feeds_into': ['reverse_threat', 'regional_tension'],
        'keywords': [
            # IRGC Navy
            'irgc navy', 'irgc naval', 'iranian warship', 'iranian frigate',
            'iranian destroyer', 'iranian submarine', 'iran fast attack craft',
            'bandar abbas naval', 'iran strait of hormuz', 'irgc boats',
            # Missile forces
            'iran missile test', 'iran ballistic missile', 'iran cruise missile',
            'iran missile launch', 'shahab missile', 'fateh missile',
            'emad missile', 'iran hypersonic', 'irgc aerospace force',
            # Air
            'iranian air force', 'iriaf', 'iran drone', 'shahed drone',
            'iran uav', 'iran mohajer', 'iranian fighter jet',
            # Ground / exercises
            'irgc exercise', 'iran military exercise', 'iran war games',
            'irgc ground forces', 'basij mobilization',
            'great prophet exercise', 'iran military drill',
            # Proxy logistics
            'iran weapons shipment', 'iran arms transfer',
            'irgc quds force', 'iran smuggling weapons'
        ],
        'rss_feeds': []
    },

    'china': {
        'name': 'China',
        'flag': '🇨🇳',
        'tier': 2,
        'weight': 0.6,
        'feeds_into': ['regional_tension'],
        'keywords': [
            # Naval
            'plan gulf', 'chinese warship', 'chinese navy persian gulf',
            'pla navy gulf', 'china naval deployment middle east',
            'chinese carrier', 'chinese destroyer gulf',
            'chinese frigate gulf', 'china anti-piracy',
            'chinese submarine indian ocean',
            # Basing
            'djibouti base china', 'china djibouti',
            'china military base', 'china port visit oman',
            'china port visit pakistan', 'gwadar china navy',
            # Intelligence / space
            'china spy ship', 'china surveillance vessel',
            'china intelligence ship', 'yuan wang tracking ship',
            # Exercises
            'china iran naval exercise', 'china russia naval exercise',
            'china military exercise middle east'
        ],
        'rss_feeds': []
    },

    'russia': {
        'name': 'Russia',
        'flag': '🇷🇺',
        'tier': 2,
        'weight': 0.7,
        'feeds_into': ['regional_tension'],
        'keywords': [
            # Mediterranean fleet
            'russian navy mediterranean', 'russian warship mediterranean',
            'russian submarine mediterranean', 'russia med fleet',
            # Syria basing
            'tartus naval base', 'hmeimim air base', 'russia syria deployment',
            'russian forces syria', 'russian air force syria',
            # Naval
            'russian warship', 'russian destroyer', 'russian frigate',
            'russian submarine', 'russia black sea fleet',
            'russia naval exercise', 'russian aircraft carrier',
            # Air
            'russian bomber patrol', 'tu-95 patrol', 'tu-160',
            'russian air force middle east', 'su-35 syria',
            # Arms / support
            'russia arms delivery', 'russia s-300', 'russia s-400',
            'russia weapons syria', 'russia iran military cooperation'
        ],
        'rss_feeds': []
    },

    # ------------------------------------------------
    # TIER 3 — Regional actors
    # ------------------------------------------------
    'saudi_uae': {
        'name': 'Saudi Arabia / UAE',
        'flag': '🇸🇦',
        'tier': 3,
        'weight': 0.5,
        'feeds_into': ['regional_tension'],
        'keywords': [
            'saudi military', 'saudi air force', 'royal saudi navy',
            'saudi air defense', 'saudi patriot', 'saudi thaad',
            'uae forces', 'uae military', 'uae air force',
            'coalition forces yemen', 'saudi yemen border',
            'uae naval', 'saudi naval exercise',
            'saudi arabia military exercise', 'uae military exercise',
            'gulf cooperation council military', 'gcc military exercise'
        ],
        'rss_feeds': []
    },

    'turkey': {
        'name': 'Turkey',
        'flag': '🇹🇷',
        'tier': 3,
        'weight': 0.5,
        'feeds_into': ['regional_tension'],
        'keywords': [
            'turkish military syria', 'turkish forces syria',
            'operation claw', 'turkish navy', 'turkish air force',
            'turkish drone strike', 'bayraktar tb2', 'akinci drone',
            'incirlik air base', 'turkish military exercise',
            'turkish navy mediterranean', 'turkish naval exercise',
            'turkey northern iraq', 'turkey pkk operation',
            'turkish ground operation syria'
        ],
        'rss_feeds': []
    },

    'egypt': {
        'name': 'Egypt',
        'flag': '🇪🇬',
        'tier': 3,
        'weight': 0.4,
        'feeds_into': ['regional_tension'],
        'keywords': [
            'egyptian military', 'egypt military exercise',
            'egyptian navy', 'egypt suez canal military',
            'egypt sinai operation', 'egyptian air force',
            'egypt rafale', 'egypt military buildup',
            'egypt libya border', 'egypt gaza border',
            'egypt israel border troops'
        ],
        'rss_feeds': []
    },

    # ------------------------------------------------
    # TIER 4 — NATO / Alliance (Europe + Arctic expansion)
    # ------------------------------------------------
    'nato': {
        'name': 'NATO',
        'flag': '🏳️',
        'tier': 4,
        'weight': 0.5,
        'feeds_into': ['regional_tension'],
        'keywords': [
            # General NATO ops
            'nato exercise', 'nato deployment', 'nato military exercise',
            'nato forces deployed', 'nato readiness', 'nato response force',
            'nato rapid reaction', 'allied command',
            # Arctic / Greenland
            'nato arctic', 'nato arctic exercise', 'thule air base',
            'pituffik space base', 'greenland military', 'greenland defense',
            'denmark military greenland', 'danish armed forces greenland',
            'arctic military exercise', 'cold response exercise',
            'nato northern flank', 'arctic patrol',
            'us greenland military', 'us arctic strategy',
            'icebreaker arctic', 'arctic surveillance',
            # Baltic / Northern Europe
            'nato baltic', 'nato baltic exercise', 'baltic air policing',
            'nato enhanced forward presence', 'nato eastern flank',
            'nato poland deployment', 'nato romania deployment',
            # Mediterranean
            'nato mediterranean', 'standing nato maritime group',
            'snmg', 'nato sea guardian', 'nato med patrol',
            # General European
            'nato defense spending', 'nato summit',
            'nato article 5', 'nato interoperability',
            'ramstein air base', 'shape nato', 'saceur'
        ],
        'rss_feeds': [
            'https://www.nato.int/cps/en/natohq/news.xml',
        ]
    }
}


# ========================================
# ASSET CATEGORIES & WEIGHTS
# ========================================

ASSET_CATEGORIES = {
    'carrier_strike_group': {
        'label': 'Carrier Strike Group',
        'icon': '🚢',
        'weight': 5.0,
        'description': 'Aircraft carrier + escorts. Maximum power projection.',
        'keywords': [
            'carrier strike group', 'aircraft carrier', 'uss nimitz',
            'uss eisenhower', 'uss ford', 'uss lincoln', 'uss truman',
            'uss roosevelt', 'uss reagan', 'uss vinson', 'uss stennis',
            'uss washington', 'uss bush', 'csg deployed'
        ]
    },
    'submarine': {
        'label': 'Submarine',
        'icon': '🔱',
        'weight': 4.5,
        'description': 'SSBN/SSGN/SSN. Stealth strike capability.',
        'keywords': [
            'submarine deployed', 'submarine gulf', 'submarine mediterranean',
            'ssbn', 'ssgn', 'ohio class', 'virginia class',
            'submarine transit suez', 'submarine indian ocean',
            'guided missile submarine'
        ]
    },
    'bomber_deployment': {
        'label': 'Strategic Bomber',
        'icon': '✈️',
        'weight': 4.0,
        'description': 'B-1/B-2/B-52 deployment signals deep strike readiness.',
        'keywords': [
            'bomber task force', 'b-1 lancer', 'b-1b',
            'b-2 spirit', 'b-2 bomber', 'b-52 stratofortress', 'b-52h',
            'bomber deployment diego garcia', 'bomber deployment middle east',
            'strategic bomber deployed', 'long-range strike'
        ]
    },
    'amphibious_group': {
        'label': 'Amphibious Ready Group',
        'icon': '⚓',
        'weight': 3.5,
        'description': 'Marines + landing ships. Ground intervention capability.',
        'keywords': [
            'amphibious ready group', 'arg deployed', 'marine expeditionary unit',
            'meu deployed', 'amphibious assault ship', 'lhd deployed',
            'lpd deployed', 'dock landing ship'
        ]
    },
    'fighter_surge': {
        'label': 'Fighter Aircraft Surge',
        'icon': '🛩️',
        'weight': 3.0,
        'description': 'Additional fighter squadron deployments.',
        'keywords': [
            'f-35 deployed', 'f-22 deployed', 'f-15 deployed', 'f-16 deployed',
            'fighter squadron deployed', 'additional aircraft',
            'air expeditionary wing', 'fighter surge', 'combat air patrol'
        ]
    },
    'air_defense': {
        'label': 'Air Defense System',
        'icon': '🛡️',
        'weight': 3.0,
        'description': 'Patriot/THAAD/Iron Dome deployment indicates threat preparation.',
        'keywords': [
            'patriot battery deployed', 'thaad deployed', 'thaad battery',
            'iron dome deployed', 'arrow battery', 'david sling deployed',
            'air defense deployment', 'sam battery', 'air defense activation'
        ]
    },
    'isr_assets': {
        'label': 'ISR / Surveillance',
        'icon': '👁️',
        'weight': 2.5,
        'description': 'Intelligence/Surveillance/Recon buildup precedes operations.',
        'keywords': [
            'mq-9 reaper', 'rq-4 global hawk', 'mq-4c triton',
            'p-8 poseidon', 'e-3 awacs', 'rc-135 rivet joint',
            'isr surge', 'surveillance aircraft', 'reconnaissance flight',
            'spy plane', 'intelligence aircraft', 'sigint aircraft'
        ]
    },
    'ground_forces': {
        'label': 'Ground Forces',
        'icon': '🪖',
        'weight': 3.5,
        'description': 'Troop deployments and ground force movements.',
        'keywords': [
            'troops deployed', 'brigade deployed', 'division deployed',
            'battalion deployed', 'special forces deployed',
            'airborne deployed', 'infantry deployed',
            'reservists called up', 'mobilization order',
            'ground forces buildup'
        ]
    },
    'logistics': {
        'label': 'Logistics / Pre-positioning',
        'icon': '📦',
        'weight': 2.0,
        'description': 'Supply buildup often precedes major operations.',
        'keywords': [
            'pre-positioned stocks', 'ammunition shipment',
            'military sealift command', 'logistics buildup',
            'fuel pre-positioning', 'hospital ship deployed',
            'supply chain military', 'c-17 airlift surge',
            'c-5 galaxy deployment', 'military cargo'
        ]
    },
    'missile_test': {
        'label': 'Missile Test / Launch',
        'icon': '🚀',
        'weight': 4.0,
        'description': 'Ballistic/cruise missile tests signal capability and intent.',
        'keywords': [
            'missile test', 'ballistic missile launch', 'cruise missile test',
            'missile exercise', 'rocket launch', 'weapons test',
            'hypersonic test', 'anti-ship missile test'
        ]
    },
    'naval_exercise': {
        'label': 'Naval Exercise',
        'icon': '⚓',
        'weight': 2.0,
        'description': 'Multi-nation or large-scale naval drills.',
        'keywords': [
            'naval exercise', 'maritime exercise', 'naval drill',
            'freedom of navigation', 'multinational naval exercise',
            'combined maritime forces', 'naval war games'
        ]
    }
}


# ========================================
# ASSET → TARGET MAPPING
# ========================================

ASSET_TARGET_MAPPING = {
    # ============================
    # CENTCOM AOR (Middle East)
    # ============================
    'centcom': {
        # Gulf / Iran axis
        'Al Udeid Air Base': {
            'location': 'Qatar',
            'targets': ['iran', 'houthis'],
            'description': 'CENTCOM forward HQ. Primary air ops hub.'
        },
        'Al Dhafra Air Base': {
            'location': 'UAE',
            'targets': ['iran', 'houthis'],
            'description': 'ISR and tanker hub. Iran-facing.'
        },
        'Bahrain Naval Base': {
            'location': 'Bahrain',
            'targets': ['iran'],
            'description': 'US 5th Fleet HQ. Naval ops center.'
        },
        'Diego Garcia': {
            'location': 'British Indian Ocean Territory',
            'targets': ['iran'],
            'description': 'Bomber staging. Deep strike capability vs Iran.'
        },
        'Gulf of Oman': {
            'location': 'Maritime',
            'targets': ['iran'],
            'description': 'Naval presence near Strait of Hormuz.'
        },
        'Persian Gulf': {
            'location': 'Maritime',
            'targets': ['iran'],
            'description': 'Forward naval presence.'
        },
        'Strait of Hormuz': {
            'location': 'Maritime',
            'targets': ['iran'],
            'description': 'Critical oil chokepoint. Maximum tension zone.'
        },

        # Levant / Eastern Med
        'Eastern Mediterranean': {
            'location': 'Maritime',
            'targets': ['lebanon', 'syria', 'hezbollah'],
            'description': 'Carrier ops, Tomahawk range to Levant.'
        },
        'Souda Bay': {
            'location': 'Greece (Crete)',
            'targets': ['lebanon', 'syria'],
            'description': 'Naval support hub for Eastern Med ops.'
        },
        'Akrotiri': {
            'location': 'Cyprus (UK)',
            'targets': ['syria', 'lebanon'],
            'description': 'RAF base. Strike and ISR platform.'
        },

        # Syria / Iraq
        'Al Tanf': {
            'location': 'Syria',
            'targets': ['syria', 'iran'],
            'description': 'US garrison. Syria-Iraq border control.'
        },
        'Al Asad Air Base': {
            'location': 'Iraq',
            'targets': ['syria', 'iran'],
            'description': 'Major US base in western Iraq.'
        },
        'Erbil': {
            'location': 'Iraq (Kurdistan)',
            'targets': ['syria', 'iran'],
            'description': 'US forces in northern Iraq.'
        },

        # Jordan
        'Muwaffaq Salti (Tower 22)': {
            'location': 'Jordan',
            'targets': ['jordan', 'syria'],
            'description': 'US base near Jordan-Syria border.'
        },

        # Red Sea / Yemen
        'Red Sea': {
            'location': 'Maritime',
            'targets': ['houthis', 'yemen'],
            'description': 'Anti-Houthi naval operations.'
        },
        'Bab el-Mandeb': {
            'location': 'Maritime',
            'targets': ['houthis', 'yemen'],
            'description': 'Critical shipping chokepoint.'
        },
        'Camp Lemonnier': {
            'location': 'Djibouti',
            'targets': ['houthis', 'yemen'],
            'description': 'US Africa Command base. Drone and SOF ops.'
        },
    },

    # ============================
    # EUCOM AOR (Europe / Arctic)
    # ============================
    'eucom': {
        'Pituffik Space Base (Thule)': {
            'location': 'Greenland (Denmark)',
            'targets': ['greenland', 'arctic'],
            'description': 'US Space Force. Missile early warning. Arctic presence.'
        },
        'Keflavik': {
            'location': 'Iceland',
            'targets': ['arctic', 'north_atlantic'],
            'description': 'NATO Atlantic / Arctic surveillance.'
        },
        'Ramstein Air Base': {
            'location': 'Germany',
            'targets': ['europe', 'nato_general'],
            'description': 'USAFE HQ. European operations hub.'
        },
        'Rota Naval Station': {
            'location': 'Spain',
            'targets': ['mediterranean', 'nato_general'],
            'description': 'US destroyer forward base.'
        },
        'Sigonella': {
            'location': 'Italy (Sicily)',
            'targets': ['mediterranean', 'libya'],
            'description': 'ISR and maritime patrol hub.'
        },
        'Baltic Region': {
            'location': 'Baltic States',
            'targets': ['nato_eastern_flank'],
            'description': 'NATO enhanced forward presence.'
        },
    }
}


# ========================================
# ALERT THRESHOLDS
# ========================================

ALERT_THRESHOLDS = {
    'normal': {
        'min_score': 0,
        'label': 'Normal',
        'color': 'green',
        'icon': '🟢',
        'dashboard_banner': False
    },
    'elevated': {
        'min_score': 10,
        'label': 'Elevated',
        'color': 'yellow',
        'icon': '🟡',
        'dashboard_banner': True
    },
    'high': {
        'min_score': 25,
        'label': 'High',
        'color': 'orange',
        'icon': '🟠',
        'dashboard_banner': True
    },
    'surge': {
        'min_score': 50,
        'label': 'Surge',
        'color': 'red',
        'icon': '🔴',
        'dashboard_banner': True
    }
}


# ========================================
# DEFENSE MEDIA RSS FEEDS
# ========================================

DEFENSE_RSS_FEEDS = {
    'The War Zone': 'https://www.twz.com/feed',
    'Breaking Defense': 'https://breakingdefense.com/feed/',
    'Defense One': 'https://www.defenseone.com/rss/all/',
    'Naval News': 'https://www.navalnews.com/feed/',
    'Stars and Stripes': 'https://www.stripes.com/rss',
    'Military Times': 'https://www.militarytimes.com/arc/outboundfeeds/rss/?outputType=xml',
    'CENTCOM': 'https://www.centcom.mil/RSS/',
    'NATO News': 'https://www.nato.int/cps/en/natohq/news.xml',
    'DVIDS': 'https://www.dvidshub.net/rss/news',
}

REDDIT_MILITARY_SUBREDDITS = [
    'CredibleDefense', 'LessCredibleDefence', 'geopolitics',
    'Military', 'WarCollege', 'navy', 'AirForce',
    'NCD', 'DefenseNews'
]

REDDIT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


# ========================================
# CACHE MANAGEMENT
# ========================================

def load_military_cache():
    """Load cached military tracker data"""
    try:
        from pathlib import Path
        if Path(MILITARY_CACHE_FILE).exists():
            with open(MILITARY_CACHE_FILE, 'r') as f:
                cache = json.load(f)
                return cache
        return {}
    except Exception as e:
        print(f"[Military Cache] Error loading: {e}")
        return {}


def save_military_cache(data):
    """Save military tracker data to cache"""
    try:
        data['cached_at'] = datetime.now(timezone.utc).isoformat()
        with open(MILITARY_CACHE_FILE, 'w') as f:
            json.dump(data, f, indent=2)
        print(f"[Military Cache] Saved cache")
    except Exception as e:
        print(f"[Military Cache] Error saving: {e}")


def is_military_cache_fresh():
    """Check if military cache is still valid"""
    try:
        cache = load_military_cache()
        if not cache or 'cached_at' not in cache:
            return False
        cached_at = datetime.fromisoformat(cache['cached_at'])
        age = datetime.now(timezone.utc) - cached_at
        return age.total_seconds() < (MILITARY_CACHE_TTL_HOURS * 3600)
    except:
        return False


# ========================================
# DATA FETCHING — RSS FEEDS
# ========================================

def fetch_defense_rss(feed_name, feed_url, max_articles=15):
    """
    Fetch articles from a defense media RSS feed
    Returns standardized article objects
    """
    articles = []

    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = requests.get(feed_url, headers=headers, timeout=15)

        if response.status_code != 200:
            print(f"[Military RSS] {feed_name}: HTTP {response.status_code}")
            return []

        root = ET.fromstring(response.content)
        items = root.findall('.//item')

        for item in items[:max_articles]:
            title_elem = item.find('title')
            link_elem = item.find('link')
            pubDate_elem = item.find('pubDate')
            desc_elem = item.find('description')
            content_elem = item.find('{http://purl.org/rss/1.0/modules/content/}encoded')

            if title_elem is None or link_elem is None:
                continue

            pub_date = ''
            if pubDate_elem is not None and pubDate_elem.text:
                try:
                    pub_date = parsedate_to_datetime(pubDate_elem.text).isoformat()
                except:
                    pub_date = datetime.now(timezone.utc).isoformat()

            description = ''
            if desc_elem is not None and desc_elem.text:
                description = desc_elem.text[:500]
            elif content_elem is not None and content_elem.text:
                description = content_elem.text[:500]

            articles.append({
                'title': title_elem.text or '',
                'description': description,
                'url': link_elem.text or '',
                'publishedAt': pub_date,
                'source': {'name': feed_name},
                'content': description,
                'feed_type': 'defense_rss'
            })

        print(f"[Military RSS] {feed_name}: ✓ {len(articles)} articles")
        return articles

    except ET.ParseError as e:
        print(f"[Military RSS] {feed_name}: XML parse error: {str(e)[:100]}")
        return []
    except Exception as e:
        print(f"[Military RSS] {feed_name}: Error: {str(e)[:100]}")
        return []


def fetch_all_defense_rss():
    """Fetch articles from all configured defense RSS feeds"""
    all_articles = []

    for feed_name, feed_url in DEFENSE_RSS_FEEDS.items():
        articles = fetch_defense_rss(feed_name, feed_url)
        all_articles.extend(articles)
        time.sleep(0.5)  # Rate limit courtesy

    print(f"[Military RSS] Total defense RSS articles: {len(all_articles)}")
    return all_articles


# ========================================
# DATA FETCHING — GDELT (Military-specific queries)
# ========================================

def fetch_gdelt_military(query, days=7, language='eng'):
    """Fetch military-related articles from GDELT"""
    try:
        params = {
            'query': query,
            'mode': 'artlist',
            'maxrecords': 50,
            'timespan': f'{days}d',
            'format': 'json',
            'sourcelang': language
        }

        response = requests.get(GDELT_BASE_URL, params=params, timeout=15)

        if response.status_code != 200:
            return []

        data = response.json()
        articles = data.get('articles', [])

        standardized = []
        for article in articles:
            standardized.append({
                'title': article.get('title', ''),
                'description': article.get('title', ''),
                'url': article.get('url', ''),
                'publishedAt': article.get('seendate', ''),
                'source': {'name': article.get('domain', 'GDELT')},
                'content': article.get('title', ''),
                'feed_type': 'gdelt'
            })

        return standardized

    except Exception as e:
        print(f"[Military GDELT] Error: {str(e)[:100]}")
        return []


def fetch_all_gdelt_military(days=7):
    """Fetch military articles from GDELT across multiple queries"""
    queries = [
        'military deployment middle east',
        'carrier strike group persian gulf',
        'military exercise middle east',
        'troops deployed middle east',
        'naval deployment mediterranean',
        'irgc military exercise',
        'chinese warship persian gulf',
        'russian navy mediterranean',
        'nato exercise arctic',
        'nato military deployment',
        'greenland military defense',
    ]

    all_articles = []

    for query in queries:
        articles = fetch_gdelt_military(query, days)
        all_articles.extend(articles)
        time.sleep(0.3)

    print(f"[Military GDELT] Total GDELT military articles: {len(all_articles)}")
    return all_articles


# ========================================
# DATA FETCHING — NewsAPI (Military-specific)
# ========================================

def fetch_newsapi_military(query, days=7):
    """Fetch military articles from NewsAPI"""
    if not NEWSAPI_KEY:
        return []

    from_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

    url = "https://newsapi.org/v2/everything"
    params = {
        'q': query,
        'from': from_date,
        'sortBy': 'publishedAt',
        'language': 'en',
        'apiKey': NEWSAPI_KEY,
        'pageSize': 50
    }

    try:
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            articles = data.get('articles', [])
            for a in articles:
                a['feed_type'] = 'newsapi'
            return articles
        return []
    except:
        return []


def fetch_all_newsapi_military(days=7):
    """Fetch military articles from NewsAPI across key queries"""
    queries = [
        'military deployment Middle East',
        'carrier strike group Gulf',
        'US troops deployed',
        'IRGC military exercise',
        'NATO exercise',
    ]

    all_articles = []
    for query in queries:
        articles = fetch_newsapi_military(query, days)
        all_articles.extend(articles)
        time.sleep(0.3)

    print(f"[Military NewsAPI] Total articles: {len(all_articles)}")
    return all_articles


# ========================================
# DATA FETCHING — Reddit
# ========================================

def fetch_reddit_military(days=7):
    """Fetch military-related Reddit posts"""
    all_posts = []
    keywords = ['deployment', 'military', 'carrier', 'strike group', 'NATO', 'CENTCOM']
    query = " OR ".join(keywords[:3])

    time_filter = "week" if days <= 7 else "month"

    for subreddit in REDDIT_MILITARY_SUBREDDITS[:5]:  # Limit to avoid rate limits
        try:
            url = f"https://www.reddit.com/r/{subreddit}/search.json"
            params = {
                "q": query,
                "restrict_sr": "true",
                "sort": "new",
                "t": time_filter,
                "limit": 15
            }
            headers = {"User-Agent": REDDIT_USER_AGENT}

            time.sleep(2)
            response = requests.get(url, params=params, headers=headers, timeout=10)

            if response.status_code == 200:
                data = response.json()
                if "data" in data and "children" in data["data"]:
                    for post in data["data"]["children"]:
                        post_data = post.get("data", {})
                        all_posts.append({
                            'title': post_data.get('title', '')[:200],
                            'description': post_data.get('selftext', '')[:300],
                            'url': f"https://www.reddit.com{post_data.get('permalink', '')}",
                            'publishedAt': datetime.fromtimestamp(
                                post_data.get('created_utc', 0),
                                tz=timezone.utc
                            ).isoformat(),
                            'source': {'name': f'r/{subreddit}'},
                            'content': post_data.get('selftext', ''),
                            'feed_type': 'reddit'
                        })

        except Exception:
            continue

    print(f"[Military Reddit] Total posts: {len(all_posts)}")
    return all_posts


# ========================================
# CORE ANALYSIS ENGINE
# ========================================

def analyze_article_military(article):
    """
    Analyze a single article for military deployment signals.

    Returns:
    {
        'actors': ['us', 'iran'],
        'asset_types': ['carrier_strike_group', 'fighter_surge'],
        'regions': ['persian_gulf'],
        'targets': ['iran'],
        'score': 8.5,
        'signals': [
            {'actor': 'us', 'asset': 'carrier_strike_group',
             'keyword': 'carrier strike group', 'weight': 5.0}
        ]
    }
    """
    title = (article.get('title') or '').lower()
    description = (article.get('description') or '').lower()
    content = (article.get('content') or '').lower()
    text = f"{title} {description} {content}"

    result = {
        'actors': set(),
        'asset_types': set(),
        'regions': set(),
        'targets': set(),
        'score': 0,
        'signals': []
    }

    # 1. Detect which military actors are mentioned
    for actor_id, actor_data in MILITARY_ACTORS.items():
        for keyword in actor_data['keywords']:
            if keyword in text:
                result['actors'].add(actor_id)
                actor_weight = actor_data['weight']

                # 2. Detect asset type
                asset_matched = False
                for asset_id, asset_data in ASSET_CATEGORIES.items():
                    for asset_kw in asset_data['keywords']:
                        if asset_kw in text:
                            result['asset_types'].add(asset_id)
                            signal_score = asset_data['weight'] * actor_weight

                            result['signals'].append({
                                'actor': actor_id,
                                'actor_name': actor_data['name'],
                                'actor_flag': actor_data['flag'],
                                'asset': asset_id,
                                'asset_label': asset_data['label'],
                                'asset_icon': asset_data['icon'],
                                'keyword': asset_kw,
                                'actor_keyword': keyword,
                                'weight': round(signal_score, 2),
                                'article_title': article.get('title', '')[:120],
                                'article_url': article.get('url', ''),
                                'source': article.get('source', {}).get('name', 'Unknown'),
                                'published': article.get('publishedAt', '')
                            })

                            result['score'] += signal_score
                            asset_matched = True
                            break  # One asset match per category per article

                    if asset_matched:
                        break

                # If actor detected but no specific asset, still count it
                if not asset_matched:
                    result['signals'].append({
                        'actor': actor_id,
                        'actor_name': actor_data['name'],
                        'actor_flag': actor_data['flag'],
                        'asset': 'unspecified',
                        'asset_label': 'Military Activity',
                        'asset_icon': '⚠️',
                        'keyword': keyword,
                        'actor_keyword': keyword,
                        'weight': round(actor_weight * 1.0, 2),
                        'article_title': article.get('title', '')[:120],
                        'article_url': article.get('url', ''),
                        'source': article.get('source', {}).get('name', 'Unknown'),
                        'published': article.get('publishedAt', '')
                    })
                    result['score'] += actor_weight

                break  # One keyword match per actor per article

    # 3. Map to target regions
    for aor, bases in ASSET_TARGET_MAPPING.items():
        for base_name, base_data in bases.items():
            if base_name.lower() in text:
                result['regions'].add(base_name)
                for target in base_data['targets']:
                    result['targets'].add(target)

    # Convert sets to lists for JSON
    result['actors'] = list(result['actors'])
    result['asset_types'] = list(result['asset_types'])
    result['regions'] = list(result['regions'])
    result['targets'] = list(result['targets'])
    result['score'] = round(result['score'], 2)

    return result


def calculate_regional_tension_multiplier(active_actors):
    """
    Multiple militaries moving simultaneously = compounding tension.

    1 actor  = 1.0x  (baseline)
    2 actors = 1.15x
    3 actors = 1.3x
    4+ actors = 1.5x (cap)
    """
    count = len(active_actors)
    if count <= 1:
        return 1.0
    elif count == 2:
        return 1.15
    elif count == 3:
        return 1.3
    else:
        return 1.5


def determine_alert_level(score):
    """Convert raw score to alert level"""
    if score >= ALERT_THRESHOLDS['surge']['min_score']:
        return 'surge'
    elif score >= ALERT_THRESHOLDS['high']['min_score']:
        return 'high'
    elif score >= ALERT_THRESHOLDS['elevated']['min_score']:
        return 'elevated'
    else:
        return 'normal'


# ========================================
# MAIN SCAN FUNCTION
# ========================================

def scan_military_posture(days=7, force_refresh=False):
    """
    Main entry point. Scans all sources, analyzes articles,
    and returns comprehensive military posture assessment.

    Called by:
    - /api/military-posture endpoint (standalone page)
    - get_military_posture(target) helper (dashboard integration)
    """

    # Check cache first
    if not force_refresh and is_military_cache_fresh():
        cache = load_military_cache()
        print("[Military Tracker] Returning cached data")
        return cache

    print(f"[Military Tracker] Starting fresh scan ({days} days)...")
    scan_start = time.time()

    # ========================================
    # FETCH FROM ALL SOURCES
    # ========================================
    print("[Military Tracker] Phase 1: Fetching data...")

    rss_articles = fetch_all_defense_rss()
    gdelt_articles = fetch_all_gdelt_military(days)
    newsapi_articles = fetch_all_newsapi_military(days)
    reddit_posts = fetch_reddit_military(days)

    all_articles = rss_articles + gdelt_articles + newsapi_articles + reddit_posts

    print(f"[Military Tracker] Total articles to analyze: {len(all_articles)}")

    # ========================================
    # ANALYZE ALL ARTICLES
    # ========================================
    print("[Military Tracker] Phase 2: Analyzing articles...")

    all_signals = []
    per_target_scores = {}  # target → total score
    per_actor_scores = {}   # actor → total score
    active_actors = set()
    asset_type_counts = {}

    for article in all_articles:
        analysis = analyze_article_military(article)

        if analysis['signals']:
            for signal in analysis['signals']:
                all_signals.append(signal)
                active_actors.add(signal['actor'])

                # Accumulate per-target scores
                for target in analysis['targets']:
                    per_target_scores[target] = per_target_scores.get(target, 0) + signal['weight']

                # Accumulate per-actor scores
                actor = signal['actor']
                per_actor_scores[actor] = per_actor_scores.get(actor, 0) + signal['weight']

                # Count asset types
                asset = signal['asset']
                asset_type_counts[asset] = asset_type_counts.get(asset, 0) + 1

    # ========================================
    # CALCULATE REGIONAL TENSION MULTIPLIER
    # ========================================
    tension_multiplier = calculate_regional_tension_multiplier(active_actors)

    print(f"[Military Tracker] Active actors: {len(active_actors)} → Tension multiplier: {tension_multiplier}x")

    # Apply tension multiplier to target scores
    for target in per_target_scores:
        per_target_scores[target] = round(per_target_scores[target] * tension_multiplier, 2)

    # ========================================
    # BUILD PER-TARGET POSTURE ASSESSMENTS
    # ========================================
    target_postures = {}

    for target, score in per_target_scores.items():
        alert_level = determine_alert_level(score)
        threshold = ALERT_THRESHOLDS[alert_level]

        # Get top signals for this target
        target_signals = [
            s for s in all_signals
            if target in analyze_article_military({
                'title': s.get('article_title', ''),
                'description': '',
                'content': ''
            }).get('targets', [])
            or s.get('actor_keyword', '') in str(ASSET_TARGET_MAPPING.get('centcom', {}))
        ]

        # Simpler approach: get signals from actors that map to this target
        relevant_signals = sorted(all_signals, key=lambda x: x['weight'], reverse=True)

        target_postures[target] = {
            'score': score,
            'alert_level': alert_level,
            'alert_label': threshold['label'],
            'alert_color': threshold['color'],
            'alert_icon': threshold['icon'],
            'show_banner': threshold['dashboard_banner'],
            'top_signals': relevant_signals[:5],
            'tension_multiplier': tension_multiplier
        }

    # ========================================
    # BUILD PER-ACTOR SUMMARIES
    # ========================================
    actor_summaries = {}

    for actor_id, score in per_actor_scores.items():
        actor_data = MILITARY_ACTORS.get(actor_id, {})
        actor_signals = [s for s in all_signals if s['actor'] == actor_id]
        actor_signals.sort(key=lambda x: x['weight'], reverse=True)

        actor_summaries[actor_id] = {
            'name': actor_data.get('name', actor_id),
            'flag': actor_data.get('flag', ''),
            'tier': actor_data.get('tier', 99),
            'total_score': round(score, 2),
            'signal_count': len(actor_signals),
            'top_signals': actor_signals[:5],
            'alert_level': determine_alert_level(score)
        }

    # ========================================
    # BUILD RESPONSE
    # ========================================
    scan_time = round(time.time() - scan_start, 1)

    result = {
        'success': True,
        'scan_time_seconds': scan_time,
        'days_analyzed': days,
        'total_articles_scanned': len(all_articles),
        'total_signals_detected': len(all_signals),
        'active_actors': list(active_actors),
        'active_actor_count': len(active_actors),
        'tension_multiplier': tension_multiplier,

        # Per-target posture (for dashboard integration)
        'target_postures': target_postures,

        # Per-actor breakdown (for standalone page)
        'actor_summaries': actor_summaries,

        # Asset type distribution
        'asset_distribution': asset_type_counts,

        # Top signals overall (for standalone page)
        'top_signals': sorted(all_signals, key=lambda x: x['weight'], reverse=True)[:20],

        # Source breakdown
        'source_breakdown': {
            'defense_rss': len(rss_articles),
            'gdelt': len(gdelt_articles),
            'newsapi': len(newsapi_articles),
            'reddit': len(reddit_posts)
        },

        'last_updated': datetime.now(timezone.utc).isoformat(),
        'cached': False,
        'version': '1.0.0'
    }

    # Save to cache
    save_military_cache(result)

    print(f"[Military Tracker] ✅ Scan complete in {scan_time}s")
    print(f"[Military Tracker]    Signals: {len(all_signals)}, Actors: {len(active_actors)}, Targets: {len(target_postures)}")

    return result


# ========================================
# DASHBOARD INTEGRATION HELPER
# ========================================

def get_military_posture(target):
    """
    Quick lookup for a specific target's military posture.

    Called by existing threat endpoints:
      probability += posture['military_bonus']

    Returns:
    {
        'alert_level': 'elevated',
        'alert_label': 'Elevated',
        'alert_color': 'yellow',
        'military_bonus': 5,
        'show_banner': True,
        'banner_text': '⚠️ Elevated US naval presence in Gulf of Oman',
        'detail_url': '/military.html',
        'top_signals': [...]
    }
    """
    try:
        data = scan_military_posture()  # Uses cache if fresh

        posture = data.get('target_postures', {}).get(target, {})

        if not posture:
            return {
                'alert_level': 'normal',
                'alert_label': 'Normal',
                'alert_color': 'green',
                'military_bonus': 0,
                'show_banner': False,
                'banner_text': '',
                'detail_url': '/military.html',
                'top_signals': []
            }

        # Convert alert level to probability bonus
        bonus_map = {
            'normal': 0,
            'elevated': 5,
            'high': 10,
            'surge': 15
        }

        alert_level = posture.get('alert_level', 'normal')
        military_bonus = bonus_map.get(alert_level, 0)

        # Build banner text from top signal
        banner_text = ''
        top_signals = posture.get('top_signals', [])
        if top_signals and posture.get('show_banner'):
            top = top_signals[0]
            banner_text = (
                f"{ALERT_THRESHOLDS[alert_level]['icon']} "
                f"MILITARY POSTURE: {top.get('actor_flag', '')} "
                f"{top.get('asset_label', 'Activity')} detected — "
                f"{top.get('article_title', '')[:80]}"
            )

        return {
            'alert_level': alert_level,
            'alert_label': posture.get('alert_label', 'Normal'),
            'alert_color': posture.get('alert_color', 'green'),
            'military_bonus': military_bonus,
            'show_banner': posture.get('show_banner', False),
            'banner_text': banner_text,
            'detail_url': '/military.html',
            'top_signals': top_signals[:3],
            'tension_multiplier': data.get('tension_multiplier', 1.0),
            'active_actors': data.get('active_actors', [])
        }

    except Exception as e:
        print(f"[Military Posture] Error for {target}: {str(e)[:200]}")
        return {
            'alert_level': 'normal',
            'military_bonus': 0,
            'show_banner': False,
            'banner_text': '',
            'detail_url': '/military.html',
            'top_signals': [],
            'error': str(e)[:100]
        }


# ========================================
# FLASK ENDPOINT REGISTRATION
# ========================================

def register_military_endpoints(app):
    """
    Register military tracker endpoints with the Flask app.
    Called from main app.py: register_military_endpoints(app)
    """

    @app.route('/api/military-posture', methods=['GET'])
    def api_military_posture():
        """
        Full military posture assessment.
        Used by standalone military.html page.
        """
        try:
            days = int(app.current_request.args.get('days', 7)) if hasattr(app, 'current_request') else 7
            refresh = False

            from flask import request as flask_request
            days = int(flask_request.args.get('days', 7))
            refresh = flask_request.args.get('refresh', 'false').lower() == 'true'

            result = scan_military_posture(days=days, force_refresh=refresh)
            return app.response_class(
                response=json.dumps(result, default=str),
                status=200,
                mimetype='application/json'
            )

        except Exception as e:
            print(f"[Military API] Error: {str(e)}")
            import traceback
            traceback.print_exc()
            return app.response_class(
                response=json.dumps({
                    'success': False,
                    'error': str(e)[:200]
                }),
                status=500,
                mimetype='application/json'
            )

    @app.route('/api/military-posture/<target>', methods=['GET'])
    def api_military_posture_target(target):
        """
        Quick posture check for a specific target.
        Used by dashboard threat cards.
        """
        try:
            posture = get_military_posture(target)
            return app.response_class(
                response=json.dumps(posture, default=str),
                status=200,
                mimetype='application/json'
            )

        except Exception as e:
            return app.response_class(
                response=json.dumps({
                    'success': False,
                    'error': str(e)[:200]
                }),
                status=500,
                mimetype='application/json'
            )

    print("[Military Tracker] ✅ Endpoints registered: /api/military-posture, /api/military-posture/<target>")
