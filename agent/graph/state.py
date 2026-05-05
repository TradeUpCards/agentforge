"""Supervisor graph state schema (W2 Stage-3b).

TypedDict is the LangGraph state contract — every node receives this dict
and returns a partial update. The graph merges updates automatically.

W2_ARCHITECTURE.md §3.2 (supervisor responsibilities), §3.4 (worker isolation).

No PHI discipline (§8.3):
  - query stored only for routing; never logged or traced
  - tool_calls_accumulated stores structural metadata only (names + counts),
    never raw tool output or extracted field values
  - worker_results stores structural summaries (chunk_ids, field counts),
    never chunk text or patient field values
"""

from __future__ import annotations

from typing import Literal, Optional

from typing_extensions import TypedDict

from agent.schemas import Citation


class SupervisorState(TypedDict):
    """Mutable graph state passed between supervisor and worker nodes.

    Fields
    ------
    query : str
        The incoming clinical query from the user. Used by the supervisor to
        choose a route and by workers to scope their tool calls.

    tool_calls_accumulated : list[dict]
        Structural metadata about each tool call made so far in this graph
        run. Each entry carries ``name`` (tool/worker name) and structural
        metadata only — ``chunk_count``, ``record_count``, ``latency_ms``.
        NEVER stores raw tool output, query text, or extracted field values.

    citations : list[Citation]
        Citation objects accumulated across all worker calls this run.
        Each Citation has a ``source_type`` discriminator so patient-record
        facts and guideline evidence remain separable (§7.3 contract).

    hops_taken : int
        Number of worker hops executed so far. Hard cap is 4 (§3.2).
        Each time the supervisor dispatches a worker, this increments by 1.

    terminal_reason : str | None
        Set when the graph terminates early or normally:
          "answered"            — supervisor decided the state has enough evidence
          "supervisor_max_hops" — 4-hop cap reached (named refusal per §3.2);
                                  logged as SUPERVISOR_MAX_HOPS_REACHED; used
                                  by agent_log aggregation queries (§3.2)
          "no_route"            — supervisor returned an unrecognised or null route
        None while the graph is still running.

    worker_results : list[dict]
        Structured summaries returned by each worker. Each entry includes
        ``worker_name``, ``chunk_ids`` or ``field_count``, ``latency_ms``.
        Structural only — no chunk text or extracted field values.
    """

    query: str
    tool_calls_accumulated: list[dict]
    citations: list[Citation]
    hops_taken: int
    terminal_reason: Optional[str]
    worker_results: list[dict]

    # Internal routing signal: set by supervisor_node so _supervisor_router
    # can determine which worker to dispatch to without a second LLM call.
    # Narrowed to Literal so future code cannot accidentally write free-text
    # (which could carry rationale leakage — Fix #4 obs-sec).
    # Cleared (set to None) after each worker call. Not logged or traced.
    _pending_route: Optional[Literal["intake_extractor", "evidence_retriever"]]
