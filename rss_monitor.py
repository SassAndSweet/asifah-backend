"""
RSS Monitor for Asifah Analytics
Comprehensive RSS feed monitoring for Middle East intelligence

v3.2.0 — February 2026

Monitors:
1. Leadership Rhetoric (MEMRI, Al-Manar, Iran Wire)
2. Israeli News Sources (Ynet, Times of Israel, JPost, i24NEWS, Haaretz)
3. Regional Arab News Sources (Arab News)
4. Airline Flight Disruptions — CACHED, background refresh every 12h

Leaders monitored:
- Naim Qassem (Hezbollah Secretary-General)
- Ali Khamenei (Iran Supreme Leader)
- Abdul-Malik al-Houthi (Houthi leader)
- Israeli leadership (Netanyahu, Gallant, Halevi)
"""

import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path
import threading
import json
import time
import re


# ========================================
# RSS FEEDS - LEADERSHIP & NEWS
# ========================================
LEADERSHIP_RSS_FEEDS = {
    'memri': 'https://news.google.com/rss/search?q=site:memri.org&hl=en&gl=US&ceid=US:en',
    'al_manar_en': 'https://english.almanar.com.lb/rss',
    'al_manar_ar': 'https://almanar.com.lb/rss',
    'iran_wire_en': 'https://iranwire.com/en/feed/',
    'iran_wire_fa': 'https://iranwire.com/fa/feed/',
}

ISRAELI_RSS_FEEDS = {
    'ynet': 'https://www.ynetnews.com/Integration/StoryRss3254.xml',
    'times_of_israel': 'https://news.google.com/rss/search?q=site:timesofisrael.com&hl=en&gl=US&ceid=US:en',
    'jpost': 'https://www.jpost.com/rss/rssfeedsheadlines.aspx',
    'i24news': 'https://news.google.com/rss/search?q=site:i24news.tv&hl=en&gl=US&ceid=US:en',
    'haaretz': 'https://www.haaretz.com/srv/haaretz-latest-news',
}

# Regional Arab News Sources
REGIONAL_ARAB_RSS_FEEDS = {
    'arab_news': 'https://news.google.com/rss/search?q=site:arabnews.com+middle+east&hl=en&gl=US&ceid=US:en',
}

# Combine all feeds
ALL_RSS_FEEDS = {**LEADERSHIP_RSS_FEEDS, **ISRAELI_RSS_FEEDS, **REGIONAL_ARAB_RSS_FEEDS}


# ========================================
# LEADERSHIP NAMES (Multi-language)
# ========================================
LEADERSHIP_NAMES = {
    'naim_qassem': {
        'names': [
            'Naim Qassem', 'Naim Kassem', 'نعيم قاسم',
            'Sheikh Naim Qassem', 'Qassem'
        ],
        'titles': ['Secretary-General', 'Hezbollah leader', 'Hezbollah chief'],
        'organization': 'hezbollah'
    },
    'khamenei': {
        'names': [
            'Ali Khamenei', 'Ayatollah Khamenei', 'خامنه‌ای',
            'علی خامنه‌ای', 'Supreme Leader Khamenei'
        ],
        'titles': ['Supreme Leader', 'Ayatollah', 'Iran\'s leader'],
        'organization': 'iran'
    },
    'abdul_malik_houthi': {
        'names': [
            'Abdul-Malik al-Houthi', 'Abdulmalik al-Houthi',
            'عبدالملك الحوثي', 'Abdul Malik Houthi'
        ],
        'titles': ['Houthi leader', 'Ansar Allah leader'],
        'organization': 'houthis'
    },
    'netanyahu': {
        'names': [
            'Benjamin Netanyahu', 'Netanyahu', 'ביבי', 'Bibi',
            'Prime Minister Netanyahu'
        ],
        'titles': ['Prime Minister', 'PM Netanyahu', 'Israeli PM'],
        'organization': 'israel'
    },
    'gallant': {
        'names': [
            'Yoav Gallant', 'Gallant', 'Defense Minister Gallant'
        ],
        'titles': ['Defense Minister', 'Israeli Defense Minister'],
        'organization': 'israel'
    },
    'halevi': {
        'names': [
            'Herzi Halevi', 'Halevi', 'IDF Chief Halevi'
        ],
        'titles': ['IDF Chief of Staff', 'Chief of Staff', 'IDF Commander'],
        'organization': 'israel'
    }
}


