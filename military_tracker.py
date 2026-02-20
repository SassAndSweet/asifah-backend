"""
Asifah Analytics — Military Asset & Deployment Tracker v2.1.0
February 20, 2026

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
  Tier 3 (Regional — Middle East):
    - Saudi Arabia
    - UAE
    - Jordan
    - Qatar
    - Kuwait
    - Egypt
    - Turkey
  Tier 3 (Regional — Europe):
    - Ukraine
  Tier 4 (Alliance):
    - NATO (Europe / Arctic expansion)

REGIONS:
  Primary: CENTCOM AOR (Persian Gulf, Red Sea, Eastern Med, Levant)
  Secondary: EUCOM (Europe, Arctic/Greenland, Black Sea, Ukraine)
  Planned: INDOPACOM

REGIONAL GROUPINGS (for frontend display):
  - Asia & The Pacific Theatre
  - European Theatre
  - Middle East & North Africa

OUTPUTS:
  - Per-target military posture scores
  - Regional tension multipliers
  - Location-aware context scoring
  - Alert objects for dashboard integration
  - Standalone page data for military.html

CHANGELOG:
  v2.1.0 - Multilingual intelligence expansion:
           * Added GDELT queries in 8 languages: Hebrew, Russian, Arabic,
             Farsi, Turkish, Ukrainian, French, Chinese
           * Added 15 new RSS feeds: Jerusalem Post, Times of Israel, Ynet,
             Israel Hayom, Al Jazeera, Al Arabiya, MEE, TASS, Moscow Times,
             Daily Sabah, TRT World, Kyiv Independent, Ukrinform,
             Iran International, Tasnim
           * Added missing English GDELT queries for Israel/IDF, Egypt, Turkey
           * Expanded English GDELT queries from 25 to 44
           * Total GDELT queries now 92 across 9 languages
  v2.0.0 - Major rewrite:
           * Added base evacuation / drawdown asset category with tiered weights
           * Added location multipliers for hotspot scoring
           * Added context-aware scoring (adversary exercises during buildup)
           * Expanded actors: Ukraine, split Saudi/UAE/Jordan/Qatar/Kuwait
           * Added regional theatre groupings for frontend
           * Expanded GDELT and RSS queries for new coverage
           * Added EUCOM target mapping (Ukraine, Black Sea, Baltic)
  v1.0.1 - Added CORS headers to all endpoint responses
  v1.0.0 - Initial release

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
# REGIONAL THEATRE GROUPINGS (for frontend)
# ========================================

REGIONAL_THEATRES = {
    'asia_pacific': {
        'label': 'Asia & The Pacific Theatre',
        'icon': '🌏',
        'order': 1,
        'actors': ['china'],
        'description': 'INDOPACOM area — China/PLAN activity, South China Sea, Indian Ocean'
    },
    'europe': {
        'label': 'European Theatre',
        'icon': '🌍',
        'order': 2,
        'actors': ['nato', 'russia', 'turkey', 'ukraine'],
        'description': 'EUCOM area — NATO, Russia, Arctic, Black Sea, Ukraine'
    },
    'middle_east': {
        'label': 'Middle East & North Africa',
        'icon': '🕌',
        'order': 3,
        'actors': ['us', 'israel', 'iran', 'egypt', 'jordan', 'kuwait', 'qatar', 'saudi_arabia', 'uae'],
        'description': 'CENTCOM area — Persian Gulf, Red Sea, Eastern Med, Levant'
    }
}


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
        'theatre': 'middle_east',
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
            'military sealift command', 'us logistics middle east',
            # Buildup / surge language
            'us military buildup', 'us force posture', 'us surge middle east',
            'massive fleet', 'armada', 'combat power',
            'us military assets middle east', 'military assets flock'
        ],
        'rss_feeds': [
            'https://www.centcom.mil/RSS/',
            'https://www.defense.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=1&Site=945',
        ]
    },

    'israel': {
        'name': 'Israel',
        'flag': '🇮🇱',
        'tier': 1,
        'theatre': 'middle_east',
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
        'theatre': 'middle_east',
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
            'iran drills', 'iran naval drill', 'iran naval exercise',
            # Proxy logistics
            'iran weapons shipment', 'iran arms transfer',
            'irgc quds force', 'iran smuggling weapons',
            # Threats & posturing
            'iran threatens', 'iran retaliation', 'iran warns',
            'iranian bases within range', 'iran retaliatory strike',
            'iran nuclear weapon', 'iran enrichment',
            'iranian defense minister'
        ],
        'rss_feeds': []
    },

    'china': {
        'name': 'China',
        'flag': '🇨🇳',
        'tier': 2,
        'theatre': 'asia_pacific',
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
            'china military exercise middle east',
            # Indo-Pacific
            'south china sea military', 'taiwan strait military',
            'pla exercise', 'chinese military exercise',
            'china naval gun', 'plan warship'
        ],
        'rss_feeds': []
    },

    'russia': {
        'name': 'Russia',
        'flag': '🇷🇺',
        'tier': 2,
        'theatre': 'europe',
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
            'russia weapons syria', 'russia iran military cooperation',
            # Ukraine theatre
            'russian offensive ukraine', 'russia ukraine front',
            'russian forces ukraine', 'russia mobilization',
            'russian missile ukraine', 'russia drone ukraine',
            'russian artillery ukraine', 'wagner group',
            'russia nuclear posture', 'russia nuclear threat',
            # Black Sea
            'russia black sea', 'russian black sea fleet',
            'sevastopol naval base', 'crimea military',
            # Arctic
            'russia arctic military', 'northern fleet',
            'russia arctic exercise'
        ],
        'rss_feeds': []
    },

    # ------------------------------------------------
    # TIER 3 — Regional actors (Middle East)
    # ------------------------------------------------
    'saudi_arabia': {
        'name': 'Saudi Arabia',
        'flag': '🇸🇦',
        'tier': 3,
        'theatre': 'middle_east',
        'weight': 0.5,
        'feeds_into': ['regional_tension'],
        'keywords': [
            'saudi military', 'saudi air force', 'royal saudi navy',
            'saudi air defense', 'saudi patriot', 'saudi thaad',
            'saudi arabia military exercise', 'saudi naval exercise',
            'saudi yemen border', 'saudi military buildup',
            'saudi defense spending', 'saudi arms deal',
            'saudi intercept', 'saudi houthi',
            'us cargo planes saudi', 'saudi base'
        ],
        'rss_feeds': []
    },

    'uae': {
        'name': 'United Arab Emirates',
        'flag': '🇦🇪',
        'tier': 3,
        'theatre': 'middle_east',
        'weight': 0.5,
        'feeds_into': ['regional_tension'],
        'keywords': [
            'uae forces', 'uae military', 'uae air force',
            'uae naval', 'uae military exercise',
            'al dhafra air base', 'uae defense',
            'uae arms deal', 'uae military buildup',
            'uae evacuation', 'uae departure',
            'emirates military', 'uae drone'
        ],
        'rss_feeds': []
    },

    'jordan': {
        'name': 'Jordan',
        'flag': '🇯🇴',
        'tier': 3,
        'theatre': 'middle_east',
        'weight': 0.5,
        'feeds_into': ['regional_tension'],
        'keywords': [
            'jordan military', 'jordanian armed forces',
            'muwaffaq salti', 'tower 22', 'jordan air base',
            'jordan border', 'jordan syria border',
            'f-15 jordan', 'us forces jordan',
            'jordan military exercise', 'jordan defense',
            'jordan intercept', 'jordan air defense',
            'eager lion exercise', 'jordan base',
            'us cargo planes jordan', 'strike eagles jordan'
        ],
        'rss_feeds': []
    },

    'qatar': {
        'name': 'Qatar',
        'flag': '🇶🇦',
        'tier': 3,
        'theatre': 'middle_east',
        'weight': 0.5,
        'feeds_into': ['regional_tension'],
        'keywords': [
            'al udeid air base', 'al udeid', 'qatar base',
            'centcom forward headquarters', 'centcom hq qatar',
            'qatar military', 'qatar defense',
            'qatar air base evacuation', 'al udeid evacuation',
            'qatar military exercise', 'us forces qatar'
        ],
        'rss_feeds': []
    },

    'kuwait': {
        'name': 'Kuwait',
        'flag': '🇰🇼',
        'tier': 3,
        'theatre': 'middle_east',
        'weight': 0.4,
        'feeds_into': ['regional_tension'],
        'keywords': [
            'camp arifjan', 'kuwait military', 'kuwait base',
            'us forces kuwait', 'kuwait defense',
            'ali al salem air base', 'kuwait evacuation',
            'kuwait military exercise'
        ],
        'rss_feeds': []
    },

    'egypt': {
        'name': 'Egypt',
        'flag': '🇪🇬',
        'tier': 3,
        'theatre': 'middle_east',
        'weight': 0.4,
        'feeds_into': ['regional_tension'],
        'keywords': [
            'egyptian military', 'egypt military exercise',
            'egyptian navy', 'egypt suez canal military',
            'egypt sinai operation', 'egyptian air force',
            'egypt rafale', 'egypt military buildup',
            'egypt libya border', 'egypt gaza border',
            'egypt israel border troops', 'bright star exercise'
        ],
        'rss_feeds': []
    },

    'turkey': {
        'name': 'Turkey',
        'flag': '🇹🇷',
        'tier': 3,
        'theatre': 'europe',
        'weight': 0.5,
        'feeds_into': ['regional_tension'],
        'keywords': [
            'turkish military syria', 'turkish forces syria',
            'operation claw', 'turkish navy', 'turkish air force',
            'turkish drone strike', 'bayraktar tb2', 'akinci drone',
            'incirlik air base', 'turkish military exercise',
            'turkish navy mediterranean', 'turkish naval exercise',
            'turkey northern iraq', 'turkey pkk operation',
            'turkish ground operation syria',
            'turkey nato', 'turkish military nato'
        ],
        'rss_feeds': []
    },

    # ------------------------------------------------
    # TIER 3 — Regional actors (Europe)
    # ------------------------------------------------
    'ukraine': {
        'name': 'Ukraine',
        'flag': '🇺🇦',
        'tier': 3,
        'theatre': 'europe',
        'weight': 0.6,
        'feeds_into': ['regional_tension'],
        'keywords': [
            # Military operations
            'ukraine military', 'ukrainian armed forces',
            'ukraine offensive', 'ukraine counteroffensive',
            'ukraine front line', 'ukraine defense',
            # Specific regions
            'zaporizhzhia front', 'kherson front', 'bakhmut',
            'kursk incursion', 'ukraine kursk',
            'donetsk front', 'luhansk front',
            # Weapons & systems
            'ukraine f-16', 'ukraine patriot', 'ukraine air defense',
            'ukraine himars', 'ukraine storm shadow',
            'ukraine atacms', 'ukraine drone warfare',
            'ukraine long range strike', 'ukraine missile',
            # Naval
            'ukraine black sea', 'ukraine naval drone',
            'ukraine anti-ship', 'ukraine sea drone',
            # Arms deliveries
            'ukraine arms delivery', 'ukraine weapons package',
            'ukraine military aid', 'ukraine ammunition',
            'ukraine defense package',
            # Mobilization
            'ukraine mobilization', 'ukraine conscription',
            'ukraine reserves', 'ukraine recruitment'
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
        'theatre': 'europe',
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
            'ramstein air base', 'shape nato', 'saceur',
            # Ukraine support
            'nato ukraine', 'nato aid ukraine', 'ramstein format'
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
            'guided missile submarine', 'uss georgia', 'uss florida',
            'uss ohio', 'uss michigan'
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
            'strategic bomber deployed', 'long-range strike',
            'b-2 stealth bomber', 'long-range mission'
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
            'air expeditionary wing', 'fighter surge', 'combat air patrol',
            'f-15e strike eagle', 'strike eagles deployed',
            'expeditionary fighter squadron', 'fighter wing deployed'
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
            'air defense deployment', 'sam battery', 'air defense activation',
            'patriot missile defense', 'air defense coordination',
            'mead-cdoc', 'air defense cell'
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
            'spy plane', 'intelligence aircraft', 'sigint aircraft',
            'rc-135w', 'electronic emissions', 'flight tracking military'
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
            'c-5 galaxy deployment', 'military cargo',
            'cargo planes flowing', 'c-130 airlift',
            'airlift surge', 'logistics surge'
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
            'hypersonic test', 'anti-ship missile test',
            'tomahawk launch', 'missile salvo'
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
    },

    # ================================================
    # NEW: Base Evacuation / Drawdown (v2.0)
    # ================================================
    'base_evacuation': {
        'label': 'Base Evacuation / Ordered Departure',
        'icon': '🚨',
        'weight': 5.0,  # Base weight — modified by sub-type in analysis
        'description': 'Evacuation of military bases or embassy drawdowns. Highest threat signal.',
        'keywords': [
            # Full military evacuation (weight: 5.0)
            'base evacuation', 'military evacuation', 'evacuated base',
            'evacuation ordered', 'personnel evacuated',
            'troops evacuated', 'evacuated troops',
            'evacuation of base', 'base drawdown',
            # NEO / noncombatant (weight: 4.5)
            'noncombatant evacuation', 'neo operation',
            'neo packet', 'neo preparation',
            # Ordered departure (weight: 4.0)
            'ordered departure', 'embassy ordered departure',
            'reduced footprint', 'nonessential personnel depart',
            'embassy drawdown', 'embassy evacuation',
            'partial evacuation', 'personnel relocated',
            # Authorized voluntary departure (weight: 3.5)
            'voluntary departure', 'authorized departure',
            'dependent evacuation', 'dependents evacuated',
            'family departure', 'family evacuation',
            'military families evacuate', 'military families depart',
            'families prepare departure', 'families leaving',
            # Related signals
            'embassy closure', 'consulate evacuation',
            'potential departures', 'prepare for evacuation'
        ]
    },

    # ================================================
    # NEW: Military Posturing / Threats (v2.0)
    # ================================================
    'military_posturing': {
        'label': 'Military Posturing / Threats',
        'icon': '⚠️',
        'weight': 2.5,
        'description': 'Explicit military threats, warnings, or posturing statements.',
        'keywords': [
            'military threat', 'threatens retaliation',
            'warns of military action', 'warns neighbors',
            'all options on the table', 'military options',
            'strike options', 'decisive military options',
            'regime change', 'regime overthrow',
            'hit very hard', 'overwhelming force',
            'bases within range', 'within our range',
            'will defend with full force', 'painful response'
        ]
    }
}


# ========================================
# EVACUATION SUB-TYPE WEIGHTS
# ========================================
# Fine-grained weighting for different evacuation signals.
# These override the base weight when a specific sub-type is detected.

EVACUATION_SUBTYPE_WEIGHTS = {
    'military_evacuation': {
        'weight': 5.0,
        'keywords': ['base evacuation', 'military evacuation', 'evacuated base',
                     'evacuation ordered', 'personnel evacuated', 'troops evacuated',
                     'evacuated troops', 'base drawdown']
    },
    'neo_operation': {
        'weight': 4.5,
        'keywords': ['noncombatant evacuation', 'neo operation', 'neo packet',
                     'neo preparation']
    },
    'ordered_departure': {
        'weight': 4.0,
        'keywords': ['ordered departure', 'embassy ordered departure',
                     'reduced footprint', 'nonessential personnel',
                     'embassy drawdown', 'embassy evacuation',
                     'partial evacuation', 'personnel relocated']
    },
    'voluntary_departure': {
        'weight': 3.5,
        'keywords': ['voluntary departure', 'authorized departure',
                     'dependent evacuation', 'dependents evacuated',
                     'family departure', 'family evacuation',
                     'military families evacuate', 'military families depart',
                     'families prepare departure', 'families leaving',
                     'potential departures', 'prepare for evacuation']
    }
}


# ========================================
# LOCATION MULTIPLIERS
# ========================================
# Hotspot locations that amplify scores when detected in article text.
# An Iranian exercise in the Strait of Hormuz ≠ a routine drill in Tehran.

LOCATION_MULTIPLIERS = {
    # Critical chokepoints (3x)
    'strait of hormuz': 3.0,
    'bab el-mandeb': 3.0,
    'suez canal': 2.5,
    'taiwan strait': 3.0,

    # Active conflict / high-tension zones (2.5x)
    'persian gulf': 2.0,
    'arabian sea': 2.0,
    'red sea': 2.0,
    'gulf of oman': 2.5,
    'eastern mediterranean': 2.0,
    'black sea': 2.0,
    'sea of azov': 2.0,

    # Specific hotspot bases/areas (2x)
    'al udeid': 2.5,
    'bahrain naval': 2.0,
    'camp arifjan': 1.5,
    'muwaffaq salti': 2.0,
    'tower 22': 2.5,
    'incirlik': 1.5,
    'diego garcia': 2.0,
    'tartus': 2.0,
    'hmeimim': 2.0,
    'zaporizhzhia': 2.0,
    'crimea': 2.0,
    'kursk': 2.0,

    # Broader regions (1.5x)
    'arctic': 1.5,
    'greenland': 1.5,
    'south china sea': 2.0,
    'baltic': 1.5
}


# ========================================
# ASSET → TARGET MAPPING
# ========================================

ASSET_TARGET_MAPPING = {
    # ============================
    # CENTCOM AOR (Middle East)
    # ============================
    'centcom': {
        'Al Udeid Air Base': {
            'location': 'Qatar',
            'targets': ['iran', 'qatar'],
            'description': 'CENTCOM forward HQ. Primary air ops hub.'
        },
        'Al Dhafra Air Base': {
            'location': 'UAE',
            'targets': ['iran', 'uae'],
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
        'Muwaffaq Salti (Tower 22)': {
            'location': 'Jordan',
            'targets': ['jordan', 'syria', 'iran'],
            'description': 'US base near Jordan-Syria border. F-15E hub.'
        },
        'Camp Arifjan': {
            'location': 'Kuwait',
            'targets': ['kuwait', 'iran'],
            'description': 'US Army Central forward HQ.'
        },
        'Ali Al Salem Air Base': {
            'location': 'Kuwait',
            'targets': ['kuwait'],
            'description': 'US Air Force operations in Kuwait.'
        },
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
        'Prince Sultan Air Base': {
            'location': 'Saudi Arabia',
            'targets': ['iran', 'saudi_arabia'],
            'description': 'US Air Force presence in Saudi Arabia.'
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
        'Grafenwöhr': {
            'location': 'Germany',
            'targets': ['europe', 'ukraine_support'],
            'description': 'US Army training hub. Ukraine training ops.'
        },
        'Rzeszów': {
            'location': 'Poland',
            'targets': ['ukraine_support'],
            'description': 'Key logistics hub for Ukraine aid.'
        },
        'Mihail Kogălniceanu': {
            'location': 'Romania',
            'targets': ['black_sea', 'nato_eastern_flank'],
            'description': 'US/NATO presence on Black Sea.'
        },
        'Deveselu': {
            'location': 'Romania',
            'targets': ['nato_eastern_flank'],
            'description': 'Aegis Ashore missile defense site.'
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
    # ── English-language defense media ──
    'The War Zone': 'https://www.twz.com/feed',
    'Breaking Defense': 'https://breakingdefense.com/feed/',
    'Defense One': 'https://www.defenseone.com/rss/all/',
    'Naval News': 'https://www.navalnews.com/feed/',
    'Stars and Stripes': 'https://www.stripes.com/rss',
    'Military Times': 'https://www.militarytimes.com/arc/outboundfeeds/rss/?outputType=xml',
    'CENTCOM': 'https://www.centcom.mil/RSS/',
    'NATO News': 'https://www.nato.int/cps/en/natohq/news.xml',
    'DVIDS': 'https://www.dvidshub.net/rss/news',

    # ── Hebrew / Israeli defense media ──
    'Jerusalem Post': 'https://www.jpost.com/rss/rssfeedsmilitary.aspx',
    'Times of Israel': 'https://www.timesofisrael.com/feed/',
    'Ynet News': 'https://www.ynetnews.com/RSS/0,84,0,0,1,0',
    'Israel Hayom': 'https://www.israelhayom.com/feed/',

    # ── Arabic-language defense / regional media ──
    'Al Jazeera English': 'https://www.aljazeera.com/xml/rss/all.xml',
    'Al Arabiya English': 'https://english.alarabiya.net/tools/rss',
    'Middle East Eye': 'https://www.middleeasteye.net/rss',

    # ── Russian-language defense media ──
    'TASS Defense': 'https://tass.com/rss/v2.xml',
    'Moscow Times': 'https://www.themoscowtimes.com/rss/news',

    # ── Turkish / regional media ──
    'Daily Sabah': 'https://www.dailysabah.com/rssFeed/defense',
    'TRT World': 'https://www.trtworld.com/rss',

    # ── Ukraine conflict trackers ──
    'Kyiv Independent': 'https://kyivindependent.com/feed/',
    'Ukrinform': 'https://www.ukrinform.net/rss/block-lastnews',

    # ── Iran / Farsi-adjacent English sources ──
    'Iran International': 'https://www.iranintl.com/en/feed',
    'Tasnim English': 'https://www.tasnimnews.com/en/rss',
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
    """Fetch military articles from GDELT across multiple queries and languages.

    v2.1: Multilingual expansion — queries in Russian, Hebrew, Arabic, Farsi,
    Turkish, Ukrainian, French, and Chinese to catch defense reporting that
    English-only queries miss entirely (e.g., Israeli IDF reports, TASS
    military dispatches, IRGC announcements in Farsi).
    """

    # ── ENGLISH queries (expanded — added Israel, Egypt, Turkey, more evac) ──
    english_queries = [
        # Core ME queries
        'military deployment middle east',
        'carrier strike group persian gulf',
        'military exercise middle east',
        'troops deployed middle east',
        'naval deployment mediterranean',
        'irgc military exercise',
        'iran strait hormuz drill',
        'chinese warship persian gulf',
        'russian navy mediterranean',
        # Evacuation / drawdown
        'military base evacuation middle east',
        'embassy evacuation middle east',
        'voluntary departure military',
        'military families evacuation',
        'ordered departure embassy',
        'noncombatant evacuation operation',
        # Israel (MISSING before v2.1!)
        'IDF military operation',
        'Israel defense forces deployment',
        'Israel military buildup',
        'Israel reservists mobilization',
        'Iron Dome deployment',
        'Israeli airstrike',
        'Israel Hezbollah military',
        'IDF northern command',
        # Regional allies
        'jordan military base',
        'qatar al udeid',
        'saudi military exercise',
        'uae military',
        'kuwait camp arifjan',
        'egypt military exercise',
        'egypt sinai troops',
        # Turkey
        'turkish military operation syria',
        'turkey military exercise',
        'incirlik air base',
        # Europe / NATO
        'nato exercise arctic',
        'nato military deployment',
        'greenland military defense',
        'nato baltic deployment',
        # Ukraine / Russia
        'ukraine military front',
        'russia ukraine offensive',
        'ukraine weapons delivery',
        'black sea military',
        'ukraine drone strike russia',
        'russia mobilization military',
        'crimea military attack',
    ]

    # ── HEBREW queries (Israeli defense reporting) ──
    hebrew_queries = [
        'צה"ל פריסה',              # IDF deployment
        'צה"ל תרגיל',              # IDF exercise
        'כיפת ברזל',               # Iron Dome
        'חיל האוויר תרגיל',        # Air Force exercise
        'מילואים גיוס',            # Reserves mobilization
        'חזבאללה צפון',            # Hezbollah north
        'פיקוד צפון כוננות',       # Northern Command readiness
        'חיל הים סיור',            # Navy patrol
    ]

    # ── RUSSIAN queries (Russia/Ukraine military reporting) ──
    russian_queries = [
        'военная операция украина',        # military operation ukraine
        'черноморский флот',               # Black Sea Fleet
        'вооруженные силы учения',         # armed forces exercises
        'ракетный удар украина',           # missile strike ukraine
        'мобилизация военная',             # military mobilization
        'северный флот арктика',           # Northern Fleet Arctic
        'военно-морской флот',             # Navy
        'ПВО развертывание',               # Air defense deployment
    ]

    # ── ARABIC queries (Gulf, Iran, regional military) ──
    arabic_queries = [
        'الحرس الثوري تدريب',             # IRGC exercise
        'قوات عسكرية الخليج',              # military forces Gulf
        'تدريب عسكري السعودية',            # military exercise Saudi
        'القوات المسلحة الإماراتية',       # UAE armed forces
        'الجيش المصري تدريب',              # Egyptian army exercise
        'القوات الأردنية',                 # Jordanian forces
        'حزب الله عسكري',                  # Hezbollah military
        'صواريخ باليستية إيران',           # ballistic missiles Iran
        'القوات البحرية مضيق هرمز',       # naval forces Strait of Hormuz
        'إخلاء قاعدة عسكرية',             # military base evacuation
    ]

    # ── FARSI queries (Iranian military / IRGC) ──
    farsi_queries = [
        'سپاه پاسداران رزمایش',           # IRGC exercise
        'نیروی دریایی رزمایش',             # Navy exercise
        'موشک بالستیک آزمایش',             # ballistic missile test
        'پهپاد نظامی',                     # military drone
        'نیروی هوافضا سپاه',               # IRGC Aerospace Force
        'تنگه هرمز رزمایش',                # Strait of Hormuz exercise
    ]

    # ── TURKISH queries (Turkey military ops) ──
    turkish_queries = [
        'türk silahlı kuvvetleri operasyon',   # Turkish armed forces operation
        'türk donanması tatbikat',              # Turkish navy exercise
        'suriye askeri operasyon',              # Syria military operation
        'bayraktar insansız hava',              # Bayraktar drone
        'incirlik üssü',                        # Incirlik base
    ]

    # ── UKRAINIAN queries (Ukrainian military reporting) ──
    ukrainian_queries = [
        'збройні сили україни',            # Armed Forces of Ukraine
        'фронт наступ',                    # front offensive
        'мобілізація військова',            # military mobilization
        'протиповітряна оборона',           # air defense
        'зброя постачання',                 # weapons supply
    ]

    # ── FRENCH queries (North Africa, Djibouti, Mediterranean) ──
    french_queries = [
        'forces armées méditerranée',       # armed forces Mediterranean
        'base militaire djibouti',          # military base Djibouti
        'opération militaire sahel',         # military operation Sahel
    ]

    # ── CHINESE queries (PLA / South China Sea) ──
    chinese_queries = [
        '军事演习 南海',                     # military exercise South China Sea
        '解放军 海军',                        # PLA Navy
        '中国 军舰',                          # China warship
    ]

    # ── Fetch all query blocks ──
    all_articles = []

    query_blocks = [
        (english_queries, 'eng', 'English'),
        (hebrew_queries, 'heb', 'Hebrew'),
        (russian_queries, 'rus', 'Russian'),
        (arabic_queries, 'ara', 'Arabic'),
        (farsi_queries, 'fas', 'Farsi'),
        (turkish_queries, 'tur', 'Turkish'),
        (ukrainian_queries, 'ukr', 'Ukrainian'),
        (french_queries, 'fra', 'French'),
        (chinese_queries, 'zho', 'Chinese'),
    ]

    for queries, lang_code, lang_name in query_blocks:
        block_count = 0
        for query in queries:
            articles = fetch_gdelt_military(query, days, language=lang_code)
            all_articles.extend(articles)
            block_count += len(articles)
            time.sleep(0.3)
        if block_count > 0:
            print(f"[Military GDELT] {lang_name} ({lang_code}): {block_count} articles from {len(queries)} queries")

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
        'base evacuation Middle East',
        'military families departure Bahrain',
        'Ukraine military',
        'Russia offensive Ukraine',
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
    keywords = ['deployment', 'military', 'carrier', 'strike group', 'NATO', 'CENTCOM',
                'evacuation', 'Ukraine']
    query = " OR ".join(keywords[:4])

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

def get_location_multiplier(text):
    """
    Scan article text for hotspot locations and return the highest multiplier.
    If multiple hotspots are mentioned, take the maximum (not additive).
    """
    max_multiplier = 1.0
    matched_location = None

    for location, multiplier in LOCATION_MULTIPLIERS.items():
        if location in text:
            if multiplier > max_multiplier:
                max_multiplier = multiplier
                matched_location = location

    return max_multiplier, matched_location


def get_evacuation_subtype_weight(text):
    """
    For base_evacuation signals, determine the specific sub-type
    and return the appropriate weight (military evac > NEO > ordered departure > voluntary).
    """
    # Check from highest to lowest priority
    for subtype_id, subtype_data in sorted(
        EVACUATION_SUBTYPE_WEIGHTS.items(),
        key=lambda x: x[1]['weight'],
        reverse=True
    ):
        for kw in subtype_data['keywords']:
            if kw in text:
                return subtype_data['weight'], subtype_id

    # Default to base weight
    return ASSET_CATEGORIES['base_evacuation']['weight'], 'unspecified'


def analyze_article_military(article):
    """
    Analyze a single article for military deployment signals.

    v2.0 enhancements:
    - Location-aware scoring (Strait of Hormuz multiplier, etc.)
    - Evacuation sub-type weight differentiation
    - Context-aware scoring
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
        'signals': [],
        'location_multiplier': 1.0,
        'hotspot_location': None
    }

    # 0. Determine location multiplier for this article
    loc_multiplier, hotspot = get_location_multiplier(text)
    result['location_multiplier'] = loc_multiplier
    result['hotspot_location'] = hotspot

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

                            # Determine weight — special handling for evacuations
                            if asset_id == 'base_evacuation':
                                asset_weight, evac_subtype = get_evacuation_subtype_weight(text)
                            else:
                                asset_weight = asset_data['weight']
                                evac_subtype = None

                            # Apply location multiplier
                            signal_score = asset_weight * actor_weight * loc_multiplier

                            signal_entry = {
                                'actor': actor_id,
                                'actor_name': actor_data['name'],
                                'actor_flag': actor_data['flag'],
                                'asset': asset_id,
                                'asset_label': asset_data['label'],
                                'asset_icon': asset_data['icon'],
                                'keyword': asset_kw,
                                'actor_keyword': keyword,
                                'weight': round(signal_score, 2),
                                'base_weight': asset_weight,
                                'location_multiplier': loc_multiplier,
                                'hotspot_location': hotspot,
                                'article_title': article.get('title', '')[:120],
                                'article_url': article.get('url', ''),
                                'source': article.get('source', {}).get('name', 'Unknown'),
                                'published': article.get('publishedAt', '')
                            }

                            if evac_subtype:
                                signal_entry['evacuation_subtype'] = evac_subtype

                            result['signals'].append(signal_entry)
                            result['score'] += signal_score
                            asset_matched = True
                            break

                    if asset_matched:
                        break

                # If actor detected but no specific asset, still count it
                if not asset_matched:
                    signal_score = actor_weight * 1.0 * loc_multiplier

                    result['signals'].append({
                        'actor': actor_id,
                        'actor_name': actor_data['name'],
                        'actor_flag': actor_data['flag'],
                        'asset': 'unspecified',
                        'asset_label': 'Military Activity',
                        'asset_icon': '⚠️',
                        'keyword': keyword,
                        'actor_keyword': keyword,
                        'weight': round(signal_score, 2),
                        'base_weight': 1.0,
                        'location_multiplier': loc_multiplier,
                        'hotspot_location': hotspot,
                        'article_title': article.get('title', '')[:120],
                        'article_url': article.get('url', ''),
                        'source': article.get('source', {}).get('name', 'Unknown'),
                        'published': article.get('publishedAt', '')
                    })
                    result['score'] += signal_score

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
    """
    count = len(active_actors)
    if count <= 1:
        return 1.0
    elif count == 2:
        return 1.15
    elif count == 3:
        return 1.3
    elif count == 4:
        return 1.45
    else:
        return 1.5 + (0.05 * (count - 5))  # Scale beyond 5


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
    per_target_scores = {}
    per_actor_scores = {}
    active_actors = set()
    asset_type_counts = {}
    evacuation_signals = []  # Track separately for dashboard alerts

    for article in all_articles:
        analysis = analyze_article_military(article)

        if analysis['signals']:
            for signal in analysis['signals']:
                all_signals.append(signal)
                active_actors.add(signal['actor'])

                for target in analysis['targets']:
                    per_target_scores[target] = per_target_scores.get(target, 0) + signal['weight']

                actor = signal['actor']
                per_actor_scores[actor] = per_actor_scores.get(actor, 0) + signal['weight']

                asset = signal['asset']
                asset_type_counts[asset] = asset_type_counts.get(asset, 0) + 1

                # Track evacuation signals specifically
                if asset == 'base_evacuation':
                    evacuation_signals.append(signal)

    # ========================================
    # CALCULATE REGIONAL TENSION MULTIPLIER
    # ========================================
    tension_multiplier = calculate_regional_tension_multiplier(active_actors)

    print(f"[Military Tracker] Active actors: {len(active_actors)} → Tension multiplier: {tension_multiplier}x")

    for target in per_target_scores:
        per_target_scores[target] = round(per_target_scores[target] * tension_multiplier, 2)

    # ========================================
    # BUILD PER-TARGET POSTURE ASSESSMENTS
    # ========================================
    target_postures = {}

    for target, score in per_target_scores.items():
        alert_level = determine_alert_level(score)
        threshold = ALERT_THRESHOLDS[alert_level]

        # Get signals relevant to this target
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
            'theatre': actor_data.get('theatre', 'unknown'),
            'total_score': round(score, 2),
            'signal_count': len(actor_signals),
            'top_signals': actor_signals[:5],
            'alert_level': determine_alert_level(score)
        }

    # Also include actors with 0 signals so they appear on the dashboard
    for actor_id, actor_data in MILITARY_ACTORS.items():
        if actor_id not in actor_summaries:
            actor_summaries[actor_id] = {
                'name': actor_data.get('name', actor_id),
                'flag': actor_data.get('flag', ''),
                'tier': actor_data.get('tier', 99),
                'theatre': actor_data.get('theatre', 'unknown'),
                'total_score': 0,
                'signal_count': 0,
                'top_signals': [],
                'alert_level': 'normal'
            }

    # ========================================
    # BUILD THEATRE GROUPINGS
    # ========================================
    theatre_data = {}

    for theatre_id, theatre_info in REGIONAL_THEATRES.items():
        theatre_actors = {}
        theatre_total_score = 0

        for actor_id in theatre_info['actors']:
            if actor_id in actor_summaries:
                theatre_actors[actor_id] = actor_summaries[actor_id]
                theatre_total_score += actor_summaries[actor_id]['total_score']

        theatre_data[theatre_id] = {
            'label': theatre_info['label'],
            'icon': theatre_info['icon'],
            'order': theatre_info['order'],
            'description': theatre_info['description'],
            'actors': theatre_actors,
            'total_score': round(theatre_total_score, 2),
            'alert_level': determine_alert_level(theatre_total_score)
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
        'target_postures': target_postures,
        'actor_summaries': actor_summaries,
        'theatre_groupings': theatre_data,
        'asset_distribution': asset_type_counts,
        'evacuation_alerts': [
            {
                'subtype': s.get('evacuation_subtype', 'unspecified'),
                'actor': s.get('actor_name', ''),
                'title': s.get('article_title', ''),
                'url': s.get('article_url', ''),
                'weight': s.get('weight', 0),
                'source': s.get('source', '')
            }
            for s in evacuation_signals
        ],
        'top_signals': sorted(all_signals, key=lambda x: x['weight'], reverse=True)[:25],
        'source_breakdown': {
            'defense_rss': len(rss_articles),
            'gdelt': len(gdelt_articles),
            'newsapi': len(newsapi_articles),
            'reddit': len(reddit_posts)
        },
        'last_updated': datetime.now(timezone.utc).isoformat(),
        'cached': False,
        'version': '2.1.0'
    }

    save_military_cache(result)

    print(f"[Military Tracker] ✅ Scan complete in {scan_time}s")
    print(f"[Military Tracker]    Signals: {len(all_signals)}, Actors: {len(active_actors)}, Targets: {len(target_postures)}")
    print(f"[Military Tracker]    Evacuation alerts: {len(evacuation_signals)}")

    return result


# ========================================
# DASHBOARD INTEGRATION HELPER
# ========================================

def get_military_posture(target):
    """
    Quick lookup for a specific target's military posture.

    Called by existing threat endpoints:
      probability += posture['military_bonus']
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

        bonus_map = {
            'normal': 0,
            'elevated': 5,
            'high': 10,
            'surge': 15
        }

        alert_level = posture.get('alert_level', 'normal')
        military_bonus = bonus_map.get(alert_level, 0)

        banner_text = ''
        top_signals = posture.get('top_signals', [])

        # Prioritize evacuation signals in banner text
        evac_alerts = data.get('evacuation_alerts', [])
        if evac_alerts and posture.get('show_banner'):
            top_evac = evac_alerts[0]
            banner_text = (
                f"🚨 BASE EVACUATION: {top_evac.get('title', '')[:80]}"
            )
        elif top_signals and posture.get('show_banner'):
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
            'active_actors': data.get('active_actors', []),
            'evacuation_alerts': evac_alerts[:3]
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
# CORS HELPER
# ========================================

