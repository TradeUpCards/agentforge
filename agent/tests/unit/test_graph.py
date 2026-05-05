"""Unit tests for the W2 Stage-3b LangGraph supervisor graph.

All Anthropic LLM calls and tool calls are mocked — no real API calls.
Tests are deterministic, fast, and run in fixture mode.

Tests:
  test_supervisor_routes_to_intake_when_doc_uploaded
  test_supervisor_routes_to_evidence_for_clinical_query
  test_hop_cap_terminates_at_4
  test_supervisor_invalid_route_rejected
  test_terminal_state_propagates_citations

W2_ARCHITECTURE.md §3.1 (graph shape), §3.2 (supervisor responsibilities),
§3.4 (worker isolation), §8.2 (sentinel patient_id range).

No-PHI discipline: no raw query text in assertions, no extracted field values.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.graph.state import SupervisorState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_state(**overrides: Any) -> SupervisorState:
    """Build a minimal SupervisorState with sensible defaults."""
    base: SupervisorState = {
        "query": "clinical query text",
        "tool_calls_accumulated": [],
        "citations": [],
        "hops_taken": 0,
        "terminal_reason": None,
        "worker_results": [],
        "_pending_route": None,
    }
    base.update(overrides)  # type: ignore[typeddict-item]
    return base


def _make_anthropic_response(route: str) -> MagicMock:
    """Build a mock Anthropic Message object returning a routing JSON."""
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = json.dumps({"route": route, "rationale": "test rationale"})
    msg = MagicMock()
    msg.content = [text_block]
    msg.stop_reason = "end_turn"
    msg.usage = MagicMock(input_tokens=50, output_tokens=20)
    return msg


# ---------------------------------------------------------------------------
# Test 1: supervisor routes to intake when doc context is present
# ---------------------------------------------------------------------------


def test_supervisor_routes_to_intake_when_doc_uploaded() -> None:
    """Supervisor LLM mocked to return intake_extractor; state should have
    _pending_route='intake_extractor' and hops_taken incremented."""
    from agent.graph.supervisor import supervisor_node

    state = _make_state(
        tool_calls_accumulated=[
            {
                "doc_ref_id": "DocumentReference/test-999100",
                "patient_id": 999100,
                "doc_type": "lab_pdf",
            }
        ]
    )

    # Mock _call_supervisor_llm directly so we don't need to thread asyncio.
    with patch("agent.graph.supervisor._call_supervisor_llm", return_value="intake_extractor"):
        patch_result = supervisor_node(state)

    assert patch_result.get("_pending_route") == "intake_extractor", (
        f"Expected _pending_route='intake_extractor', got {patch_result.get('_pending_route')!r}"
    )
    assert patch_result.get("hops_taken") == 1, (
        f"Expected hops_taken=1, got {patch_result.get('hops_taken')!r}"
    )
    assert patch_result.get("terminal_reason") is None


# ---------------------------------------------------------------------------
# Test 2: supervisor routes to evidence for a clinical query (no doc)
# ---------------------------------------------------------------------------


def test_supervisor_routes_to_evidence_for_clinical_query() -> None:
    """Supervisor LLM mocked to return evidence_retriever; state should have
    _pending_route='evidence_retriever' and hops_taken incremented."""
    from agent.graph.supervisor import supervisor_node

    state = _make_state(
        query="What is the A1c target for type 2 diabetes?",
        tool_calls_accumulated=[],
    )

    with patch("agent.graph.supervisor._call_supervisor_llm", return_value="evidence_retriever"):
        patch_result = supervisor_node(state)

    assert patch_result.get("_pending_route") == "evidence_retriever", (
        f"Expected _pending_route='evidence_retriever', got {patch_result.get('_pending_route')!r}"
    )
    assert patch_result.get("hops_taken") == 1
    assert patch_result.get("terminal_reason") is None


# ---------------------------------------------------------------------------
# Test 3: 4-hop cap terminates with max_hops reason
# ---------------------------------------------------------------------------


def test_hop_cap_terminates_at_4(caplog: pytest.LogCaptureFixture) -> None:
    """When hops_taken >= 4, supervisor must:
      - return terminal_reason='supervisor_max_hops' (§3.2 named refusal)
      - log a line containing SUPERVISOR_MAX_HOPS_REACHED
      - NOT call the LLM
    """
    from agent.graph.supervisor import supervisor_node

    state = _make_state(hops_taken=4)

    with caplog.at_level(logging.WARNING, logger="agent.graph.supervisor"):
        with patch("agent.graph.supervisor.build_llm_client") as mock_build:
            patch_result = supervisor_node(state)
            # LLM should NOT be called
            mock_build.assert_not_called()

    assert patch_result.get("terminal_reason") == "supervisor_max_hops", (
        f"Expected terminal_reason='supervisor_max_hops', got {patch_result.get('terminal_reason')!r}"
    )
    assert any(
        "SUPERVISOR_MAX_HOPS_REACHED" in record.message
        for record in caplog.records
    ), "Expected SUPERVISOR_MAX_HOPS_REACHED log sentinel not found"


# ---------------------------------------------------------------------------
# Test 4: invalid route from LLM is rejected → terminal_reason='no_route'
# ---------------------------------------------------------------------------


def test_supervisor_invalid_route_rejected() -> None:
    """When supervisor LLM returns an unrecognised route, supervisor should
    set terminal_reason='no_route' without crashing."""
    from agent.graph.supervisor import supervisor_node

    state = _make_state()

    with patch("agent.graph.supervisor._call_supervisor_llm", return_value="evil_node"):
        patch_result = supervisor_node(state)

    assert patch_result.get("terminal_reason") == "no_route", (
        f"Expected terminal_reason='no_route', got {patch_result.get('terminal_reason')!r}"
    )


# ---------------------------------------------------------------------------
# Test 5: terminal state propagates citations from worker
# ---------------------------------------------------------------------------


def test_terminal_state_propagates_citations() -> None:
    """Full happy path: evidence_retriever_node adds citations, supervisor
    then terminates. Final state.citations should be populated."""
    from agent.graph.workers.evidence_retriever import evidence_retriever_node
    from agent.schemas import Citation

    state = _make_state(
        query="metformin type 2 diabetes A1c target",
        hops_taken=1,
    )

    # Mock search_guidelines to return one known chunk.
    mock_chunk = {
        "chunk_id": "ada-2024-s2-3-chunk-1",
        "section": "S2.3",
        "source_url": "https://example.com/ada",
        "source_attribution": "ADA 2024",
        "body": "The A1C target for most nonpregnant adults is <7%.",
        "leading_excerpt": "The A1C target for most nonpregnant adults is <7%.",
        "score": 0.95,
    }

    with patch("agent.graph.workers.evidence_retriever.search_guidelines") as mock_sg:
        mock_sg.return_value = [mock_chunk]
        patch_result = evidence_retriever_node(state)

    # Check citations were added.
    new_citations = patch_result.get("citations", [])
    assert len(new_citations) >= 1, f"Expected at least 1 citation, got {len(new_citations)}"

    first = new_citations[0]
    assert isinstance(first, Citation), f"Expected Citation instance, got {type(first)}"
    assert first.source_type == "guideline", (
        f"Expected source_type='guideline', got {first.source_type!r}"
    )
    assert first.source_id == "ada-2024-s2-3-chunk-1", (
        f"Expected source_id='ada-2024-s2-3-chunk-1', got {first.source_id!r}"
    )

    # worker_results should have structural metadata.
    worker_results = patch_result.get("worker_results", [])
    assert len(worker_results) >= 1
    assert worker_results[-1]["worker_name"] == "evidence_retriever"
    assert worker_results[-1]["citations_added"] == len(new_citations)


# ---------------------------------------------------------------------------
# Test 6: supervisor_router sends to correct worker or END
# ---------------------------------------------------------------------------


def test_supervisor_router_dispatches_intake() -> None:
    """_supervisor_router returns 'intake_extractor' when _pending_route is set."""
    from agent.graph.builder import _supervisor_router

    state = _make_state(_pending_route="intake_extractor")
    result = _supervisor_router(state)
    assert result == "intake_extractor"


def test_supervisor_router_dispatches_evidence() -> None:
    """_supervisor_router returns 'evidence_retriever' when _pending_route is set."""
    from agent.graph.builder import _supervisor_router
    from langgraph.graph import END

    state = _make_state(_pending_route="evidence_retriever")
    result = _supervisor_router(state)
    assert result == "evidence_retriever"


def test_supervisor_router_ends_on_terminal() -> None:
    """_supervisor_router returns END when terminal_reason is set."""
    from agent.graph.builder import _supervisor_router
    from langgraph.graph import END

    state = _make_state(terminal_reason="supervisor_max_hops")
    result = _supervisor_router(state)
    assert result == END


def test_supervisor_router_ends_when_answered() -> None:
    """_supervisor_router returns END on terminal_reason='answered'."""
    from agent.graph.builder import _supervisor_router
    from langgraph.graph import END

    state = _make_state(terminal_reason="answered")
    result = _supervisor_router(state)
    assert result == END