# ========================================
# CONTEXT DETECTION
# ========================================
CONTEXT_INDICATORS = {
    'domestic': [
        'مقاومة', 'شعب', 'أمة', 'إخوان', 'شهداء',
        'resistance', 'our people', 'our nation', 'brothers', 'martyrs',
        'internal', 'domestic', 'lebanese people', 'iranian people',
        'friday prayers', 'sermon', 'israeli public', 'security cabinet'
    ],
    'international': [
        'israel', 'israeli', 'إسرائيل', 'zionist', 'صهيوني',
        'america', 'american', 'أمريكا', 'united states', 'us forces',
        'washington', 'tel aviv', 'واشنطن', 'تل أبيب',
        'hezbollah', 'hamas', 'iran', 'tehran', 'lebanon', 'beirut',
        'will strike', 'سنضرب', 'will attack', 'سنهاجم',
        'retaliate', 'revenge', 'response', 'انتقام', 'رد'
    ],
    'operational': [
        'prepared to', 'ready to', 'מוכנים', 'مستعدون', 'جاهزون',
        'target', 'targets', 'أهداف', 'هدف', 'מטרות',
        'missile', 'missiles', 'صواريخ', 'صاروخ', 'טילים',
        'drone', 'drones', 'طائرة مسيرة', 'طائرات', 'כטב"מ',
        'military operation', 'عملية عسكرية', 'מבצע צבאי',
        'if they strike', 'إذا ضربوا', 'if attacked', 'אם יתקפו',
        'idf announces', 'idf strikes', 'idf operation'
    ]
}


# ========================================
# THREAT LANGUAGE DETECTION
# ========================================
THREAT_KEYWORDS = {
    'explicit_threat': [
        'will strike', 'will attack', 'will retaliate', 'will respond',
        'سنضرب', 'سنهاجم', 'سنرد', 'سننتقم',
        'promised to strike', 'vowed to attack', 'pledged to respond',
        'נתקוף', 'נגיב'
    ],
    'conditional_threat': [
        'if israel', 'if america', 'if they attack', 'if violated',
        'إذا ضربت', 'إذا هاجمت', 'لو ضربوا',
        'should israel', 'were israel to', 'אם ישראל'
    ],
    'capability_signal': [
        'our missiles', 'our weapons', 'our capabilities',
        'صواريخنا', 'أسلحتنا', 'قدراتنا', 'הטילים שלנו',
        'can reach', 'able to strike', 'within range',
        'idf capable', 'israeli capability'
    ]
}


# ========================================
# LEADERSHIP CONTEXT WEIGHTS
# ========================================
LEADERSHIP_WEIGHTS = {
    'naim_qassem': {
        'domestic': 1.3,
        'international': 1.8,
        'operational': 2.2
    },
    'khamenei': {
        'domestic': 1.2,
        'international': 2.0,
        'operational': 2.5
    },
    'abdul_malik_houthi': {
        'domestic': 1.1,
        'international': 1.5,
        'operational': 2.0
    },
    'netanyahu': {
        'domestic': 1.2,
        'international': 1.7,
        'operational': 2.3
    },
    'gallant': {
        'domestic': 1.1,
        'international': 1.6,
        'operational': 2.4
    },
    'halevi': {
        'domestic': 1.0,
        'international': 1.5,
        'operational': 2.5
    }
}


