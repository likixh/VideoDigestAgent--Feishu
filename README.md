# YouTube Video Summarizer

Automatically monitors YouTube channels for new videos, extracts transcripts, **auto-detects the content type** (stocks, crypto, podcast, tech review, education, news, etc.), generates tailored summaries using your choice of LLM (Gemini, OpenAI, or Claude), and emails them to you.

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

#### Gmail App Password (for sending emails)
1. Go to [Google Account Security](https://myaccount.google.com/security)
2. Enable **2-Step Verification** if not already on
3. Go to [App Passwords](https://myaccount.google.com/apppasswords)
4. Select **Mail** and your device, then click **Generate**
5. Copy the 16-character password — this is your `SENDER_PASSWORD`

> **Note:** If you don't use Gmail, update `SMTP_SERVER` and `SMTP_PORT` for your provider (e.g., Outlook: `smtp.office365.com:587`).

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

# Summary languages (up to 2)
SUMMARY_LANGUAGES=English,Chinese

# Verify accuracy with a second LLM pass (optional)
VERIFY_SUMMARY=false

# Email
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
| `GEMINI_MODEL` | No | `gemini-2.0-flash` | Gemini model to use |
| `OPENAI_API_KEY` | If openai | — | OpenAI API key |
| `OPENAI_MODEL` | No | `gpt-4o-mini` | OpenAI model to use |
| `ANTHROPIC_API_KEY` | If anthropic | — | Anthropic API key |
| `ANTHROPIC_MODEL` | No | `claude-sonnet-4-5-20250929` | Claude model to use |
| `SUMMARY_LANGUAGES` | No | `English` | Up to 2 languages, comma-separated |
| `VERIFY_SUMMARY` | No | `false` | Enable accuracy verification pass |
| `SMTP_SERVER` | No | `smtp.gmail.com` | SMTP server |
| `SMTP_PORT` | No | `587` | SMTP port |
| `SENDER_EMAIL` | Yes | — | Email to send from |
| `SENDER_PASSWORD` | Yes | — | SMTP password / app password |
| `RECIPIENT_EMAILS` | Yes | — | Email(s) to send summaries to (comma-separated) |
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

## Agentic Framework Integration

The project supports three pipeline engines — choose the one that fits your needs:

### Default Pipeline (`PIPELINE_ENGINE=default`)
The original sequential pipeline. Simple, reliable, low cost.

```
classify → select prompt → summarize → verify (optional)
```

### LangGraph Pipeline (`PIPELINE_ENGINE=langgraph`)
A proper state machine workflow (like Airflow/Temporal for LLM flows):

```
classify → RAG context retrieval → select prompt → summarize
    → quality check → (retry if low quality) → verify → done
```

**What it adds over default:**
- **RAG context injection**: Automatically fetches relevant past video summaries to enrich new summaries ("Last week you said X about NVDA, now...")
- **Quality gate**: Scores summaries on length, structure, and TL;DR presence — retries up to 2x if quality is low
- **Conditional routing**: Different paths based on content type and quality score

Install: `pip install langgraph chromadb`

### CrewAI Pipeline (`PIPELINE_ENGINE=crewai`)
Multi-agent orchestration (like microservices architecture for AI):

```
Researcher → Analyst → Writer → Fact-Checker
```

Each agent has a specialized role:
| Agent | Role | Analog |
|-------|------|--------|
| **Researcher** | Queries RAG for historical context | Data service |
| **Analyst** | Deep content analysis by content type | Domain service |
| **Writer** | Crafts structured markdown summary | Presentation service |
| **Fact-Checker** | Verifies accuracy against transcript | QA service |

Install: `pip install crewai chromadb`

### RAG-Powered Features (`RAG_ENABLED=true`)

When RAG is enabled, every processed video gets indexed into a local ChromaDB vector store. This powers several analytical features:

```bash
# Ask any question about past videos
python3 main.py --ask "What did RhinoFinance say about NVDA last week?"

# Compare what channels say about the same topic
python3 main.py --compare "interest rates"

# Track a channel's sentiment over time
python3 main.py --trends RhinoFinance

# View RAG index stats
python3 main.py --rag-stats
```

### Weekly Digest

Aggregate all summaries from a time period into a single executive briefing email:

```bash
# Weekly digest (default: last 7 days)
python3 main.py --digest

# Last 3 days
python3 main.py --digest --days 3

# Preview without emailing
python3 main.py --digest --dry-run
```

The digest includes:
- Executive overview (LLM-generated synthesis of all recent content)
- Per-channel breakdown with condensed summaries
- Cross-channel analysis (if RAG is enabled)

## Prediction Tracker (Optional)

Track the accuracy of stock/crypto predictions made by YouTube channels over time.

When enabled, the system automatically:
1. **Extracts** structured predictions from video summaries (tickers, direction, conviction, price targets)
2. **Fetches** actual market data from yfinance (stocks/ETFs) and CoinGecko (crypto)
3. **Scores** predictions at 1-week, 1-month, and 3-month windows
4. **Generates** per-channel scorecards and cross-channel leaderboards

### Enable

```env
PREDICTION_TRACKING=true
```

### Prediction Tracker CLI

```bash
# View a channel's scorecard
python3 main.py --scorecard MeetKevin

# Cross-channel leaderboard
python3 main.py --leaderboard

# List tracked predictions
python3 main.py --predictions
python3 main.py --predictions MeetKevin

# Manually trigger score update
python3 main.py --score-update

# Backfill predictions from previously saved summaries
python3 main.py --backfill

# Tracker stats
python3 main.py --tracker-stats

# Change evaluation window (default: 1M)
python3 main.py --scorecard MeetKevin --eval-window 3M
```

### Scoring Dimensions

| Dimension | Weight | Description |
|-----------|--------|-------------|
| Direction accuracy | 50% | Did the stock move the predicted direction? |
| Target accuracy | 20% | Did it hit the stated price target? |
| Benchmark performance | 20% | Did the pick beat SPY (stocks) or BTC (crypto)? |
| Magnitude | 10% | Bonus for large correct moves |

Conviction level (high/medium/low) acts as a multiplier on the composite score.

## Architecture

```
main.py                  — Orchestrator: CLI + pipeline router + polling loop
youtube_monitor.py       — Detects new uploads via YouTube Data API (multi-channel)
transcript_extractor.py  — Extracts captions (YouTube API → Whisper fallback)
summarizer.py            — Default pipeline: classify → prompt → summarize → verify
langgraph_pipeline.py    — LangGraph state machine with RAG + quality checks
crew_summarizer.py       — CrewAI multi-agent crew (4 specialized agents)
rag_store.py             — ChromaDB vector store for transcripts + summaries
cross_analyzer.py        — Cross-video analysis, trends, contradiction detection
digest.py                — Weekly digest / newsletter generator
prediction_extractor.py  — LLM-powered extraction of structured predictions
prediction_db.py         — SQLite database for predictions, scores, and price cache
market_data.py           — Market data agent (yfinance + CoinGecko)
prediction_scorer.py     — Scoring engine + report generation
prediction_tracker.py    — Orchestrator tying extraction → market data → scoring
emailer.py               — Formats and sends summary email via SMTP
history.py               — Processing history tracking (JSON persistence)
config.py                — Loads settings from .env
```

### Concepts for Software Engineers

| AI Concept | Traditional Analog | Implementation |
|---|---|---|
| **Prompt Engineering** | Writing API docs / interface contracts | `summarizer.py` prompt templates |
| **RAG Pipeline** | Elasticsearch (embed + index + search) | `rag_store.py` with ChromaDB |
| **Agent Framework** | Workflow engine (Airflow, Temporal) | `langgraph_pipeline.py` |
| **Multi-Agent** | Microservice orchestration (K8s, saga pattern) | `crew_summarizer.py` with CrewAI |
| **MCP Protocol** | gRPC / API gateway | Pipeline router in `main.py` |

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
| `SUMMARY_LANGUAGES` | No | `English` | Up to 2 languages, comma-separated |
| `VERIFY_SUMMARY` | No | `false` | Enable accuracy verification pass |
| `PIPELINE_ENGINE` | No | `default` | Pipeline: `default`, `langgraph`, or `crewai` |
| `RAG_ENABLED` | No | `false` | Enable RAG for cross-video context + analytics |
| `PREDICTION_TRACKING` | No | `false` | Enable prediction tracking and scoring |
| `SMTP_SERVER` | No | `smtp.gmail.com` | SMTP server |
| `SMTP_PORT` | No | `587` | SMTP port |
| `SENDER_EMAIL` | Yes | — | Email to send from |
| `SENDER_PASSWORD` | Yes | — | SMTP password / app password |
| `RECIPIENT_EMAILS` | Yes | — | Email(s) to send summaries to (comma-separated) |
| `POLL_INTERVAL` | No | `3600` | Seconds between checks |

## Cost Estimate

Each video goes through 2-3 LLM calls (classify + summarize, optionally + verify):

| | Gemini | OpenAI | Anthropic |
|---|---|---|---|
| Without verification | Free | ~$0.02/video | ~$0.02/video |
| With verification | Free | ~$0.04/video | ~$0.04/video |
| Per extra language | Free | ~$0.02/video | ~$0.02/video |
| CrewAI pipeline | Free | ~$0.08/video | ~$0.08/video |

- **YouTube Data API**: Free tier (10,000 units/day, each poll ~4 units/channel)
- **Email**: Free via Gmail SMTP
- **ChromaDB (RAG)**: Free (local, no external service)
