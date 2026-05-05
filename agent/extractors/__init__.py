"""Document extraction package — Stage 1 + Stage 2.

Public surface:
  attach_and_extract()       — sync entry point (always callable from sync or async code)
  attach_and_extract_async() — async entry point for FastAPI / async callers

This module implements the two-stage extraction pipeline
(W2_ARCHITECTURE.md §2.2):

    Stage 1: Docling layout engine → DoclingDoc (real bounding boxes)
    Stage 2: Haiku schema extraction → LabReport / IntakeForm

Stage 2 is invoked when doc_type is "lab_pdf" or "intake_form". For any
other doc_type (including "debug") the function returns a DoclingDoc,
preserving Stage-1-only behavior for smoke tests and forward compat.

Sync/async design note:
  The Stage-1 path is fully synchronous (Docling is CPU-bound, no I/O).
  The Stage-2 path calls the Anthropic API (async I/O). The public
  attach_and_extract() is synchronous and uses asyncio.run() for Stage-2.
  Async callers (FastAPI routes, LangGraph nodes) should prefer the
  attach_and_extract_async() coroutine to avoid nested event loop issues.

Architectural hard constraints enforced here:
- Two-stage discipline: Docling owns bboxes; Haiku owns field values.
  LLM never invents coordinates.
- patient_id must be in sentinel range 999100-999199 (validated by schemas).
- No raw document text in logs (block count and page count only).
- Extracted field values never in logs, traces, or exception messages.
- Langfuse span: haiku_schema_extraction — tokens/cost/latency only.
  NEVER patient_id value, doc text, or extracted values.

W2_ARCHITECTURE.md §2.5: the tool signature is
    attach_and_extract(patient_id, doc_ref_id, doc_type)
Return type widens in Stage 2: LabReport | IntakeForm | DoclingDoc.
"""

from __future__ import annotations

import asyncio
import importlib.metadata
import json
import logging
import time
from pathlib import Path
from typing import Any, Literal, Union

from agent._phi_scrubber import mask_observability_patterns
from agent.document_schemas import BBox, DoclingBlock, DoclingDoc, IntakeForm, LabReport

logger = logging.getLogger(__name__)

DocType = Literal["lab_pdf", "intake_form"]

_SENTINEL_MIN = 999_100
_SENTINEL_MAX = 999_199

# Model name used for Haiku extraction (matches W2_ARCHITECTURE.md model split)
_HAIKU_MODEL = "claude-haiku-4-5"


def _get_docling_version() -> str:
    """Return the installed Docling version string, or 'unavailable'."""
    try:
        return importlib.metadata.version("docling")
    except importlib.metadata.PackageNotFoundError:
        return "unavailable"


