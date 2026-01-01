"""
Asifah Analytics - Flask Backend v1.2
Adds 30-minute caching and rate limit tracking
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

# Cache storage (in-memory, resets when server restarts)
# Structure: {cache_key: {"data": {...}, "timestamp": datetime, "expires": datetime}}
CACHE = {}
CACHE_DURATION_MINUTES = 30

# Rate limit tracking
# Structure: {"date": "2026-01-01", "count": 27}
RATE_LIMIT = {"date": None, "count": 0}
DAILY_LIMIT = 100  # NewsAPI free tier

# Target configurations
TARGETS = {
    "hezbollah": {
        "keywords": ["Hezbollah", "Lebanon Israel", "Southern Lebanon", "Nasrallah"],
        "escalation": [
            "strike", "attack", "military action", "retaliate", "offensive",
            "troops", "border", "rocket", "missile",
        ],
    },
    "iran": {
        "keywords": ["Iran Israel", "Iranian", "Tehran", "nuclear", "IRGC"],
        "escalation": [
            "strike", "attack", "military action", "retaliate", "sanctions",
            "nuclear facility", "enrichment", "weapons",
        ],
    },
    "houthis": {
        "keywords": ["Houthis", "Yemen", "Ansar Allah", "Red Sea"],
        "escalation": [
            "strike", "attack", "military action", "shipping",
            "missile", "drone", "blockade",
        ],
    },
}


def get_cache_key(target, days):
    """Generate unique cache key for a scan request"""
    # Use target + days + current hour to create cache key
    # This means cache is shared across all users for the same target/timeframe
    now_utc = datetime.now(timezone.utc)
    hour_key = now_utc.strftime("%Y-%m-%d-%H")  # Changes every hour
    return hashlib.md5(f"{target}:{days}:{hour_key}".encode()).hexdigest()


def get_from_cache(cache_key):
    """Retrieve from cache if valid"""
    if cache_key in CACHE:
        cached = CACHE[cache_key]
        if datetime.now(timezone.utc) < cached["expires"]:
            return cached["data"]
        else:
            # Expired, remove it
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
    
    # Reset counter if it's a new day
    if RATE_LIMIT["date"] != today:
        RATE_LIMIT["date"] = today
        RATE_LIMIT["count"] = 0
    
    # Increment
    RATE_LIMIT["count"] += 1
    return RATE_LIMIT["count"]


def get_rate_limit_info():
    """Get current rate limit status"""
    now_utc = datetime.now(timezone.utc)
    today = now_utc.strftime("%Y-%m-%d")
    
    # Reset if new day
    if RATE_LIMIT["date"] != today:
        RATE_LIMIT["date"] = today
        RATE_LIMIT["count"] = 0
    
    # Calculate time until reset (midnight UTC)
    tomorrow = now_utc.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    seconds_until_reset = int((tomorrow - now_utc).total_seconds())
    
    return {
        "requests_used": RATE_LIMIT["count"],
        "requests_limit": DAILY_LIMIT,
        "requests_remaining": max(0, DAILY_LIMIT - RATE_LIMIT["count"]),
        "resets_in_seconds": seconds_until_reset,
        "reset_time_utc": tomorrow.isoformat(timespec="seconds")
    }


@app.route("/")
def home():
    """Health check endpoint"""
    rate_info = get_rate_limit_info()
    return jsonify({
        "status": "online",
        "service": "Asifah Analytics Backend",
        "version": "1.2",
        "features": ["caching", "rate_limiting"],
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
    Scan news sources for a specific target with caching

    Query parameters:
    - target: hezbollah, iran, or houthis
    - days: number of days to look back (1–30)
    """

    # Check API key
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
        # Return cached data with metadata
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

    # Build NewsAPI request
    now = datetime.now(timezone.utc)
    from_date = now - timedelta(days=days)
    from_date_str = from_date.isoformat(timespec="seconds")
    to_date_str = now.isoformat(timespec="seconds")

    target_config = TARGETS[target]
    query = " OR ".join(target_config["keywords"])
    page_size = min(days * 10, 100)

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
        # Make API call
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        # Increment rate limit counter
        requests_used = increment_rate_limit()

        if data.get("status") != "ok":
            return jsonify({
                "error": "NewsAPI error",
                "message": data.get("message", "Unknown error"),
                "rate_limit": get_rate_limit_info()
            }), 500

        articles = data.get("articles", [])
        total_results = data.get("totalResults", 0)

        # Prepare response
        response_data = {
            "target": target,
            "days": days,
            "from": from_date_str,
            "to": to_date_str,
            "articles": articles,
            "totalResults": total_results,
            "escalation_keywords": target_config["escalation"],
            "target_keywords": target_config["keywords"],
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
