#!/usr/bin/env python3
"""Web interface for YouTube Video Summarizer Agent.

Usage:
    python app.py                # start on http://127.0.0.1:5000
    python app.py --port 8080    # custom port
"""

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

app = Flask(__name__)
app.secret_key = os.urandom(24)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(BASE_DIR, ".env")
ENV_EXAMPLE = os.path.join(BASE_DIR, ".env.example")
HISTORY_FILE = os.path.join(BASE_DIR, "processed_videos.json")
SUMMARIES_DIR = os.path.join(BASE_DIR, "summaries")


# ── Agent process state ──────────────────────────────────────────────────────

agent_state = {
    "running": False,
    "process": None,
    "output": [],
    "mode": None,
    "start_time": None,
    "exit_code": None,
}
agent_lock = threading.Lock()


# ── Configuration schema ─────────────────────────────────────────────────────

CONFIG_SECTIONS = [
    {
        "id": "youtube",
        "title": "YouTube",
        "icon": "bi-youtube",
        "fields": [
            {
                "key": "YOUTUBE_CHANNELS",
                "label": "Channels",
                "type": "text",
                "placeholder": "RhinoFinance,TechChannel",
                "help": "Comma-separated handles (without @). Can be empty if search is configured.",
            },
            {
                "key": "YOUTUBE_API_KEY",
                "label": "API Key",
                "type": "password",
                "placeholder": "YouTube Data API v3 key",
                "required": True,
            },
        ],
    },
    {
        "id": "youtube_search",
        "title": "YouTube Search",
        "icon": "bi-search",
        "fields": [
            {
                "key": "YOUTUBE_SEARCH_QUERIES",
                "label": "Search Queries",
                "type": "text",
                "placeholder": "AI news,artificial intelligence,machine learning",
                "help": "Comma-separated search terms. Leave empty to disable search.",
            },
            {
                "key": "YOUTUBE_SEARCH_MAX_RESULTS",
                "label": "Results per Query",
                "type": "number",
                "default": "10",
                "help": "Max results per search query (1-50)",
            },
            {
                "key": "YOUTUBE_SEARCH_INTERVAL",
                "label": "Search Interval (seconds)",
                "type": "number",
                "default": "14400",
                "help": "How often to search (14400 = every 4 hours)",
            },
            {
                "key": "YOUTUBE_SEARCH_QUOTA_BUDGET",
                "label": "Daily Quota Budget",
                "type": "number",
                "default": "5000",
                "help": "Max YouTube API units for search per day (total limit: 10,000)",
            },
            {
                "key": "YOUTUBE_SEARCH_RELEVANCE_KEYWORDS",
                "label": "Relevance Keywords",
                "type": "text",
                "placeholder": "AI,machine learning,LLM,GPT",
                "help": "Pre-filter: skip results whose title doesn't contain any of these",
                "default": "AI,artificial intelligence,machine learning,deep learning,LLM,GPT,neural network,transformer,AGI,GenAI",
            },
            {
                "key": "YOUTUBE_SEARCH_MIN_DURATION",
                "label": "Min Duration (minutes)",
                "type": "number",
                "default": "10",
                "help": "Skip videos shorter than this (filters out short clips)",
            },
        ],
    },
    {
        "id": "llm",
        "title": "LLM Provider",
        "icon": "bi-robot",
        "fields": [
            {
                "key": "LLM_PROVIDER",
                "label": "Provider",
                "type": "select",
                "options": ["gemini", "openai", "anthropic"],
                "default": "gemini",
            },
            {
                "key": "GEMINI_API_KEY",
                "label": "Gemini API Key",
                "type": "password",
                "placeholder": "Gemini API key",
                "show_if": "gemini",
            },
            {
                "key": "GEMINI_MODEL",
                "label": "Gemini Model",
                "type": "text",
                "default": "gemini-3.1-pro-preview",
                "show_if": "gemini",
            },
            {
                "key": "GEMINI_FALLBACK_MODELS",
                "label": "Fallback Models",
                "type": "text",
                "default": "gemini-3-pro-preview,gemini-3-flash-preview,gemini-2.5-pro,gemini-2.5-flash,gemini-2.5-flash-lite",
                "show_if": "gemini",
                "help": "Comma-separated fallback model chain",
            },
            {
                "key": "OPENAI_API_KEY",
                "label": "OpenAI API Key",
                "type": "password",
                "placeholder": "OpenAI API key",
                "show_if": "openai",
            },
            {
                "key": "OPENAI_MODEL",
                "label": "OpenAI Model",
                "type": "text",
                "default": "gpt-4o-mini",
                "show_if": "openai",
            },
            {
                "key": "ANTHROPIC_API_KEY",
                "label": "Anthropic API Key",
                "type": "password",
                "placeholder": "Anthropic API key",
                "show_if": "anthropic",
            },
            {
                "key": "ANTHROPIC_MODEL",
                "label": "Anthropic Model",
                "type": "text",
                "default": "claude-sonnet-4-5-20250929",
                "show_if": "anthropic",
            },
        ],
    },
    {
        "id": "summary",
        "title": "Summary Settings",
        "icon": "bi-card-text",
        "fields": [
            {
                "key": "SUMMARY_LANGUAGES",
                "label": "Languages",
                "type": "text",
                "default": "English",
                "help": "Up to 2 languages, comma-separated",
            },
            {
                "key": "VERIFY_SUMMARY",
                "label": "Verify Summary",
                "type": "checkbox",
                "default": "false",
                "help": "Second LLM pass for accuracy check (doubles cost)",
            },
            {
                "key": "OUTPUT_MODE",
                "label": "Output Mode",
                "type": "select",
                "options": ["email", "local", "both"],
                "default": "email",
            },
            {
                "key": "POLL_INTERVAL",
                "label": "Poll Interval (seconds)",
                "type": "number",
                "default": "3600",
                "help": "Seconds between polling checks",
            },
        ],
    },
    {
        "id": "email",
        "title": "Email Settings",
        "icon": "bi-envelope",
        "fields": [
            {
                "key": "SMTP_SERVER",
                "label": "SMTP Server",
                "type": "text",
                "default": "smtp.gmail.com",
            },
            {
                "key": "SMTP_PORT",
                "label": "SMTP Port",
                "type": "number",
                "default": "587",
            },
            {
                "key": "SENDER_EMAIL",
                "label": "Sender Email",
                "type": "email",
                "placeholder": "you@gmail.com",
            },
            {
                "key": "SENDER_PASSWORD",
                "label": "Sender Password",
                "type": "password",
                "placeholder": "Gmail App Password",
                "help": "16-character Google App Password",
            },
            {
                "key": "RECIPIENT_EMAILS",
                "label": "Recipients",
                "type": "text",
                "placeholder": "user@gmail.com,other@gmail.com",
                "help": "Comma-separated email addresses",
            },
        ],
    },
]


