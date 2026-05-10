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

from agent.schemas import AgentResponse, Citation, RefusalResponse, RetrievedRecord


class SupervisorState(TypedDict):
    """Mutable graph state passed between supervisor and worker nodes.

    Fields
    ------
    query : str
        The incoming clinical query from the user. Used by the supervisor to
        choose a route and by workers to scope their tool calls.

    patient_id : int | None
        Top-level patient context for this graph run. Set by the /graph_chat
        handler from the ChatRequest body. evidence_retriever_node reads this
        directly (decision #7 — no longer buried in tool_calls_accumulated).

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
          "responded"           — responder node completed synthesis successfully
        None while the graph is still running.

    worker_results : list[dict]
        Structured summaries returned by each worker. Shape aligns to
        ToolCallSummary (decision #10): tool_name, params, latency_ms,
        success, record_count, error. Structural only — no chunk text or
        extracted field values.

    final_response : AgentResponse | RefusalResponse | None
        Written by the responder node after synthesis. The /graph_chat handler
        reads this after graph.invoke() completes. None until the responder runs.

    node_observability : list[dict]
        Per-node 7-field observability records (decision #14). Each entry:
        node_name, latency_ms, tokens_input, tokens_output,
        cost_estimate_usd, retrieval_hits, extraction_confidence.
        Appended by each node that incurs LLM or retrieval cost.

    retrieved_records : list[RetrievedRecord]
        Full record content (table, record_id, citation_strength, fields)
        accumulated by retrieval workers. The responder reads these directly
        to give the LLM the actual diagnosis text, drug names, lab values,
        etc. — not just citation IDs. Without this, citations alone carry
        only `table:id` references and the LLM (correctly) reports back
        "no clinical content available." Populated by evidence_retriever
        from the W1 patient tools' RetrievedRecord output.

    extraction_payload : Optional[dict]
        Set by intake_extractor_node when the graph is invoked for document
        extraction (the /attach_and_extract HTTP path).  Carries the full
        result of attach_and_extract_with_metadata_async() so the endpoint
        can read it from the final state and build its HTTP response:
            {result: dict (LabReport|IntakeForm model_dump),
             docling_blocks: list[dict],
             field_verdicts: list[dict],
             original_values: dict,
             n_blocks: int,
             n_pages: int,
             extraction_confidence_avg: float}
        None on chat-only graph runs (intake_extractor not invoked, or
        invoked without a doc context — the worker silently skips).

        PHI: this field carries the structured extraction result which
        includes patient values.  Same posture as retrieved_records —
        held in graph memory only, NOT logged or traced.  The supervisor
        does NOT pass this field to its routing LLM; only its presence/
        absence affects routing (a structural signal).
    """

    query: str
    patient_id: Optional[int]
    tool_calls_accumulated: list[dict]
    citations: list[Citation]
    hops_taken: int
    terminal_reason: Optional[str]
    worker_results: list[dict]
    final_response: Optional[AgentResponse | RefusalResponse]
    node_observability: list[dict]
    retrieved_records: list[RetrievedRecord]
    extraction_payload: Optional[dict]

    # Internal routing signal: set by supervisor_node so _supervisor_router
    # can determine which worker to dispatch to without a second LLM call.
    # Narrowed to Literal so future code cannot accidentally write free-text
    # (which could carry rationale leakage — Fix #4 obs-sec).
    # Cleared (set to None) after each worker call. Not logged or traced.
    _pending_route: Optional[Literal["intake_extractor", "evidence_retriever"]]
