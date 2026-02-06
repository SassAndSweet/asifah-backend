"""
RSS Monitor for Asifah Analytics
Comprehensive RSS feed monitoring for Middle East intelligence

Monitors:
1. Leadership Rhetoric (MEMRI, Al-Manar, Iran Wire)
2. Israeli News Sources (Ynet, Times of Israel, JPost, i24NEWS, Haaretz)
3. Regional Sources (expandable)

Leaders monitored:
- Naim Qassem (Hezbollah Secretary-General)
- Ali Khamenei (Iran Supreme Leader)
- Abdul-Malik al-Houthi (Houthi leader)
- Israeli leadership (Netanyahu, Gallant, Halevi)
"""

import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
import re


# ========================================
# RSS FEEDS - LEADERSHIP & NEWS
# ========================================
LEADERSHIP_RSS_FEEDS = {
    'memri': 'https://www.memri.org/rss/memri_tv.xml',
    'al_manar_en': 'https://english.almanar.com.lb/rss',
    'al_manar_ar': 'https://almanar.com.lb/rss',
    'iran_wire_en': 'https://iranwire.com/en/feed/',
    'iran_wire_fa': 'https://iranwire.com/fa/feed/',
}

ISRAELI_RSS_FEEDS = {
    'ynet': 'https://www.ynetnews.com/rss/rss.xml',
    'times_of_israel': 'https://www.timesofisrael.com/feed/',
    'jpost': 'https://www.jpost.com/rss/rssfeedsheadlines.aspx',
    'i24news': 'https://www.i24news.tv/en/rss',
    'haaretz': 'https://www.haaretz.com/cmlink/1.628810',
}

# Combine all feeds
ALL_RSS_FEEDS = {**LEADERSHIP_RSS_FEEDS, **ISRAELI_RSS_FEEDS}


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
        # Arabic
        'مقاومة', 'شعب', 'أمة', 'إخوان', 'شهداء',
        # English
        'resistance', 'our people', 'our nation', 'brothers', 'martyrs',
        'internal', 'domestic', 'lebanese people', 'iranian people',
        'friday prayers', 'sermon', 'israeli public', 'security cabinet'
    ],
    'international': [
        # Explicitly names adversaries
        'israel', 'israeli', 'إسرائيل', 'zionist', 'صهيوني',
        'america', 'american', 'أمريكا', 'united states', 'us forces',
        'washington', 'tel aviv', 'واشنطن', 'تل أبيب',
        'hezbollah', 'hamas', 'iran', 'tehran', 'lebanon', 'beirut',
        # Threat language
        'will strike', 'سنضرب', 'will attack', 'سنهاجم',
        'retaliate', 'revenge', 'response', 'انتقام', 'رد'
    ],
    'operational': [
        # Specific operational language
        'prepared to', 'ready to', 'מוכנים', 'مستعدون', 'جاهزون',
        'target', 'targets', 'أهداف', 'هدף', 'מטרות',
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
        'domestic': 1.3,        # Rallying domestic base
        'international': 1.8,   # Speaking to adversaries
        'operational': 2.2      # Announcing operations
    },
    'khamenei': {
        'domestic': 1.2,        # Friday prayers rhetoric
        'international': 2.0,   # UN statements, direct threats
        'operational': 2.5      # Religious decree / operational order
    },
    'abdul_malik_houthi': {
        'domestic': 1.1,        # Less global influence
        'international': 1.5,   # Red Sea / international threats
        'operational': 2.0      # Announcing strikes
    },
    'netanyahu': {
        'domestic': 1.2,        # Domestic political messaging
        'international': 1.7,   # International statements
        'operational': 2.3      # Operational orders / warnings
    },
    'gallant': {
        'domestic': 1.1,        # Military briefings
        'international': 1.6,   # International warnings
        'operational': 2.4      # Direct military orders
    },
    'halevi': {
        'domestic': 1.0,        # Internal briefings
        'international': 1.5,   # Rare public statements
        'operational': 2.5      # Operational announcements (IDF Chief)
    }
}


