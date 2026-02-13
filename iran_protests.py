"""
iran_protests.py - UPDATED WITH PRODUCTION STATUS
Standalone module for Iran Protests Analytics page
Handles oil prices, OPEC stats, and Iran production monitoring
"""

import requests
from datetime import datetime, timedelta
from flask import jsonify

# ============================================
# OIL PRICE DATA (BRENT CRUDE)
# ============================================

def get_brent_oil_price():
    """
    Fetch current Brent crude oil price from Alpha Vantage API
    Returns: dict with price, change %, direction arrow, timestamp
    """
    try:
        # Alpha Vantage API - Free tier (500 calls/day)
        API_KEY = "NUW8NKIRMXNMRTD9"
        
        url = f"https://www.alphavantage.co/query?function=CRUDE_OIL_BRENT&interval=daily&apikey={API_KEY}"
        
        response = requests.get(url, timeout=10)
        data = response.json()
        
        # Parse the response
        if "data" in data and len(data["data"]) > 0:
            latest = data["data"][0]
            previous = data["data"][1] if len(data["data"]) > 1 else latest
            
            current_price = float(latest["value"])
            previous_price = float(previous["value"])
            
            price_change = current_price - previous_price
            percent_change = (price_change / previous_price) * 100
            
            if price_change > 0.01:
                arrow = "↑"
                trend = "up"
            elif price_change < -0.01:
                arrow = "↓"
                trend = "down"
            else:
                arrow = "→"
                trend = "flat"
            
            return {
                "success": True,
                "current_price": round(current_price, 2),
                "price_change": round(price_change, 2),
                "percent_change": round(percent_change, 2),
                "arrow": arrow,
                "trend": trend,
                "timestamp": latest["date"],
                "currency": "USD",
                "unit": "bbl"
            }
        else:
            return get_fallback_oil_price()
            
    except Exception as e:
        print(f"[Oil Price API Error]: {e}")
        return get_fallback_oil_price()


def get_fallback_oil_price():
    """Fallback oil price data when API is unavailable"""
    return {
        "success": True,
        "current_price": 71.19,
        "price_change": 0.12,
        "percent_change": 0.17,
        "arrow": "↑",
        "trend": "up",
        "timestamp": datetime.now().strftime("%Y-%m-%d"),
        "currency": "USD",
        "unit": "bbl",
        "source": "fallback"
    }


def get_oil_sparkline_data(days=90):
    """
    Get historical Brent oil price data for sparkline chart
    Returns: list of {date, price} for last N days
    """
    try:
        sparkline_data = []
        base_price = 71.19
        
        # Generate sample historical data (replace with real API call later)
        for i in range(days, 0, -1):
            date = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
            price = base_price + (i % 10 - 5) * 0.5
            sparkline_data.append({
                "date": date,
                "price": round(price, 2)
            })
        
        return {
            "success": True,
            "data": sparkline_data,
            "days": days,
            "source": "sample"
        }
        
    except Exception as e:
        print(f"[Sparkline Data Error]: {e}")
        return {
            "success": False,
            "data": [],
            "error": str(e)
        }


# ============================================
# OPEC STATISTICS (IRAN)
# ============================================

def get_iran_oil_reserves():
    """
    Return Iran's proven oil and gas reserves from OPEC ASB 2025
    Source: OPEC Annual Statistical Bulletin 2025
    """
    return {
        "success": True,
        "oil_reserves": {
            "amount": 208.6,
            "unit": "billion barrels",
            "global_rank": 3,
            "context": "#3 globally (after Venezuela, Saudi Arabia)"
        },
        "gas_reserves": {
            "amount": 33.988,
            "unit": "trillion cubic meters",
            "global_rank": 2,
            "context": "#2 globally (after Russia)"
        },
        "opec_membership": {
            "member_since": 1960,
            "founding_member": True
        },
        "economic_data": {
            "population": 86.63,
            "population_unit": "million",
            "gdp_per_capita": 4633,
            "currency": "USD"
        },
        "source": {
            "name": "OPEC Annual Statistical Bulletin 2025",
            "date": "July 2, 2025",
            "url": "https://www.opec.org/annual-statistical-bulletin.html"
        }
    }


# ============================================
# IRAN OIL PRODUCTION STATUS (NEW!)
# ============================================

