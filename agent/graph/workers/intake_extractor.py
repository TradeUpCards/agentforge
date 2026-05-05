"""Intake extractor worker node (W2 Stage-3b).

Wraps attach_and_extract_async() — the Stage-2 async entry point.
Input: state.query + any uploaded document context encoded in
       state.tool_calls_accumulated (see SupervisorState docstring).
Output: state patch with new Citation objects (source_type='extracted_document')
        appended to state.citations, plus structural metadata in
        state.worker_results and state.tool_calls_accumulated.

W2_ARCHITECTURE.md §3.4 (worker isolation), §3.3 (span: worker.intake_extractor).

No-PHI discipline (§8.3):
  - Langfuse span tags: worker_name, n_citations_added, latency_ms.
    NEVER chunk text, field values, or query content.
  - Citation objects carry source_block_id (structural), not raw text.
  - Exceptions are scrubbed before logging.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from agent._phi_scrubber import mask_observability_patterns
from agent.document_schemas import IntakeForm, LabReport
from agent.extractors import attach_and_extract_async
from agent.graph.state import SupervisorState
from agent.schemas import Citation

logger = logging.getLogger(__name__)

_WORKER_NAME = "intake_extractor"


def intake_extractor_node(state: SupervisorState) -> dict[str, Any]:
    """LangGraph worker node: document extraction.

    Reads doc context from the first tool_calls_accumulated entry that
    carries 'doc_ref_id', 'patient_id', 'doc_type', and optionally 'pdf_path'.
    Calls attach_and_extract_async() and converts the result to Citation objects.

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

    with _langfuse_span(lf, _langfuse_available) as _span:
        try:
            doc_ctx = _find_doc_context(state)
            if doc_ctx is None:
                logger.info(
                    "intake_extractor_node: no doc context in state — skipping extraction"
                )
            else:
                result = _run_extraction(doc_ctx)
                new_citations = _build_citations(result, doc_ctx)
                n_citations_added = len(new_citations)
        except Exception as exc:
            scrubbed = mask_observability_patterns(str(exc))
            logger.error(
                "intake_extractor_node extraction failed: %s",
                type(exc).__name__,
            )
            logger.debug("intake_extractor_node scrubbed error: %s", scrubbed)

        latency_ms = int((time.monotonic() - t0) * 1000)
        if _langfuse_available and lf is not None:
            try:
                lf.update_current_span(
                    metadata={
                        "worker_name": _WORKER_NAME,
                        "n_citations_added": n_citations_added,
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
        "citations_added": n_citations_added,
        "latency_ms": latency_ms,
    })

    existing_results = list(state.get("worker_results", []))
    existing_results.append({
        "worker_name": _WORKER_NAME,
        "citations_added": n_citations_added,
        "record_count": n_citations_added,
        "latency_ms": latency_ms,
    })

    return {
        "citations": existing_citations,
        "tool_calls_accumulated": existing_tool_calls,
        "worker_results": existing_results,
    }


def _find_doc_context(state: SupervisorState) -> dict[str, Any] | None:
    """Extract document context from state.tool_calls_accumulated.

    The first entry carrying 'doc_ref_id' and 'patient_id' is used.
    Returns None if no such entry is found.
    """
    for entry in state.get("tool_calls_accumulated", []):
        if "doc_ref_id" in entry and "patient_id" in entry:
            return entry
    return None


def _run_extraction(doc_ctx: dict[str, Any]) -> LabReport | IntakeForm:
    """Call attach_and_extract_async() synchronously.

    attach_and_extract_async() is the preferred async entry point (avoids
    the asyncio.run() footgun documented in Stage-2). We use a thread-pool
    workaround when an event loop is already running (e.g. pytest-asyncio).
    """
    patient_id = int(doc_ctx["patient_id"])
    doc_ref_id = str(doc_ctx["doc_ref_id"])
    doc_type = str(doc_ctx.get("doc_type", "lab_pdf"))
    pdf_path_str = doc_ctx.get("pdf_path")
    pdf_path = Path(pdf_path_str) if pdf_path_str else None

    coro = attach_and_extract_async(
        patient_id=patient_id,
        doc_ref_id=doc_ref_id,
        doc_type=doc_type,
        pdf_path=pdf_path,
    )

    # Python 3.12: asyncio.get_event_loop() raises DeprecationWarning when
    # no running loop. Use get_running_loop() to detect running loop safely.
    try:
        asyncio.get_running_loop()
        _loop_running = True
    except RuntimeError:
        _loop_running = False

    if _loop_running:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result(timeout=120)
    else:
        return asyncio.run(coro)


def _build_citations(
    result: LabReport | IntakeForm,
    doc_ctx: dict[str, Any],
) -> list[Citation]:
    """Convert an extraction result into Citation objects.

    Each Citation has source_type='extracted_document' and a block_id
    pointing back to the Docling bbox (the two-stage grounding contract).
    No raw field values are included in the citation's quote_or_value beyond
    the structural block reference — see §8.3.
    """
    citations: list[Citation] = []
    doc_ref_id = str(doc_ctx.get("doc_ref_id", "unknown"))

    if isinstance(result, LabReport):
        for lab_result in result.results:
            # LabResult carries source_block_id (Docling block ref) but not
            # the BBox itself — the BBox lives in the DoclingBlock, which is
            # not threaded to the worker at this stage. Citation bbox=None is
            # correct here; the click-to-source overlay resolves bbox from
            # the block_id at render time (via the stored DoclingDoc).
            citations.append(
                Citation(
                    source_type="extracted_document",
                    source_id=doc_ref_id,
                    page_or_section=None,  # resolved at render time via block_id
                    field_or_chunk_id=lab_result.source_block_id,
                    quote_or_value=lab_result.source_block_id,  # block ref, not field value
                    bbox=None,
                )
            )
    elif isinstance(result, IntakeForm):
        # IntakeForm cites via source_citations: field_name → source_block_id
        for field_name, block_id in (result.source_citations or {}).items():
            citations.append(
                Citation(
                    source_type="extracted_document",
                    source_id=doc_ref_id,
                    page_or_section=None,
                    field_or_chunk_id=block_id,
                    quote_or_value=f"{field_name}:{block_id}",
                    bbox=None,
                )
            )

    return citations


class _langfuse_span:
    """Context manager that opens a Langfuse span named 'worker.intake_extractor'
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
                self._span = self._lf.start_observation(name="worker.intake_extractor")
            except Exception:
                self._span = None
        return self

    def __exit__(self, *_: object) -> None:
        if self._span is not None:
            try:
                self._span.end()
            except Exception:
                pass
