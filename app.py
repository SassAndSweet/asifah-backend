"""
Asifah Analytics - Flask Backend v1.8 IRAN PROTESTS TRACKER
Added Iran protest monitoring with multilingual OSINT and protest-specific metrics
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import os
from datetime import datetime, timedelta, timezone
import hashlib
import traceback
import time

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

# Reddit configuration
REDDIT_USER_AGENT = "AsifahAnalytics/1.7 (OSINT monitoring tool)"
REDDIT_SUBREDDITS = {
    "hezbollah": ["ForbiddenBromance", "Israel", "Lebanon", "geopolitics", "MiddleEastNews", "OSINT"],
    "iran": ["Iran", "Israel", "geopolitics", "MiddleEastNews", "OSINT"],
    "houthis": ["Yemen", "geopolitics", "MiddleEastNews", "OSINT"]
}

# Polymarket configuration
POLYMARKET_KEYWORDS = [
    "Israel strike",
    "Lebanon attack",
    "Iran strike",
    "Houthis Yemen",
    "Hezbollah",
    "Israel Iran",
    "Israel Lebanon",
    "Gaza",
    "Middle East conflict"
]

# Target configurations with multilingual keywords
TARGETS = {
    "hezbollah": {
        "keywords_en": ["Hezbollah", "Lebanon Israel", "Southern Lebanon", "Nasrallah"],
        "keywords_ar": ["حزب الله", "لبنان", "إسرائيل", "نصرالله", "ضربة", "عملية عسكرية"],
        "keywords_he": ["חיזבאללה", "לבנון", "נסראללה", "תקיפה"],
        "keywords_fa": [],  # No Farsi coverage for Hezbollah
        "domains_ar": ["aawsat.com", "alhurra.com", "alarabiya.net", "aljazeera.net"],
        "reddit_keywords": ["Hezbollah", "Lebanon", "Israel", "IDF", "Lebanese", "border", "missile", "strike"],
        "escalation": [
            "strike", "attack", "military action", "retaliate", "offensive",
            "troops", "border", "rocket", "missile", "ضربة", "هجوم", "عملية",
        ],
    },
    "iran": {
        "keywords_en": ["Iran Israel", "Iranian nuclear", "Tehran", "IRGC"],
        "keywords_ar": ["إيران", "إسرائيل", "طهران", "نووي", "الحرس الثوري"],
        "keywords_he": ["איראן", "ישראל", "טהרן", "גרעיני", "משמרות המהפכה"],
        "keywords_fa": ["ایران", "اسرائیل", "تهران", "هسته‌ای", "سپاه پاسداران", "حمله"],
        "domains_ar": ["aawsat.com", "alhurra.com", "alarabiya.net"],
        "reddit_keywords": ["Iran", "Israel", "IRGC", "nuclear", "Tehran", "strike", "sanctions"],
        "escalation": [
            "strike", "attack", "military action", "retaliate", "sanctions",
            "nuclear facility", "enrichment", "weapons", "ضربة", "هجوم", "حمله",
        ],
    },
    "houthis": {
        "keywords_en": ["Houthis", "Yemen", "Red Sea", "Ansar Allah"],
        "keywords_ar": ["الحوثي", "اليمن", "البحر الأحمر", "أنصار الله", "صاروخ"],
        "keywords_he": ["חות'ים", "תימן", "ים סוף", "טיל"],
        "keywords_fa": [],  # No Farsi coverage for Houthis
        "domains_ar": ["aawsat.com", "alhurra.com", "alarabiya.net"],
        "reddit_keywords": ["Houthi", "Yemen", "Red Sea", "shipping", "missile", "drone", "Ansar Allah"],
        "escalation": [
            "strike", "attack", "military action", "shipping",
            "missile", "drone", "blockade", "ضربة", "هجوم", "صاروخ",
        ],
    },
}

# Iran Protests configuration with multilingual keywords
IRAN_PROTESTS = {
    "keywords_en": [
        "Iran protest", "Iran protests", "Tehran protest", "Iranian protesters",
        "Iran demonstration", "Iran unrest", "Iran uprising", "Women Life Freedom",
        "Mahsa Amini", "Iran riot", "Iran crackdown"
    ],
    "keywords_fa": [
        "اعتراضات", "تظاهرات", "ناآرامی", "معترضان", "خیابان",
        "کشته", "دستگیری", "بازداشت", "سرکوب", "زن زندگی آزادی",
        "مهسا امینی", "شورش", "تجمع"
    ],
    "keywords_ar": [
        "احتجاجات إيران", "متظاهرون إيرانيون", "طهران", "اضطرابات إيران",
        "قمع إيران", "اعتقالات إيران"
    ],
    "keywords_he": [
        "הפגנות באיראן", "מחאות איראן", "טהרן", "מפגינים איראנים",
        "אי שקט באיראן", "דיכוי באיראן"
    ],
    "reddit_keywords": [
        "Iran protest", "Iranian unrest", "Tehran", "Mahsa Amini",
        "Women Life Freedom", "Iran revolution", "Iran regime"
    ],
    "subreddits": ["iran", "Iranian", "NewIran", "worldnews", "geopolitics", "OSINT"],
    "domains_fa": ["bbc.com/persian", "radiofarda.com", "iranintl.com"],
    
    # Keywords for extracting specific metrics
    "crowd_indicators": [
        "thousands", "hundreds", "crowd", "protesters", "demonstrators",
        "هزاران", "صدها", "جمعیت", "معترضان", "تظاهرکنندگان"
    ],
    "casualty_indicators": [
        "killed", "dead", "death", "died", "shot", "injured", "wounded",
        "کشته", "مرگ", "زخمی", "قتل", "قتیل", "جريح"
    ],
    "arrest_indicators": [
        "arrested", "detention", "detained", "imprisoned", "custody",
        "بازداشت", "دستگیری", "زندان", "اعتقال", "محتجز"
    ],
    "city_names": [
        "Tehran", "Mashhad", "Isfahan", "Shiraz", "Tabriz", "Karaj",
        "Qom", "Ahvaz", "Kermanshah", "Rasht", "Kerman", "Urmia",
        "تهران", "مشهد", "اصفهان", "شیراز", "تبریز", "کرج"
    ],
    "regime_stability_indicators": {
        "positive": [  # Pro-regime stability
            "crackdown", "arrested", "suppressed", "security forces", "IRGC deployed",
            "internet shutdown", "سرکوب", "دستگیری", "نیروهای امنیتی"
        ],
        "negative": [  # Anti-regime stability
            "defection", "join protesters", "refuse orders", "military defection",
            "انشقاق", "پیوستن به معترضان", "عدم اطاعت"
        ]
    }
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
    
    if not keywords or len(keywords) == 0:
        diagnostic_info["error"] = "No keywords provided"
        return [], diagnostic_info
    
    # Try multiple GDELT query strategies
    strategies = [
        # Strategy 1: OR query with proper parentheses (GDELT requirement)
        {
            "name": "or_query_parentheses",
            "url": "https://api.gdeltproject.org/api/v2/doc/doc",
            "params": {
                "query": f"({' OR '.join(keywords)})",  # FIXED: Wrapped in parentheses
                "mode": "artlist",
                "maxrecords": 20,
                "timespan": f"{days}d",
                "format": "json",
                "sort": "datedesc"
            }
        },
        # Strategy 2: Single most important keyword
        {
            "name": "single_keyword",
            "url": "https://api.gdeltproject.org/api/v2/doc/doc",
            "params": {
                "query": keywords[0],
                "mode": "artlist",
                "maxrecords": 20,
                "timespan": f"{days}d",
                "format": "json",
                "sort": "datedesc"
            }
        },
        # Strategy 3: Try with domain filter if available
        {
            "name": "with_domains",
            "url": "https://api.gdeltproject.org/api/v2/doc/doc",
            "params": {
                "query": keywords[0],
                "mode": "artlist",
                "maxrecords": 20,
                "timespan": f"{days}d",
                "format": "json",
                "sort": "datedesc",
                "domain": domains[0] if domains else None
            }
        }
    ]
    
    # Add source language if specified
    lang_codes = {"ar": "ara", "he": "heb", "fa": "per"}
    if language in lang_codes:
        for strategy in strategies:
            if strategy["params"]:
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


def fetch_reddit_posts_from_subreddit(subreddit, keywords, days):
    """
    Fetch Reddit posts from a single subreddit
    Used by Iran protests tracker to query custom subreddit lists
    Returns (posts, diagnostic_info)
    """
    diagnostic_info = {
        "attempted": True,
        "subreddit": subreddit,
        "posts_found": 0,
        "success": False
    }
    
    # Time filter for Reddit
    if days <= 1:
        time_filter = "day"
    elif days <= 7:
        time_filter = "week"
    elif days <= 30:
        time_filter = "month"
    else:
        time_filter = "year"
    
    try:
        # Build search query
        query = " OR ".join(keywords[:3])  # Limit to top 3 keywords
        
        url = f"https://www.reddit.com/r/{subreddit}/search.json"
        params = {
            "q": query,
            "restrict_sr": "true",
            "sort": "new",
            "t": time_filter,
            "limit": 25
        }
        
        headers = {"User-Agent": REDDIT_USER_AGENT}
        
        # Rate limiting
        time.sleep(1)
        
        response = requests.get(url, params=params, headers=headers, timeout=10)
        
        if response.status_code == 429:
            diagnostic_info["error"] = "Rate limited"
            return [], diagnostic_info
        
        response.raise_for_status()
        data = response.json()
        
        posts = []
        if "data" in data and "children" in data["data"]:
            for post in data["data"]["children"]:
                post_data = post.get("data", {})
                
                normalized_post = {
                    "title": post_data.get("title", "")[:200],
                    "description": post_data.get("selftext", "")[:300],
                    "url": f"https://www.reddit.com{post_data.get('permalink', '')}",
                    "publishedAt": datetime.fromtimestamp(
                        post_data.get("created_utc", 0),
                        tz=timezone.utc
                    ).isoformat(),
                    "source": {"name": f"Reddit r/{subreddit}"},
                    "content": post_data.get("selftext", "")[:500]
                }
                
                posts.append(normalized_post)
        
        diagnostic_info["posts_found"] = len(posts)
        diagnostic_info["success"] = True
        diagnostic_info["count"] = len(posts)
        
        return posts, diagnostic_info
        
    except Exception as e:
        diagnostic_info["error"] = str(e)
        diagnostic_info["success"] = False
        return [], diagnostic_info


def fetch_reddit_posts(target, keywords, days):
    """
    Fetch Reddit posts from relevant subreddits
    Returns (posts, diagnostic_info)
    """
    diagnostic_info = {
        "attempted": True,
        "subreddits_searched": [],
        "posts_found": 0,
        "errors": []
    }
    
    # Get subreddit list for this target
    subreddits = REDDIT_SUBREDDITS.get(target, [])
    if not subreddits:
        diagnostic_info["error"] = "No subreddits configured for target"
        return [], diagnostic_info
    
    all_posts = []
    
    # Time filter for Reddit (day, week, month, year, all)
    if days <= 1:
        time_filter = "day"
    elif days <= 7:
        time_filter = "week"
    elif days <= 30:
        time_filter = "month"
    else:
        time_filter = "year"
    
    # Search each subreddit
    for subreddit in subreddits:
        try:
            diagnostic_info["subreddits_searched"].append(subreddit)
            
            # Build search query - use OR for keywords
            query = " OR ".join(keywords[:3])  # Limit to top 3 keywords for Reddit
            
            url = f"https://www.reddit.com/r/{subreddit}/search.json"
            params = {
                "q": query,
                "restrict_sr": "true",  # Search only this subreddit
                "sort": "new",
                "t": time_filter,
                "limit": 25  # Get up to 25 posts per subreddit
            }
            
            headers = {
                "User-Agent": REDDIT_USER_AGENT
            }
            
            # Rate limiting - Reddit allows ~60 requests/min
            time.sleep(1)  # Be polite to Reddit
            
            response = requests.get(url, params=params, headers=headers, timeout=10)
            
            if response.status_code == 429:  # Rate limited
                diagnostic_info["errors"].append(f"r/{subreddit}: Rate limited")
                continue
            
            response.raise_for_status()
            data = response.json()
            
            # Parse Reddit JSON structure
            if "data" in data and "children" in data["data"]:
                posts = data["data"]["children"]
                
                for post in posts:
                    post_data = post.get("data", {})
                    
                    # Normalize to article format
                    normalized_post = {
                        "title": post_data.get("title", "")[:200],
                        "description": post_data.get("selftext", "")[:300],
                        "url": f"https://www.reddit.com{post_data.get('permalink', '')}",
                        "publishedAt": datetime.fromtimestamp(
                            post_data.get("created_utc", 0), 
                            tz=timezone.utc
                        ).isoformat(),
                        "source": {"name": f"r/{subreddit}"},
                        "content": post_data.get("selftext", ""),
                        "language": "en",
                        "reddit_score": post_data.get("score", 0),
                        "reddit_comments": post_data.get("num_comments", 0),
                        "reddit_upvote_ratio": post_data.get("upvote_ratio", 0)
                    }
                    
                    all_posts.append(normalized_post)
                
        except Exception as e:
            diagnostic_info["errors"].append(f"r/{subreddit}: {str(e)}")
            continue
    
    diagnostic_info["posts_found"] = len(all_posts)
    diagnostic_info["success"] = len(all_posts) > 0
    
    return all_posts, diagnostic_info


def fetch_polymarket_markets(keywords, limit=10):
    """
    Fetch prediction markets from Polymarket API
    Returns (markets, diagnostic_info)
    """
    diagnostic_info = {
        "attempted": True,
        "keywords_searched": [],
        "markets_found": 0,
        "errors": []
    }
    
    all_markets = {}  # Use dict to deduplicate by market ID
    
    for keyword in keywords:
        try:
            diagnostic_info["keywords_searched"].append(keyword)
            
            url = "https://gamma-api.polymarket.com/public-search"
            params = {
                "q": keyword,
                "events_status": "active",  # Only active markets
                "limit_per_type": 5  # 5 results per keyword
            }
            
            # Be polite to Polymarket API
            time.sleep(0.5)
            
            response = requests.get(url, params=params, timeout=10)
            
            if response.status_code == 429:  # Rate limited
                diagnostic_info["errors"].append(f"'{keyword}': Rate limited")
                continue
            
            response.raise_for_status()
            data = response.json()
            
            # Extract events (which contain markets)
            events = data.get("events", [])
            
            for event in events:
                # Skip if inactive or closed
                if not event.get("active") or event.get("closed"):
                    continue
                
                markets = event.get("markets", [])
                
                for market in markets:
                    # Skip if inactive or closed
                    if not market.get("active") or market.get("closed"):
                        continue
                    
                    market_id = market.get("id")
                    if not market_id or market_id in all_markets:
                        continue  # Skip duplicates
                    
                    # Parse outcome prices (usually JSON string)
                    outcome_prices_str = market.get("outcomePrices", "[]")
                    try:
                        outcome_prices = eval(outcome_prices_str) if isinstance(outcome_prices_str, str) else outcome_prices_str
                        probability = float(outcome_prices[0]) if len(outcome_prices) > 0 else 0
                    except:
                        probability = 0
                    
                    # Extract market info
                    market_info = {
                        "id": market_id,
                        "question": market.get("question", "")[:200],
                        "probability": round(probability, 3),
                        "volume": market.get("volumeNum", 0),
                        "slug": market.get("slug", ""),
                        "url": f"https://polymarket.com/event/{event.get('slug', '')}" if event.get('slug') else "",
                        "end_date": market.get("endDate", ""),
                        "category": event.get("category", "")
                    }
                    
                    all_markets[market_id] = market_info
            
        except requests.Timeout:
            diagnostic_info["errors"].append(f"'{keyword}': Timeout")
            continue
        except Exception as e:
            diagnostic_info["errors"].append(f"'{keyword}': {str(e)}")
            continue
    
    # Sort by volume (most traded = most credible)
    sorted_markets = sorted(
        all_markets.values(), 
        key=lambda x: x["volume"], 
        reverse=True
    )[:limit]
    
    diagnostic_info["markets_found"] = len(sorted_markets)
    diagnostic_info["success"] = len(sorted_markets) > 0
    
    return sorted_markets, diagnostic_info


@app.route("/")
def home():
    """Health check endpoint"""
    rate_info = get_rate_limit_info()
    return jsonify({
        "status": "online",
        "service": "Asifah Analytics Backend - IRAN PROTESTS TRACKER v1.8",
        "version": "1.8-iran-protests",
        "features": ["caching", "rate_limiting", "quadrilingual_gdelt", "enhanced_diagnostics", "reddit_osint", "polymarket_markets", "iran_protests_tracker"],
        "has_api_key": bool(NEWS_API_KEY),
        "rate_limit": rate_info,
        "cache_info": {
            "duration_minutes": CACHE_DURATION_MINUTES,
            "cached_items": len(CACHE)
        },
        "reddit_subreddits": REDDIT_SUBREDDITS,
        "polymarket_keywords": POLYMARKET_KEYWORDS,
        "iran_protests_config": {
            "subreddits": IRAN_PROTESTS.get("subreddits"),
            "languages": ["en", "fa", "ar", "he"]
        },
        "endpoints": {
            "/": "Health check with rate limit info",
            "/scan": "Scan news sources (GET with ?target=hezbollah&days=7)",
            "/scan-iran-protests": "Track Iran protest activity (GET with ?days=7)",
            "/health": "Basic health check",
            "/rate-limit": "Current rate limit status",
            "/polymarket-data": "Fetch Polymarket prediction markets"
        },
    })


@app.route("/rate-limit", methods=["GET"])
def rate_limit_status():
    """Endpoint to check current rate limit"""
    return jsonify(get_rate_limit_info())


@app.route("/scan", methods=["GET"])
def scan():
    """
    Scan news sources for a specific target with multilingual + Reddit support
    REDDIT VERSION v1.7

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
        "gdelt_fa": {},
        "reddit": {}
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
        
        # Fetch from Reddit
        articles_reddit, reddit_diag = fetch_reddit_posts(
            target,
            target_config.get("reddit_keywords", target_config["keywords_en"]),
            days
        )
        diagnostics["reddit"] = reddit_diag
        
        # Combine all articles
        all_articles = articles_en + articles_ar + articles_he + articles_fa + articles_reddit
        
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
            "articles_reddit": articles_reddit,
            "totalResults": len(all_articles),
            "totalResults_en": len(articles_en),
            "totalResults_ar": len(articles_ar),
            "totalResults_he": len(articles_he),
            "totalResults_fa": len(articles_fa),
            "totalResults_reddit": len(articles_reddit),
            "escalation_keywords": target_config["escalation"],
            "target_keywords": target_config["keywords_en"],
            "reddit_subreddits": REDDIT_SUBREDDITS[target],
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