# ── Helper functions ─────────────────────────────────────────────────────────


def read_env() -> dict:
    """Read .env file and return key-value pairs."""
    env_path = ENV_FILE
    if not os.path.exists(env_path):
        if os.path.exists(ENV_EXAMPLE):
            import shutil

            shutil.copy(ENV_EXAMPLE, env_path)
    values = {}
    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    values[key.strip()] = value.strip()
    return values


def write_env(values: dict) -> None:
    """Write config values to .env file with section headers."""
    lines = []
    for section in CONFIG_SECTIONS:
        lines.append(f"# {'─' * 46}")
        lines.append(f"# {section['title']}")
        lines.append(f"# {'─' * 46}")
        for field in section["fields"]:
            key = field["key"]
            if key in values and values[key]:
                lines.append(f"{key}={values[key]}")
        lines.append("")

    with open(ENV_FILE, "w") as f:
        f.write("\n".join(lines) + "\n")


def load_history() -> list:
    """Load processing history sorted newest first."""
    if not os.path.exists(HISTORY_FILE):
        return []
    with open(HISTORY_FILE, "r") as f:
        data = json.load(f)
    if isinstance(data, list):
        return [
            {"video_id": vid, "status": "sent", "title": "", "channel": ""}
            for vid in data
        ]
    items = [{"video_id": vid_id, **meta} for vid_id, meta in data.items()]
    items.sort(key=lambda x: x.get("date", ""), reverse=True)
    return items


