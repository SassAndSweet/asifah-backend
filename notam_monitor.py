"""
Asifah Analytics — NOTAM Monitor v1.0.0
March 5, 2026

Real NOTAM data from Autorouter API (Eurocontrol EAD source)
Redis-cached with 2-hour TTL. Background refresh every 2 hours.

Replaces the broken FAA external API approach.
"""

import requests
import json
import re
import time
import threading
from datetime import datetime, timezone

# ========================================
# CONFIGURATION
# ========================================

AUTOROUTER_NOTAM_URL = "https://api.autorouter.aero/v1.0/notam"
NOTAM_CACHE_TTL = 2 * 60 * 60  # 2 hours
NOTAM_REDIS_KEY = 'mideast_notam_cache'

# Upstash Redis credentials (shared with military tracker)
import os
UPSTASH_REDIS_URL = os.environ.get('UPSTASH_REDIS_URL')
UPSTASH_REDIS_TOKEN = os.environ.get('UPSTASH_REDIS_TOKEN')

# ========================================
# MIDDLE EAST FIR / ICAO REGIONS
# ========================================

MIDEAST_NOTAM_REGIONS = {
    'israel': {
        'fir_codes': ['LLLL'],
        'icao_codes': ['LLBG', 'LLSD', 'LLOV'],
        'display_name': 'Israel',
        'flag': '🇮🇱'
    },
    'lebanon': {
        'fir_codes': ['OLBB'],
        'icao_codes': ['OLBA'],
        'display_name': 'Lebanon',
        'flag': '🇱🇧'
    },
    'syria': {
        'fir_codes': ['OSTT'],
        'icao_codes': ['OSDI', 'OSAP'],
        'display_name': 'Syria',
        'flag': '🇸🇾'
    },
    'iran': {
        'fir_codes': ['OIIX'],
        'icao_codes': ['OIIE', 'OIII', 'OISS'],
        'display_name': 'Iran',
        'flag': '🇮🇷'
    },
    'iraq': {
        'fir_codes': ['ORBB'],
        'icao_codes': ['ORBI', 'ORER'],
        'display_name': 'Iraq',
        'flag': '🇮🇶'
    },
    'jordan': {
        'fir_codes': ['OJAC'],
        'icao_codes': ['OJAI'],
        'display_name': 'Jordan',
        'flag': '🇯🇴'
    },
    'egypt': {
        'fir_codes': ['HECC'],
        'icao_codes': ['HECA', 'HEGN'],
        'display_name': 'Egypt',
        'flag': '🇪🇬'
    },
    'saudi_arabia': {
        'fir_codes': ['OEJD', 'OEDF'],
        'icao_codes': ['OEJN', 'OERK', 'OEDF'],
        'display_name': 'Saudi Arabia',
        'flag': '🇸🇦'
    },
    'uae': {
        'fir_codes': ['OMAE'],
        'icao_codes': ['OMDB', 'OMAD', 'OMSJ'],
        'display_name': 'UAE',
        'flag': '🇦🇪'
    },
    'qatar': {
        'fir_codes': ['OTDF'],
        'icao_codes': ['OTHH'],
        'display_name': 'Qatar',
        'flag': '🇶🇦'
    },
    'bahrain': {
        'fir_codes': ['OBBB'],
        'icao_codes': ['OBBI'],
        'display_name': 'Bahrain',
        'flag': '🇧🇭'
    },
    'oman': {
        'fir_codes': ['OOMM'],
        'icao_codes': ['OOMS', 'OOSA'],
        'display_name': 'Oman',
        'flag': '🇴🇲'
    },
    'kuwait': {
        'fir_codes': ['OKAC'],
        'icao_codes': ['OKBK'],
        'display_name': 'Kuwait',
        'flag': '🇰🇼'
    },
    'yemen': {
        'fir_codes': ['OYSC'],
        'icao_codes': ['OYAA', 'OYSN'],
        'display_name': 'Yemen',
        'flag': '🇾🇪'
    }
}

# Critical NOTAM patterns
NOTAM_CRITICAL_PATTERNS = [
    r'AIRSPACE\s+CLOSED',
    r'CLSD',
    r'PROHIBITED\s+AREA',
    r'RESTRICTED\s+AREA',
    r'DANGER\s+AREA',
    r'NO[-\s]?FLY\s+ZONE',
    r'MIL(?:ITARY)?\s+(?:EXERCISE|OPS|OPERATIONS|ACTIVITY)',
    r'LIVE\s+FIRING',
    r'MISSILE\s+(?:LAUNCH|TEST|FIRING)',
    r'UAV|UAS|DRONE|UNMANNED',
    r'GPS\s+(?:JAMMING|INTERFERENCE|SPOOFING)',
    r'NAVIGATION\s+(?:WARNING|UNRELIABLE)',
    r'CONFLICT\s+ZONE',
    r'HOSTILE\s+(?:ACTIVITY|ENVIRONMENT)',
    r'ANTI[-\s]?AIRCRAFT',
    r'SAM\s+(?:SITE|ACTIVITY)',
    r'NOTAM\s+(?:IMMEDIATE|URGENT)',
    r'TRIGGER\s+NOTAM'
]