# ========================================
# RSS FETCHING FUNCTIONS
# ========================================
def fetch_all_rss(feed_dict=None):
    """
    Fetch all RSS feeds (leadership + Israeli + regional Arab + any additional)

    Args:
        feed_dict: Optional custom feed dictionary, defaults to ALL_RSS_FEEDS

    Returns: List of articles
    """
    if feed_dict is None:
        feed_dict = ALL_RSS_FEEDS

    all_articles = []

    for feed_name, feed_url in feed_dict.items():
        try:
            print(f"[RSS] Fetching {feed_name}...")

            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'application/rss+xml, application/xml, text/xml, */*'
            }

            response = requests.get(feed_url, headers=headers, timeout=15)

            if response.status_code != 200:
                print(f"[RSS] {feed_name} HTTP {response.status_code}")
                continue

            try:
                root = ET.fromstring(response.content)
            except ET.ParseError as e:
                print(f"[RSS] {feed_name} XML parse error: {e}")
                continue

            items = root.findall('.//item')

            for item in items[:15]:
                title_elem = item.find('title')
                link_elem = item.find('link')
                pubDate_elem = item.find('pubDate')
                description_elem = item.find('description')
                content_elem = item.find('{http://purl.org/rss/1.0/modules/content/}encoded')

                if title_elem is None or link_elem is None:
                    continue

                pub_date = pubDate_elem.text if pubDate_elem is not None else datetime.now(timezone.utc).isoformat()

                description = ''
                if description_elem is not None and description_elem.text:
                    description = description_elem.text[:500]
                elif content_elem is not None and content_elem.text:
                    description = content_elem.text[:500]

                # Determine language
                lang = 'en'
                if 'ar' in feed_name or 'arabic' in feed_url.lower():
                    lang = 'ar'
                elif 'fa' in feed_name or 'farsi' in feed_url.lower():
                    lang = 'fa'
                elif 'haaretz' in feed_name:
                    lang = 'he'

                # Determine source display name
                source_names = {
                    'ynet': 'Ynet',
                    'times_of_israel': 'Times of Israel',
                    'jpost': 'Jerusalem Post',
                    'i24news': 'i24NEWS',
                    'haaretz': 'Haaretz',
                    'memri': 'MEMRI',
                    'al_manar_en': 'Al-Manar (EN)',
                    'al_manar_ar': 'Al-Manar (AR)',
                    'iran_wire_en': 'Iran Wire',
                    'iran_wire_fa': 'Iran Wire (FA)',
                    'arab_news': 'Arab News',
                }
                source_display = source_names.get(feed_name, feed_name.upper().replace('_', ' '))

                all_articles.append({
                    'title': title_elem.text or '',
                    'description': description,
                    'url': link_elem.text or '',
                    'publishedAt': pub_date,
                    'source': {'name': source_display},
                    'content': description,
                    'language': lang
                })

            print(f"[RSS] ✅ {feed_name}: {len(items)} articles")

        except Exception as e:
            print(f"[RSS] {feed_name} error: {str(e)[:100]}")
            continue

    print(f"[RSS] Total fetched: {len(all_articles)} articles from {len(feed_dict)} feeds")
    return all_articles


def fetch_leadership_rss():
    """Fetch only leadership RSS feeds"""
    return fetch_all_rss(LEADERSHIP_RSS_FEEDS)


def fetch_israeli_rss():
    """Fetch only Israeli news RSS feeds"""
    return fetch_all_rss(ISRAELI_RSS_FEEDS)


# ========================================
# AIRLINE FLIGHT DISRUPTION MONITORING
# Cached system — background refresh every 12h
# ========================================

FLIGHT_CACHE_FILE = '/tmp/flight_disruptions_cache.json'
_flight_scan_running = False
_flight_scan_lock = threading.Lock()

# Comprehensive airline list
MONITORED_AIRLINES = [
    # Star Alliance
    'Lufthansa', 'United Airlines', 'United', 'Air Canada', 'Turkish Airlines',
    'Swiss', 'SWISS', 'Austrian Airlines', 'Austrian', 'Singapore Airlines',
    'LOT Polish', 'ANA', 'Air India', 'Scandinavian Airlines', 'SAS',
    'TAP Air Portugal',
    # SkyTeam
    'Air France', 'KLM', 'Delta', 'Delta Airlines', 'Korean Air',
    'ITA Airways', 'Alitalia', 'Aeroflot', 'Vietnam Airlines',
    'China Airlines',
    # Oneworld
    'British Airways', 'American Airlines', 'American', 'Cathay Pacific',
    'Qantas', 'Japan Airlines', 'JAL', 'Iberia', 'Finnair', 'Qatar Airways',
    # Middle East carriers
    'Emirates', 'Etihad', 'flydubai', 'Air Arabia',
    'Saudia', 'Saudi Arabian Airlines', 'Gulf Air', 'Kuwait Airways',
    'Royal Jordanian', 'Oman Air', 'Middle East Airlines', 'MEA',
    # Israeli carriers
    'El Al', 'Arkia', 'Israir',
    # Low-cost carriers
    'Wizz Air', 'Ryanair', 'EasyJet', 'Pegasus Airlines', 'IndiGo',
    'AirAsia', 'Jetstar', 'Norwegian', 'Vueling', 'Transavia',
    # Regional carriers
    'Air Astana', 'Azerbaijan Airlines', 'Georgian Airways', 'Belavia',
    'Ukraine International', 'Aegean Airlines', 'Croatia Airlines',
    # Other major carriers
    'Ethiopian Airlines', 'EgyptAir', 'Egypt Air', 'Air New Zealand',
    'South African Airways', 'Kenya Airways', 'Royal Air Maroc',
]

