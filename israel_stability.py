"""
Israel Stability Backend v1.0.0
Standalone microservice for Israel Stability Index

Deployed on: asifah-backend (Render) — same service as app.py
Redis: Same Upstash instance as ME/Europe backends (key: israel_cache)

MODULES:
- Economic indicators: NIS/USD (Yahoo Finance + fallback), TASE TA-35 index
- Conflict scanning: Google News RSS (ToI, Haaretz, JPost, Ynet, JPost)
- Strike/incident tracker: ACLED API (with RSS fallback when key unavailable)
- Knesset/coalition politics scanner
- Leadership status badges: Netanyahu, Bennett, Gallant, Smotrich, Ben Gvir
- Stability score: Active-war calibrated (not chronic-collapse like Lebanon)

SCORING MODEL (active war baseline):
  base = 50
  + economic_health (NIS stability, TASE performance)     max +10
  - war_intensity (conflict scan score)                   max -25
  - coalition_fragility                                   max -15
  - regional_threat_level (Iran, Hezbollah, Houthi)       max -15
  + hostage_deal_bonus (if active deal/ceasefire)         max +8
  - humanitarian_pressure (ICJ/ICC/intl isolation)        max -5
"""

from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
import json
import os
import time
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

# ========================================
# CONFIGURATION
# ========================================

UPSTASH_URL   = os.environ.get('UPSTASH_REDIS_REST_URL')
UPSTASH_TOKEN = os.environ.get('UPSTASH_REDIS_REST_TOKEN')
ACLED_API_KEY = os.environ.get('ACLED_API_KEY')       # Optional — falls back to RSS
ACLED_EMAIL   = os.environ.get('ACLED_EMAIL')          # Required with ACLED key
TASE_API_KEY  = os.environ.get('TASE_API_KEY')         # TASE Data Hub — indices online
REDIS_CACHE_KEY = 'israel_cache'
CACHE_TTL_SECONDS = 4 * 60 * 60  # 4 hours

# Political figures — status is semi-static, updated via news scan heuristics
POLITICAL_FIGURES = {
    'netanyahu': {
        'name': 'Benjamin Netanyahu',
        'title': 'Prime Minister',
        'party': 'Likud',
        'flag': '🇮🇱',
        'icon': '🏛️',
        'status': 'ACTIVE',
        'status_color': '#4ade80',
        'note': 'On trial (bribery/fraud); coalition dependent on far-right'
    },
    'bennett': {
        'name': 'Naftali Bennett',
        'title': 'Former PM / Opposition',
        'party': 'New Right (ind.)',
        'flag': '🇮🇱',
        'icon': '🌟',
        'status': 'WATCHING',
        'status_color': '#facc15',
        'note': 'Dark horse PM candidate; vocal critic of war management'
    },
    'gallant': {
        'name': 'Yoav Gallant',
        'title': 'Former Defense Minister',
        'party': 'Likud (dismissed Nov 2024)',
        'flag': '🇮🇱',
        'icon': '⚔️',
        'status': 'DISMISSED',
        'status_color': '#fb923c',
        'note': 'Dismissed Nov 2024 over hostage/ceasefire disagreements'
    },
    'smotrich': {
        'name': 'Bezalel Smotrich',
        'title': 'Finance Minister',
        'party': 'Religious Zionism',
        'flag': '🇮🇱',
        'icon': '💰',
        'status': 'ACTIVE',
        'status_color': '#4ade80',
        'note': 'Controls settlement policy; key coalition veto player'
    },
    'ben_gvir': {
        'name': 'Itamar Ben Gvir',
        'title': 'National Security Minister',
        'party': 'Otzma Yehudit',
        'flag': '🇮🇱',
        'icon': '🔥',
        'status': 'ACTIVE',
        'status_color': '#f87171',
        'note': 'Far-right; repeatedly threatened to collapse govt over Gaza deal'
    }
}

# ========================================
# REDIS HELPERS (same pattern as Lebanon)
# ========================================

def _redis_available():
    return bool(UPSTASH_URL and UPSTASH_TOKEN)

def _redis_get(key):
    try:
        response = requests.get(
            f"{UPSTASH_URL}/get/{key}",
            headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"},
            timeout=5
        )
        data = response.json()
        if data.get('result'):
            return json.loads(data['result'])
        return None
    except Exception as e:
        print(f"[Redis] GET error: {str(e)[:100]}")
        return None

def _redis_set(key, value, ex=None):
    try:
        cmd = ["SET", key, json.dumps(value)]
        if ex:
            cmd += ["EX", ex]
        response = requests.post(
            f"{UPSTASH_URL}",
            headers={
                "Authorization": f"Bearer {UPSTASH_TOKEN}",
                "Content-Type": "application/json"
            },
            json=cmd,
            timeout=5
        )
        result = response.json()
        if result.get('result') == 'OK':
            print(f"[Redis] ✅ Saved key: {key}")
            return True
        return False
    except Exception as e:
        print(f"[Redis] SET error: {str(e)[:100]}")
        return False