# ========================================
# REDIS CACHE
# ========================================

def _load_notam_redis():
    if UPSTASH_REDIS_URL and UPSTASH_REDIS_TOKEN:
        try:
            resp = requests.get(
                f"{UPSTASH_REDIS_URL}/get/{NOTAM_REDIS_KEY}",
                headers={"Authorization": f"Bearer {UPSTASH_REDIS_TOKEN}"},
                timeout=5
            )
            data = resp.json()
            if data.get("result"):
                return json.loads(data["result"])
        except Exception as e:
            print(f"[ME NOTAM Cache] Redis load error: {e}")
    return None


def _save_notam_redis(data):
    data['cached_at'] = datetime.now(timezone.utc).isoformat()
    if UPSTASH_REDIS_URL and UPSTASH_REDIS_TOKEN:
        try:
            payload = json.dumps(data, default=str)
            resp = requests.post(
                f"{UPSTASH_REDIS_URL}/set/{NOTAM_REDIS_KEY}",
                headers={
                    "Authorization": f"Bearer {UPSTASH_REDIS_TOKEN}",
                    "Content-Type": "application/json"
                },
                json={"value": payload},
                timeout=10
            )
            if resp.status_code == 200:
                print("[ME NOTAM Cache] ✅ Saved to Redis")
        except Exception as e:
            print(f"[ME NOTAM Cache] Redis save error: {e}")


def _is_notam_fresh():
    cached = _load_notam_redis()
    if not cached or 'cached_at' not in cached:
        return False, None
    try:
        cached_at = datetime.fromisoformat(cached['cached_at'])
        age = (datetime.now(timezone.utc) - cached_at).total_seconds()
        if age < NOTAM_CACHE_TTL:
            return True, cached
        return False, cached
    except:
        return False, None


# ========================================
# NOTAM FETCHING
# ========================================

def classify_notam(text):
    if not text:
        return None
    text_upper = text.upper()

    for pattern in NOTAM_CRITICAL_PATTERNS:
        if re.search(pattern, text_upper):
            if any(kw in text_upper for kw in ['CONFLICT ZONE', 'WAR ZONE', 'HOSTILE', 'ANTI-AIRCRAFT', 'SAM ']):
                return {'type': 'Conflict Zone', 'color': 'red'}
            if any(kw in text_upper for kw in ['AIRSPACE CLOSED', 'NO-FLY', 'NO FLY', 'PROHIBITED', 'CLSD']):
                return {'type': 'Airspace Closure', 'color': 'red'}
            if any(kw in text_upper for kw in ['MISSILE LAUNCH', 'MISSILE TEST', 'LIVE FIRING']):
                return {'type': 'Missile/Live Firing', 'color': 'red'}
            if any(kw in text_upper for kw in ['MIL EXERCISE', 'MILITARY EXERCISE', 'MIL OPS', 'MILITARY OPS']):
                return {'type': 'Military Exercise', 'color': 'orange'}
            if any(kw in text_upper for kw in ['GPS JAMMING', 'GPS INTERFERENCE', 'GPS SPOOFING']):
                return {'type': 'GPS Interference', 'color': 'yellow'}
            if any(kw in text_upper for kw in ['DRONE', 'UAV', 'UAS', 'UNMANNED']):
                return {'type': 'Drone Activity', 'color': 'orange'}
            if any(kw in text_upper for kw in ['RESTRICTED', 'DANGER AREA']):
                return {'type': 'Restricted Area', 'color': 'yellow'}
            return {'type': 'Airspace Notice', 'color': 'blue'}

    if any(kw in text_upper for kw in ['AIRSPACE CLOSED', 'CLSD']):
        return {'type': 'Airspace Closure', 'color': 'red'}
    if 'MIL' in text_upper and any(kw in text_upper for kw in ['EXERCISE', 'OPS', 'ACTIVITY']):
        return {'type': 'Military Exercise', 'color': 'orange'}
    if 'DANGER AREA' in text_upper or 'RESTRICTED AREA' in text_upper:
        return {'type': 'Restricted Area', 'color': 'yellow'}

    return None


