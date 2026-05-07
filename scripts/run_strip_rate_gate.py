#!/usr/bin/env python3
"""CI strip-rate regression gate for the AgentForge eval suite.

Reads a baseline strip-rate snapshot and a current run's JSON results (from
``python -m agent.tests.eval.runner --output-json <path>``), then exits
non-zero if any case category's verifier strip rate rises more than
MAX_REGRESSION_PP (default 5 percentage points absolute) above the baseline.

Strip rate formula (per category):

    strip_rate = sum(claims_stripped_count) / max(
                     sum(claims_passed_count) + sum(claims_stripped_count), 1)

Only categories present in the BASELINE are checked.  New categories that
appear in the current run (not in the baseline) are reported but do not
fail the gate — they are monitored and added to the baseline on the next
deliberate ``--update-baseline`` run.

Usage (read-only check):
    python scripts/run_strip_rate_gate.py \\
        --baseline agent/tests/eval/strip_rate_baseline.json \\
        --current-json /tmp/eval_results.json

Usage (update baseline):
    python scripts/run_strip_rate_gate.py --update-baseline \\
        --baseline agent/tests/eval/strip_rate_baseline.json \\
        --current-json /tmp/eval_results.json

Called by .github/workflows/agent-eval.yml alongside run_eval_gate.py.

Exit codes:
    0 -- all baseline categories within threshold; any new categories reported
    1 -- one or more categories rose beyond threshold
    2 -- argument / file error

EVAL_SUITE.md §8.  W2_ARCHITECTURE.md §5.5.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path


_DEFAULT_MAX_REGRESSION = 0.05  # 5 percentage points
_BASELINE_KEY = "strip_rates"


def _load_json(path: str) -> dict | list:
    p = Path(path)
    if not p.exists():
        print(f"[strip-rate-gate] ERROR: file not found: {path}", file=sys.stderr)
        sys.exit(2)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"[strip-rate-gate] ERROR: invalid JSON in {path}: {e}", file=sys.stderr)
        sys.exit(2)


def compute_strip_rates(rows: list[dict]) -> dict[str, float]:
    """Aggregate per-case claims counts into strip rates keyed by category.

    Only non-skipped cases with at least one claim (passed or stripped)
    contribute to the denominator.  Skipped cases and cases with zero total
    claims (e.g. refused, extraction-only) are excluded to avoid zero-division
    and false signals.
    """
    passed_by_cat: dict[str, int] = {}
    stripped_by_cat: dict[str, int] = {}
    for row in rows:
        if row.get("skipped"):
            continue
        stripped = row.get("claims_stripped_count", 0) or 0
        passed   = row.get("claims_passed_count",  0) or 0
        if stripped + passed == 0:
            # Extraction-only case, refused case, or case without claims —
            # exclude from strip-rate denominator.
            continue
        cat = row.get("category") or "uncategorized"
        passed_by_cat[cat]   = passed_by_cat.get(cat, 0)   + passed
        stripped_by_cat[cat] = stripped_by_cat.get(cat, 0) + stripped

    rates: dict[str, float] = {}
    for cat in set(passed_by_cat) | set(stripped_by_cat):
        total = passed_by_cat.get(cat, 0) + stripped_by_cat.get(cat, 0)
        rates[cat] = round(stripped_by_cat.get(cat, 0) / max(total, 1), 4)
    return rates


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--baseline", required=True,
        help="Path to strip_rate_baseline.json"
    )
    parser.add_argument(
        "--current-json", required=True, dest="current_json",
        help="Path to JSON written by: python -m agent.tests.eval.runner --output-json <path>"
    )
    parser.add_argument(
        "--max-regression", type=float, default=_DEFAULT_MAX_REGRESSION,
        metavar="FLOAT",
        help=f"Maximum allowed strip-rate rise per category (default: {_DEFAULT_MAX_REGRESSION})"
    )
    parser.add_argument(
        "--update-baseline", action="store_true",
        help="Write the current strip rates to the baseline file and exit 0."
    )
    args = parser.parse_args()

    current_data = _load_json(args.current_json)
    if not isinstance(current_data, list):
        print(
            "[strip-rate-gate] ERROR: --current-json must be a list of case rows "
            "(output of: python -m agent.tests.eval.runner --output-json <path>)",
            file=sys.stderr,
        )
        sys.exit(2)

    current_rates = compute_strip_rates(current_data)

    if args.update_baseline:
        baseline_path = Path(args.baseline)
        # Merge with existing baseline keys (keep keys not in current run).
        existing: dict = {}
        if baseline_path.exists():
            try:
                existing = json.loads(baseline_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass
        existing_rates: dict[str, float] = existing.get(_BASELINE_KEY, {})
        merged = {**existing_rates, **current_rates}
        output = {
            "generated": str(date.today()),
            "note": existing.get("note", ""),
            _BASELINE_KEY: merged,
        }
        baseline_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
        print(f"[strip-rate-gate] Updated baseline: {baseline_path}")
        for cat, rate in sorted(merged.items()):
            print(f"  {cat}: {rate:.1%}")
        sys.exit(0)

    baseline_data = _load_json(args.baseline)
    if not isinstance(baseline_data, dict):
        print("[strip-rate-gate] ERROR: baseline must be a JSON object", file=sys.stderr)
        sys.exit(2)

    baseline_rates: dict[str, float] = baseline_data.get(_BASELINE_KEY, {})
    if not baseline_rates:
        print(
            "[strip-rate-gate] ERROR: baseline has no 'strip_rates' dict",
            file=sys.stderr,
        )
        sys.exit(2)

    print("[strip-rate-gate] Baseline strip rates (per category):")
    for cat in sorted(baseline_rates):
        print(f"  {cat}: {baseline_rates[cat]:.1%}")

    print("[strip-rate-gate] Current strip rates (per category):")
    for cat in sorted(current_rates):
        marker = "" if cat in baseline_rates else "  [NEW]"
        print(f"  {cat}: {current_rates[cat]:.1%}{marker}")

    regressions: list[str] = []
    for cat, baseline_rate in sorted(baseline_rates.items()):
        current_rate = current_rates.get(cat, 0.0)
        # Round to 4 decimal places before comparison to avoid IEEE 754 noise.
        # A rise of exactly max_regression is not flagged — check is strictly >.
        rise = round(current_rate - baseline_rate, 4)
        if rise > args.max_regression:
            regressions.append(
                f"  REGRESSION: {cat}: {baseline_rate:.1%} -> {current_rate:.1%} "
                f"(rise {rise:.1%} > threshold {args.max_regression:.1%})"
            )

    if regressions:
        print(f"\n[strip-rate-gate] FAIL: {len(regressions)} category regression(s):")
        for r in regressions:
            print(r)
        print(
            "\n[strip-rate-gate] A rising strip rate means the verifier is stripping "
            "more claims than the baseline — claim grounding has regressed."
        )
        print(
            "[strip-rate-gate] After fixing the root cause, update the baseline:\n"
            "  python scripts/run_strip_rate_gate.py --update-baseline "
            "--current-json /tmp/eval_results.json "
            "--baseline agent/tests/eval/strip_rate_baseline.json"
        )
        sys.exit(1)

    print(
        f"\n[strip-rate-gate] PASS: all baseline categories within "
        f"{args.max_regression:.1%} strip-rate threshold."
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