def _add_cors_headers(response):
    """Add CORS headers to a Flask response"""
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return response


# ========================================
# FLASK ENDPOINT REGISTRATION
# ========================================

def register_military_endpoints(app):
    """
    Register military tracker endpoints with the Flask app.
    Called from main app.py: register_military_endpoints(app)
    """

    @app.route('/api/military-posture', methods=['GET', 'OPTIONS'])
    def api_military_posture():
        """
        Full military posture assessment.
        Used by standalone military.html page.
        """
        from flask import request as flask_request

        # Handle CORS preflight
        if flask_request.method == 'OPTIONS':
            response = app.response_class(response='', status=200)
            return _add_cors_headers(response)

        try:
            days = int(flask_request.args.get('days', 7))
            refresh = flask_request.args.get('refresh', 'false').lower() == 'true'

            result = scan_military_posture(days=days, force_refresh=refresh)
            response = app.response_class(
                response=json.dumps(result, default=str),
                status=200,
                mimetype='application/json'
            )
            return _add_cors_headers(response)

        except Exception as e:
            print(f"[Military API] Error: {str(e)}")
            import traceback
            traceback.print_exc()
            response = app.response_class(
                response=json.dumps({
                    'success': False,
                    'error': str(e)[:200]
                }),
                status=500,
                mimetype='application/json'
            )
            return _add_cors_headers(response)

    @app.route('/api/military-posture/<target>', methods=['GET', 'OPTIONS'])
    def api_military_posture_target(target):
        """
        Quick posture check for a specific target.
        Used by dashboard threat cards.
        """
        from flask import request as flask_request

        # Handle CORS preflight
        if flask_request.method == 'OPTIONS':
            response = app.response_class(response='', status=200)
            return _add_cors_headers(response)

        try:
            posture = get_military_posture(target)
            response = app.response_class(
                response=json.dumps(posture, default=str),
                status=200,
                mimetype='application/json'
            )
            return _add_cors_headers(response)

        except Exception as e:
            response = app.response_class(
                response=json.dumps({
                    'success': False,
                    'error': str(e)[:200]
                }),
                status=500,
                mimetype='application/json'
            )
            return _add_cors_headers(response)

    print("[Military Tracker] ✅ Endpoints registered: /api/military-posture, /api/military-posture/<target>")