# Airline group mappings — if headline says "Lufthansa Group", resolve to primary airline
AIRLINE_GROUPS = {
    'Lufthansa Group': 'Lufthansa',
    'IAG': 'British Airways',
    'Air France-KLM': 'Air France',
}

# All Middle East destinations to monitor
MONITORED_DESTINATIONS = [
    # High priority (conflict zones)
    'Tel Aviv', 'Israel', 'Beirut', 'Lebanon', 'Damascus', 'Syria',
    'Tehran', 'Iran', 'Baghdad', 'Iraq', 'Sanaa', 'Yemen',
    # Regional capitals
    'Amman', 'Jordan', 'Dubai', 'UAE', 'Riyadh', 'Saudi Arabia',
    'Cairo', 'Egypt', 'Istanbul', 'Turkey', 'Doha', 'Qatar',
    'Muscat', 'Oman', 'Kuwait', 'Bahrain', 'Jeddah', 'Erbil',
]

DISRUPTION_SEARCH_KEYWORDS = [
    'airline suspended flights',
    'airline cancelled flights',
    'flight cancellation',
    'suspend service',
    'cancel flights',
    'resume flights',
    'flights suspended until',
    'halt flights',
]


def _load_flight_cache():
    """Load flight disruption cache from /tmp file"""
    try:
        if Path(FLIGHT_CACHE_FILE).exists():
            with open(FLIGHT_CACHE_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        print(f"[Flight Cache] Load error: {e}")
    return {}


def _save_flight_cache(data):
    """Save flight disruption cache to /tmp file"""
    try:
        with open(FLIGHT_CACHE_FILE, 'w') as f:
            json.dump(data, f, indent=2)
        print(f"[Flight Cache] ✅ Saved {len(data.get('cancellations', []))} disruptions to cache")
    except Exception as e:
        print(f"[Flight Cache] Save error: {e}")


def _is_flight_cache_fresh(cache_data, max_age_hours=12):
    """Check if flight cache is fresh enough"""
    if not cache_data or 'cached_at' not in cache_data:
        return False
    try:
        cached_at = datetime.fromisoformat(cache_data['cached_at'])
        age = datetime.now(timezone.utc) - cached_at
        return age.total_seconds() < (max_age_hours * 3600)
    except Exception:
        return False


def _extract_airline_from_title(title):
    """Extract airline name from news headline"""
    title_lower = title.lower()

    # Check airline groups first (e.g. "Lufthansa Group" → "Lufthansa")
    for group_name, primary_airline in AIRLINE_GROUPS.items():
        if group_name.lower() in title_lower:
            return primary_airline

    # Check individual airlines
    for airline in MONITORED_AIRLINES:
        if airline.lower() in title_lower:
            return airline

    # Try sentence pattern: "[Airline] suspends/cancels"
    pattern = re.search(
        r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+(?:suspend|cancel|halt|resume|restart)',
        title, re.IGNORECASE
    )
    if pattern:
        potential = pattern.group(1)
        if len(potential) > 3 and potential not in ['United States', 'Middle East', 'European Union']:
            return potential

    # Pattern 2: Capitalized word before action verb
    words = title.split()
    for i, word in enumerate(words):
        if len(word) > 3 and word[0].isupper() and i < len(words) - 1:
            next_word = words[i + 1].lower()
            if next_word in ['suspend', 'suspends', 'cancel', 'cancels', 'halt',
                             'halts', 'resume', 'resumes', 'pause', 'pauses']:
                return word

    return "Unknown Airline"


def _extract_status_from_title(title):
    """Extract flight status from headline"""
    title_lower = title.lower()

    if 'resume' in title_lower or 'restart' in title_lower or 'return' in title_lower:
        return 'Resumed'
    elif 'cancel' in title_lower:
        return 'Cancelled'
    elif 'suspend' in title_lower or 'halt' in title_lower or 'stop' in title_lower:
        return 'Suspended'
    return 'Disrupted'


def _extract_duration_from_title(title):
    """Extract duration from headline"""
    # "until [date]"
    until_match = re.search(r'until\s+([A-Za-z]+\s+\d{1,2}(?:,?\s+\d{4})?)', title, re.IGNORECASE)
    if until_match:
        return f"Until {until_match.group(1)}"

    # Specific months
    months = ['January', 'February', 'March', 'April', 'May', 'June',
              'July', 'August', 'September', 'October', 'November', 'December']
    for month in months:
        if month.lower() in title.lower():
            year_match = re.search(r'\b(202[4-9])\b', title)
            if year_match:
                return f"Until {month} {year_match.group(1)}"
            return f"Until {month}"

    # "for X days/weeks/months"
    for_match = re.search(r'for\s+(\d+)\s+(day|week|month)s?', title, re.IGNORECASE)
    if for_match:
        num = for_match.group(1)
        unit = for_match.group(2)
        return f"For {num} {unit}{'s' if int(num) > 1 else ''}"

    if 'indefinite' in title.lower():
        return 'Indefinite'

    return 'Unknown'


def _parse_rss_date(pub_date):
    """Parse RSS pub date to ISO format"""
    try:
        if pub_date:
            from email.utils import parsedate_to_datetime
            date_obj = parsedate_to_datetime(pub_date)
            return date_obj.isoformat()
    except Exception:
        pass
    return datetime.now(timezone.utc).isoformat()


def _run_flight_disruption_scan():
    """
    Full scan for flight disruptions across all destinations.
    This is the heavy operation — runs in background thread.

    Searches Google News RSS for airline disruptions to Middle East.
    Returns list of disruption dicts.
    """
    print(f"\n[Flight Scan] Starting full disruption scan at {datetime.now(timezone.utc).isoformat()}")
    start_time = time.time()

    all_disruptions = []
    seen_urls = set()

    # Strategy: search each destination with disruption keywords
    for destination in MONITORED_DESTINATIONS:
        for keyword in DISRUPTION_SEARCH_KEYWORDS[:3]:  # Top 3 keywords per destination
            query = f'{keyword} {destination}'

            try:
                url = f"https://news.google.com/rss/search?q={query.replace(' ', '+')}&hl=en&gl=US&ceid=US:en"

                response = requests.get(url, timeout=10, headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                })

                if response.status_code != 200:
                    continue

                try:
                    root = ET.fromstring(response.content)
                except ET.ParseError:
                    continue

                items = root.findall('.//item')

                for item in items[:5]:  # Top 5 per query
                    title_elem = item.find('title')
                    link_elem = item.find('link')
                    pubDate_elem = item.find('pubDate')

                    if title_elem is None or link_elem is None:
                        continue

                    title = title_elem.text or ''
                    link = link_elem.text or ''
                    pub_date = pubDate_elem.text if pubDate_elem is not None else ''

                    if link in seen_urls:
                        continue
                    seen_urls.add(link)

                    # Verify the headline is actually about a disruption
                    title_lower = title.lower()
                    if not any(kw in title_lower for kw in
                               ['suspend', 'cancel', 'halt', 'resume', 'restart',
                                'stop', 'disrupt', 'ground', 'delay']):
                        continue

                    # Extract origin city if mentioned
                    origin = 'Various'
                    origin_cities = [
                        'Frankfurt', 'Paris', 'London', 'New York', 'Dubai', 'Istanbul',
                        'Munich', 'Vienna', 'Warsaw', 'Amsterdam', 'Madrid', 'Rome',
                        'Zurich', 'Geneva', 'Delhi', 'Mumbai', 'Singapore', 'Hong Kong',
                        'Athens', 'Toronto', 'Chicago', 'Los Angeles', 'Berlin',
                        'Stockholm', 'Copenhagen', 'Helsinki', 'Lisbon', 'Brussels',
                        'Milan', 'Doha', 'Abu Dhabi', 'Seoul', 'Tokyo', 'Bangkok',
                    ]
                    for city in origin_cities:
                        if city.lower() in title_lower:
                            origin = city
                            break

                    disruption = {
                        'airline': _extract_airline_from_title(title),
                        'route': f"{origin} → {destination}",
                        'origin': origin,
                        'destination': destination,
                        'date': _parse_rss_date(pub_date),
                        'duration': _extract_duration_from_title(title),
                        'status': _extract_status_from_title(title),
                        'source_url': link,
                        'headline': title[:150]
                    }

                    all_disruptions.append(disruption)

            except Exception as e:
                print(f"[Flight Scan] Error for {destination}: {str(e)[:100]}")
                continue

        # Small delay between destinations to be polite to Google
        time.sleep(0.5)

    # Also search by specific airline name (catches airline-first headlines)
    for airline in MONITORED_AIRLINES[:15]:  # Top 15 airlines
        query = f'{airline} suspend cancel flights Middle East'
        try:
            url = f"https://news.google.com/rss/search?q={query.replace(' ', '+')}&hl=en&gl=US&ceid=US:en"
            response = requests.get(url, timeout=10, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            })
            if response.status_code != 200:
                continue

            root = ET.fromstring(response.content)
            items = root.findall('.//item')

            for item in items[:3]:
                title_elem = item.find('title')
                link_elem = item.find('link')
                pubDate_elem = item.find('pubDate')

                if title_elem is None or link_elem is None:
                    continue

                title = title_elem.text or ''
                link = link_elem.text or ''
                pub_date = pubDate_elem.text if pubDate_elem is not None else ''

                if link in seen_urls:
                    continue
                seen_urls.add(link)

                title_lower = title.lower()
                if not any(kw in title_lower for kw in
                           ['suspend', 'cancel', 'halt', 'resume', 'restart', 'stop']):
                    continue

                # Try to detect destination from title
                dest_found = 'Middle East'
                for dest in MONITORED_DESTINATIONS:
                    if dest.lower() in title_lower:
                        dest_found = dest
                        break

                disruption = {
                    'airline': airline,
                    'route': f"Various → {dest_found}",
                    'origin': 'Various',
                    'destination': dest_found,
                    'date': _parse_rss_date(pub_date),
                    'duration': _extract_duration_from_title(title),
                    'status': _extract_status_from_title(title),
                    'source_url': link,
                    'headline': title[:150]
                }
                all_disruptions.append(disruption)

        except Exception:
            continue
        time.sleep(0.3)

    # ========================================
    # POST-PROCESSING
    # ========================================

    # Filter to last 30 days
    thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
    recent = []
    for d in all_disruptions:
        try:
            d_date = datetime.fromisoformat(d['date'].replace('Z', '+00:00'))
            if d_date >= thirty_days_ago:
                recent.append(d)
        except Exception:
            recent.append(d)  # Include if date parse fails

    # Sort by date (newest first)
    recent.sort(key=lambda x: x.get('date', ''), reverse=True)

    # Deduplicate by airline + destination (keep newest)
    unique = []
    seen_combos = set()
    for d in recent:
        combo = f"{d['airline']}_{d['destination']}"
        if combo not in seen_combos:
            seen_combos.add(combo)
            unique.append(d)

    elapsed = time.time() - start_time
    print(f"[Flight Scan] ✅ Complete in {elapsed:.1f}s — {len(unique)} unique disruptions (from {len(all_disruptions)} raw)")

    return unique


