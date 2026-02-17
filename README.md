# Asifah Analytics - Backend

**Flask API Server for OSINT Threat Monitoring Platform**

© 2025–2026 RCGG. All Rights Reserved.

---

## Overview

This is the backend API server for Asifah Analytics, providing RESTful endpoints for aggregating and analyzing open-source intelligence data from multiple sources including NewsAPI, GDELT Event Database, Reddit, Polymarket, Iran Wire, HRANA, Syria Direct, and SOHR. The server handles threat probability calculations with weighted scoring, time decay, momentum analysis, and source credibility factors.

**Frontend Repository:** [asifah-analytics](https://github.com/SassAndSweet/asifah-analytics)
**Live Dashboard:** [asifahanalytics.com](https://asifahanalytics.com)

---

## Technical Stack

- **Framework**: Flask (Python 3.11+)
- **Hosting**: Render.com (free tier with auto-sleep)
- **API Format**: RESTful JSON responses
- **CORS**: Configured for cross-origin requests from asifahanalytics.com and GitHub Pages
- **Caching**: Server-side response caching with `?refresh=true` override for on-demand scans
- **Dependencies**: requests, flask-cors, feedparser, praw (Reddit API)

---

## API Endpoints

### Threat Probability Scores (Primary)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/iran-strike-probability` | GET | Iran threat score — returns cached result; add `?refresh=true&days=N` for fresh scan |
| `/api/hezbollah-activity` | GET | Hezbollah/Lebanon threat score — same cache/refresh pattern |
| `/api/houthis-threat` | GET | Houthis/Yemen threat score — same cache/refresh pattern |
| `/api/syria-conflict` | GET | Syria conflict threat score — same cache/refresh pattern |

**Response format (all four):**
```json
{
  "success": true,
  "probability": 42,
  "timeline": "91-180 days",
  "recent_headlines": [...],
  "cached": true,
  "last_updated": "2026-02-16T12:00:00Z"
}
```

**Query parameters:**
- `refresh=true` — Force fresh scan (triggered by "Scan OSINT" button clicks)
- `days=N` — Time window: 1 (24h), 2 (48h), 7 (default), 30

### Multi-Actor Threat Matrix

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/threat-matrix/{target}` | GET | Combined incoming/outgoing threat analysis for a target |

**Targets:** `iran`, `hezbollah`, `houthis`, `syria`

**Response includes:**
- `combined_probability` — Aggregated score with coordination bonus
- `incoming_threats.israel` — Israel strike probability against target
- `incoming_threats.us` — US strike probability against target
- `outgoing_threats.vs_israel` — Target's threat to Israel
- `outgoing_threats.vs_us` — Target's threat to US (where applicable)
- Risk level badges: `low`, `moderate`, `high`, `very_high`

### Prediction Markets

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/polymarket` | GET | Crowdsourced prediction market probabilities for Middle East conflict scenarios |

### Aviation Monitoring

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/flight-cancellations` | GET | Airline suspensions/cancellations to conflict-zone airports |
| `/api/notams` | GET | Active NOTAMs (Notices to Air Missions) for Middle East airspace |

### Iran Protest Monitoring

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/scan-iran-protests` | GET | Protest intensity score, HRANA casualty data (deaths/arrests/injuries), regime stability calculation |

### Country-Specific Article Feeds

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/scan-yemen` | GET | Yemen/Houthi/Red Sea article aggregation |
| `/scan-osint` | GET | General OSINT article scan — accepts `?q=` query parameter |

### System

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/rate-limit` | GET | NewsAPI quota status (requests used, remaining, reset timer) |

---

## Threat Scoring Algorithm

### Three-Factor Weighted Model

1. **Volume Score** (max 40 points)
   - Raw article count from NewsAPI
   - Formula: `min(article_count × 2, 40)`
   - Recency bias: articles within 24h weighted higher

2. **Escalation Score** (max 40 points)
   - Military action keyword frequency with phrase-level weighting
   - Formula: `min(escalation_keyword_count × 3, 40)`
   - High-weight phrases: "military strike," "nuclear facility," "ballistic missile"

3. **Mention Score** (max 20 points)
   - Target-specific terminology density
   - Formula: `min(target_mention_count, 20)`

### Additional Scoring Factors
- **Time decay**: Recent articles weighted exponentially higher than older ones
- **Source credibility**: Official sources (Reuters, AP, government) weighted above aggregators
- **Momentum analysis**: Compares current window to prior period for trend detection
- **Coordination bonuses**: Multi-front activity across targets increases combined scores

**Final Probability** = (Volume + Escalation + Mention) × modifiers, capped at 99%

### Headline Weighting

Each returned headline includes:
- `weight` — Calculated impact score
- `threat_type` — Classification (Israel Strike, US Strike, Reverse Threat)
- `phrase` — Triggering escalation phrase (if any)
- `why_included` — Human-readable explanation of scoring rationale

---

## Data Sources & Integration

### NewsAPI
- 75,000+ global news outlets
- Rate limited: 100 requests/day (free tier)
- Quota tracked and exposed via `/rate-limit` endpoint

### GDELT Event Database
- Structured conflict events with geolocation
- Goldstein Scale intensity scoring
- Multilingual: Arabic, Hebrew, Farsi content
- 15-minute update cycle

### Reddit OSINT
- Subreddits: r/ForbiddenBromance, r/Israel, r/Lebanon, r/OSINT, r/Yemen, r/geopolitics
- Filters for high-engagement posts (>50 upvotes)
- Comment thread sentiment analysis

### RSS Feeds
- **Iran Wire**: Persian-language dissident journalism
- **HRANA**: Human Rights Activists News Agency — casualty tracking with structured death/arrest/injury counts
- **Syria Direct**: English and Arabic Syria conflict reporting
- **SOHR**: Syrian Observatory for Human Rights

### Polymarket
- Prediction market API for Middle East conflict contracts
- Crowd-sourced probability data (supplementary, not intelligence assessment)

---

## Deployment

**Hosting Platform:** Render.com
**Deployment Method:** Git push to main branch triggers automatic deployment
**Auto-Sleep:** Backend sleeps after 15 minutes of inactivity (free tier)
**Wake Time:** ~30–60 seconds on first request after sleep
**Caching:** Server-side caching of threat scores; frontend loads cached data on page load, fresh scans triggered by user button clicks with `?refresh=true`

---

## Environment Variables

Required API keys (set in Render.com dashboard):

| Variable | Service | Notes |
|----------|---------|-------|
| `NEWSAPI_KEY` | NewsAPI.org | Free tier: 100 requests/day |
| `REDDIT_CLIENT_ID` | Reddit API | OAuth client ID |
| `REDDIT_CLIENT_SECRET` | Reddit API | OAuth client secret |
| `REDDIT_USER_AGENT` | Reddit API | e.g., `AsifahAnalytics/2.0` |

---

## Related Services

| Service | Repository | Purpose |
|---------|-----------|---------|
| **Frontend** | `asifah-analytics` | GitHub Pages — landing page + country stability pages |
| **Lebanon Backend** | `lebanon-stability-backend` | Separate Flask server for Lebanon-specific indicators (currency, bonds, gold reserves, Hezbollah activity) hosted on Render.com |

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
export REDDIT_USER_AGENT="AsifahAnalytics/2.0"

# Run development server
python app.py
```

Server runs on `http://localhost:5000`

---

## Recent Enhancements

### February 2026
- ✅ API endpoint restructuring: new `/api/{target}` pattern replacing legacy `/scan-*` routes
- ✅ Server-side caching with `?refresh=true` override for on-demand scans
- ✅ Iran protest endpoint debugging (regime stability calculations, casualty fallback logic, HRANA data parsing)
- ✅ Yemen article feed endpoint (`/scan-yemen`)
- ✅ Headline weighting system with `weight`, `threat_type`, `phrase`, and `why_included` fields

### January–February 2026
- ✅ Multi-Actor Threat Matrix endpoint (`/api/threat-matrix/{target}`)
- ✅ NOTAMs endpoint (`/api/notams`)
- ✅ Flight cancellations endpoint (`/flight-cancellations`)
- ✅ Polymarket proxy endpoint (`/api/polymarket`)
- ✅ Enhanced scoring algorithms with time decay, momentum analysis, and source credibility weighting
- ✅ Coordination bonus calculations for multi-front threat scenarios
- ✅ Rate limit tracking endpoint (`/rate-limit`)

### December 2025 – January 2026
- ✅ GDELT event database integration with Goldstein Scale scoring
- ✅ Reddit OSINT scraping with engagement filtering
- ✅ Iran Wire + HRANA RSS feed integration
- ✅ CORS configuration for asifahanalytics.com custom domain
- ✅ Initial threat probability calculation engine

### Planned Features
- ⬜ Telegram channel scraping (including Yair Altman's Hebrew channel)
- ⬜ Advanced NLP/sentiment analysis
- ⬜ Historical baseline storage for anomaly detection
- ⬜ Machine learning-based prediction models
- ⬜ Automated scheduled scans (vs. manual-only)

---

## Security & Privacy

- **No data storage**: All processing is ephemeral; no user data stored; cached threat scores are in-memory only
- **Rate limiting**: NewsAPI free tier (100 requests/day), tracked via dashboard
- **CORS restrictions**: Only accepts requests from asifahanalytics.com and GitHub Pages origins
- **Environment variables**: API keys secured in Render.com dashboard, never committed to code
- **Private repository**: Source code not publicly accessible
- **No PII**: Backend processes only public news data and open-source intelligence feeds

---

## Intellectual Property

### Copyright Notice

**© 2025–2026 RCGG. All Rights Reserved.**

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
**Primary Use Case:** Middle East regional threat analysis
**Development Timeline:** December 2025 – Present
**Current Version:** 3.0 (February 2026)
**Repository:** Private (GitHub)

For technical support, feature requests, or analytical feedback:
- **Email:** [asifahanalytics@gmail.com](mailto:asifahanalytics@gmail.com)
- **Instagram:** [@asifahanalytics](https://instagram.com/asifahanalytics)
- **Support:** [Buy Me a Coffee](https://buymeacoffee.com/asifahanalytics)

---

*Last Updated: February 16, 2026*
