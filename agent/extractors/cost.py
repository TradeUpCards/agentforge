"""Model pricing for eval cost attribution (P1 eval metrics).

Source:   https://www.anthropic.com/pricing (API pricing page)
Verified: 2026-05-06

Re-verify this table:
  - Before each weekly Gauntlet demo (pricing can change without notice).
  - Any time a model name in _HAIKU_MODEL or _SONNET_MODEL changes.
  - After any Anthropic pricing announcement.

Pricing schema: USD per 1,000,000 tokens (per-million).

  Model                    Input $/M    Output $/M
  ----------------------   ----------   ----------
  claude-haiku-4-5           0.80         4.00
  claude-sonnet-4-6          3.00        15.00

Per-document cost ceiling: $0.50 across all retry attempts (PRD §6.4).
Per-run ceiling: configurable via MAX_EXTRACTION_COST_USD_PER_RUN env var (default $5.00).

PHI discipline: no patient data enters this module. Only token counts
(integers) and float pricing constants are processed.
"""

from __future__ import annotations

# Keys must match the model strings used in agent/extractors/__init__.py
# (_HAIKU_MODEL, _SONNET_MODEL constants).
MODEL_PRICING: dict[str, dict[str, float]] = {
    "claude-haiku-4-5": {
        "input_per_million":  0.80,
        "output_per_million": 4.00,
    },
    "claude-sonnet-4-6": {
        "input_per_million":  3.00,
        "output_per_million": 15.00,
    },
}

_MILLION = 1_000_000


def compute_cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> float | None:
    """Return estimated USD cost for one Anthropic call, or None if model is unknown.

    Args:
        model:         Model identifier (e.g. "claude-haiku-4-5").
        input_tokens:  Total input tokens from usage.input_tokens.
        output_tokens: Output tokens from usage.output_tokens.

    Returns:
        Float cost in USD, or None when model is not in MODEL_PRICING.
        Returns None (not 0.0) so callers can distinguish "cost unknown"
        from "cost was actually zero."

    PHI discipline: only integer counts and float constants are processed.
    No string data from document payloads flows through this function.
    """
    pricing = MODEL_PRICING.get(model)
    if pricing is None:
        return None
    return (
        pricing["input_per_million"] * input_tokens / _MILLION
        + pricing["output_per_million"] * output_tokens / _MILLION
    )
