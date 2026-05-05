"""Evidence retriever worker node (W2 Stage-3b).

Wraps search_guidelines() (Stage-3a hybrid RAG) and the W1 patient-record
tools (get_problem_list, get_active_medications, get_recent_labs, get_allergies,
get_recent_encounters) via execute_tool().

Routing heuristic (deterministic, no LLM call):
  - If the query mentions clinical-record keywords (medication, allergy, lab,
    encounter, prescription, problem, condition, history, diagnosis) AND a
    patient_id is available in state → call one or more W1 patient tools.
  - Otherwise → call search_guidelines (guideline evidence).
  - When both are relevant, call guidelines first (cheaper, no DB).

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

# Keywords that indicate the query needs patient-record tools (W1).
_PATIENT_RECORD_KEYWORDS = frozenset({
    "medication", "medications", "drug", "drugs", "prescription", "prescriptions",
    "allergy", "allergies", "lab", "labs", "laboratory", "result", "results",
    "encounter", "encounters", "visit", "problem", "problems", "condition",
    "conditions", "history", "diagnosis", "diagnoses", "hba1c", "a1c",
    "glucose", "blood pressure", "bp",
})


def evidence_retriever_node(state: SupervisorState) -> dict[str, Any]:
    """LangGraph worker node: evidence retrieval.

    Decides between guideline search and patient-record tools based on
    the query content. Builds Citation objects and appends to state.

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
    n_citations_added = 0
    tool_called = "none"

    with _langfuse_span(lf, _langfuse_available) as _span:
        try:
            query = state.get("query", "")
            patient_id = _extract_patient_id(state)
            needs_patient_data = _query_needs_patient_data(query)

            if needs_patient_data and patient_id is not None:
                new_citations, tool_called = _fetch_patient_records(patient_id, query)
            else:
                new_citations, tool_called = _fetch_guidelines(query)

            n_citations_added = len(new_citations)
        except Exception as exc:
            scrubbed = mask_observability_patterns(str(exc))
            logger.error(
                "evidence_retriever_node failed: %s",
                type(exc).__name__,
            )
            logger.debug("evidence_retriever_node scrubbed error: %s", scrubbed)

        latency_ms = int((time.monotonic() - t0) * 1000)
        if _langfuse_available and lf is not None:
            try:
                lf.update_current_span(
                    metadata={
                        "worker_name": _WORKER_NAME,
                        "n_citations_added": n_citations_added,
                        "tool_called": tool_called,
                        "latency_ms": latency_ms,
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
        "tool_called": tool_called,
        "citations_added": n_citations_added,
        "latency_ms": latency_ms,
    })

    existing_results = list(state.get("worker_results", []))
    existing_results.append({
        "worker_name": _WORKER_NAME,
        "tool_called": tool_called,
        "citations_added": n_citations_added,
        "record_count": n_citations_added,
        "latency_ms": latency_ms,
    })

    return {
        "citations": existing_citations,
        "tool_calls_accumulated": existing_tool_calls,
        "worker_results": existing_results,
    }


def _query_needs_patient_data(query: str) -> bool:
    """Deterministic heuristic: does this query need patient-record tools?

    Checks for overlap between lower-cased query tokens and the set of
    patient-record keywords. No LLM involved — pure string matching.
    """
    query_lower = query.lower()
    return any(kw in query_lower for kw in _PATIENT_RECORD_KEYWORDS)


def _extract_patient_id(state: SupervisorState) -> int | None:
    """Find the patient_id from tool_calls_accumulated doc context, if present."""
    for entry in state.get("tool_calls_accumulated", []):
        if "patient_id" in entry:
            try:
                return int(entry["patient_id"])
            except (TypeError, ValueError):
                pass
    return None


def _fetch_guidelines(query: str) -> tuple[list[Citation], str]:
    """Call search_guidelines and return (citations, tool_called)."""
    chunks = search_guidelines(query, top_k=5)
    citations: list[Citation] = []
    for chunk in chunks:
        citations.append(
            Citation(
                source_type="guideline",
                source_id=chunk["chunk_id"],
                page_or_section=chunk.get("section"),
                field_or_chunk_id=chunk["chunk_id"],
                quote_or_value=chunk.get("leading_excerpt", chunk["chunk_id"]),
                bbox=None,
            )
        )
    return citations, "search_guidelines"


def _fetch_patient_records(patient_id: int, query: str) -> tuple[list[Citation], str]:
    """Call W1 patient-record tools and return (citations, tool_called).

    Selects which W1 tools to call based on query content. Calls are
    synchronous (W1 tools are sync; execute_tool is async so we run it).
    """
    # Decide which tools to invoke (structural heuristic, no LLM).
    tools_to_call: list[str] = []
    query_lower = query.lower()

    if any(kw in query_lower for kw in ("medication", "drug", "prescription")):
        tools_to_call.append("get_active_medications")
    if any(kw in query_lower for kw in ("allergy", "allergies")):
        tools_to_call.append("get_allergies")
    if any(kw in query_lower for kw in ("lab", "result", "hba1c", "a1c", "glucose")):
        tools_to_call.append("get_recent_labs")
    if any(kw in query_lower for kw in ("encounter", "visit")):
        tools_to_call.append("get_recent_encounters")
    if any(kw in query_lower for kw in ("problem", "condition", "history", "diagnosis")):
        tools_to_call.append("get_problem_list")

    # Default: if no keyword matched, call problem list as safest default.
    if not tools_to_call:
        tools_to_call = ["get_problem_list"]

    citations: list[Citation] = []
    tool_input = {"patient_id": patient_id}

    for tool_name in tools_to_call:
        records = _run_tool_sync(execute_tool, tool_name, tool_input)
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

    tool_called = ",".join(tools_to_call) if tools_to_call else "none"
    return citations, tool_called


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
