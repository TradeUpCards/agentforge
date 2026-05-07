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
    phi_log_scan: [str, ...]                 # PHI strings that must be masked by the scrubber
    min_guideline_citations: <int>           # citations with source_type == "guideline" >= N
    expect_extraction_n_results_gte: <int>   # extraction.results list length >= N
    expect_extraction_field: {...}           # named field in extraction equals a value

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
    rubric: list[str] = field(default_factory=list)
    """PRD §6 boolean rubric categories this case exercises. Closed set:
    schema_valid, citation_present, factually_consistent, safe_refusal, no_phi_in_logs.
    Runner aggregates per-rubric pass rates for baseline.json comparison."""
    doc_type: str | None = None
    """If set, runner calls POST /attach_and_extract instead of POST /chat.
    Must be one of: lab_pdf, intake_form."""
    phi_log_scan: list[str] = field(default_factory=list)
    """Strings the PHI scrubber must mask. Each entry is asserted to be absent
    from mask_observability_patterns(entry) output."""

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
                    messages=data.get("messages") or [],
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
                    rubric=list(data.get("rubric", []) or []),
                    doc_type=data.get("doc_type"),
                    phi_log_scan=list(data.get("phi_log_scan", []) or []),
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
    # W2 additions
    "rubric",        # list[str] — PRD §6 rubric categories this case exercises
    "doc_type",      # str | None — if set, case calls /attach_and_extract instead of /chat
    "phi_log_scan",  # list[str] — PHI strings that must be masked by the scrubber
}

_KNOWN_DIFFICULTIES: set[str] = {"smoke", "basic", "intermediate", "advanced"}
_KNOWN_TIERS: set[str] = {"smoke", "full", "nightly"}
_KNOWN_RUBRICS: set[str] = {
    "schema_valid", "citation_present", "factually_consistent",
    "safe_refusal", "no_phi_in_logs",
}

_DSL_CHECKS: set[str] = {
    "min_claims",
    "max_claims",
    "min_citations",
    "must_mention",
    "must_not_mention",
    "expect_refusal_reason_contains",
    "expect_tools_called",
    "phi_log_scan",
    "min_guideline_citations",
    "expect_extraction_n_results_gte",
    "expect_extraction_field",
}