def load_israel_cache():
    if _redis_available():
        data = _redis_get(REDIS_CACHE_KEY)
        if data:
            print(f"[Cache] ✅ Loaded israel_cache from Redis")
            return data
    return {
        'last_updated': None,
        'history': {},
        'metadata': {'storage': 'tmp_fallback'}
    }

def save_israel_cache(cache_data):
    cache_data['last_updated'] = datetime.now(timezone.utc).isoformat()
    if _redis_available():
        _redis_set(REDIS_CACHE_KEY, cache_data, ex=CACHE_TTL_SECONDS)
        return
    # /tmp fallback (ephemeral on Render — warns)
    print("[Cache] ⚠️ Redis not available — data will not persist across deploys")

def update_israel_history(snapshot: dict):
    """Append today's snapshot to rolling 90-day history."""
    try:
        cache = load_israel_cache()
        today = datetime.now(timezone.utc).date().isoformat()
        cache['history'][today] = snapshot
        # Keep 90 days max
        if len(cache['history']) > 90:
            for old in sorted(cache['history'].keys())[:-90]:
                del cache['history'][old]
        save_israel_cache(cache)
        print(f"[Cache] ✅ History updated for {today} ({len(cache['history'])} days)")
    except Exception as e:
        print(f"[Cache] History update error: {str(e)}")

# ========================================
# ECONOMIC INDICATORS
# ========================================

