import pytest
from unittest.mock import patch
from backend.services.extraction_v4.retry import call_with_retry, is_transient_gemini_error


def test_is_transient_gemini_error_matches_known_markers():
    assert is_transient_gemini_error(Exception("503 UNAVAILABLE. {'error': {'code': 503}}"))
    assert is_transient_gemini_error(Exception("429 Too Many Requests"))
    assert is_transient_gemini_error(Exception("RESOURCE_EXHAUSTED: quota exceeded"))


def test_is_transient_gemini_error_does_not_match_real_errors():
    assert not is_transient_gemini_error(Exception("400 Bad Request: invalid schema"))
    assert not is_transient_gemini_error(ValueError("GEMINI_API_KEY not configured"))


def test_call_with_retry_succeeds_after_transient_failures():
    calls = {"count": 0}

    def flaky():
        calls["count"] += 1
        if calls["count"] < 3:
            raise Exception("503 UNAVAILABLE. Model overloaded.")
        return "ok"

    with patch("backend.services.extraction_v4.retry.time.sleep") as mock_sleep:
        result = call_with_retry(flaky, max_attempts=3, base_delay_s=1)

    assert result == "ok"
    assert calls["count"] == 3
    assert mock_sleep.call_count == 2  # slept between attempts 1->2 and 2->3


def test_call_with_retry_gives_up_after_max_attempts():
    def always_fails():
        raise Exception("503 UNAVAILABLE. Model overloaded.")

    with patch("backend.services.extraction_v4.retry.time.sleep"):
        with pytest.raises(Exception, match="503 UNAVAILABLE"):
            call_with_retry(always_fails, max_attempts=3, base_delay_s=1)


def test_call_with_retry_does_not_retry_non_transient_errors():
    calls = {"count": 0}

    def fails_for_real():
        calls["count"] += 1
        raise ValueError("GEMINI_API_KEY not configured")

    with patch("backend.services.extraction_v4.retry.time.sleep") as mock_sleep:
        with pytest.raises(ValueError, match="GEMINI_API_KEY"):
            call_with_retry(fails_for_real, max_attempts=3, base_delay_s=1)

    assert calls["count"] == 1  # no retry attempted
    mock_sleep.assert_not_called()