def _validate_case_schema(path: Path, data: dict[str, Any]) -> None:
    """Strict schema check at case-load time.

    Enforces SYNTHETIC_DATA_PLAN.md validation rules 1, 2, and 5:
    - unknown top-level keys are rejected (catches typos)
    - difficulty and tier must be in their closed enums
    - rubric values must be in the closed set of known rubric names
    - tool_mix must be a superset of expect_tools_called when both non-empty
    - expected{} must include at least one deterministic check beyond status
      (or phi_log_scan at top level when doc_type is set)
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

    # Validate rubric values against the closed set.
    for r in data.get("rubric", []) or []:
        if r not in _KNOWN_RUBRICS:
            raise ValueError(
                f"{path.name}: unknown rubric {r!r}. Known: {sorted(_KNOWN_RUBRICS)!r}"
            )

    expected = data.get("expected", {}) or {}
    # phi_log_scan at top level also satisfies the deterministic-check requirement
    # (it is not inside expected{}, but it is a meaningful assertion).
    has_top_level_phi_scan = bool(data.get("phi_log_scan"))
    has_deterministic = any(k in expected for k in _DSL_CHECKS) or has_top_level_phi_scan
    if not has_deterministic:
        raise ValueError(
            f"{path.name}: expected{{}} must include at least one of "
            f"{sorted(_DSL_CHECKS)!r}; status alone is not sufficient."
        )

    # When doc_type is present, messages is optional (defaults to []).
    # No extra validation needed here — messages absence is handled in load_all().

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


def _sign(case: EvalCase, secret: str, timestamp: int) -> str:
    """Replay-protected signing: include the unix timestamp inside the
    payload (must match the layout in agent.py:verify_hmac). Caller is
    responsible for passing the same `timestamp` it puts in the request
    body — the agent rejects any mismatch as a signature failure."""
    payload = (
        f"{case.user_id}|{case.patient_id}|{timestamp}|"
        + "|".join(m["content"] for m in case.messages)
    )
    return hmac.new(
        secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def _sign_attach(
    user_id: int,
    patient_id: int,
    doc_ref_id: str,
    doc_type: str,
    timestamp: int,
    file_sha256_hex: str,
    secret: str,
) -> str:
    """HMAC for /attach_and_extract — distinct payload from /chat.
    Mirrors the verification logic in agent/main.py:attach_and_extract_endpoint.
    """
    payload = f"{user_id}|{patient_id}|{doc_ref_id}|{doc_type}|{timestamp}|{file_sha256_hex}"
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
    latency_ms: int = 0
    """Wall time (ms) for this case's HTTP round-trip — measured at
    `_run_case_once`. 0 for skipped cases. Surfaced in the eval report
    so per-case latency regressions are visible without leaving the
    runner output."""
    rubric_results: dict[str, bool] = field(default_factory=dict)
    """Per-rubric pass/fail for this case. Keys are rubric names from case.rubric.
    True = passed, False = failed. Populated by _evaluate()."""

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

    # --- W2 DSL additions ---

    # phi_log_scan: assert each string is masked by the outbound PHI scrubber
    if case.phi_log_scan:
        from agent._phi_scrubber import mask_observability_patterns
        for phi_string in case.phi_log_scan:
            scrubbed = mask_observability_patterns(phi_string)
            if phi_string.lower() in scrubbed.lower():
                failures.append(
                    f"phi_log_scan: {phi_string!r} not masked by scrubber "
                    f"(got: {scrubbed!r})"
                )

    # min_guideline_citations: assert AgentResponse.citations has N+ guideline entries
    if "min_guideline_citations" in expected:
        citations = response.get("citations") or []
        guideline_cites = [
            c for c in citations
            if isinstance(c, dict) and c.get("source_type") == "guideline"
        ]
        if len(guideline_cites) < expected["min_guideline_citations"]:
            failures.append(
                f"min_guideline_citations: expected >= {expected['min_guideline_citations']}, "
                f"got {len(guideline_cites)}"
            )

    # expect_extraction_n_results_gte: assert extraction.results has >= N entries (lab_pdf)
    if "expect_extraction_n_results_gte" in expected:
        extraction = response.get("extraction") or {}
        results_list = extraction.get("results") or []
        n = expected["expect_extraction_n_results_gte"]
        if len(results_list) < n:
            failures.append(
                f"expect_extraction_n_results_gte: expected >= {n} results, "
                f"got {len(results_list)}"
            )

    # expect_extraction_field: assert a named field in extraction equals a value
    # Format: {"field_path": "results.0.value", "expected_value": 7.8}
    # OR:     {"field_path": "allergies.0.substance", "expected_value": "Penicillin"}
    if "expect_extraction_field" in expected:
        extraction = response.get("extraction") or {}
        field_checks = expected["expect_extraction_field"]
        if not isinstance(field_checks, list):
            field_checks = [field_checks]
        for field_check in field_checks:
            field_path: str = field_check.get("field_path", "")
            expected_val = field_check.get("expected_value")
            # Navigate the path: "results.0.value" → extraction["results"][0]["value"]
            obj: Any = extraction
            ok = True
            for part in field_path.split("."):
                if obj is None:
                    ok = False
                    break
                if isinstance(obj, list):
                    try:
                        obj = obj[int(part)]
                    except (IndexError, ValueError):
                        ok = False
                        break
                elif isinstance(obj, dict):
                    obj = obj.get(part)
                else:
                    ok = False
                    break
            if not ok or obj != expected_val:
                failures.append(
                    f"expect_extraction_field: {field_path!r} expected {expected_val!r}, "
                    f"got {obj!r}"
                )

    # Compute per-rubric pass/fail
    rubric_results: dict[str, bool] = {}
    case_passed = not failures
    for r in case.rubric:
        rubric_results[r] = case_passed

    result = CaseResult(
        case=case, status_code=status_code, response=response,
        failures=failures, rubric_results=rubric_results,
    )
    return result


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


def _run_extraction_case(client: TestClient, case: EvalCase, secret: str) -> CaseResult:
    """Dispatch to POST /attach_and_extract for doc_type cases.

    In test environments, the extractor is mocked via conftest.py's
    mock_extraction_async fixture, so no real Docling/Haiku call happens.
    The mock reads from agent/fixtures/patients/<patient_id>_<doc_type>.json
    and returns the parsed LabReport or IntakeForm directly.
    """
    import hashlib as _hashlib
    # Use a minimal synthetic PDF bytes (just a PDF header) so file_sha256_hex
    # is stable and deterministic in fixture mode.
    _FIXTURE_PDF_BYTES = b"%PDF-1.4 fixture"
    file_sha256_hex = _hashlib.sha256(_FIXTURE_PDF_BYTES).hexdigest()
    doc_ref_id = f"docref-{case.patient_id}-{case.doc_type}"

    timestamp = int(time.time())
    sig = _sign_attach(
        user_id=case.user_id,
        patient_id=case.patient_id,
        doc_ref_id=doc_ref_id,
        doc_type=case.doc_type,
        timestamp=timestamp,
        file_sha256_hex=file_sha256_hex,
        secret=secret,
    )
    t0 = time.perf_counter()
    r = client.post(
        "/attach_and_extract",
        data={
            "patient_id": str(case.patient_id),
            "doc_ref_id": doc_ref_id,
            "doc_type": case.doc_type,
        },
        files={"file": ("fixture.pdf", _FIXTURE_PDF_BYTES, "application/pdf")},
        headers={
            "X-OpenEMR-User-Id": str(case.user_id),
            "X-OpenEMR-Timestamp": str(timestamp),
            "X-OpenEMR-HMAC": sig,
        },
    )
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    try:
        payload = r.json()
    except json.JSONDecodeError:
        payload = {"status": "error", "raw_body": r.text}
    result = _evaluate(case, r.status_code, payload)
    result.latency_ms = elapsed_ms
    return result


def _run_case_once(client: TestClient, case: EvalCase, secret: str) -> CaseResult:
    if case.doc_type is not None:
        return _run_extraction_case(client, case, secret)
    # --- existing /chat path (unchanged) ---
    # Sign with current timestamp so the request stays inside the agent's
    # 30s freshness window. Replay-protection coverage proper lives in the
    # verify_hmac unit tests; here we just need the sig + body to validate.
    timestamp = int(time.time())
    sig = "deadbeef" * 8 if case.bad_hmac else _sign(case, secret, timestamp)
    body = {
        "user_id": case.user_id,
        "patient_id": case.patient_id,
        "timestamp": timestamp,
        "hmac": sig,
        "messages": case.messages,
    }
    t0 = time.perf_counter()
    r = client.post("/chat", json=body)
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    try:
        payload = r.json()
    except json.JSONDecodeError:
        payload = {"status": "error", "raw_body": r.text}
    result = _evaluate(case, r.status_code, payload)
    result.latency_ms = elapsed_ms
    return result


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


def _apply_extraction_mock_if_needed():
    """Context manager that applies the fixture-extraction mock when
    USE_FIXTURE_EXTRACTION=true.  Mirrors conftest.py's autouse fixture so
    the CLI runner (``python -m agent.tests.eval.runner``) honours the same
    env flag as the pytest path.

    Usage::

        with _apply_extraction_mock_if_needed():
            results = run_all()
    """
    import contextlib
    import os

    if os.environ.get("USE_FIXTURE_EXTRACTION", "").lower() != "true":
        return contextlib.nullcontext()

    from unittest.mock import patch
    import json as _json
    from pathlib import Path as _Path
    from agent.document_schemas import LabReport as _LabReport, IntakeForm as _IntakeForm

    _fixtures_dir = _Path(__file__).parent.parent.parent / "fixtures" / "patients"

    async def _mock_async(
        patient_id: int,
        doc_ref_id: str,
        doc_type: str,
        pdf_path: object = None,
        *,
        stage1_only: bool = False,
    ) -> _LabReport | _IntakeForm:
        pattern = f"{patient_id}_{doc_type}*.json"
        matches = sorted(_fixtures_dir.glob(pattern))
        if not matches:
            raise FileNotFoundError(
                f"No extraction fixture for patient_id={patient_id}, doc_type={doc_type!r}"
            )
        data = _json.loads(matches[0].read_text(encoding="utf-8"))
        if doc_type == "lab_pdf":
            return _LabReport.model_validate(data)
        if doc_type == "intake_form":
            return _IntakeForm.model_validate(data)
        raise ValueError(f"Unknown doc_type {doc_type!r}")

    import contextlib as _contextlib

    @_contextlib.contextmanager
    def _ctx():
        with patch(
            "agent.extractors.attach_and_extract_async",
            side_effect=_mock_async,
        ), patch(
            "agent.main.attach_and_extract_async",
            side_effect=_mock_async,
        ):
            yield

    return _ctx()


def run_all(tier: str | None = None) -> list[CaseResult]:
    """Run the eval suite, optionally filtered by tier.

    `tier` of None runs every case. A tier value (`smoke`, `full`, or
    `nightly`) filters to cases whose `tier` field matches — useful for
    pre-commit (smoke), CI (smoke+full via two passes), and nightly
    (full + nightly via two passes) workflows.

    When USE_FIXTURE_EXTRACTION=true, applies the extraction fixture mock
    (same as conftest.py's autouse fixture) so CLI-mode runs and pytest
    runs behave identically for doc_type cases.
    """
    settings = get_settings()
    client = TestClient(app)
    cases = EvalCase.load_all()
    results: list[CaseResult] = []

    with _apply_extraction_mock_if_needed():
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

    # Per-rubric pass rates (PRD §6 boolean rubric categories).
    rubric_totals: dict[str, int] = {}
    rubric_passes: dict[str, int] = {}
    for r in results:
        for rubric_name, passed in r.rubric_results.items():
            rubric_totals[rubric_name] = rubric_totals.get(rubric_name, 0) + 1
            if passed:
                rubric_passes[rubric_name] = rubric_passes.get(rubric_name, 0) + 1

    if rubric_totals:
        lines.append("## Pass rate by rubric (PRD §6)")
        lines.append("")
        lines.append("| Rubric | Pass | Total | Rate |")
        lines.append("|---|---:|---:|---:|")
        for rubric_name in sorted(rubric_totals):
            total_r = rubric_totals[rubric_name]
            passes_r = rubric_passes.get(rubric_name, 0)
            rate = passes_r / total_r if total_r else 0.0
            lines.append(f"| `{rubric_name}` | {passes_r} | {total_r} | {rate:.1%} |")
        lines.append("")

    # Latency — surfaces per-case wall-time so regressions are visible
    # without leaving the runner output. Skipped cases are excluded
    # because their latency is 0 (request never fired).
    timed = [r for r in results if not r.skipped]
    if timed:
        lat_total = sum(r.latency_ms for r in timed)
        lat_avg = lat_total // len(timed)
        lat_max = max(r.latency_ms for r in timed)
        lat_min = min(r.latency_ms for r in timed)
        # Sorted slowest-first so the worst offenders are at the top.
        slowest = sorted(timed, key=lambda r: r.latency_ms, reverse=True)[:10]
        lines.append("## Latency")
        lines.append("")
        lines.append(f"**Cases timed:** {len(timed)}  ")
        lines.append(f"**Total wall time:** {lat_total} ms  ")
        lines.append(
            f"**Per-case:** avg {lat_avg} ms · min {lat_min} ms · max {lat_max} ms"
        )
        lines.append("")
        lines.append(f"### Slowest {len(slowest)} cases")
        lines.append("")
        lines.append("| Case | Latency (ms) | Tier | Status |")
        lines.append("|---|---:|---|---|")
        for r in slowest:
            status = "skipped" if r.skipped else (
                "pass" if r.passed or r.case.expected_to_fail else "fail"
            )
            lines.append(
                f"| `{r.case.name}` | {r.latency_ms} | `{r.case.tier}` | {status} |"
            )
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


def write_baseline(results: list[CaseResult], path: Path | None = None) -> Path:
    """Write per-rubric pass rates to baseline.json.

    Called by --update-baseline. CI compares each run's rubric pass rates
    against this file and fails if any rubric regresses by > 5% (W2_ARCHITECTURE.md §5.1).
    """
    baseline_path = path or (Path(__file__).parent / "baseline.json")

    rubric_totals: dict[str, int] = {}
    rubric_passes: dict[str, int] = {}
    for r in results:
        for rubric_name, passed in r.rubric_results.items():
            rubric_totals[rubric_name] = rubric_totals.get(rubric_name, 0) + 1
            if passed:
                rubric_passes[rubric_name] = rubric_passes.get(rubric_name, 0) + 1

    rubric_pass_rates: dict[str, float] = {}
    for rubric_name, total in rubric_totals.items():
        passes = rubric_passes.get(rubric_name, 0)
        rubric_pass_rates[rubric_name] = round(passes / total, 4) if total else 0.0

    non_skipped = [r for r in results if not r.skipped]
    payload = {
        "generated": dt.datetime.now().strftime("%Y-%m-%d"),
        "total_cases": len(non_skipped),
        "rubric_pass_rates": rubric_pass_rates,
    }
    baseline_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return baseline_path


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
    parser.add_argument(
        "--output-json",
        metavar="PATH",
        default=None,
        help="Write full results as JSON to this path (in addition to the markdown report).",
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        default=False,
        help=(
            "Write per-rubric pass rates to agent/tests/eval/baseline.json. "
            "Use after a known-good run to update the CI regression threshold baseline. "
            "W2_ARCHITECTURE.md §5.1 / §5.3."
        ),
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

    if args.output_json:
        import dataclasses
        output_path = Path(args.output_json)
        # Serialize results to JSON — skip non-serialisable case reference,
        # emit only what downstream tooling needs.
        rows = []
        for r in results:
            rows.append({
                "name": r.case.name,
                "category": r.case.category,
                "tier": r.case.tier,
                "rubric": r.case.rubric,
                "passed": r.passed,
                "skipped": r.skipped,
                "skip_reason": r.skip_reason,
                "failures": r.failures,
                "rubric_results": r.rubric_results,
                "latency_ms": r.latency_ms,
            })
        output_path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
        print(f"Wrote JSON results: {output_path}")

    if args.update_baseline:
        baseline_path = write_baseline(results)
        print(f"Updated baseline: {baseline_path}")

    if real_failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