def get_summary_files() -> list:
    """Get list of saved summary markdown files."""
    if not os.path.exists(SUMMARIES_DIR):
        return []
    files = []
    for fname in sorted(os.listdir(SUMMARIES_DIR), reverse=True):
        if fname.endswith(".md"):
            filepath = os.path.join(SUMMARIES_DIR, fname)
            stat = os.stat(filepath)
            files.append(
                {
                    "filename": fname,
                    "size": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime).strftime(
                        "%Y-%m-%d %H:%M"
                    ),
                }
            )
    return files


def read_summary_file(filename: str) -> str:
    """Read a summary markdown file (with path traversal protection)."""
    safe_name = os.path.basename(filename)
    filepath = os.path.join(SUMMARIES_DIR, safe_name)
    if not os.path.exists(filepath):
        return ""
    if not os.path.abspath(filepath).startswith(os.path.abspath(SUMMARIES_DIR)):
        return ""
    with open(filepath, "r", encoding="utf-8") as f:
        return f.read()


# ── Agent management ─────────────────────────────────────────────────────────


def _read_agent_output(process):
    """Background thread: read agent stdout line by line."""
    try:
        for line in process.stdout:
            with agent_lock:
                agent_state["output"].append(line.rstrip("\n"))
    except Exception:
        pass
    process.wait()
    with agent_lock:
        agent_state["running"] = False
        agent_state["exit_code"] = process.returncode
        agent_state["output"].append(
            f"\n--- Agent finished (exit code: {process.returncode}) ---"
        )


def start_agent(mode: str, video_id: str = "", dry_run: bool = False) -> bool:
    """Start the agent as a subprocess. Returns False if already running."""
    with agent_lock:
        if agent_state["running"]:
            return False

    cmd = [sys.executable, os.path.join(BASE_DIR, "main.py")]
    if mode == "poll":
        cmd.append("--poll")
    elif mode == "video" and video_id:
        cmd.extend(["--video", video_id])
    elif mode == "retry":
        cmd.append("--retry")
    elif mode == "check":
        cmd.append("--check")
    elif mode == "history":
        cmd.append("--history")
    # mode == "once" needs no extra args

    if dry_run:
        cmd.append("--dry-run")

    # Pass .env values into the subprocess environment
    env = os.environ.copy()
    env.update(read_env())

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=BASE_DIR,
            bufsize=1,
            env=env,
        )
    except Exception as e:
        with agent_lock:
            agent_state["output"] = [f"Failed to start agent: {e}"]
        return False

    with agent_lock:
        agent_state["running"] = True
        agent_state["process"] = process
        agent_state["output"] = [f"$ {' '.join(cmd)}\n"]
        agent_state["mode"] = mode
        agent_state["start_time"] = time.time()
        agent_state["exit_code"] = None

    thread = threading.Thread(target=_read_agent_output, args=(process,), daemon=True)
    thread.start()
    return True


def stop_agent() -> bool:
    """Stop the running agent subprocess."""
    with agent_lock:
        if not agent_state["running"] or not agent_state["process"]:
            return False
        try:
            agent_state["process"].terminate()
            agent_state["output"].append("\n--- Stopped by user ---")
        except Exception:
            pass
    return True


# ── Template context ─────────────────────────────────────────────────────────


@app.context_processor
def inject_globals():
    """Make agent_running available in every template."""
    return {"agent_running": agent_state["running"]}


# ── Routes ───────────────────────────────────────────────────────────────────