def _trigger_flight_background_scan():
    """Trigger a background scan if one isn't already running"""
    global _flight_scan_running
    with _flight_scan_lock:
        if _flight_scan_running:
            print("[Flight Scan] Background scan already in progress, skipping")
            return
        _flight_scan_running = True

    def _do_scan():
        global _flight_scan_running
        try:
            disruptions = _run_flight_disruption_scan()

            cache_data = {
                'success': True,
                'cancellations': disruptions,
                'count': len(disruptions),
                'last_updated': datetime.now(timezone.utc).isoformat(),
                'cached_at': datetime.now(timezone.utc).isoformat(),
                'version': '3.2.0'
            }

            _save_flight_cache(cache_data)

        except Exception as e:
            print(f"[Flight Scan] Background scan error: {e}")
            import traceback
            traceback.print_exc()
        finally:
            with _flight_scan_lock:
                _flight_scan_running = False

    threading.Thread(target=_do_scan, daemon=True).start()
    print("[Flight Scan] Background scan thread started")


def _flight_periodic_scan_thread():
    """
    Background thread — scans every 12 hours.
    First scan after 20s delay (let app boot first).
    """
    SCAN_INTERVAL = 12 * 60 * 60  # 12 hours
    INITIAL_DELAY = 20  # seconds

    print(f"[Flight Scan] Periodic thread started — {SCAN_INTERVAL // 3600}h interval, {INITIAL_DELAY}s initial delay")
    time.sleep(INITIAL_DELAY)

    while True:
        try:
            print(f"\n[Flight Scan] Periodic scan starting at {datetime.now(timezone.utc).isoformat()}")
            disruptions = _run_flight_disruption_scan()

            cache_data = {
                'success': True,
                'cancellations': disruptions,
                'count': len(disruptions),
                'last_updated': datetime.now(timezone.utc).isoformat(),
                'cached_at': datetime.now(timezone.utc).isoformat(),
                'version': '3.2.0'
            }

            _save_flight_cache(cache_data)

        except Exception as e:
            print(f"[Flight Scan] Periodic scan error: {e}")
            import traceback
            traceback.print_exc()

        print(f"[Flight Scan] Next scan in {SCAN_INTERVAL // 3600} hours")
        time.sleep(SCAN_INTERVAL)