def fetch_nis_usd():
    """
    Fetch NIS/USD exchange rate.
    Primary: Yahoo Finance (ILS=X)
    Fallback: exchangerate-api open endpoint
    """
    print("[Israel Econ] Fetching NIS/USD...")
    try:
        url = "https://query1.finance.yahoo.com/v8/finance/chart/ILS=X?interval=1d&range=5d"
        r = requests.get(url, timeout=10, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        if r.status_code == 200:
            data = r.json()
            result = data.get('chart', {}).get('result', [{}])[0]
            meta = result.get('meta', {})
            price = meta.get('regularMarketPrice')
            prev  = meta.get('previousClose') or meta.get('chartPreviousClose')
            if price and price > 0:
                change_pct = ((price - prev) / prev * 100) if prev else 0
                trend = 'weakening' if change_pct > 0.1 else ('strengthening' if change_pct < -0.1 else 'stable')
                print(f"[Israel Econ] ✅ NIS/USD: {price:.4f} ({change_pct:+.2f}%)")
                return {
                    'usd_to_ils': round(price, 4),
                    'change_pct_24h': round(change_pct, 3),
                    'trend': trend,
                    'source': 'Yahoo Finance',
                    'timestamp': datetime.now(timezone.utc).isoformat()
                }
    except Exception as e:
        print(f"[Israel Econ] Yahoo Finance NIS error: {str(e)[:80]}")

    # Fallback
    try:
        r = requests.get("https://open.exchangerate-api.com/v6/latest/USD", timeout=10)
        if r.status_code == 200:
            rate = r.json().get('rates', {}).get('ILS')
            if rate:
                print(f"[Israel Econ] ✅ NIS/USD fallback: {rate:.4f}")
                return {
                    'usd_to_ils': round(rate, 4),
                    'change_pct_24h': 0,
                    'trend': 'stable',
                    'source': 'ExchangeRate-API',
                    'timestamp': datetime.now(timezone.utc).isoformat()
                }
    except Exception as e:
        print(f"[Israel Econ] Fallback NIS error: {str(e)[:80]}")

    print("[Israel Econ] Using NIS estimate")
    return {
        'usd_to_ils': 3.70,
        'change_pct_24h': 0,
        'trend': 'stable',
        'source': 'Estimated',
        'estimated': True,
        'timestamp': datetime.now(timezone.utc).isoformat()
    }


def fetch_tase_index():
    """
    Fetch Tel Aviv Stock Exchange TA-35 index.
    Primary:  TASE Data Hub API (datawise.tase.co.il) — last-rate + intraday
    Fallback: Yahoo Finance (^TA35 / ^TA125)
    """
    print("[Israel Econ] Fetching TASE TA-35...")

    # ── Primary: TASE official API ──
    if TASE_API_KEY:
        try:
            headers = {
                "accept": "application/json",
                "accept-language": "en-US",
                "apikey": TASE_API_KEY
            }
            # Last rate — current index value
            r = requests.get(
                "https://datawise.tase.co.il/v1/tase-indices-online-data/last-rate",
                params={"indexId": 22},
                headers=headers,
                timeout=10
            )
            if r.status_code == 200:
                data = r.json()
                # Response is a list; find indexId=22
                entries = data if isinstance(data, list) else data.get('result', [data])
                entry = next((e for e in entries if str(e.get('indexId', '')) == '22'), entries[0] if entries else {})
                value = entry.get('lastIndexRate') or entry.get('indexRate') or entry.get('rate')
                change = entry.get('change') or entry.get('changeRate') or 0
                if value:
                    value = float(str(value).replace(',', ''))
                    change_pct = float(str(change).replace(',', '')) if change else 0
                    print(f"[Israel Econ] ✅ TASE API TA-35: {value:,.2f} ({change_pct:+.2f}%)")

                    # ── Intraday sparkline ──
                    sparkline = []
                    try:
                        open_time = "09:40:00"
                        r2 = requests.get(
                            "https://datawise.tase.co.il/v1/tase-indices-online-data/intraday",
                            params={"indexId": 22, "startTime": open_time},
                            headers=headers,
                            timeout=10
                        )
                        if r2.status_code == 200:
                            intraday_data = r2.json()
                            entries2 = intraday_data if isinstance(intraday_data, list) else intraday_data.get('result', [])
                            sparkline = [
                                {
                                    'time': e.get('lastSaleTime', ''),
                                    'value': float(str(e.get('lastIndexRate', 0)).replace(',', ''))
                                }
                                for e in entries2
                                if e.get('lastIndexRate')
                            ]
                            print(f"[Israel Econ] ✅ Intraday: {len(sparkline)} datapoints")
                    except Exception as e2:
                        print(f"[Israel Econ] Intraday error: {str(e2)[:80]}")

                    return {
                        'index': 'TA35',
                        'value': round(value, 2),
                        'change_pct_24h': round(change_pct, 3),
                        'trend': 'rising' if change_pct > 0.3 else ('falling' if change_pct < -0.3 else 'flat'),
                        'source': 'TASE Data Hub',
                        'sparkline': sparkline,
                        'timestamp': datetime.now(timezone.utc).isoformat()
                    }
            print(f"[Israel Econ] TASE API returned {r.status_code} — falling back to Yahoo")
        except Exception as e:
            print(f"[Israel Econ] TASE API error: {str(e)[:80]}")

    # ── Fallback: Yahoo Finance ──
    print("[Israel Econ] Using Yahoo Finance fallback for TASE...")
    for ticker in ['^TA35', '^TA125']:
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=5d"
            r = requests.get(url, timeout=10, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            })
            if r.status_code == 200:
                data = r.json()
                result = data.get('chart', {}).get('result', [{}])[0]
                meta   = result.get('meta', {})
                price  = meta.get('regularMarketPrice')
                prev   = meta.get('previousClose') or meta.get('chartPreviousClose')
                if price and price > 0:
                    change_pct = ((price - prev) / prev * 100) if prev else 0
                    print(f"[Israel Econ] ✅ Yahoo {ticker}: {price:,.2f} ({change_pct:+.2f}%)")
                    return {
                        'index': ticker.replace('^', ''),
                        'value': round(price, 2),
                        'change_pct_24h': round(change_pct, 3),
                        'trend': 'rising' if change_pct > 0.3 else ('falling' if change_pct < -0.3 else 'flat'),
                        'source': 'Yahoo Finance',
                        'sparkline': [],
                        'timestamp': datetime.now(timezone.utc).isoformat()
                    }
        except Exception as e:
            print(f"[Israel Econ] Yahoo {ticker} error: {str(e)[:80]}")
            continue

    return {
        'index': 'TA35',
        'value': None,
        'change_pct_24h': 0,
        'trend': 'unknown',
        'source': 'Unavailable',
        'estimated': True,
        'sparkline': [],
        'timestamp': datetime.now(timezone.utc).isoformat()
    }

# ========================================
# CONFLICT & NEWS SCANNING
# ========================================

WAR_KEYWORDS = [
    # Active hostilities
    'IDF strike', 'Israeli airstrike', 'Gaza offensive', 'IDF operation',
    'Hamas attack', 'missile attack Israel', 'drone Israel', 'ballistic missile Israel',
    'Iron Dome intercept', 'Ben Gurion Airport closed', 'Hezbollah fire',
    'Houthi missile', 'Iran attack Israel',
    # Hostage situation
    'hostage deal', 'hostage release', 'Gaza ceasefire', 'ceasefire collapse',
    'hostage negotiations', 'Sinwar', 'Hamas ceasefire',
    # Coalition crisis signals
    'Ben Gvir resign', 'Ben Gvir threatens', 'Smotrich coalition',
    'Netanyahu coalition', 'no confidence Netanyahu', 'coalition collapse',
    'Netanyahu resign', 'Netanyahu indictment',
    # Bennett signals
    'Bennett prime minister', 'Bennett challenge Netanyahu', 'Bennett coalition'
]

SEVERITY_HIGH = [
    'war', 'explosion', 'killed', 'dead', 'casualties', 'attack', 'strike',
    'fired', 'launched', 'intercepted', 'ceasefire collapse', 'escalation',
    'ground operation', 'invasion', 'offensive', 'missile', 'ballistic'
]