# ========================================
# RSS FETCHING FUNCTIONS
# ========================================
def fetch_all_rss(feed_dict=None):
    """
    Fetch all RSS feeds (leadership + Israeli + any additional)
    
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
            
            # Parse RSS
            try:
                root = ET.fromstring(response.content)
            except ET.ParseError as e:
                print(f"[RSS] {feed_name} XML parse error: {e}")
                continue
            
            # Extract items
            items = root.findall('.//item')
            
            for item in items[:15]:  # Top 15 per feed
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
                source_display = feed_name.upper().replace('_', ' ')
                if feed_name == 'ynet':
                    source_display = 'Ynet'
                elif feed_name == 'times_of_israel':
                    source_display = 'Times of Israel'
                elif feed_name == 'jpost':
                    source_display = 'Jerusalem Post'
                elif feed_name == 'i24news':
                    source_display = 'i24NEWS'
                elif feed_name == 'haaretz':
                    source_display = 'Haaretz'
                elif feed_name == 'memri':
                    source_display = 'MEMRI'
                elif feed_name == 'al_manar_en':
                    source_display = 'Al-Manar (EN)'
                elif feed_name == 'al_manar_ar':
                    source_display = 'Al-Manar (AR)'
                elif feed_name == 'iran_wire_en':
                    source_display = 'Iran Wire'
                elif feed_name == 'iran_wire_fa':
                    source_display = 'Iran Wire (FA)'
                
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
# LEADERSHIP DETECTION
# ========================================
def detect_leadership_quote(article):
    """
    Detect if article contains leadership quote
    
    Returns:
    {
        'has_leadership': bool,
        'leader': str (key from LEADERSHIP_NAMES),
        'leader_name': str (display name),
        'organization': str,
        'context': str ('domestic', 'international', 'operational'),
        'weight_multiplier': float,
        'threat_level': str ('explicit', 'conditional', 'capability', 'none'),
        'quote_snippet': str
    }
    """
    
    title = article.get('title', '').lower()
    description = article.get('description', '').lower()
    content = article.get('content', '').lower()
    full_text = f"{title} {description} {content}"
    
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
        # Check names
        for name in leader_data['names']:
            if name.lower() in full_text:
                result['has_leadership'] = True
                result['leader'] = leader_key
                result['leader_name'] = leader_data['names'][0]  # Use primary name
                result['organization'] = leader_data['organization']
                break
        
        # Also check titles
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
    
    # If no leadership detected, return early
    if not result['has_leadership']:
        return result
    
    # Classify context (domestic vs international vs operational)
    result['context'] = classify_context(full_text)
    
    # Detect threat level
    result['threat_level'] = detect_threat_level(full_text)
    
    # Calculate weight multiplier
    result['weight_multiplier'] = calculate_leadership_weight(
        result['leader'],
        result['context'],
        result['threat_level']
    )
    
    # Extract quote snippet (first 100 chars with leader name)
    for name in LEADERSHIP_NAMES[result['leader']]['names']:
        if name.lower() in full_text:
            idx = full_text.lower().find(name.lower())
            snippet_start = max(0, idx - 20)
            snippet_end = min(len(full_text), idx + 100)
            result['quote_snippet'] = full_text[snippet_start:snippet_end].strip()
            break
    
    return result


def classify_context(text):
    """
    Classify statement as domestic, international, or operational
    
    Hierarchy:
    1. Operational (highest priority - announces specific action)
    2. International (medium - addresses adversaries)
    3. Domestic (default - internal messaging)
    """
    
    text_lower = text.lower()
    
    # Check operational first (highest priority)
    operational_score = sum(1 for keyword in CONTEXT_INDICATORS['operational'] if keyword in text_lower)
    
    if operational_score >= 2:
        return 'operational'
    
    # Check international
    international_score = sum(1 for keyword in CONTEXT_INDICATORS['international'] if keyword in text_lower)
    
    if international_score >= 2:
        return 'international'
    
    # Check domestic
    domestic_score = sum(1 for keyword in CONTEXT_INDICATORS['domestic'] if keyword in text_lower)
    
    if domestic_score >= 2:
        return 'domestic'
    
    # Default to international if any adversary is named
    if any(word in text_lower for word in ['israel', 'america', 'united states', 'hezbollah', 'iran']):
        return 'international'
    
    # Default to domestic
    return 'domestic'


def detect_threat_level(text):
    """
    Detect threat level in statement
    
    Levels:
    - explicit: Direct threat to strike
    - conditional: Threat contingent on action
    - capability: Mentions weapons/capabilities
    - none: No threat detected
    """
    
    text_lower = text.lower()
    
    # Check explicit threats
    for keyword in THREAT_KEYWORDS['explicit_threat']:
        if keyword in text_lower:
            return 'explicit'
    
    # Check conditional threats
    for keyword in THREAT_KEYWORDS['conditional_threat']:
        if keyword in text_lower:
            return 'conditional'
    
    # Check capability signals
    for keyword in THREAT_KEYWORDS['capability_signal']:
        if keyword in text_lower:
            return 'capability'
    
    return 'none'


def calculate_leadership_weight(leader_key, context, threat_level):
    """
    Calculate final weight multiplier for leadership statement
    
    Formula:
    Base weight (by context) × Threat multiplier
    
    Threat multipliers:
    - explicit: 1.3x
    - conditional: 1.15x
    - capability: 1.1x
    - none: 1.0x
    """
    
    # Get base weight from context
    base_weight = LEADERSHIP_WEIGHTS.get(leader_key, {}).get(context, 1.0)
    
    # Apply threat multiplier
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
    """
    Wrapper function to add leadership data to article object
    
    Call this on each article before scoring:
    
    for article in all_articles:
        article['leadership'] = enhance_article_with_leadership(article)
    """
    
    leadership_data = detect_leadership_quote(article)
    
    if leadership_data['has_leadership']:
        print(f"[Leadership] ✅ Detected: {leadership_data['leader_name']} "
              f"({leadership_data['context']}, {leadership_data['threat_level']}) "
              f"weight: {leadership_data['weight_multiplier']}x")
    
    return leadership_data


def apply_leadership_multiplier(base_score, article):
    """
    Apply leadership multiplier to article's base score
    
    Usage in your scoring function:
    
    if 'leadership' in article and article['leadership']['has_leadership']:
        article_contribution = apply_leadership_multiplier(article_contribution, article)
    """
    
    if 'leadership' not in article:
        return base_score
    
    leadership = article['leadership']
    
    if not leadership['has_leadership']:
        return base_score
    
    multiplier = leadership['weight_multiplier']
    
    return base_score * multiplier


# ========================================
# TESTING FUNCTION
# ========================================
def test_rss_monitor():
    """Test function to verify RSS feeds and detection"""
    print("\n" + "="*60)
    print("TESTING RSS MONITOR")
    print("="*60 + "\n")
    
    # Test all RSS feeds
    print("Fetching ALL RSS feeds...\n")
    articles = fetch_all_rss()
    
    print(f"\nTotal articles fetched: {len(articles)}")
    
    # Count by source
    sources = {}
    for article in articles:
        source = article.get('source', {}).get('name', 'Unknown')
        sources[source] = sources.get(source, 0) + 1
    
    print("\nArticles by source:")
    for source, count in sorted(sources.items(), key=lambda x: x[1], reverse=True):
        print(f"  {source}: {count}")
    
    print("\nTesting leadership detection...\n")
    
    # Test on each article
    leadership_count = 0
    israeli_ops_count = 0
    
    for article in articles[:30]:  # Test first 30
        leadership = detect_leadership_quote(article)
        
        if leadership['has_leadership']:
            leadership_count += 1
            print(f"✅ DETECTED: {leadership['leader_name']}")
            print(f"   Context: {leadership['context']}")
            print(f"   Threat: {leadership['threat_level']}")
            print(f"   Weight: {leadership['weight_multiplier']}x")
            print(f"   Source: {article.get('source', {}).get('name', 'Unknown')}")
            print(f"   Title: {article['title'][:80]}...")
            print()
        
        # Count Israeli ops
        title_lower = article.get('title', '').lower()
        if any(word in title_lower for word in ['idf', 'strikes', 'operation', 'seized', 'attack']):
            israeli_ops_count += 1
    
    print(f"\nResults:")
    print(f"  Leadership quotes: {leadership_count}/{len(articles[:30])}")
    print(f"  Israeli operations: {israeli_ops_count}/{len(articles[:30])}")
    print("\n" + "="*60)


if __name__ == "__main__":
    # Run test when executed directly
    test_rss_monitor()
