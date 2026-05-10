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
    extraction_payload: dict[str, Any] | None = None
    extraction_error: str | None = None

    with _langfuse_span(lf, _langfuse_available) as _span:
        try:
            doc_ctx = _find_doc_context(state)
            if doc_ctx is None:
                logger.info(
                    "intake_extractor_node: no doc context in state — skipping extraction"
                )
            else:
                payload = _run_extraction_full(doc_ctx)
                result = payload["result"]
                docling_blocks = payload["docling_blocks"]
                new_citations = _build_citations(result, doc_ctx, docling_blocks)
                n_citations_added = len(new_citations)

                # Surface the FULL payload + computed response metadata so
                # the /attach_and_extract endpoint can build its HTTP
                # response from final_state['extraction_payload'] without
                # re-doing model_dump or the per-doc-type counting logic.
                # PHI: held in state memory; NOT logged, NOT span-tagged.
                n_blocks, n_pages, conf_avg = _compute_response_meta(result)
                extraction_payload = {
                    "result": result.model_dump(mode="json"),
                    "docling_blocks": payload.get("docling_blocks") or [],
                    "field_verdicts": payload.get("field_verdicts") or [],
                    "original_values": payload.get("original_values") or {},
                    "n_blocks": n_blocks,
                    "n_pages": n_pages,
                    "extraction_confidence_avg": conf_avg,
                }
        except Exception as exc:
            scrubbed = mask_observability_patterns(str(exc))
            extraction_error = type(exc).__name__
            logger.error(
                "intake_extractor_node extraction failed: %s",
                extraction_error,
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
                        "extraction_error": extraction_error,
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

    state_patch: dict[str, Any] = {
        "citations": existing_citations,
        "tool_calls_accumulated": existing_tool_calls,
        "worker_results": existing_results,
    }
    # Only set extraction_payload when we actually ran an extraction.
    # On chat-only flows where doc_ctx is None we leave the field
    # alone so the supervisor's terminal logic stays correct.
    if extraction_payload is not None:
        state_patch["extraction_payload"] = extraction_payload

    return state_patch


def _compute_response_meta(result: Any) -> tuple[int, int, float]:
    """Compute (n_blocks, n_pages, extraction_confidence_avg) for the response.

    Mirrors the per-doc-type logic the /attach_and_extract endpoint used
    to do inline; lifted here so the endpoint just reads from
    extraction_payload after graph.ainvoke().

    No PHI: only counts and averaged confidence values.
    """
    if isinstance(result, LabReport):
        n_blocks = len(result.results)
        n_pages = result.extraction_metadata.page_count
        confidences = [r.confidence for r in result.results]
    elif isinstance(result, IntakeForm):
        n_blocks = (
            len(result.current_medications)
            + len(result.allergies)
            + len(result.family_history)
        )
        n_pages = result.extraction_metadata.page_count
        confidences = []
    else:
        n_blocks = len(getattr(result, "blocks", []))
        n_pages = getattr(result, "page_count", 1)
        confidences = []

    confidence_avg = sum(confidences) / len(confidences) if confidences else 0.0
    return n_blocks, n_pages, confidence_avg


def _find_doc_context(state: SupervisorState) -> dict[str, Any] | None:
    """Extract document context from state.tool_calls_accumulated.

    The first entry carrying 'doc_ref_id' and 'patient_id' is used.
    Returns None if no such entry is found.
    """
    for entry in state.get("tool_calls_accumulated", []):
        if "doc_ref_id" in entry and "patient_id" in entry:
            return entry
    return None


def _run_extraction_full(doc_ctx: dict[str, Any]) -> dict[str, Any]:
    """Call attach_and_extract_with_metadata_async() synchronously and
    return the FULL payload dict.

    Replaces the older _run_extraction() (which returned only
    (result, docling_blocks)).  The graph-routed extraction path needs
    the full payload — result + docling_blocks + field_verdicts +
    original_values — so the /extract_via_graph endpoint can build its
    HTTP response from the final-state extraction_payload without
    re-doing model_dump or running Stage-1 again.

    attach_and_extract_with_metadata_async() is preferred over the legacy
    attach_and_extract_async() here because the legacy variant returns
    ONLY the validated LabReport/IntakeForm and discards the Docling
    block inventory; we'd then have to re-run Stage-1 just to get page
    numbers and bboxes.

    Thread-pool workaround for the event-loop-already-running case
    (pytest-asyncio + FastAPI request handler both).  When the caller
    is already inside a running asyncio loop, asyncio.run() refuses;
    we spawn a worker thread that runs a fresh event loop just for the
    extraction coroutine.
    """
    patient_id = int(doc_ctx["patient_id"])
    doc_ref_id = str(doc_ctx["doc_ref_id"])
    doc_type = str(doc_ctx.get("doc_type", "lab_pdf"))
    pdf_path_str = doc_ctx.get("pdf_path")
    pdf_path = Path(pdf_path_str) if pdf_path_str else None
    triggered_by = doc_ctx.get("triggered_by")
    parent_extraction_id = doc_ctx.get("parent_extraction_id")

    # Pass triggered_by + parent_extraction_id through ONLY when set —
    # the metadata function accepts them as optional kwargs and routes
    # them through Stage-2 retry-ladder telemetry.
    extraction_kwargs: dict[str, Any] = {
        "patient_id": patient_id,
        "doc_ref_id": doc_ref_id,
        "doc_type": doc_type,
        "pdf_path": pdf_path,
    }
    if triggered_by is not None:
        extraction_kwargs["triggered_by"] = triggered_by
    if parent_extraction_id is not None:
        extraction_kwargs["parent_extraction_id"] = parent_extraction_id

    coro = attach_and_extract_with_metadata_async(**extraction_kwargs)

    # Python 3.12: asyncio.get_event_loop() raises DeprecationWarning when
    # no running loop. Use get_running_loop() to detect a running loop safely.
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

    # Defensive: ensure payload is a dict with the expected keys.
    if not isinstance(payload, dict):
        return {
            "result": None,
            "docling_blocks": [],
            "field_verdicts": [],
            "original_values": {},
        }
    return payload


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
