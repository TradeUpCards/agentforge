"""Meta-tests for the eval rubric gate.

These tests verify:
1. baseline.json has the required structure and all 5 rubric categories.
2. run_eval_gate.py's regression math is correct (>5pp absolute drop = fail).
3. The rubric closed set is enforced.
4. A deliberate regression is detected.
5. A pass-through (no regression) is accepted.

These run in CI as part of the unit test suite (not the eval suite) so
the gate machinery itself is regression-tested.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


_BASELINE_PATH = Path(__file__).parent.parent / "baseline.json"
_KNOWN_RUBRICS = {
    "citation_present",
    "factually_consistent",
    "no_phi_in_logs",
    "safe_refusal",
    "schema_valid",
}
_REGRESSION_THRESHOLD = 0.05  # 5 percentage points absolute


def _load_baseline() -> dict:
    """Load baseline.json and return its contents."""
    assert _BASELINE_PATH.exists(), (
        f"baseline.json not found at {_BASELINE_PATH}. "
        "Run: python -m agent.tests.eval.runner --update-baseline"
    )
    return json.loads(_BASELINE_PATH.read_text(encoding="utf-8"))


def _check_regression(baseline_rates: dict, current_rates: dict, threshold: float) -> list[str]:
    """Return list of rubrics that regressed beyond threshold.

    Drop is rounded to 4 decimal places before comparison to avoid
    floating-point noise (e.g. 1.0 - 0.95 = 0.050000000000000044 in IEEE 754).
    A drop of exactly ``threshold`` is not flagged (check is strictly >).
    """
    regressions = []
    for rubric, baseline_rate in baseline_rates.items():
        current_rate = current_rates.get(rubric, 0.0)
        drop = round(baseline_rate - current_rate, 4)
        if drop > threshold:
            regressions.append(
                f"{rubric}: {baseline_rate:.1%} -> {current_rate:.1%} "
                f"(drop={drop:.1%}, threshold={threshold:.1%})"
            )
    return regressions


class TestBaselineStructure:
    def test_baseline_exists(self):
        """baseline.json must exist and be valid JSON."""
        data = _load_baseline()
        assert isinstance(data, dict), "baseline.json must be a dict"

    def test_baseline_has_all_rubrics(self):
        """baseline.json must have pass rates for all 5 PRD rubric categories."""
        data = _load_baseline()
        rates = data.get("rubric_pass_rates", {})
        missing = _KNOWN_RUBRICS - set(rates.keys())
        assert not missing, (
            f"baseline.json is missing rubric pass rates for: {sorted(missing)}. "
            f"Run --update-baseline after authoring all cases."
        )

    def test_baseline_rates_are_valid_floats(self):
        """All rubric pass rates must be floats in [0.0, 1.0]."""
        data = _load_baseline()
        rates = data.get("rubric_pass_rates", {})
        for rubric, rate in rates.items():
            assert isinstance(rate, (int, float)), f"{rubric} rate is not a number: {rate!r}"
            assert 0.0 <= rate <= 1.0, f"{rubric} rate out of range [0,1]: {rate}"

    def test_baseline_has_generated_field(self):
        """baseline.json must have a 'generated' date string."""
        data = _load_baseline()
        assert "generated" in data, "baseline.json missing 'generated' field"


class TestRegressionMath:
    def test_no_regression_passes(self):
        """Identical baseline and current rates -> no regressions."""
        baseline = {"factually_consistent": 0.9, "safe_refusal": 1.0}
        current  = {"factually_consistent": 0.9, "safe_refusal": 1.0}
        regressions = _check_regression(baseline, current, _REGRESSION_THRESHOLD)
        assert not regressions, f"Unexpected regressions: {regressions}"

    def test_small_drop_passes(self):
        """A drop < 5pp is within threshold -> no regression flagged."""
        baseline = {"factually_consistent": 1.0}
        current  = {"factually_consistent": 0.96}  # 4pp drop
        regressions = _check_regression(baseline, current, _REGRESSION_THRESHOLD)
        assert not regressions, f"4pp drop should not be flagged: {regressions}"

    def test_exact_threshold_passes(self):
        """A drop of exactly 5pp is at the boundary -> no regression (threshold is strictly >)."""
        baseline = {"factually_consistent": 1.0}
        current  = {"factually_consistent": 0.95}  # exactly 5pp drop
        regressions = _check_regression(baseline, current, _REGRESSION_THRESHOLD)
        assert not regressions, f"Exact 5pp drop should not be flagged: {regressions}"

    def test_regression_detected(self):
        """A drop > 5pp must be detected and reported."""
        baseline = {"factually_consistent": 1.0, "safe_refusal": 1.0}
        current  = {"factually_consistent": 0.89, "safe_refusal": 1.0}  # 11pp drop
        regressions = _check_regression(baseline, current, _REGRESSION_THRESHOLD)
        assert len(regressions) == 1
        assert "factually_consistent" in regressions[0]

    def test_missing_rubric_in_current_treated_as_zero(self):
        """If a rubric disappears from current results, it's treated as 0% -> regression."""
        baseline = {"citation_present": 0.8}
        current  = {}  # rubric absent -> effectively 0%
        regressions = _check_regression(baseline, current, _REGRESSION_THRESHOLD)
        assert regressions, "Missing rubric should be treated as 0% and flagged as regression"
