def fetch_oil_alpha_vantage():
    """Try Alpha Vantage API (750 requests/month = 25/day)"""
    try:
        # Alpha Vantage - Free tier with real API key
        url = f"https://www.alphavantage.co/query?function=BRENT&interval=daily&apikey={ALPHA_VANTAGE_KEY}"
        
        response = requests.get(url, timeout=10)
        
        if response.status_code != 200:
            print(f"[Oil Price] Alpha Vantage HTTP error: {response.status_code}")
            return None
        
        data = response.json()
        
        # Check for rate limit messages
        if 'Note' in data:
            print(f"[Oil Price] Alpha Vantage rate limit: {data['Note']}")
            return None
        
        if 'Information' in data:
            print(f"[Oil Price] Alpha Vantage info: {data['Information']}")
            return None
        
        if 'Error Message' in data:
            print(f"[Oil Price] Alpha Vantage error: {data['Error Message']}")
            return None
        
        # Extract data array
        time_series = data.get('data')
        
        # Validate we got actual price data
        if not time_series:
            print(f"[Oil Price] No 'data' field. Keys: {list(data.keys())}")
            return None
        
        if not isinstance(time_series, list) or len(time_series) == 0:
            print(f"[Oil Price] Empty data array")
            return None
        
        # Get latest price
        latest = time_series[0]
        
        if 'value' not in latest:
            print(f"[Oil Price] No 'value' in latest entry. Keys: {list(latest.keys())}")
            return None
        
        price = float(latest['value'])
        
        if price <= 0:
            print(f"[Oil Price] Invalid price: {price}")
            return None
        
        print(f"[Oil Price] ✅ Alpha Vantage: Brent ${price:.2f}")
        
        return {
            'price': round(price, 2),
            'change': 0,
            'change_percent': 0,
            'currency': 'USD',
            'commodity': 'Brent Crude',
            'source': 'Alpha Vantage'
        }
        
    except KeyError as e:
        print(f"[Oil Price] Alpha Vantage missing key: {e}")
        return None
    except ValueError as e:
        print(f"[Oil Price] Alpha Vantage value error: {e}")
        return None
    except Exception as e:
        print(f"[Oil Price] Alpha Vantage failed: {str(e)[:100]}")
        return None
