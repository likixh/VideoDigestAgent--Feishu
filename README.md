# YouTube Stock Analysis Summarizer

Automatically monitors a YouTube channel (default: [@RhinoFinance](https://www.youtube.com/@RhinoFinance)) for new videos, extracts transcripts, generates detailed stock analysis summaries using Google Gemini AI (free!), and emails them to you.

## What You Get

Each email summary includes:
- **Overall Market Sentiment** — the creator's general market outlook
- **Stock Tickers Mentioned** — every ticker discussed with a one-liner
- **Detailed Stock Analysis** — bull/bear thesis, price targets, key takeaways per stock
- **Other Key Information** — macro data, sector trends, catalysts
- **TL;DR** — 3-5 sentence executive summary

## Setup

### 1. Get API Keys

#### YouTube Data API v3 Key
1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (or select an existing one)
3. Go to **APIs & Services > Library**
4. Search for **"YouTube Data API v3"** and click **Enable**
5. Go to **APIs & Services > Credentials**
6. Click **Create Credentials > API Key**
7. Copy the key — this is your `YOUTUBE_API_KEY`
8. (Recommended) Click **Restrict Key** and limit it to YouTube Data API v3 only

#### Google Gemini API Key (free)
1. Go to [Google AI Studio](https://aistudio.google.com/apikey)
2. Sign in with your Google account
3. Click **Create API Key**
4. Copy the key — this is your `GEMINI_API_KEY`
5. That's it — the free tier gives you 15 requests/minute, more than enough

#### Gmail App Password (for sending emails)
1. Go to [Google Account Security](https://myaccount.google.com/security)
2. Enable **2-Step Verification** if not already on
3. Go to [App Passwords](https://myaccount.google.com/apppasswords)
4. Select **Mail** and your device, then click **Generate**
5. Copy the 16-character password — this is your `SENDER_PASSWORD`

> **Note:** If you don't use Gmail, update `SMTP_SERVER` and `SMTP_PORT` for your provider (e.g., Outlook: `smtp.office365.com:587`).

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure Environment

```bash
cp .env.example .env
```

Edit `.env` with your actual values:

```env
YOUTUBE_API_KEY=AIza...
GEMINI_API_KEY=AIza...
YOUTUBE_CHANNEL_HANDLE=RhinoFinance
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SENDER_EMAIL=you@gmail.com
SENDER_PASSWORD=abcd efgh ijkl mnop
RECIPIENT_EMAIL=you@gmail.com
POLL_INTERVAL=3600
```

## Usage

### Check once for new videos
```bash
python main.py
```

### Run continuously (checks every hour)
```bash
python main.py --poll
```

### Test with a specific video
```bash
python main.py --video dQw4w9WgXcQ
```

## Run as a Background Service (Optional)

### Using systemd (Linux)

Create `/etc/systemd/system/yt-summarizer.service`:

```ini
[Unit]
Description=YouTube Stock Analysis Summarizer
After=network.target

[Service]
Type=simple
WorkingDirectory=/path/to/youtubersummary
ExecStart=/usr/bin/python3 main.py --poll
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
```

Then:
```bash
sudo systemctl enable yt-summarizer
sudo systemctl start yt-summarizer
```

### Using cron (simpler alternative)

```bash
crontab -e
```

Add this line to check every hour:
```
0 * * * * cd /path/to/youtubersummary && /usr/bin/python3 main.py >> /var/log/yt-summarizer.log 2>&1
```

## Architecture

```
main.py                  — Orchestrator: CLI + polling loop
youtube_monitor.py       — Detects new uploads via YouTube Data API
transcript_extractor.py  — Extracts video captions via youtube-transcript-api
summarizer.py            — Sends transcript to Gemini for structured analysis
emailer.py               — Formats and sends summary email via SMTP
config.py                — Loads settings from .env
```

## Cost Estimate

- **YouTube Data API**: Free tier gives 10,000 units/day. Each poll uses ~4 units, so ~2,500 checks/day (way more than needed).
- **Gemini API**: Free! The free tier is more than sufficient for this use case.
- **Email**: Free via Gmail SMTP.
