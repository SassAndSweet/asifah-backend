"""
Asifah Analytics — Military Asset & Deployment Tracker v2.7.0
March 4, 2026

Tracks military asset movements across multiple actors and regions.
Feeds deployment scores into existing threat probability calculations.

ACTORS TRACKED:
  Tier 1 (Direct strike correlation):
    - US / CENTCOM
    - Israel / IDF
  Tier 2 (Adversary / Active Theatre):
    - Iran / IRGC
    - Iraq (Active theatre — IRI militia attacks, ISIS, US withdrawal)
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
    - Greenland / Denmark
    - Poland
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
  v2.5.0 - Iraq actor integration:
           * Added Iraq as Tier 2 active theatre actor (weight 0.7)
           * Comprehensive keyword coverage: IRI militias (Kata'ib Hezbollah,
             Harakat al-Nujaba, Asa'ib Ahl al-Haq, Islamic Resistance in Iraq),
             PMF/Hashd al-Shaabi, ISIS/ISIL Iraq, US withdrawal, Iraqi airspace
           * Added Arabic keywords for Iraqi militia and military coverage
           * Added Iraq-specific location multipliers: Al Asad (2.5x),
             Ain al-Assad, Erbil (2.0x), Taji, Balad, Baghdad Green Zone,
             Camp Victory, Iraqi airspace corridor
           * Updated ASSET_TARGET_MAPPING: existing Iraq bases now feed
             'iraq' target; added Taji, Balad, Baghdad Green Zone
           * Added Iraq RSS feeds: Iraqi News Agency, Rudaw, Kurdistan24
           * Added Iraq GDELT queries in English and Arabic
           * Added Iraq NewsAPI query
           * Added 'iraq' to REGIONAL_THEATRES middle_east actors
  v2.4.0 - Upstash Redis persistent cache:
           * Replaced /tmp file cache with Upstash Redis
           * Cache now survives Render deploys and cold starts
           * Same pattern as Iran and Lebanon modules
           * /tmp file used as local fallback only
  v2.3.0 - Multilingual keyword matching + new actors:
           * Added Greenland and Poland as Tier 3 European actors
           * Added multilingual keywords to Russia, Ukraine, Iran, Israel
             actors so GDELT non-English articles trigger score matches
           * Added Polish and Danish/Norwegian GDELT query blocks
           * Expanded Russian and Ukrainian GDELT queries
           * Added drone incursion and airspace violation keywords
             for Poland (border drone flyovers from Belarus/Russia)
           * Added Greenland sovereignty and Arctic militarization keywords
           * Added location multipliers for Poland border hotspots
           * Total GDELT queries now 120+ across 11 languages
  v2.2.0 - Background scan & stability fix:
           * Moved initial scan to background thread (prevents gunicorn
             worker timeout crashes on cold start)
           * Endpoint returns stale cache or empty skeleton while scan
             runs — never blocks workers
           * Removed manual _add_cors_headers() — Flask-CORS handles
             all CORS globally from app.py
           * Added _background_scan_running lock to prevent duplicate scans
           * Added graceful empty response when no cache exists yet
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
import threading

# ========================================
# CONFIGURATION
# ========================================

GDELT_BASE_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
NEWSAPI_KEY = os.environ.get('NEWSAPI_KEY')

# Upstash Redis (persistent cache across Render cold starts)
UPSTASH_REDIS_URL = os.environ.get('UPSTASH_REDIS_URL')
UPSTASH_REDIS_TOKEN = os.environ.get('UPSTASH_REDIS_TOKEN')

# Local fallback cache (wiped on deploy, used when Redis unavailable)
MILITARY_CACHE_FILE = '/tmp/military_tracker_cache.json'
MILITARY_CACHE_TTL_HOURS = 4

# Background scan lock — prevents duplicate concurrent scans
_background_scan_running = False
_background_scan_lock = threading.Lock()

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
        'actors': ['nato', 'russia', 'turkey', 'ukraine', 'greenland', 'poland', 'cyprus'],
        'description': 'EUCOM area — NATO, Russia, Arctic, Black Sea, Ukraine, Poland eastern flank, Cyprus'
    },
    'middle_east': {
        'label': 'Middle East & North Africa',
        'icon': '🕌',
        'order': 3,
        'actors': ['us', 'israel', 'iran', 'iraq', 'bahrain', 'egypt', 'jordan', 'kuwait', 'oman', 'qatar', 'saudi_arabia', 'uae'],
        'description': 'CENTCOM area — Persian Gulf, Red Sea, Eastern Med, Levant, Iraq theatre'
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
            'centcom', 'us central command', 'pentagon deploys',
            'department of defense deployment', 'us forces middle east',
            'carrier strike group', 'uss ', 'us navy gulf', 'us navy middle east',
            'amphibious ready group', 'us destroyer', 'us cruiser',
            'us submarine mediterranean', 'us submarine gulf',
            'bomber task force', 'b-1 lancer', 'b-2 spirit', 'b-52 middle east',
            'f-35 deployment middle east', 'f-22 deployment', 'usaf deploys',
            'kc-135', 'kc-46', 'aerial refueling middle east',
            'mq-9 reaper', 'rq-4 global hawk', 'us isr assets',
            'us troops deployed middle east', 'us forces iraq',
            'us forces syria', 'us forces jordan',
            '82nd airborne', '101st airborne middle east',
            'marine expeditionary', 'us special operations',
            'patriot battery deployed', 'thaad deployment',
            'iron dome us', 'us air defense middle east',
            'pre-positioned stocks', 'ammunition shipment',
            'military sealift command', 'us logistics middle east',
            'us military buildup', 'us force posture', 'us surge middle east',
            'massive fleet', 'armada', 'combat power',
            'us military assets middle east', 'military assets flock',
            # Active war posture (v2.6.0)
            'us strikes iran', 'us attack iran', 'us retaliates iran',
            'pentagon iran strike', 'centcom strike iran',
            'us military action iran', 'us iran war',
            'us forces high alert', 'defcon', 'force protection elevated',
            'us embassy evacuation middle east', 'us citizens leave',
            'shelter in place embassy', 'us warships iran',
            'us carrier iran', 'us bomber iran',
            'b-2 iran', 'b-52 iran', 'tomahawk iran',
        ],
        'rss_feeds': [
            'https://news.google.com/rss/search?q=site:centcom.mil&hl=en&gl=US&ceid=US:en',
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
            # IDF mobilization & operations
            'idf mobilization', 'idf mobilisation', 'israel reservists called',
            'israel reserves mobilized', 'idf northern command',
            'idf southern command', 'idf central command',
            'idf ground operation', 'idf troops deployed',
            'israel military buildup', 'idf offensive',
            # Air Force
            'israeli air force exercise', 'iaf exercise', 'iaf drill',
            'f-35 israel', 'f-15 israel', 'israeli airstrike',
            'israel aerial refueling', 'israeli drone strike',
            'iaf strike iran', 'iaf long range strike',
            # Navy
            'israeli navy', 'israel submarine', 'israeli corvette',
            'israel naval blockade', 'israel red sea',
            # Air defense systems
            'iron dome deployment', 'david sling', 'arrow battery',
            'israel air defense activation', 'iron dome intercept',
            'iron dome activated', 'iron dome overwhelmed', 'iron dome fails',
            'iron dome saturated', 'iron dome capacity',
            'david sling intercept', 'arrow intercept', 'arrow 3 intercept',
            'arrow missile defense', 'multi-layer defense',
            # Intelligence
            'mossad operation', 'shin bet alert', 'aman intelligence',
            'israel intelligence assessment',
            # Home Front Command / Pikud HaOref
            'home front command', 'pikud haoref', 'pikud ha-oref',
            'rocket alert', 'rocket siren', 'incoming rocket',
            'red alert israel', 'red alert app', 'tzeva adom',
            'missile alert israel', 'air raid siren israel',
            'rocket barrage israel', 'missile barrage israel',
            'rockets fired at israel', 'missiles fired at israel',
            'shelter instructions', 'bomb shelter israel',
            'home front command instructions',
            'multiple alerts', 'nationwide alert israel',
            # City-specific alerts (high location multiplier)
            'tel aviv siren', 'tel aviv rocket', 'tel aviv alert',
            'tel aviv missile', 'tel aviv hit', 'tel aviv impact',
            'jerusalem siren', 'jerusalem alert', 'jerusalem missile',
            'haifa siren', 'haifa alert', 'haifa rocket', 'haifa hit',
            'eilat siren', 'eilat missile', 'eilat alert',
            'beer sheva siren', 'beersheba alert', 'negev alert',
            'golan rockets', 'golan attack', 'golan shelling',
            # Airport / airspace
            'ben gurion airport closed', 'ben gurion divert',
            'ben gurion cancelled', 'ben gurion suspended',
            'israel airspace closed', 'israel flights cancelled',
            'israel flights suspended', 'ovda airport closed',
            'ramon airport closed',
            # Active Iran-Israel war (v2.7.2)
            'iran strikes israel', 'iran attack israel',
            'iran missile strike israel', 'iran retaliatory strike',
            'iran launches missiles', 'iran fires missiles',
            'iranian missile attack', 'iranian strike israel',
            'iran drone attack israel', 'shahed drone israel',
            'iran ballistic missile israel', 'iran cruise missile israel',
            'iranian ballistic missile tel aviv', 'iranian missile hits israel',
            'iran retaliates israel', 'iran retaliatory strike israel',
            'israel retaliates iran', 'israel strikes iran',
            'israel attack iran', 'idf strikes iran',
            'israel iran war', 'iran israel war',
            'iran israel conflict', 'iran israel escalation',
            'full scale war iran israel', 'regional war middle east',
            'multi front war israel', 'seven front war',
            # War damage & casualties
            'casualties israel', 'killed in israel', 'wounded israel',
            'dead in israel', 'injuries israel', 'israel death toll',
            'missile hits israel', 'missile impact israel',
            'debris falls israel', 'shrapnel israel', 'fragments israel',
            'direct hit israel', 'impact confirmed israel',
            'building hit israel', 'residential area hit israel',
            # US-Israel coordination
            'operation epic fury', 'us israel joint strike',
            'us israel coordinated', 'us defends israel',
            'patriot battery israel', 'thaad israel', 'thaad deployed israel',
            'us troops israel', 'centcom israel',
            # Evacuation & diplomatic
            'authorized departure israel', 'evacuate israel',
            'us citizens leave israel', 'us embassy israel alert',
            'leave israel immediately', 'commercial flights israel',
            'israel state of emergency', 'israel wartime government',
            'israel war cabinet',
            # Hebrew keywords
            'צה"ל', 'כיפת ברזל', 'חיל האוויר',
            'פיקוד צפון', 'פיקוד דרום', 'פיקוד מרכז',
            'מילואים', 'חזבאללה', 'חמאס',
            'חיל הים', 'תרגיל', 'גיוס',
            'כוננות', 'פריסה', 'סיור',
            'פיקוד העורף', 'צבע אדום', 'אזעקה',
            'התרעה', 'מרחב מוגן', 'מקלט',
            'יירוט', 'טיל בליסטי', 'רקטות',
            'שיגור', 'מטח רקטות', 'מטח טילים',
            'חץ', 'שלט דוד', 'כיפת ברזל נפלה',
            'מלחמה', 'מצב חירום', 'פינוי',
            'נפגעים', 'הרוגים', 'פצועים',
            'פגיעה ישירה', 'נפילה', 'רסיסים',
            'תל אביב אזעקה', 'חיפה אזעקה', 'ירושלים אזעקה',
            'נתב"ג סגור', 'שדה תעופה סגור',
            # Arabic keywords
            'صواريخ على إسرائيل', 'هجوم إيراني على إسرائيل',
            'القبة الحديدية', 'صافرات الإنذار إسرائيل',
            'قصف تل أبيب', 'قصف حيفا', 'قصف القدس',
            'حرب إسرائيل إيران', 'عملية إيبك فيوري',
            'إسرائيل تحت القصف', 'صاروخ باليستي إسرائيل',
            'الجبهة الداخلية', 'ملجأ', 'إنذار أحمر',
        ],
        'rss_feeds': [
            'https://news.google.com/rss/search?q=Israel+Iran+missile+attack+war&hl=en&gl=US&ceid=US:en',
            'https://news.google.com/rss/search?q=Israel+iron+dome+intercept+siren+alert&hl=en&gl=US&ceid=US:en',
            'https://news.google.com/rss/search?q=Israel+ballistic+missile+casualties+tel+aviv&hl=en&gl=US&ceid=US:en',
        ]
    },

    # ------------------------------------------------
    # TIER 2 — Adversary / Active Theatre
    # ------------------------------------------------
    'iran': {
        'name': 'Iran',
        'flag': '🇮🇷',
        'tier': 2,
        'theatre': 'middle_east',
        'weight': 0.8,
        'feeds_into': ['reverse_threat', 'regional_tension'],
        'keywords': [
            'irgc navy', 'irgc naval', 'iranian warship', 'iranian frigate',
            'iranian destroyer', 'iranian submarine', 'iran fast attack craft',
            'bandar abbas naval', 'iran strait of hormuz', 'irgc boats',
            'iran missile test', 'iran ballistic missile', 'iran cruise missile',
            'iran missile launch', 'shahab missile', 'fateh missile',
            'emad missile', 'iran hypersonic', 'irgc aerospace force',
            'iranian air force', 'iriaf', 'iran drone', 'shahed drone',
            'iran uav', 'iran mohajer', 'iranian fighter jet',
            'irgc exercise', 'iran military exercise', 'iran war games',
            'irgc ground forces', 'basij mobilization',
            'great prophet exercise', 'iran military drill',
            'iran drills', 'iran naval drill', 'iran naval exercise',
            'iran weapons shipment', 'iran arms transfer',
            'irgc quds force', 'iran smuggling weapons',
            'iran threatens', 'iran retaliation', 'iran warns',
            'iranian bases within range', 'iran retaliatory strike',
            'iran nuclear weapon', 'iran enrichment',
            'iranian defense minister',
            # Farsi keywords (match GDELT Farsi articles)
            'سپاه پاسداران', 'رزمایش', 'نیروی دریایی',
            'موشک بالستیک', 'پهپاد', 'نیروی هوافضا',
            'تنگه هرمز', 'سپاه قدس',
            # Arabic keywords (match Arabic-language Iran coverage)
            'الحرس الثوري', 'صواريخ باليستية إيران',
            'القوات البحرية الإيرانية', 'مضيق هرمز',
            # Active war / strike keywords (v2.6.0)
            'iran strikes israel', 'iran attacks israel',
            'iran missile launch israel', 'iran retaliatory strike israel',
            'iran fires missiles at israel', 'iranian attack on israel',
            'irgc launches', 'irgc fires', 'irgc strike',
            'iran ballistic missile launch', 'iran massive strike',
            'iran second strike', 'iran retaliates',
            'iran nuclear sites', 'iran nuclear facilities strike',
            'natanz', 'fordow', 'isfahan nuclear',
            'iran air defense activated', 'iran intercept',
            'iran war footing', 'iran full mobilization',
            'iran declares war', 'iran state of war',
            'strait of hormuz closed', 'hormuz blockade',
            'iran oil embargo', 'iran shipping attack',
            'حمله به اسرائیل', 'شلیک موشک', 'جنگ ایران اسرائیل',
            'حمله موشکی', 'عملیات نظامی',
        ],
        'rss_feeds': []
    },

    # ------------------------------------------------
    # TIER 2 — Iraq (Active theatre: IRI militias, ISIS, US withdrawal)
    # v2.5.0
    # ------------------------------------------------
    'iraq': {
        'name': 'Iraq',
        'flag': '🇮🇶',
        'tier': 2,
        'theatre': 'middle_east',
        'weight': 0.7,
        'feeds_into': ['strike_probability', 'regional_tension'],
        'keywords': [
            # --- IRI / Iran-aligned militias (primary threat) ---
            'islamic resistance in iraq', 'islamic resistance iraq',
            'iri attack', 'iri drone', 'iri rocket',
            'kata\'ib hezbollah', 'kataib hezbollah', 'kata\'ib hizballah',
            'harakat al-nujaba', 'harakat al nujaba', 'nujaba movement',
            'asa\'ib ahl al-haq', 'asaib ahl al haq', 'aah militia',
            'kata\'ib sayyid al-shuhada', 'kataib sayyid',
            'badr organization', 'badr corps', 'badr militia',
            'iran-backed militia iraq', 'iran backed militia iraq',
            'iran-aligned militia iraq', 'iran aligned militia',
            'iran proxy iraq', 'iranian proxy attack iraq',
            'militia attack us base iraq', 'militia drone attack iraq',
            'militia rocket attack iraq', 'one-way attack drone iraq',
            'attack on coalition forces iraq',
            # --- PMF / Hashd al-Shaabi ---
            'popular mobilization forces', 'pmf iraq',
            'hashd al-shaabi', 'hashd al shaabi', 'al-hashd',
            'pmf militia', 'pmf checkpoint', 'pmf deployment',
            'popular mobilization', 'hashd forces',
            # --- ISIS / ISIL in Iraq ---
            'isis iraq', 'isil iraq', 'daesh iraq',
            'isis attack iraq', 'isis ambush iraq', 'isis resurgence iraq',
            'isis prison iraq', 'isis prisoners iraq', 'isis fighters iraq',
            'isis sleeper cell iraq', 'islamic state iraq',
            'isis ied iraq', 'isis suicide iraq',
            'counter-isis iraq', 'counter isis operation',
            'operation inherent resolve',
            # --- US forces in Iraq ---
            'us forces iraq', 'us troops iraq', 'coalition forces iraq',
            'us withdrawal iraq', 'us pullout iraq', 'us drawdown iraq',
            'us base iraq', 'american forces iraq',
            'operation inherent resolve', 'cjtf-oir',
            'us military iraq withdrawal', 'coalition withdrawal iraq',
            'us advisors iraq', 'us advisory mission iraq',
            # --- Iraqi military / government ---
            'iraqi military', 'iraqi armed forces', 'iraqi army',
            'iraqi air force', 'iraqi navy',
            'iraqi security forces', 'iraqi federal police',
            'iraqi counter-terrorism', 'icts iraq', 'isof iraq',
            'iraqi special operations',
            'iraq defense minister', 'iraq security',
            'maliki iraq', 'nouri al-maliki',
            # --- Key locations ---
            'al asad airbase', 'ain al-asad', 'ain al asad',
            'erbil base', 'erbil attack', 'erbil rocket',
            'camp victory iraq', 'taji base', 'balad air base',
            'baghdad green zone', 'green zone attack',
            'baghdad international airport', 'biap',
            'al-tanf iraq', 'qaim border crossing',
            # --- Iraqi airspace (critical for Iran strike corridor) ---
            'iraqi airspace', 'iraq airspace corridor',
            'iraq air corridor', 'overfly iraq',
            'iraq flight restriction', 'iraq no-fly',
            # --- Sectarian / political instability ---
            'iraq sectarian', 'iraq sectarian violence',
            'iraq political crisis', 'iraq government formation',
            'iraq parliament', 'kurdistan iraq',
            'kurdish peshmerga', 'peshmerga',
            'krg iraq', 'erbil sulaymaniyah',
            # Arabic keywords (match GDELT/Arabic coverage)
            'المقاومة الإسلامية في العراق',
            'كتائب حزب الله', 'حركة النجباء',
            'عصائب أهل الحق', 'الحشد الشعبي',
            'القوات المسلحة العراقية', 'الجيش العراقي',
            'داعش العراق', 'قوات التحالف العراق',
            'الانسحاب الأمريكي العراق',
            'قاعدة عين الأسد', 'أربيل هجوم',
            'المنطقة الخضراء', 'الأجواء العراقية',
        ],
        'rss_feeds': [
            'https://news.google.com/rss/search?q=iraq+military+OR+militia+OR+ISIS&hl=en&gl=US&ceid=US:en',
            'https://news.google.com/rss/search?q=site:rudaw.net+military&hl=en&gl=US&ceid=US:en',
            'https://news.google.com/rss/search?q=site:kurdistan24.net+military&hl=en&gl=US&ceid=US:en',
        ]
    },

    'china': {
        'name': 'China',
        'flag': '🇨🇳',
        'tier': 2,
        'theatre': 'asia_pacific',
        'weight': 0.6,
        'feeds_into': ['regional_tension'],
        'keywords': [
            'plan gulf', 'chinese warship', 'chinese navy persian gulf',
            'pla navy gulf', 'china naval deployment middle east',
            'chinese carrier', 'chinese destroyer gulf',
            'chinese frigate gulf', 'china anti-piracy',
            'chinese submarine indian ocean',
            'djibouti base china', 'china djibouti',
            'china military base', 'china port visit oman',
            'china port visit pakistan', 'gwadar china navy',
            'china spy ship', 'china surveillance vessel',
            'china intelligence ship', 'yuan wang tracking ship',
            'china iran naval exercise', 'china russia naval exercise',
            'china military exercise middle east',
            'south china sea military', 'taiwan strait military',
            'pla exercise', 'chinese military exercise',
            'chinese naval gun', 'plan warship'
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
            'russian navy mediterranean', 'russian warship mediterranean',
            'russian submarine mediterranean', 'russia med fleet',
            'tartus naval base', 'hmeimim air base', 'russia syria deployment',
            'russian forces syria', 'russian air force syria',
            'russian warship', 'russian destroyer', 'russian frigate',
            'russian submarine', 'russia black sea fleet',
            'russia naval exercise', 'russian aircraft carrier',
            'russian bomber patrol', 'tu-95 patrol', 'tu-160',
            'russian air force middle east', 'su-35 syria',
            'russia arms delivery', 'russia s-300', 'russia s-400',
            'russia weapons syria', 'russia iran military cooperation',
            'russian offensive ukraine', 'russia ukraine front',
            'russian forces ukraine', 'russia mobilization',
            'russian missile ukraine', 'russia drone ukraine',
            'russian artillery ukraine', 'wagner group',
            'russia nuclear posture', 'russia nuclear threat',
            'russia black sea', 'russian black sea fleet',
            'sevastopol naval base', 'crimea military',
            'russia arctic military', 'northern fleet',
            'russia arctic exercise',
            # Russian keywords (match GDELT Russian-language articles)
            'вооруженные силы', 'военная операция', 'ракетный удар',
            'черноморский флот', 'северный флот', 'мобилизация',
            'наступление', 'артиллерия', 'ПВО', 'учения',
            'ядерное оружие', 'стратегические силы',
            'крылатая ракета', 'баллистическая ракета',
            'военно-морской флот', 'подводная лодка',
            'бомбардировщик', 'истребитель',
            'дрон', 'беспилотник', 'БПЛА',
            'фронт', 'контрнаступление', 'оборона',
            # Active war keywords (v2.7.1)
            'russia launches missiles', 'russia fires missiles',
            'russian missile strike', 'russian drone strike',
            'russia attacks ukraine', 'russian offensive',
            'russia shahed', 'russian shahed drone',
            'russia escalation', 'russia nuclear warning',
            'putin warns', 'putin threatens',
        ],
        'rss_feeds': [
            'https://news.google.com/rss/search?q=russia+military+OR+missile+OR+offensive+OR+ukraine+attack&hl=en&gl=US&ceid=US:en',
        ]
    },

    # ------------------------------------------------
    # TIER 3 — Regional actors (Middle East)
    # ------------------------------------------------
    'saudi_arabia': {
        'name': 'Saudi Arabia',
        'flag': '🇸🇦',
        'tier': 3,
        'theatre': 'middle_east',
        'weight': 0.6,
        'feeds_into': ['regional_tension'],
        'keywords': [
            'saudi military', 'saudi air force', 'royal saudi navy',
            'saudi air defense', 'saudi patriot', 'saudi thaad',
            'saudi arabia military exercise', 'saudi naval exercise',
            'saudi yemen border', 'saudi military buildup',
            'saudi defense spending', 'saudi arms deal',
            'saudi intercept', 'saudi houthi',
            'us cargo planes saudi', 'saudi base',
            'prince sultan air base', 'king abdulaziz air base',
            'king fahd air base', 'eskan village',
            # War keywords (v2.7.0)
            'iran strike saudi', 'iranian missile saudi',
            'iranian attack saudi arabia', 'iran drone saudi',
            'saudi intercept missile', 'saudi air defense activated',
            'riyadh attack', 'riyadh missile', 'riyadh drone',
            'eastern province attack', 'dhahran attack',
            'aramco attack', 'saudi oil attack',
            'saudi embassy closed', 'saudi shelter in place',
            'us embassy saudi closed', 'saudi arabia war',
            'houthi attack saudi', 'houthi missile riyadh',
            'us embassy riyadh hit', 'us embassy riyadh drone',
            'us embassy riyadh attack', 'riyadh embassy strike',
            'iran strikes saudi arabia', 'ballistic missile riyadh',
            'riyadh struck', 'riyadh hit', 'jeddah attack',
            'saudi oil facility attack', 'ras tanura attack',
            'saudi port attack', 'jubail attack',
            'iran drone riyadh', 'iranian drone saudi',
            # Arabic keywords
            'القوات المسلحة السعودية', 'تدريب عسكري السعودية',
            'هجوم على السعودية', 'صاروخ إيراني السعودية',
            'الدفاع الجوي السعودي', 'قاعدة الأمير سلطان',
            'أرامكو هجوم', 'الرياض هجوم',
        ],
        'rss_feeds': [
            'https://news.google.com/rss/search?q=saudi+arabia+military+OR+missile+OR+attack+OR+defense&hl=en&gl=US&ceid=US:en',
        ]
    },

    'uae': {
        'name': 'United Arab Emirates',
        'flag': '🇦🇪',
        'tier': 3,
        'theatre': 'middle_east',
        'weight': 0.6,
        'feeds_into': ['regional_tension'],
        'keywords': [
            'uae forces', 'uae military', 'uae air force',
            'uae naval', 'uae military exercise',
            'al dhafra air base', 'uae defense',
            'uae arms deal', 'uae military buildup',
            'uae evacuation', 'uae departure',
            'emirates military', 'uae drone',
            # War keywords (v2.7.0)
            'iran strike uae', 'iranian missile uae',
            'iranian attack uae', 'iran drone uae',
            'dubai attack', 'dubai missile', 'dubai drone',
            'abu dhabi attack', 'abu dhabi missile',
            'us embassy dubai', 'us embassy dubai hit',
            'us embassy abu dhabi', 'uae intercept missile',
            'uae air defense activated', 'uae shelter',
            'al dhafra attack', 'al dhafra missile',
            'jebel ali port attack', 'uae war',
            'houthi attack uae', 'houthi missile uae',
            'fujairah attack', 'fujairah port', 'fujairah struck',
            'fujairah missile', 'fujairah drone',
            'us embassy dubai hit', 'us embassy abu dhabi hit',
            'uae embassy attack', 'uae embassy struck',
            'iran strikes uae', 'ballistic missile dubai',
            'ballistic missile abu dhabi', 'iran drone dubai',
            'uae port struck', 'uae port attack',
            # Arabic keywords
            'القوات المسلحة الإماراتية',
            'هجوم على الإمارات', 'صاروخ إيراني الإمارات',
            'دبي هجوم', 'أبوظبي هجوم',
            'قاعدة الظفرة', 'السفارة الأمريكية دبي',
        ],
        'rss_feeds': [
            'https://news.google.com/rss/search?q=UAE+OR+dubai+OR+abu+dhabi+military+OR+missile+OR+attack&hl=en&gl=US&ceid=US:en',
        ]
    },

    'jordan': {
        'name': 'Jordan',
        'flag': '🇯🇴',
        'tier': 3,
        'theatre': 'middle_east',
        'weight': 0.6,
        'feeds_into': ['regional_tension'],
        'keywords': [
            'jordan military', 'jordanian armed forces',
            'muwaffaq salti', 'tower 22', 'jordan air base',
            'jordan border', 'jordan syria border',
            'f-15 jordan', 'us forces jordan',
            'jordan military exercise', 'jordan defense',
            'jordan intercept', 'jordan air defense',
            'eager lion exercise', 'jordan base',
            'us cargo planes jordan', 'strike eagles jordan',
            # War keywords (v2.7.0)
            'jordan intercept drone', 'jordan intercept missile',
            'jordan intercept ballistic', 'jordan shoots down',
            'jordanian airspace violation', 'jordan airspace',
            'jordan air defense activated', 'jordan scramble jets',
            'debris jordan', 'fragments jordan', 'shrapnel jordan',
            'iran missile jordan', 'iranian drone jordan',
            'jordan shelter', 'amman attack', 'amman missile',
            'us embassy jordan closed', 'jordan war',
            'jordan intercepted drones', 'jordan intercepted missiles',
            # Arabic keywords
            'القوات الأردنية', 'الجيش الأردني',
            'الأردن اعتراض صاروخ', 'الأردن دفاع جوي',
            'المجال الجوي الأردني', 'عمان هجوم',
        ],
        'rss_feeds': [
            'https://news.google.com/rss/search?q=jordan+military+OR+intercept+OR+missile+OR+airspace&hl=en&gl=US&ceid=US:en',
        ]
    },

'qatar': {
        'name': 'Qatar',
        'flag': '🇶🇦',
        'tier': 3,
        'theatre': 'middle_east',
        'weight': 0.6,
        'feeds_into': ['strike_probability', 'regional_tension'],
        'keywords': [
            'al udeid air base', 'al udeid', 'qatar base',
            'centcom forward headquarters', 'centcom hq qatar',
            'qatar military', 'qatar defense',
            'qatar air base evacuation', 'al udeid evacuation',
            'qatar military exercise', 'us forces qatar',
            # War keywords (v2.7.0)
            'al udeid hit', 'al udeid attack', 'al udeid missile',
            'al udeid struck', 'iran missile qatar',
            'iranian attack qatar', 'iranian strike qatar',
            'qatar intercept missile', 'qatar air defense',
            'qatar airspace closed', 'qatar flights suspended',
            'qatar airways grounded', 'qatar flights grounded',
            'doha attack', 'doha missile', 'doha shelter',
            'qatar civil aviation suspended', 'qatar war',
            'قطر هجوم', 'قاعدة العديد', 'الدوحة صاروخ',
        ],
        'rss_feeds': [
            'https://news.google.com/rss/search?q=qatar+OR+al+udeid+military+OR+missile+OR+attack+OR+flights&hl=en&gl=US&ceid=US:en',
        ]
    },

    'kuwait': {
        'name': 'Kuwait',
        'flag': '🇰🇼',
        'tier': 3,
        'theatre': 'middle_east',
        'weight': 0.6,
        'feeds_into': ['strike_probability', 'regional_tension'],
        'keywords': [
            'camp arifjan', 'kuwait military', 'kuwait base',
            'us forces kuwait', 'kuwait defense',
            'ali al salem air base', 'kuwait evacuation',
            'kuwait military exercise',
            # War keywords (v2.7.0)
            'iran strike kuwait', 'iranian missile kuwait',
            'iranian attack kuwait', 'iran drone kuwait',
            'kuwait port attack', 'kuwait drone strike',
            'us soldiers killed kuwait', 'us troops killed kuwait',
            'kuwait intercept missile', 'kuwait air defense',
            'kuwait city attack', 'kuwait shrapnel',
            'kuwait embassy closed', 'us embassy kuwait closed',
            'kuwait warplanes crashed', 'kuwait war',
            'camp arifjan attack', 'ali al salem attack',
            'kuwait casualties', 'kuwait killed',
            'us embassy kuwait hit', 'us embassy kuwait drone',
            'us embassy kuwait attack', 'kuwait embassy strike',
            'kuwait troops dead', 'american soldiers kuwait',
            'soldiers died kuwait', 'troops died kuwait',
            'kuwait base struck', 'kuwait base hit',
            'iran strikes kuwait', 'ballistic missile kuwait',
            # Arabic keywords
            'الكويت هجوم', 'صاروخ إيراني الكويت',
            'معسكر عريفجان', 'قاعدة علي السالم',
            'السفارة الأمريكية الكويت',
        ],
        'rss_feeds': [
            'https://news.google.com/rss/search?q=kuwait+military+OR+missile+OR+attack+OR+troops&hl=en&gl=US&ceid=US:en',
        ]
    },

    'bahrain': {
        'name': 'Bahrain',
        'flag': '🇧🇭',
        'tier': 3,
        'theatre': 'middle_east',
        'weight': 0.6,
        'feeds_into': ['strike_probability', 'regional_tension'],
        'keywords': [
            # US 5th Fleet / Naval Forces Central Command
            'us 5th fleet', 'fifth fleet', 'navcent', 'naval forces central command',
            'nsa bahrain', 'naval support activity bahrain',
            'us naval base bahrain', 'bahrain naval base',
            'juffair', 'mina salman',
            # Bahrain military
            'bahrain military', 'bahrain defense force', 'bdf',
            'bahrain air force', 'bahrain navy',
            'bahrain military exercise', 'bahrain defense',
            'bahrain base', 'bahrain deployment',
            'sheikh isa air base', 'bahrain airbase',
            # Regional role
            'bahrain iran tensions', 'bahrain security',
            'combined maritime forces bahrain', 'cmf bahrain',
            'international maritime security construct',
            'combined task force 150', 'ctf 150',
            'combined task force 152', 'ctf 152',
            'combined task force 153', 'ctf 153',
            'bahrain evacuation', 'bahrain departure',
            'bahrain threat', 'bahrain alert',
            # Bahrain defense / intercept (v2.7.2)
            'bahrain intercept missile', 'bahrain intercept drone',
            'bahrain air defense', 'bahrain air defense activated',
            'bahrain shoots down', 'bahrain shelter',
            'manama attack', 'manama missile', 'manama struck',
            'bahrain struck', 'bahrain hit', 'bahrain shrapnel',
            'iran attack bahrain', 'iranian missile bahrain',
            'iranian strike bahrain', 'iran drone bahrain',
            # Arabic keywords
            'قوة دفاع البحرين', 'الأسطول الخامس',
            'القاعدة البحرية البحرين',
        ],
        'rss_feeds': []
    },

    'egypt': {
        'name': 'Egypt',
        'flag': '🇪🇬',
        'tier': 3,
        'theatre': 'middle_east',
        'weight': 0.5,
        'feeds_into': ['regional_tension'],
        'keywords': [
            'egyptian military', 'egypt military exercise',
            'egyptian navy', 'egypt suez canal military',
            'egypt sinai operation', 'egyptian air force',
            'egypt rafale', 'egypt military buildup',
            'egypt libya border', 'egypt gaza border',
            'egypt israel border troops', 'bright star exercise',
            # War keywords (v2.7.0)
            'suez canal closed', 'suez canal military',
            'suez canal disruption', 'egypt rafah crossing',
            'egypt gaza humanitarian', 'egypt border tensions',
            'egypt air defense', 'egypt intercept',
            'egypt airspace', 'cairo military alert',
            'egypt sinai buildup', 'egypt red sea military',
            'sharm el sheikh military', 'egypt war footing',
            'egypt intercept missile', 'egypt intercept drone',
            'egypt scramble jets', 'egyptian jets scramble',
            'egypt closes airspace', 'egypt airspace closed',
            'cairo alert', 'egypt military alert',
            'egypt mobilization', 'egypt deploys troops sinai',
            'suez canal attack', 'suez canal struck',
            'suez canal closed war', 'suez shipping disruption',
            'egypt red sea patrol', 'egypt naval deployment',
            # Arabic keywords
            'الجيش المصري', 'القوات المسلحة المصرية',
            'قناة السويس عسكري', 'مصر دفاع جوي',
            'سيناء عملية', 'معبر رفح',
        ],
        'rss_feeds': [
            'https://news.google.com/rss/search?q=egypt+military+OR+suez+OR+sinai+OR+defense&hl=en&gl=US&ceid=US:en',
        ]
    },

    'oman': {
        'name': 'Oman',
        'flag': '🇴🇲',
        'tier': 3,
        'theatre': 'middle_east',
        'weight': 0.5,
        'feeds_into': ['regional_tension'],
        'keywords': [
            'oman military', 'royal oman armed forces',
            'oman air force', 'oman navy', 'oman defense',
            'oman strait of hormuz', 'oman gulf',
            'muscat military', 'oman base',
            'oman us military', 'oman access agreement',
            'masirah island', 'thumrait air base',
            'duqm port', 'duqm naval base', 'port of duqm',
            'oman air defense', 'oman intercept',
            # War keywords (v2.7.0)
            'iran attack oman', 'iranian missile oman',
            'oman airspace', 'oman airspace violation',
            'oman strait closure', 'oman war',
            'oman intercept missile', 'oman shelter',
            'muscat attack', 'duqm attack',
            'oman evacuation', 'oman embassy',
            # Arabic keywords
            'القوات المسلحة العمانية', 'سلطنة عمان عسكري',
            'ميناء الدقم', 'مسقط هجوم',
        ],
        'rss_feeds': [
            'https://news.google.com/rss/search?q=oman+military+OR+muscat+OR+duqm+OR+strait+hormuz&hl=en&gl=US&ceid=US:en',
        ]
    },

    'turkey': {
        'name': 'Turkey',
        'flag': '🇹🇷',
        'tier': 3,
        'theatre': 'europe',
        'weight': 0.6,
        'feeds_into': ['regional_tension'],
        'keywords': [
            'turkish military syria', 'turkish forces syria',
            'operation claw', 'turkish navy', 'turkish air force',
            'turkish drone strike', 'bayraktar tb2', 'akinci drone',
            'incirlik air base', 'turkish military exercise',
            'turkish navy mediterranean', 'turkish naval exercise',
            'turkey northern iraq', 'turkey pkk operation',
            'turkish ground operation syria',
            'turkey nato', 'turkish military nato',
            # War keywords (v2.7.0)
            'incirlik attack', 'incirlik strike', 'incirlik base alert',
            'turkey iran tensions', 'iran attack turkey',
            'iranian missile turkey', 'turkish airspace violation',
            'turkey air defense', 'turkey intercept',
            'turkey bosphorus military', 'turkish straits closure',
            'turkey border alert', 'erdogan military',
            'turkey war', 'turkey nato article 5',
            # Turkish keywords
            'türk silahlı kuvvetleri', 'türk donanması',
            'hava kuvvetleri', 'askeri operasyon',
            'İncirlik üssü saldırı', 'hava savunma',
            'füze saldırısı', 'savaş', 'NATO madde 5',
            # Active war — intercepts & strikes (v2.7.1)
            'turkey intercepts missile', 'turkey intercepts ballistic',
            'turkey shoots down drone', 'turkey shoots down missile',
            'turkish intercept', 'turkey missile intercept',
            'incirlik high alert', 'incirlik closed',
            'iran strikes turkey', 'iran attacks turkey',
            'iranian missile hits turkey', 'iranian drone turkey',
            'turkey scrambles jets', 'turkish jets scramble',
            'ankara shelter', 'istanbul shelter',
            'turkey activates air defense', 'turkey nato article 5',
            'turkey invokes article 5', 'article 5 turkey',
            'debris falls turkey', 'shrapnel turkey',
            'missile intercepted over turkey',
        ],
        'rss_feeds': [
            'https://news.google.com/rss/search?q=turkey+military+OR+incirlik+OR+erdogan+defense+OR+attack&hl=en&gl=US&ceid=US:en',
        ]
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
            'ukraine military', 'ukrainian armed forces',
            'ukraine offensive', 'ukraine counteroffensive',
            'ukraine front line', 'ukraine defense',
            'zaporizhzhia front', 'kherson front', 'bakhmut',
            'kursk incursion', 'ukraine kursk',
            'donetsk front', 'luhansk front',
            'ukraine f-16', 'ukraine patriot', 'ukraine air defense',
            'ukraine himars', 'ukraine storm shadow',
            'ukraine atacms', 'ukraine drone warfare',
            'ukraine long range strike', 'ukraine missile',
            'ukraine black sea', 'ukraine naval drone',
            'ukraine anti-ship', 'ukraine sea drone',
            'ukraine arms delivery', 'ukraine weapons package',
            'ukraine military aid', 'ukraine ammunition',
            'ukraine defense package',
            'ukraine mobilization', 'ukraine conscription',
            'ukraine reserves', 'ukraine recruitment',
            # Ukrainian keywords (match GDELT Ukrainian articles)
            'збройні сили', 'зброя', 'наступ', 'оборона',
            'фронт', 'мобілізація', 'протиповітряна оборона',
            'ракетний удар', 'артилерія', 'дрон', 'БПЛА',
            'контрнаступ', 'зенітна ракета',
            'постачання зброї', 'військова допомога',
            'морський дрон', 'безпілотник',
            # Russian keywords (many Ukraine war articles in Russian)
            'украина наступление', 'украина фронт',
            'украина оружие', 'украина мобилизация',
            'ВСУ', 'вооруженные силы украины'
        ],
        'rss_feeds': [
            'https://news.google.com/rss/search?q=ukraine+military+OR+missile+OR+offensive+OR+drone+attack&hl=en&gl=US&ceid=US:en',
        ]
    },

    'greenland': {
        'name': 'Greenland',
        'flag': '🇬🇱',
        'tier': 3,
        'theatre': 'europe',
        'weight': 0.4,
        'feeds_into': ['regional_tension'],
        'keywords': [
            # English — sovereignty & acquisition
            'greenland sovereignty', 'greenland acquisition', 'greenland trump',
            'greenland independence', 'greenland autonomy', 'greenland referendum',
            'greenland self-rule', 'greenland self-determination',
            'greenland purchase', 'buy greenland', 'us greenland deal',
            'greenland strategic', 'greenland geopolitical',
            # English — military & Arctic
            'greenland military', 'greenland defense', 'greenland defence',
            'greenland nato', 'greenland arctic', 'greenland us military',
            'thule air base', 'pituffik space base',
            'greenland radar', 'greenland early warning',
            'greenland surveillance', 'greenland patrol',
            'arctic military exercise', 'arctic sovereignty',
            'arctic nato', 'arctic icebreaker',
            'us arctic strategy', 'arctic military buildup',
            # English — resources & China
            'greenland rare earth', 'greenland critical minerals',
            'greenland mining', 'greenland china', 'greenland mineral',
            'greenland lithium', 'greenland uranium',
            # English — Denmark relations
            'denmark greenland', 'danish armed forces greenland',
            'denmark military greenland', 'greenland denmark tensions',
            'múte egede', 'naalakkersuisut',
            # Danish keywords (match GDELT Danish articles)
            'grønland', 'grønlands selvstyre', 'grønland forsvar',
            'grønland suverænitet', 'grønland nato',
            'grønland militær', 'pituffik', 'thule',
            'arktisk forsvar', 'arktisk sikkerhed',
            'forsvaret grønland',
            # Greenlandic
            'kalaallit nunaat', 'namminersorlutik',
        ],
        'rss_feeds': []
    },

    'poland': {
        'name': 'Poland',
        'flag': '🇵🇱',
        'tier': 3,
        'theatre': 'europe',
        'weight': 0.5,
        'feeds_into': ['regional_tension'],
        'keywords': [
            # English — military posture
            'poland military', 'polish armed forces', 'polish army',
            'poland defense spending', 'poland defence spending',
            'poland military buildup', 'poland military modernization',
            'poland nato', 'poland nato deployment',
            'poland eastern flank', 'nato poland',
            'us forces poland', 'us troops poland',
            'poland patriot', 'poland air defense',
            'poland himars', 'poland abrams', 'poland k2 tanks',
            'poland f-35', 'poland military procurement',
            # English — drone incursions & airspace violations
            'poland drone incursion', 'drone over poland',
            'drone crossed into poland', 'drone entered polish airspace',
            'poland airspace violation', 'airspace violation poland',
            'unidentified drone poland', 'mystery drone poland',
            'drone flyover poland', 'drone overflight poland',
            'poland border drone', 'drone from belarus',
            'drone from ukraine entered poland', 'drone from russia poland',
            'stray drone poland', 'wayward drone poland',
            'poland scramble jets', 'poland intercept drone',
            'poland shoot down drone', 'poland airspace incursion',
            'object entered polish airspace', 'missile entered poland',
            'projectile crossed into poland', 'poland airspace breach',
            'przewodów', 'przewodow missile',
            # English — border & Belarus
            'poland border', 'poland belarus border',
            'poland ukraine border', 'poland border crisis',
            'poland border troops', 'poland border security',
            'poland migration crisis', 'hybrid warfare poland',
            'belarus hybrid attack', 'lukashenko poland border',
            # English — exercises & bases
            'poland military exercise', 'steadfast defender poland',
            'dragon exercise poland', 'anakonda exercise',
            'rzeszów', 'rzeszow logistics', 'poland logistics hub',
            'redzikowo', 'aegis ashore poland',
            'poland missile defense', 'poland shield',
            'lask air base', 'poznań military',
            # Polish keywords (match GDELT Polish articles)
            'wojsko polskie', 'siły zbrojne',
            'dron nad polską', 'naruszenie przestrzeni powietrznej',
            'obrona powietrzna', 'ćwiczenia wojskowe',
            'granica polsko-białoruska', 'granica polsko-ukraińska',
            'modernizacja armii', 'zakupy wojskowe',
            'NATO w Polsce', 'flanka wschodnia',
            'incydent graniczny', 'obiekt w przestrzeni powietrznej',
            'bezzałogowiec', 'dron zwiadowczy',
        ],
        'rss_feeds': []
    },

    'cyprus': {
        'name': 'Cyprus',
        'flag': '🇨🇾',
        'tier': 3,
        'theatre': 'europe',
        'weight': 0.5,
        'feeds_into': ['regional_tension'],
        'keywords': [
            'cyprus military', 'cyprus defense', 'cyprus defence',
            'cyprus base', 'cyprus british base',
            'akrotiri base', 'raf akrotiri', 'akrotiri attack',
            'akrotiri drone', 'akrotiri strike',
            'dhekelia base', 'sovereign base areas',
            'cyprus air base', 'cyprus nato',
            # War keywords (v2.7.0)
            'iran attack cyprus', 'iranian drone cyprus',
            'iranian strike cyprus', 'iran missile cyprus',
            'cyprus airspace closed', 'cyprus flights cancelled',
            'cyprus evacuation', 'us evacuate cyprus',
            'cyprus shelter', 'nicosia attack',
            'limassol military', 'larnaca military',
            'paphos air base', 'andreas papandreou air base',
            'cyprus intercept', 'cyprus air defense',
            'european forces cyprus', 'france cyprus',
            'uk forces cyprus', 'british forces cyprus',
            'greece deploy cyprus', 'cyprus war',
            'cyprus reinforcement', 'destroyer cyprus',
            # Greek keywords
            'κύπρος στρατιωτικό', 'ακρωτήρι βάση',
        ],
        'rss_feeds': [
            'https://news.google.com/rss/search?q=cyprus+military+OR+akrotiri+OR+attack+OR+evacuation&hl=en&gl=US&ceid=US:en',
        ]
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
            'nato exercise', 'nato deployment', 'nato military exercise',
            'nato forces deployed', 'nato readiness', 'nato response force',
            'nato rapid reaction', 'allied command',
            'nato arctic', 'nato arctic exercise', 'thule air base',
            'pituffik space base', 'greenland military', 'greenland defense',
            'denmark military greenland', 'danish armed forces greenland',
            'arctic military exercise', 'cold response exercise',
            'nato northern flank', 'arctic patrol',
            'us greenland military', 'us arctic strategy',
            'icebreaker arctic', 'arctic surveillance',
            'nato baltic', 'nato baltic exercise', 'baltic air policing',
            'nato enhanced forward presence', 'nato eastern flank',
            'nato poland deployment', 'nato romania deployment',
            'nato mediterranean', 'standing nato maritime group',
            'snmg', 'nato sea guardian', 'nato med patrol',
            'nato defense spending', 'nato summit',
            'nato article 5', 'nato interoperability',
            'ramstein air base', 'shape nato', 'saceur',
            'nato ukraine', 'nato aid ukraine', 'ramstein format'
        ],
        'rss_feeds': [
            'https://news.google.com/rss/search?q=site:nato.int+news&hl=en&gl=US&ceid=US:en',
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
            'mead-cdoc', 'air defense cell',
            # Israel active air defense (v2.7.2)
            'iron dome intercept', 'iron dome activated', 'iron dome overwhelmed',
            'arrow intercept', 'arrow 3 intercept', 'david sling intercept',
            'air defense activated', 'air defense fires',
            'missile intercepted', 'intercepted over israel',
            'multi-layer defense', 'ballistic missile intercept',
            'iran missile intercept', 'intercepts ballistic',
            'shoots down drone', 'shoots down missile',
            # Regional air defense / intercept (v2.7.2)
            'patriot intercept', 'patriot missile intercept',
            'thaad intercept', 'thaad engagement',
            'air defense intercept', 'air defense engagement',
            'intercepted missile', 'intercepted drone',
            'intercepted ballistic', 'intercepted cruise missile',
            'shot down drone', 'shot down missile',
            'air defense system activated', 'air defense response',
            'saudi air defense intercept', 'saudi intercept',
            'uae air defense intercept', 'uae intercept',
            'jordan intercept', 'jordan air defense',
            'qatar air defense', 'kuwait air defense',
            'bahrain air defense', 'oman air defense',
            'egypt air defense', 'turkey air defense',
            'intercepted over saudi', 'intercepted over uae',
            'intercepted over jordan', 'intercepted over qatar',
            'intercepted over bahrain', 'intercepted over kuwait',
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
            'ground forces buildup',
            # Active war ground signals (v2.7.2)
            'soldiers killed', 'troops killed', 'service members killed',
            'casualties confirmed', 'killed in action',
            'wounded in action', 'soldiers wounded',
            'idf troops deployed', 'idf ground operation',
            'reservists mobilized', 'reserves called up',
            'home front command', 'shelter instructions',
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
        'description': 'Ballistic/cruise missile tests and live-fire launches.',
        'keywords': [
            'missile test', 'ballistic missile launch', 'cruise missile test',
            'missile exercise', 'rocket launch', 'weapons test',
            'hypersonic test', 'anti-ship missile test',
            'tomahawk launch', 'missile salvo',
            # Active missile fire (v2.7.2)
            'ballistic missile', 'cruise missile', 'missile barrage',
            'missile salvo', 'fires missiles', 'launches missiles',
            'rocket barrage', 'missile strike', 'missile attack',
            'iran fires missiles', 'iran launches missiles',
            'iran ballistic missile', 'iran cruise missile',
            'iranian missile attack', 'iranian ballistic missile',
            'houthi missile', 'hezbollah rockets',
            'missile hits', 'missile impact', 'missile struck',
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
    'base_evacuation': {
        'label': 'Base Evacuation / Ordered Departure',
        'icon': '🚨',
        'weight': 5.0,
        'description': 'Evacuation of military bases or embassy drawdowns. Highest threat signal.',
        'keywords': [
            'base evacuation', 'military evacuation', 'evacuated base',
            'evacuation ordered', 'personnel evacuated',
            'troops evacuated', 'evacuated troops',
            'evacuation of base', 'base drawdown',
            'noncombatant evacuation', 'neo operation',
            'neo packet', 'neo preparation',
            'ordered departure', 'embassy ordered departure',
            'reduced footprint', 'nonessential personnel depart',
            'embassy drawdown', 'embassy evacuation',
            'partial evacuation', 'personnel relocated',
            'voluntary departure', 'authorized departure',
            'dependent evacuation', 'dependents evacuated',
            'family departure', 'family evacuation',
            'military families evacuate', 'military families depart',
            'families prepare departure', 'families leaving',
            'embassy closure', 'consulate evacuation',
            'potential departures', 'prepare for evacuation',
            # Active war evacuation signals (v2.7.2)
            'us citizens leave israel', 'leave israel immediately',
            'evacuate israel', 'evacuate cyprus',
            'us citizens leave', 'citizens urged to leave',
            'authorized departure israel', 'authorized departure',
            'commercial flights cancelled', 'airport closed',
            'ben gurion closed', 'ben gurion airport closed',
            'airspace closed', 'flights grounded',
            'shelter in place', 'seek shelter',
        ]
    },
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
            'will defend with full force', 'painful response',
            # Target under fire / victim-of-attack signals (v2.7.2)
            'struck by missile', 'hit by missile', 'hit by drone',
            'attacked by iran', 'iranian attack on', 'iranian strike on',
            'iranian missiles strike', 'iran attacks',
            'under attack', 'came under fire', 'shelling reported',
            'explosion reported', 'blast reported',
            'embassy hit', 'embassy struck', 'embassy attacked',
            'port struck', 'port attacked', 'oil facility attacked',
            'base hit', 'base struck', 'base attacked',
            'casualties reported', 'killed in attack',
            'wounded in attack', 'shrapnel', 'debris fell',
            'infrastructure hit', 'civilian casualties',
        ]
    },
    'drone_incursion': {
        'label': 'Drone Incursion / Airspace Violation',
        'icon': '🛸',
        'weight': 3.5,
        'description': 'Unidentified drone or object entering sovereign airspace. Border threat signal.',
        'keywords': [
            'drone incursion', 'drone entered airspace',
            'drone crossed border', 'airspace violation',
            'unidentified drone', 'mystery drone',
            'drone flyover', 'drone overflight',
            'stray drone', 'wayward drone',
            'object entered airspace', 'airspace breach',
            'scramble jets drone', 'intercept drone',
            'shoot down drone', 'drone shot down',
            'missile crossed border', 'projectile entered airspace',
            'border airspace incident',
            'drone from belarus', 'drone from russia',
            'uav crossed border', 'uav incursion',
            # Active war drone/airspace (v2.7.2)
            'shahed drone', 'iranian drone', 'iran drone attack',
            'drone swarm', 'drone strike', 'kamikaze drone',
            'one-way attack drone', 'uav attack',
            'airspace closed', 'airspace violation',
        ]
    }
}


# ========================================
# EVACUATION SUB-TYPE WEIGHTS
# ========================================

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

LOCATION_MULTIPLIERS = {
    'strait of hormuz': 3.0,
    'bab el-mandeb': 3.0,
    'suez canal': 2.5,
    'taiwan strait': 3.0,
    'persian gulf': 2.0,
    'arabian sea': 2.0,
    'red sea': 2.0,
    'gulf of oman': 2.5,
    'eastern mediterranean': 2.0,
    'black sea': 2.0,
    'sea of azov': 2.0,
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
    'arctic': 1.5,
    'greenland': 1.5,
    'south china sea': 2.0,
    'baltic': 1.5,
    # Poland-specific hotspots (v2.3.0)
    'rzeszów': 2.0,
    'rzeszow': 2.0,
    'redzikowo': 2.0,
    'przewodów': 2.5,
    'przewodow': 2.5,
    'poland belarus border': 2.0,
    'polish airspace': 2.0,
    'suwalki gap': 2.5,
    'kaliningrad': 2.0,
    'lask air base': 1.5,
    # Iraq-specific hotspots (v2.5.0)
    'al asad': 2.5,
    'ain al-asad': 2.5,
    'ain al asad': 2.5,
    'erbil': 2.0,
    'taji': 2.0,
    'balad air base': 2.0,
    'baghdad green zone': 2.5,
    'green zone': 2.0,
    'camp victory': 2.0,
    'iraqi airspace': 2.5,
    'iraq airspace': 2.5,
    'anbar province': 2.0,
    'qaim': 2.0,
    'sinjar': 1.5,
    'kirkuk': 1.5,
    'mosul': 1.5,
    'basra': 1.5,
    'sulaymaniyah': 1.5,
    'diyala': 2.0,
    # Bahrain (v2.6.0)
    'bahrain naval base': 2.5,
    'juffair': 2.5,
    'nsa bahrain': 2.5,
    'fifth fleet': 2.5,
    '5th fleet': 2.5,
    'sheikh isa air base': 2.0,
    'mina salman': 2.0,
    # Kuwait (v2.7.0)
    'camp arifjan': 2.5,
    'ali al salem': 2.0,
    'kuwait port': 2.0,
    'kuwait city': 1.5,
    # Saudi Arabia (v2.7.0)
    'prince sultan air base': 2.5,
    'king abdulaziz air base': 2.0,
    'king fahd air base': 2.0,
    'riyadh': 2.0,
    'dhahran': 2.0,
    'eastern province': 2.0,
    'aramco': 2.5,
    # UAE (v2.7.0)
    'al dhafra': 2.5,
    'dubai': 1.5,
    'abu dhabi': 2.0,
    'jebel ali': 2.0,
    # Jordan (v2.7.0)
    'muwaffaq salti': 2.5,
    'tower 22': 2.5,
    'amman': 1.5,
    # Qatar (v2.7.0)
    'al udeid': 2.5,
    'doha': 1.5,
    # Oman (v2.7.0)
    'duqm': 2.0,
    'masirah': 2.0,
    'thumrait': 2.0,
    'muscat': 1.5,
    # Cyprus (v2.7.0)
    'akrotiri': 2.5,
    'dhekelia': 2.0,
    'larnaca': 1.5,
    'paphos air base': 2.0,
    'nicosia': 1.5,
    'limassol': 1.5,
    # Egypt (v2.7.0)
    'suez canal': 3.0,
    'sharm el sheikh': 1.5,
    'cairo': 1.5,
    # UAE ports (v2.7.1)
    'fujairah': 2.5,
    'ras tanura': 2.5,
    # Saudi ports (v2.7.1)
    'jubail': 2.0,
    'jeddah': 1.5,
    # Turkey (v2.7.1)
    'incirlik': 2.5,
    'ankara': 1.5,
    'istanbul': 1.5,
    # Israel (v2.7.2)
    'tel aviv': 2.5,
    'haifa': 2.5,
    'jerusalem': 2.0,
    'ben gurion': 3.0,
    'dimona': 3.0,
    'nevatim': 3.0,
    'ramon air base': 2.5,
    'hatzerim': 2.5,
    'ramat david': 2.5,
    'palmachim': 2.5,
    'eilat': 2.0,
    'negev': 1.5,
    'golan': 2.0,
    'iron dome': 2.0,
    'arrow': 2.0,
}


# ========================================
# ASSET → TARGET MAPPING
# ========================================

ASSET_TARGET_MAPPING = {
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
            'targets': ['bahrain', 'iran'],
            'description': 'US 5th Fleet HQ. Naval ops center.'
        },
        'NSA Bahrain (5th Fleet HQ)': {
            'location': 'Bahrain',
            'targets': ['bahrain', 'iran'],
            'description': 'US 5th Fleet / NAVCENT HQ. Primary naval command for Persian Gulf ops.'
        },
        'Sheikh Isa Air Base': {
            'location': 'Bahrain',
            'targets': ['bahrain', 'iran'],
            'description': 'Bahrain Air Force base. Coalition air ops.'
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
            'targets': ['syria', 'iran', 'iraq'],
            'description': 'US garrison. Syria-Iraq border control.'
        },
        'Al Asad Air Base': {
            'location': 'Iraq (Anbar)',
            'targets': ['iraq', 'syria', 'iran'],
            'description': 'Major US base in western Iraq. Frequent IRI militia target.'
        },
        'Erbil': {
            'location': 'Iraq (Kurdistan)',
            'targets': ['iraq', 'syria', 'iran'],
            'description': 'US forces in northern Iraq / KRG. IRI militia target.'
        },
        # v2.5.0 — new Iraq base entries
        'Taji': {
            'location': 'Iraq (Baghdad)',
            'targets': ['iraq'],
            'description': 'Iraqi military base north of Baghdad. Former Coalition hub.'
        },
        'Balad Air Base': {
            'location': 'Iraq (Saladin)',
            'targets': ['iraq'],
            'description': 'Major Iraqi Air Force base. Former US Joint Base Balad.'
        },
        'Baghdad Green Zone': {
            'location': 'Iraq (Baghdad)',
            'targets': ['iraq'],
            'description': 'International Zone. US Embassy compound. IRI militia rocket target.'
        },
        'Camp Victory': {
            'location': 'Iraq (Baghdad)',
            'targets': ['iraq'],
            'description': 'Former US HQ complex near Baghdad airport.'
        },
        'Qaim Border Crossing': {
            'location': 'Iraq (Anbar)',
            'targets': ['iraq', 'syria'],
            'description': 'Iraq-Syria border. Key smuggling / militia transit corridor.'
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
        'King Abdulaziz Air Base': {
            'location': 'Saudi Arabia (Dhahran)',
            'targets': ['saudi_arabia', 'iran'],
            'description': 'Saudi/coalition air ops. Eastern Province.'
        },
        'Duqm Naval Base': {
            'location': 'Oman',
            'targets': ['oman', 'iran'],
            'description': 'UK/US naval logistics. Indian Ocean access.'
        },
        'Thumrait Air Base': {
            'location': 'Oman',
            'targets': ['oman', 'iran'],
            'description': 'Omani Air Force. Coalition staging.'
        },
        'Masirah Island': {
            'location': 'Oman',
            'targets': ['oman'],
            'description': 'Remote air base. Indian Ocean patrol.'
        },
    },
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
            'targets': ['ukraine_support', 'poland'],
            'description': 'Key logistics hub for Ukraine aid. Near Ukrainian border.'
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
        'Redzikowo': {
            'location': 'Poland',
            'targets': ['poland', 'nato_eastern_flank'],
            'description': 'Aegis Ashore missile defense site. NATO BMD.'
        },
        'Łask Air Base': {
            'location': 'Poland',
            'targets': ['poland', 'nato_eastern_flank'],
            'description': 'Polish Air Force base. NATO air policing.'
        },
        'Poznań': {
            'location': 'Poland',
            'targets': ['poland', 'nato_eastern_flank'],
            'description': 'US Army V Corps forward HQ.'
        },
        'Suwalki Gap': {
            'location': 'Poland/Lithuania border',
            'targets': ['poland', 'nato_eastern_flank'],
            'description': 'Critical NATO corridor between Kaliningrad and Belarus.'
        },
        'RAF Akrotiri': {
            'location': 'Cyprus (UK SBA)',
            'targets': ['cyprus', 'syria', 'lebanon'],
            'description': 'UK sovereign base. Strike and ISR. Iran drone target.'
        },
        'Dhekelia': {
            'location': 'Cyprus (UK SBA)',
            'targets': ['cyprus'],
            'description': 'UK sovereign base area. Eastern Cyprus.'
        },
        'Andreas Papandreou Air Base': {
            'location': 'Cyprus (Paphos)',
            'targets': ['cyprus'],
            'description': 'Cypriot/Greek Air Force. Eastern Med.'
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
    'Stars and Stripes': 'https://news.google.com/rss/search?q=site:stripes.com+military&hl=en&gl=US&ceid=US:en',
    'Military Times': 'https://www.militarytimes.com/arc/outboundfeeds/rss/?outputType=xml',
    'CENTCOM': 'https://news.google.com/rss/search?q=site:centcom.mil&hl=en&gl=US&ceid=US:en',
    'NATO News': 'https://news.google.com/rss/search?q=site:nato.int+news&hl=en&gl=US&ceid=US:en',
    'DVIDS': 'https://www.dvidshub.net/rss/news',
    'Jerusalem Post': 'https://www.jpost.com/rss/rssfeedsmilitary.aspx',
    'Times of Israel': 'https://news.google.com/rss/search?q=site:timesofisrael.com+military&hl=en&gl=US&ceid=US:en',
    'Ynet News': 'https://www.ynetnews.com/Integration/StoryRss3254.xml',
    'Israel Hayom': 'https://www.israelhayom.com/feed/',
    'Al Jazeera English': 'https://www.aljazeera.com/xml/rss/all.xml',
    'Al Arabiya English': 'https://english.alarabiya.net/tools/rss',
    'Middle East Eye': 'https://www.middleeasteye.net/rss',
    'TASS Defense': 'https://tass.com/rss/v2.xml',
    'Moscow Times': 'https://www.themoscowtimes.com/rss/news',
    'Daily Sabah': 'https://www.dailysabah.com/rssFeed/defense',
    'TRT World': 'https://www.trtworld.com/rss',
    'Kyiv Independent': 'https://kyivindependent.com/feed/',
    'Ukrinform': 'https://www.ukrinform.net/rss/block-lastnews',
    'Iran International': 'https://www.iranintl.com/en/feed',
    'Tasnim English': 'https://news.google.com/rss/search?q=site:tasnimnews.com+military&hl=en&gl=US&ceid=US:en',
    # v2.3.0 additions — Poland & Arctic
    'Defence24 Poland': 'https://defence24.com/rss',
    'Polish Press Agency': 'https://www.pap.pl/en/rss.xml',
    'Arctic Today': 'https://news.google.com/rss/search?q=site:arctictoday.com&hl=en&gl=US&ceid=US:en',
    'High North News': 'https://news.google.com/rss/search?q=site:highnorthnews.com+arctic&hl=en&gl=US&ceid=US:en',
    # v2.5.0 additions — Iraq
    'Iraq News (Google)': 'https://news.google.com/rss/search?q=iraq+military+OR+militia+OR+ISIS&hl=en&gl=US&ceid=US:en',
    'Rudaw English': 'https://news.google.com/rss/search?q=site:rudaw.net+military&hl=en&gl=US&ceid=US:en',
    'Kurdistan24': 'https://news.google.com/rss/search?q=site:kurdistan24.net+military&hl=en&gl=US&ceid=US:en',
    # v2.6.0 — Bahrain
    'Bahrain News (Google)': 'https://news.google.com/rss/search?q=bahrain+military+OR+fifth+fleet+OR+naval&hl=en&gl=US&ceid=US:en',
    # v2.7.0 — War footing: all Gulf + regional actors
    'Kuwait Military (Google)': 'https://news.google.com/rss/search?q=kuwait+military+OR+missile+OR+attack+OR+troops&hl=en&gl=US&ceid=US:en',
    'Saudi Military (Google)': 'https://news.google.com/rss/search?q=saudi+arabia+military+OR+missile+OR+attack+OR+defense&hl=en&gl=US&ceid=US:en',
    'UAE Military (Google)': 'https://news.google.com/rss/search?q=UAE+OR+dubai+OR+abu+dhabi+military+OR+missile+OR+attack&hl=en&gl=US&ceid=US:en',
    'Jordan Military (Google)': 'https://news.google.com/rss/search?q=jordan+military+OR+intercept+OR+missile+OR+airspace&hl=en&gl=US&ceid=US:en',
    'Qatar Military (Google)': 'https://news.google.com/rss/search?q=qatar+OR+al+udeid+military+OR+missile+OR+attack+OR+flights&hl=en&gl=US&ceid=US:en',
    'Oman Military (Google)': 'https://news.google.com/rss/search?q=oman+military+OR+muscat+OR+duqm+OR+strait+hormuz&hl=en&gl=US&ceid=US:en',
    'Egypt Military (Google)': 'https://news.google.com/rss/search?q=egypt+military+OR+suez+OR+sinai+OR+defense&hl=en&gl=US&ceid=US:en',
    'Turkey Military (Google)': 'https://news.google.com/rss/search?q=turkey+military+OR+incirlik+OR+erdogan+defense+OR+attack&hl=en&gl=US&ceid=US:en',
    'Cyprus Military (Google)': 'https://news.google.com/rss/search?q=cyprus+military+OR+akrotiri+OR+attack+OR+evacuation&hl=en&gl=US&ceid=US:en',
}

REDDIT_MILITARY_SUBREDDITS = [
    'CredibleDefense', 'LessCredibleDefence', 'geopolitics',
    'Military', 'WarCollege', 'navy', 'AirForce',
    'NCD', 'DefenseNews'
]

REDDIT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


# ========================================
# UPSTASH REDIS CACHE (v2.4.0)
# Persistent across Render deploys/cold starts
# Same pattern as Iran and Lebanon modules
# ========================================

MILITARY_REDIS_KEY = 'military_tracker_cache'


def load_military_cache():
    """Load cached military tracker data from Upstash Redis, fallback to /tmp"""
    if UPSTASH_REDIS_URL and UPSTASH_REDIS_TOKEN:
        try:
            resp = requests.get(
                f"{UPSTASH_REDIS_URL}/get/{MILITARY_REDIS_KEY}",
                headers={"Authorization": f"Bearer {UPSTASH_REDIS_TOKEN}"},
                timeout=5
            )
            data = resp.json()
            if data.get("result"):
                cache = json.loads(data["result"])
                print(f"[Military Cache] Loaded from Redis (cached_at: {cache.get('cached_at', 'unknown')})")
                return cache
            print("[Military Cache] No existing cache in Redis")
        except Exception as e:
            print(f"[Military Cache] Redis load error: {e}")

    try:
        from pathlib import Path
        if Path(MILITARY_CACHE_FILE).exists():
            with open(MILITARY_CACHE_FILE, 'r') as f:
                cache = json.load(f)
                print("[Military Cache] Loaded from /tmp fallback")
                return cache
    except Exception as e:
        print(f"[Military Cache] /tmp load error: {e}")

    return {}


def save_military_cache(data):
    """Save military tracker data to Upstash Redis + /tmp fallback"""
    data['cached_at'] = datetime.now(timezone.utc).isoformat()

    if UPSTASH_REDIS_URL and UPSTASH_REDIS_TOKEN:
        try:
            payload = json.dumps(data, default=str)
            resp = requests.post(
                f"{UPSTASH_REDIS_URL}/set/{MILITARY_REDIS_KEY}",
                headers={
                    "Authorization": f"Bearer {UPSTASH_REDIS_TOKEN}",
                    "Content-Type": "application/json"
                },
                json={"value": payload},
                timeout=10
            )
            if resp.status_code == 200:
                print("[Military Cache] ✅ Saved to Redis")
            else:
                print(f"[Military Cache] Redis save HTTP {resp.status_code}")
        except Exception as e:
            print(f"[Military Cache] Redis save error: {e}")

    try:
        with open(MILITARY_CACHE_FILE, 'w') as f:
            json.dump(data, f, indent=2, default=str)
        print("[Military Cache] Saved /tmp fallback")
    except Exception as e:
        print(f"[Military Cache] /tmp save error: {e}")


def is_military_cache_fresh():
    """Check if military cache is still valid"""
    try:
        cache = load_military_cache()
        if not cache or 'cached_at' not in cache:
            return False
        cached_at = datetime.fromisoformat(cache['cached_at'])
        age = datetime.now(timezone.utc) - cached_at
        is_fresh = age.total_seconds() < (MILITARY_CACHE_TTL_HOURS * 3600)
        if is_fresh:
            age_min = age.total_seconds() / 60
            print(f"[Military Cache] Fresh ({age_min:.0f}min old)")
        return is_fresh
    except:
        return False


def _build_empty_skeleton():
    """Return a valid but empty military posture response."""
    actor_summaries = {}
    for actor_id, actor_data in MILITARY_ACTORS.items():
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

    theatre_data = {}
    for theatre_id, theatre_info in REGIONAL_THEATRES.items():
        theatre_actors = {}
        for actor_id in theatre_info['actors']:
            if actor_id in actor_summaries:
                theatre_actors[actor_id] = actor_summaries[actor_id]
        theatre_data[theatre_id] = {
            'label': theatre_info['label'],
            'icon': theatre_info['icon'],
            'order': theatre_info['order'],
            'description': theatre_info['description'],
            'actors': theatre_actors,
            'total_score': 0,
            'alert_level': 'normal'
        }

    return {
        'success': True,
        'scan_time_seconds': 0,
        'days_analyzed': 7,
        'total_articles_scanned': 0,
        'total_signals_detected': 0,
        'active_actors': [],
        'active_actor_count': 0,
        'tension_multiplier': 1.0,
        'target_postures': {},
        'actor_summaries': actor_summaries,
        'theatre_groupings': theatre_data,
        'asset_distribution': {},
        'evacuation_alerts': [],
        'top_signals': [],
        'source_breakdown': {
            'defense_rss': 0,
            'gdelt': 0,
            'newsapi': 0,
            'reddit': 0
        },
        'last_updated': datetime.now(timezone.utc).isoformat(),
        'cached': False,
        'scan_in_progress': True,
        'message': 'Initial scan in progress. Data will appear shortly.',
        'version': '2.7.0'
    }


# ========================================
# DATA FETCHING — RSS FEEDS
# ========================================

def fetch_defense_rss(feed_name, feed_url, max_articles=15):
    """Fetch articles from a defense media RSS feed"""
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
        time.sleep(0.5)
    print(f"[Military RSS] Total defense RSS articles: {len(all_articles)}")
    return all_articles


# ========================================
# DATA FETCHING — GDELT
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
        response = None
        for attempt in range(2):
            try:
                response = requests.get(GDELT_BASE_URL, params=params, timeout=60)
                if response.status_code == 200:
                    break
            except requests.Timeout:
                if attempt == 0:
                    time.sleep(2)
                    continue
                raise
        if not response or response.status_code != 200:
            return []

        try:
            data = response.json()
        except (json.JSONDecodeError, ValueError):
            return []
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
    """Fetch military articles from GDELT across multiple queries and languages."""

    english_queries = [
        # --- CENTCOM / Middle East ---
        'military deployment middle east',
        'carrier strike group persian gulf',
        'military exercise middle east',
        'troops deployed middle east',
        'naval deployment mediterranean',
        'irgc military exercise',
        'iran strait hormuz drill',
        'chinese warship persian gulf',
        'russian navy mediterranean',
        'military base evacuation middle east',
        'embassy evacuation middle east',
        'voluntary departure military',
        'military families evacuation',
        'ordered departure embassy',
        'noncombatant evacuation operation',
        # --- Israel / IDF ---
        'IDF military operation',
        'Israel defense forces deployment',
        'Israel military buildup',
        'Israel reservists mobilization',
        'Iron Dome deployment',
        'Israeli airstrike',
        'Israel Hezbollah military',
        'IDF northern command',
        # --- Gulf States ---
        'jordan military base',
        'qatar al udeid',
        'saudi military exercise',
        'uae military',
        'kuwait camp arifjan',
        'egypt military exercise',
        'egypt sinai troops',
        # --- Turkey ---
        'turkish military operation syria',
        'turkey military exercise',
        'incirlik air base',
        # --- NATO / Europe ---
        'nato exercise arctic',
        'nato military deployment',
        'greenland military defense',
        'nato baltic deployment',
        # --- Ukraine / Russia war ---
        'ukraine military front',
        'russia ukraine offensive',
        'ukraine weapons delivery',
        'black sea military',
        'ukraine drone strike russia',
        'russia mobilization military',
        'crimea military attack',
        'ukraine front line advance',
        'russia missile strike ukraine',
        'ukraine air defense intercept',
        'kursk incursion ukraine',
        # --- Greenland / Arctic (v2.3.0) ---
        'greenland sovereignty dispute',
        'greenland trump acquisition',
        'arctic military buildup',
        'pituffik space base greenland',
        'greenland rare earth minerals',
        'greenland independence referendum',
        'denmark greenland military',
        'arctic nato exercise',
        'us arctic strategy',
        # --- Poland / Eastern Flank (v2.3.0) ---
        'poland military buildup',
        'poland defense spending',
        'poland nato eastern flank',
        'drone incursion poland',
        'drone entered polish airspace',
        'poland airspace violation',
        'poland belarus border crisis',
        'aegis ashore redzikowo poland',
        'us troops poland deployment',
        'poland scramble jets',
        'unidentified object polish airspace',
        'suwalki gap military',
        'poland military modernization',
        'poland F-35 purchase',
        'hybrid warfare poland border',
        # --- Iraq (v2.5.0) ---
        'Iraq militia attack US base',
        'Islamic Resistance Iraq drone',
        'Kataib Hezbollah attack',
        'Iraq ISIS resurgence',
        'US withdrawal Iraq',
        'US forces Iraq drawdown',
        'coalition forces Iraq attack',
        'PMF Popular Mobilization Iraq',
        'Al Asad airbase attack',
        'Erbil rocket attack',
        'Iraq airspace corridor Iran',
        'Iran proxy militia Iraq',
        'ISIS prisoners Iraq',
        'Operation Inherent Resolve Iraq',
        'Iraq sectarian violence',
        'Maliki Iraq government',
        'Peshmerga Kurdistan military',
        # v2.6.0 — Active Iran-Israel conflict + Bahrain
        'Iran missile strike Israel',
        'Iran attack Israel missiles',
        'Israel retaliates Iran',
        'Iran Israel war',
        'ballistic missile Israel intercept',
        'iron dome intercept barrage',
        'home front command rocket alert',
        'US military response Iran',
        'CENTCOM Iran strike',
        'Bahrain 5th Fleet alert',
        'Strait of Hormuz military',
        'regional war Middle East escalation',
        'Iran nuclear facilities strike',
        'airlines cancel Middle East war',
        # v2.7.2 — Israel active war GDELT queries
        'Israel Iran ballistic missile attack',
        'Israel iron dome intercept overwhelmed',
        'Israel home front command siren alert',
        'Israel Tel Aviv missile impact casualties',
        'Israel airspace closed war Iran',
        'Israel Ben Gurion airport closed missile',
        'Israel multi front war missile barrage',
        'Israel casualties missile strike dead wounded',
        'Israel bomb shelter siren red alert',
        'Israel war cabinet emergency session',
        # v2.7.0 — Gulf state + regional actor war queries
        'Kuwait Iranian missile attack',
        'Kuwait US soldiers killed',
        'Kuwait port drone strike',
        'Saudi Arabia Iranian missile Riyadh',
        'Saudi Aramco attack Iran',
        'Saudi air defense intercept',
        'UAE Dubai embassy attack',
        'UAE Abu Dhabi missile',
        'Al Dhafra air base attack',
        'Jordan intercept Iranian drone missile',
        'Jordan airspace ballistic missile',
        'Qatar Al Udeid missile hit',
        'Qatar flights suspended war',
        'Qatar airspace closed',
        'Oman Strait Hormuz military',
        'Oman Duqm naval base',
        'Egypt Suez Canal war disruption',
        'Egypt Sinai military buildup',
        'Turkey Incirlik base attack',
        'Turkey Iran border tensions',
        'Cyprus Akrotiri drone attack',
        'Cyprus evacuation Iran',
        'Cyprus flights cancelled war',
        'UK forces Cyprus reinforcement',
    ]

    hebrew_queries = [
        'צה"ל פריסה',
        'צה"ל תרגיל',
        'כיפת ברזל',
        'חיל האוויר תרגיל',
        'מילואים גיוס',
        'חזבאללה צפון',
        'פיקוד צפון כוננות',
        'חיל הים סיור',
        # v2.6.0 — Home Front Command / active war
        'פיקוד העורף התרעה',
        'צבע אדום טיל',
        'יירוט טיל בליסטי',
        'מטח רקטות איראן',
        'מלחמה איראן ישראל',
        # v2.7.2 — Israel active war Hebrew
        'פגיעה ישירה תל אביב',
        'נפגעים הרוגים פצועים טיל',
        'כיפת ברזל רווי נפילות',
        'נתב"ג סגור טיסות מבוטלות',
        'פינוי אזרחים מקלט',
    ]

    russian_queries = [
        'военная операция украина',
        'черноморский флот',
        'вооруженные силы учения',
        'ракетный удар украина',
        'мобилизация военная',
        'северный флот арктика',
        'военно-морской флот',
        'ПВО развертывание',
        'наступление фронт донецк',
        'наступление фронт запорожье',
        'артиллерия обстрел украина',
        'крылатая ракета удар',
        'баллистическая ракета удар',
        'дрон удар украина',
        'беспилотник атака',
        'БПЛА удар',
        'курск вторжение',
        'контрнаступление украина',
        'потери военные',
        'подкрепление войска',
        'фронт продвижение',
        'ядерная угроза',
        'мобилизация призыв',
    ]

    arabic_queries = [
        'الحرس الثوري تدريب',
        'قوات عسكرية الخليج',
        'تدريب عسكري السعودية',
        'القوات المسلحة الإماراتية',
        'الجيش المصري تدريب',
        'القوات الأردنية',
        'حزب الله عسكري',
        'صواريخ باليستية إيران',
        'القوات البحرية مضيق هرمز',
        'إخلاء قاعدة عسكرية',
        # v2.5.0 — Iraq Arabic queries
        'المقاومة الإسلامية العراق هجوم',
        'كتائب حزب الله هجوم قاعدة',
        'الحشد الشعبي عمليات',
        'داعش العراق هجوم',
        'الانسحاب الأمريكي العراق',
        'قاعدة عين الأسد هجوم',
        'القوات المسلحة العراقية',
        # v2.6.0 — Active conflict + Bahrain
        'حرب إيران إسرائيل',
        'هجوم صاروخي إيران إسرائيل',
        'الأسطول الخامس البحرين تأهب',
        'القوات الأمريكية تأهب قصوى',
        # v2.7.0 — Gulf state Arabic queries
        'الكويت هجوم صاروخي إيراني',
        'السعودية دفاع جوي اعتراض',
        'الإمارات دبي هجوم',
        'الأردن اعتراض صواريخ طائرات',
        'قطر العديد صاروخ',
        'عمان مضيق هرمز عسكري',
        'مصر قناة السويس حرب',
        'قبرص أكروتيري هجوم',
        # v2.7.2 — Israel war Arabic
        'إسرائيل صاروخ باليستي إيراني هجوم',
        'القبة الحديدية تل أبيب صاروخ',
        'إسرائيل حرب إيران قصف ضحايا',
    ]

    farsi_queries = [
        'سپاه پاسداران رزمایش',
        'نیروی دریایی رزمایش',
        'موشک بالستیک آزمایش',
        'پهپاد نظامی',
        'نیروی هوافضا سپاه',
        'تنگه هرمز رزمایش',
        # v2.6.0 — Active conflict
        'حمله به اسرائیل موشک',
        'جنگ ایران اسرائیل',
        'عملیات نظامی سپاه',
    ]

    turkish_queries = [
        'türk silahlı kuvvetleri operasyon',
        'türk donanması tatbikat',
        'suriye askeri operasyon',
        'bayraktar insansız hava',
        'incirlik üssü',
        # v2.7.0 — War queries
        'İncirlik üssü saldırı',
        'Türkiye hava savunma',
        'İran saldırı Türkiye',
        'füze saldırısı Türkiye',
    ]

    ukrainian_queries = [
        'збройні сили україни',
        'фронт наступ',
        'мобілізація військова',
        'протиповітряна оборона',
        'зброя постачання',
        'ракетний удар росія',
        'дрон атака',
        'артилерія обстріл',
        'контрнаступ запоріжжя',
        'фронт донецьк',
        'фронт луганськ',
        'курськ операція',
        'морський дрон чорне море',
        'F-16 Україна',
        'Patriot ППО',
        'HIMARS удар',
        'Storm Shadow ракета',
        'мобілізація призов',
        'військова допомога',
    ]

    french_queries = [
        'forces armées méditerranée',
        'base militaire djibouti',
        'opération militaire sahel',
    ]

    chinese_queries = [
        '军事演习 南海',
        '解放军 海军',
        '中国 军舰',
    ]

    polish_queries = [
        'wojsko polskie ćwiczenia',
        'siły zbrojne modernizacja',
        'dron nad Polską',
        'naruszenie przestrzeni powietrznej',
        'obrona powietrzna Polska',
        'NATO flanka wschodnia',
        'granica polsko-białoruska wojsko',
        'granica polsko-ukraińska incydent',
        'zakupy wojskowe Polska',
        'Patriot Polska',
        'F-35 Polska',
        'Redzikowo tarcza',
        'Suwałki korytarz',
        'bezzałogowiec granica',
    ]

    danish_norwegian_queries = [
        'grønland forsvar',
        'grønland suverænitet',
        'arktisk militær',
        'Pituffik base',
        'grønland NATO',
        'Danmark forsvar grønland',
        'arktisk sikkerhed',
        'forsvaret Arktis',
        'militær øvelse Arktis',
        'Grønland selvstændighed',
    ]

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
        (polish_queries, 'pol', 'Polish'),
        (danish_norwegian_queries, 'dan', 'Danish'),
    ]

    for queries, lang_code, lang_name in query_blocks:
        block_count = 0
        for query in queries:
            articles = fetch_gdelt_military(query, days, language=lang_code)
            all_articles.extend(articles)
            block_count += len(articles)
            time.sleep(0.5)
        if block_count > 0:
            print(f"[Military GDELT] {lang_name} ({lang_code}): {block_count} articles from {len(queries)} queries")

    print(f"[Military GDELT] Total GDELT military articles: {len(all_articles)}")
    return all_articles


# ========================================
# DATA FETCHING — NewsAPI
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
        'Poland military NATO',
        'drone Poland airspace',
        'Greenland sovereignty Arctic',
        # v2.5.0 — Iraq
        'Iraq militia attack coalition base',
        'Iraq ISIS military operation',
        # v2.6.0 — War footing
        'Bahrain 5th Fleet military alert',
        'Iran Israel war missile strike',
        # v2.7.0 — Gulf + regional
        'Kuwait Iran attack US soldiers',
        'Saudi Arabia Iranian missile defense',
        'UAE Dubai embassy attack missile',
        'Jordan intercept Iranian missiles drones',
        'Qatar Al Udeid base missile attack',
        'Cyprus Akrotiri Iran drone attack',
        'Oman military Strait Hormuz',
        # v2.7.2 — Israel war
        'Israel Iran missile attack ballistic',
        'Israel iron dome intercept war siren',
        'Israel home front command alert casualties',
        'Israel Tel Aviv Haifa missile impact',
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

    for subreddit in REDDIT_MILITARY_SUBREDDITS[:5]:
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
    """Scan article text for hotspot locations and return the highest multiplier."""
    max_multiplier = 1.0
    matched_location = None

    for location, multiplier in LOCATION_MULTIPLIERS.items():
        if location in text:
            if multiplier > max_multiplier:
                max_multiplier = multiplier
                matched_location = location

    return max_multiplier, matched_location


def get_evacuation_subtype_weight(text):
    """For base_evacuation signals, determine the specific sub-type."""
    for subtype_id, subtype_data in sorted(
        EVACUATION_SUBTYPE_WEIGHTS.items(),
        key=lambda x: x[1]['weight'],
        reverse=True
    ):
        for kw in subtype_data['keywords']:
            if kw in text:
                return subtype_data['weight'], subtype_id

    return ASSET_CATEGORIES['base_evacuation']['weight'], 'unspecified'


def analyze_article_military(article):
    """Analyze a single article for military deployment signals."""
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

    loc_multiplier, hotspot = get_location_multiplier(text)
    result['location_multiplier'] = loc_multiplier
    result['hotspot_location'] = hotspot

    for actor_id, actor_data in MILITARY_ACTORS.items():
        for keyword in actor_data['keywords']:
            if keyword in text:
                result['actors'].add(actor_id)
                actor_weight = actor_data['weight']

                asset_matched = False
                for asset_id, asset_data in ASSET_CATEGORIES.items():
                    for asset_kw in asset_data['keywords']:
                        if asset_kw in text:
                            result['asset_types'].add(asset_id)

                            if asset_id == 'base_evacuation':
                                asset_weight, evac_subtype = get_evacuation_subtype_weight(text)
                            else:
                                asset_weight = asset_data['weight']
                                evac_subtype = None

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

                break

    for aor, bases in ASSET_TARGET_MAPPING.items():
        for base_name, base_data in bases.items():
            if base_name.lower() in text:
                result['regions'].add(base_name)
                for target in base_data['targets']:
                    result['targets'].add(target)

    result['actors'] = list(result['actors'])
    result['asset_types'] = list(result['asset_types'])
    result['regions'] = list(result['regions'])
    result['targets'] = list(result['targets'])
    result['score'] = round(result['score'], 2)

    return result


def calculate_regional_tension_multiplier(active_actors):
    """Multiple militaries moving simultaneously = compounding tension."""
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
        return 1.5 + (0.05 * (count - 5))


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
    """Main entry point."""

    if not force_refresh and is_military_cache_fresh():
        cache = load_military_cache()
        cache['cached'] = True
        print("[Military Tracker] Returning fresh cached data")
        return cache

    if not force_refresh:
        stale_cache = load_military_cache()
        if stale_cache and 'cached_at' in stale_cache:
            stale_cache['cached'] = True
            stale_cache['stale'] = True
            _trigger_background_scan(days)
            print("[Military Tracker] Returning stale cache, background refresh triggered")
            return stale_cache

        print("[Military Tracker] No cache found, returning skeleton. Periodic scan will populate.")
        return _build_empty_skeleton()

    return _run_full_scan(days)


def _trigger_background_scan(days=7):
    """Start a background scan if one isn't already running."""
    global _background_scan_running

    with _background_scan_lock:
        if _background_scan_running:
            print("[Military Tracker] Background scan already in progress, skipping")
            return
        _background_scan_running = True

    def _do_scan():
        global _background_scan_running
        try:
            print("[Military Tracker] Background scan starting...")
            _run_full_scan(days)
        except Exception as e:
            print(f"[Military Tracker] Background scan error: {e}")
        finally:
            with _background_scan_lock:
                _background_scan_running = False

    thread = threading.Thread(target=_do_scan, daemon=True)
    thread.start()