def fetch_airline_disruptions():
    """
    Public API — returns cached disruptions (non-blocking).
    If no cache exists, triggers background scan and returns empty list.

    This is the function imported by app.py.
    """
    cache = _load_flight_cache()

    if cache and 'cancellations' in cache:
        if _is_flight_cache_fresh(cache, max_age_hours=12):
            print(f"[Flight Cache] Returning {len(cache['cancellations'])} cached disruptions (fresh)")
            return cache['cancellations']
        else:
            # Stale but usable — return it and trigger background refresh
            print(f"[Flight Cache] Returning {len(cache['cancellations'])} cached disruptions (stale, triggering refresh)")
            _trigger_flight_background_scan()
            return cache['cancellations']

    # No cache at all — trigger scan, return empty
    print("[Flight Cache] No cache found, triggering background scan")
    _trigger_flight_background_scan()
    return []


def register_flight_scan_thread():
    """
    Start the periodic flight scan background thread.
    Call this from app.py after app initialization.
    """
    thread = threading.Thread(target=_flight_periodic_scan_thread, daemon=True)
    thread.start()
    print("[Flight Scan] ✅ Periodic scan thread registered (12h cycle)")


def get_flight_cache_for_endpoint():
    """
    Returns the full cache response for the /flight-cancellations endpoint.
    Non-blocking — returns cache or empty skeleton.
    """
    cache = _load_flight_cache()

    if cache and 'cancellations' in cache:
        fresh = _is_flight_cache_fresh(cache, max_age_hours=12)

        if not fresh:
            _trigger_flight_background_scan()
            cache['stale'] = True

        return {
            'success': True,
            'cancellations': cache.get('cancellations', [])[:20],
            'count': cache.get('count', 0),
            'last_updated': cache.get('last_updated', ''),
            'cached': True,
            'stale': not fresh,
            'version': '3.2.0'
        }

    # No cache — trigger scan, return skeleton
    _trigger_flight_background_scan()
    return {
        'success': True,
        'cancellations': [],
        'count': 0,
        'last_updated': None,
        'cached': False,
        'scan_in_progress': True,
        'message': 'Initial scan in progress. Disruption data will appear shortly.',
        'version': '3.2.0'
    }


