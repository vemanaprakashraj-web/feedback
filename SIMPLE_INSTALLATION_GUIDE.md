# SocialPulse AI — Simple Installation Guide

This guide installs the backend and frontend separately.

## 1. Requirements

Install:

- Python 3.11 or newer
- Node.js 20 or newer
- npm

Check versions:

```powershell
python --version
node --version
npm --version
```

## 2. Open the project

```powershell
cd "C:\Users\veman\OneDrive\文档\Default Project"
```

If the project folder is copied somewhere else, use that folder instead.

## 3. Start the backend

Open PowerShell window 1:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
uvicorn app.main:app --reload --port 8000
```

Keep this window running.

The backend will be available at:

```text
http://localhost:8000
```

API documentation:

```text
http://localhost:8000/docs
```

## 4. Start the frontend

Open PowerShell window 2:

```powershell
cd frontend
Copy-Item .env.example .env
npm install
npm run dev
```

Open the URL printed by Vite, normally:

```text
http://localhost:5173
```

## 5. First test without API keys

Use the **Demo dataset** button on the home page. Demo records are synthetic and are visibly labelled as demo data.

You can also upload a CSV or Excel file. The backend creates `backend/data/socialpulse.db` automatically on first use.

## 6. Supported platforms

The application supports exactly three social platforms:

```text
YouTube
Instagram
Facebook
```

Pasting a link shows a preview card automatically:

- **YouTube** — the official video thumbnail appears immediately, no API key needed.
- **Instagram / Facebook** — the post photo appears when Meta Graph API credentials are configured. Without them, the card clearly says which values are missing instead of showing a fake image.

## 7. Enable YouTube data collection

Edit `backend/.env`:

```env
YOUTUBE_API_KEY=your_real_key
```

Get a key from Google Cloud Console by enabling the **YouTube Data API v3**. Restart the backend after editing `.env`.

## 8. Enable Instagram/Facebook previews

For official Instagram/Facebook link previews, create a Meta app and configure:

```env
META_ACCESS_TOKEN=your_long_lived_token
META_API_VERSION=v21.0
INSTAGRAM_ACCOUNT_ID=your_professional_instagram_account_id
FACEBOOK_PAGE_ID=your_facebook_page_id
```

The Instagram account must be a professional (Business or Creator) account connected to a Facebook Page. Request only the Meta permissions required by your app. The application does not scrape personal profiles or use unofficial endpoints.

Restart the backend after editing `.env`.

## 9. Export results

Open a completed analysis and use the CSV, Excel, or PDF buttons. Downloads contain the complete stored record data, not only summary metrics.

## 10. Stop the application

In both PowerShell windows, press:

```text
Ctrl + C
```

## Common problems

### API unavailable

Make sure the backend window is running and open:

```text
http://localhost:8000/api/health
```

### CORS error

Add the frontend origin to `backend/.env`:

```env
CORS_ORIGINS=http://localhost:5173
```

Restart the backend.

### Port already in use

Close the old process or start the frontend on another port:

```powershell
npm run dev -- --port 5174
```

Then set `VITE_API_URL` in `frontend/.env` if needed.

### Missing Python packages

Reactivate the backend environment:

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Important

Do not commit or share a real `.env` file. Do not use scraping or unofficial endpoints for platforms that require official API access.
