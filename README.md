# Asifah Analytics - Backend

**Flask API Server for OSINT Threat Monitoring Platform**

© 2025 RCGG. All Rights Reserved.

---

## Overview

This is the backend API server for Asifah Analytics, providing RESTful endpoints for aggregating and analyzing open-source intelligence data from multiple sources including NewsAPI, GDELT Event Database, Reddit, Iran Wire, and HRANA.

**Frontend Repository:** [asifah-analytics](https://github.com/SassAndSweet/asifah-analytics)  
**Live Dashboard:** https://asifahanalytics.com

---

## Technical Stack

- **Framework**: Flask (Python 3.11+)
- **Hosting**: Render.com (free tier with auto-sleep)
- **API Format**: RESTful JSON responses
- **CORS**: Configured for cross-origin requests from asifahanalytics.com
- **Dependencies**: requests, flask-cors, feedparser, praw (Reddit API)

---

## API Endpoints

### News Analysis
- `GET /api/news/<target>` - Fetch and analyze news for specified target (hezbollah, iran, houthis)
- Query parameters: `days` (24h, 48h, 7d, 30d)

### GDELT Events
- `GET /api/gdelt/<target>` - Query GDELT Event Database for conflict events
- Returns structured event data with Goldstein Scale intensity scoring

### Reddit OSINT
- `GET /api/reddit` - Scrape relevant subreddits for street-level sentiment
- Filters for high-engagement posts and analyzes comment threads

### Iranian Protest Monitoring
- `GET /api/protests` - Aggregate Iran Wire and HRANA RSS feeds
- Returns casualty counts and geographic distribution

---

## Deployment

**Hosting Platform:** Render.com  
**Deployment Method:** Git push to main branch triggers automatic deployment  
**Auto-Sleep:** Backend sleeps after 15 minutes of inactivity (free tier)  
**Wake Time:** ~30 seconds on first request after sleep

---

## Environment Variables

Required API keys (set in Render.com dashboard):
- `NEWSAPI_KEY` - NewsAPI.org API key
- `REDDIT_CLIENT_ID` - Reddit API client ID
- `REDDIT_CLIENT_SECRET` - Reddit API secret
- `REDDIT_USER_AGENT` - Reddit API user agent

---

## Local Development
```bash
# Clone repository
git clone https://github.com/SassAndSweet/asifah-backend.git
cd asifah-backend

# Install dependencies
pip install -r requirements.txt

# Set environment variables
export NEWSAPI_KEY="your_key_here"
export REDDIT_CLIENT_ID="your_client_id"
export REDDIT_CLIENT_SECRET="your_secret"
export REDDIT_USER_AGENT="AsifahAnalytics/1.0"

# Run development server
python app.py
```

Server runs on `http://localhost:5000`

---

## Security & Privacy

- **No data storage**: All processing is ephemeral, no user data stored
- **Rate limiting**: NewsAPI free tier (100 requests/day)
- **CORS restrictions**: Only accepts requests from asifahanalytics.com domain
- **Environment variables**: API keys secured in Render.com dashboard
- **Private repository**: Source code not publicly accessible

---

## Intellectual Property

### Copyright Notice

**© 2025 RCGG. All Rights Reserved.**

This software, including all source code, algorithms, API endpoints, and associated materials, is the exclusive intellectual property of RCGG.

### Proprietary License

**ALL RIGHTS RESERVED.** This is proprietary software.

**Unauthorized use, reproduction, distribution, modification, or commercial exploitation of this software in whole or in part is strictly prohibited without prior written consent from the copyright holder.**

Specifically prohibited without authorization:
- Copying or reproducing the source code
- Modifying or creating derivative works
- Distributing, publishing, or sublicensing the software
- Using the software for commercial purposes
- Reverse engineering or decompiling the code
- Hosting unauthorized instances of this API
- Incorporating the software into other products or services

**Internal Use Authorization:** This software is authorized for use by the copyright holder and designated individuals within the U.S. Department of State for unclassified analytical purposes only.

**For licensing inquiries or permission requests, contact the copyright holder.**

---

## Development & Maintenance

**Developer:** RCGG  
**Development Timeline:** December 2025 - Present  
**Current Version:** 2.0 (January 2026)  
**Repository:** Private (GitHub)

For technical support or feature requests, contact the repository owner.

---

*Last Updated: January 24, 2026*