@app.route("/polymarket-data", methods=["GET"])
def polymarket_data():
    """
    Fetch prediction market data from Polymarket
    Returns top 10 most-traded active markets related to Israel/Middle East conflicts
    """
    
    # Check cache first
    cache_key = "polymarket_markets"
    cached_data = get_from_cache(cache_key)
    
    if cached_data:
        cached_data["cached"] = True
        return jsonify(cached_data)
    
    try:
        # Fetch markets from Polymarket
        markets, diagnostics = fetch_polymarket_markets(
            POLYMARKET_KEYWORDS, 
            limit=10
        )
        
        response_data = {
            "markets": markets,
            "total_markets": len(markets),
            "keywords_used": POLYMARKET_KEYWORDS,
            "last_updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "cached": False,
            "diagnostics": diagnostics,
            "disclaimer": "Polymarket data represents betting market probabilities (crowd-sourced predictions). This is supplementary data only and does not constitute intelligence assessment or analytical tradecraft."
        }
        
        # Cache for 2 hours (longer than news cache since markets change slower)
        CACHE[cache_key] = {
            "data": response_data,
            "timestamp": datetime.now(timezone.utc),
            "expires": datetime.now(timezone.utc) + timedelta(hours=2)
        }
        
        return jsonify(response_data)
        
    except Exception as e:
        return jsonify({
            "error": "Server error",
            "message": str(e),
            "traceback": traceback.format_exc()
        }), 500


