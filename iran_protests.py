"""
iran_protests.py
Standalone module for Iran Protests Analytics page
Handles oil prices, OPEC stats, and Iran-specific data
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
        # Get your key at: https://www.alphavantage.co/support/#api-key
        API_KEY = "YOUR_ALPHA_VANTAGE_KEY"  # Replace with your key
        
        # Brent crude oil ticker: BZ=F (or use WTI if needed)
        url = f"https://www.alphavantage.co/query?function=CRUDE_OIL_BRENT&interval=daily&apikey={API_KEY}"
        
        response = requests.get(url, timeout=10)
        data = response.json()
        
        # Parse the response
        if "data" in data and len(data["data"]) > 0:
            latest = data["data"][0]
            previous = data["data"][1] if len(data["data"]) > 1 else latest
            
            current_price = float(latest["value"])
            previous_price = float(previous["value"])
            
            # Calculate change
            price_change = current_price - previous_price
            percent_change = (price_change / previous_price) * 100
            
            # Determine direction arrow
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
            # Fallback to hardcoded data if API fails
            return get_fallback_oil_price()
            
    except Exception as e:
        print(f"[Oil Price API Error]: {e}")
        return get_fallback_oil_price()


def get_fallback_oil_price():
    """
    Fallback oil price data when API is unavailable
    """
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
        # You can use Alpha Vantage or another API for historical data
        # For now, return sample data structure
        
        sparkline_data = []
        base_price = 71.19
        
        # Generate sample historical data (replace with real API call)
        for i in range(days, 0, -1):
            date = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
            # Add some variation (replace with real data)
            price = base_price + (i % 10 - 5) * 0.5
            sparkline_data.append({
                "date": date,
                "price": round(price, 2)
            })
        
        return {
            "success": True,
            "data": sparkline_data,
            "days": days,
            "source": "sample"  # Replace with real source when API is integrated
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
    
    return {
        "success": True,
        "oil_price": oil_price,
        "reserves": reserves,
        "sparkline": sparkline,
        "timestamp": datetime.now().isoformat()
    }


# ============================================
# ALTERNATIVE: Simple API-Free Version
# ============================================

def get_iran_oil_data_simple():
    """
    Simple version without external API calls
    Returns hardcoded current data + OPEC stats
    Good for testing or if you don't want API dependencies
    """
    return {
        "success": True,
        "oil_price": {
            "current_price": 71.19,
            "price_change": 0.12,
            "percent_change": 0.17,
            "arrow": "↑",
            "trend": "up",
            "timestamp": datetime.now().strftime("%Y-%m-%d"),
            "currency": "USD",
            "unit": "bbl"
        },
        "reserves": get_iran_oil_reserves(),
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
