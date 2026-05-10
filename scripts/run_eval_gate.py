#!/usr/bin/env python3
"""CI rubric regression gate for the AgentForge eval suite.

Reads a baseline pass-rate snapshot and a current run's JSON results,
then exits non-zero if any rubric category regresses by more than
MAX_REGRESSION_PP (default 5 percentage points absolute).

Usage:
    python scripts/run_eval_gate.py \\
        --baseline agent/tests/eval/baseline.json \\
        --current  /tmp/eval_results.json \\
        --max-regression 0.05

Called by .github/workflows/agent-eval.yml after the pytest eval run.
Called by .gitlab-ci.yml (when: manual, runner provisioned).

Exit codes:
    0 -- all rubrics at or above baseline (within threshold)
    1 -- one or more rubrics regressed beyond threshold
    2 -- argument / file error

W2_ARCHITECTURE.md §5.1 / §5.5.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


_DEFAULT_MAX_REGRESSION = 0.05  # 5 percentage points


def load_json(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        print(f"[eval-gate] ERROR: file not found: {path}", file=sys.stderr)
        sys.exit(2)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"[eval-gate] ERROR: invalid JSON in {path}: {e}", file=sys.stderr)
        sys.exit(2)


def compute_rubric_rates(results_rows: list[dict]) -> dict[str, float]:
    """Aggregate per-case rubric_results into overall pass rates."""
    totals: dict[str, int] = {}
    passes: dict[str, int] = {}
    for row in results_rows:
        for rubric, passed in (row.get("rubric_results") or {}).items():
            totals[rubric] = totals.get(rubric, 0) + 1
            if passed:
                passes[rubric] = passes.get(rubric, 0) + 1
    return {
        rubric: round(passes.get(rubric, 0) / total, 4)
        for rubric, total in totals.items()
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, help="Path to baseline.json")
    parser.add_argument(
        "--current", required=True,
        help="Path to JSON file written by: python -m agent.tests.eval.runner --output-json <path>"
    )
    parser.add_argument(
        "--max-regression", type=float, default=_DEFAULT_MAX_REGRESSION,
        metavar="FLOAT",
        help=f"Maximum allowed pass-rate drop per rubric (default: {_DEFAULT_MAX_REGRESSION})"
    )
    args = parser.parse_args()

    baseline_data = load_json(args.baseline)
    current_data = load_json(args.current)

    baseline_rates: dict[str, float] = baseline_data.get("rubric_pass_rates", {})
    if not baseline_rates:
        print("[eval-gate] ERROR: baseline.json has no rubric_pass_rates", file=sys.stderr)
        sys.exit(2)

    # current_data is either a list[dict] (from --output-json) or a dict with rubric_pass_rates
    if isinstance(current_data, list):
        current_rates = compute_rubric_rates(current_data)
    else:
        current_rates = current_data.get("rubric_pass_rates", {})

    print("[eval-gate] Baseline rubric pass rates:")
    for rubric in sorted(baseline_rates):
        print(f"  {rubric}: {baseline_rates[rubric]:.1%}")

    print("[eval-gate] Current rubric pass rates:")
    for rubric in sorted(current_rates):
        print(f"  {rubric}: {current_rates[rubric]:.1%}")

    # Check 1: relative regression — current rate dropped > max_regression vs baseline
    regressions: list[str] = []
    for rubric, baseline_rate in sorted(baseline_rates.items()):
        current_rate = current_rates.get(rubric, 0.0)
        # Round to 4 decimal places before comparison to avoid IEEE 754 noise
        # (e.g. 1.0 - 0.95 = 0.050000000000000044). A drop of exactly
        # max_regression is not flagged -- the check is strictly >.
        drop = round(baseline_rate - current_rate, 4)
        if drop > args.max_regression:
            regressions.append(
                f"  REGRESSION: {rubric}: {baseline_rate:.1%} -> {current_rate:.1%} "
                f"(drop {drop:.1%} > threshold {args.max_regression:.1%})"
            )

    # Check 2: absolute floor — current rate below min_pass_rate[rubric]
    # Encodes PRD line 136: "drops below the pass threshold".
    min_pass_rates: dict[str, float] = baseline_data.get("min_pass_rate", {})
    floor_failures: list[str] = []
    if min_pass_rates:
        print("[eval-gate] Min-pass-rate floor per rubric:")
        for rubric in sorted(min_pass_rates):
            print(f"  {rubric}: {min_pass_rates[rubric]:.1%}")
        for rubric, floor in sorted(min_pass_rates.items()):
            current_rate = current_rates.get(rubric, 0.0)
            if current_rate < floor:
                floor_failures.append(
                    f"  FLOOR BREACH: {rubric}: {current_rate:.1%} < floor {floor:.1%}"
                )
    else:
        print("[eval-gate] No min_pass_rate floor defined in baseline.json (skipping floor check).")

    all_failures = regressions + floor_failures
    if all_failures:
        if regressions:
            print(f"\n[eval-gate] FAIL: {len(regressions)} rubric regression(s) detected:")
            for r in regressions:
                print(r)
        if floor_failures:
            print(f"\n[eval-gate] FAIL: {len(floor_failures)} rubric floor breach(es) detected:")
            for f in floor_failures:
                print(f)
        print("\n[eval-gate] Run: python -m agent.tests.eval.runner --update-baseline")
        print("[eval-gate] to update the baseline after intentional improvements.")
        sys.exit(1)

    print(f"\n[eval-gate] PASS: All rubrics within {args.max_regression:.1%} regression threshold.")
    if min_pass_rates:
        print("[eval-gate] PASS: All rubrics at or above min_pass_rate floor.")
    sys.exit(0)


if __name__ == "__main__":
    main()
