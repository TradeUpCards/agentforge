"""Unit tests for the W2 graph-phase responder node.

The responder node lives at agent/graph/workers/responder.py (NEW — authored
by the agent-rag teammate). These tests are authored by the quality-lead and
specify the behavioral contract the responder must satisfy.

Tests will be BLOCKED (ImportError) until agent-rag lands responder.py and
agent/_synthesis.py. Each test documents its blocker explicitly so the CI
output identifies which symbol is missing.

W2_ARCHITECTURE.md §3 (responder role), decision #11 (outbound PHI gate in
responder), decision #12 (synthesize_with_verifier extracted helper), decision
#14 (7-field observability), decision #15 (escalated_to_sonnet field).

No-PHI discipline: all patient_ids are sentinel range 999100-999199.
No real names, MRNs, dates, or addresses in any fixture.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.graph.state import SupervisorState
from agent.schemas import AgentResponse, Citation, Claim, ClaimType, RefusalResponse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_state(**overrides: Any) -> SupervisorState:
    """Build a minimal SupervisorState with responder-relevant defaults."""
    base: SupervisorState = {
        "query": "pre-visit brief for this patient",
        "patient_id": 999100,
        "tool_calls_accumulated": [],
        "citations": [
            Citation(
                source_type="patient_record",
                source_id="prescriptions:1001",
                field_or_chunk_id="prescriptions:1001",
                quote_or_value="metformin 1000mg",
            )
        ],
        "hops_taken": 1,
        "terminal_reason": "answered",
        "worker_results": [
            {
                "worker_name": "evidence_retriever",
                "citations_added": 1,
                "record_count": 1,
                "latency_ms": 120,
            }
        ],
        "final_response": None,
        "node_observability": [],
        "_pending_route": None,
    }
    base.update(overrides)  # type: ignore[typeddict-item]
    return base


def _make_verified_claim(text: str = "Patient is on metformin 1000mg BID.") -> Claim:
    return Claim(
        text=text,
        claim_type=ClaimType.FACT,
        source_record_ids=["prescriptions:1001"],
        verified=True,
    )


def _make_refused_verifier_result() -> MagicMock:
    """Mock VerifierResult with REFUSED verdict."""
    from agent.schemas import VerifierVerdict
    result = MagicMock()
    result.verdict = VerifierVerdict.REFUSED
    result.claims_passed = []
    result.claims_failed = [_make_verified_claim()]
    result.failure_rate = 0.8
    result.retry_needed = True
    return result


def _make_pass_verifier_result() -> MagicMock:
    """Mock VerifierResult with PASS verdict."""
    from agent.schemas import VerifierVerdict
    claim = _make_verified_claim()
    result = MagicMock()
    result.verdict = VerifierVerdict.PASS
    result.claims_passed = [claim]
    result.claims_failed = []
    result.failure_rate = 0.0
    result.retry_needed = False
    return result


# ---------------------------------------------------------------------------
# Test 1: responder calls synthesize_with_verifier and escalates to Sonnet
#         on first REFUSED attempt (decision #4, #15)
# ---------------------------------------------------------------------------


def test_responder_escalates_to_sonnet_on_refused_verifier() -> None:
    """Responder retries with Sonnet when first verifier verdict is REFUSED.

    BLOCKED ON: agent-rag landing agent/graph/workers/responder.py and
    agent/_synthesis.py (synthesize_with_verifier function).

    Sequence:
      1. responder calls synthesize_with_verifier(model=haiku) → REFUSED
      2. responder calls synthesize_with_verifier(model=sonnet) → PASS
      3. Final AgentResponse has escalated_to_sonnet=True
    """
    try:
        from agent.graph.workers.responder import responder_node
        from agent._synthesis import synthesize_with_verifier
    except ImportError as e:
        pytest.skip(
            f"BLOCKED: responder.py / _synthesis.py not yet available: {e}. "
            "Unblocked when agent-rag teammate lands those files."
        )

    from agent.config import get_settings
    from agent.schemas import Message, Role, VerifierVerdict
    settings = get_settings()

    state = _make_state()

    call_count = [0]
    models_used: list[str] = []

    def _mock_synthesize(
        *,
        messages: list,
        retrieved_records: list,
        citations: list,
        model: str,
        request_id: str,
        patient_id: int,
    ) -> tuple:
        """Returns (AgentResponse | RefusalResponse, VerifierResult)."""
        call_count[0] += 1
        models_used.append(model)
        if call_count[0] == 1:
            # First attempt: REFUSED
            refusal = RefusalResponse(
                reason="verifier refused — too many ungrounded claims",
                request_id=request_id,
            )
            return refusal, _make_refused_verifier_result()
        else:
            # Second attempt: PASS
            response = AgentResponse(
                message=Message(role=Role.ASSISTANT, content="Patient is on metformin."),
                claims=[_make_verified_claim()],
                request_id=request_id,
                escalated_to_sonnet=True,
            )
            return response, _make_pass_verifier_result()

    with patch("agent.graph.workers.responder.synthesize_with_verifier", side_effect=_mock_synthesize):
        patch_result = responder_node(state)

    # Responder must have tried twice
    assert call_count[0] >= 2, (
        f"Expected 2 synthesize_with_verifier calls (Haiku + Sonnet escalation), "
        f"got {call_count[0]}"
    )

    # Second call must use Sonnet (model_reasoning)
    assert any(m == settings.model_reasoning for m in models_used[1:]), (
        f"Expected Sonnet ({settings.model_reasoning!r}) on retry; "
        f"models used: {models_used!r}"
    )

    # final_response must be set in the state patch
    final_response = patch_result.get("final_response")
    assert final_response is not None, "responder_node must set final_response in state patch"

    # escalated_to_sonnet must be True
    if isinstance(final_response, AgentResponse):
        assert final_response.escalated_to_sonnet is True, (
            "AgentResponse.escalated_to_sonnet must be True after Sonnet escalation"
        )


# ---------------------------------------------------------------------------
# Test 2: responder runs outbound PHI gate (decision #11)
# ---------------------------------------------------------------------------


def test_responder_phi_gate_refuses_cross_patient_identifier() -> None:
    """Responder refuses when synthesized prose contains a cross-patient identifier.

    BLOCKED ON: agent-rag landing agent/graph/workers/responder.py and
    agent/_synthesis.py.

    The outbound PHI gate (agent/agent.py:1156-1192 pattern) must be
    replicated in the responder node per decision #11 (defense-in-depth).
    When the synthesized prose contains a patient_id from outside the current
    session (e.g. patient_id=999150 in a session for patient_id=999100),
    the responder must return a RefusalResponse with reason matching
    "cross-patient" or "outbound" or "phi" (case-insensitive).
    """
    try:
        from agent.graph.workers.responder import responder_node
        from agent._synthesis import synthesize_with_verifier
    except ImportError as e:
        pytest.skip(
            f"BLOCKED: responder.py / _synthesis.py not yet available: {e}. "
            "Unblocked when agent-rag teammate lands those files."
        )

    from agent.schemas import Message, Role

    state = _make_state(patient_id=999100)

    def _mock_synthesize_phi_in_prose(
        *,
        messages: list,
        retrieved_records: list,
        citations: list,
        model: str,
        request_id: str,
        patient_id: int,
    ) -> tuple:
        """Synthesize returns prose containing a cross-patient identifier."""
        # Prose contains patient_id=999150 (different from session patient 999100)
        cross_patient_prose = (
            "Patient is on metformin 1000mg. "
            "Note: patient_id=999150 also had elevated A1c per prior records."
        )
        response = AgentResponse(
            message=Message(role=Role.ASSISTANT, content=cross_patient_prose),
            claims=[_make_verified_claim()],
            request_id=request_id,
        )
        return response, _make_pass_verifier_result()

    with patch("agent.graph.workers.responder.synthesize_with_verifier",
               side_effect=_mock_synthesize_phi_in_prose):
        patch_result = responder_node(state)

    final_response = patch_result.get("final_response")
    assert final_response is not None, "responder_node must set final_response"
    assert isinstance(final_response, RefusalResponse), (
        f"Expected RefusalResponse when cross-patient identifier in prose, "
        f"got {type(final_response).__name__!r}"
    )

    # Reason must reference the PHI gate pattern
    reason_lower = final_response.reason.lower()
    phi_keywords = ("cross-patient", "cross patient", "outbound", "phi", "cross_patient")
    assert any(kw in reason_lower for kw in phi_keywords), (
        f"RefusalResponse.reason must reference PHI gate pattern; "
        f"got reason={final_response.reason!r}"
    )


# ---------------------------------------------------------------------------
# Test 3: responder returns 7-field observability in state patch (decision #14)
# ---------------------------------------------------------------------------


def test_responder_emits_7_field_observability() -> None:
    """Responder appends a 7-field observability record to node_observability.

    BLOCKED ON: agent-rag landing agent/graph/workers/responder.py and
    agent/_synthesis.py.

    Decision #14: each node that incurs LLM or retrieval cost appends a
    7-field record: node_name, latency_ms, tokens_input, tokens_output,
    cost_estimate_usd, retrieval_hits, extraction_confidence.

    All 7 fields must be present (not necessarily non-zero, but present and
    of the correct type).
    """
    try:
        from agent.graph.workers.responder import responder_node
        from agent._synthesis import synthesize_with_verifier
    except ImportError as e:
        pytest.skip(
            f"BLOCKED: responder.py / _synthesis.py not yet available: {e}. "
            "Unblocked when agent-rag teammate lands those files."
        )

    from agent.schemas import Message, Role

    state = _make_state()

    def _mock_synthesize(
        *,
        messages: list,
        retrieved_records: list,
        citations: list,
        model: str,
        request_id: str,
        patient_id: int,
    ) -> tuple:
        response = AgentResponse(
            message=Message(role=Role.ASSISTANT, content="Patient is on metformin."),
            claims=[_make_verified_claim()],
            request_id=request_id,
        )
        return response, _make_pass_verifier_result()

    with patch("agent.graph.workers.responder.synthesize_with_verifier",
               side_effect=_mock_synthesize):
        patch_result = responder_node(state)

    # node_observability must be updated
    obs_list = patch_result.get("node_observability", [])
    assert obs_list, (
        "responder_node must append at least one record to node_observability"
    )

    # Find the responder's observability record
    responder_obs = None
    for obs in obs_list:
        if isinstance(obs, dict) and obs.get("node_name") in ("responder", "responder_node"):
            responder_obs = obs
            break

    # If node_name key isn't present, just take the last entry
    if responder_obs is None and obs_list:
        responder_obs = obs_list[-1]

    assert responder_obs is not None, "Could not find responder observability record"

    _REQUIRED_FIELDS = {
        "node_name",
        "latency_ms",
        "tokens_input",
        "tokens_output",
        "cost_estimate_usd",
        "retrieval_hits",
        "extraction_confidence",
    }
    missing = _REQUIRED_FIELDS - set(responder_obs.keys())
    assert not missing, (
        f"Responder observability record missing fields: {sorted(missing)!r}. "
        f"Record: {responder_obs!r}"
    )

    # Type checks: latency_ms and token counts must be numeric
    assert isinstance(responder_obs["latency_ms"], (int, float)), (
        f"latency_ms must be numeric, got {type(responder_obs['latency_ms'])}"
    )
    assert isinstance(responder_obs["tokens_input"], (int, float)), (
        f"tokens_input must be numeric, got {type(responder_obs['tokens_input'])}"
    )
    assert isinstance(responder_obs["tokens_output"], (int, float)), (
        f"tokens_output must be numeric, got {type(responder_obs['tokens_output'])}"
    )
    assert isinstance(responder_obs["cost_estimate_usd"], (int, float)), (
        f"cost_estimate_usd must be numeric, got {type(responder_obs['cost_estimate_usd'])}"
    )