def calculate_protest_metrics(articles, config, days):
    """
    Calculate Iran protest-specific metrics from articles
    Returns protest intensity, casualties, regime stability, and geographic spread
    """
    if not articles or len(articles) == 0:
        return {
            "protest_intensity": 10,
            "estimated_crowd": "Unknown",
            "casualties_reported": 0,
            "arrests_reported": 0,
            "regime_stability": 75,  # High = stable regime
            "cities_affected": [],
            "total_cities": 0
        }
    
    # Count mentions of various indicators
    crowd_mentions = 0
    casualty_mentions = 0
    arrest_mentions = 0
    cities_found = set()
    regime_positive = 0  # Pro-regime indicators (crackdowns, arrests)
    regime_negative = 0  # Anti-regime indicators (defections, etc.)
    
    # Estimated numbers from text parsing
    estimated_deaths = 0
    estimated_arrests = 0
    
    for article in articles:
        text = (
            (article.get('title', '') or '') + ' ' +
            (article.get('description', '') or '') + ' ' +
            (article.get('content', '') or '')
        ).lower()
        
        # Count crowd indicators
        for indicator in config.get("crowd_indicators", []):
            if indicator.lower() in text:
                crowd_mentions += 1
        
        # Count casualty indicators
        for indicator in config.get("casualty_indicators", []):
            if indicator.lower() in text:
                casualty_mentions += 1
                
                # Try to extract numbers near "killed" or "dead"
                import re
                patterns = [
                    r'(\d+)\s+(?:killed|dead|deaths)',
                    r'(?:killed|dead)\s+(\d+)',
                    r'(\d+)\s+(?:کشته|مرگ)'
                ]
                for pattern in patterns:
                    matches = re.findall(pattern, text)
                    for match in matches:
                        try:
                            estimated_deaths += int(match)
                        except:
                            pass
        
        # Count arrest indicators
        for indicator in config.get("arrest_indicators", []):
            if indicator.lower() in text:
                arrest_mentions += 1
                
                # Try to extract arrest numbers
                import re
                patterns = [
                    r'(\d+)\s+(?:arrested|detained)',
                    r'(?:arrested|detained)\s+(\d+)',
                    r'(\d+)\s+(?:بازداشت|دستگیری)'
                ]
                for pattern in patterns:
                    matches = re.findall(pattern, text)
                    for match in matches:
                        try:
                            estimated_arrests += int(match)
                        except:
                            pass
        
        # Detect cities
        for city in config.get("city_names", []):
            if city.lower() in text:
                cities_found.add(city)
        
        # Regime stability indicators
        for indicator in config.get("regime_stability_indicators", {}).get("positive", []):
            if indicator.lower() in text:
                regime_positive += 1
        
        for indicator in config.get("regime_stability_indicators", {}).get("negative", []):
            if indicator.lower() in text:
                regime_negative += 1
    
    # Calculate protest intensity (0-100)
    # Based on: coverage frequency, crowd mentions, geographic spread
    coverage_per_day = len(articles) / max(days, 1)
    intensity = min(
        (coverage_per_day * 5) +  # Article frequency
        (crowd_mentions * 3) +     # Crowd size mentions
        (len(cities_found) * 4) +  # Geographic spread
        (casualty_mentions * 2),   # Violence indicators
        100
    )
    intensity = max(10, int(intensity))
    
    # Estimate crowd size category
    if crowd_mentions == 0:
        crowd_estimate = "Unknown - no reports"
    elif crowd_mentions < 3:
        crowd_estimate = "Hundreds (isolated incidents)"
    elif crowd_mentions < 10:
        crowd_estimate = "Thousands (localized)"
    elif crowd_mentions < 20:
        crowd_estimate = "Tens of thousands (widespread)"
    else:
        crowd_estimate = "Hundreds of thousands+ (nationwide)"
    
    # Calculate regime stability (0-100, where 100 = very stable regime)
    # High crackdown = regime still in control = higher stability
    # Defections, spread, intensity = lower stability
    base_stability = 70
    stability_score = base_stability
    stability_score += min(regime_positive * 2, 30)  # Crackdowns increase "stability"
    stability_score -= min(regime_negative * 10, 40)  # Defections decrease stability
    stability_score -= min(len(cities_found) * 2, 20)  # Spread decreases stability
    stability_score -= min((intensity / 100) * 30, 30)  # High intensity decreases stability
    stability_score = max(5, min(int(stability_score), 95))
    
    return {
        "protest_intensity": intensity,
        "estimated_crowd": crowd_estimate,
        "casualties_reported": max(casualty_mentions, estimated_deaths),
        "arrests_reported": max(arrest_mentions, estimated_arrests),
        "regime_stability": stability_score,
        "cities_affected": sorted(list(cities_found)),
        "total_cities": len(cities_found),
        "coverage_volume": len(articles),
        "crowd_mentions": crowd_mentions,
        "casualty_mentions": casualty_mentions,
        "arrest_mentions": arrest_mentions
    }


