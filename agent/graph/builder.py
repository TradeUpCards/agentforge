"""Graph builder — constructs and compiles the LangGraph supervisor graph.

build_supervisor_graph() is the single public function. It wires up:
  - supervisor_node (entry point, routing + hop-cap enforcement)
  - intake_extractor_node (Stage-2 document extraction worker)
  - evidence_retriever_node (Stage-3a hybrid RAG + W1 patient-record worker)

Graph shape (W2_ARCHITECTURE.md §3.1):
  supervisor → worker (by route) → supervisor (loop) → END (when terminal)

Routing is determined by inspecting the state after each supervisor call:
  - state['terminal_reason'] is set → END
  - state['hops_taken'] incremented → route to the worker the supervisor chose.

The supervisor encodes its routing decision as the 'hops_taken' increment +
the updated state. The conditional edge function reads the last entry in
state['tool_calls_accumulated'] to recover which worker was chosen, OR
checks terminal_reason for the stop condition.

Because the supervisor returns {hops_taken: N+1} on a valid route and
{terminal_reason: ...} on a stop, the edge function has unambiguous signals.

Worker → supervisor edge is always unconditional (workers always return to
supervisor per §3.4 worker isolation rule).

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

# Node names — referenced in edges; defined once to avoid string drift.
_SUPERVISOR = "supervisor"
_INTAKE_EXTRACTOR = "intake_extractor"
_EVIDENCE_RETRIEVER = "evidence_retriever"


def build_supervisor_graph() -> CompiledStateGraph:
    """Build and compile the LangGraph supervisor graph.

    Returns a CompiledStateGraph ready for .invoke() or .stream() calls.
    The graph enforces the 4-hop cap in supervisor_node; this function only
    declares the topology.

    Node wiring:
      entry:  supervisor
      edges:  supervisor → {intake_extractor | evidence_retriever | END}
              intake_extractor → supervisor
              evidence_retriever → supervisor

    Returns:
        CompiledStateGraph
    """
    graph = StateGraph(SupervisorState)

    # Register nodes.
    graph.add_node(_SUPERVISOR, supervisor_node)
    graph.add_node(_INTAKE_EXTRACTOR, intake_extractor_node)
    graph.add_node(_EVIDENCE_RETRIEVER, evidence_retriever_node)

    # Entry point.
    graph.set_entry_point(_SUPERVISOR)

    # Supervisor → worker (conditional) or END.
    graph.add_conditional_edges(
        _SUPERVISOR,
        _supervisor_router,
        {
            _INTAKE_EXTRACTOR: _INTAKE_EXTRACTOR,
            _EVIDENCE_RETRIEVER: _EVIDENCE_RETRIEVER,
            END: END,
        },
    )

    # Workers always return to supervisor (§3.4 worker isolation).
    graph.add_edge(_INTAKE_EXTRACTOR, _SUPERVISOR)
    graph.add_edge(_EVIDENCE_RETRIEVER, _SUPERVISOR)

    return graph.compile()


def _supervisor_router(state: SupervisorState) -> str:
    """Conditional edge function: maps state to the next node name.

    Called by LangGraph after supervisor_node returns a state patch.
    Reads terminal_reason and the last tool_calls_accumulated entry to
    determine the next node.

    Returns:
        Node name: 'intake_extractor', 'evidence_retriever', or END sentinel.
    """
    # Terminal conditions.
    terminal_reason = state.get("terminal_reason")
    if terminal_reason is not None:
        return END

    # Determine which worker the supervisor chose by inspecting the last
    # tool_calls_accumulated entry. The supervisor doesn't write to
    # tool_calls_accumulated directly — it's the workers that do. So on the
    # FIRST call, accumulated is empty and hops_taken just incremented.
    # We use hops_taken mod-based detection: the supervisor stores its
    # routing intent in _pending_route via a side channel in the state.
    #
    # Design note: we use a '_pending_route' key in the state to carry the
    # supervisor's decision to the edge function without polluting
    # tool_calls_accumulated (which is worker-written structural metadata).
    pending_route = state.get("_pending_route")  # type: ignore[typeddict-item]
    if pending_route == "intake_extractor":
        return _INTAKE_EXTRACTOR
    if pending_route == "evidence_retriever":
        return _EVIDENCE_RETRIEVER

    # Fallback: if no pending_route, go to END (max_hops or no_route).
    return END
