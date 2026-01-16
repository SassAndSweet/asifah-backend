import anthropic
import os
from datetime import datetime, timedelta
import json
from flask import Flask, render_template, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# Initialize the Anthropic client
client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

# Define threat assessment keywords with severity weights
ESCALATION_KEYWORDS = {
    # Direct military action - highest severity
    "strike": 3.0,
    "attack": 3.0,
    "bombing": 3.0,
    "airstrike": 3.0,
    "missile": 2.5,
    "rocket": 2.5,
    "military operation": 2.5,
    "offensive": 2.5,
    
    # Retaliation and response - high severity
    "retaliate": 2.5,
    "retaliation": 2.5,
    "response": 1.5,
    "counterattack": 2.5,
    
    # Territorial actions - high severity
    "invasion": 3.0,
    "incursion": 2.5,
    
    # Threats and warnings - medium severity
    "threatens": 2.0,
    "warned": 2.0,
    "vowed": 2.0,
    "promised to strike": 2.5,
    "will respond": 2.0,
    "severe response": 2.5,
    "consequences": 1.5,
    
    # Military buildup - medium severity
    "mobilization": 2.0,
    "troops deployed": 2.0,
    "forces gathering": 2.0,
    "military buildup": 2.0,
    "reserves called up": 2.0,
    
    # Casualties - high severity
    "killed": 2.5,
    "dead": 2.5,
    "casualties": 2.5,
    "wounded": 2.0,
    "injured": 2.0,
    "death toll": 2.5,
    "fatalities": 2.5,
    
    # Flight disruptions - medium severity (indicator of escalation)
    "flight cancellations": 2.0,
    "cancelled flights": 2.0,
    "suspend flights": 2.0,
    "suspended flights": 2.0,
    "airline suspends": 2.0,
    "suspended service to": 2.0,
    "halted flights": 2.0,
    "halt flights": 2.0,
    "grounded flights": 2.0,
    "airspace closed": 2.5,
    "no-fly zone": 2.5,
    
    # Travel advisories - medium severity
    "travel advisory": 1.5,
    "do not travel": 2.0,
    "avoid all travel": 2.0,
    "reconsider travel": 1.5,
    
    # Specific airline suspensions - medium severity
    "emirates suspend": 2.0,
    "emirates cancel": 2.0,
    "emirates halt": 2.0,
    "turkish airlines suspend": 2.0,
    "turkish airlines cancel": 2.0,
    "turkish airlines halt": 2.0,
    "lufthansa suspend": 2.0,
    "lufthansa cancel": 2.0,
    "air france suspend": 2.0,
    "air france cancel": 2.0,
    "british airways suspend": 2.0,
    "british airways cancel": 2.0,
    "qatar airways suspend": 2.0,
    "qatar airways cancel": 2.0,
    "etihad suspend": 2.0,
    "etihad cancel": 2.0,
    "klm suspend": 2.0,
    "klm cancel": 2.0,
}

DEESCALATION_KEYWORDS = {
    # Diplomatic efforts - negative weight (reduces threat)
    "ceasefire": -2.0,
    "truce": -2.0,
    "peace talks": -2.0,
    "diplomatic": -1.5,
    "negotiation": -1.5,
    "dialogue": -1.5,
    "agreement": -1.5,
    "de-escalation": -2.0,
    "deescalation": -2.0,
    "calm": -1.5,
    "restraint": -1.5,
    "stand down": -2.0,
    "pullback": -2.0,
    "withdrawal": -1.5,
}

# Target-specific keywords for each threat
TARGET_KEYWORDS = {
    "iran": [
        "iran", "iranian", "tehran", "isfahan", "natanz", 
        "fordow", "irgc", "revolutionary guard", "khamenei",
        "islamic republic", "persian"
    ],
    "hezbollah": [
        "hezbollah", "hizballah", "hizbollah", "nasrallah",
        "lebanon", "lebanese", "beirut", "southern lebanon",
        "dahieh", "dahiyeh", "litani", "shia militia", "shiite militia",
        "bekaa", "tyre", "sidon"
    ],
    "houthis": [
        "houthi", "houthis", "yemen", "yemeni", "ansarallah",
        "ansar allah", "sanaa", "sana'a", "hodeidah", "hodeida",
        "red sea attacks", "saada", "aden"
    ]
}

