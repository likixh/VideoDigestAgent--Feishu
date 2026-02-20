# YouTube Video Summarizer

Automatically monitors YouTube channels for new videos, extracts transcripts, **auto-detects the content type** (stocks, crypto, podcast, tech review, education, news, etc.), generates tailored summaries using your choice of LLM (Gemini, OpenAI, or Claude), and delivers them via email, local file, or both.

## How It Works

The app uses an **agent-based pipeline** to produce accurate, content-aware summaries:

```
1. CLASSIFY  — Detects video type (stock analysis? podcast? tutorial?)
2. PROMPT    — Selects the best summary template for that content type
3. SUMMARIZE — Generates a structured summary via LLM
4. VERIFY    — (Optional) Second LLM pass to catch errors & hallucinations
```

## Supported Content Types

| Type | What you get |
|------|-------------|
| **Stock Analysis** | Sentiment score, tickers, bull/bear thesis, price targets, conviction levels |
| **Macro Economics** | Economic outlook, indicators, central bank commentary, sector views |
| **Crypto** | Market sentiment, token analysis, on-chain signals |
| **Podcast/Interview** | Guest bios, discussion points, notable quotes, contrarian views |
| **Tech Review** | Specs, pros/cons, comparisons, verdict |
| **Educational** | Core concepts, step-by-step process, pitfalls, key takeaways |
| **News** | Key facts, perspectives, implications |
| **General** | Auto-structured summary adapted to content |

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
| **OpenAI** | ~$0.02-0.10/video | Go to [OpenAI Platform](https://platform.openai.com/api-keys) → Create new secret key |
| **Anthropic (Claude)** | ~$0.02-0.06/video | Go to [Anthropic Console](https://console.anthropic.com/) → API Keys → Create Key |

#### Gmail App Password (only needed if `OUTPUT_MODE` is `email` or `both`)
1. Go to [Google Account Security](https://myaccount.google.com/security)
2. Enable **2-Step Verification** if not already on
3. Go to [App Passwords](https://myaccount.google.com/apppasswords)
4. Select **Mail** and your device, then click **Generate**
5. Copy the 16-character password — this is your `SENDER_PASSWORD`

> **Note:** If you don't use Gmail, update `SMTP_SERVER` and `SMTP_PORT` for your provider (e.g., Outlook: `smtp.office365.com:587`).
>
> **Tip:** If you just want to save summaries locally without email, set `OUTPUT_MODE=local` and skip this step entirely.

### 2. Install Dependencies

```bash
# Required for Whisper audio fallback (Mac)
brew install ffmpeg

pip3 install -r requirements.txt
```

> **Note:** `ffmpeg` is only needed if a video has no captions and Whisper kicks in. The app tries YouTube captions first (instant), and only downloads + transcribes audio as a fallback.

### 3. Configure Environment

```bash
cp .env.example .env
```

Edit `.env` with your actual values:

```env
# Add your YouTube channels (comma-separated, without @)
YOUTUBE_CHANNELS=RhinoFinance,MeetKevin,MrBeast

YOUTUBE_API_KEY=AIza...

# Pick your LLM: gemini, openai, or anthropic
LLM_PROVIDER=gemini
GEMINI_API_KEY=AIza...
# Fallback models if primary hits quota (optional)
# GEMINI_FALLBACK_MODELS=gemini-3-pro-preview,gemini-3-flash-preview,gemini-2.5-pro,gemini-2.5-flash,gemini-2.5-flash-lite

# Summary languages (up to 2)
SUMMARY_LANGUAGES=English,Chinese

# Verify accuracy with a second LLM pass (optional)
VERIFY_SUMMARY=false

# Output: email, local, or both
OUTPUT_MODE=email

# Email (only needed when OUTPUT_MODE is email or both)
SENDER_EMAIL=you@gmail.com
SENDER_PASSWORD=abcd efgh ijkl mnop
RECIPIENT_EMAILS=you@gmail.com
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

### Dry run (no email sent — prints summary to stdout)
```bash
python3 main.py --video dQw4w9WgXcQ --dry-run
```

### Validate your configuration
```bash
python3 main.py --check
```

## Configuration Reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `YOUTUBE_CHANNELS` | Yes | — | Comma-separated channel handles (without @) |
| `YOUTUBE_API_KEY` | Yes | — | YouTube Data API v3 key |
| `LLM_PROVIDER` | No | `gemini` | LLM to use: `gemini`, `openai`, or `anthropic` |
| `GEMINI_API_KEY` | If gemini | — | Google Gemini API key |
| `GEMINI_MODEL` | No | `gemini-3.1-pro-preview` | Gemini model to use |
| `GEMINI_FALLBACK_MODELS` | No | `gemini-3-pro-preview,gemini-3-flash-preview,gemini-2.5-pro,gemini-2.5-flash,gemini-2.5-flash-lite` | Fallback models when primary hits quota (comma-separated, in order) |
| `OPENAI_API_KEY` | If openai | — | OpenAI API key |
| `OPENAI_MODEL` | No | `gpt-4o-mini` | OpenAI model to use |
| `ANTHROPIC_API_KEY` | If anthropic | — | Anthropic API key |
| `ANTHROPIC_MODEL` | No | `claude-sonnet-4-5-20250929` | Claude model to use |
| `SUMMARY_LANGUAGES` | No | `English` | Up to 2 languages, comma-separated |
| `VERIFY_SUMMARY` | No | `false` | Enable accuracy verification pass |
| `OUTPUT_MODE` | No | `email` | `email`, `local` (save file only), or `both` |
| `SMTP_SERVER` | No | `smtp.gmail.com` | SMTP server |
| `SMTP_PORT` | No | `587` | SMTP port |
| `SENDER_EMAIL` | If email/both | — | Email to send from |
| `SENDER_PASSWORD` | If email/both | — | SMTP password / app password |
| `RECIPIENT_EMAILS` | If email/both | — | Email(s) to send summaries to (comma-separated) |
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
Description=YouTube Video Summarizer
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
transcript_extractor.py  — Extracts captions (YouTube API → Whisper fallback)
summarizer.py            — Agent pipeline: classify → prompt → summarize → verify
emailer.py               — Formats and sends summary email via SMTP
history.py               — Tracks processed videos + saves summaries locally
config.py                — Loads settings from .env (incl. OUTPUT_MODE)
```

## Cost Estimate

Each video goes through 2-3 LLM calls (classify + summarize, optionally + verify):

| | Gemini | OpenAI | Anthropic |
|---|---|---|---|
| Without verification | Free | ~$0.02/video | ~$0.02/video |
| With verification | Free | ~$0.04/video | ~$0.04/video |
| Per extra language | Free | ~$0.02/video | ~$0.02/video |

- **YouTube Data API**: Free tier (10,000 units/day, each poll ~4 units/channel)
- **Email**: Free via Gmail SMTP (or skip email entirely with `OUTPUT_MODE=local`)