def _run_docling_layout(pdf_path: Path) -> list[dict[str, object]]:
    """Run Docling layout extraction on a PDF file.

    Returns a list of raw block dicts from Docling's DocumentConverter.
    Each dict includes: text, page number, and bounding box coordinates.

    This function is the ONLY place in the codebase that invokes Docling.
    No LLM is called here; coordinates are real layout-engine output.

    Raises RuntimeError if Docling is not installed.
    """
    try:
        from docling.document_converter import DocumentConverter
    except ImportError as exc:
        raise RuntimeError(
            "Docling is not installed. Add 'docling>=2.0.0' to requirements.txt "
            "and rebuild the Docker image. See W2_ARCHITECTURE.md §2.3 for the "
            "Mistral OCR API fallback if install is blocked."
        ) from exc

    converter = DocumentConverter()
    result = converter.convert(str(pdf_path))
    doc = result.document

    raw_blocks: list[dict[str, object]] = []
    block_index = 0

    # Iterate through all content items in the Docling document.
    # Docling's export_to_dict() gives a stable representation we can
    # walk without depending on internal object structure.
    for item, level in doc.iterate_items():
        # Only process items that have text content and bounding box info.
        # Docling items expose .text and .prov (provenance list with bbox).
        text = getattr(item, "text", None)
        prov_list = getattr(item, "prov", [])

        if not text or not prov_list:
            continue

        # Use the first provenance entry (most items have exactly one).
        prov = prov_list[0]
        bbox_obj = getattr(prov, "bbox", None)
        page_no = getattr(prov, "page_no", 1)

        if bbox_obj is None:
            continue

        # Docling bbox coords: l (left), t (top), r (right), b (bottom)
        # in the page's coordinate system. Normalize to our BBox shape
        # (x0=left, y0=bottom, x1=right, y1=top in PDF points).
        left = float(getattr(bbox_obj, "l", 0.0))
        top = float(getattr(bbox_obj, "t", 0.0))
        right = float(getattr(bbox_obj, "r", 0.0))
        bottom = float(getattr(bbox_obj, "b", 0.0))

        # Ensure x0 < x1 and y0 < y1 (degenerate boxes are skipped).
        x0, x1 = (left, right) if left < right else (right, left)
        y0, y1 = (min(top, bottom), max(top, bottom))

        if x1 <= x0 or y1 <= y0:
            continue

        # Determine block_type from Docling item label.
        item_label = str(getattr(item, "label", "text")).lower()
        if "table" in item_label:
            block_type = "table"
        elif "figure" in item_label or "picture" in item_label:
            block_type = "figure"
        elif "header" in item_label or "title" in item_label:
            block_type = "header"
        elif "footer" in item_label:
            block_type = "footer"
        elif "list" in item_label:
            block_type = "list_item"
        else:
            block_type = "text"

        raw_blocks.append({
            "block_id": f"block_{block_index}",
            "text": str(text),
            "block_type": block_type,
            "page": int(page_no),
            "x0": x0,
            "y0": y0,
            "x1": x1,
            "y1": y1,
        })
        block_index += 1

    return raw_blocks


def _raw_blocks_to_docling_doc(
    raw_blocks: list[dict[str, object]],
    doc_ref_id: str,
    page_count: int,
    docling_version: str,
) -> DoclingDoc:
    """Convert raw Docling block dicts into a DoclingDoc."""
    blocks: list[DoclingBlock] = []
    for rb in raw_blocks:
        page = int(rb["page"])  # type: ignore[arg-type]
        bbox = BBox(
            page=page,
            x0=float(rb["x0"]),  # type: ignore[arg-type]
            y0=float(rb["y0"]),  # type: ignore[arg-type]
            x1=float(rb["x1"]),  # type: ignore[arg-type]
            y1=float(rb["y1"]),  # type: ignore[arg-type]
        )
        blocks.append(
            DoclingBlock(
                block_id=str(rb["block_id"]),
                text=str(rb["text"]),
                block_type=rb["block_type"],  # type: ignore[arg-type]
                page=page,
                bbox=bbox,
            )
        )

    return DoclingDoc(
        document_reference_id=doc_ref_id,
        page_count=page_count,
        blocks=blocks,
        docling_version=docling_version,
    )


def _get_page_count(pdf_path: Path) -> int:
    """Return the page count of a PDF using pypdfium2 (bundled with Docling).

    Falls back to 1 if the library is unavailable.
    """
    try:
        import pypdfium2 as pdfium  # type: ignore[import-untyped]
        doc = pdfium.PdfDocument(str(pdf_path))
        count = len(doc)
        doc.close()
        return max(1, count)
    except Exception:
        return 1


