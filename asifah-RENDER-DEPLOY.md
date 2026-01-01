# Asifah Analytics - Render.com Deployment Guide

Complete step-by-step instructions for deploying your Flask backend to Render.com (free tier).

---

## Overview

You'll create TWO separate GitHub repositories:
1. **Frontend repo** (already done!) - `asifah-analytics` with the dashboard
2. **Backend repo** (new!) - `asifah-backend` with the Flask server

The backend handles NewsAPI requests and returns data to your dashboard.

---

## Part 1: Create Backend GitHub Repository

### Step 1: Create New Repository

1. Go to [GitHub.com](https://github.com) and log in
2. Click the **+** button (top right) → **New repository**
3. Configure:
   - **Name**: `asifah-backend`
   - **Description**: "Flask backend for Asifah Analytics OSINT dashboard"
   - **Visibility**: **Private** (recommended)
   - **Initialize**: Leave ALL checkboxes UNCHECKED
4. Click **Create repository**

### Step 2: Upload Backend Files

1. On your new repository page, click **uploading an existing file**
2. Download these 2 files from this conversation:
   - `asifah-backend-app.py` → **Rename to `app.py`** (important!)
   - `asifah-backend-requirements.txt` → **Rename to `requirements.txt`**
3. Drag and drop BOTH files into the upload box
4. **CRITICAL**: Make sure files are named exactly:
   - `app.py` (NOT asifah-backend-app.py)
   - `requirements.txt` (NOT asifah-backend-requirements.txt)
5. Add commit message: "Initial commit - Flask backend for Asifah Analytics"
6. Click **Commit changes**

### Step 3: Verify Files

Your `asifah-backend` repository should now show:
- ✅ `app.py`
- ✅ `requirements.txt`

---

## Part 2: Deploy to Render.com

### Step 1: Create New Web Service

1. Go to [dashboard.render.com](https://dashboard.render.com/)
2. You should already be logged in and connected to GitHub
3. Click **New +** button (top right)
4. Select **Web Service**

### Step 2: Connect Repository

1. Render will show your GitHub repositories
2. Find **`asifah-backend`** in the list
3. Click **Connect**

(If you don't see it, click "Configure account" and make sure Render has access to all repos or specifically to asifah-backend)

### Step 3: Configure Web Service

Fill out the form:

**Basic Settings:**
- **Name**: `asifah-backend` (or whatever you prefer)
- **Region**: Select closest to you (e.g., `Oregon (US West)`)
- **Branch**: `main`
- **Root Directory**: Leave blank
- **Runtime**: **Python 3**

**Build Settings:**
- **Build Command**: `pip install -r requirements.txt`
- **Start Command**: `gunicorn app:app`

**Instance Type:**
- Select **Free** ($0/month)

### Step 4: Deploy!

1. Scroll to bottom
2. Click **Create Web Service**
3. Render will now:
   - Pull your code from GitHub
   - Install dependencies
   - Start your Flask server
   - This takes 2-3 minutes

### Step 5: Wait for Deployment

You'll see a deployment log in real-time. Watch for:
```
==> Installing dependencies
==> Building...
==> Starting service...
Your service is live 🎉
```

### Step 6: Get Your Backend URL

Once deployed, at the top of the page you'll see your live URL:

```
https://asifah-backend.onrender.com
```

**Copy this URL!** You'll need it in Part 3.

---

## Part 3: Update Frontend to Use Backend

### Step 1: Edit Your Dashboard

1. Go to your **`asifah-analytics`** repository (the frontend one)
2. Download the **`asifah-updated-index.html`** file from this conversation
3. Open it in a text editor
4. Find line 420 (near the top of the `<script>` section):

```javascript
const BACKEND_URL = 'https://YOUR-APP-NAME.onrender.com';
```

5. Replace with your actual Render URL:

```javascript
const BACKEND_URL = 'https://asifah-backend.onrender.com';
```

(Or whatever your actual Render URL is - copy it exactly!)

6. **Save** the file

### Step 2: Upload to GitHub

1. Go to your **`asifah-analytics`** repository
2. Click on the existing `index.html` file
3. Click the pencil icon (Edit)
4. **Select all** the old code (Ctrl+A)
5. **Delete** it all
6. **Copy** all the code from your updated file
7. **Paste** into GitHub
8. Scroll down, add commit message: "Connect to Render backend"
9. Click **Commit changes**

### Step 3: Wait for GitHub Pages

GitHub Pages will automatically rebuild (2-3 minutes)

---

## Part 4: Test Your Dashboard!

### Step 1: Open Dashboard

Go to: `https://sassandsweet.github.io/asifah-analytics/`

### Step 2: Click SCAN

1. Select a time window (try "7 Days")
2. Click **SCAN OSINT SOURCES**
3. You should see:
   - Loading spinner
   - Probabilities updating (with actual percentages!)
   - Headlines appearing
   - Timestamp at bottom

### Step 3: Verify It's Working

Check that:
- ✅ Hezbollah shows a percentage (not "--")
- ✅ Iran shows a percentage
- ✅ Houthis shows a percentage
- ✅ Headlines appear with clickable links
- ✅ Timestamp updates
- ✅ No error messages

**If this works - YOU'RE DONE! 🎉**

---

## Troubleshooting

### Problem: "Error scanning news sources"

**Solution 1: Check Backend Status**
1. Go to your Render dashboard
2. Click on `asifah-backend` service
3. Make sure it shows **"Live"** (green dot)
4. If it says "Deploying" wait a few more minutes

**Solution 2: Check Backend URL**
1. Make sure URL in frontend matches Render URL exactly
2. Should be: `https://asifah-backend.onrender.com` (or your actual URL)
3. NO trailing slash
4. Must start with `https://`

**Solution 3: Test Backend Directly**
1. Open a new browser tab
2. Go to: `https://asifah-backend.onrender.com/`
3. You should see a JSON response:
```json
{
  "status": "online",
  "service": "Asifah Analytics Backend"
}
```
4. If you see this, backend is working!

### Problem: Backend shows "Build failed"

**Check these:**
1. Files are named exactly `app.py` and `requirements.txt`
2. Both files are in the root of the repository (not in a folder)
3. No typos in file contents

### Problem: Free tier sleeping

**What's happening:**
Render free tier "spins down" after 15 minutes of inactivity. First request after sleeping takes ~30 seconds to wake up.

**Solutions:**
1. **Just wait**: First scan after sleep takes 30 sec, then fast
2. **Upgrade to $7/month**: Stays awake 24/7
3. **Use a ping service**: Free services like UptimeRobot ping your backend every 5 minutes to keep it awake

---

## Maintenance & Updates

### Update Backend Code

1. Edit files in `asifah-backend` repo
2. Commit changes
3. Render automatically redeploys (2-3 minutes)

### Update Frontend

1. Edit `index.html` in `asifah-analytics` repo
2. Commit changes  
3. GitHub Pages rebuilds (2-3 minutes)

### Check Backend Logs

1. Go to Render dashboard
2. Click on `asifah-backend`
3. Click "Logs" tab
4. See real-time request logs

---

## Security Notes

### Your API Key is Safe! ✅

- API key lives ONLY in `app.py` on Render's servers
- Never exposed to browser or users
- Not visible in GitHub (it's hardcoded in app.py in a private repo)
- Colleagues can use dashboard without seeing the key

### Rate Limits

NewsAPI free tier:
- 100 requests per day
- Each scan = 3 requests (one per target)
- You can scan ~33 times per day
- Resets at midnight UTC

---

## Sharing with Colleagues

### Frontend Only (Dashboard)

Share: `https://sassandsweet.github.io/asifah-analytics/`

- Anyone can access
- They just see the dashboard
- Backend is invisible to them

### GitHub Access (If Needed)

If colleagues want to see code:

1. Settings → Collaborators
2. Add their GitHub username
3. They can view (or edit if you grant access)

---

## Cost Breakdown

**Current Setup (Free Forever):**
- Render.com: $0/month (free tier)
- GitHub Pages: $0/month (free)
- NewsAPI: $0/month (free tier, 100 req/day)
- **Total: $0/month**

**If You Outgrow Free Tier:**
- Render Starter: $7/month (always awake, faster)
- NewsAPI Pro: $449/month (unlimited requests)
- GitHub: Still free

For State Dept use, the free tier is probably fine!

---

## Quick Reference Commands

**Test backend directly:**
```
https://YOUR-BACKEND.onrender.com/
```

**Test specific scan:**
```
https://YOUR-BACKEND.onrender.com/scan?target=hezbollah&days=7
```

**Frontend URL:**
```
https://sassandsweet.github.io/asifah-analytics/
```

---

## Summary Checklist

**Backend Setup:**
- [ ] Created `asifah-backend` GitHub repo
- [ ] Uploaded `app.py` and `requirements.txt`
- [ ] Created Web Service on Render
- [ ] Configured Python 3, gunicorn
- [ ] Deployment shows "Live"
- [ ] Copied backend URL

**Frontend Update:**
- [ ] Downloaded updated index.html
- [ ] Changed BACKEND_URL to Render URL
- [ ] Uploaded to GitHub `asifah-analytics` repo
- [ ] Waited for GitHub Pages rebuild

**Testing:**
- [ ] Dashboard loads
- [ ] Scan button works
- [ ] Probabilities update
- [ ] Headlines appear
- [ ] No errors

---

**You're all set! Welcome to cloud-deployed OSINT analysis! 🚀**

*Last Updated: January 1, 2026*
