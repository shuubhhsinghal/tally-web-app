"""Retry wrapper for Gemini calls that hit a transient error -- the model
being temporarily overloaded (503 UNAVAILABLE) or rate-limited (429) usually
clears up within a few seconds, but without a retry it previously turned
into a permanently "Parsing Failed" draft on the very first hiccup, forcing
the user to notice and manually re-upload."""

import os
import time

_TRANSIENT_MARKERS = ("503", "UNAVAILABLE", "overloaded", "RESOURCE_EXHAUSTED", "429")

_VALID_EXTRACTION_PROVIDERS = ("gemini", "qwen")


def get_extraction_provider(override: str = None) -> str:
    """Which vision model powers invoice extraction: "gemini" or "qwen".
    Read fresh from the DB on every call (never cached), so flipping the
    toggle in Settings takes effect on the very next upload -- no backend
    restart needed. The DB setting (set from Settings) takes priority; the
    env var only supplies the default before it's ever been set there.

    `override`, when given a valid provider name, wins over both -- this is
    how a specific upload channel (e.g. WhatsApp, which always wants Qwen
    regardless of the Settings toggle used for manual uploads) pins its own
    provider without touching the global default."""
    if override:
        override = override.strip().lower()
        if override in _VALID_EXTRACTION_PROVIDERS:
            return override

    from backend.database import get_app_setting
    stored = get_app_setting("extraction_provider")
    provider = (stored or os.getenv("EXTRACTION_PROVIDER", "gemini")).strip().lower()
    return provider if provider in _VALID_EXTRACTION_PROVIDERS else "gemini"


def is_transient_gemini_error(exc: Exception) -> bool:
    text = str(exc)
    return any(marker in text for marker in _TRANSIENT_MARKERS)


def call_with_retry(fn, max_attempts: int = 3, base_delay_s: float = 3.0):
    """Calls fn() (a zero-arg callable wrapping one Gemini generate_content
    request), retrying with a short backoff only on transient errors.
    Re-raises immediately on anything else (a real error worth surfacing
    right away rather than delaying), and re-raises the last error once
    attempts are exhausted. Runs on a background thread (see
    purchase_drafts.py's run_in_threadpool), so a blocking sleep here is
    safe -- it never blocks the main event loop."""
    last_exc = None
    for attempt in range(max_attempts):
        try:
            return fn()
        except Exception as e:
            last_exc = e
            if not is_transient_gemini_error(e) or attempt == max_attempts - 1:
                raise
            delay = base_delay_s * (attempt + 1)
            print(f"Gemini call hit a transient error (attempt {attempt + 1}/{max_attempts}), retrying in {delay:.0f}s: {e}")
            time.sleep(delay)
    raise last_exc
