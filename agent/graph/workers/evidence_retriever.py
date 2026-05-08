"""Evidence retriever worker node (W2 Stage-3b, graph phase).

Wraps search_guidelines() (Stage-3a hybrid RAG) and the W1 patient-record
tools (get_problem_list, get_active_medications, get_recent_labs, get_allergies,
get_recent_encounters) via execute_tool().

Retrieval strategy (decision #3 — replaces old keyword-router heuristic):
  - When state["patient_id"] is present, ALWAYS fetch all 5 W1 patient tools
    in parallel (mirrors agent.py:fetch_baseline_context). No keyword routing.
  - ADDITIVELY call search_guidelines when any token from _GUIDELINE_KEYWORDS
    appears in query.lower(). This is an additive call — patient data fetch
    always fires first when patient_id is present.
  - When patient_id is absent: call search_guidelines only.

W2_ARCHITECTURE.md §3.4 (worker isolation), §3.3 (span: worker.evidence_retriever).

No-PHI discipline (§8.3):
  - Langfuse span tags: worker_name, n_citations_added, latency_ms, tool_called.
    NEVER raw query text or chunk text.
  - Exceptions are scrubbed before logging.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from agent._phi_scrubber import mask_observability_patterns
from agent.graph.state import SupervisorState
from agent.schemas import Citation
from agent.tools import execute_tool, search_guidelines

logger = logging.getLogger(__name__)

_WORKER_NAME = "evidence_retriever"

# All 5 W1 patient-record tools, fetched in parallel when patient_id is present.
# Mirrors _BASELINE_TOOLS in agent/agent.py:fetch_baseline_context.
_ALL_PATIENT_TOOLS = (
    "get_problem_list",
    "get_active_medications",
    "get_recent_labs",
    "get_allergies",
    "get_recent_encounters",
)

# Keywords indicating the query needs guideline evidence.
# Low-bar set — any overlap triggers the additive search_guidelines call.
_GUIDELINE_KEYWORDS = frozenset({
    "guideline", "ada", "uspstf", "recommendation", "recommendations",
    "evidence", "target", "goal", "threshold", "standard", "protocol",
    "consensus", "clinical practice", "jnc", "aha",
})

# TODO: centralize pricing constants
# Haiku 4.5: $1.00/MTok input, $5.00/MTok output (no LLM call in this worker)
# (included for future cost attribution if LLM is added to evidence retrieval)


def evidence_retriever_node(state: SupervisorState) -> dict[str, Any]:
    """LangGraph worker node: evidence retrieval.

    Decision #3: always fetch all 5 W1 patient tools when patient_id is
    present. Additively call search_guidelines when query mentions
    _GUIDELINE_KEYWORDS. Builds Citation objects and appends to state.

    Returns a state patch. LangGraph merges it automatically.
    """
    try:
        from langfuse import get_client
        lf = get_client()
        _langfuse_available = True
    except Exception:
        lf = None
        _langfuse_available = False

    t0 = time.monotonic()
    new_citations: list[Citation] = []
    new_records: list[Any] = []
    n_citations_added = 0
    tools_called: list[str] = []

    with _langfuse_span(lf, _langfuse_available) as _span:
        try:
            query = state.get("query", "")
            # Decision #7: patient_id is now a top-level SupervisorState field.
            patient_id = state.get("patient_id")

            if patient_id is not None:
                # Decision #3: always fetch all 5 W1 patient tools in parallel.
                patient_citations, patient_records, patient_tools = _fetch_all_patient_records(
                    patient_id
                )
                new_citations.extend(patient_citations)
                new_records.extend(patient_records)
                tools_called.extend(patient_tools)

            # Additive: fetch guidelines when query has relevant keywords.
            if _query_needs_guidelines(query):
                guideline_citations, guideline_records, guideline_tool = _fetch_guidelines(query)
                new_citations.extend(guideline_citations)
                new_records.extend(guideline_records)
                tools_called.append(guideline_tool)

            # If no patient_id and no guideline keywords, fall back to guidelines.
            if not tools_called:
                guideline_citations, guideline_records, guideline_tool = _fetch_guidelines(query)
                new_citations.extend(guideline_citations)
                new_records.extend(guideline_records)
                tools_called.append(guideline_tool)

            n_citations_added = len(new_citations)
        except Exception as exc:
            scrubbed = mask_observability_patterns(str(exc))
            logger.error(
                "evidence_retriever_node failed: %s",
                type(exc).__name__,
            )
            logger.debug("evidence_retriever_node scrubbed error: %s", scrubbed)

        latency_ms = int((time.monotonic() - t0) * 1000)
        tool_called_str = ",".join(tools_called) if tools_called else "none"
        if _langfuse_available and lf is not None:
            try:
                lf.update_current_span(
                    metadata={
                        "worker_name": _WORKER_NAME,
                        "n_citations_added": n_citations_added,
                        "tool_called": tool_called_str,
                        "latency_ms": latency_ms,
                        "retrieval_hits": n_citations_added,
                    }
                )
            except Exception:
                pass

    # Build state patch.
    existing_citations = list(state.get("citations", []))
    existing_citations.extend(new_citations)

    existing_tool_calls = list(state.get("tool_calls_accumulated", []))
    existing_tool_calls.append({
        "name": _WORKER_NAME,
        "tool_called": tool_called_str,
        "citations_added": n_citations_added,
        "latency_ms": latency_ms,
    })

    # Decision #10: worker_results aligned to ToolCallSummary shape.
    existing_results = list(state.get("worker_results", []))
    existing_results.append({
        "tool_name": _WORKER_NAME,
        "params": {
            "patient_id": state.get("patient_id"),
            "query_chars": len(state.get("query", "")),
        },
        "latency_ms": latency_ms,
        "success": True,
        "record_count": n_citations_added,
        "error": None,
        # Legacy keys for supervisor context message compatibility.
        "worker_name": _WORKER_NAME,
        "citations_added": n_citations_added,
        "tool_called": tool_called_str,
    })

    # Decision #14: per-node observability entry.
    obs_entry = {
        "node_name": _WORKER_NAME,
        "latency_ms": latency_ms,
        "tokens_input": 0,   # no LLM call in this worker
        "tokens_output": 0,
        "cost_estimate_usd": 0.0,
        "retrieval_hits": n_citations_added,
        "extraction_confidence": None,
    }
    existing_obs = list(state.get("node_observability", []))
    existing_obs.append(obs_entry)

    # Accumulate full RetrievedRecord objects so the responder hydrates
    # the LLM with real diagnosis text / drug names / lab values rather
    # than just `table:id` references derived from citations.
    existing_records = list(state.get("retrieved_records", []))
    existing_records.extend(new_records)

    return {
        "citations": existing_citations,
        "tool_calls_accumulated": existing_tool_calls,
        "worker_results": existing_results,
        "node_observability": existing_obs,
        "retrieved_records": existing_records,
    }


def _query_needs_guidelines(query: str) -> bool:
    """Deterministic heuristic: does this query need guideline evidence?

    Checks for overlap between lower-cased query tokens and _GUIDELINE_KEYWORDS.
    No LLM involved — pure string matching.
    """
    query_lower = query.lower()
    return any(kw in query_lower for kw in _GUIDELINE_KEYWORDS)


def _fetch_guidelines(query: str) -> tuple[list[Citation], list[Any], str]:
    """Call search_guidelines and return (citations, retrieved_records, tool_called).

    The retrieved_records carry the full chunk content (body, section,
    source attribution) under table=`guideline_corpus` so the responder's
    `_format_patient_context()` renders them as `<guideline>` blocks in the
    LLM's system prompt — same contract as patient records. Without these,
    the citations are present in state but the responder LLM can't cite
    them in claims because it never saw the chunk text.

    Mirrors the worker→responder bridging fix in
    `_fetch_all_patient_records` (2026-05-07): citations alone aren't
    enough — the responder needs the actual content rendered into its
    system prompt.
    """
    from agent.schemas import CitationStrength, RetrievedRecord

    chunks = search_guidelines(query, top_k=5)
    citations: list[Citation] = []
    retrieved_records: list[Any] = []
    for chunk in chunks:
        chunk_id = chunk["chunk_id"]
        citations.append(
            Citation(
                source_type="guideline",
                source_id=chunk_id,
                page_or_section=chunk.get("section"),
                field_or_chunk_id=chunk_id,
                quote_or_value=chunk.get("leading_excerpt", chunk_id),
                bbox=None,
            )
        )
        # Pipe the full chunk content through to the responder via state.
        # Table name is `guideline_corpus` — agent.agent._format_patient_context
        # special-cases this to render as <guideline> blocks (vs. <patient_record>).
        retrieved_records.append(
            RetrievedRecord(
                table="guideline_corpus",
                record_id=chunk_id,
                citation_strength=CitationStrength.FREE_TEXT,
                fields={
                    "body": chunk.get("body", chunk.get("leading_excerpt", "")),
                    "section": chunk.get("section"),
                    "source_attribution": chunk.get("source_attribution"),
                    "source_url": chunk.get("source_url"),
                },
            )
        )
    return citations, retrieved_records, "search_guidelines"


def _fetch_all_patient_records(
    patient_id: int,
) -> tuple[list[Citation], list[Any], list[str]]:
    """Fetch all 5 W1 patient tools in parallel (decision #3).

    Mirrors agent.py:fetch_baseline_context but adapted for the sync
    LangGraph node context. All 5 tools are always called regardless of
    query content — the evidence_retriever worker has no keyword router.

    Returns (citations, retrieved_records, tools_called_list).

    The retrieved_records carry the full RetrievedRecord (table, record_id,
    citation_strength, fields) so the responder can give the LLM the actual
    record content (diagnosis text, drug names, lab values), not just
    citation IDs. Without these, the LLM correctly reports
    "no clinical content available" because the citations alone carry only
    `table:id` references.
    """
    tool_input = {"patient_id": patient_id}
    citations: list[Citation] = []
    retrieved_records: list[Any] = []
    tools_called: list[str] = []

    for tool_name in _ALL_PATIENT_TOOLS:
        try:
            records = _run_tool_sync(execute_tool, tool_name, tool_input)
            tools_called.append(tool_name)
            for rec in records:
                citations.append(
                    Citation(
                        source_type="patient_record",
                        source_id=f"{rec.table}:{rec.record_id}",
                        page_or_section=None,
                        field_or_chunk_id=rec.record_id,
                        quote_or_value=f"{rec.table}:{rec.record_id}",
                        bbox=None,
                    )
                )
                # Preserve the full record (with fields) so the responder
                # can hydrate retrieved_records with real content rather
                # than empty `fields={}` from the citation-only conversion.
                retrieved_records.append(rec)
        except Exception as exc:
            scrubbed = mask_observability_patterns(str(exc))
            logger.error(
                "evidence_retriever: tool %s failed: %s",
                tool_name,
                type(exc).__name__,
            )
            logger.debug("evidence_retriever tool error (scrubbed): %s", scrubbed)
            tools_called.append(tool_name)  # still record the attempt

    return citations, retrieved_records, tools_called


def _run_tool_sync(tool_fn: Any, tool_name: str, tool_input: dict[str, Any]) -> list[Any]:
    """Run an async tool function synchronously.

    execute_tool is async; we need to call it from a sync context (LangGraph
    node). Handles both running and non-running event loops.
    Python 3.12: use get_running_loop() to detect running loop safely.
    """
    coro = tool_fn(tool_name, tool_input)

    try:
        asyncio.get_running_loop()
        _loop_running = True
    except RuntimeError:
        _loop_running = False

    if _loop_running:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result(timeout=30)
    else:
        return asyncio.run(coro)


class _langfuse_span:
    """Context manager that opens a Langfuse span named 'worker.evidence_retriever'
    when Langfuse is available, and is a no-op otherwise.

    W2_ARCHITECTURE.md §3.3: every worker body is a named span.
    """

    def __init__(self, lf: Any, available: bool) -> None:
        self._lf = lf
        self._available = available
        self._span = None

    def __enter__(self) -> "_langfuse_span":
        if self._available and self._lf is not None:
            try:
                # Use start_observation (non-context-manager form) — matches installed
                # Langfuse version; start_span is not available in this version.
                self._span = self._lf.start_observation(name="worker.evidence_retriever")
            except Exception:
                self._span = None
        return self

    def __exit__(self, *_: object) -> None:
        if self._span is not None:
            try:
                self._span.end()
            except Exception:
                pass