# Source credibility weights
SOURCE_WEIGHTS = {
    # Tier 1: High credibility international sources
    "Reuters": 1.0,
    "Associated Press": 1.0,
    "BBC News": 1.0,
    "The New York Times": 1.0,
    "The Washington Post": 1.0,
    "The Guardian": 1.0,
    "Financial Times": 1.0,
    "Wall Street Journal": 1.0,
    
    # Tier 2: Credible regional sources
    "Al Jazeera English": 0.9,
    "Haaretz": 0.9,
    "The Times of Israel": 0.85,
    "Al-Monitor": 0.85,
    "Middle East Eye": 0.8,
    
    # Tier 3: Government/Official sources
    "UN News": 0.95,
    "U.S. Department of State": 0.95,
    "Israeli Government": 0.9,
    
    # Tier 4: Other credible sources
    "NPR": 0.9,
    "CNN": 0.85,
    "ABC News": 0.85,
    "NBC News": 0.85,
    "CBS News": 0.85,
    
    # Tier 5: Specialized/regional sources
    "Jerusalem Post": 0.8,
    "I24 News": 0.75,
    "Axios": 0.85,
    "The Atlantic": 0.85,
    "Foreign Policy": 0.85,
    
    # Default weight for unknown sources
    "default": 0.5
}

def calculate_article_score(article, target, hours_old):
    """
    Calculate weighted score for a single article based on:
    1. Keyword presence and severity
    2. Source credibility
    3. Time decay (more recent = higher weight)
    4. Target relevance
    """
    score = 0
    text = f"{article.get('title', '')} {article.get('description', '')} {article.get('content', '')}".lower()
    
    # Check for target relevance
    target_matches = sum(1 for keyword in TARGET_KEYWORDS[target] if keyword in text)
    if target_matches == 0:
        return 0, {}, False  # Article not relevant to target
    
    # Calculate keyword severity score
    matched_keywords = {}
    is_deescalation = False
    
    # Check escalation keywords
    for keyword, severity in ESCALATION_KEYWORDS.items():
        if keyword in text:
            matched_keywords[keyword] = severity
            score += severity
    
    # Check deescalation keywords
    for keyword, severity in DEESCALATION_KEYWORDS.items():
        if keyword in text:
            matched_keywords[keyword] = severity
            score += severity  # These are negative values
            is_deescalation = True
    
    # Apply source weight
    source_name = article.get('source', {}).get('name', 'Unknown')
    source_weight = SOURCE_WEIGHTS.get(source_name, SOURCE_WEIGHTS['default'])
    
    # Apply time decay (exponential decay over 7 days)
    # Recent articles (0-24h) get full weight
    # After 24h, weight decays exponentially
    if hours_old <= 24:
        time_weight = 1.0
    else:
        days_old = hours_old / 24
        time_weight = 0.5 ** (days_old / 7)  # Halves every 7 days
    
    # Calculate final weighted score
    weighted_score = score * source_weight * time_weight
    
    return weighted_score, matched_keywords, is_deescalation

