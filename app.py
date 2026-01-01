"""
Asifah Analytics - Flask Backend
Handles NewsAPI requests server-side to avoid CORS issues
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import os
from datetime import datetime, timedelta, timezone

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

# ---------------------------------------------------------------------------
# NewsAPI configuration
# ---------------------------------------------------------------------------

# Preferred: set NEWS_API_KEY as an environment variable on Render.
# This will use the env var if present, otherwise fall back to the
# hard-coded key. Long term, delete the hard-coded key from GitHub and
# rely only on the env var.
NEWS_API_KEY = os.environ.get("NEWS_API_KEY") or "32de6811aacf4fc2ab651901a08b5235"

# Target configurations
TARGETS = {
    "hezbollah": {
        "keywords": ["Hezbollah", "Lebanon Israel", "Southern Lebanon", "Nasrallah"],
        "escalation": [
            "strike",
            "attack",
            "military action",
            "retaliate",
            "offensive",
            "troops",
            "border",
            "rocket",
            "missile",
        ],
    },
    "iran": {
        "keywords": ["Iran Israel", "Iranian", "Tehran", "nuclear", "IRGC"],
        "escalation": [
            "strike",
            "attack",
            "military action",
            "retaliate",
            "sanctions",
            "nuclear facility",
            "enrichment",
            "weapons",
        ],
    },
    "houthis": {
        "keywords": ["Houthis", "Yemen", "Ansar Allah", "Red Sea"],
        "escalation": [
            "strike",
            "attack",
            "military action",
            "shipping",
            "missile",
            "drone",
            "blockade",
        ],
    },
}


@app.route("/")
def home():
    """Health check endpoint"""
    return jsonify(
        {
            "status": "online",
            "service": "Asifah Analytics Backend",
            "version": "1.1",
            "has_api_key": bool(NEWS_API_KEY),
            "endpoints": {
                "/": "Health check",
                "/scan": "Scan news sources (GET with ?target=hezbollah&days=7)",
                "/health": "Basic health check",
            },
        }
    )


@app.route("/scan", methods=["GET"])
def scan():
    """
    Scan news sources for a specific target

    Query parameters:
    - target: hezbollah, iran, or houthis
    - days: number of days to look back (1–30).
            Frontend currently uses: 1, 2, 7, 30
    """

    # Ensure API key is configured
    if not NEWS_API_KEY:
        return (
            jsonify(
                {
                    "error": "Configuration error",
                    "message": "NEWS_API_KEY is not set on the server.",
                }
            ),
            500,
        )

    # -------------------- Get & validate parameters -----------------------
    target = (request.args.get("target") or "").lower()
    days_param = request.args.get("days", "7")

    try:
        days = int(days_param)
    except ValueError:
        days = 7

    # Clamp days to [1, 30]
    days = max(1, min(days, 30))

    # Validate target
    if target not in TARGETS:
        return (
            jsonify(
                {
                    "error": "Invalid target",
                    "valid_targets": list(TARGETS.keys()),
                }
            ),
            400,
        )

    # -------------------- Date range (UTC, precise) -----------------------
    now = datetime.now(timezone.utc)
    from_date = now - timedelta(days=days)

    from_date_str = from_date.isoformat(timespec="seconds")
    to_date_str = now.isoformat(timespec="seconds")

    # -------------------- Build NewsAPI request ---------------------------
    target_config = TARGETS[target]
    query = " OR ".join(target_config["keywords"])

    # Scale page size with days, cap at 100 (NewsAPI limit)
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
        # Call NewsAPI
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()  # handle HTTP 4xx/5xx
        data = response.json()

        if data.get("status") != "ok":
            # Logical error from NewsAPI (quota, bad key, etc.)
            return (
                jsonify(
                    {
                        "error": "NewsAPI error",
                        "message": data.get("message", "Unknown error"),
                    }
                ),
                500,
            )

        articles = data.get("articles", [])
        total_results = data.get("totalResults", 0)

        # Payload the frontend expects
        return jsonify(
            {
                "target": target,
                "days": days,
                "from": from_date_str,
                "to": to_date_str,
                "articles": articles,
                "totalResults": total_results,
                "escalation_keywords": target_config["escalation"],
                "target_keywords": target_config["keywords"],
            }
        )

    except requests.exceptions.RequestException as e:
        # Network / timeout / HTTP-level issue
        return (
            jsonify(
                {
                    "error": "Request failed",
                    "message": str(e),
                }
            ),
            500,
        )
    except Exception as e:
        # Anything else
        return (
            jsonify(
                {
                    "error": "Server error",
                    "message": str(e),
                }
            ),
            500,
        )


@app.route("/health", methods=["GET"])
def health():
    """Health check for monitoring"""
    return jsonify(
        {
            "status": "healthy",
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "has_api_key": bool(NEWS_API_KEY),
        }
    )


if __name__ == "__main__":
    # For local testing
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
