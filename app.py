from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
from datetime import datetime, timedelta
import os
from collections import Counter
import praw
from telegram_scraper import TelegramScraper
import asyncio

app = Flask(__name__)
CORS(app)

# API Keys from environment variables
NEWSAPI_KEY = os.getenv('NEWSAPI_KEY')
REDDIT_CLIENT_ID = os.getenv('REDDIT_CLIENT_ID')
REDDIT_CLIENT_SECRET = os.getenv('REDDIT_CLIENT_SECRET')
REDDIT_USER_AGENT = os.getenv('REDDIT_USER_AGENT', 'AsifahAnalytics/1.0')

# Initialize Reddit
reddit = praw.Reddit(
    client_id=REDDIT_CLIENT_ID,
    client_secret=REDDIT_CLIENT_SECRET,
    user_agent=REDDIT_USER_AGENT
)

def query_gdelt(target, days_back=7):
    """Query GDELT for recent coverage"""
    try:
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days_back)
        
        query_terms = {
            'hezbollah': 'Hezbollah OR "Hizbullah" OR "Nasrallah"',
            'iran': 'Iran OR "IRGC" OR "Tehran" OR "Iranian"',
            'houthis': 'Houthis OR "Ansar Allah" OR "Yemen" OR "Houthi"'
        }
        
        query = query_terms.get(target, target)
        
        url = "https://api.gdeltproject.org/api/v2/doc/doc"
        params = {
            'query': f'{query} AND (Israel OR IDF OR "Israeli military")',
            'mode': 'artlist',
            'maxrecords': 250,
            'format': 'json',
            'startdatetime': start_date.strftime('%Y%m%d%H%M%S'),
            'enddatetime': end_date.strftime('%Y%m%d%H%M%S')
        }
        
        response = requests.get(url, params=params, timeout=15)
        
        if response.status_code == 200:
            data = response.json()
            articles = data.get('articles', [])
            
            # Analyze escalation keywords
            escalation_keywords = [
                'strike', 'attack', 'military operation', 'raid', 'escalation',
                'retaliation', 'response', 'target', 'assassinate', 'eliminate'
            ]
            
            escalation_count = 0
            for article in articles:
                title = article.get('title', '').lower()
                if any(keyword in title for keyword in escalation_keywords):
                    escalation_count += 1
            
            return {
                'article_count': len(articles),
                'escalation_mentions': escalation_count,
                'top_sources': Counter([a.get('domain', 'unknown') for a in articles[:50]]).most_common(5)
            }
        else:
            return {'error': f'GDELT returned status {response.status_code}'}
            
    except Exception as e:
        return {'error': str(e)}

def query_newsapi(target, days_back=7):
    """Query NewsAPI for recent coverage"""
    try:
        query_terms = {
            'hezbollah': 'Hezbollah AND (Israel OR IDF)',
            'iran': 'Iran AND (Israel OR IDF OR strike)',
            'houthis': 'Houthis AND (Israel OR Red Sea OR strike)'
        }
        
        query = query_terms.get(target, target)
        from_date = (datetime.now() - timedelta(days=days_back)).strftime('%Y-%m-%d')
        
        url = 'https://newsapi.org/v2/everything'
        params = {
            'q': query,
            'from': from_date,
            'sortBy': 'relevancy',
            'language': 'en',
            'pageSize': 100,
            'apiKey': NEWSAPI_KEY
        }
        
        response = requests.get(url, params=params, timeout=15)
        
        if response.status_code == 200:
            data = response.json()
            articles = data.get('articles', [])
            
            # Count threat indicators
            threat_keywords = ['imminent', 'prepare', 'planning', 'soon', 'ready']
            threat_count = sum(1 for a in articles if any(kw in a.get('title', '').lower() for kw in threat_keywords))
            
            return {
                'article_count': len(articles),
                'threat_indicators': threat_count,
                'top_sources': Counter([a.get('source', {}).get('name', 'unknown') for a in articles]).most_common(5)
            }
        else:
            return {'error': f'NewsAPI returned status {response.status_code}'}
            
    except Exception as e:
        return {'error': str(e)}

