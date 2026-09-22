"""Retry wrapper for Gemini calls that hit a transient error -- the model
being temporarily overloaded (503 UNAVAILABLE) or rate-limited (429) usually
clears up within a few seconds, but without a retry it previously turned
into a permanently "Parsing Failed" draft on the very first hiccup, forcing
the user to notice and manually re-upload."""

import time

_TRANSIENT_MARKERS = ("503", "UNAVAILABLE", "overloaded", "RESOURCE_EXHAUSTED", "429")


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