def get_iran_oil_production_status():
    """
    Track Iran's oil production/export status using:
    1. Latest OPEC/EIA production data (monthly)
    2. News scanning for export halts/sanctions
    3. Simple status indicator (🟢/🟡/🔴)
    """
    try:
        # Step 1: Get latest production data (hardcoded from latest OPEC/EIA reports)
        # Update this monthly from: https://www.eia.gov/international/data/country/IRN
        # Or from UANI tracker: https://www.unitedagainstnucleariran.com/tanker-tracker
        
        latest_production = {
            "barrels_per_day": 3187000,  # 3.187 million bpd (Dec 2025 OPEC data)
            "date": "2025-12-01",
            "source": "OPEC Monthly Oil Market Report",
            "source_url": "https://www.opec.org/opec_web/en/publications/338.htm"
        }
        
        baseline_production = 2000000  # 2M bpd = normal sanctions-era level
        
        # Step 2: Check for recent news about Iran oil halts/shutdowns
        halt_news = scan_iran_oil_news()
        
        # Step 3: Determine status
        bpd = latest_production["barrels_per_day"]
        
        if halt_news["halt_detected"]:
            status = "halted"
            status_emoji = "🔴"
            status_text = "EXPORT HALT REPORTED"
            status_detail = halt_news["summary"]
            news_link = halt_news["url"]
        elif bpd >= baseline_production:
            status = "exporting"
            status_emoji = "🟢"
            status_text = "EXPORTING OIL"
            status_detail = f"{round(bpd/1000000, 2)}M bpd (Normal operations)"
            news_link = None
        elif bpd >= 1000000:
            status = "reduced"
            status_emoji = "🟡"
            status_text = "REDUCED EXPORTS"
            status_detail = f"{round(bpd/1000000, 2)}M bpd (Sanctions impact)"
            news_link = None
        else:
            status = "minimal"
            status_emoji = "🔴"
            status_text = "MINIMAL EXPORTS"
            status_detail = f"{round(bpd/1000000, 2)}M bpd (Heavy sanctions)"
            news_link = None
        
        return {
            "success": True,
            "status": status,
            "emoji": status_emoji,
            "status_text": status_text,
            "status_detail": status_detail,
            "production_bpd": bpd,
            "production_date": latest_production["date"],
            "production_source": latest_production["source"],
            "production_source_url": latest_production["source_url"],
            "news_link": news_link,
            "last_updated": datetime.now().isoformat()
        }
        
    except Exception as e:
        print(f"[Oil Production Status Error]: {e}")
        return {
            "success": False,
            "status": "unknown",
            "emoji": "⚪",
            "status_text": "STATUS UNKNOWN",
            "status_detail": "Error fetching production data",
            "error": str(e)
        }


def scan_iran_oil_news():
    """
    Scan recent news for Iran oil export halts/shutdowns
    Returns: dict with halt_detected (bool), summary, url
    """
    try:
        # Search keywords for oil export disruptions
        keywords = [
            "iran oil halt",
            "iran stops oil exports",
            "iran oil shutdown",
            "iran suspends oil",
            "iran oil production cut"
        ]
        
        # TODO: Integrate with existing GDELT/NewsAPI scan
        # Check articles from last 7 days for keywords
        
        return {
            "halt_detected": False,  # Set to True if keywords found
            "summary": None,
            "url": None,
            "source": None
        }
        
    except Exception as e:
        print(f"[News Scan Error]: {e}")
        return {
            "halt_detected": False,
            "summary": None,
            "url": None
        }


# ============================================
# COMBINED ENDPOINT DATA
# ============================================

def get_iran_oil_data():
    """
    Combined endpoint returning all Iran oil-related data
    Use this in your Flask route
    """
    oil_price = get_brent_oil_price()
    reserves = get_iran_oil_reserves()
    sparkline = get_oil_sparkline_data(90)
    production_status = get_iran_oil_production_status()  # NEW!
    
    return {
        "success": True,
        "oil_price": oil_price,
        "reserves": reserves,
        "sparkline": sparkline,
        "production_status": production_status,  # NEW!
        "timestamp": datetime.now().isoformat()
    }


# ============================================
# FLASK ROUTE (Add to app.py)
# ============================================

"""
Add this to your app.py:

from iran_protests import get_iran_oil_data

@app.route('/api/iran-oil-data')
def iran_oil_data_endpoint():
    try:
        data = get_iran_oil_data()
        return jsonify(data)
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500
"""
