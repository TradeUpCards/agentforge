"""Pytest entry for the eval suite.

Discovers via pytest's default file-name convention. The actual case loading,
HMAC, runner, and markdown report writer live in `runner.py` (also usable
as a CLI: `python -m agent.tests.eval.runner`).

Tier filtering: set the `EVAL_TIER` env var to limit which cases run.
For example, `EVAL_TIER=smoke pytest agent/tests/eval/` runs only
`tier: smoke` cases — the pre-commit hook uses this to keep commits
fast (~6 cases / ~10s) while CI runs the full fixture-mode suite.
Unset (default) means "run every case the other skip rules allow".
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from agent.config import get_settings
from agent.main import app
from agent.tests.eval.runner import EvalCase, run_case


@pytest.mark.parametrize("case", EvalCase.load_all(), ids=lambda c: c.name)
def test_eval_case(case: EvalCase) -> None:
    tier_filter = os.environ.get("EVAL_TIER", "").strip().lower()
    if tier_filter and case.tier != tier_filter:
        pytest.skip(
            f"Case {case.name!r} has tier={case.tier!r}; "
            f"EVAL_TIER={tier_filter!r} requested."
        )
    settings = get_settings()
    if case.live_llm_required and settings.use_fixture_llm:
        pytest.skip(
            f"Case {case.name!r} requires a live LLM; pytest runs in fixture "
            f"mode for determinism. Use the CLI runner against a live LLM."
        )
    if case.live_db_required and settings.use_fixture_data:
        pytest.skip(
            f"Case {case.name!r} requires live Synthea-imported DB data; "
            f"pytest runs against the Maria fixture for determinism."
        )
    if case.fixture_data_required and not settings.use_fixture_data:
        pytest.skip(
            f"Case {case.name!r} is calibrated against Maria fixture data."
        )
    client = TestClient(app)
    result = run_case(client, case, settings.openemr_hmac_secret)

    if case.expected_to_fail:
        assert result.failures, (
            f"Case {case.name!r} marked expected_to_fail but all assertions passed "
            f"— the case is no longer testing what it was designed to test."
        )
    else:
        assert not result.failures, (
            f"Case {case.name!r} failed:\n  " + "\n  ".join(result.failures)
        )
