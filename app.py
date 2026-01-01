"""
Asifah Analytics - Flask Backend
Handles NewsAPI requests server-side to avoid CORS issues
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import os
from datetime import datetime, timedelta

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

# Your NewsAPI key
NEWS_API_KEY = '32de6811aacf4fc2ab651901a08b5235'

# Target configurations
TARGETS = {
    'hezbollah': {
        'keywords': ['Hezbollah', 'Lebanon Israel', 'Southern Lebanon', 'Nasrallah'],
        'escalation': ['strike', 'attack', 'military action', 'retaliate', 'offensive', 'troops', 'border', 'rocket', 'missile']
    },
    'iran': {
        'keywords': ['Iran Israel', 'Iranian', 'Tehran', 'nuclear', 'IRGC'],
        'escalation': ['strike', 'attack', 'military action', 'retaliate', 'sanctions', 'nuclear facility', 'enrichment', 'weapons']
    },
    'houthis': {
        'keywords': ['Houthis', 'Yemen', 'Ansar Allah', 'Red Sea'],
        'escalation': ['strike', 'attack', 'military action', 'shipping', 'missile', 'drone', 'blockade']
    }
}


@app.route('/')
def home():
    """Health check endpoint"""
    return jsonify({
        'status': 'online',
        'service': 'Asifah Analytics Backend',
        'version': '1.0',
        'endpoints': {
            '/': 'Health check',
            '/scan': 'Scan news sources (GET with ?target=hezbollah&days=7)'
        }
    })


@app.route('/scan', methods=['GET'])
def scan():
    """
    Scan news sources for a specific target
    
    Query parameters:
    - target: hezbollah, iran, or houthis
    - days: number of days to look back (1, 2, 7, or 30)
    """
    
    # Get parameters
    target = request.args.get('target', '').lower()
    days = int(request.args.get('days', 7))
    
    # Validate target
    if target not in TARGETS:
        return jsonify({
            'error': 'Invalid target',
            'valid_targets': list(TARGETS.keys())
        }), 400
    
    # Calculate date range
    from_date = datetime.now() - timedelta(days=days)
    from_date_str = from_date.strftime('%Y-%m-%d')
    
    # Get target configuration
    target_config = TARGETS[target]
    query = ' OR '.join(target_config['keywords'])
    
    # Build NewsAPI request
    url = 'https://newsapi.org/v2/everything'
    params = {
        'q': query,
        'from': from_date_str,
        'sortBy': 'publishedAt',
        'language': 'en',
        'pageSize': 20,
        'apiKey': NEWS_API_KEY
    }
    
    try:
        # Make request to NewsAPI
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        
        if data.get('status') != 'ok':
            return jsonify({
                'error': 'NewsAPI error',
                'message': data.get('message', 'Unknown error')
            }), 500
        
        # Return articles with target configuration
        return jsonify({
            'target': target,
            'days': days,
            'articles': data.get('articles', []),
            'totalResults': data.get('totalResults', 0),
            'escalation_keywords': target_config['escalation'],
            'target_keywords': target_config['keywords']
        })
        
    except requests.exceptions.RequestException as e:
        return jsonify({
            'error': 'Request failed',
            'message': str(e)
        }), 500
    except Exception as e:
        return jsonify({
            'error': 'Server error',
            'message': str(e)
        }), 500


@app.route('/health', methods=['GET'])
def health():
    """Health check for monitoring"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat()
    })


if __name__ == '__main__':
    # For local testing
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