def assess_threat_level(target="iran"):
    """
    Main function to assess threat level for Israeli military action
    Returns probability (0-100), timeline estimate, and supporting data
    """
    
    # Search for recent news about the target
    try:
        # Get English language articles
        search_results_en = search_news(target, language="en")
        
        # Get Hebrew language articles for Israeli perspective
        search_results_he = search_news(target, language="he")
        
        # Get Arabic articles for regional perspective
        search_results_ar = search_news(target, language="ar")
        
        # Get Farsi articles if target is Iran
        search_results_fa = None
        if target == "iran":
            search_results_fa = search_news("iran", language="fa")
        
        # Get Reddit discussions from relevant subreddits
        reddit_results = search_reddit(target)
        
        # Combine all results
        all_articles = []
        
        if search_results_en and 'articles' in search_results_en:
            all_articles.extend(search_results_en['articles'])
        
        if search_results_he and 'articles' in search_results_he:
            all_articles.extend(search_results_he['articles'])
        
        if search_results_ar and 'articles' in search_results_ar:
            all_articles.extend(search_results_ar['articles'])
        
        if search_results_fa and 'articles' in search_results_fa:
            all_articles.extend(search_results_fa['articles'])
        
        if reddit_results:
            all_articles.extend(reddit_results)
        
        if not all_articles:
            return {
                "success": False,
                "error": "No articles found",
                "probability": 0,
                "timeline": "Unknown",
                "confidence": "Low"
            }
        
        # Calculate weighted scores for all articles
        article_scores = []
        total_weighted_score = 0
        recent_article_count = 0  # Articles from last 48 hours
        older_article_count = 0
        deescalation_count = 0
        
        current_time = datetime.now()
        
        for article in all_articles:
            # Calculate hours since publication
            pub_date_str = article.get('publishedAt', '')
            try:
                if 'T' in pub_date_str:
                    pub_date = datetime.fromisoformat(pub_date_str.replace('Z', '+00:00'))
                else:
                    pub_date = datetime.strptime(pub_date_str, '%Y-%m-%d')
                hours_old = (current_time - pub_date.replace(tzinfo=None)).total_seconds() / 3600
            except:
                hours_old = 48  # Default to 48 hours if parsing fails
            
            weighted_score, keywords, is_deescalation = calculate_article_score(article, target, hours_old)
            
            if weighted_score != 0:  # Only include relevant articles
                article_scores.append({
                    'article': article,
                    'weighted_score': weighted_score,
                    'keywords': keywords,
                    'hours_old': hours_old,
                    'is_deescalation': is_deescalation,
                    'source_weight': SOURCE_WEIGHTS.get(
                        article.get('source', {}).get('name', 'Unknown'),
                        SOURCE_WEIGHTS['default']
                    )
                })
                total_weighted_score += weighted_score
                
                if hours_old <= 48:
                    recent_article_count += 1
                else:
                    older_article_count += 1
                
                if is_deescalation:
                    deescalation_count += 1
        
        # Sort articles by weighted score (highest contribution first)
        article_scores.sort(key=lambda x: abs(x['weighted_score']), reverse=True)
        
        # Calculate base probability
        # Start with article count (more coverage = higher probability)
        base_score = min(len(article_scores) * 0.5, 15)  # Cap at 15 points
        
        # Add weighted score component
        # Normalize to 0-85 range (leaving room for other factors)
        normalized_weighted_score = min(total_weighted_score * 2, 85)
        
        # Calculate momentum (recent vs older articles)
        if older_article_count > 0:
            momentum_ratio = recent_article_count / older_article_count
            if momentum_ratio > 1.5:
                momentum = "increasing"
                momentum_multiplier = 1.2
            elif momentum_ratio > 0.8:
                momentum = "stable"
                momentum_multiplier = 1.0
            else:
                momentum = "decreasing"
                momentum_multiplier = 0.8
        else:
            momentum = "increasing"
            momentum_multiplier = 1.2
        
        # Apply momentum multiplier
        final_score = (base_score + normalized_weighted_score) * momentum_multiplier
        
        # Convert to probability (0-100)
        probability = min(int(final_score), 99)  # Cap at 99%
        
        # Determine timeline based on probability and momentum
        if probability >= 80:
            if momentum == "increasing":
                timeline = "0-7 Days (Imminent threat)"
            else:
                timeline = "0-14 Days (Very high threat)"
        elif probability >= 60:
            if momentum == "increasing":
                timeline = "7-14 Days (High threat)"
            else:
                timeline = "14-30 Days (Elevated threat)"
        elif probability >= 40:
            timeline = "0-30 Days (Elevated threat)"
        elif probability >= 20:
            timeline = "30-60 Days (Moderate threat)"
        else:
            timeline = "60+ Days (Low threat)"
        
        # Determine confidence based on article count and source diversity
        unique_sources = len(set(a['article'].get('source', {}).get('name', 'Unknown') 
                                for a in article_scores))
        
        if len(article_scores) >= 20 and unique_sources >= 8:
            confidence = "High"
        elif len(article_scores) >= 10 and unique_sources >= 5:
            confidence = "Medium"
        else:
            confidence = "Low"
        
        # Prepare top contributing articles for display
        top_articles = []
        for item in article_scores[:15]:  # Top 15 articles
            article = item['article']
            contribution_percent = (abs(item['weighted_score']) / max(abs(total_weighted_score), 1)) * 100
            
            top_articles.append({
                'title': article.get('title', 'No title'),
                'source': article.get('source', {}).get('name', 'Unknown'),
                'url': article.get('url', ''),
                'publishedAt': article.get('publishedAt', ''),
                'contribution': round(item['weighted_score'], 2),
                'contribution_percent': round(contribution_percent, 2),
                'severity': max(item['keywords'].values()) if item['keywords'] else 0,
                'source_weight': item['source_weight'],
                'time_decay': 0.5 ** ((item['hours_old'] / 24) / 7) if item['hours_old'] > 24 else 1.0,
                'deescalation': item['is_deescalation']
            })
        
        return {
            "success": True,
            "target": target,
            "probability": probability,
            "timeline": timeline,
            "confidence": confidence,
            "momentum": momentum,
            "total_articles": len(article_scores),
            "recent_articles_48h": recent_article_count,
            "older_articles": older_article_count,
            "deescalation_count": deescalation_count,
            "scoring_breakdown": {
                "base_score": base_score,
                "weighted_score": round(total_weighted_score, 2),
                "momentum_multiplier": momentum_multiplier,
                "article_count": len(article_scores),
                "recent_articles_48h": recent_article_count,
                "older_articles": older_article_count,
                "source_weighting_applied": True,
                "time_decay_applied": True
            },
            "top_scoring_articles": top_articles,
            "escalation_keywords": list(ESCALATION_KEYWORDS.keys()),
            "target_keywords": TARGET_KEYWORDS[target],
            "articles_en": search_results_en.get('articles', []) if search_results_en else [],
            "articles_he": search_results_he.get('articles', []) if search_results_he else [],
            "articles_ar": search_results_ar.get('articles', []) if search_results_ar else [],
            "articles_fa": search_results_fa.get('articles', []) if search_results_fa and target == "iran" else [],
            "articles_reddit": reddit_results if reddit_results else [],
            "totalResults_en": search_results_en.get('totalResults', 0) if search_results_en else 0,
            "totalResults_he": search_results_he.get('totalResults', 0) if search_results_he else 0,
            "totalResults_ar": search_results_ar.get('totalResults', 0) if search_results_ar else 0,
            "totalResults_fa": search_results_fa.get('totalResults', 0) if search_results_fa and target == "iran" else 0,
            "totalResults_reddit": len(reddit_results) if reddit_results else 0,
            "reddit_subreddits": get_target_subreddits(target),
            "rate_limit": search_results_en.get('rate_limit', {}) if search_results_en else {},
            "cached": False,
            "version": "2.0.0"
        }
        
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "probability": 0,
            "timeline": "Unknown",
            "confidence": "Low"
        }