@app.route("/")
def dashboard():
    history = load_history()
    env = read_env()

    total = len([h for h in history if h.get("status") != "init"])
    sent = sum(1 for h in history if h.get("status") == "sent")
    failed = sum(1 for h in history if h.get("status") == "failed")

    channels = env.get("YOUTUBE_CHANNELS", "")
    channel_count = len([c for c in channels.split(",") if c.strip()]) if channels else 0

    search_queries = env.get("YOUTUBE_SEARCH_QUERIES", "")
    config_info = {
        "provider": env.get("LLM_PROVIDER", "gemini"),
        "channels": channels or "—",
        "search_queries": search_queries or "disabled",
        "output_mode": env.get("OUTPUT_MODE", "email"),
        "languages": env.get("SUMMARY_LANGUAGES", "English"),
        "poll_interval": env.get("POLL_INTERVAL", "3600"),
    }

    return render_template(
        "dashboard.html",
        active="dashboard",
        stats={
            "total": total,
            "sent": sent,
            "failed": failed,
            "channels": channel_count,
        },
        recent=history[:10],
        config_info=config_info,
        summary_count=len(get_summary_files()),
    )


@app.route("/config", methods=["GET"])
def config_page():
    values = read_env()
    return render_template(
        "config.html", active="config", sections=CONFIG_SECTIONS, values=values
    )


@app.route("/config", methods=["POST"])
def config_save():
    # Collect current values to preserve passwords that weren't re-entered
    old_values = read_env()
    values = {}

    for section in CONFIG_SECTIONS:
        for field in section["fields"]:
            key = field["key"]
            if field.get("type") == "checkbox":
                values[key] = "true" if request.form.get(key) else "false"
            else:
                val = request.form.get(key, "").strip()
                if val:
                    values[key] = val
                elif field.get("type") == "password" and old_values.get(key):
                    # Keep existing password if the user left the field blank
                    values[key] = old_values[key]

    write_env(values)
    flash("Configuration saved.", "success")
    return redirect(url_for("config_page"))


@app.route("/run")
def run_page():
    return render_template("run.html", active="run")


@app.route("/api/run", methods=["POST"])
def api_run():
    data = request.get_json() or {}
    mode = data.get("mode", "once")
    video_id = data.get("video_id", "")
    dry_run = data.get("dry_run", False)

    if mode == "video" and not video_id:
        return jsonify({"error": "Video ID is required"}), 400

    ok = start_agent(mode, video_id, dry_run)
    if not ok:
        return jsonify({"error": "Agent is already running"}), 409
    return jsonify({"status": "started", "mode": mode})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    ok = stop_agent()
    if not ok:
        return jsonify({"error": "No agent is running"}), 400
    return jsonify({"status": "stopped"})


@app.route("/api/status")
def api_status():
    with agent_lock:
        return jsonify(
            {
                "running": agent_state["running"],
                "mode": agent_state["mode"],
                "output": agent_state["output"],
                "start_time": agent_state["start_time"],
                "exit_code": agent_state["exit_code"],
            }
        )


@app.route("/archive")
def archive_page():
    history = load_history()
    summary_files = get_summary_files()

    status_filter = request.args.get("status", "all")
    if status_filter != "all":
        history = [h for h in history if h.get("status") == status_filter]

    return render_template(
        "archive.html",
        active="archive",
        history=history,
        summary_files=summary_files,
        status_filter=status_filter,
    )


@app.route("/archive/summary/<path:filename>")
def view_summary(filename):
    content = read_summary_file(filename)
    if not content:
        flash("Summary file not found.", "error")
        return redirect(url_for("archive_page"))
    return render_template(
        "summary_detail.html", active="archive", filename=filename, content=content
    )


# ── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="YT Summarizer Web UI")
    parser.add_argument("--port", type=int, default=5000, help="Port (default 5000)")
    parser.add_argument("--host", default="127.0.0.1", help="Host (default 127.0.0.1)")
    args = parser.parse_args()

    print(f"\n  YT Summarizer Web UI → http://{args.host}:{args.port}\n")
    app.run(host=args.host, port=args.port, debug=True)
