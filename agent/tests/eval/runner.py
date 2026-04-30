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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from fastapi.testclient import TestClient

from agent.config import get_settings
from agent.main import app


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

    @classmethod
    def load_all(cls) -> list[EvalCase]:
        if not _CASES_DIR.exists():
            return []
        cases: list[EvalCase] = []
        for path in sorted(_CASES_DIR.glob("*.yaml")):
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            cases.append(
                cls(
                    name=data["name"],
                    description=data.get("description", ""),
                    patient_id=int(data.get("patient_id", 1)),
                    user_id=int(data.get("user_id", 1)),
                    messages=data["messages"],
                    expected=data.get("expected", {}),
                    bad_hmac=bool(data.get("bad_hmac", False)),
                    expected_to_fail=bool(data.get("expected_to_fail", False)),
                    live_llm_required=bool(data.get("live_llm_required", False)),
                )
            )
        return cases


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


def run_case(client: TestClient, case: EvalCase, secret: str) -> CaseResult:
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


def run_all() -> list[CaseResult]:
    settings = get_settings()
    client = TestClient(app)
    cases = EvalCase.load_all()
    return [run_case(client, c, settings.openemr_hmac_secret) for c in cases]


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
    real_failures = [r for r in results if not r.passed and not r.case.expected_to_fail]
    expected_failures_seen = [r for r in results if not r.passed and r.case.expected_to_fail]
    passed_count = total - len(real_failures) - len(expected_failures_seen)
    expected_failures_caught = len(expected_failures_seen)

    lines: list[str] = []
    lines.append(f"# Eval run — {timestamp}")
    lines.append("")
    lines.append(f"**Mode:** `{mode}`  ")
    lines.append(f"**Cases:** {total}  ")
    lines.append(f"**Real failures:** {len(real_failures)}  ")
    lines.append(f"**Expected-fail cases that fired:** {expected_failures_caught}  ")
    lines.append(f"**Clean passes:** {passed_count}")
    lines.append("")

    for r in results:
        if r.passed:
            badge = "✅ PASS"
        elif r.case.expected_to_fail:
            badge = "✅ PASS (expected-fail caught)"
        else:
            badge = "❌ FAIL"

        lines.append(f"## {badge} — `{r.case.name}`")
        if r.case.description:
            lines.append("")
            lines.append(r.case.description)
        lines.append("")
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
    results = run_all()
    path = write_report(results)
    print(f"Wrote eval report: {path}")
    real_failures = [r for r in results if not r.passed and not r.case.expected_to_fail]
    if real_failures:
        print(f"\n{len(real_failures)} real failure(s):")
        for r in real_failures:
            print(f"  - {r.case.name}: {r.failures}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