RSS_SOURCES = [
    # Times of Israel
    ('https://www.timesofisrael.com/feed/', 'Times of Israel'),
    # Jerusalem Post
    ('https://www.jpost.com/rss/rssfeedsfrontpage.aspx', 'Jerusalem Post'),
    # Haaretz (English)
    ('https://www.haaretz.com/cmlink/1.628765', 'Haaretz'),
    # Google News — Israel war
    ('https://news.google.com/rss/search?q=Israel+IDF+Gaza+war&hl=en&gl=US&ceid=US:en', 'Google News - War'),
    # Google News — coalition
    ('https://news.google.com/rss/search?q=Netanyahu+coalition+Knesset&hl=en&gl=US&ceid=US:en', 'Google News - Coalition'),
    # Google News — Bennett
    ('https://news.google.com/rss/search?q=Naftali+Bennett+Israel+politics&hl=en&gl=US&ceid=US:en', 'Google News - Bennett'),
    # Google News — hostages
    ('https://news.google.com/rss/search?q=Israel+hostage+deal+Gaza+ceasefire&hl=en&gl=US&ceid=US:en', 'Google News - Hostages'),
    # Ynet (English via Google)
    ('https://news.google.com/rss/search?q=Ynet+Israel&hl=en&gl=US&ceid=US:en', 'Ynet'),
]


