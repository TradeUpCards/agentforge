"""Test environment overrides.

Eval cases (01-06) and chat-endpoint integration tests were calibrated
against the Maria Hernandez fixture and the canned LLM response. They
need both `USE_FIXTURE_DATA=true` and `USE_FIXTURE_LLM=true` for
deterministic, free, fast runs in CI.

Runtime production flips both to `false` (live LLM, real DB queries).
The eval framework's CLI (`python -m agent.tests.eval.runner`) honors
.env settings — use that for live-mode evaluation against the real DB.

Live integration tests (those marked `@pytest.mark.live`) make real API
calls (Anthropic, Cohere, etc.) and are SKIPPED by default so ordinary
CI runs do not incur accidental cost.  Pass `--live` to opt in:

    pytest agent/tests/integration/ --live -v
"""

from __future__ import annotations

import os

import pytest

# Set BEFORE any agent module is imported so config.py picks them up.
os.environ["USE_FIXTURE_DATA"] = "true"
os.environ["USE_FIXTURE_LLM"] = "true"

# Bust the @lru_cache on get_settings so subsequent imports see the
# overrides instead of stale .env values.
from agent.config import get_settings

get_settings.cache_clear()


# ---------------------------------------------------------------------------
# --live opt-in marker (Fix #A, Stage-3b round-3)
# ---------------------------------------------------------------------------


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--live",
        action="store_true",
        default=False,
        help="run live integration tests that hit real APIs (Anthropic, Cohere, etc.)",
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    if config.getoption("--live"):
        return  # opt-in granted; let live-marked tests run
    skip_live = pytest.mark.skip(
        reason="--live not provided; pass --live to run live API tests"
    )
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)
