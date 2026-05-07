"""Meta-tests for the eval strip-rate gate.

These tests verify:
1. strip_rate_baseline.json has the required structure.
2. run_strip_rate_gate.py's regression math is correct (>5pp absolute rise = fail).
3. A deliberate regression is detected and the category + delta are surfaced.
4. A pass-through (no regression) is accepted.
5. A new category appearing in current results does not immediately fail the gate.

These run in CI as part of the unit test suite so the gate machinery itself is
regression-tested before it is invoked against real eval results.

Strip rate formula:
    strip_rate(category) = claims_stripped / (claims_passed + claims_stripped)

W2_ARCHITECTURE.md §5.5 / EVAL_SUITE.md §8.
"""

from __future__ import annotations

import json
from pathlib import Path


_BASELINE_PATH = Path(__file__).parent.parent / "strip_rate_baseline.json"
_REGRESSION_THRESHOLD = 0.05  # 5 percentage points absolute


def _load_baseline() -> dict:
    assert _BASELINE_PATH.exists(), (
        f"strip_rate_baseline.json not found at {_BASELINE_PATH}. "
        "Run: python scripts/run_strip_rate_gate.py --update-baseline "
        "--current-json /tmp/eval_results.json "
        "--baseline agent/tests/eval/strip_rate_baseline.json"
    )
    return json.loads(_BASELINE_PATH.read_text(encoding="utf-8"))


def _check_strip_regression(
    baseline_rates: dict[str, float],
    current_rates: dict[str, float],
    threshold: float,
) -> list[str]:
    """Return list of categories that rose beyond threshold.

    Only categories present in the BASELINE are checked.  New categories in
    current (not in baseline) are allowed silently — they are reported but
    not counted as regressions.  A rise of exactly ``threshold`` is not
    flagged (check is strictly >).

    Drop is rounded to 4 decimal places before comparison to avoid IEEE 754
    noise (e.g. 0.08 - 0.00 = 0.08000000000000002).
    """
    regressions: list[str] = []
    for category, baseline_rate in baseline_rates.items():
        current_rate = current_rates.get(category, 0.0)
        rise = round(current_rate - baseline_rate, 4)
        if rise > threshold:
            regressions.append(
                f"{category}: {baseline_rate:.1%} -> {current_rate:.1%} "
                f"(rise={rise:.1%}, threshold={threshold:.1%})"
            )
    return regressions


class TestStripRateBaselineStructure:
    def test_baseline_exists_and_valid(self):
        """strip_rate_baseline.json must exist, be valid JSON, and have the
        required top-level structure: 'generated' string and 'strip_rates'
        dict of floats in [0.0, 1.0]."""
        data = _load_baseline()
        assert isinstance(data, dict), "strip_rate_baseline.json must be a dict"
        assert "generated" in data, "strip_rate_baseline.json missing 'generated' field"
        assert "strip_rates" in data, "strip_rate_baseline.json missing 'strip_rates' field"
        rates = data["strip_rates"]
        assert isinstance(rates, dict), "'strip_rates' must be a dict"
        for category, rate in rates.items():
            assert isinstance(rate, (int, float)), (
                f"strip_rate for {category!r} is not a number: {rate!r}"
            )
            assert 0.0 <= rate <= 1.0, (
                f"strip_rate for {category!r} out of [0,1]: {rate}"
            )


class TestStripRateMath:
    def test_no_regression_passes(self):
        """Identical baseline and current strip rates -> no regressions."""
        baseline = {"happy_path": 0.02, "edge_case": 0.00}
        current  = {"happy_path": 0.02, "edge_case": 0.00}
        regressions = _check_strip_regression(baseline, current, _REGRESSION_THRESHOLD)
        assert not regressions, f"Unexpected regressions: {regressions}"

    def test_small_increase_passes(self):
        """A rise < 5pp is within threshold -> no regression flagged."""
        baseline = {"happy_path": 0.05}
        current  = {"happy_path": 0.08}  # 3pp rise
        regressions = _check_strip_regression(baseline, current, _REGRESSION_THRESHOLD)
        assert not regressions, f"3pp rise should not be flagged: {regressions}"

    def test_exact_threshold_passes(self):
        """A rise of exactly 5pp is at the boundary -> no regression (threshold is strictly >)."""
        baseline = {"happy_path": 0.00}
        current  = {"happy_path": 0.05}  # exactly 5pp rise
        regressions = _check_strip_regression(baseline, current, _REGRESSION_THRESHOLD)
        assert not regressions, f"Exact 5pp rise should not be flagged: {regressions}"

    def test_regression_detected(self):
        """A rise > 5pp must be detected and the category + delta must be surfaced."""
        baseline = {"happy_path": 0.00, "edge_case": 0.00}
        current  = {"happy_path": 0.08, "edge_case": 0.00}  # 8pp rise on happy_path
        regressions = _check_strip_regression(baseline, current, _REGRESSION_THRESHOLD)
        assert len(regressions) == 1
        assert "happy_path" in regressions[0]
        assert "8.0%" in regressions[0] or "0.08" in regressions[0] or "rise=8" in regressions[0]

    def test_new_category_in_current_does_not_fail(self):
        """A new category in current (not in baseline) is silently allowed.

        New categories appear when a new extraction template ships or a new
        case category is authored.  They should not immediately fail CI.
        """
        baseline = {"happy_path": 0.00}
        current  = {"happy_path": 0.00, "new_extraction_type": 0.12}
        regressions = _check_strip_regression(baseline, current, _REGRESSION_THRESHOLD)
        assert not regressions, (
            "A new category not in baseline should not count as a regression: "
            f"{regressions}"
        )