def _run_stage1_layout(
    patient_id: int,
    doc_ref_id: str,
    doc_type: str,
    pdf_path: Path | None,
) -> DoclingDoc:
    """Execute Stage 1: sentinel check, PDF validation, Docling layout.

    This is the synchronous core shared by both the sync and async public
    entry points. Returns a DoclingDoc.

    Raises:
        ValueError:   patient_id outside sentinel range.
        RuntimeError: Docling not installed or PDF not found.
    """
    if not (_SENTINEL_MIN <= patient_id <= _SENTINEL_MAX):
        raise ValueError(
            f"patient_id is outside W2 sentinel range "
            f"[{_SENTINEL_MIN}, {_SENTINEL_MAX}]."
        )

    if pdf_path is None:
        raise RuntimeError(
            "OpenEMR storage integration is not yet wired in Stage 1/2. "
            "Pass pdf_path explicitly for now."
        )

    if not pdf_path.exists():
        raise RuntimeError(f"PDF not found at {pdf_path}")

    docling_version = _get_docling_version()
    page_count = _get_page_count(pdf_path)

    # No PHI in logs: log page count and doc_type only, not patient_id or text.
    logger.info(
        "attach_and_extract: layout extraction started",
        extra={
            "doc_ref_id": doc_ref_id,
            "doc_type": doc_type,
            "page_count": page_count,
            "docling_version": docling_version,
        },
    )

    raw_blocks = _run_docling_layout(pdf_path)

    docling_doc = _raw_blocks_to_docling_doc(
        raw_blocks=raw_blocks,
        doc_ref_id=doc_ref_id,
        page_count=page_count,
        docling_version=docling_version,
    )

    # No PHI in logs: log block count only.
    logger.info(
        "attach_and_extract: layout extraction complete",
        extra={
            "doc_ref_id": doc_ref_id,
            "n_blocks": len(docling_doc.blocks),
            "page_count": docling_doc.page_count,
        },
    )

    return docling_doc


