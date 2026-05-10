"""Graph builder — constructs and compiles the LangGraph supervisor graph.

build_supervisor_graph() is the single public function. It wires up:
  - supervisor_node (entry point, routing + hop-cap enforcement)
  - intake_extractor_node (Stage-2 document extraction worker)
  - evidence_retriever_node (Stage-3a hybrid RAG + W1 patient-record worker)
  - responder_node (NEW — W2 graph phase, terminal synthesis node)

Graph shape (W2_ARCHITECTURE.md §3.1, updated for graph phase):
  supervisor → {worker(s)} → supervisor (loop) → responder → END
  supervisor (no_route)    → END  [bypasses responder]

Routing is determined by inspecting the state after each supervisor call:
  - state['terminal_reason'] == "answered" or "supervisor_max_hops"
    → route to responder (synthesis)
  - state['terminal_reason'] == "no_route"
    → route to END (bypass responder, handler returns RefusalResponse)
  - state['_pending_route'] is set → route to the named worker

The supervisor encodes its routing decision as the 'hops_taken' increment +
the updated state. The conditional edge function reads _pending_route to
determine which worker was chosen, or terminal_reason for the stop condition.

Worker → supervisor edge is always unconditional (workers always return to
supervisor per §3.4 worker isolation rule).

Responder → END edge is always unconditional (terminal node).

W2_ARCHITECTURE.md §3.1, §3.2, §3.5.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from agent.graph.state import SupervisorState
from agent.graph.supervisor import supervisor_node
from agent.graph.workers.evidence_retriever import evidence_retriever_node
from agent.graph.workers.intake_extractor import intake_extractor_node
from agent.graph.workers.responder import responder_node

# Node names — referenced in edges; defined once to avoid string drift.
_SUPERVISOR = "supervisor"
_INTAKE_EXTRACTOR = "intake_extractor"
_EVIDENCE_RETRIEVER = "evidence_retriever"
_RESPONDER = "responder"


def build_supervisor_graph() -> CompiledStateGraph:
    """Build and compile the LangGraph supervisor graph.

    Returns a CompiledStateGraph ready for .invoke() or .stream() calls.
    The graph enforces the 4-hop cap in supervisor_node; this function only
    declares the topology.

    Node wiring:
      entry:  supervisor
      edges:  supervisor → {intake_extractor | evidence_retriever | responder | END}
              intake_extractor → supervisor
              evidence_retriever → supervisor
              responder → END

    The responder fires when terminal_reason is "answered" or "supervisor_max_hops".
    When terminal_reason is "no_route", the graph routes directly to END so the
    /graph_chat handler can return RefusalResponse without going through responder.

    Returns:
        CompiledStateGraph
    """
    graph = StateGraph(SupervisorState)

    # Register nodes.
    graph.add_node(_SUPERVISOR, supervisor_node)
    graph.add_node(_INTAKE_EXTRACTOR, intake_extractor_node)
    graph.add_node(_EVIDENCE_RETRIEVER, evidence_retriever_node)
    graph.add_node(_RESPONDER, responder_node)

    # Entry point.
    graph.set_entry_point(_SUPERVISOR)

    # Supervisor → worker | responder | END (conditional).
    graph.add_conditional_edges(
        _SUPERVISOR,
        _supervisor_router,
        {
            _INTAKE_EXTRACTOR: _INTAKE_EXTRACTOR,
            _EVIDENCE_RETRIEVER: _EVIDENCE_RETRIEVER,
            _RESPONDER: _RESPONDER,
            END: END,
        },
    )

    # Workers always return to supervisor (§3.4 worker isolation).
    graph.add_edge(_INTAKE_EXTRACTOR, _SUPERVISOR)
    graph.add_edge(_EVIDENCE_RETRIEVER, _SUPERVISOR)

    # Responder is terminal — always routes to END.
    graph.add_edge(_RESPONDER, END)

    return graph.compile()


def _supervisor_router(state: SupervisorState) -> str:
    """Conditional edge function: maps state to the next node name.

    Called by LangGraph after supervisor_node returns a state patch.
    Reads terminal_reason and _pending_route to determine the next node.

    Updated for graph phase (decision #2):
      - "answered" or "supervisor_max_hops" → responder (not END directly)
      - "no_route" → END (bypass responder, /graph_chat handler returns RefusalResponse)
      - _pending_route set → the named worker

    Returns:
        Node name: 'intake_extractor', 'evidence_retriever', 'responder',
        or END sentinel.
    """
    terminal_reason = state.get("terminal_reason")

    if terminal_reason == "no_route":
        # Bypass responder — /graph_chat handler returns RefusalResponse directly.
        return END

    if terminal_reason == "extraction_complete":
        # Graph-routed extraction is done — payload is in state.extraction_payload.
        # Skip the responder (which would synthesize a chat answer); the
        # /extract_via_graph endpoint reads the payload from final_state
        # and builds its HTTP response directly.
        return END

    if terminal_reason in ("answered", "supervisor_max_hops"):
        # Enough evidence accumulated (or hop cap) — run synthesis.
        return _RESPONDER

    if terminal_reason is not None:
        # Unknown terminal reason — safe to route to END.
        return END

    # Determine which worker the supervisor chose via _pending_route.
    pending_route = state.get("_pending_route")  # type: ignore[typeddict-item]
    if pending_route == "intake_extractor":
        return _INTAKE_EXTRACTOR
    if pending_route == "evidence_retriever":
        return _EVIDENCE_RETRIEVER

    # Fallback: if no pending_route and no terminal_reason, go to END.
    return END
