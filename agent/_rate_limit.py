"""In-memory rate limiting + token budget tracking per user_id.

Closes two production blockers from the audit pass:

- `agent-review`: "no cost ceiling on the agent" — pathological prompts
  or runaway UC3 conversations could run unbounded against Anthropic.
  This module enforces a per-user-per-hour token budget that rejects
  *before* the LLM call when usage is already over limit.
- `ai-security-review`: "no identity-based rate limiting" — a compromised
  session, buggy script, or curious clinician could exhaust the agent
  for everyone. This module enforces a per-user-per-minute request rate.

Both checks run AFTER HMAC verification so the user_id is trusted (an
attacker can't spam with forged user_ids to consume rate-limit slots
because the request would HMAC-fail before reaching here).

Storage is in-memory on the agent process. For week-1 single-VPS this is
fine; for multi-replica deployments the obvious upgrade is Redis with
the same window-based eviction logic. The public API is designed so
that swap is a drop-in implementation change — callers won't change.

Time injection: every helper accepts `now: float | None = None` so unit
tests can pass synthetic timestamps without sleeping. Production
callers leave `now` unset and the helpers read the wall clock.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import Lock


# Per-user request timestamps. Eviction is lazy — old entries are
# popped on the next read for that user, not on a sweep timer.
_request_log: dict[int, deque[float]] = defaultdict(deque)
_request_lock = Lock()

# Per-user (timestamp, tokens) pairs. Same lazy-eviction shape as the
# request log, but tracks token-spend not request count.
_token_log: dict[int, deque[tuple[float, int]]] = defaultdict(deque)
_token_lock = Lock()


def check_request_rate(
    user_id: int,
    max_per_window: int,
    window_seconds: int,
    *,
    now: float | None = None,
) -> bool:
    """Check + record a request for `user_id` against a rolling window.

    Returns True if the request is allowed (and was recorded). Returns
    False if the user has already issued `max_per_window` requests in
    the last `window_seconds` — in that case the request is NOT
    recorded, so the rate limiter doesn't keep extending its own block.

    Thread-safe (single global lock); per-user state isolated by key.
    """
    if now is None:
        now = time.time()
    cutoff = now - window_seconds
    with _request_lock:
        log = _request_log[user_id]
        # Lazy eviction: pop entries older than the window.
        while log and log[0] < cutoff:
            log.popleft()
        if len(log) >= max_per_window:
            return False
        log.append(now)
        return True


def check_token_budget(
    user_id: int,
    max_tokens_per_window: int,
    window_seconds: int,
    *,
    now: float | None = None,
) -> tuple[bool, int]:
    """Check whether `user_id` has remaining token budget in the window.

    Returns (allowed, current_total). `allowed` is True when the user's
    cumulative recorded tokens in the past `window_seconds` are strictly
    less than `max_tokens_per_window`. The check does not consume budget
    — call `record_token_usage` after the LLM responds to add the actual
    spend to the rolling total.

    `current_total` is returned so the caller can include it in the
    refusal reason / log payload for diagnostics.
    """
    if now is None:
        now = time.time()
    cutoff = now - window_seconds
    with _token_lock:
        log = _token_log[user_id]
        while log and log[0][0] < cutoff:
            log.popleft()
        total = sum(tokens for _, tokens in log)
    return total < max_tokens_per_window, total


def record_token_usage(
    user_id: int,
    tokens: int,
    *,
    now: float | None = None,
) -> None:
    """Record `tokens` spent on a single LLM call for `user_id`. Called
    after the LLM returns so subsequent budget checks see the spend.

    No-op when `tokens <= 0` — defensive against fixture-mode runs and
    SDK shapes where usage isn't reported."""
    if tokens <= 0:
        return
    if now is None:
        now = time.time()
    with _token_lock:
        _token_log[user_id].append((now, tokens))


def _reset_for_tests() -> None:
    """Test-only: clear all in-memory state. Production code never calls
    this — used by unit tests in setup to isolate state per test."""
    with _request_lock:
        _request_log.clear()
    with _token_lock:
        _token_log.clear()