@app.route("/scan-iran-protests", methods=["GET"])
def scan_iran_protests():
    """
    Scan news sources for Iran protest activity
    Returns protest metrics, casualties, regime stability, and geographic spread
    
    Query parameters:
    - days: number of days to look back (1-30, default 7)
    """
    
    if not NEWS_API_KEY:
        return jsonify({
            "error": "Configuration error",
            "message": "NEWS_API_KEY is not set on the server.",
        }), 500
    
    # Get parameters
    days_param = request.args.get("days", "7")
    
    try:
        days = int(days_param)
    except ValueError:
        days = 7
    
    days = max(1, min(days, 30))
    
    # Check cache first
    cache_key = get_cache_key("iran_protests", days)
    cached_data = get_from_cache(cache_key)
    
    if cached_data:
        cached_data["cached"] = True
        cached_data["rate_limit"] = get_rate_limit_info()
        return jsonify(cached_data)
    
    # Check rate limit
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
    
    config = IRAN_PROTESTS
    page_size = min(days * 10, 100)
    
    # Initialize diagnostic tracking
    diagnostics = {
        "newsapi": {},
        "gdelt_fa": {},
        "gdelt_ar": {},
        "gdelt_he": {},
        "reddit": {}
    }
    
    try:
        # Fetch from NewsAPI (English)
        query_en = " OR ".join(config["keywords_en"][:5])  # Limit to top 5 keywords
        url = "https://newsapi.org/v2/everything"
        params = {
            "q": query_en,
            "from": from_date_str,
            "to": to_date_str,
            "sortBy": "publishedAt",
            "language": "en",
            "pageSize": page_size,
            "apiKey": NEWS_API_KEY,
        }
        
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        articles_en = []
        if data.get("status") == "ok":
            articles_en = data.get("articles", [])
            increment_rate_limit()
        
        diagnostics["newsapi"] = {
            "success": len(articles_en) > 0,
            "count": len(articles_en)
        }
        
        # Fetch from GDELT (Farsi) - PRIMARY source for Iran protests
        articles_fa, gdelt_fa_diag = fetch_gdelt_articles(
            config["keywords_fa"][:5],  # Top 5 Farsi keywords
            "fa",
            days,
            config.get("domains_fa")
        )
        diagnostics["gdelt_fa"] = gdelt_fa_diag
        
        # Fetch from GDELT (Arabic)
        articles_ar, gdelt_ar_diag = fetch_gdelt_articles(
            config["keywords_ar"][:5],
            "ar",
            days
        )
        diagnostics["gdelt_ar"] = gdelt_ar_diag
        
        # Fetch from GDELT (Hebrew)
        articles_he, gdelt_he_diag = fetch_gdelt_articles(
            config["keywords_he"][:5],
            "he",
            days
        )
        diagnostics["gdelt_he"] = gdelt_he_diag
        
        # Fetch from Reddit - use custom subreddit list for Iran
        reddit_posts = []
        reddit_diag = {"success": False, "count": 0}
        
        for subreddit in config.get("subreddits", []):
            posts, diag = fetch_reddit_posts_from_subreddit(
                subreddit,
                config.get("reddit_keywords", config["keywords_en"]),
                days
            )
            reddit_posts.extend(posts)
            if diag.get("success"):
                reddit_diag["success"] = True
                reddit_diag["count"] = reddit_diag.get("count", 0) + diag.get("count", 0)
        
        diagnostics["reddit"] = reddit_diag
        
        # Combine all articles
        all_articles = articles_en + articles_fa + articles_ar + articles_he + reddit_posts
        
        # Calculate protest metrics
        metrics = calculate_protest_metrics(all_articles, config, days)
        
        # Prepare response
        response_data = {
            "days": days,
            "from": from_date_str,
            "to": to_date_str,
            "articles": all_articles,
            "articles_en": articles_en,
            "articles_fa": articles_fa,
            "articles_ar": articles_ar,
            "articles_he": articles_he,
            "articles_reddit": reddit_posts,
            "totalResults": len(all_articles),
            "totalResults_en": len(articles_en),
            "totalResults_fa": len(articles_fa),
            "totalResults_ar": len(articles_ar),
            "totalResults_he": len(articles_he),
            "totalResults_reddit": len(reddit_posts),
            
            # Protest-specific metrics
            "protest_intensity": metrics["protest_intensity"],
            "estimated_crowd": metrics["estimated_crowd"],
            "casualties_reported": metrics["casualties_reported"],
            "arrests_reported": metrics["arrests_reported"],
            "regime_stability": metrics["regime_stability"],
            "cities_affected": metrics["cities_affected"],
            "total_cities": metrics["total_cities"],
            
            # Additional context
            "coverage_volume": metrics["coverage_volume"],
            "crowd_mentions": metrics["crowd_mentions"],
            "casualty_mentions": metrics["casualty_mentions"],
            "arrest_mentions": metrics["arrest_mentions"],
            
            "cached": False,
            "rate_limit": get_rate_limit_info(),
            "diagnostics": diagnostics
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
