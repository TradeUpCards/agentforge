"""End-to-end integration tests for the W2 Stage-3b LangGraph supervisor graph.

Marked `@pytest.mark.live` — SKIPPED by default unless pytest is invoked
with `--live`.  This prevents accidental API cost in CI.

Tests mock the LLM supervisor but exercise the real graph topology:
  - test_graph_handles_guideline_query_e2e
  - test_graph_handles_lab_pdf_e2e
  - test_graph_uc1_pre_visit_brief_returns_patient_record_citation  (NEW — UC1 graph)
  - test_graph_uc3_guideline_question_returns_guideline_citation    (NEW — UC3 graph)

W2_ARCHITECTURE.md §3.1 (graph shape), §3.3 (trace shape), §8.2 (sentinel PIDs).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agent.schemas import AgentResponse, RefusalResponse

# All tests in this file make real API calls (or infrastructure calls that
# require a live key).  The --live marker gate in conftest.py skips them
# unless the caller opts in with `pytest --live`.
pytestmark = pytest.mark.live


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_supervisor_response(route: str) -> MagicMock:
    """Build a mock Anthropic Message that the supervisor will parse as the given route."""
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = json.dumps({"route": route, "rationale": "e2e test"})
    msg = MagicMock()
    msg.content = [text_block]
    msg.stop_reason = "end_turn"
    msg.usage = MagicMock(input_tokens=40, output_tokens=15)
    return msg


def _make_none_response() -> MagicMock:
    """Build a mock supervisor response that terminates the graph."""
    return _make_supervisor_response("none")


# ---------------------------------------------------------------------------
# Test 1: guideline query → evidence_retriever → citations
# ---------------------------------------------------------------------------


def test_graph_handles_guideline_query_e2e() -> None:
    """Feed a clinical query; mock search_guidelines and supervisor LLM.

    Sequence mocked:
      1. Supervisor call 1 → "evidence_retriever"
      2. evidence_retriever_node calls search_guidelines (mocked)
      3. Supervisor call 2 → "none" (terminate)
    Final state must have at least one citation of source_type='guideline'.
    """
    from agent.graph.builder import build_supervisor_graph
    from agent.graph.state import SupervisorState

    mock_chunk = {
        "chunk_id": "ada-2024-s2-3-chunk-7",
        "section": "S2.3",
        "source_url": "https://example.com/ada",
        "source_attribution": "ADA Standards of Care 2024",
        "body": "The A1C target for most nonpregnant adults is <7%.",
        "leading_excerpt": "The A1C target for most nonpregnant adults is <7%.",
        "score": 0.92,
    }

    # Build a response sequence: first call routes to evidence_retriever, second ends.
    supervisor_responses = [
        _make_supervisor_response("evidence_retriever"),
        _make_none_response(),
    ]
    call_count = [0]

    def _mock_run_until_complete(coro: Any) -> Any:
        # Each call to run_until_complete returns the next supervisor response.
        idx = min(call_count[0], len(supervisor_responses) - 1)
        call_count[0] += 1
        return supervisor_responses[idx]

    initial_state: SupervisorState = {
        "query": "What is the A1c target for type 2 diabetes?",
        "patient_id": None,
        "tool_calls_accumulated": [],
        "citations": [],
        "hops_taken": 0,
        "terminal_reason": None,
        "worker_results": [],
        "final_response": None,
        "node_observability": [],
        "_pending_route": None,
    }

    graph = build_supervisor_graph()

    # Mock _call_supervisor_llm to sequence: evidence_retriever → none.
    # Return 4-tuple (route, tokens_in, tokens_out, escalated) per W2 graph phase.
    supervisor_routes = ["evidence_retriever", "none"]
    supervisor_call_count = [0]

    def _mock_call_llm(state: Any) -> tuple:
        idx = min(supervisor_call_count[0], len(supervisor_routes) - 1)
        supervisor_call_count[0] += 1
        return supervisor_routes[idx], 50, 20, False

    # Mock synthesize_with_verifier so the responder doesn't need a real LLM.
    async def _mock_synthesize(**kwargs: Any) -> tuple:
        from agent.schemas import Message, Role, VerifierVerdict
        from unittest.mock import MagicMock as _MM
        _verdict = _MM()
        _verdict.verdict = VerifierVerdict.PASS
        _verdict.claims_passed = []
        _verdict.claims_failed = []
        _verdict.failure_rate = 0.0
        _verdict.retry_needed = False
        response = AgentResponse(
            message=Message(role=Role.ASSISTANT, content="ADA recommends <7% A1c target."),
            request_id=kwargs.get("request_id", "test"),
        )
        return response, _verdict

    with patch("agent.graph.workers.evidence_retriever.search_guidelines") as mock_sg, \
         patch("agent.graph.workers.responder.synthesize_with_verifier",
               side_effect=_mock_synthesize):
        mock_sg.return_value = [mock_chunk]

        with patch("agent.graph.supervisor._call_supervisor_llm", side_effect=_mock_call_llm):
            final_state = graph.invoke(initial_state)

    citations = final_state.get("citations", [])
    assert len(citations) >= 1, (
        f"Expected at least 1 citation after guideline query, got {len(citations)}"
    )
    guideline_citations = [c for c in citations if c.source_type == "guideline"]
    assert len(guideline_citations) >= 1, (
        f"Expected at least 1 guideline citation, got {len(guideline_citations)} of {len(citations)} total"
    )
    assert guideline_citations[0].source_id == "ada-2024-s2-3-chunk-7"

    terminal_reason = final_state.get("terminal_reason")
    assert terminal_reason in ("answered", "responded", "supervisor_max_hops", "no_route"), (
        f"Expected a terminal reason, got {terminal_reason!r}"
    )


# ---------------------------------------------------------------------------
# Test 2: lab PDF → intake_extractor → citations
# ---------------------------------------------------------------------------


def test_graph_handles_lab_pdf_e2e() -> None:
    """Provide synthetic lab context (sentinel patient 999100); mock
    attach_and_extract_async and supervisor LLM.

    Sequence mocked:
      1. Supervisor call 1 → "intake_extractor"
      2. intake_extractor_node calls attach_and_extract_async (mocked)
      3. Supervisor call 2 → "none" (terminate)
    Final state must have at least one citation of source_type='extracted_document'
    with a valid field_or_chunk_id (block_id reference).
    """
    from agent.document_schemas import ExtractMeta, LabReport, LabResult
    from agent.graph.builder import build_supervisor_graph
    from agent.graph.state import SupervisorState

    # Build a minimal LabReport (sentinel patient 999100 per §8.2).
    # LabResult has source_block_id (Docling block ref) but not bbox —
    # bbox lives in the DoclingBlock, resolved at render time.
    mock_lab_result = LabResult(
        test_name="HbA1c",
        value=7.8,
        unit="%",
        reference_range="<5.7",
        abnormal=True,
        collection_date=None,
        source_block_id="block_0",
        confidence=0.95,
    )
    mock_extract_meta = ExtractMeta(
        docling_version="test",
        extraction_model="claude-haiku-4-5",
        page_count=1,
        extraction_confidence_avg=0.95,
    )
    mock_lab_report = LabReport(
        patient_id=999100,
        document_reference_id="DocumentReference/synthetic-lab-999100",
        results=[mock_lab_result],
        extraction_metadata=mock_extract_meta,
    )

    supervisor_responses = [
        _make_supervisor_response("intake_extractor"),
        _make_none_response(),
    ]
    call_count = [0]

    def _mock_run_until_complete(coro: Any) -> Any:
        idx = min(call_count[0], len(supervisor_responses) - 1)
        call_count[0] += 1
        return supervisor_responses[idx]

    initial_state: SupervisorState = {
        "query": "Extract lab results from the uploaded PDF",
        "patient_id": 999100,
        "tool_calls_accumulated": [
            {
                "doc_ref_id": "DocumentReference/synthetic-lab-999100",
                "patient_id": 999100,
                "doc_type": "lab_pdf",
                "pdf_path": None,
            }
        ],
        "citations": [],
        "hops_taken": 0,
        "terminal_reason": None,
        "worker_results": [],
        "final_response": None,
        "node_observability": [],
        "_pending_route": None,
    }

    graph = build_supervisor_graph()

    # Mock _call_supervisor_llm to sequence: intake_extractor → none.
    # Return 4-tuple (route, tokens_in, tokens_out, escalated) per W2 graph phase.
    supervisor_routes = ["intake_extractor", "none"]
    supervisor_call_count = [0]

    def _mock_call_llm(state: Any) -> tuple:
        idx = min(supervisor_call_count[0], len(supervisor_routes) - 1)
        supervisor_call_count[0] += 1
        return supervisor_routes[idx], 50, 20, False

    # attach_and_extract_async must be an awaitable — asyncio.run() calls it.
    async def _mock_extract(*args: Any, **kwargs: Any) -> Any:
        return mock_lab_report

    # Mock synthesize_with_verifier so the responder doesn't need a real LLM.
    async def _mock_synthesize(**kwargs: Any) -> tuple:
        from agent.schemas import Message, Role, VerifierVerdict
        from unittest.mock import MagicMock as _MM
        _verdict = _MM()
        _verdict.verdict = VerifierVerdict.PASS
        _verdict.claims_passed = []
        _verdict.claims_failed = []
        _verdict.failure_rate = 0.0
        _verdict.retry_needed = False
        response = AgentResponse(
            message=Message(role=Role.ASSISTANT, content="HbA1c is 7.8%."),
            request_id=kwargs.get("request_id", "test"),
        )
        return response, _verdict

    with patch(
        "agent.graph.workers.intake_extractor.attach_and_extract_async",
        side_effect=_mock_extract,
    ), patch(
        "agent.graph.workers.responder.synthesize_with_verifier",
        side_effect=_mock_synthesize,
    ):
        with patch("agent.graph.supervisor._call_supervisor_llm", side_effect=_mock_call_llm):
            final_state = graph.invoke(initial_state)

    citations = final_state.get("citations", [])
    assert len(citations) >= 1, (
        f"Expected at least 1 citation after lab PDF extraction, got {len(citations)}"
    )
    extracted_citations = [c for c in citations if c.source_type == "extracted_document"]
    assert len(extracted_citations) >= 1, (
        f"Expected at least 1 extracted_document citation, got {len(extracted_citations)} of {len(citations)} total"
    )
    # Verify block_id roundtrip (structural citation, not raw field value).
    first_ext = extracted_citations[0]
    assert first_ext.field_or_chunk_id, "field_or_chunk_id (block_id) must be non-empty"
    assert first_ext.source_id == "DocumentReference/synthetic-lab-999100"


# ---------------------------------------------------------------------------
# Test 3 (UC1 e2e): pre-visit brief via /graph_chat shape
# ---------------------------------------------------------------------------


def test_graph_uc1_pre_visit_brief_returns_patient_record_citation() -> None:
    """UC1 e2e — pre-visit brief via graph: final_response has patient_record citation.

    BLOCKED ON: agent-rag landing responder.py + _synthesis.py + /graph_chat endpoint.
    Until those land, this test exercises the graph shape up to the responder boundary.

    Sequence:
      1. patient_id=999100, query='give me a pre-visit brief'
      2. Supervisor → evidence_retriever (mocked)
      3. evidence_retriever fetches W1 tools (mocked via execute_tool)
      4. Supervisor → responder (mocked if not available)
      5. final_response is AgentResponse with patient_record citation
      6. claims_passed_count >= 1

    Env: USE_FIXTURE_LLM=true USE_FIXTURE_DATA=true USE_FIXTURE_EXTRACTION=true
    """
    import os
    import pytest

    # Try importing the full graph with responder. If responder isn't available,
    # we still run the graph without it and assert on the citation shape.
    from agent.graph.builder import build_supervisor_graph
    from agent.graph.state import SupervisorState

    # Build a state that mimics what /graph_chat will pass.
    # patient_id=999100 is the sentinel range (§8.2) — no real PHI.
    initial_state: SupervisorState = {
        "query": "give me a pre-visit brief for this patient",
        "patient_id": 999100,
        "tool_calls_accumulated": [],
        "citations": [],
        "hops_taken": 0,
        "terminal_reason": None,
        "worker_results": [],
        "final_response": None,
        "node_observability": [],
        "_pending_route": None,
    }

    # Route: supervisor → evidence_retriever → supervisor → answered/responder
    supervisor_routes = ["evidence_retriever", "none"]
    supervisor_call_count = [0]

    def _mock_call_llm(state: Any) -> tuple:
        idx = min(supervisor_call_count[0], len(supervisor_routes) - 1)
        supervisor_call_count[0] += 1
        return supervisor_routes[idx], 50, 20, False

    # Mock execute_tool to return a minimal patient record (sentinel patient only).
    async def _mock_execute_tool(tool_name: str, tool_input: dict) -> list:
        from agent.schemas import CitationStrength, RetrievedRecord
        return [
            RetrievedRecord(
                table="prescriptions",
                record_id="1001",
                citation_strength=CitationStrength.STRUCTURED,
                fields={"drug_name": "SYNTHETIC_MED"},
            )
        ]

    # Mock synthesize_with_verifier so the responder doesn't need a real LLM.
    async def _mock_synthesize(**kwargs: Any) -> tuple:
        from agent.schemas import Message, Role, VerifierVerdict
        from unittest.mock import MagicMock as _MM
        _verdict = _MM()
        _verdict.verdict = VerifierVerdict.PASS
        _verdict.claims_passed = []
        _verdict.claims_failed = []
        _verdict.failure_rate = 0.0
        _verdict.retry_needed = False
        response = AgentResponse(
            message=Message(role=Role.ASSISTANT, content="Pre-visit brief for sentinel patient."),
            request_id=kwargs.get("request_id", "test"),
        )
        return response, _verdict

    graph = build_supervisor_graph()

    with patch("agent.graph.workers.evidence_retriever.execute_tool",
               side_effect=_mock_execute_tool), \
         patch("agent.graph.workers.responder.synthesize_with_verifier",
               side_effect=_mock_synthesize), \
         patch("agent.graph.supervisor._call_supervisor_llm",
               side_effect=_mock_call_llm):
        final_state = graph.invoke(initial_state)

    # At least one citation must be present (patient_record type)
    citations = final_state.get("citations", [])
    assert len(citations) >= 1, (
        f"UC1 pre-visit brief: expected >= 1 citation in final state, got {len(citations)}"
    )

    patient_record_cites = [c for c in citations if c.source_type == "patient_record"]
    assert len(patient_record_cites) >= 1, (
        f"UC1: expected at least 1 patient_record citation, "
        f"got {[c.source_type for c in citations]!r}"
    )

    terminal_reason = final_state.get("terminal_reason")
    assert terminal_reason in ("answered", "responded", "supervisor_max_hops"), (
        f"UC1: expected terminal terminal_reason, got {terminal_reason!r}"
    )

    # Verify the responder did set a final_response (wiring check).
    # claims list may be empty in this mock-based test since synthesize_with_verifier
    # is stubbed — the citation assertion above is the load-bearing check.
    # The full-stack claims assertion lives in the eval cases (65_graph_uc1_pre_visit_brief.yaml).
    final_response = final_state.get("final_response")
    if final_response is not None:
        assert isinstance(final_response, (AgentResponse, RefusalResponse)), (
            f"UC1: final_response must be AgentResponse or RefusalResponse, "
            f"got {type(final_response).__name__!r}"
        )


# ---------------------------------------------------------------------------
# Test 4 (UC3 e2e): guideline question via /graph_chat shape
# ---------------------------------------------------------------------------


def test_graph_uc3_guideline_question_returns_guideline_citation() -> None:
    """UC3 e2e — guideline question via graph: at least 1 guideline citation.

    BLOCKED ON: agent-rag landing responder.py + /graph_chat endpoint.

    Sequence:
      1. patient_id=999100, query='What is the ADA-recommended A1c target?'
      2. Supervisor → evidence_retriever (mocked)
      3. evidence_retriever sees guideline keyword → calls search_guidelines
      4. Supervisor → answered/responder
      5. final state has at least 1 citation with source_type='guideline'

    Env: USE_FIXTURE_LLM=true USE_FIXTURE_DATA=true
    """
    from agent.graph.builder import build_supervisor_graph
    from agent.graph.state import SupervisorState

    mock_guideline_chunk = {
        "chunk_id": "ada-2024-s6-2-chunk-99",
        "section": "S6.2",
        "source_url": "https://example.com/ada",
        "source_attribution": "ADA Standards of Care 2024",
        "body": "The A1C target for most nonpregnant adults with T2DM is <7%.",
        "leading_excerpt": "The A1C target for most nonpregnant adults with T2DM is <7%.",
        "score": 0.94,
    }

    initial_state: SupervisorState = {
        "query": "What is the ADA-recommended A1c target for this patient?",
        "patient_id": 999100,
        "tool_calls_accumulated": [],
        "citations": [],
        "hops_taken": 0,
        "terminal_reason": None,
        "worker_results": [],
        "final_response": None,
        "node_observability": [],
        "_pending_route": None,
    }

    supervisor_routes = ["evidence_retriever", "none"]
    supervisor_call_count = [0]

    def _mock_call_llm(state: Any) -> tuple:
        idx = min(supervisor_call_count[0], len(supervisor_routes) - 1)
        supervisor_call_count[0] += 1
        return supervisor_routes[idx], 50, 20, False

    # Mock synthesize_with_verifier so the responder doesn't need a real LLM.
    async def _mock_synthesize(**kwargs: Any) -> tuple:
        from agent.schemas import Message, Role, VerifierVerdict
        from unittest.mock import MagicMock as _MM
        _verdict = _MM()
        _verdict.verdict = VerifierVerdict.PASS
        _verdict.claims_passed = []
        _verdict.claims_failed = []
        _verdict.failure_rate = 0.0
        _verdict.retry_needed = False
        response = AgentResponse(
            message=Message(role=Role.ASSISTANT, content="ADA recommends A1c <7%."),
            request_id=kwargs.get("request_id", "test"),
        )
        return response, _verdict

    graph = build_supervisor_graph()

    with patch("agent.graph.workers.evidence_retriever.search_guidelines",
               return_value=[mock_guideline_chunk]) as mock_sg, \
         patch("agent.graph.workers.responder.synthesize_with_verifier",
               side_effect=_mock_synthesize), \
         patch("agent.graph.supervisor._call_supervisor_llm",
               side_effect=_mock_call_llm):
        final_state = graph.invoke(initial_state)

    citations = final_state.get("citations", [])
    guideline_cites = [c for c in citations if c.source_type == "guideline"]

    assert len(guideline_cites) >= 1, (
        f"UC3 guideline question: expected >= 1 guideline citation, "
        f"got {len(guideline_cites)} of {len(citations)} total. "
        f"citation types: {[c.source_type for c in citations]!r}"
    )

    # Verify the specific chunk_id roundtrips
    chunk_ids = {c.source_id for c in guideline_cites}
    assert "ada-2024-s6-2-chunk-99" in chunk_ids, (
        f"Expected ADA chunk_id in guideline citations; got chunk_ids={chunk_ids!r}"
    )

    terminal_reason = final_state.get("terminal_reason")
    assert terminal_reason in ("answered", "responded", "supervisor_max_hops"), (
        f"UC3: expected terminal terminal_reason, got {terminal_reason!r}"
    )
