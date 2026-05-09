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
from agent.extractors import attach_and_extract_with_metadata_async
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
                result, docling_blocks = _run_extraction(doc_ctx)
                new_citations = _build_citations(result, doc_ctx, docling_blocks)
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


def _run_extraction(
    doc_ctx: dict[str, Any],
) -> tuple[LabReport | IntakeForm, list[dict[str, Any]]]:
    """Call attach_and_extract_with_metadata_async() synchronously.

    Returns the (extraction_result, docling_blocks) tuple needed by
    _build_citations() so the citations can carry the source block's page
    number (page_or_section) and an actual extracted-value string
    (quote_or_value), per PRD §5 minimum citation shape.

    docling_blocks is the list of {block_id, page (1-based), bbox, text_snippet,
    block_type} dicts returned by the metadata variant; used for block_id→page
    lookup in _build_citations.

    attach_and_extract_with_metadata_async() is preferred over the legacy
    attach_and_extract_async() here because the legacy variant returns ONLY
    the validated LabReport/IntakeForm and discards the Docling block
    inventory; we'd then have to re-run Stage-1 just to get page numbers.

    Thread-pool workaround for event-loop-already-running case (pytest-asyncio).
    """
    patient_id = int(doc_ctx["patient_id"])
    doc_ref_id = str(doc_ctx["doc_ref_id"])
    doc_type = str(doc_ctx.get("doc_type", "lab_pdf"))
    pdf_path_str = doc_ctx.get("pdf_path")
    pdf_path = Path(pdf_path_str) if pdf_path_str else None

    coro = attach_and_extract_with_metadata_async(
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
            payload = future.result(timeout=120)
    else:
        payload = asyncio.run(coro)

    # payload is dict[str, Any] from attach_and_extract_with_metadata_async
    # with keys: result, docling_blocks, field_verdicts, original_values.
    result = payload.get("result")
    docling_blocks = payload.get("docling_blocks") or []
    if not isinstance(docling_blocks, list):
        docling_blocks = []
    return result, docling_blocks


def _build_citations(
    result: LabReport | IntakeForm,
    doc_ctx: dict[str, Any],
    docling_blocks: list[dict[str, Any]] | None = None,
) -> list[Citation]:
    """Convert an extraction result into Citation objects.

    Each Citation has source_type='extracted_document' and a block_id
    pointing back to the Docling bbox (the two-stage grounding contract).

    PRD §5 minimum citation shape requires page_or_section (page number)
    and quote_or_value (the extracted text/value), both populated.  Prior
    to this change both were stubbed (`page_or_section=None`,
    `quote_or_value=block_id`).  We now:

    - Look up page_or_section via a block_id → page map built from the
      docling_blocks list passed in by the caller (no DoclingDoc thread
      refactor required — caller already has the metadata variant's
      block list).
    - Populate quote_or_value with a concise rendering of the actual
      extracted value: lab result reading for LabReport entries; the
      first item's primary attribute (with "+N more" suffix for lists)
      for IntakeForm entries.

    BBox stays None on the citation itself — frontend resolves bbox at
    click time via the OpenEMR /resolve_citation.php endpoint.  This
    keeps the agent-side payload light and the bbox value in a single
    canonical store (co_pilot_extracted_fields.bbox_json or
    co_pilot_extractions.docling_blocks_json).
    """
    citations: list[Citation] = []
    doc_ref_id = str(doc_ctx.get("doc_ref_id", "unknown"))

    # Build block_id → page map for page_or_section lookup.
    block_pages: dict[str, int] = {}
    for block in docling_blocks or []:
        if not isinstance(block, dict):
            continue
        bid = block.get("block_id")
        page = block.get("page")
        if isinstance(bid, str) and isinstance(page, int):
            block_pages[bid] = page

    def _page_str(block_id: str | None) -> str | None:
        if block_id is None:
            return None
        page = block_pages.get(block_id)
        return str(page) if page is not None else None

    if isinstance(result, LabReport):
        for lab_result in result.results:
            # quote_or_value: actual extracted reading.  Truncated to
            # ~80 chars to keep response compact; not PHI-sensitive
            # since the agent already returns this in the structured
            # extraction payload.
            quote = f"{lab_result.test_name}: {lab_result.value} {lab_result.unit}".strip()
            if len(quote) > 80:
                quote = quote[:77] + "..."
            citations.append(
                Citation(
                    source_type="extracted_document",
                    source_id=doc_ref_id,
                    page_or_section=_page_str(lab_result.source_block_id),
                    field_or_chunk_id=lab_result.source_block_id,
                    quote_or_value=quote,
                    bbox=None,  # client resolves via /resolve_citation.php
                )
            )
    elif isinstance(result, IntakeForm):
        # IntakeForm cites via source_citations: field_name → source_block_id.
        # We synthesise a meaningful quote_or_value from the structured
        # fields rather than echoing the block_id.
        for field_name, block_id in (result.source_citations or {}).items():
            quote = _intake_quote_for_field(result, field_name)
            citations.append(
                Citation(
                    source_type="extracted_document",
                    source_id=doc_ref_id,
                    page_or_section=_page_str(block_id),
                    field_or_chunk_id=block_id,
                    quote_or_value=quote,
                    bbox=None,
                )
            )

    return citations


def _intake_quote_for_field(form: IntakeForm, field_name: str) -> str:
    """Return a concise human-readable quote for an IntakeForm citation.

    Walks the structured fields to surface the actual extracted value.
    For list-valued fields, returns the first item's primary attribute
    plus a "+N more" suffix when the list has more entries.  Quote is
    capped at ~80 chars.
    """
    def _cap(s: str) -> str:
        s = s.strip()
        return s if len(s) <= 80 else s[:77] + "..."

    if field_name == "demographics":
        if form.demographics is not None and form.demographics.name:
            return _cap(form.demographics.name)
    if field_name == "chief_concern":
        if form.chief_concern:
            return _cap(form.chief_concern)
    if field_name == "current_medications":
        meds = form.current_medications or []
        if meds:
            head = meds[0].name
            extra = len(meds) - 1
            return _cap(head if extra == 0 else f"{head} (+{extra} more)")
    if field_name == "allergies":
        allergies = form.allergies or []
        if allergies:
            head = allergies[0].substance
            extra = len(allergies) - 1
            return _cap(head if extra == 0 else f"{head} (+{extra} more)")
    if field_name == "family_history":
        history = form.family_history or []
        if history:
            head = history[0].condition
            relation = history[0].relation or "?"
            extra = len(history) - 1
            base = f"{relation}: {head}"
            return _cap(base if extra == 0 else f"{base} (+{extra} more)")
    # Fallback: keep the field_name as the quote so the citation is
    # never empty.  Better than echoing the opaque block_id.
    return field_name


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
