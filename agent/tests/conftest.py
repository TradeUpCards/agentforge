"""Test environment overrides.

Eval cases (01-06) and chat-endpoint integration tests were calibrated
against the Maria Hernandez fixture and the canned LLM response. They
need both `USE_FIXTURE_DATA=true` and `USE_FIXTURE_LLM=true` for
deterministic, free, fast runs in CI.

Runtime production flips both to `false` (live LLM, real DB queries).
The eval framework's CLI (`python -m agent.tests.eval.runner`) honors
.env settings — use that for live-mode evaluation against the real DB.
"""

from __future__ import annotations

import os

# Set BEFORE any agent module is imported so config.py picks them up.
os.environ["USE_FIXTURE_DATA"] = "true"
os.environ["USE_FIXTURE_LLM"] = "true"

# Bust the @lru_cache on get_settings so subsequent imports see the
# overrides instead of stale .env values.
from agent.config import get_settings

get_settings.cache_clear()