def get_target_subreddits(target):
    """Get relevant subreddits for each target"""
    subreddit_map = {
        "iran": ["Iran", "Iranian", "geopolitics", "worldnews"],
        "hezbollah": ["Lebanon", "Israel", "geopolitics"],
        "houthis": ["Yemen", "Israel", "geopolitics"]
    }
    return subreddit_map.get(target, ["geopolitics"])

def search_news(target, language="en"):
    """
    Search for news articles about the target using Claude with web search
    """
    try:
        # Construct search query based on target and language
        if target == "iran":
            if language == "en":
                query = "Israel Iran military strike attack threat latest news"
            elif language == "he":
                query = "ישראל איران תקיפה צבאית איום חדשות"
            elif language == "ar":
                query = "إسرائيل إيران ضربة عسكرية تهديد أخبار"
            elif language == "fa":
                query = "اسرائیل ایران حمله نظامی تهدید اخبار"
        elif target == "hezbollah":
            if language == "en":
                query = "Israel Hezbollah military strike attack threat latest news"
            elif language == "he":
                query = "ישראל חיזבאללה תקיפה צבאית איום חדשות"
            elif language == "ar":
                query = "إسرائيل حزب الله ضربة عسكرية تهديد أخبار"
        elif target == "houthis":
            if language == "en":
                query = "Israel Houthis Yemen military strike attack threat latest news"
            elif language == "he":
                query = "ישראל חות'ים תימן תקיפה צבאית איום חדשות"
            elif language == "ar":
                query = "إسرائيل الحوثيين اليمن ضربة عسكرية تهديد أخبار"
        
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            tools=[
                {
                    "type": "web_search_20250305",
                    "name": "web_search",
                    "search_tool_version": "2025-03-05"
                }
            ],
            messages=[
                {
                    "role": "user",
                    "content": f"""Search for recent news articles about: {query}

Focus on articles from the last 7 days that discuss:
- Military threats or actions
- Diplomatic tensions
- Strategic developments
- Expert analysis
- Official statements

Return the results as a JSON object with this structure:
{{
    "articles": [
        {{
            "title": "article title",
            "description": "article description",
            "url": "article url",
            "source": {{"name": "source name"}},
            "publishedAt": "ISO date string",
            "content": "article content snippet",
            "language": "{language}"
        }}
    ],
    "totalResults": number
}}

Search for at least 20 articles if available."""
                }
            ]
        )
        
        # Extract the search results from Claude's response
        result_text = ""
        for block in response.content:
            if block.type == "text":
                result_text += block.text
        
        # Try to parse JSON from the response
        try:
            # Find JSON in the response
            start_idx = result_text.find('{')
            end_idx = result_text.rfind('}') + 1
            if start_idx != -1 and end_idx != 0:
                json_str = result_text[start_idx:end_idx]
                results = json.loads(json_str)
                return results
        except json.JSONDecodeError:
            # If JSON parsing fails, return a structured error
            return {
                "articles": [],
                "totalResults": 0,
                "error": "Failed to parse search results"
            }
        
        return {
            "articles": [],
            "totalResults": 0
        }
        
    except Exception as e:
        print(f"Error searching news: {e}")
        return {
            "articles": [],
            "totalResults": 0,
            "error": str(e)
        }

