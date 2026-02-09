# YouTube Stock Analysis Summarizer

Automatically monitors YouTube channels for new videos, extracts transcripts, generates detailed stock analysis summaries using your choice of LLM (Gemini, OpenAI, or Claude), and emails them to you.

## What You Get

Each email summary includes:
- **Overall Market Sentiment** — the creator's general market outlook
- **Stock Tickers Mentioned** — every ticker discussed with a one-liner
- **Detailed Stock Analysis** — bull/bear thesis, price targets, key takeaways per stock
- **Other Key Information** — macro data, sector trends, catalysts
- **TL;DR** — 3-5 sentence executive summary

## Setup

### 1. Get API Keys

#### YouTube Data API v3 Key (required)
1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (or select an existing one)
3. Go to **APIs & Services > Library**
4. Search for **"YouTube Data API v3"** and click **Enable**
5. Go to **APIs & Services > Credentials**
6. Click **Create Credentials > API Key**
7. Copy the key — this is your `YOUTUBE_API_KEY`
8. (Recommended) Click **Restrict Key** and limit it to YouTube Data API v3 only

#### LLM API Key (choose one)

| Provider | Cost | How to get the key |
|----------|------|--------------------|
| **Gemini** (recommended) | Free tier | Go to [Google AI Studio](https://aistudio.google.com/apikey) → Create API Key |
| **OpenAI** | ~$0.01-0.05/video | Go to [OpenAI Platform](https://platform.openai.com/api-keys) → Create new secret key |
| **Anthropic (Claude)** | ~$0.01-0.03/video | Go to [Anthropic Console](https://console.anthropic.com/) → API Keys → Create Key |

#### Gmail App Password (for sending emails)
1. Go to [Google Account Security](https://myaccount.google.com/security)
2. Enable **2-Step Verification** if not already on
3. Go to [App Passwords](https://myaccount.google.com/apppasswords)
4. Select **Mail** and your device, then click **Generate**
5. Copy the 16-character password — this is your `SENDER_PASSWORD`

> **Note:** If you don't use Gmail, update `SMTP_SERVER` and `SMTP_PORT` for your provider (e.g., Outlook: `smtp.office365.com:587`).

### 2. Install Dependencies

```bash
pip3 install -r requirements.txt
```

### 3. Configure Environment

```bash
cp .env.example .env
```

Edit `.env` with your actual values:

```env
# Add your YouTube channels (comma-separated, without @)
YOUTUBE_CHANNELS=RhinoFinance,MeetKevin,StockMoe

YOUTUBE_API_KEY=AIza...

# Pick your LLM: gemini, openai, or anthropic
LLM_PROVIDER=gemini
GEMINI_API_KEY=AIza...

# Email
SENDER_EMAIL=you@gmail.com
SENDER_PASSWORD=abcd efgh ijkl mnop
RECIPIENT_EMAIL=you@gmail.com
```

## Usage

### Check once for new videos
```bash
python3 main.py
```

### Run continuously (checks every hour)
```bash
python3 main.py --poll
```

### Test with a specific video
```bash
python3 main.py --video dQw4w9WgXcQ
```

## Configuration Reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `YOUTUBE_CHANNELS` | Yes | — | Comma-separated channel handles (without @) |
| `YOUTUBE_API_KEY` | Yes | — | YouTube Data API v3 key |
| `LLM_PROVIDER` | No | `gemini` | LLM to use: `gemini`, `openai`, or `anthropic` |
| `GEMINI_API_KEY` | If gemini | — | Google Gemini API key |
| `GEMINI_MODEL` | No | `gemini-2.0-flash` | Gemini model to use |
| `OPENAI_API_KEY` | If openai | — | OpenAI API key |
| `OPENAI_MODEL` | No | `gpt-4o-mini` | OpenAI model to use |
| `ANTHROPIC_API_KEY` | If anthropic | — | Anthropic API key |
| `ANTHROPIC_MODEL` | No | `claude-sonnet-4-5-20250929` | Claude model to use |
| `SMTP_SERVER` | No | `smtp.gmail.com` | SMTP server |
| `SMTP_PORT` | No | `587` | SMTP port |
| `SENDER_EMAIL` | Yes | — | Email to send from |
| `SENDER_PASSWORD` | Yes | — | SMTP password / app password |
| `RECIPIENT_EMAIL` | Yes | — | Email to send summaries to |
| `POLL_INTERVAL` | No | `3600` | Seconds between checks |

## Run as a Background Service (Optional)

### Using cron (simplest)

```bash
crontab -e
```

Add this line to check every hour:
```
0 * * * * cd /path/to/youtubersummary && /usr/bin/python3 main.py >> /tmp/yt-summarizer.log 2>&1
```

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

## Architecture

```
main.py                  — Orchestrator: CLI + polling loop
youtube_monitor.py       — Detects new uploads via YouTube Data API (multi-channel)
transcript_extractor.py  — Extracts video captions via youtube-transcript-api
summarizer.py            — Pluggable LLM backend (Gemini / OpenAI / Claude)
emailer.py               — Formats and sends summary email via SMTP
config.py                — Loads settings from .env
```

## Cost Estimate

- **YouTube Data API**: Free tier gives 10,000 units/day. Each poll uses ~4 units per channel.
- **Gemini API**: Free! The free tier is more than sufficient for this use case.
- **OpenAI API**: ~$0.01-0.05 per video (using gpt-4o-mini).
- **Anthropic API**: ~$0.01-0.03 per video (using Claude Sonnet).
- **Email**: Free via Gmail SMTP.