async def _run_haiku_extraction(
    doc: DoclingDoc,
    doc_type: str,
    patient_id: int,
    doc_ref_id: str,
) -> LabReport | IntakeForm:
    """Call Haiku for schema extraction and parse the result.

    Langfuse instrumentation:
      - Span name: haiku_schema_extraction
      - Tags: token/cost/latency, model_name, doc_type, n_blocks
      - patient_id_in_sentinel_range boolean (never the raw value)
      - NEVER: raw doc text, extracted field values, patient_id integer

    Raises:
        RuntimeError:                Haiku returned non-JSON or unexpected shape.
        ExtractionLowGrounding:      >30% of fields fail grounding check.
        ValueError:                  Invalid block_id or confidence OOB.
    """
    from agent.extractors.haiku_extraction import (
        build_intake_form_messages,
        build_intake_form_system,
        build_lab_report_messages,
        build_lab_report_system,
        parse_intake_form,
        parse_lab_report,
    )
    from agent.llm_client import build_llm_client
    from langfuse import get_client

    lf = get_client()

    if doc_type == "lab_pdf":
        system_blocks = build_lab_report_system()
        messages = build_lab_report_messages(doc)
    else:
        system_blocks = build_intake_form_system()
        messages = build_intake_form_messages(doc)

    # system_blocks are the schema-spec cacheable prefix — bitwise-stable across
    # calls. Do NOT scrub: (a) they contain no PHI (generic schema instructions
    # only), (b) any string substitution breaks prompt-cache hit semantics
    # (Anthropic keys the cache on exact byte content of the system block).
    # The user message (variable suffix carrying doc block text) is sent to
    # Anthropic under the existing BAA path — same posture as Stage-1 Docling
    # output — and is intentionally not logged anywhere in this function.

    llm_client = build_llm_client()

    # TODO(stage-3): replace with lf.start_span("haiku_schema_extraction") when
    # supervisor/worker graph is wired (see W2_ARCHITECTURE.md §3.3 trace shape).
    # Langfuse span metadata: structural only, no PHI.
    span_meta: dict[str, Any] = {
        "doc_type": doc_type,
        "n_blocks": len(doc.blocks),
        "page_count": doc.page_count,
        "model": _HAIKU_MODEL,
        "patient_id_in_sentinel_range": _SENTINEL_MIN <= patient_id <= _SENTINEL_MAX,
    }

    latency_ms = 0
    t0 = time.monotonic()
    try:
        response = await llm_client.create(
            model=_HAIKU_MODEL,
            system=system_blocks,  # type: ignore[arg-type]
            messages=messages,
            max_tokens=2048,
            temperature=0.0,
        )
    except Exception as sdk_exc:
        latency_ms = int((time.monotonic() - t0) * 1000)
        # Scrub exception message: SDK errors may echo request-body fragments.
        scrubbed_msg = mask_observability_patterns(str(sdk_exc))
        lf.update_current_trace(
            name="haiku_schema_extraction",
            metadata={**span_meta, "latency_ms": latency_ms, "error": "sdk_error"},
        )
        raise RuntimeError(
            f"Haiku SDK call failed for doc_type={doc_type}: {scrubbed_msg}"
        ) from None

    latency_ms = int((time.monotonic() - t0) * 1000)

    # Extract text from response
    response_text = "".join(
        getattr(b, "text", "") for b in response.content
        if getattr(b, "type", None) == "text"
    ).strip()

    # Strip markdown fences if present (Haiku sometimes wraps with ```)
    if response_text.startswith("```"):
        lines = response_text.splitlines()
        inner = lines[1:-1] if lines and lines[-1].strip() == "```" else lines[1:]
        response_text = "\n".join(inner).strip()

    # Emit token/cache usage to Langfuse in a single call — no PHI, structural only.
    # (Collapsed from two calls: the earlier try/finally draft emitted latency_ms
    # on the way out, then overwrote it here. Single call avoids the overwrite.)
    usage = response.usage
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cache_create = getattr(usage, "cache_creation_input_tokens", 0) or 0
    lf.update_current_trace(
        name="haiku_schema_extraction",
        metadata={
            **span_meta,
            "latency_ms": latency_ms,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cache_read_input_tokens": cache_read,
            "cache_creation_input_tokens": cache_create,
            "stop_reason": response.stop_reason,
        },
    )

    # Parse the JSON response into the appropriate schema.
    # Fix #1: json.JSONDecodeError carries the raw response text in its .doc
    # attribute. If Haiku paraphrased block content into malformed JSON, .doc
    # could contain PHI. We catch the error, log only structural info (line/col),
    # and raise from None to drop __cause__/__context__ and their .doc attribute.
    # ValueError / KeyError from the parser are also wrapped — their messages are
    # structured and value-free, but we scrub defensively before re-raising.
    try:
        if doc_type == "lab_pdf":
            return parse_lab_report(
                haiku_json_text=response_text,
                doc=doc,
                patient_id=patient_id,
                doc_ref_id=doc_ref_id,
                extraction_model=_HAIKU_MODEL,
            )
        else:
            return parse_intake_form(
                haiku_json_text=response_text,
                doc=doc,
                patient_id=patient_id,
                doc_ref_id=doc_ref_id,
                extraction_model=_HAIKU_MODEL,
            )
    except json.JSONDecodeError as exc:
        # exc.doc holds the raw response text — suppress it entirely.
        raise RuntimeError(
            f"Haiku response was not valid JSON for doc_type={doc_type} "
            f"(decode error at line {exc.lineno}, col {exc.colno}). "
            "Raw response suppressed for PHI compliance."
        ) from None
    except (ValueError, KeyError) as exc:
        # Parser raises are structured (no raw field values) but scrub defensively.
        scrubbed = mask_observability_patterns(str(exc))
        raise RuntimeError(
            f"Haiku output failed schema validation for doc_type={doc_type}: {scrubbed}"
        ) from None


