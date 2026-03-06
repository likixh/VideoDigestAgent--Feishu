# Video Digest Agent

Automatically monitors **YouTube channels**, **YouTube keyword searches**, and **Bilibili user spaces** for new videos, extracts transcripts, **auto-detects the content type** (stocks, crypto, podcast, tech review, education, news, etc.), generates tailored summaries using your choice of LLM (Gemini, OpenAI, or Claude), and delivers them via email, local file, or both.

Includes a **web UI** for point-and-click configuration and one-click runs, as well as a CLI for automation and scripting.

## How It Works

The app uses an **agent-based pipeline** to produce accurate, content-aware summaries:

```
1. DISCOVER  — Fetches new uploads from YouTube channels, search results, or Bilibili users
2. CLASSIFY  — Detects video type (stock analysis? podcast? tutorial?)
3. PROMPT    — Selects the best summary template for that content type
4. SUMMARIZE — Generates a structured summary via LLM
5. VERIFY    — (Optional) Second LLM pass to catch errors & hallucinations
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

#### YouTube Data API v3 Key (required for YouTube sources)
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
# YouTube channels to monitor (comma-separated, without @)
YOUTUBE_CHANNELS=RhinoFinance,MeetKevin

YOUTUBE_API_KEY=AIza...

# Pick your LLM: gemini, openai, or anthropic
LLM_PROVIDER=gemini
GEMINI_API_KEY=AIza...

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

### Web UI

```bash
python3 app.py
```

> **macOS — port 5000 already in use?** macOS Monterey and later runs AirPlay Receiver on port 5000 by default. Either disable it (**System Settings → General → AirDrop & Handoff → AirPlay Receiver → off**) or use a different port:
> ```bash
> python3 app.py --port 8080
> ```

Opens a dashboard at `http://127.0.0.1:5000` with:
- **Dashboard** — live stats, recent history, config overview
- **Run** — trigger once, poll, test a specific video, retry failed, or validate config
- **Config** — edit all settings through a form (no manual `.env` editing required)
- **Archive** — browse and read saved summary files

```bash
python3 app.py --port 8080 --host 0.0.0.0   # custom port / expose to network
```

### CLI

#### Check once for new videos
```bash
python3 main.py
```

#### Run continuously (checks every hour)
```bash
python3 main.py --poll
```

#### Test with a specific video
```bash
# YouTube video
python3 main.py --video dQw4w9WgXcQ

# Bilibili video (prefix BV IDs are detected automatically)
python3 main.py --video BV1xx411c7XZ
```

#### Dry run (no email sent — prints summary to stdout)
```bash
python3 main.py --video dQw4w9WgXcQ --dry-run
```

#### Validate your configuration
```bash
python3 main.py --check
```

#### Show processing history
```bash
python3 main.py --history
```

#### Retry previously failed videos
```bash
python3 main.py --retry
```

## Video Sources

### YouTube Channels

Set `YOUTUBE_CHANNELS` to a comma-separated list of channel handles (without `@`). The agent resolves handles to channel IDs and caches them locally to minimise API usage.

### YouTube Keyword Search

Set `YOUTUBE_SEARCH_QUERIES` to discover new videos beyond your subscribed channels:

```env
YOUTUBE_SEARCH_QUERIES=AI news,machine learning,LLM
YOUTUBE_SEARCH_MAX_RESULTS=5          # results per query
YOUTUBE_SEARCH_INTERVAL=14400         # search every 4 hours (seconds)
YOUTUBE_SEARCH_QUOTA_BUDGET=5000      # max API units/day for search
YOUTUBE_SEARCH_RELEVANCE_KEYWORDS=AI,LLM,GPT   # title pre-filter
YOUTUBE_SEARCH_MIN_DURATION=10        # skip clips shorter than N minutes
YOUTUBE_SEARCH_MAX_TOTAL=15           # cap total videos per search cycle
YOUTUBE_SEARCH_MIN_VIEWS=1000         # skip low-traffic videos (0 = off)
```

Keyword search uses the YouTube Search API (100 units/call). The daily free tier is 10,000 units total across all API calls.

### Bilibili

Monitor Bilibili user spaces for new uploads. Requires browser cookies for subtitle/transcript access:

```env
BILIBILI_ENABLED=true
BILIBILI_USERS=12345,67890            # numeric UIDs from profile URLs
BILIBILI_SESSDATA=...                 # get from browser DevTools
BILIBILI_BILI_JCT=...
BILIBILI_BUVID3=...
```