def _parse_rss_articles(url, source_name, days=7):
    """Fetch and parse RSS feed, returning articles within date window."""
    articles = []
    try:
        r = requests.get(url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
        if r.status_code != 200:
            return articles
        root = ET.fromstring(r.content)
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        for item in root.findall('.//item'):
            title_el = item.find('title')
            link_el  = item.find('link')
            pub_el   = item.find('pubDate')
            desc_el  = item.find('description')
            if title_el is None:
                continue
            pub_str = pub_el.text if pub_el is not None else ''
            # Date filter
            if pub_str:
                try:
                    pub_dt = parsedate_to_datetime(pub_str)
                    if pub_dt < cutoff:
                        continue
                except Exception:
                    pass
            articles.append({
                'title': title_el.text or '',
                'url': link_el.text if link_el is not None else '',
                'published': pub_str,
                'source': source_name,
                'description': (desc_el.text or '')[:200] if desc_el is not None else ''
            })
    except Exception as e:
        print(f"[RSS] {source_name} error: {str(e)[:80]}")
    return articles


def scan_israel_conflict(days=7):
    """
    Scan RSS feeds for conflict, coalition, and hostage indicators.
    Returns scored conflict data + article list.
    """
    print("[Israel Conflict] Starting scan...")
    all_articles = []

    for url, name in RSS_SOURCES:
        fetched = _parse_rss_articles(url, name, days=days)
        all_articles.extend(fetched)
        print(f"[Israel Conflict] {name}: {len(fetched)} articles")

    # Deduplicate by title
    seen = set()
    unique = []
    for a in all_articles:
        key = a['title'].strip().lower()[:80]
        if key not in seen:
            seen.add(key)
            unique.append(a)
    all_articles = unique

    # Score each article
    war_hits       = 0
    coalition_hits = 0
    hostage_hits   = 0
    bennett_hits   = 0
    high_severity  = 0

    war_articles       = []
    coalition_articles = []
    hostage_articles   = []
    bennett_articles   = []

    for a in all_articles:
        t = a['title'].lower()
        d = a.get('description', '').lower()
        combined = t + ' ' + d

        is_war       = any(kw.lower() in combined for kw in WAR_KEYWORDS[:14])
        is_coalition = any(kw.lower() in combined for kw in WAR_KEYWORDS[14:22])
        is_hostage   = any(kw.lower() in combined for kw in WAR_KEYWORDS[22:30])
        is_bennett   = any(kw.lower() in combined for kw in WAR_KEYWORDS[30:])
        is_severe    = any(kw in combined for kw in SEVERITY_HIGH)

        if is_war:
            war_hits += 1
            war_articles.append(a)
        if is_coalition:
            coalition_hits += 1
            coalition_articles.append(a)
        if is_hostage:
            hostage_hits += 1
            hostage_articles.append(a)
        if is_bennett:
            bennett_hits += 1
            bennett_articles.append(a)
        if is_severe:
            high_severity += 1

    # Conflict intensity score 0-100
    conflict_score = min(100,
        war_hits * 4 +
        high_severity * 3 +
        coalition_hits * 2 +
        hostage_hits * 1
    )

    # Coalition fragility score 0-100
    coalition_score = min(100, coalition_hits * 8 + bennett_hits * 5)

    # Hostage/ceasefire status heuristic
    ceasefire_active = hostage_hits > 0 and any(
        'ceasefire' in a['title'].lower() or 'deal' in a['title'].lower()
        for a in hostage_articles
    )

    print(f"[Israel Conflict] War:{war_hits} | Coalition:{coalition_hits} | Hostage:{hostage_hits} | Bennett:{bennett_hits}")
    print(f"[Israel Conflict] Conflict score: {conflict_score}/100 | Coalition fragility: {coalition_score}/100")

    return {
        'conflict_score': conflict_score,
        'coalition_score': coalition_score,
        'war_article_count': war_hits,
        'coalition_article_count': coalition_hits,
        'hostage_article_count': hostage_hits,
        'bennett_mentions': bennett_hits,
        'high_severity_count': high_severity,
        'ceasefire_active': ceasefire_active,
        'articles': {
            'war': war_articles[:10],
            'coalition': coalition_articles[:8],
            'hostage': hostage_articles[:8],
            'bennett': bennett_articles[:5]
        },
        'all_articles': all_articles[:40]
    }

# ========================================
# ACLED STRIKE TRACKER (with RSS fallback)
# ========================================

def fetch_acled_strikes():
    """
    Fetch recent strike/conflict events from ACLED API.
    Requires ACLED_API_KEY + ACLED_EMAIL env vars.
    Falls back to RSS strike mentions if key unavailable.
    """
    print("[ACLED] Fetching Israel strikes...")

    if ACLED_API_KEY and ACLED_EMAIL:
        try:
            # ACLED API v2 — events in Israel/Gaza/Lebanon last 30 days
            since = (datetime.now(timezone.utc) - timedelta(days=30)).strftime('%Y-%m-%d')
            params = {
                'key': ACLED_API_KEY,
                'email': ACLED_EMAIL,
                'country': 'Israel|Palestine|Lebanon',
                'event_date': since,
                'event_date_where': 'BETWEEN',
                'event_date_to': datetime.now(timezone.utc).strftime('%Y-%m-%d'),
                'event_type': 'Explosions/Remote violence|Battles|Strategic developments',
                'limit': 200,
                'fields': 'event_date|event_type|sub_event_type|actor1|actor2|country|location|latitude|longitude|fatalities|notes',
                'format': 'json'
            }
            r = requests.get('https://api.acleddata.com/acled/read', params=params, timeout=15)
            if r.status_code == 200:
                data = r.json()
                events = data.get('data', [])
                print(f"[ACLED] ✅ {len(events)} events retrieved")
                return {
                    'source': 'ACLED',
                    'event_count': len(events),
                    'events': events[:100],
                    'last_updated': datetime.now(timezone.utc).isoformat(),
                    'acled_available': True
                }
        except Exception as e:
            print(f"[ACLED] API error: {str(e)[:100]}")

    # === FALLBACK: RSS strike mentions ===
    print("[ACLED] No API key — using RSS strike fallback")
    strike_articles = []
    strike_queries = [
        'IDF airstrike Gaza today',
        'Israeli strike Lebanon Syria',
        'Houthi missile Israel',
        'Iran attack Israel strike'
    ]
    for q in strike_queries:
        url = f"https://news.google.com/rss/search?q={q.replace(' ', '+')}&hl=en&gl=US&ceid=US:en"
        strike_articles.extend(_parse_rss_articles(url, 'Google News - Strikes', days=7))

    # Deduplicate
    seen = set()
    unique_strikes = []
    for a in strike_articles:
        k = a['title'].strip().lower()[:80]
        if k not in seen:
            seen.add(k)
            unique_strikes.append(a)

    return {
        'source': 'RSS_fallback',
        'event_count': len(unique_strikes),
        'events': [],  # No lat/lng without ACLED
        'articles': unique_strikes[:15],
        'acled_available': False,
        'acled_note': 'Register at acleddata.com for free research API key. Set ACLED_API_KEY and ACLED_EMAIL env vars.',
        'last_updated': datetime.now(timezone.utc).isoformat()
    }

# ========================================
# KNESSET / COALITION POLITICS
# ========================================

# Static coalition data — update as needed
COALITION_DATA = {
    'pm': 'Benjamin Netanyahu',
    'president': 'Isaac Herzog',
    'defense_minister': 'Israel Katz (since Nov 2024)',
    'coalition_seats': 64,
    'knesset_total': 120,
    'majority_threshold': 61,
    'coalition_parties': [
        {'name': 'Likud', 'seats': 32, 'leader': 'Netanyahu'},
        {'name': 'Shas', 'seats': 11, 'leader': 'Deri'},
        {'name': 'United Torah Judaism', 'seats': 7, 'leader': 'Gafni'},
        {'name': 'Religious Zionism', 'seats': 7, 'leader': 'Smotrich'},
        {'name': 'Otzma Yehudit', 'seats': 6, 'leader': 'Ben Gvir'},
        {'name': 'Noam', 'seats': 1, 'leader': 'Maoz'}
    ],
    'opposition_leader': 'Yair Lapid (Yesh Atid)',
    'formed': '2022-12-29',
    'next_election': 'By November 2026 (can be called earlier)',
    'war_cabinet_active': False,  # Gantz resigned June 2024
    'icc_warrants': ['Netanyahu', 'Gallant (former)'],
    'criminal_trial': 'Netanyahu — bribery, fraud, breach of trust (ongoing)',
}


def scan_knesset_news():
    """Scan for Knesset/coalition/election news."""
    print("[Knesset] Scanning political news...")
    knesset_queries = [
        'Netanyahu Knesset coalition 2026',
        'Israel election early vote',
        'Ben Gvir Smotrich coalition threat',
        'Naftali Bennett Israel leadership'
    ]
    articles = []
    for q in knesset_queries:
        url = f"https://news.google.com/rss/search?q={q.replace(' ','+')}+Israel&hl=en&gl=US&ceid=US:en"
        articles.extend(_parse_rss_articles(url, 'Google News - Politics', days=14))

    # Deduplicate
    seen = set()
    unique = []
    for a in articles:
        k = a['title'].strip().lower()[:80]
        if k not in seen:
            seen.add(k)
            unique.append(a)

    # Check for election signals
    election_keywords = ['early election', 'snap election', 'coalition collapse', 'no confidence', 'new election']
    election_signals  = [a for a in unique if any(kw in a['title'].lower() for kw in election_keywords)]

    print(f"[Knesset] {len(unique)} political articles | {len(election_signals)} election signals")
    return {
        'articles': unique[:20],
        'election_signal_count': len(election_signals),
        'election_signals': election_signals[:5],
        'coalition_data': COALITION_DATA
    }

# ========================================
# LEADERSHIP STATUS (dynamic update from news)
# ========================================

def build_leadership_status(conflict_data, knesset_data):
    """
    Return leadership badges with dynamically updated notes
    based on recent news scan hits.
    """
    figures = {k: dict(v) for k, v in POLITICAL_FIGURES.items()}

    all_titles = [
        a['title'].lower()
        for a in (
            knesset_data.get('articles', []) +
            conflict_data.get('articles', {}).get('coalition', []) +
            conflict_data.get('articles', {}).get('bennett', [])
        )
    ]

    # Netanyahu — check for coalition threat signals
    if any('resign' in t or 'no confidence' in t or 'coalition collapse' in t for t in all_titles):
        figures['netanyahu']['status'] = 'UNDER PRESSURE'
        figures['netanyahu']['status_color'] = '#fb923c'

    # Ben Gvir — check for resignation threats
    if any('ben gvir' in t and ('resign' in t or 'threat' in t or 'quit' in t) for t in all_titles):
        figures['ben_gvir']['status'] = 'THREATENING EXIT'
        figures['ben_gvir']['status_color'] = '#f87171'

    # Bennett — check for active campaigning signals
    if conflict_data.get('bennett_mentions', 0) > 2:
        figures['bennett']['status'] = 'ACTIVE'
        figures['bennett']['status_color'] = '#6495ED'

    return figures

# ========================================
# STABILITY SCORE CALCULATION
# ========================================

def calculate_israel_stability(economic_data, tase_data, conflict_data, knesset_data, strike_data):
    """
    Israel Stability Score (0–100)

    Active-war calibrated model. Unlike Lebanon (chronic collapse),
    Israel has a functioning state under severe war stress.
    A score of 50-60 = "stressed but functional" during active war.
    """
    print("[Israel Stability] Calculating score (v1.0.0 active-war model)...")

    base = 50

    # ── Economic component (+10 max) ──
    econ_bonus = 0
    nis = economic_data.get('usd_to_ils', 3.7)
    nis_change = abs(economic_data.get('change_pct_24h', 0))
    tase_change = tase_data.get('change_pct_24h', 0) if tase_data else 0

    # NIS: pre-war ~3.45; war stress ~3.6-3.9; severe pressure >4.0
    if nis < 3.60:
        econ_bonus += 5   # Relatively stable
    elif nis < 3.80:
        econ_bonus += 2   # Mild war pressure
    elif nis < 4.00:
        econ_bonus += 0   # Elevated pressure
    else:
        econ_bonus -= 3   # Severe pressure

    # TASE performance
    if tase_change > 0.5:
        econ_bonus += 3
    elif tase_change > 0:
        econ_bonus += 1
    elif tase_change < -1.5:
        econ_bonus -= 3
    elif tase_change < -0.5:
        econ_bonus -= 1

    econ_bonus = max(-8, min(10, econ_bonus))
    print(f"[Israel Stability] Economic: {econ_bonus:+d} (NIS={nis:.3f}, TASE={tase_change:+.2f}%)")

    # ── War intensity (-25 max) ──
    conflict_score = conflict_data.get('conflict_score', 0)
    war_impact = int((conflict_score / 100) * 25)
    print(f"[Israel Stability] War impact: -{war_impact} (conflict_score={conflict_score})")

    # ── Coalition fragility (-15 max) ──
    coalition_score = conflict_data.get('coalition_score', 0)
    election_signals = knesset_data.get('election_signal_count', 0)
    coalition_impact = int((coalition_score / 100) * 12) + min(election_signals * 2, 3)
    coalition_impact = min(coalition_impact, 15)
    print(f"[Israel Stability] Coalition impact: -{coalition_impact} (coalition_score={coalition_score}, election_signals={election_signals})")

    # ── Regional threat (-15 max) ──
    # Static baseline — Iran/Hezbollah/Houthi ongoing
    # Will be made dynamic with ACLED when key is available
    event_count = strike_data.get('event_count', 0)
    if event_count > 50:
        regional_impact = 15
    elif event_count > 20:
        regional_impact = 10
    elif event_count > 5:
        regional_impact = 7
    else:
        # RSS fallback: use conflict scan as proxy
        regional_impact = 8  # Baseline: Iran/Hezbollah/Houthi threats always present
    print(f"[Israel Stability] Regional impact: -{regional_impact} (events={event_count})")

    # ── Hostage/ceasefire deal bonus (+8 max) ──
    hostage_bonus = 0
    if conflict_data.get('ceasefire_active', False):
        hostage_bonus = 8
    elif conflict_data.get('hostage_article_count', 0) > 3:
        hostage_bonus = 3  # Active negotiations signal
    print(f"[Israel Stability] Hostage bonus: +{hostage_bonus}")

    # ── Humanitarian/ICC pressure (-5 max) ──
    # ICJ/ICC proceedings, international isolation — static for now
    humanitarian_drag = -4
    print(f"[Israel Stability] Humanitarian drag: {humanitarian_drag}")

    # ── Final score ──
    score = (
        base
        + econ_bonus
        - war_impact
        - coalition_impact
        - regional_impact
        + hostage_bonus
        + humanitarian_drag
    )
    score = max(0, min(100, int(score)))

    if score >= 70:
        risk_level = 'Stressed but Functional'
        risk_color = 'yellow'
    elif score >= 50:
        risk_level = 'Moderate War Stress'
        risk_color = 'orange'
    elif score >= 30:
        risk_level = 'High War Stress'
        risk_color = 'red'
    else:
        risk_level = 'Crisis Level'
        risk_color = 'red'

    # Trend
    trend = 'stable'
    if conflict_data.get('conflict_score', 0) > 60 or coalition_impact > 8:
        trend = 'worsening'
    elif hostage_bonus >= 8 and econ_bonus > 0:
        trend = 'improving'

    print(f"[Israel Stability] ✅ Score: {score}/100 ({risk_level})")
    print(f"[Israel Stability] Components: base={base}, econ={econ_bonus:+}, war=-{war_impact}, coalition=-{coalition_impact}, regional=-{regional_impact}, hostage=+{hostage_bonus}, humanitarian={humanitarian_drag}")

    return {
        'score': score,
        'risk_level': risk_level,
        'risk_color': risk_color,
        'trend': trend,
        'components': {
            'base': base,
            'economic_bonus': econ_bonus,
            'war_impact': -war_impact,
            'coalition_impact': -coalition_impact,
            'regional_impact': -regional_impact,
            'hostage_bonus': hostage_bonus,
            'humanitarian_drag': humanitarian_drag
        },
        'version': '1.0.0-active-war'
    }

# ========================================
# TRENDS
# ========================================

def get_israel_trends(days=30):
    """Return sparkline-ready trend data from Redis history."""
    try:
        cache = load_israel_cache()
        history = cache.get('history', {})
        if not history:
            return {'success': False, 'message': 'Building trend data...', 'days_collected': 0}

        dates = sorted(history.keys(), reverse=True)[:days]
        dates.reverse()

        trends = {
            'dates': [],
            'stability': [],
            'nis_rate': [],
            'tase': [],
            'conflict_score': [],
            'coalition_score': []
        }
        for d in dates:
            snap = history[d]
            trends['dates'].append(d)
            trends['stability'].append(snap.get('stability_score', 0))
            trends['nis_rate'].append(snap.get('nis_usd', 0))
            trends['tase'].append(snap.get('tase_value', 0))
            trends['conflict_score'].append(snap.get('conflict_score', 0))
            trends['coalition_score'].append(snap.get('coalition_score', 0))

        return {
            'success': True,
            'days_collected': len(dates),
            'trends': trends,
            'storage': 'redis' if _redis_available() else 'tmp_file'
        }
    except Exception as e:
        return {'success': False, 'message': str(e), 'days_collected': 0}

# ========================================
# API ENDPOINTS
# ========================================

def scan_israel_stability():
    """Main Israel stability endpoint — runs all modules and returns full payload."""
    try:
        force_refresh = request.args.get('refresh', '').lower() == 'true'
        print(f"[Israel] Starting full scan (refresh={force_refresh})...")

        # Check cache unless forced refresh
        if not force_refresh and _redis_available():
            cached = _redis_get(REDIS_CACHE_KEY)
            if cached and cached.get('last_updated'):
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(cached['last_updated'])).total_seconds()
                if age < CACHE_TTL_SECONDS:
                    print(f"[Israel] ✅ Returning cached data ({int(age/60)}m old)")
                    cached['from_cache'] = True
                    return jsonify(cached)

        # Run all modules
        economic  = fetch_nis_usd()
        tase      = fetch_tase_index()
        conflict  = scan_israel_conflict(days=7)
        knesset   = scan_knesset_news()
        strikes   = fetch_acled_strikes()
        leadership = build_leadership_status(conflict, knesset)
        stability = calculate_israel_stability(economic, tase, conflict, knesset, strikes)

        # Build today's history snapshot
        snapshot = {
            'stability_score': stability['score'],
            'nis_usd': economic.get('usd_to_ils', 0),
            'tase_value': tase.get('value', 0),
            'conflict_score': conflict.get('conflict_score', 0),
            'coalition_score': conflict.get('coalition_score', 0),
            'hostage_articles': conflict.get('hostage_article_count', 0),
            'timestamp': datetime.now(timezone.utc).isoformat()
        }
        update_israel_history(snapshot)

        payload = {
            'success': True,
            'stability': stability,
            'economic': {
                'nis': economic,
                'tase': tase
            },
            'conflict': {
                'score': conflict.get('conflict_score', 0),
                'coalition_score': conflict.get('coalition_score', 0),
                'war_articles': conflict.get('articles', {}).get('war', [])[:8],
                'coalition_articles': conflict.get('articles', {}).get('coalition', [])[:5],
                'hostage_articles': conflict.get('articles', {}).get('hostage', [])[:5],
                'bennett_articles': conflict.get('articles', {}).get('bennett', [])[:4],
                'ceasefire_active': conflict.get('ceasefire_active', False),
                'high_severity_count': conflict.get('high_severity_count', 0),
                'bennett_mentions': conflict.get('bennett_mentions', 0)
            },
            'strikes': {
                'source': strikes.get('source'),
                'event_count': strikes.get('event_count', 0),
                'events': strikes.get('events', [])[:50],
                'articles': strikes.get('articles', [])[:10],
                'acled_available': strikes.get('acled_available', False),
                'acled_note': strikes.get('acled_note', '')
            },
            'knesset': {
                'coalition': knesset.get('coalition_data', {}),
                'election_signal_count': knesset.get('election_signal_count', 0),
                'election_signals': knesset.get('election_signals', []),
                'articles': knesset.get('articles', [])[:10]
            },
            'leadership': leadership,
            'all_articles': conflict.get('all_articles', [])[:30],
            'last_updated': datetime.now(timezone.utc).isoformat(),
            'version': '1.0.0-israel',
            'from_cache': False
        }

        # Cache the full payload
        if _redis_available():
            _redis_set(REDIS_CACHE_KEY, payload, ex=CACHE_TTL_SECONDS)

        return jsonify(payload)

    except Exception as e:
        print(f"[Israel] ❌ Error: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


def api_israel_trends():
    """Sparkline trend data endpoint."""
    try:
        days = min(int(request.args.get('days', 30)), 90)
        return jsonify(get_israel_trends(days))
    except Exception as e:
        return jsonify({'success': False, 'message': str(e), 'days_collected': 0}), 500


def api_israel_leadership():
    """Leadership status badges — lightweight, no full scan."""
    return jsonify({
        'success': True,
        'figures': POLITICAL_FIGURES,
        'coalition': COALITION_DATA,
        'last_updated': datetime.now(timezone.utc).isoformat()
    })


def api_israel_strikes():
    """Strike/incident data for heatmap."""
    try:
        data = fetch_acled_strikes()
        return jsonify({'success': True, **data})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


def robots():
    return "User-agent: *\nDisallow: /\n", 200, {'Content-Type': 'text/plain'}


def register_israel_stability_endpoints(flask_app):
    flask_app.add_url_rule('/scan-israel-stability', view_func=scan_israel_stability, methods=['GET'])
    flask_app.add_url_rule('/api/israel-trends', view_func=api_israel_trends, methods=['GET'])
    flask_app.add_url_rule('/api/israel-leadership', view_func=api_israel_leadership, methods=['GET'])
    flask_app.add_url_rule('/api/israel-strikes', view_func=api_israel_strikes, methods=['GET'])
    flask_app.add_url_rule('/robots.txt', view_func=robots)
    print("[Israel Stability] ✅ Routes registered")


def register_israel_stability_endpoints(flask_app):
    flask_app.add_url_rule('/scan-israel-stability', view_func=scan_israel_stability, methods=['GET'])
    flask_app.add_url_rule('/api/israel-trends', view_func=api_israel_trends, methods=['GET'])
    flask_app.add_url_rule('/api/israel-leadership', view_func=api_israel_leadership, methods=['GET'])
    flask_app.add_url_rule('/api/israel-strikes', view_func=api_israel_strikes, methods=['GET'])
    print("[Israel Stability] ✅ Routes registered")