def _run_full_scan(days=7):
    """Execute the full scan pipeline."""

    print(f"[Military Tracker] Starting fresh scan ({days} days)...")
    scan_start = time.time()

    print("[Military Tracker] Phase 1: Fetching data...")

    rss_articles = fetch_all_defense_rss()
    gdelt_articles = fetch_all_gdelt_military(days)
    newsapi_articles = fetch_all_newsapi_military(days)
    reddit_posts = fetch_reddit_military(days)

    all_articles = rss_articles + gdelt_articles + newsapi_articles + reddit_posts

    print(f"[Military Tracker] Total articles to analyze: {len(all_articles)}")

    print("[Military Tracker] Phase 2: Analyzing articles...")

    all_signals = []
    per_target_scores = {}
    per_actor_scores = {}
    active_actors = set()
    asset_type_counts = {}
    evacuation_signals = []

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

                if asset == 'base_evacuation':
                    evacuation_signals.append(signal)

    tension_multiplier = calculate_regional_tension_multiplier(active_actors)

    print(f"[Military Tracker] Active actors: {len(active_actors)} → Tension multiplier: {tension_multiplier}x")

    for target in per_target_scores:
        per_target_scores[target] = round(per_target_scores[target] * tension_multiplier, 2)

    target_postures = {}

    for target, score in per_target_scores.items():
        alert_level = determine_alert_level(score)
        threshold = ALERT_THRESHOLDS[alert_level]
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
        'version': '2.7.0'
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
    """Quick lookup for a specific target's military posture."""
    try:
        data = scan_military_posture()

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
# FLASK ENDPOINT REGISTRATION
# ========================================