def search_reddit(target):
    """
    Search Reddit for discussions about the target
    """
    try:
        subreddits = get_target_subreddits(target)
        
        # Construct search query
        if target == "iran":
            query = "Israel Iran strike attack military threat"
        elif target == "hezbollah":
            query = "Israel Hezbollah strike attack military threat Lebanon"
        elif target == "houthis":
            query = "Israel Houthis Yemen strike attack military threat"
        
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            tools=[
                {
                    "type": "web_search_20250305",
                    "name": "web_search",
                    "search_tool_version": "2025-03-05"
                }
            ],
            messages=[
                {
                    "role": "user",
                    "content": f"""Search Reddit for recent discussions about: {query}

Focus on subreddits: {', '.join(['r/' + s for s in subreddits])}

Look for posts from the last 7 days that discuss:
- Military developments
- Strategic analysis
- Breaking news
- Expert opinions

Return results as a JSON array of post objects with this structure:
[
    {{
        "title": "post title",
        "url": "post url",
        "source": {{"name": "r/subreddit"}},
        "publishedAt": "ISO date string",
        "content": "post content",
        "reddit_score": score,
        "reddit_comments": comment_count,
        "reddit_upvote_ratio": ratio,
        "language": "en"
    }}
]

Return at least 5-10 relevant posts if available."""
                }
            ]
        )
        
        # Extract the search results
        result_text = ""
        for block in response.content:
            if block.type == "text":
                result_text += block.text
        
        # Try to parse JSON from the response
        try:
            start_idx = result_text.find('[')
            end_idx = result_text.rfind(']') + 1
            if start_idx != -1 and end_idx != 0:
                json_str = result_text[start_idx:end_idx]
                results = json.loads(json_str)
                return results
        except json.JSONDecodeError:
            return []
        
        return []
        
    except Exception as e:
        print(f"Error searching Reddit: {e}")
        return []

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/threat/<target>')
def get_threat(target):
    """API endpoint to get threat assessment for a specific target"""
    if target not in TARGET_KEYWORDS:
        return jsonify({
            "success": False,
            "error": f"Invalid target. Must be one of: {', '.join(TARGET_KEYWORDS.keys())}"
        }), 400
    
    result = assess_threat_level(target)
    return jsonify(result)

@app.route('/api/threat/all')
def get_all_threats():
    """API endpoint to get threat assessments for all targets"""
    results = {}
    for target in TARGET_KEYWORDS.keys():
        results[target] = assess_threat_level(target)
    return jsonify(results)

if __name__ == '__main__':
    # Test the threat assessment
    print("Testing Iran threat assessment...")
    result = assess_threat_level("iran")
    print(json.dumps(result, indent=2))
    
    print("\n" + "="*80 + "\n")
    print("Testing Hezbollah threat assessment...")
    result = assess_threat_level("hezbollah")
    print(json.dumps(result, indent=2))
    
    print("\n" + "="*80 + "\n")
    print("Testing Houthis threat assessment...")
    result = assess_threat_level("houthis")
    print(json.dumps(result, indent=2))
    
    # Start Flask server
    app.run(debug=True, port=5000)
