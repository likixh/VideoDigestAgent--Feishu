# Plan: Gemini Model Auto-Fallback on Quota Exhaustion

## Problem
When the configured Gemini model (`GEMINI_MODEL`) hits its quota limit, all LLM calls fail and every video is marked as failed with no recovery.

## Approach
Add a **fallback model chain** so that when a Gemini model returns a quota/rate-limit error, the system automatically retries with the next model in the list.

---

## Changes

### 1. `config.py` — Add `GEMINI_FALLBACK_MODELS`

- New env var: `GEMINI_FALLBACK_MODELS` (comma-separated list of alternative Gemini model names)
- Default: `gemini-2.0-flash,gemini-1.5-flash,gemini-2.0-flash-lite`
- The primary `GEMINI_MODEL` is always tried first; `GEMINI_FALLBACK_MODELS` defines the ordered fallback chain used only when quota is exhausted
- If the user sets `GEMINI_FALLBACK_MODELS` to empty, fallback is disabled (fail immediately as today)

### 2. `summarizer.py` — Wrap Gemini calls with fallback retry

**Modify `_llm_call()`:**

- Extract the Gemini-specific call into a helper `_call_gemini(model, system_prompt, user_message)`
- On the first call, use `config.GEMINI_MODEL`
- If it raises a quota/rate-limit exception, iterate through `config.GEMINI_FALLBACK_MODELS` and retry with each
- Catch these specific exception types:
  - `google.api_core.exceptions.ResourceExhausted` (HTTP 429 — quota)
  - `google.genai.errors.ClientError` / `google.genai.errors.ServerError` with quota-related messages
- Log a warning each time a fallback is triggered so the user knows which model was used
- If all models in the chain are exhausted, raise the original exception (current behaviour preserved)

**No changes to OpenAI/Anthropic paths** — this feature is scoped to Gemini only as requested.

### 3. `.env.example` — Document the new variable

Add `GEMINI_FALLBACK_MODELS` with a comment explaining the fallback chain.

### 4. `README.md` — Update Configuration Reference table

Add `GEMINI_FALLBACK_MODELS` row to the table.

---

## File-by-file summary

| File | What changes |
|------|-------------|
| `config.py` | Parse `GEMINI_FALLBACK_MODELS` env var into a list |
| `summarizer.py` | Add fallback loop around Gemini calls in `_llm_call()` |
| `.env.example` | Add `GEMINI_FALLBACK_MODELS` with default + comment |
| `README.md` | Add row to config reference table |

---

## What does NOT change
- OpenAI and Anthropic call paths (untouched)
- The public API of `summarize()`, `_classify()`, `_verify()` (unchanged)
- Non-quota errors (e.g. invalid API key, malformed request) still fail immediately
- `main.py` logic (no changes needed)