def fetch_notams_for_region(region_key):
    region = MIDEAST_NOTAM_REGIONS.get(region_key)
    if not region:
        return []

    notams = []
    all_codes = region.get('fir_codes', []) + region.get('icao_codes', [])
    if not all_codes:
        return []

    try:
        codes_json = json.dumps(all_codes[:6])
        params = {'itemas': codes_json, 'offset': 0, 'limit': 50}
        headers = {
            'User-Agent': 'AsifahAnalytics-ME/1.0.0 (OSINT monitoring)',
            'Accept': 'application/json'
        }

        print(f"[ME NOTAM] Fetching {region_key}: {all_codes[:6]}")
        response = requests.get(AUTOROUTER_NOTAM_URL, params=params, headers=headers, timeout=20)

        if response.status_code != 200:
            print(f"[ME NOTAM] {region_key}: HTTP {response.status_code}")
            return []

        data = response.json()
        raw_notams = data if isinstance(data, list) else data.get('notams', data.get('rows', []))

        for notam in raw_notams:
            notam_text = notam.get('all', '') or notam.get('text', '') or notam.get('message', '') or notam.get('e', '') or str(notam)
            item_e = notam.get('e', '') or notam.get('itemE', '') or ''
            full_text = f"{notam_text} {item_e}".upper()

            classification = classify_notam(full_text)
            if not classification:
                continue

            valid_from = notam.get('b', '') or notam.get('startValidity', '') or ''
            valid_to = notam.get('c', '') or notam.get('endValidity', '') or ''
            icao_loc = notam.get('a', '') or notam.get('itema', '') or notam.get('location', '')

            notams.append({
                'region': region_key,
                'country': region['display_name'],
                'flag': region['flag'],
                'type': classification['type'],
                'type_color': classification['color'],
                'summary': item_e[:250] if item_e else notam_text[:250],
                'icao_location': icao_loc,
                'valid_from': valid_from,
                'valid_to': valid_to,
                'source': 'Autorouter / Eurocontrol EAD'
            })

        print(f"[ME NOTAM] {region_key}: {len(notams)} critical NOTAMs (from {len(raw_notams)} total)")

    except requests.Timeout:
        print(f"[ME NOTAM] {region_key}: Timeout")
    except Exception as e:
        print(f"[ME NOTAM] {region_key}: Error: {str(e)[:150]}")

    return notams


def scan_all_mideast_notams():
    all_notams = []
    for region_key in MIDEAST_NOTAM_REGIONS:
        try:
            notams = fetch_notams_for_region(region_key)
            all_notams.extend(notams)
            time.sleep(1)
        except Exception as e:
            print(f"[ME NOTAM] Scan failed for {region_key}: {e}")

    severity_order = {'red': 0, 'orange': 1, 'yellow': 2, 'blue': 4, 'gray': 5}
    all_notams.sort(key=lambda x: severity_order.get(x.get('type_color', 'gray'), 5))
    print(f"[ME NOTAM] Total critical NOTAMs: {len(all_notams)}")
    return all_notams


# ========================================
# MAIN SCAN (with Redis caching)
# ========================================

def run_mideast_notam_scan():
    is_fresh, cached = _is_notam_fresh()
    if is_fresh and cached:
        cached['cached'] = True
        return cached

    print("[ME NOTAM] Running fresh scan from Autorouter API...")
    notams = scan_all_mideast_notams()

    result = {
        'success': True,
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'total_notams': len(notams),
        'notams': notams,
        'regions_scanned': list(MIDEAST_NOTAM_REGIONS.keys()),
        'data_source': 'Autorouter / Eurocontrol EAD',
        'version': '1.0.0',
        'cached': False
    }

    _save_notam_redis(result)
    return result


# ========================================
# FLASK ENDPOINT REGISTRATION
# ========================================

def register_notam_endpoints(app):
    from flask import jsonify, request as flask_request

    @app.route('/api/notams', methods=['GET'])
    def api_notams():
        try:
            force = flask_request.args.get('force', 'false').lower() == 'true'

            if not force:
                is_fresh, cached = _is_notam_fresh()
                if is_fresh and cached:
                    cached['cached'] = True
                    cached['cache_source'] = 'redis'
                    return jsonify(cached)

            data = run_mideast_notam_scan()
            return jsonify(data)

        except Exception as e:
            print(f"[ME NOTAM API] Error: {e}")
            return jsonify({'success': False, 'error': str(e), 'notams': [], 'count': 0}), 500

    print("[ME NOTAM] ✅ Endpoint registered: /api/notams")

    # Background refresh thread
    def _periodic_notam_scan():
        time.sleep(45)  # Wait for app to boot
        while True:
            try:
                print("[ME NOTAM] Periodic scan starting...")
                notams = scan_all_mideast_notams()
                result = {
                    'success': True,
                    'timestamp': datetime.now(timezone.utc).isoformat(),
                    'total_notams': len(notams),
                    'notams': notams,
                    'regions_scanned': list(MIDEAST_NOTAM_REGIONS.keys()),
                    'data_source': 'Autorouter / Eurocontrol EAD',
                    'version': '1.0.0',
                    'cached': False
                }
                _save_notam_redis(result)
                print(f"[ME NOTAM] Periodic scan complete. {len(notams)} NOTAMs. Sleeping 2h.")
                time.sleep(NOTAM_CACHE_TTL)
            except Exception as e:
                print(f"[ME NOTAM] Periodic scan error: {e}")
                time.sleep(3600)

    thread = threading.Thread(target=_periodic_notam_scan, daemon=True)
    thread.start()
    print("[ME NOTAM] ✅ Background scan thread started (2h cycle)")
