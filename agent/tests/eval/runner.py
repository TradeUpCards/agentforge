"""Eval suite runner — required for early submission per the brief schedule.

Loads YAML case fixtures from `agent/tests/eval/cases/`, invokes the chat
endpoint (in-process via FastAPI's TestClient), evaluates assertions, and
writes a markdown report to `agent/tests/eval/results/<timestamp>.md`.

Run from repo root:
    python -m pytest agent/tests/eval/runner.py -v

Or programmatically (writes a report file):
    python -m agent.tests.eval.runner

Assertion DSL (in each case YAML's `expected:` block):
    status: ok | refused | error             # required
    min_claims: <int>                        # claims_passed >= N
    max_claims: <int>                        # claims_passed <= N
    min_citations: <int>                     # at least N distinct record_ids
    must_mention: [substr1, substr2, ...]    # case-insensitive substrings in message.content
    must_not_mention: [substr1, ...]
    expect_refusal_reason_contains: <substr> # only when status: refused
    expect_tools_called: [name1, name2, ...] # tools whose `success: true` must appear

Cases that intentionally fail their assertions (so the runner reports
non-trivial findings) should set `expected_to_fail: true` so the report
distinguishes "found a real bug" from "the case found what it was looking for".
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from fastapi.testclient import TestClient

from agent._validators import validate_no_real_pii
from agent.config import get_settings
from agent.main import app

# Re-export under the legacy private name so any in-process callers that
# imported it as `runner._validate_no_real_pii` still resolve.
_validate_no_real_pii = validate_no_real_pii


_CASES_DIR = Path(__file__).parent / "cases"
_RESULTS_DIR = Path(__file__).parent / "results"


# ---------------------------------------------------------------------------
# Case loading
# ---------------------------------------------------------------------------


@dataclass
class EvalCase:
    name: str
    description: str
    patient_id: int
    user_id: int
    messages: list[dict[str, str]]
    expected: dict[str, Any]
    category: str = "uncategorized"
    """Label used to slice metrics in the report. One of: happy_path,
    auth_boundary, refusal, edge_case, ambiguous, prompt_injection.
    Defaults to 'uncategorized' when a YAML omits the field."""
    bad_hmac: bool = False
    """If True, send an invalid HMAC to test the auth boundary."""
    expected_to_fail: bool = False
    """If True, an assertion failure indicates the case found what it was
    designed to find (e.g. a deliberately broken claim that the verifier
    catches). Reported as PASS in the markdown report."""
    live_llm_required: bool = False
    """If True, the case only makes sense against a real LLM (not the
    fixture LLM). Pytest skips these in conftest's fixture-mode default;
    the CLI runner runs them whenever USE_FIXTURE_LLM is false."""
    live_db_required: bool = False
    """If True, the case targets a specific Synthea-imported patient and
    only makes sense against the real DB. Skipped when USE_FIXTURE_DATA
    is true (which redirects all queries to the canned Maria fixture)."""
    fixture_data_required: bool = False
    """If True, the case is calibrated against the Maria fixture's
    specific record content (e.g., asserts must_mention 'metformin').
    Skipped when USE_FIXTURE_DATA is false because patient_id=1 in the
    live DB likely has different data."""
    difficulty: str = "basic"
    """One of: smoke, basic, intermediate, advanced. Drives reporting and
    sub-suite selection. Default 'basic' preserves existing case semantics
    for the 11 backfilled cases."""
    tool_mix: list[str] = field(default_factory=list)
    """Declarative list of tools this case is expected to exercise. Used by
    the coverage report to flag tool-coverage holes. Cross-checked against
    expect_tools_called when both are non-empty."""
    failure_mode: str = ""
    """Open-vocabulary granular failure-mode tag, finer-grained than
    category. The runner reports unique values to surface typos. Empty
    string means 'unspecified'; new cases should set it."""
    source_incident_id: str | None = None
    """Free-text reference to a source ticket / trace / decision-doc that
    motivated this case (the trace-to-fixture provenance link the
    observability review flagged as missing). Optional."""
    tier: str = "full"
    """One of: smoke, full, nightly. Drives runner subsetting. Smoke is a
    fast pre-commit subset; full is the CI default; nightly includes
    live-LLM/live-DB cases."""
    synthetic: bool = False
    """True when the case references a synthetic patient fixture (sentinel
    range 999000-999999). Used by the no-real-PHI validator to know which
    fixtures to scan."""

    @classmethod
    def load_all(cls) -> list[EvalCase]:
        if not _CASES_DIR.exists():
            return []
        cases: list[EvalCase] = []
        for path in sorted(_CASES_DIR.glob("*.yaml")):
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            _validate_case_schema(path, data)
            cases.append(
                cls(
                    name=data["name"],
                    description=data.get("description", ""),
                    patient_id=int(data.get("patient_id", 1)),
                    user_id=int(data.get("user_id", 1)),
                    messages=data["messages"],
                    expected=data.get("expected", {}),
                    category=str(data.get("category", "uncategorized")),
                    bad_hmac=bool(data.get("bad_hmac", False)),
                    expected_to_fail=bool(data.get("expected_to_fail", False)),
                    live_llm_required=bool(data.get("live_llm_required", False)),
                    live_db_required=bool(data.get("live_db_required", False)),
                    fixture_data_required=bool(data.get("fixture_data_required", False)),
                    difficulty=str(data.get("difficulty", "basic")),
                    tool_mix=list(data.get("tool_mix", []) or []),
                    failure_mode=str(data.get("failure_mode", "")),
                    source_incident_id=data.get("source_incident_id"),
                    tier=str(data.get("tier", "full")),
                    synthetic=bool(data.get("synthetic", False)),
                )
            )
        return cases


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------
#
# Schema and PII checks per SYNTHETIC_DATA_PLAN.md (approved 2026-05-02).
# _validate_case_schema runs at every load_all() — strict checking catches
# typos in field names, illegal enum values, and DSL inconsistencies before
# they silently degrade coverage.
#
# _validate_no_real_pii is for synthetic patient fixtures (the JSON files
# that will land under agent/fixtures/patients/ in plan step 2). It's a
# conservative regex/blocklist pass; hand-review remains the primary
# control for accidental real-name collisions.

_KNOWN_CASE_KEYS: set[str] = {
    # required
    "name",
    "messages",
    # core optional
    "description",
    "patient_id",
    "user_id",
    "expected",
    # boundary flags
    "bad_hmac",
    "expected_to_fail",
    "live_llm_required",
    "live_db_required",
    "fixture_data_required",
    # taxonomy (existing + extended)
    "category",
    "difficulty",
    "tool_mix",
    "failure_mode",
    "source_incident_id",
    "tier",
    "synthetic",
}

_KNOWN_DIFFICULTIES: set[str] = {"smoke", "basic", "intermediate", "advanced"}
_KNOWN_TIERS: set[str] = {"smoke", "full", "nightly"}

_DSL_CHECKS: set[str] = {
    "min_claims",
    "max_claims",
    "min_citations",
    "must_mention",
    "must_not_mention",
    "expect_refusal_reason_contains",
    "expect_tools_called",
}


def _validate_case_schema(path: Path, data: dict[str, Any]) -> None:
    """Strict schema check at case-load time.

    Enforces SYNTHETIC_DATA_PLAN.md validation rules 1, 2, and 5:
    - unknown top-level keys are rejected (catches typos)
    - difficulty and tier must be in their closed enums
    - tool_mix must be a superset of expect_tools_called when both non-empty
    - expected{} must include at least one deterministic check beyond status
    """
    extra = set(data.keys()) - _KNOWN_CASE_KEYS
    if extra:
        raise ValueError(
            f"{path.name}: unknown top-level keys {sorted(extra)!r}. "
            f"Known keys: {sorted(_KNOWN_CASE_KEYS)!r}"
        )

    diff = data.get("difficulty", "basic")
    if diff not in _KNOWN_DIFFICULTIES:
        raise ValueError(
            f"{path.name}: difficulty {diff!r} not in {sorted(_KNOWN_DIFFICULTIES)!r}"
        )

    tier = data.get("tier", "full")
    if tier not in _KNOWN_TIERS:
        raise ValueError(
            f"{path.name}: tier {tier!r} not in {sorted(_KNOWN_TIERS)!r}"
        )

    expected = data.get("expected", {}) or {}
    has_deterministic = any(k in expected for k in _DSL_CHECKS)
    if not has_deterministic:
        raise ValueError(
            f"{path.name}: expected{{}} must include at least one of "
            f"{sorted(_DSL_CHECKS)!r}; status alone is not sufficient."
        )

    declared_mix = set(data.get("tool_mix", []) or [])
    asserted_tools = set(expected.get("expect_tools_called", []) or [])
    if declared_mix and asserted_tools and not asserted_tools <= declared_mix:
        missing = asserted_tools - declared_mix
        raise ValueError(
            f"{path.name}: expect_tools_called {sorted(missing)!r} not "
            f"declared in tool_mix {sorted(declared_mix)!r}"
        )


# ---------------------------------------------------------------------------
# HMAC helper (matches PHP and Python verifier)
# ---------------------------------------------------------------------------


def _sign(case: EvalCase, secret: str) -> str:
    payload = (
        f"{case.user_id}|{case.patient_id}|"
        + "|".join(m["content"] for m in case.messages)
    )
    return hmac.new(
        secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()


# ---------------------------------------------------------------------------
# Assertion evaluation
# ---------------------------------------------------------------------------


@dataclass
class CaseResult:
    case: EvalCase
    status_code: int
    response: dict[str, Any]
    failures: list[str] = field(default_factory=list)
    skipped: bool = False
    skip_reason: str = ""

    @property
    def passed(self) -> bool:
        return not self.failures


def _evaluate(case: EvalCase, status_code: int, response: dict[str, Any]) -> CaseResult:
    failures: list[str] = []
    expected = case.expected

    expected_status = expected.get("status")
    if expected_status and response.get("status") != expected_status:
        failures.append(
            f"status: expected {expected_status!r}, got {response.get('status')!r}"
        )

    if "min_claims" in expected:
        claims = response.get("claims") or []
        if len(claims) < expected["min_claims"]:
            failures.append(
                f"min_claims: expected >= {expected['min_claims']}, got {len(claims)}"
            )

    if "max_claims" in expected:
        claims = response.get("claims") or []
        if len(claims) > expected["max_claims"]:
            failures.append(
                f"max_claims: expected <= {expected['max_claims']}, got {len(claims)}"
            )

    if "min_citations" in expected:
        claims = response.get("claims") or []
        seen: set[str] = set()
        for c in claims:
            for rid in c.get("source_record_ids", []):
                seen.add(rid)
        if len(seen) < expected["min_citations"]:
            failures.append(
                f"min_citations: expected >= {expected['min_citations']}, "
                f"got {len(seen)}"
            )

    message_content = (response.get("message") or {}).get("content", "")
    lower = message_content.lower()

    for substr in expected.get("must_mention", []) or []:
        if substr.lower() not in lower:
            failures.append(f"must_mention: missing {substr!r}")

    for substr in expected.get("must_not_mention", []) or []:
        if substr.lower() in lower:
            failures.append(f"must_not_mention: contains forbidden {substr!r}")

    if "expect_refusal_reason_contains" in expected:
        reason = response.get("reason", "")
        if expected["expect_refusal_reason_contains"].lower() not in reason.lower():
            failures.append(
                f"expect_refusal_reason_contains: {expected['expect_refusal_reason_contains']!r} "
                f"not in reason {reason!r}"
            )

    expected_tools = expected.get("expect_tools_called", []) or []
    if expected_tools:
        tools = response.get("tools_called") or []
        names_called_ok = {t["tool_name"] for t in tools if t.get("success")}
        missing = [t for t in expected_tools if t not in names_called_ok]
        if missing:
            failures.append(f"expect_tools_called: missing {missing}")

    return CaseResult(case=case, status_code=status_code, response=response, failures=failures)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def _looks_transient(result: CaseResult) -> bool:
    """Detect the LLM-side-blip signature: status=refused with zero
    successful tool calls.

    The agent's correct response to an LLM call failure (timeout, rate
    limit, transient network) is to refuse cleanly without firing
    tools. That is genuinely the right behavior — but for nightly eval
    purposes the same shape looks identical to a hard architectural
    failure. We only treat this signature as flaky when the case's tier
    actually involves a live LLM (nightly); fixture-mode runs that
    refuse with no tool calls are real failures.

    Surfaced by case 09 (synthea_polypharmacy_brief) on 2026-05-02 — see
    SYNTHETIC_DATA_PLAN.md close-out and `.gauntlet/week1/reviews/`.
    """
    resp = result.response
    if resp.get("status") != "refused":
        return False
    tools = resp.get("tools_called") or []
    return not any(t.get("success") for t in tools)


def _run_case_once(client: TestClient, case: EvalCase, secret: str) -> CaseResult:
    sig = "deadbeef" * 8 if case.bad_hmac else _sign(case, secret)
    body = {
        "user_id": case.user_id,
        "patient_id": case.patient_id,
        "hmac": sig,
        "messages": case.messages,
    }
    r = client.post("/chat", json=body)
    try:
        payload = r.json()
    except json.JSONDecodeError:
        payload = {"status": "error", "raw_body": r.text}
    return _evaluate(case, r.status_code, payload)


def run_case(
    client: TestClient,
    case: EvalCase,
    secret: str,
    max_retries: int = 0,
) -> CaseResult:
    """Run a single eval case, optionally retrying on the transient-LLM
    signature. `max_retries` is the number of *additional* attempts after
    the first run — `max_retries=1` means up to two total runs."""
    result = _run_case_once(client, case, secret)
    for attempt in range(max_retries):
        if not _looks_transient(result):
            return result
        time.sleep(2)
        result = _run_case_once(client, case, secret)
    return result


def run_all(tier: str | None = None) -> list[CaseResult]:
    """Run the eval suite, optionally filtered by tier.

    `tier` of None runs every case. A tier value (`smoke`, `full`, or
    `nightly`) filters to cases whose `tier` field matches — useful for
    pre-commit (smoke), CI (smoke+full via two passes), and nightly
    (full + nightly via two passes) workflows.
    """
    settings = get_settings()
    client = TestClient(app)
    cases = EvalCase.load_all()
    results: list[CaseResult] = []
    for case in cases:
        skip_reason: str | None = None
        if tier is not None and case.tier != tier:
            skip_reason = f"tier={case.tier!r} does not match --tier {tier!r}"
        # Skip cases that require a live LLM in fixture-LLM mode, or
        # require live DB data in fixture-data mode. Mirrors the pytest
        # conftest skip logic — without this, Synthea-targeted cases
        # would fail in CI because patient_id 92 returns the Maria
        # fixture (or empty), not Guadalupe's actual chart.
        elif case.live_llm_required and settings.use_fixture_llm:
            skip_reason = "requires live LLM (USE_FIXTURE_LLM=true)"
        elif case.live_db_required and settings.use_fixture_data:
            skip_reason = "requires live DB data (USE_FIXTURE_DATA=true)"
        elif case.fixture_data_required and not settings.use_fixture_data:
            skip_reason = "calibrated against fixture data (USE_FIXTURE_DATA=false)"
        if skip_reason:
            results.append(CaseResult(
                case=case,
                status_code=0,
                response={},
                skipped=True,
                skip_reason=skip_reason,
            ))
            continue
        # Nightly cases hit a live LLM and can flake with the
        # transient signature (see _looks_transient). One retry on
        # that specific shape; smoke and full are deterministic
        # fixture-mode and get no retries.
        retries = 1 if case.tier == "nightly" else 0
        results.append(run_case(client, case, settings.openemr_hmac_secret, max_retries=retries))
    return results


# ---------------------------------------------------------------------------
# Markdown report
# ---------------------------------------------------------------------------


def write_report(results: list[CaseResult], path: Path | None = None) -> Path:
    _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = dt.datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    path = path or (_RESULTS_DIR / f"{timestamp}.md")

    settings = get_settings()
    mode = "fixture" if settings.use_fixture_llm else "live"

    total = len(results)
    skipped = [r for r in results if r.skipped]
    non_skipped = [r for r in results if not r.skipped]
    real_failures = [
        r for r in non_skipped if not r.passed and not r.case.expected_to_fail
    ]
    expected_failures_seen = [
        r for r in non_skipped if not r.passed and r.case.expected_to_fail
    ]
    passed_count = (
        len(non_skipped) - len(real_failures) - len(expected_failures_seen)
    )
    expected_failures_caught = len(expected_failures_seen)

    # Group results by category for the slice-by-scenario section.
    by_category: dict[str, list[CaseResult]] = {}
    for r in results:
        by_category.setdefault(r.case.category, []).append(r)

    lines: list[str] = []
    lines.append(f"# Eval run — {timestamp}")
    lines.append("")
    lines.append(f"**Mode:** `{mode}`  ")
    lines.append(f"**Cases:** {total}  ")
    lines.append(f"**Clean passes:** {passed_count}  ")
    lines.append(f"**Real failures:** {len(real_failures)}  ")
    lines.append(f"**Expected-fail cases that fired:** {expected_failures_caught}  ")
    lines.append(f"**Skipped (live-LLM-only):** {len(skipped)}")
    lines.append("")

    # Per-category pass rate — labeled-scenario surface that lets a
    # reviewer see at a glance which classes of failure mode are passing
    # vs which are gaps.
    if by_category:
        lines.append("## Pass rate by category")
        lines.append("")
        lines.append("| Category | Cases | Passed | Real failures | Skipped |")
        lines.append("|---|---:|---:|---:|---:|")
        for cat in sorted(by_category):
            cat_results = by_category[cat]
            cat_skipped = sum(1 for r in cat_results if r.skipped)
            cat_passed = sum(
                1 for r in cat_results
                if not r.skipped and (r.passed or r.case.expected_to_fail)
            )
            cat_failed = sum(
                1 for r in cat_results
                if not r.skipped and not r.passed and not r.case.expected_to_fail
            )
            lines.append(
                f"| `{cat}` | {len(cat_results)} | {cat_passed} | {cat_failed} | {cat_skipped} |"
            )
        lines.append("")

    # Per-tier slice — drives the smoke/full/nightly subsetting that
    # SYNTHETIC_DATA_PLAN.md step 6 will use for the pre-commit hook.
    by_tier: dict[str, list[CaseResult]] = {}
    for r in results:
        by_tier.setdefault(r.case.tier, []).append(r)
    if by_tier:
        lines.append("## Pass rate by tier")
        lines.append("")
        lines.append("| Tier | Cases | Passed | Real failures | Skipped |")
        lines.append("|---|---:|---:|---:|---:|")
        for tier in ("smoke", "full", "nightly"):
            if tier not in by_tier:
                continue
            tier_results = by_tier[tier]
            tier_skipped = sum(1 for r in tier_results if r.skipped)
            tier_passed = sum(
                1 for r in tier_results
                if not r.skipped and (r.passed or r.case.expected_to_fail)
            )
            tier_failed = sum(
                1 for r in tier_results
                if not r.skipped and not r.passed and not r.case.expected_to_fail
            )
            lines.append(
                f"| `{tier}` | {len(tier_results)} | {tier_passed} | {tier_failed} | {tier_skipped} |"
            )
        lines.append("")

    # Per-difficulty slice — sanity-checks that the suite isn't just smoke
    # cases passing while the hard ones fail.
    by_difficulty: dict[str, list[CaseResult]] = {}
    for r in results:
        by_difficulty.setdefault(r.case.difficulty, []).append(r)
    if by_difficulty:
        lines.append("## Pass rate by difficulty")
        lines.append("")
        lines.append("| Difficulty | Cases | Passed | Real failures | Skipped |")
        lines.append("|---|---:|---:|---:|---:|")
        for diff in ("smoke", "basic", "intermediate", "advanced"):
            if diff not in by_difficulty:
                continue
            diff_results = by_difficulty[diff]
            diff_skipped = sum(1 for r in diff_results if r.skipped)
            diff_passed = sum(
                1 for r in diff_results
                if not r.skipped and (r.passed or r.case.expected_to_fail)
            )
            diff_failed = sum(
                1 for r in diff_results
                if not r.skipped and not r.passed and not r.case.expected_to_fail
            )
            lines.append(
                f"| `{diff}` | {len(diff_results)} | {diff_passed} | {diff_failed} | {diff_skipped} |"
            )
        lines.append("")

    # Failure-mode distribution — surfaces typos and shows coverage spread.
    failure_modes: dict[str, int] = {}
    for r in results:
        fm = r.case.failure_mode or "(unspecified)"
        failure_modes[fm] = failure_modes.get(fm, 0) + 1
    if failure_modes:
        lines.append("## Failure-mode distribution")
        lines.append("")
        lines.append("| Failure mode | Cases |")
        lines.append("|---|---:|")
        for fm in sorted(failure_modes):
            lines.append(f"| `{fm}` | {failure_modes[fm]} |")
        lines.append("")

    # Per-case detail, grouped by category so the report reads as
    # "happy path: ✓✓✓✓; auth: ✓; refusal: ✓; edge case: ✓; ambiguous:
    # ✓; prompt_injection: ✓".
    for cat in sorted(by_category):
        lines.append(f"## Category: `{cat}`")
        lines.append("")
        for r in by_category[cat]:
            if r.skipped:
                badge = "⏭ SKIPPED"
            elif r.passed:
                badge = "✅ PASS"
            elif r.case.expected_to_fail:
                badge = "✅ PASS (expected-fail caught)"
            else:
                badge = "❌ FAIL"

            lines.append(f"### {badge} — `{r.case.name}`")
            if r.case.description:
                lines.append("")
                lines.append(r.case.description)
            lines.append("")
            if r.skipped:
                lines.append(f"- Skip reason: {r.skip_reason}")
            else:
                lines.append(f"- HTTP status: `{r.status_code}`")
                lines.append(f"- Response status: `{r.response.get('status')}`")
                if r.failures:
                    lines.append("- Assertion failures:")
                    for f in r.failures:
                        lines.append(f"  - `{f}`")
            lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# Pytest entry lives in test_eval_cases.py (file-name discovery).
#
# ---------------------------------------------------------------------------
# CLI entrypoint — generates a report
# ---------------------------------------------------------------------------


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tier",
        choices=("smoke", "full", "nightly"),
        default=None,
        help="Run only cases at this tier. Default: all tiers.",
    )
    args = parser.parse_args()

    results = run_all(tier=args.tier)
    path = write_report(results)
    skipped = [r for r in results if r.skipped]
    real_failures = [
        r for r in results
        if not r.skipped and not r.passed and not r.case.expected_to_fail
    ]
    print(f"Wrote eval report: {path}")
    if args.tier:
        print(f"Filtered to tier={args.tier!r} — {len(results) - len(skipped)} case(s) ran.")
    if skipped:
        print(f"\n{len(skipped)} case(s) skipped:")
        for r in skipped:
            print(f"  - {r.case.name} ({r.skip_reason})")
    if real_failures:
        print(f"\n{len(real_failures)} real failure(s):")
        for r in real_failures:
            print(f"  - {r.case.name}: {r.failures}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