# ========================================
# LEADERSHIP DETECTION
# ========================================
def detect_leadership_quote(article):
    """
    Detect if article contains leadership quote

    Returns dict with has_leadership, leader, context, weight_multiplier, etc.
    """
    title = article.get('title') or ''
    description = article.get('description') or ''
    content = article.get('content') or ''
    full_text = f"{title} {description} {content}".lower()

    result = {
        'has_leadership': False,
        'leader': None,
        'leader_name': None,
        'organization': None,
        'context': 'domestic',
        'weight_multiplier': 1.0,
        'threat_level': 'none',
        'quote_snippet': ''
    }

    # Check for leadership names
    for leader_key, leader_data in LEADERSHIP_NAMES.items():
        for name in leader_data['names']:
            if name.lower() in full_text:
                result['has_leadership'] = True
                result['leader'] = leader_key
                result['leader_name'] = leader_data['names'][0]
                result['organization'] = leader_data['organization']
                break

        if not result['has_leadership']:
            for title_word in leader_data['titles']:
                if title_word.lower() in full_text:
                    result['has_leadership'] = True
                    result['leader'] = leader_key
                    result['leader_name'] = leader_data['names'][0]
                    result['organization'] = leader_data['organization']
                    break

        if result['has_leadership']:
            break

    if not result['has_leadership']:
        return result

    result['context'] = classify_context(full_text)
    result['threat_level'] = detect_threat_level(full_text)
    result['weight_multiplier'] = calculate_leadership_weight(
        result['leader'], result['context'], result['threat_level']
    )

    for name in LEADERSHIP_NAMES[result['leader']]['names']:
        if name.lower() in full_text:
            idx = full_text.lower().find(name.lower())
            snippet_start = max(0, idx - 20)
            snippet_end = min(len(full_text), idx + 100)
            result['quote_snippet'] = full_text[snippet_start:snippet_end].strip()
            break

    return result