def scrape_reddit(target):
    """Scrape relevant Reddit communities"""
    try:
        subreddits = {
            'hezbollah': ['ForbiddenBromance', 'Israel', 'lebanon'],
            'iran': ['Israel', 'iran', 'geopolitics'],
            'houthis': ['Israel', 'Yemen', 'geopolitics']
        }
        
        relevant_subs = subreddits.get(target, ['Israel'])
        
        all_posts = []
        escalation_mentions = 0
        
        for sub_name in relevant_subs:
            try:
                subreddit = reddit.subreddit(sub_name)
                
                for post in subreddit.hot(limit=50):
                    title_lower = post.title.lower()
                    
                    # Check if relevant to target
                    target_keywords = {
                        'hezbollah': ['hezbollah', 'hizbullah', 'lebanon', 'nasrallah'],
                        'iran': ['iran', 'iranian', 'irgc', 'tehran'],
                        'houthis': ['houthi', 'yemen', 'red sea', 'ansar allah']
                    }
                    
                    if any(kw in title_lower for kw in target_keywords.get(target, [])):
                        all_posts.append({
                            'title': post.title,
                            'score': post.score,
                            'comments': post.num_comments,
                            'subreddit': sub_name
                        })
                        
                        # Check for escalation language
                        if any(word in title_lower for word in ['strike', 'attack', 'war', 'escalation']):
                            escalation_mentions += 1
                            
            except Exception as e:
                print(f"Error scraping r/{sub_name}: {e}")
                
        return {
            'post_count': len(all_posts),
            'escalation_mentions': escalation_mentions,
            'top_posts': sorted(all_posts, key=lambda x: x['score'], reverse=True)[:10]
        }
        
    except Exception as e:
        return {'error': str(e)}

def scrape_telegram(target):
    """Scrape Telegram channels for intelligence"""
    try:
        # Define channels to monitor per target
        channels = {
            'hezbollah': [
                'almayadeen',
                'manartv',
                'lbcgroup',
            ],
            'iran': [
                'PressTV',
                'IranIntl_En',
            ],
            'houthis': [
                'yemenipress',
            ]
        }
        
        scraper = TelegramScraper()
        
        # Run async scraping
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        messages = loop.run_until_complete(
            scraper.scrape_channels(channels.get(target, []), hours_back=24)
        )
        loop.close()
        
        # Analyze for threats
        analysis = scraper.analyze_for_threats(messages, target)
        
        return {
            'message_count': len(messages),
            'relevant_count': analysis['relevant_count'],
            'threat_score': analysis['threat_score'],
            'top_messages': analysis['top_messages']
        }
        
    except Exception as e:
        return {'error': str(e)}

def calculate_threat_score(gdelt_data, news_data, reddit_data, telegram_data):
    """Calculate overall threat probability"""
    score = 0
    
    # GDELT weighting (35%)
    if 'error' not in gdelt_data:
        article_score = min(gdelt_data.get('article_count', 0) / 10, 20)
        escalation_score = min(gdelt_data.get('escalation_mentions', 0) / 5, 15)
        score += article_score + escalation_score
    
    # NewsAPI weighting (30%)
    if 'error' not in news_data:
        article_score = min(news_data.get('article_count', 0) / 8, 15)
        threat_score = min(news_data.get('threat_indicators', 0) * 3, 15)
        score += article_score + threat_score
    
    # Reddit weighting (20%)
    if 'error' not in reddit_data:
        post_score = min(reddit_data.get('post_count', 0) / 3, 10)
        escalation_score = min(reddit_data.get('escalation_mentions', 0) * 2, 10)
        score += post_score + escalation_score
    
    # Telegram weighting (15%)
    if 'error' not in telegram_data:
        message_score = min(telegram_data.get('relevant_count', 0) / 5, 8)
        threat_score = min(telegram_data.get('threat_score', 0) / 2, 7)
        score += message_score + threat_score
    
    return min(score, 100)

@app.route('/')
def home():
    return jsonify({
        'status': 'Asifah Analytics API is running',
        'version': '2.0',
        'endpoints': ['/api/hezbollah', '/api/iran', '/api/houthis']
    })

@app.route('/api/<target>')
def get_analysis(target):
    """Main endpoint for threat analysis"""
    if target not in ['hezbollah', 'iran', 'houthis']:
        return jsonify({'error': 'Invalid target. Use hezbollah, iran, or houthis'}), 400
    
    # Gather intelligence from all sources
    gdelt_data = query_gdelt(target)
    news_data = query_newsapi(target)
    reddit_data = scrape_reddit(target)
    telegram_data = scrape_telegram(target)
    
    # Calculate threat probability
    threat_probability = calculate_threat_score(gdelt_data, news_data, reddit_data, telegram_data)
    
    return jsonify({
        'target': target,
        'timestamp': datetime.now().isoformat(),
        'threat_probability': round(threat_probability, 1),
        'intelligence': {
            'gdelt': gdelt_data,
            'newsapi': news_data,
            'reddit': reddit_data,
            'telegram': telegram_data
        }
    })

@app.route('/api/telegram/<target>')
def get_telegram_intel(target):
    """Standalone Telegram intelligence endpoint"""
    if target not in ['hezbollah', 'iran', 'houthis']:
        return jsonify({'error': 'Invalid target'}), 400
    
    telegram_data = scrape_telegram(target)
    return jsonify({
        'target': target,
        'timestamp': datetime.now().isoformat(),
        'telegram_intelligence': telegram_data
    })

if __name__ == '__main__':
    app.run(debug=True)