To get the cookies: open [bilibili.com](https://www.bilibili.com), log in, open DevTools (F12) → Application → Cookies, and copy the values for `SESSDATA`, `bili_jct`, and `buvid3`.

Bilibili support requires the optional `bilibili-api-python` and `httpx` packages (included in `requirements.txt`).

## Configuration Reference

### Core

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `YOUTUBE_CHANNELS` | One source required | — | Comma-separated channel handles (without @) |
| `YOUTUBE_API_KEY` | If YouTube enabled | — | YouTube Data API v3 key |
| `LLM_PROVIDER` | No | `gemini` | LLM to use: `gemini`, `openai`, or `anthropic` |
| `GEMINI_API_KEY` | If gemini | — | Google Gemini API key |
| `GEMINI_MODEL` | No | `gemini-3.1-pro-preview` | Gemini model to use |
| `GEMINI_FALLBACK_MODELS` | No | see `.env.example` | Fallback model chain when primary hits quota |
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

### YouTube Search

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `YOUTUBE_SEARCH_QUERIES` | No | — | Comma-separated search terms; leave empty to disable |
| `YOUTUBE_SEARCH_MAX_RESULTS` | No | `5` | Results per search query (1–50) |
| `YOUTUBE_SEARCH_INTERVAL` | No | `14400` | Seconds between search runs |
| `YOUTUBE_SEARCH_QUOTA_BUDGET` | No | `5000` | Max YouTube API units for search per day |
| `YOUTUBE_SEARCH_RELEVANCE_KEYWORDS` | No | see `.env.example` | Title pre-filter keywords |
| `YOUTUBE_SEARCH_MIN_DURATION` | No | `10` | Skip videos shorter than N minutes |
| `YOUTUBE_SEARCH_MAX_TOTAL` | No | `15` | Max total search results processed per cycle |
| `YOUTUBE_SEARCH_MIN_VIEWS` | No | `1000` | Skip videos with fewer views (0 = off) |

### Bilibili

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `BILIBILI_ENABLED` | No | `false` | Enable Bilibili monitoring |
| `BILIBILI_USERS` | If enabled | — | Comma-separated numeric UIDs |
| `BILIBILI_SESSDATA` | If enabled | — | Browser cookie for auth |
| `BILIBILI_BILI_JCT` | If enabled | — | CSRF token cookie |
| `BILIBILI_BUVID3` | If enabled | — | Device identifier cookie |

## Run as a Background Service (Optional)

### Using cron (simplest)

```bash
crontab -e
```

Add this line to check every hour:
```
0 * * * * cd /path/to/VideoDigestAgent && /usr/bin/python3 main.py >> /tmp/video-digest.log 2>&1
```

### Using systemd (Linux)

Create `/etc/systemd/system/video-digest.service`:

```ini
[Unit]
Description=Video Digest Agent
After=network.target

[Service]
Type=simple
WorkingDirectory=/path/to/VideoDigestAgent
ExecStart=/usr/bin/python3 main.py --poll
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
```

Then:
```bash
sudo systemctl enable video-digest
sudo systemctl start video-digest
```

## Architecture

```
app.py                   — Flask web UI (dashboard, config editor, archive viewer)
main.py                  — CLI orchestrator: argument parsing + polling loop
youtube_monitor.py       — Detects new uploads via YouTube Data API; keyword search
bilibili_monitor.py      — Detects new uploads from Bilibili user spaces
transcript_extractor.py  — Extracts captions (YouTube API → Whisper fallback; Bilibili subtitles)
summarizer.py            — Agent pipeline: classify → prompt → summarize → verify
emailer.py               — Formats and sends summary email via SMTP
history.py               — Tracks processed videos + saves summaries locally
config.py                — Loads and validates all settings from .env
```

## Cost Estimate

Each video goes through 2–3 LLM calls (classify + summarize, optionally + verify):

| | Gemini | OpenAI | Anthropic |
|---|---|---|---|
| Without verification | Free | ~$0.02/video | ~$0.02/video |
| With verification | Free | ~$0.04/video | ~$0.04/video |
| Per extra language | Free | ~$0.02/video | ~$0.02/video |

- **YouTube Data API**: Free tier (10,000 units/day; channel polling ~4 units/channel/check; search ~100 units/call)
- **Email**: Free via Gmail SMTP (or skip email entirely with `OUTPUT_MODE=local`)
