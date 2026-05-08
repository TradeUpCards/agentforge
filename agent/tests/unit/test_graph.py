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
    """Build a minimal SupervisorState with sensible defaults.

    Includes all fields required by the W2 graph-phase SupervisorState
    (patient_id, final_response, node_observability) so existing tests
    continue to work after the schema expansion in decision #7 and #14.
    """
    base: SupervisorState = {
        "query": "clinical query text",
        "patient_id": None,
        "tool_calls_accumulated": [],
        "citations": [],
        "hops_taken": 0,
        "terminal_reason": None,
        "worker_results": [],
        "final_response": None,
        "node_observability": [],
        "retrieved_records": [],
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
    # Return value is a 4-tuple: (route, tokens_input, tokens_output, escalated)
    # after the W2 graph-phase update (decision #14 — per-node observability).
    with patch("agent.graph.supervisor._call_supervisor_llm",
               return_value=("intake_extractor", 50, 20, False)):
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

    with patch("agent.graph.supervisor._call_supervisor_llm",
               return_value=("evidence_retriever", 50, 20, False)):
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

    with patch("agent.graph.supervisor._call_supervisor_llm",
               return_value=("evil_node", 50, 20, False)):
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
# Regression test (added 2026-05-08): worker→responder content bridging
#
# Background:
#   On 2026-05-07 a production user reported that /graph_chat returned
#   "no clinical content" responses even when the OpenEMR DB was populated
#   with hundreds of problems / prescriptions / encounters per patient. Root
#   cause: agent/graph/workers/evidence_retriever.py was building Citation
#   objects with quote_or_value=f"{table}:{record_id}" but never threading
#   the underlying RetrievedRecord.fields through to the responder. The
#   responder's _citations_to_retrieved_records() reconstructed records
#   with `fields={}`, so the LLM honestly reported "received record IDs
#   but no clinical content."
#
#   The 67-case eval gate at 100% on all 5 rubrics did NOT catch this
#   because (a) USE_FIXTURE_LLM=true short-circuits the supervisor LLM in
#   CI, (b) the factually_consistent rubric's substring verifier matched
#   the citation source_id against itself trivially. The bug only
#   manifested under a real LLM doing honest reporting.
#
# This test pins the contract: when evidence_retriever runs against
# tools that return RetrievedRecord objects with populated fields, the
# state patch must carry those records (with fields) through to the
# responder via state["retrieved_records"]. If the bridging regresses,
# this test fails immediately in CI — no live-LLM deploy required.
# ---------------------------------------------------------------------------


def test_evidence_retriever_threads_record_fields_to_state() -> None:
    """Regression: state["retrieved_records"] must carry the
    RetrievedRecord.fields content (not just citation IDs) so the responder
    can hydrate the LLM with actual diagnosis / drug / lab values.

    See: 2026-05-08 fix/evidence-retriever-record-fields branch.
    """
    from agent.graph.workers.evidence_retriever import evidence_retriever_node
    from agent.schemas import CitationStrength, RetrievedRecord

    # Build a fixture-shaped RetrievedRecord with realistic populated fields.
    fake_problem = RetrievedRecord(
        table="lists",
        record_id="1408",
        citation_strength=CitationStrength.CODE_BACKED,
        fields={
            "title": "Type 2 diabetes mellitus",
            "diagnosis": "ICD-10:E11.9",
            "date_added": "2024-08-12",
            "type": "medical_problem",
        },
    )
    fake_med = RetrievedRecord(
        table="prescriptions",
        record_id="2231",
        citation_strength=CitationStrength.CODE_BACKED,
        fields={
            "drug": "Metformin",
            "dosage": "1000mg",
            "frequency": "BID",
            "rxnorm_drugcode": "860975",
        },
    )

    state = _make_state(
        query="pre-visit brief for this patient",
        patient_id=999100,  # sentinel range per W2_ARCHITECTURE.md §8.2
    )

    # Stub execute_tool to return our fixture records for any tool call.
    async def _stub_execute_tool(tool_name: str, tool_input: dict[str, Any]) -> list[Any]:
        # Return both records on the first tool call, empty for others —
        # mirrors the worker fanning out to 5 W1 tools.
        if tool_name == "get_problem_list":
            return [fake_problem]
        if tool_name == "get_active_medications":
            return [fake_med]
        return []

    with patch(
        "agent.graph.workers.evidence_retriever.execute_tool",
        side_effect=_stub_execute_tool,
    ):
        patch_result = evidence_retriever_node(state)

    # 1. State patch MUST include retrieved_records key.
    assert "retrieved_records" in patch_result, (
        "evidence_retriever must return state[\"retrieved_records\"] so the "
        "responder can hydrate the LLM with full record content"
    )

    records = patch_result["retrieved_records"]
    assert len(records) == 2, (
        f"Expected 2 RetrievedRecord objects (1 problem + 1 medication), "
        f"got {len(records)}"
    )

    # 2. Each record must carry its full fields dict — NOT empty.
    # This is the exact regression the production bug exhibited:
    # citation IDs reached the responder but fields were lost en route.
    by_table = {r.table: r for r in records}
    assert "lists" in by_table and "prescriptions" in by_table, (
        f"Expected lists + prescriptions tables, got {sorted(by_table.keys())}"
    )

    problem = by_table["lists"]
    assert problem.fields, (
        "RetrievedRecord(lists:1408).fields must be non-empty — the bug "
        "this guards against had fields={} reaching the responder"
    )
    assert problem.fields.get("title") == "Type 2 diabetes mellitus", (
        "Problem title content lost — the LLM would report 'no clinical "
        f"content' instead of the diagnosis. Got: {problem.fields!r}"
    )
    assert problem.fields.get("diagnosis") == "ICD-10:E11.9", (
        f"Problem diagnosis code lost. Got: {problem.fields!r}"
    )

    med = by_table["prescriptions"]
    assert med.fields, "Medication fields must be non-empty"
    assert med.fields.get("drug") == "Metformin", (
        f"Medication drug name lost. Got: {med.fields!r}"
    )

    # 3. Citations should still be present (existing contract preserved).
    new_citations = patch_result.get("citations", [])
    assert len(new_citations) >= 2, (
        f"Citations contract regressed: expected ≥2, got {len(new_citations)}"
    )


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


def test_supervisor_router_routes_to_responder_on_max_hops() -> None:
    """_supervisor_router routes to 'responder' when terminal_reason='supervisor_max_hops'.

    Updated in W2 graph phase (decision #9 + builder.py update):
    supervisor_max_hops → responder (partial evidence synthesis), NOT END.
    Bypassing responder only happens on 'no_route'.
    """
    from agent.graph.builder import _supervisor_router

    state = _make_state(terminal_reason="supervisor_max_hops")
    result = _supervisor_router(state)
    assert result == "responder", (
        f"supervisor_max_hops must route to 'responder' (not END). "
        f"Decision #9: max_hops → responder runs on partial citations. "
        f"Got: {result!r}"
    )


def test_supervisor_router_routes_to_responder_when_answered() -> None:
    """_supervisor_router routes to 'responder' on terminal_reason='answered'.

    Updated in W2 graph phase: 'answered' → responder (for synthesis), NOT END.
    Only 'no_route' bypasses the responder and goes directly to END.
    """
    from agent.graph.builder import _supervisor_router

    state = _make_state(terminal_reason="answered")
    result = _supervisor_router(state)
    assert result == "responder", (
        f"terminal_reason='answered' must route to 'responder'. "
        f"Got: {result!r}"
    )


# ===========================================================================
# W2 graph-phase additions — tests 7-13
# Required by the quality-lead brief (w2-graph-supervisor phase).
# Each test targets a specific regression class from W2_ARCHITECTURE.md §5.5.
# ===========================================================================


# ---------------------------------------------------------------------------
# Test 7: patient_id propagates from top-level state to evidence_retriever
# ---------------------------------------------------------------------------


def test_patient_id_propagates_to_evidence_retriever() -> None:
    """Decision #7 — patient_id is a top-level SupervisorState field.

    Build a state with patient_id=999100 at the top level (NOT buried in
    tool_calls_accumulated). Run evidence_retriever_node. Assert that the node
    reads patient_id from state["patient_id"] and calls patient-record tools
    (not guidelines-only).

    Regression class: if patient_id were read only from tool_calls_accumulated,
    a /graph_chat request would silently fall back to guideline-only retrieval
    even when a patient_id was provided.
    """
    from agent.graph.workers.evidence_retriever import evidence_retriever_node

    # State has patient_id at top level only — no doc entry in
    # tool_calls_accumulated carrying patient_id.
    state = _make_state(
        query="show me the medications for this patient",
        patient_id=999100,
        tool_calls_accumulated=[],
    )

    with patch("agent.graph.workers.evidence_retriever.execute_tool") as mock_exec:
        # execute_tool is async — return an empty list of RetrievedRecord
        async def _fake_exec(tool_name: str, tool_input: dict) -> list:
            return []

        mock_exec.side_effect = _fake_exec
        patch_result = evidence_retriever_node(state)

    # execute_tool must have been called at least once (patient-record path)
    assert mock_exec.called, (
        "execute_tool should be called when patient_id is present in state "
        "and query contains patient-record keywords"
    )

    # patient_id=999100 should be somewhere in the call arguments
    all_calls = mock_exec.call_args_list
    patient_ids_seen = set()
    for call in all_calls:
        args, kwargs = call
        if len(args) >= 2 and isinstance(args[1], dict):
            patient_ids_seen.add(args[1].get("patient_id"))
    assert 999100 in patient_ids_seen, (
        f"Expected patient_id=999100 forwarded to execute_tool; "
        f"saw patient_ids: {patient_ids_seen!r}"
    )


# ---------------------------------------------------------------------------
# Test 8: all 5 W1 tools called when patient_id present (decision #3)
# ---------------------------------------------------------------------------


def test_all_five_w1_tools_called_when_patient_id_present() -> None:
    """Decision #3 — always-fetch-all-5 W1 tools when patient_id is in state.

    When patient_id is present and the query asks for a pre-visit brief (which
    covers all clinical domains), all five W1 tools must appear in worker_results.

    Regression class: if evidence_retriever_node silently skips tools because
    the keyword router is too restrictive, citations for whole clinical domains
    (e.g. allergies) will disappear from graph_chat responses without any
    error — a silent coverage regression.
    """
    from agent.graph.workers.evidence_retriever import evidence_retriever_node

    # A pre-visit brief query that covers all clinical domains.
    state = _make_state(
        query="give me a pre-visit brief for this patient",
        patient_id=999100,
        tool_calls_accumulated=[],
    )

    _W1_TOOLS = {
        "get_problem_list",
        "get_active_medications",
        "get_recent_labs",
        "get_allergies",
        "get_recent_encounters",
    }

    with patch("agent.graph.workers.evidence_retriever.execute_tool") as mock_exec:
        async def _fake_exec(tool_name: str, tool_input: dict) -> list:
            return []

        mock_exec.side_effect = _fake_exec
        patch_result = evidence_retriever_node(state)

    tools_called_names = {call.args[0] for call in mock_exec.call_args_list}
    missing = _W1_TOOLS - tools_called_names
    assert not missing, (
        f"evidence_retriever_node should call all 5 W1 tools for a broad query "
        f"with patient_id present; missing: {sorted(missing)!r}. "
        f"Tools actually called: {sorted(tools_called_names)!r}"
    )


# ---------------------------------------------------------------------------
# Test 9: guideline keyword triggers additive search_guidelines
# ---------------------------------------------------------------------------


def test_guideline_keyword_triggers_additive_search_guidelines() -> None:
    """Decision #3 — guideline keyword is additive (both W1 + search_guidelines).

    When query contains a GUIDELINE_KEYWORD (e.g. "ADA target") AND patient_id
    is in state, both the 5 W1 tools AND search_guidelines should be called.

    Regression class: if search_guidelines is gated on "no patient data path",
    guideline citations will never appear alongside patient-record citations
    in graph_chat responses — breaking UC3 (guideline-grounded advice).
    """
    from agent.graph.workers.evidence_retriever import evidence_retriever_node

    state = _make_state(
        query="ADA target for A1c — is this patient at goal?",
        patient_id=999100,
        tool_calls_accumulated=[],
    )

    mock_chunk = {
        "chunk_id": "ada-2024-s6-2-chunk-1",
        "section": "S6.2",
        "source_url": "https://example.com/ada",
        "source_attribution": "ADA 2024",
        "body": "The A1C target for most nonpregnant adults is <7%.",
        "leading_excerpt": "The A1C target for most nonpregnant adults is <7%.",
        "score": 0.91,
    }

    with patch("agent.graph.workers.evidence_retriever.search_guidelines") as mock_sg, \
         patch("agent.graph.workers.evidence_retriever.execute_tool") as mock_exec:

        mock_sg.return_value = [mock_chunk]

        async def _fake_exec(tool_name: str, tool_input: dict) -> list:
            return []

        mock_exec.side_effect = _fake_exec
        patch_result = evidence_retriever_node(state)

    assert mock_sg.called, (
        "search_guidelines should be called when query contains a guideline keyword "
        "(e.g. 'ADA') even when patient_id is present — additive, not exclusive"
    )
    assert mock_exec.called, (
        "execute_tool (W1 tools) should ALSO be called alongside search_guidelines"
    )

    # At least one guideline citation should appear in the result
    citations = patch_result.get("citations", [])
    guideline_cites = [c for c in citations if c.source_type == "guideline"]
    assert guideline_cites, (
        f"Expected guideline citation when query contains ADA keyword; got {citations!r}"
    )


# ---------------------------------------------------------------------------
# Test 10: guideline keyword OFF — no search_guidelines call
# ---------------------------------------------------------------------------


def test_guideline_keyword_absent_no_search_guidelines_call() -> None:
    """Decision #3 — search_guidelines NOT called when query lacks guideline keywords.

    When patient_id is present and the query asks only for medications (no
    guideline keyword), only the W1 patient-record tools should fire. The
    search_guidelines function should NOT be invoked.

    Regression class: if search_guidelines is always called regardless of query
    shape, the token budget per request grows silently and guideline citations
    will appear even for queries that didn't ask for clinical guidance —
    confusing the PCP with unsolicited guideline references.
    """
    from agent.graph.workers.evidence_retriever import evidence_retriever_node

    state = _make_state(
        query="show me his medications",
        patient_id=999100,
        tool_calls_accumulated=[],
    )

    with patch("agent.graph.workers.evidence_retriever.search_guidelines") as mock_sg, \
         patch("agent.graph.workers.evidence_retriever.execute_tool") as mock_exec:

        mock_sg.return_value = []

        async def _fake_exec(tool_name: str, tool_input: dict) -> list:
            return []

        mock_exec.side_effect = _fake_exec
        patch_result = evidence_retriever_node(state)

    assert not mock_sg.called, (
        "search_guidelines should NOT be called when query lacks guideline keywords "
        "(query='show me his medications'). "
        f"mock_sg.call_count={mock_sg.call_count}"
    )
    assert mock_exec.called, (
        "execute_tool (W1 patient-record tools) should still be called for a "
        "medication query with patient_id present"
    )


# ---------------------------------------------------------------------------
# Test 11: supervisor escalates to Sonnet on bad JSON (decision #4)
# ---------------------------------------------------------------------------


def test_supervisor_escalates_to_sonnet_on_bad_json() -> None:
    """Decision #4 — supervisor escalates to Sonnet when Haiku returns non-JSON.

    Mock the LLM client to return non-JSON on the first call (Haiku attempt)
    and valid JSON on the second call (Sonnet escalation). Assert that:
      1. At least 2 LLM calls are made.
      2. The second call uses model_reasoning (Sonnet 4.6).

    Regression class: if escalation is removed or short-circuited, bad-JSON
    from Haiku causes a silent no_route termination instead of a retry — all
    graph_chat requests fall through to RefusalResponse on any transient
    Haiku JSON parse error.
    """
    from agent.graph.supervisor import supervisor_node
    from agent.config import get_settings
    settings = get_settings()

    state = _make_state(
        query="pre-visit brief for this patient",
        patient_id=999100,
    )

    call_count = [0]
    models_used: list[str] = []

    async def _mock_create(
        *,
        model: str,
        system: object,
        messages: object,
        max_tokens: int = 128,
        temperature: float = 0.0,
        **kwargs: object,
    ) -> MagicMock:
        call_count[0] += 1
        models_used.append(model)

        text_block = MagicMock()
        text_block.type = "text"

        if call_count[0] == 1:
            # First call (Haiku): non-JSON response to trigger escalation
            text_block.text = "I cannot determine the route for this query."
        else:
            # Second call (Sonnet): valid JSON
            text_block.text = '{"route": "evidence_retriever", "rationale": "test"}'

        msg = MagicMock()
        msg.content = [text_block]
        msg.stop_reason = "end_turn"
        msg.usage = MagicMock(input_tokens=50, output_tokens=20)
        return msg

    mock_client = MagicMock()
    mock_client.create = _mock_create

    with patch("agent.graph.supervisor.build_llm_client", return_value=mock_client):
        patch_result = supervisor_node(state)

    # Must have made at least 2 LLM calls (Haiku + Sonnet escalation)
    assert call_count[0] >= 2, (
        f"Expected at least 2 LLM calls (Haiku + Sonnet escalation), "
        f"got {call_count[0]}. models used: {models_used!r}"
    )

    # Second call must use model_reasoning (Sonnet 4.6)
    assert any(m == settings.model_reasoning for m in models_used[1:]), (
        f"Expected Sonnet (model_reasoning={settings.model_reasoning!r}) on retry; "
        f"models used in order: {models_used!r}"
    )


# ---------------------------------------------------------------------------
# Test 12: no_route bypasses responder (decision #9)
# ---------------------------------------------------------------------------


def test_no_route_bypasses_responder() -> None:
    """Decision #9 — no_route supervisor terminal bypasses the responder node.

    When supervisor returns "none" with empty worker_results (nothing was
    retrieved), the graph should terminate with terminal_reason='no_route'
    and the responder node should never be invoked.

    Regression class: if the router sends no_route to the responder, the
    responder attempts to synthesize from empty evidence and either fabricates
    or produces an opaque error — the correct behavior is a clean RefusalResponse.
    """
    from agent.graph.builder import _supervisor_router

    # State with no_route terminal reason and no worker results.
    state = _make_state(
        terminal_reason="no_route",
        worker_results=[],
    )

    result = _supervisor_router(state)

    from langgraph.graph import END
    assert result == END, (
        f"no_route should route to END (bypassing responder), got {result!r}"
    )

    # Confirm terminal_reason is preserved in state for the /graph_chat handler
    # to inspect and return the correct RefusalResponse.
    assert state.get("terminal_reason") == "no_route"


def test_no_route_full_graph_bypasses_responder_node() -> None:
    """Full graph integration check: supervisor returns no_route, graph ends at END.

    Build the full graph, mock the supervisor LLM to return an invalid route
    ("evil_node") which triggers terminal_reason='no_route'. Verify the
    final state has terminal_reason='no_route' and final_response is None
    (responder never ran because no_route bypasses it).
    """
    from agent.graph.builder import build_supervisor_graph

    # _call_supervisor_llm now returns a 4-tuple (route, tok_in, tok_out, escalated)
    with patch("agent.graph.supervisor._call_supervisor_llm",
               return_value=("evil_node", 50, 20, False)):
        graph = build_supervisor_graph()
        initial_state = _make_state(
            query="",
            patient_id=999100,
        )
        final_state = graph.invoke(initial_state)

    assert final_state.get("terminal_reason") == "no_route", (
        f"Expected terminal_reason='no_route', got {final_state.get('terminal_reason')!r}"
    )
    # final_response must be None — responder never ran
    assert final_state.get("final_response") is None, (
        "Responder node must NOT set final_response when terminal_reason='no_route'"
    )


# ---------------------------------------------------------------------------
# Test 13: supervisor_max_hops still routes to responder (decision #9)
# ---------------------------------------------------------------------------


def test_supervisor_max_hops_routes_to_responder() -> None:
    """Decision #9 — supervisor_max_hops routes to responder (partial evidence ok).

    When hops_taken reaches the 4-hop cap, supervisor sets
    terminal_reason='supervisor_max_hops'. The builder's _supervisor_router
    must route to 'responder' (not END directly) per decision #9.

    The responder then synthesizes from partial citations and sets
    terminal_reason='responded'.

    Regression class: if max_hops routes to END instead of responder, the
    user receives an empty response after 4 hops even when partial evidence
    was retrieved — silent data loss.
    """
    from agent.graph.builder import _supervisor_router

    state = _make_state(
        terminal_reason="supervisor_max_hops",
        worker_results=[
            {"worker_name": "evidence_retriever", "citations_added": 2},
        ],
    )

    result = _supervisor_router(state)

    assert result == "responder", (
        f"supervisor_max_hops must route to 'responder' (partial evidence synthesis). "
        f"Decision #9: max_hops → responder, only no_route bypasses responder. "
        f"Got: {result!r}"
    )