def attach_and_extract(
    patient_id: int,
    doc_ref_id: str,
    doc_type: str,
    pdf_path: Path | None = None,
    *,
    stage1_only: bool = False,
) -> Union[LabReport, IntakeForm, DoclingDoc]:
    """Sync entry point: Stage 1 + optional Stage 2.

    For doc_type "lab_pdf" or "intake_form": runs Docling layout (Stage 1)
    then calls Haiku for schema extraction (Stage 2) via asyncio.run(),
    returning LabReport or IntakeForm respectively.

    For any other doc_type (including "debug"): returns DoclingDoc only,
    preserving Stage-1-only behavior for smoke tests.

    This sync wrapper exists so Stage-1 smoke tests (which are synchronous
    pytest tests) continue to work unchanged, and so that any code path
    that calls attach_and_extract synchronously (e.g. CLI scripts, tests)
    doesn't need to be changed. FastAPI route handlers and LangGraph nodes
    should prefer attach_and_extract_async() to avoid spawning a new event loop.

    Args:
        patient_id:   Must be in the W2 sentinel range 999100-999199.
        doc_ref_id:   FHIR DocumentReference ID (content-stable across re-runs).
        doc_type:     "lab_pdf", "intake_form", or any other string.
        pdf_path:     Path to the local PDF file. When None, expects OpenEMR
                      storage integration (not yet wired in Stage 1/2).
        stage1_only:  If True, always return DoclingDoc (skip Stage 2). Used
                      by smoke tests and layout-only callers who want the
                      Docling output without invoking an LLM. Keyword-only.
                      # TODO(stage-3): evaluate removal once smoke tests are
                      # replaced by integration tests under the supervisor graph.

    Returns:
        LabReport | IntakeForm | DoclingDoc depending on doc_type and
        stage1_only.

    Raises:
        ValueError:                 patient_id outside sentinel range.
        RuntimeError:               Docling not installed or PDF not found.
        ExtractionLowGrounding:     >30% of fields fail grounding check.
    """
    docling_doc = _run_stage1_layout(patient_id, doc_ref_id, doc_type, pdf_path)

    if not stage1_only and doc_type in ("lab_pdf", "intake_form"):
        logger.info(
            "attach_and_extract: starting Haiku schema extraction",
            extra={
                "doc_ref_id": doc_ref_id,
                "doc_type": doc_type,
                "n_blocks": len(docling_doc.blocks),
            },
        )
        result: LabReport | IntakeForm = asyncio.run(
            _run_haiku_extraction(
                doc=docling_doc,
                doc_type=doc_type,
                patient_id=patient_id,
                doc_ref_id=doc_ref_id,
            )
        )
        n_fields = (
            len(result.results)
            if isinstance(result, LabReport)
            else (
                len(result.current_medications)
                + len(result.allergies)
                + len(result.family_history)
            )
        )
        logger.info(
            "attach_and_extract: Haiku extraction complete",
            extra={
                "doc_ref_id": doc_ref_id,
                "doc_type": doc_type,
                "n_extracted_fields": n_fields,
            },
        )
        return result

    # Default: return DoclingDoc for smoke tests, debug, unknown types
    return docling_doc


async def attach_and_extract_async(
    patient_id: int,
    doc_ref_id: str,
    doc_type: str,
    pdf_path: Path | None = None,
    *,
    stage1_only: bool = False,
) -> Union[LabReport, IntakeForm, DoclingDoc]:
    """Async entry point: Stage 1 + optional Stage 2.

    Preferred over attach_and_extract() for async callers (FastAPI routes,
    LangGraph nodes) to avoid creating a new event loop via asyncio.run().

    Same behavior and args as attach_and_extract() — see its docstring.
    """
    docling_doc = _run_stage1_layout(patient_id, doc_ref_id, doc_type, pdf_path)

    if not stage1_only and doc_type in ("lab_pdf", "intake_form"):
        logger.info(
            "attach_and_extract_async: starting Haiku schema extraction",
            extra={
                "doc_ref_id": doc_ref_id,
                "doc_type": doc_type,
                "n_blocks": len(docling_doc.blocks),
            },
        )
        result = await _run_haiku_extraction(
            doc=docling_doc,
            doc_type=doc_type,
            patient_id=patient_id,
            doc_ref_id=doc_ref_id,
        )
        n_fields = (
            len(result.results)
            if isinstance(result, LabReport)
            else (
                len(result.current_medications)
                + len(result.allergies)
                + len(result.family_history)
            )
        )
        logger.info(
            "attach_and_extract_async: Haiku extraction complete",
            extra={
                "doc_ref_id": doc_ref_id,
                "doc_type": doc_type,
                "n_extracted_fields": n_fields,
            },
        )
        return result

    return docling_doc