def register_military_endpoints(app):
    """Register military tracker endpoints with the Flask app."""

    @app.route('/api/military-posture', methods=['GET', 'OPTIONS'])
    def api_military_posture():
        """Full military posture assessment for military.html"""
        from flask import request as flask_request, jsonify

        if flask_request.method == 'OPTIONS':
            return '', 200

        try:
            days = int(flask_request.args.get('days', 7))
            refresh = flask_request.args.get('refresh', 'false').lower() == 'true'

            if refresh:
                _trigger_background_scan(days)
            result = scan_military_posture(days=days, force_refresh=False)
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

    @app.route('/api/military-posture/<target>', methods=['GET', 'OPTIONS'])
    def api_military_posture_target(target):
        """Quick posture check for a specific target."""
        from flask import request as flask_request

        if flask_request.method == 'OPTIONS':
            return '', 200

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

    # PERIODIC BACKGROUND SCAN (every 12 hours)
    def _periodic_scan():
        time.sleep(10)
        while True:
            try:
                print("[Military Tracker] Periodic scan starting...")
                _trigger_background_scan(days=7)
                time.sleep(60)
                while _background_scan_running:
                    time.sleep(30)
                print("[Military Tracker] Periodic scan complete. Sleeping 4 hours (war footing).")
                time.sleep(14400)
            except Exception as e:
                print(f"[Military Tracker] Periodic scan error: {e}")
                time.sleep(3600)

    periodic_thread = threading.Thread(target=_periodic_scan, daemon=True)
    periodic_thread.start()
