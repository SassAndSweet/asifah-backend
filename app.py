"""
Asifah Analytics - Flask Backend v1.4
Enhanced with GDELT quadrilingual support (English, Hebrew, Arabic, Farsi)
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import os
from datetime import datetime, timedelta, timezone
import hashlib

app = Flask(__name__)
CORS(app)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
NEWS_API_KEY = os.environ.get("NEWS_API_KEY") or "32de6811aacf4fc2ab651901a08b5235"

# Cache storage
CACHE = {}
CACHE_DURATION_MINUTES = 30

# Rate limit tracking
RATE_LIMIT = {"date": None, "count": 0}
DAILY_LIMIT = 100  # NewsAPI free tier

# Target configurations with multilingual keywords
TARGETS = {
    "hezbollah": {
        "keywords_en": ["Hezbollah", "Lebanon Israel", "Southern Lebanon", "Nasrallah"],
        "keywords_ar": ["حزب الله", "لبنان إسرائيل", "جنوب لبنان", "نصر الله", "حسن نصرالله"],
        "keywords_he": ["חיזבאללה", "לבנון ישראל", "דרום לבנון", "נסראללה"],
        "keywords_fa": [],  # No Farsi sources for Hezbollah
        "escalation": [
            "strike", "attack", "military action", "retaliate", "offensive",
            "troops", "border", "rocket", "missile",
        ],
    },
    "iran": {
        "keywords_en": ["Iran Israel", "Iranian", "Tehran", "nuclear", "IRGC"],
        "keywords_ar": ["إيران إسرائيل", "إيراني", "طهران", "نووي", "الحرس الثوري"],
        "keywords_he": ["איראן ישראל", "איראני", "טהרן", "גרעיני", "משמרות המהפכה"],
        "keywords_fa": ["ایران اسرائیل", "ایرانی", "تهران", "هسته‌ای", "سپاه پاسداران"],
        "escalation": [
            "strike", "attack", "military action", "retaliate", "sanctions",
            "nuclear facility", "enrichment", "weapons",
        ],
    },
    "houthis": {
        "keywords_en": ["Houthis", "Yemen", "Ansar Allah", "Red Sea"],
        "keywords_ar": ["الحوثيون", "اليمن", "أنصار الله", "البحر الأحمر"],
        "keywords_he": ["חות'ים", "תימן", "אנסאר אללה", "ים סוף"],
        "keywords_fa": [],  # No Farsi sources for Houthis
        "escalation": [
            "strike", "attack", "military action", "shipping",
            "missile", "drone", "blockade",
        ],
    },
}


def get_cache_key(target, days):
    """Generate unique cache key for a scan request"""
    now_utc = datetime.now(timezone.utc)
    hour_key = now_utc.strftime("%Y-%m-%d-%H")
    return hashlib.md5(f"{target}:{days}:{hour_key}".encode()).hexdigest()


def get_from_cache(cache_key):
    """Retrieve from cache if valid"""
    if cache_key in CACHE:
        cached = CACHE[cache_key]
        if datetime.now(timezone.utc) < cached["expires"]:
            return cached["data"]
        else:
            del CACHE[cache_key]
    return None


def save_to_cache(cache_key, data):
    """Save data to cache with expiration"""
    now_utc = datetime.now(timezone.utc)
    CACHE[cache_key] = {
        "data": data,
        "timestamp": now_utc,
        "expires": now_utc + timedelta(minutes=CACHE_DURATION_MINUTES)
    }


def increment_rate_limit():
    """Track API requests and reset daily"""
    now_utc = datetime.now(timezone.utc)
    today = now_utc.strftime("%Y-%m-%d")
    
    if RATE_LIMIT["date"] != today:
        RATE_LIMIT["date"] = today
        RATE_LIMIT["count"] = 0
    
    RATE_LIMIT["count"] += 1
    return RATE_LIMIT["count"]


def get_rate_limit_info():
    """Get current rate limit status"""
    now_utc = datetime.now(timezone.utc)
    today = now_utc.strftime("%Y-%m-%d")
    
    if RATE_LIMIT["date"] != today:
        RATE_LIMIT["date"] = today
        RATE_LIMIT["count"] = 0
    
    tomorrow = now_utc.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    seconds_until_reset = int((tomorrow - now_utc).total_seconds())
    
    return {
        "requests_used": RATE_LIMIT["count"],
        "requests_limit": DAILY_LIMIT,
        "requests_remaining": max(0, DAILY_LIMIT - RATE_LIMIT["count"]),
        "resets_in_seconds": seconds_until_reset,
        "reset_time_utc": tomorrow.isoformat(timespec="seconds")
    }


def fetch_newsapi_english(target_config, from_date_str, to_date_str, page_size):
    """Fetch English news from NewsAPI"""
    query = " OR ".join(target_config["keywords_en"])
    
    url = "https://newsapi.org/v2/everything"
    params = {
        "q": query,
        "from": from_date_str,
        "to": to_date_str,
        "sortBy": "publishedAt",
        "language": "en",
        "pageSize": page_size,
        "apiKey": NEWS_API_KEY,
    }
    
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        if data.get("status") == "ok":
            return data.get("articles", [])
        return []
    except Exception as e:
        print(f"NewsAPI error: {e}")
        return []


def fetch_gdelt_articles(keywords, language, days):
    """Fetch articles from GDELT in specified language"""
    # GDELT DOC API endpoint
    url = "https://api.gdeltproject.org/api/v2/doc/doc"
    
    # Build query
    query = " OR ".join(keywords)
    
    # Calculate timespan (last N days)
    # GDELT uses format like "3d" for 3 days
    timespan = f"{days}d"
    
    params = {
        "query": query,
        "mode": "artlist",
        "maxrecords": 20,
        "timespan": timespan,
        "format": "json",
        "sort": "datedesc"
    }
    
    # Add source language filter if specified
    if language == "ar":
        params["sourcelang"] = "ara"  # Arabic
    elif language == "he":
        params["sourcelang"] = "heb"  # Hebrew
    elif language == "fa":
        params["sourcelang"] = "per"  # Persian/Farsi
    
    try:
        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        # GDELT returns articles in 'articles' key
        articles = data.get("articles", [])
        
        # Normalize GDELT format to match NewsAPI format
        normalized = []
        for article in articles:
            normalized.append({
                "title": article.get("title", ""),
                "description": article.get("seendate", ""),  # GDELT doesn't have description
                "url": article.get("url", ""),
                "publishedAt": article.get("seendate", ""),
                "source": {
                    "name": article.get("domain", "Unknown")
                },
                "content": "",
                "language": language
            })
        
        return normalized
    except Exception as e:
        print(f"GDELT error for {language}: {e}")
        return []


@app.route("/")
def home():
    """Health check endpoint"""
    rate_info = get_rate_limit_info()
    return jsonify({
        "status": "online",
        "service": "Asifah Analytics Backend",
        "version": "1.4",
        "features": ["caching", "rate_limiting", "quadrilingual_gdelt"],
        "has_api_key": bool(NEWS_API_KEY),
        "rate_limit": rate_info,
        "cache_info": {
            "duration_minutes": CACHE_DURATION_MINUTES,
            "cached_items": len(CACHE)
        },
        "endpoints": {
            "/": "Health check with rate limit info",
            "/scan": "Scan news sources (GET with ?target=hezbollah&days=7)",
            "/health": "Basic health check",
            "/rate-limit": "Current rate limit status"
        },
    })


@app.route("/rate-limit", methods=["GET"])
def rate_limit_status():
    """Endpoint to check current rate limit"""
    return jsonify(get_rate_limit_info())


@app.route("/scan", methods=["GET"])
def scan():
    """
    Scan news sources for a specific target with trilingual support

    Query parameters:
    - target: hezbollah, iran, or houthis
    - days: number of days to look back (1–30)
    """

    if not NEWS_API_KEY:
        return jsonify({
            "error": "Configuration error",
            "message": "NEWS_API_KEY is not set on the server.",
        }), 500

    # Get parameters
    target = (request.args.get("target") or "").lower()
    days_param = request.args.get("days", "7")

    try:
        days = int(days_param)
    except ValueError:
        days = 7

    days = max(1, min(days, 30))

    # Validate target
    if target not in TARGETS:
        return jsonify({
            "error": "Invalid target",
            "valid_targets": list(TARGETS.keys()),
        }), 400

    # Check cache first
    cache_key = get_cache_key(target, days)
    cached_data = get_from_cache(cache_key)
    
    if cached_data:
        cached_data["cached"] = True
        cached_data["rate_limit"] = get_rate_limit_info()
        return jsonify(cached_data)

    # Check rate limit before making API call
    rate_info = get_rate_limit_info()
    if rate_info["requests_remaining"] <= 0:
        return jsonify({
            "error": "Rate limit exceeded",
            "message": f"Daily limit of {DAILY_LIMIT} requests reached. Resets at midnight UTC.",
            "rate_limit": rate_info
        }), 429

    # Build time range
    now = datetime.now(timezone.utc)
    from_date = now - timedelta(days=days)
    from_date_str = from_date.isoformat(timespec="seconds")
    to_date_str = now.isoformat(timespec="seconds")

    target_config = TARGETS[target]
    page_size = min(days * 10, 100)

    try:
        # Fetch from NewsAPI (English) - counts against rate limit
        articles_en = fetch_newsapi_english(target_config, from_date_str, to_date_str, page_size)
        requests_used = increment_rate_limit()
        
        # Fetch from GDELT (Arabic) - FREE, no rate limit
        articles_ar = fetch_gdelt_articles(target_config["keywords_ar"], "ar", days)
        
        # Fetch from GDELT (Hebrew) - FREE, no rate limit
        articles_he = fetch_gdelt_articles(target_config["keywords_he"], "he", days)
        
        # Fetch from GDELT (Farsi) - FREE, no rate limit (only if keywords exist)
        articles_fa = []
        if target_config.get("keywords_fa") and len(target_config["keywords_fa"]) > 0:
            articles_fa = fetch_gdelt_articles(target_config["keywords_fa"], "fa", days)
        
        # Combine all articles
        all_articles = articles_en + articles_ar + articles_he + articles_fa
        
        # Prepare response with language-separated articles
        response_data = {
            "target": target,
            "days": days,
            "from": from_date_str,
            "to": to_date_str,
            "articles": all_articles,  # Combined for backward compatibility
            "articles_en": articles_en,
            "articles_ar": articles_ar,
            "articles_he": articles_he,
            "articles_fa": articles_fa,
            "totalResults": len(all_articles),
            "totalResults_en": len(articles_en),
            "totalResults_ar": len(articles_ar),
            "totalResults_he": len(articles_he),
            "totalResults_fa": len(articles_fa),
            "escalation_keywords": target_config["escalation"],
            "target_keywords": target_config["keywords_en"],
            "cached": False,
            "rate_limit": get_rate_limit_info()
        }

        # Save to cache
        save_to_cache(cache_key, response_data)

        return jsonify(response_data)

    except requests.exceptions.RequestException as e:
        return jsonify({
            "error": "Request failed",
            "message": str(e),
            "rate_limit": get_rate_limit_info()
        }), 500
    except Exception as e:
        return jsonify({
            "error": "Server error",
            "message": str(e),
            "rate_limit": get_rate_limit_info()
        }), 500


@app.route("/health", methods=["GET"])
def health():
    """Health check for monitoring"""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "has_api_key": bool(NEWS_API_KEY),
        "rate_limit": get_rate_limit_info(),
        "cache_size": len(CACHE)
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
