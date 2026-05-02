"""Unit tests for agent/_rate_limit.py.

Time injection (`now=` parameter) means every test runs in milliseconds
without sleeping; setup() resets the in-process state so tests are
order-independent.
"""

from __future__ import annotations

import pytest

from agent._rate_limit import (
    _reset_for_tests,
    check_request_rate,
    check_token_budget,
    record_token_usage,
)


@pytest.fixture(autouse=True)
def _isolate_state():
    """Clear the rolling-window state before every test."""
    _reset_for_tests()
    yield
    _reset_for_tests()


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


def test_first_request_is_allowed():
    assert check_request_rate(user_id=1, max_per_window=5, window_seconds=60, now=0.0) is True


def test_request_rate_blocks_after_threshold():
    for _ in range(5):
        assert check_request_rate(1, 5, 60, now=0.0) is True
    assert check_request_rate(1, 5, 60, now=0.0) is False


def test_request_rate_window_evicts_old_entries():
    # Fill the bucket at t=0
    for _ in range(3):
        assert check_request_rate(1, 3, 60, now=0.0) is True
    # Still over limit at t=30 (within the 60-second window)
    assert check_request_rate(1, 3, 60, now=30.0) is False
    # Past the window — old entries evict, new request fits
    assert check_request_rate(1, 3, 60, now=61.0) is True


def test_request_rate_per_user_isolation():
    for _ in range(3):
        check_request_rate(1, 3, 60, now=0.0)
    # User 2 has its own bucket
    assert check_request_rate(2, 3, 60, now=0.0) is True


def test_blocked_request_does_not_extend_block():
    """A request that gets rejected must NOT be added to the log,
    otherwise the rate limiter keeps extending its own block window."""
    for _ in range(3):
        check_request_rate(1, 3, 60, now=0.0)
    # Reject — not recorded
    assert check_request_rate(1, 3, 60, now=10.0) is False
    # 60s after the original entries (not after the rejected one) —
    # should be allowed because the original 3 just aged out.
    assert check_request_rate(1, 3, 60, now=61.0) is True


# ---------------------------------------------------------------------------
# Token budget
# ---------------------------------------------------------------------------


def test_token_budget_starts_with_full_allowance():
    allowed, total = check_token_budget(user_id=1, max_tokens_per_window=1000, window_seconds=3600, now=0.0)
    assert allowed is True
    assert total == 0


def test_token_budget_accumulates_across_calls():
    record_token_usage(1, 300, now=0.0)
    record_token_usage(1, 400, now=10.0)
    allowed, total = check_token_budget(1, 1000, 3600, now=20.0)
    assert allowed is True
    assert total == 700


def test_token_budget_blocks_when_at_or_above_limit():
    record_token_usage(1, 1000, now=0.0)
    allowed, total = check_token_budget(1, 1000, 3600, now=10.0)
    assert allowed is False
    assert total == 1000


def test_token_budget_window_evicts_old_spend():
    record_token_usage(1, 1000, now=0.0)
    # Within window: still over
    allowed, total = check_token_budget(1, 1000, 3600, now=1800.0)
    assert allowed is False
    # Past window: budget restored
    allowed, total = check_token_budget(1, 1000, 3600, now=3601.0)
    assert allowed is True
    assert total == 0


def test_token_budget_per_user_isolation():
    record_token_usage(1, 5000, now=0.0)
    allowed, total = check_token_budget(2, 1000, 3600, now=0.0)
    assert allowed is True
    assert total == 0


def test_record_token_usage_ignores_non_positive():
    # Defensive: SDK fixture-mode responses can report 0 tokens
    record_token_usage(1, 0, now=0.0)
    record_token_usage(1, -50, now=0.0)
    _, total = check_token_budget(1, 100, 3600, now=10.0)
    assert total == 0


def test_check_token_budget_does_not_consume():
    """Checking the budget must NOT add to the log — only
    record_token_usage does. Otherwise a clinician opening a chat
    panel would burn budget without making a request."""
    record_token_usage(1, 100, now=0.0)
    for _ in range(10):
        check_token_budget(1, 1000, 3600, now=10.0)
    _, total = check_token_budget(1, 1000, 3600, now=10.0)
    assert total == 100
