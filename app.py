"""
Asifah Analytics - Flask Backend v1.5 DIAGNOSTIC
Enhanced GDELT debugging to see what's failing
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import os
from datetime import datetime, timedelta, timezone
import hashlib
import traceback

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
        "keywords_ar": ["حزب الله", "إسرائيل لبنان", "جنوب لبنان", "نصرالله"],
        "keywords_he": ["חיזבאללה", "לבנון", "נסראללה"],
        "keywords_fa": [],
        "domains_ar": ["aawsat.com", "alhurra.com", "alarabiya.net", "aljazeera.net"],
        "escalation": [
            "strike", "attack", "military action", "retaliate", "offensive",
            "troops", "border", "rocket", "missile",
        ],
    },
    "iran": {
        "keywords_en": ["Iran Israel", "Iranian", "Tehran", "nuclear", "IRGC"],
        "keywords_ar": ["إيران", "طهران", "نووي", "الحرس الثوري"],
        "keywords_he": ["איראן", "טהרן", "גרעיני"],
        "keywords_fa": ["ایران", "تهران", "هسته‌ای", "سپاه"],
        "domains_ar": ["aawsat.com", "alhurra.com", "alarabiya.net"],
        "escalation": [
            "strike", "attack", "military action", "retaliate", "sanctions",
            "nuclear facility", "enrichment", "weapons",
        ],
    },
    "houthis": {
        "keywords_en": ["Houthis", "Yemen", "Red Sea"],
        "keywords_ar": ["الحوثي", "اليمن", "البحر الأحمر"],
        "keywords_he": ["חות'ים", "תימן"],
        "keywords_fa": [],
        "domains_ar": ["aawsat.com", "alhurra.com", "alarabiya.net"],
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
            return data.get("articles", []), None
        return [], f"NewsAPI returned status: {data.get('status')}"
    except Exception as e:
        return [], f"NewsAPI error: {str(e)}"


def fetch_gdelt_articles(keywords, language, days, domains=None):
    """
    Fetch articles from GDELT in specified language
    DIAGNOSTIC VERSION - returns (articles, error_info)
    """
    diagnostic_info = {
        "attempted": True,
        "url": None,
        "params": None,
        "response_status": None,
        "response_text": None,
        "error": None,
        "articles_found": 0
    }
    
    # Try multiple GDELT query strategies
    strategies = [
        # Strategy 1: Simple keyword search with language filter
        {
            "name": "keyword_with_lang",
            "url": "https://api.gdeltproject.org/api/v2/doc/doc",
            "params": {
                "query": " OR ".join(keywords),
                "mode": "artlist",
                "maxrecords": 20,
                "timespan": f"{days}d",
                "format": "json",
                "sort": "datedesc"
            }
        },
        # Strategy 2: Broader search with just one keyword
        {
            "name": "single_keyword",
            "url": "https://api.gdeltproject.org/api/v2/doc/doc",
            "params": {
                "query": keywords[0] if keywords else "",
                "mode": "artlist",
                "maxrecords": 20,
                "timespan": f"{days}d",
                "format": "json",
                "sort": "datedesc"
            }
        }
    ]
    
    # Try domain filtering if available
    if domains and len(domains) > 0:
        strategies.insert(0, {
            "name": "domain_specific",
            "url": "https://api.gdeltproject.org/api/v2/doc/doc",
            "params": {
                "query": " OR ".join(keywords),
                "mode": "artlist",
                "maxrecords": 20,
                "timespan": f"{days}d",
                "format": "json",
                "sort": "datedesc",
                "sourcecountry": "LB" if "aawsat" in str(domains) else None  # Try Lebanon country filter
            }
        })
    
    # Add source language if specified
    lang_codes = {"ar": "ara", "he": "heb", "fa": "per"}
    if language in lang_codes:
        for strategy in strategies:
            strategy["params"]["sourcelang"] = lang_codes[language]
    
    # Try each strategy
    for i, strategy in enumerate(strategies):
        try:
            diagnostic_info["strategy"] = strategy["name"]
            diagnostic_info["url"] = strategy["url"]
            
            # Clean None values from params
            params = {k: v for k, v in strategy["params"].items() if v is not None}
            diagnostic_info["params"] = params
            
            response = requests.get(strategy["url"], params=params, timeout=20)
            diagnostic_info["response_status"] = response.status_code
            
            response.raise_for_status()
            
            # Try to parse JSON
            try:
                data = response.json()
                diagnostic_info["response_structure"] = list(data.keys()) if isinstance(data, dict) else "not_dict"
            except:
                diagnostic_info["response_text"] = response.text[:500]  # First 500 chars
                continue
            
            # GDELT can return articles in different keys
            articles = []
            if isinstance(data, dict):
                articles = data.get("articles", data.get("timeline", []))
            elif isinstance(data, list):
                articles = data
            
            diagnostic_info["articles_found"] = len(articles)
            
            if len(articles) > 0:
                # Normalize GDELT format
                normalized = []
                for article in articles[:20]:  # Limit to 20
                    if isinstance(article, dict):
                        normalized.append({
                            "title": article.get("title", article.get("url", ""))[:200],
                            "description": article.get("seendate", ""),
                            "url": article.get("url", article.get("title", "")),
                            "publishedAt": article.get("seendate", ""),
                            "source": {"name": article.get("domain", article.get("source", "GDELT"))},
                            "content": "",
                            "language": language
                        })
                
                diagnostic_info["success"] = True
                return normalized, diagnostic_info
                
        except requests.Timeout:
            diagnostic_info["error"] = f"Strategy {i+1} timeout after 20s"
            continue
        except Exception as e:
            diagnostic_info["error"] = f"Strategy {i+1} error: {str(e)}"
            diagnostic_info["traceback"] = traceback.format_exc()[:500]
            continue
    
    # All strategies failed
    diagnostic_info["success"] = False
    return [], diagnostic_info


@app.route("/")
def home():
    """Health check endpoint"""
    rate_info = get_rate_limit_info()
    return jsonify({
        "status": "online",
        "service": "Asifah Analytics Backend - DIAGNOSTIC v1.5",
        "version": "1.5-diagnostic",
        "features": ["caching", "rate_limiting", "quadrilingual_gdelt", "enhanced_diagnostics"],
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
    DIAGNOSTIC VERSION - includes detailed error information

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

    # Initialize diagnostic tracking
    diagnostics = {
        "newsapi": {},
        "gdelt_ar": {},
        "gdelt_he": {},
        "gdelt_fa": {}
    }

    try:
        # Fetch from NewsAPI (English)
        articles_en, newsapi_error = fetch_newsapi_english(target_config, from_date_str, to_date_str, page_size)
        diagnostics["newsapi"] = {
            "success": len(articles_en) > 0,
            "count": len(articles_en),
            "error": newsapi_error
        }
        
        if len(articles_en) > 0:
            increment_rate_limit()
        
        # Fetch from GDELT (Arabic)
        articles_ar, gdelt_ar_diag = fetch_gdelt_articles(
            target_config["keywords_ar"], 
            "ar", 
            days,
            target_config.get("domains_ar")
        )
        diagnostics["gdelt_ar"] = gdelt_ar_diag
        
        # Fetch from GDELT (Hebrew)
        articles_he, gdelt_he_diag = fetch_gdelt_articles(
            target_config["keywords_he"], 
            "he", 
            days
        )
        diagnostics["gdelt_he"] = gdelt_he_diag
        
        # Fetch from GDELT (Farsi)
        articles_fa = []
        if target_config.get("keywords_fa") and len(target_config["keywords_fa"]) > 0:
            articles_fa, gdelt_fa_diag = fetch_gdelt_articles(
                target_config["keywords_fa"], 
                "fa", 
                days
            )
            diagnostics["gdelt_fa"] = gdelt_fa_diag
        else:
            diagnostics["gdelt_fa"] = {"skipped": True, "reason": "No Farsi keywords configured"}
        
        # Combine all articles
        all_articles = articles_en + articles_ar + articles_he + articles_fa
        
        # Prepare response with diagnostics
        response_data = {
            "target": target,
            "days": days,
            "from": from_date_str,
            "to": to_date_str,
            "articles": all_articles,
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
            "rate_limit": get_rate_limit_info(),
            "diagnostics": diagnostics  # DIAGNOSTIC INFO
        }

        # Save to cache
        save_to_cache(cache_key, response_data)

        return jsonify(response_data)

    except Exception as e:
        return jsonify({
            "error": "Server error",
            "message": str(e),
            "traceback": traceback.format_exc(),
            "rate_limit": get_rate_limit_info(),
            "diagnostics": diagnostics
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