def classify_context(text):
    """Classify statement as domestic, international, or operational"""
    if not text:
        return 'domestic'

    text_lower = text.lower()

    operational_score = sum(1 for keyword in CONTEXT_INDICATORS['operational'] if keyword in text_lower)
    if operational_score >= 2:
        return 'operational'

    international_score = sum(1 for keyword in CONTEXT_INDICATORS['international'] if keyword in text_lower)
    if international_score >= 2:
        return 'international'

    domestic_score = sum(1 for keyword in CONTEXT_INDICATORS['domestic'] if keyword in text_lower)
    if domestic_score >= 2:
        return 'domestic'

    if any(word in text_lower for word in ['israel', 'america', 'united states', 'hezbollah', 'iran']):
        return 'international'

    return 'domestic'


def detect_threat_level(text):
    """Detect threat level: explicit, conditional, capability, or none"""
    if not text:
        return 'none'

    text_lower = text.lower()

    for keyword in THREAT_KEYWORDS['explicit_threat']:
        if keyword in text_lower:
            return 'explicit'

    for keyword in THREAT_KEYWORDS['conditional_threat']:
        if keyword in text_lower:
            return 'conditional'

    for keyword in THREAT_KEYWORDS['capability_signal']:
        if keyword in text_lower:
            return 'capability'

    return 'none'


def calculate_leadership_weight(leader_key, context, threat_level):
    """Calculate final weight multiplier for leadership statement"""
    base_weight = LEADERSHIP_WEIGHTS.get(leader_key, {}).get(context, 1.0)

    threat_multipliers = {
        'explicit': 1.3,
        'conditional': 1.15,
        'capability': 1.1,
        'none': 1.0
    }

    threat_multiplier = threat_multipliers.get(threat_level, 1.0)
    final_weight = base_weight * threat_multiplier

    return round(final_weight, 2)


# ========================================
# INTEGRATION WITH EXISTING SCORING
# ========================================
def enhance_article_with_leadership(article):
    """Add leadership detection data to article object"""
    leadership_data = detect_leadership_quote(article)

    if leadership_data['has_leadership']:
        print(f"[Leadership] ✅ Detected: {leadership_data['leader_name']} "
              f"({leadership_data['context']}, {leadership_data['threat_level']}) "
              f"weight: {leadership_data['weight_multiplier']}x")

    return leadership_data


def apply_leadership_multiplier(base_score, article):
    """Apply leadership multiplier to article's base score"""
    if 'leadership' not in article:
        return base_score

    leadership = article['leadership']

    if not leadership['has_leadership']:
        return base_score

    multiplier = leadership['weight_multiplier']
    return base_score * multiplier


# ========================================
# TESTING
# ========================================
if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("TESTING RSS MONITOR v3.2.0")
    print("=" * 60 + "\n")

    print("Fetching ALL RSS feeds...\n")
    articles = fetch_all_rss()
    print(f"\nTotal articles fetched: {len(articles)}")

    sources = {}
    for article in articles:
        source = article.get('source', {}).get('name', 'Unknown')
        sources[source] = sources.get(source, 0) + 1

    print("\nArticles by source:")
    for source, count in sorted(sources.items(), key=lambda x: x[1], reverse=True):
        print(f"  {source}: {count}")

    print("\nTesting flight disruption scan...\n")
    disruptions = _run_flight_disruption_scan()
    print(f"\nFound {len(disruptions)} disruptions")
    for d in disruptions[:5]:
        print(f"  • [{d['status']}] {d['airline']} → {d['destination']}: {d['headline'][:80]}...")
