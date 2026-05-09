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
import os
import time
from pathlib import Path
from typing import Any, Literal, Union

from agent._phi_scrubber import mask_observability_patterns
from agent.document_schemas import BBox, DoclingBlock, DoclingDoc, IntakeForm, LabReport
from agent.extractors.cost import compute_cost_usd
from agent.extractors.template_id import resolve_template_id

logger = logging.getLogger(__name__)

DocType = Literal["lab_pdf", "intake_form"]

_SENTINEL_MIN = 999_100
_SENTINEL_MAX = 999_199

# Model names (W2_ARCHITECTURE.md model split)
_HAIKU_MODEL = "claude-haiku-4-5"
_SONNET_MODEL = "claude-sonnet-4-6"

# Cost ceilings (PRD §6.4)
# Per-document: hard ceiling across all retry attempts for one document.
_PER_DOC_COST_CEILING_USD = 0.50
# Per-run: read lazily from env at runtime (not at import time) so eval suites
# can override via os.environ before the first call without patching globals.
_DEFAULT_PER_RUN_COST_CEILING_USD = 5.00


def _get_per_run_cost_ceiling() -> float:
    """Return per-run cost ceiling from env, defaulting to $5.00.

    Read lazily so eval suites may set MAX_EXTRACTION_COST_USD_PER_RUN in
    os.environ before the first call; a module-level snapshot would miss
    those overrides.
    """
    raw = os.environ.get("MAX_EXTRACTION_COST_USD_PER_RUN")
    if raw is not None:
        try:
            return float(raw)
        except ValueError:
            pass
    return _DEFAULT_PER_RUN_COST_CEILING_USD


# ---------------------------------------------------------------------------
# P2 exception: cost ceiling exceeded
# ---------------------------------------------------------------------------


class ExtractionCostCeilingExceeded(RuntimeError):
    """Raised when cumulative cost would exceed the per-document ceiling.

    Message format:
        cost_ceiling_exceeded: cumulative cost {cumulative_cost_usd:.5f}
        would exceed per-doc ceiling {ceiling:.2f} on attempt {attempt_n}

    PHI discipline: no patient_id integer, no raw extracted values.
    Only structural floats (cost estimates) and attempt ordinal appear.
    """


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

    Multi-page PDF note (bug fix):
    Docling's default pipeline (StandardPdfPipeline + DoclingParseDocumentBackend)
    rasterises each page via pypdfium2 before passing it to the layout model.
    On memory-constrained hosts (e.g. the dev laptop running Docker + uvicorn)
    the rasterisation of pages 2+ of a 3-page intake form throws std::bad_alloc
    inside the pypdfium2 C extension, causing Docling to silently skip those
    pages.  Page 1 succeeds; pages 2-N produce zero items.

    Fix: for PDFs use PyPdfiumDocumentBackend, which extracts text and table
    geometry directly from the PDF's embedded text layer (fast, zero
    rasterisation, zero ML inference for the text path).  This is identical to
    what the old PdfBackend.PYPDFIUM2 enum selected in earlier Docling versions.
    Images (PNG/JPG) are unaffected — they still flow through the default
    image pipeline.  Typed PDFs (all current fixtures) have a reliable text
    layer, so the quality is equivalent to the layout-model path while
    eliminating the memory spike.
    """
    try:
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
    except ImportError as exc:
        raise RuntimeError(
            "Docling is not installed. Add 'docling>=2.0.0' to requirements.txt "
            "and rebuild the Docker image. See W2_ARCHITECTURE.md §2.3 for the "
            "Mistral OCR API fallback if install is blocked."
        ) from exc

    # For PDFs: use PyPdfiumDocumentBackend (direct text extraction, no
    # layout-model rasterisation) to avoid std::bad_alloc on pages 2+ of
    # multi-page typed intakes.  do_ocr=False because typed PDFs have an
    # embedded text layer; do_table_structure=True preserves table markdown.
    # Images (PNG/JPG) continue to use the default image pipeline.
    pdf_pipeline_options = PdfPipelineOptions(
        do_ocr=False,
        do_table_structure=True,
    )

    # Stage-4a: explicitly enable IMAGE input alongside PDF so PNG/JPG intake
    # forms and lab scans produced by phone-camera uploads also flow through
    # the layout pipeline.  Docling's RapidOCR engine handles images natively
    # (already pulled as a transitive dep at install time).  W2_ARCHITECTURE.md
    # §2.1 doesn't restrict the doc type beyond "lab PDF / intake form" — the
    # MIME type is incidental.
    converter = DocumentConverter(
        allowed_formats=[InputFormat.PDF, InputFormat.IMAGE],
        format_options={
            InputFormat.PDF: PdfFormatOption(
                pipeline_options=pdf_pipeline_options,
                backend=PyPdfiumDocumentBackend,
            ),
        },
    )
    result = converter.convert(str(pdf_path))
    doc = result.document

    raw_blocks: list[dict[str, object]] = []
    block_index = 0

    # Iterate through all content items in the Docling document.
    # Docling's export_to_dict() gives a stable representation we can
    # walk without depending on internal object structure.
    for item, level in doc.iterate_items():
        # Determine label early — used for both block_type AND table-extraction
        # routing below.
        item_label = str(getattr(item, "label", "text")).lower()

        # Tables in Docling are TableItem objects.  Their content lives in
        # item.data (TableData with table_cells), NOT in a .text attribute.
        # Calling getattr(item, "text") on a TableItem returns "" / None,
        # so without explicit handling all table content (medications,
        # allergies, family history on intake forms; lab-result rows on
        # lab PDFs) is silently dropped.  Render to markdown and treat
        # the whole table as a single block — keeps row context together,
        # which is what Haiku/Sonnet needs to extract item-level rows.
        if "table" in item_label:
            try:
                text = item.export_to_markdown(doc=doc)
            except TypeError:
                # Older Docling signatures may not require `doc=`.
                try:
                    text = item.export_to_markdown()
                except Exception:
                    text = ""
            except Exception:
                text = ""
        else:
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

        # Determine block_type from Docling item label (item_label was
        # computed at the top of the loop body for table-extraction routing).
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


_BLOCK_TEXT_SNIPPET_MAX_CHARS = 240
"""Maximum text snippet length per block in the P3/P4-R2 response payload.

Bumped from 80 → 240 chars for P4 R2 (user-decided PHI posture).  The
response payload already carries fully-verified field values (same PHI
surface); longer snippets enable the "Assert from block" UI affordance,
which needs enough context for the clinician to identify the correct block
in the picker without opening a second tab.  Truncation is bytewise on
the rstripped text (no further normalisation — preserves original
punctuation and case).
"""


def _serialize_blocks_for_response(doc: DoclingDoc) -> list[dict[str, Any]]:
    """Serialize the full Docling block inventory for the P3/P4-R2 response payload.

    Produces the ``docling_blocks`` array described in the P3/P4 API contract.
    Every block in the DoclingDoc is included — both cited (referenced by
    field_verdicts) and uncited — so the UI can render all blocks and the
    backend can build a complete overlay even before the verdicts are applied.

    BBox format in the output matches the API contract:
        {"x": left, "y": bottom, "w": width, "h": height}
    in PDF user-space points, origin bottom-left.  The DoclingDoc stores
    blocks as (x0, y0, x1, y1) where x0/y0 are left/bottom.

    Text snippet truncation: rstrip whitespace first, then take the first
    _BLOCK_TEXT_SNIPPET_MAX_CHARS characters.  No further normalisation
    (preserves original case, punctuation, etc.).  PHI discipline: the
    original document text is already visible to the clinician, so
    _BLOCK_TEXT_SNIPPET_MAX_CHARS-char snippets are acceptable.

    block_type: copied directly from DoclingBlock.block_type (set by
    _run_docling_layout from the Docling item label).  The "Assert from
    block" picker UI filters by this value — "table", "text", "header",
    "footer", "figure", "list_item", or "unknown".

    Args:
        doc: The DoclingDoc produced by Stage 1.

    Returns:
        List of dicts, one per block, each with keys:
            block_id, page, bbox (x/y/w/h), text_snippet, block_type.
    """
    result: list[dict[str, Any]] = []
    for block in doc.blocks:
        bbox = block.bbox
        text_snippet = block.text.rstrip()[:_BLOCK_TEXT_SNIPPET_MAX_CHARS]
        result.append({
            "block_id": block.block_id,
            "page": bbox.page,
            "bbox": {
                "x": bbox.x0,
                "y": bbox.y0,
                "w": bbox.x1 - bbox.x0,
                "h": bbox.y1 - bbox.y0,
            },
            "text_snippet": text_snippet,
            "block_type": block.block_type,
        })
    return result


def build_original_values_map(
    result: "Union[LabReport, IntakeForm]",
    field_verdicts: "list[dict[str, Any]]",
) -> "dict[str, Any]":
    """Build a flat {field_path: original_value} map for verified fields only.

    The HITL review UI needs to render an editable field form without
    walking the nested Pydantic model_dump.  This helper produces a flat
    projection keyed on the same field_path strings emitted by the
    extraction verifier, containing only the fields that passed grounding.

    Stripped fields are intentionally omitted — they carry no trusted value
    (the verifier already discarded them) and surface separately through
    field_verdicts.stripped_fields[*].  The UI shows those as "Missed" rows.

    PHI discipline: this map is returned on the same response that already
    contains the full extraction model_dump — no new PHI surface.  The
    map must NOT be written to logs or Langfuse spans.

    Algorithm:
      1. Build a set of verified field_paths from field_verdicts (status == "verified").
      2. Walk the result model_dump (mode="python" for native Python types).
      3. For each verified field_path, resolve the value via a simple dotted
         path + bracket-index walk.  Unresolvable paths are silently skipped.

    Args:
        result: LabReport or IntakeForm from the successful extraction.
        field_verdicts: The list of field_verdict dicts from
            attach_and_extract_with_metadata_async (keys: field_path,
            source_block_id, status, verifier_reason).

    Returns:
        dict mapping field_path → value for every verified field.
        Example: {"results[0].test_name": "HDL", "results[0].value": 42.0, ...}
    """
    # Build a set of verified field_paths for O(1) membership test.
    verified_paths: set[str] = {
        fv["field_path"]
        for fv in field_verdicts
        if fv.get("status") == "verified"
    }

    if not verified_paths:
        return {}

    # model_dump with mode="python" gives us native Python types (float, date, etc.)
    # which are JSON-serialisable when mode="json" but more useful for resolution.
    # We use mode="json" here so that dates serialise to ISO strings and the output
    # is directly embeddable in the JSONResponse without a second conversion pass.
    dumped = result.model_dump(mode="json")

    original_values: dict[str, Any] = {}
    for field_path in verified_paths:
        value = _resolve_field_path(dumped, field_path)
        if value is not _UNRESOLVED:
            original_values[field_path] = value

    return original_values


# Sentinel for unresolved path (avoids confusing None with "field exists, value is null")
class _UnresolvedSentinel:
    """Internal sentinel for paths that cannot be resolved in the model_dump."""
    __slots__ = ()


_UNRESOLVED = _UnresolvedSentinel()


def _resolve_field_path(
    data: Any,
    field_path: str,
) -> Any:
    """Walk a dotted-bracket field path into a nested dict/list structure.

    Supports paths like:
      "results[0].test_name"
      "demographics.name"
      "current_medications[1].dose"
      "chief_concern"

    Returns _UNRESOLVED if any segment of the path cannot be resolved
    (missing key, out-of-range index, or unexpected type).

    PHI discipline: this function never logs path names or resolved values.
    """
    import re
    current: Any = data
    # Tokenise: split on "." and then split each token on "[" / "]".
    # E.g. "results[0].test_name" → ["results", "0", "test_name"]
    # E.g. "demographics.name"    → ["demographics", "name"]
    segments = re.split(r"[.\[\]]+", field_path)
    for seg in segments:
        if not seg:
            continue
        if isinstance(current, dict):
            if seg not in current:
                return _UNRESOLVED
            current = current[seg]
        elif isinstance(current, list):
            try:
                idx = int(seg)
            except ValueError:
                return _UNRESOLVED
            if idx < 0 or idx >= len(current):
                return _UNRESOLVED
            current = current[idx]
        else:
            # Primitive — cannot descend further.
            return _UNRESOLVED
    return current


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
        raise RuntimeError("PDF not found at the specified path")

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
    filename: str | None = None,
    *,
    model: str = _HAIKU_MODEL,
    prompt_variant: str = "default",
    attempt_n: int = 1,
    triggered_by: str = "initial",
    parent_extraction_id: int | None = None,
) -> LabReport | IntakeForm:
    """Call the LLM for schema extraction and parse the result.

    Langfuse instrumentation:
      - Span name: "haiku_schema_extraction" for Haiku, "sonnet_schema_extraction"
        for Sonnet (chosen at the start of the function based on `model`).
      - Tags: token/cost/latency, model_name, doc_type, n_blocks, template_id,
        attempt_n, prompt_variant, triggered_by, parent_extraction_id.
      - patient_id_in_sentinel_range boolean (never the raw value).
      - NEVER: raw doc text, extracted field values, patient_id integer, raw filename.

    Args:
        doc:                  DoclingDoc from Stage 1.
        doc_type:             "lab_pdf" or "intake_form".
        patient_id:           Must be in sentinel range.
        doc_ref_id:           FHIR DocumentReference ID.
        filename:             Original upload filename — used ONLY to resolve
                              template_id.  Never emitted to spans or logs directly.
        model:                Anthropic model string.  Default: _HAIKU_MODEL.
                              Pass _SONNET_MODEL for attempt 3 escalation.
        prompt_variant:       "default" or "verbatim_only".  Controls which user
                              message builder is invoked.  Default: "default".
        attempt_n:            Retry ladder ordinal (1, 2, 3).  Emitted to span.
        triggered_by:         "initial", "auto_retry", or "manual_retry".
        parent_extraction_id: DB ID of the prior attempt row (None for attempt 1).

    Raises:
        RuntimeError:            LLM returned non-JSON or unexpected shape.
        ExtractionLowGrounding:  >30% of fields fail grounding check.
        ValueError:              Invalid block_id or confidence OOB.
    """
    from agent.extractors.haiku_extraction import (
        ExtractionStats,
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

    # Determine span name based on model — named spans let Langfuse group by model tier.
    span_name = (
        "sonnet_schema_extraction" if model == _SONNET_MODEL else "haiku_schema_extraction"
    )

    # Choose prompt builder based on prompt_variant.
    use_verbatim = (prompt_variant == "verbatim_only")

    if doc_type == "lab_pdf":
        system_blocks = build_lab_report_system()
        messages = build_lab_report_messages(doc, verbatim=use_verbatim)
    else:
        system_blocks = build_intake_form_system()
        messages = build_intake_form_messages(doc, verbatim=use_verbatim)

    # system_blocks are the schema-spec cacheable prefix — bitwise-stable across
    # calls. Do NOT scrub: (a) they contain no PHI (generic schema instructions
    # only), (b) any string substitution breaks prompt-cache hit semantics
    # (Anthropic keys the cache on exact byte content of the system block).
    # The user message (variable suffix carrying doc block text) is sent to
    # Anthropic under the existing BAA path — same posture as Stage-1 Docling
    # output — and is intentionally not logged anywhere in this function.

    llm_client = build_llm_client()

    # Resolve template_id from filename BEFORE building span_meta.
    # Only the resolved label (from a closed vocabulary) enters the span —
    # the raw filename is never emitted.
    template_id = resolve_template_id(filename)

    # Langfuse span metadata: structural only, no PHI.
    # All P1 keys plus the new P2 attempt-chain fields are included here.
    span_meta: dict[str, Any] = {
        "doc_type": doc_type,
        "n_blocks": len(doc.blocks),
        "page_count": doc.page_count,
        "model": model,
        "patient_id_in_sentinel_range": _SENTINEL_MIN <= patient_id <= _SENTINEL_MAX,
        "template_id": template_id,
        "prompt_variant": prompt_variant,
        "attempt_n": attempt_n,
        "triggered_by": triggered_by,
        "parent_extraction_id": parent_extraction_id,  # None for first attempt
    }

    latency_ms = 0
    t0 = time.monotonic()
    # Open a named span for the schema extraction attempt per §3.3 trace shape.
    # Uses lf.start_observation() with explicit .end() calls — compatible with
    # the async try/except structure.  Metadata is structural only — no PHI,
    # no raw doc text, no extracted values.
    span_obj = None
    try:
        span_obj = lf.start_observation(name=span_name, metadata=span_meta)
    except Exception:
        pass

    _span_ended = False

    try:
        response = await llm_client.create(
            model=model,
            system=system_blocks,  # type: ignore[arg-type]
            messages=messages,
            max_tokens=2048,
            temperature=0.0,
        )
    except Exception as sdk_exc:
        latency_ms = int((time.monotonic() - t0) * 1000)
        # Scrub exception message: SDK errors may echo request-body fragments.
        scrubbed_msg = mask_observability_patterns(str(sdk_exc))
        if span_obj is not None and not _span_ended:
            try:
                span_obj.update(
                    metadata={**span_meta, "latency_ms": latency_ms, "error": "sdk_error"},
                )
                span_obj.end()
                _span_ended = True
            except Exception:
                pass
        raise RuntimeError(
            f"SDK call failed for doc_type={doc_type} model={model}: {scrubbed_msg}"
        ) from None

    latency_ms = int((time.monotonic() - t0) * 1000)

    # Extract text from response
    response_text = "".join(
        getattr(b, "text", "") for b in response.content
        if getattr(b, "type", None) == "text"
    ).strip()

    # Strip markdown fences if present (Haiku/Sonnet sometimes wraps with ```)
    if response_text.startswith("```"):
        lines = response_text.splitlines()
        inner = lines[1:-1] if lines and lines[-1].strip() == "```" else lines[1:]
        response_text = "\n".join(inner).strip()

    # Capture usage here — used for both cost computation and span update.
    usage = response.usage
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cache_create = getattr(usage, "cache_creation_input_tokens", 0) or 0

    # Compute cost before the parse block so it's available on all paths.
    cost_usd = compute_cost_usd(model, usage.input_tokens, usage.output_tokens)

    # Parse the JSON response into the appropriate schema.
    # Fix #1: json.JSONDecodeError carries the raw response text in its .doc
    # attribute. If the LLM paraphrased block content into malformed JSON, .doc
    # could contain PHI. We catch the error, log only structural info (line/col),
    # and raise from None to drop __cause__/__context__ and their .doc attribute.
    # ValueError / KeyError from the parser are also wrapped — their messages are
    # structured and value-free, but we scrub defensively before re-raising.
    try:
        if doc_type == "lab_pdf":
            parsed_result, extraction_stats = parse_lab_report(
                haiku_json_text=response_text,
                doc=doc,
                patient_id=patient_id,
                doc_ref_id=doc_ref_id,
                extraction_model=model,
            )
        else:
            parsed_result, extraction_stats = parse_intake_form(
                haiku_json_text=response_text,
                doc=doc,
                patient_id=patient_id,
                doc_ref_id=doc_ref_id,
                extraction_model=model,
            )
    except json.JSONDecodeError as exc:
        # exc.doc holds the raw response text — suppress it entirely.
        if span_obj is not None and not _span_ended:
            try:
                span_obj.update(
                    metadata={**span_meta, "error": "json_decode_error"}
                )
                span_obj.end()
                _span_ended = True
            except Exception:
                pass
        raise RuntimeError(
            f"LLM response was not valid JSON for doc_type={doc_type} model={model} "
            f"(decode error at line {exc.lineno}, col {exc.colno}). "
            "Raw response suppressed for PHI compliance."
        ) from None
    except (ValueError, KeyError) as exc:
        # Parser raises are structured (no raw field values) but scrub defensively.
        scrubbed = mask_observability_patterns(str(exc))
        if span_obj is not None and not _span_ended:
            try:
                span_obj.update(
                    metadata={**span_meta, "error": "grounding_or_parse_failure"}
                )
                span_obj.end()
                _span_ended = True
            except Exception:
                pass
        raise RuntimeError(
            f"LLM output failed schema validation for doc_type={doc_type} "
            f"model={model}: {scrubbed}"
        ) from None

    # Success path: emit all P1 attributes plus P2 attempt-chain fields and close span.
    if span_obj is not None and not _span_ended:
        try:
            span_obj.update(
                metadata={
                    **span_meta,
                    "latency_ms": latency_ms,
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "cache_read_input_tokens": cache_read,
                    "cache_creation_input_tokens": cache_create,
                    "stop_reason": response.stop_reason,
                    "cost_usd": cost_usd,
                    "total_fields": extraction_stats.total_fields,
                    "stripped_fields": extraction_stats.stripped_fields,
                    "strip_rate": round(extraction_stats.strip_rate, 4),
                    "verifier_reason_counts": extraction_stats.verifier_reason_counts,
                },
            )
            span_obj.end()
            _span_ended = True
        except Exception:
            pass

    # P3: return the stats alongside the parsed result so callers can build
    # field_verdicts in the HTTP response payload without re-parsing.  Internal
    # callers (_run_extraction_with_retry, P2 tests) unpack the tuple; the
    # public attach_and_extract / attach_and_extract_async wrappers preserve
    # their pre-P3 return type by discarding the stats.
    return parsed_result, extraction_stats


async def _run_extraction_with_retry(
    *,
    doc: DoclingDoc,
    doc_type: str,
    patient_id: int,
    doc_ref_id: str,
    filename: str | None = None,
    parent_extraction_id: int | None = None,
    triggered_by: str = "initial",
) -> tuple[Union[LabReport, IntakeForm], "ExtractionStats"]:
    """Run the auto-retry escalation ladder per PRD §6.

    Ladder:
      attempt 1: Haiku,  prompt_variant=default        (always runs)
      attempt 2: Haiku,  prompt_variant=verbatim_only  (only if attempt 1 raised ExtractionLowGrounding)
      attempt 3: Sonnet, prompt_variant=verbatim_only  (only if attempt 2 raised ExtractionLowGrounding)

    On ExtractionLowGrounding from all three attempts, re-raises the LAST
    ExtractionLowGrounding without wrapping it (so existing call sites that
    catch ExtractionLowGrounding continue to work unchanged).

    Cost discipline:
      Before each attempt, cumulative_cost_usd is checked against
      _PER_DOC_COST_CEILING_USD ($0.50).  If exceeded, raises
      ExtractionCostCeilingExceeded instead of proceeding.

      Cost estimation uses compute_cost_usd(model, max_tokens, max_tokens)
      where max_tokens=2048 (the per-call budget) as an upper-bound
      pre-attempt approximation.  Actual cost is tracked inside
      _run_haiku_extraction via the Langfuse span; the wrapper accumulates
      the conservative upper-bound so it can gate without coupling to the
      internal token counters.  This avoids re-plumbing actual token usage
      out of _run_haiku_extraction and keeps the wrapper self-contained.

    PHI discipline:
      - ExtractionCostCeilingExceeded message contains only structural floats
        and attempt ordinal — no patient_id, no extracted values.
      - On ExtractionLowGrounding, the exception object from the failed attempt
        is NOT passed into the next attempt call — each attempt is independent
        and receives only the original DoclingDoc.  The caught exception is
        discarded (stored temporarily only to re-raise on final failure).

    Args:
        doc:                  DoclingDoc from Stage 1.
        doc_type:             "lab_pdf" or "intake_form".
        patient_id:           Sentinel-range patient ID.
        doc_ref_id:           FHIR DocumentReference ID.
        filename:             Upload filename for template_id resolution only.
        parent_extraction_id: DB ID of the prior extraction chain head (None
                              for fresh extractions; set for manual reprocess).
        triggered_by:         "initial", "auto_retry", or "manual_retry".

    Returns:
        LabReport | IntakeForm from the first successful attempt.

    Raises:
        ExtractionLowGrounding:        All three attempts refused (>30% strip rate).
        ExtractionCostCeilingExceeded: Cumulative estimated cost would exceed
                                       _PER_DOC_COST_CEILING_USD before an attempt.
    """
    from agent.extractors.haiku_extraction import ExtractionLowGrounding

    # Retry ladder: ordered triples of (model, prompt_variant).
    # Attempt numbering is 1-indexed to match the DB schema (PRD §4.1).
    _LADDER: list[tuple[str, str]] = [
        (_HAIKU_MODEL,  "default"),
        (_HAIKU_MODEL,  "verbatim_only"),
        (_SONNET_MODEL, "verbatim_only"),
    ]

    cumulative_cost_usd: float = 0.0
    # Conservative budget per call: max_tokens=2048 for both input and output.
    # This is an intentional overestimate — we never have 2048 output tokens AND
    # 2048 input tokens simultaneously, but using max_tokens for both sides gives
    # a safe upper bound without access to actual token counts before the call.
    _BUDGET_TOKENS = 2048

    last_grounding_exc: ExtractionLowGrounding | None = None

    for attempt_idx, (attempt_model, attempt_variant) in enumerate(_LADDER):
        attempt_n = attempt_idx + 1

        # Pre-attempt cost check: would this attempt push us over the ceiling?
        attempt_cost_estimate = compute_cost_usd(attempt_model, _BUDGET_TOKENS, _BUDGET_TOKENS)
        # compute_cost_usd returns None only for unknown models; both ladder models are known.
        if attempt_cost_estimate is None:
            attempt_cost_estimate = 0.0
        if cumulative_cost_usd + attempt_cost_estimate > _PER_DOC_COST_CEILING_USD:
            raise ExtractionCostCeilingExceeded(
                f"cost_ceiling_exceeded: cumulative cost {cumulative_cost_usd:.5f} "
                f"would exceed per-doc ceiling {_PER_DOC_COST_CEILING_USD:.2f} "
                f"on attempt {attempt_n}"
            )

        try:
            result, extraction_stats = await _run_haiku_extraction(
                doc=doc,
                doc_type=doc_type,
                patient_id=patient_id,
                doc_ref_id=doc_ref_id,
                filename=filename,
                model=attempt_model,
                prompt_variant=attempt_variant,
                attempt_n=attempt_n,
                triggered_by=triggered_by if attempt_n == 1 else "auto_retry",
                parent_extraction_id=parent_extraction_id,
            )
            return result, extraction_stats

        except ExtractionLowGrounding as exc:
            # PHI note: exc contains only structural counts and the reason code
            # string from _check_grounding_rate — no field values, no block text.
            # We discard the failed attempt's content entirely; the exception
            # object itself is stored only to re-raise on final failure.
            last_grounding_exc = exc
            # Accumulate the upper-bound estimate before moving to the next attempt.
            cumulative_cost_usd += attempt_cost_estimate
            # Continue to next attempt; each attempt is independent.

    # All three attempts failed — re-raise the last ExtractionLowGrounding
    # without wrapping, so callers that catch ExtractionLowGrounding are unaffected.
    assert last_grounding_exc is not None  # guaranteed by loop logic
    raise last_grounding_exc


def attach_and_extract(
    patient_id: int,
    doc_ref_id: str,
    doc_type: str,
    pdf_path: Path | None = None,
    *,
    stage1_only: bool = False,
    filename: str | None = None,
    triggered_by: Literal["initial", "auto_retry", "manual_retry"] = "initial",
    parent_extraction_id: int | None = None,
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
        patient_id:            Must be in the W2 sentinel range 999100-999199.
        doc_ref_id:            FHIR DocumentReference ID (content-stable across re-runs).
        doc_type:              "lab_pdf", "intake_form", or any other string.
        pdf_path:              Path to the local PDF file. When None, expects OpenEMR
                               storage integration (not yet wired in Stage 1/2).
        stage1_only:           If True, always return DoclingDoc (skip Stage 2). Used
                               by smoke tests and layout-only callers who want the
                               Docling output without invoking an LLM. Keyword-only.
                               # TODO(stage-3): evaluate removal once smoke tests are
                               # replaced by integration tests under the supervisor graph.
        filename:              Original upload filename. Used ONLY to resolve template_id
                               for the Langfuse span — never logged or traced directly.
                               Keyword-only.
        triggered_by:          Why this extraction was triggered. "initial" for the
                               first extraction of a document; "auto_retry" for a retry
                               initiated by the retry ladder; "manual_retry" for one
                               triggered by clinician action in the UI. Keyword-only.
                               Default: "initial".
        parent_extraction_id:  DB row ID of the prior extraction in this chain (None
                               for the first extraction of a document). Keyword-only.
                               Default: None.

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
            "attach_and_extract: starting extraction (auto-retry ladder)",
            extra={
                "doc_ref_id": doc_ref_id,
                "doc_type": doc_type,
                "n_blocks": len(docling_doc.blocks),
            },
        )
        # _run_extraction_with_retry returns (result, stats); discard stats at
        # this public boundary to preserve the pre-P3 return-type contract.
        # Callers needing metadata should use attach_and_extract_with_metadata_async().
        result, _stats = asyncio.run(
            _run_extraction_with_retry(
                doc=docling_doc,
                doc_type=doc_type,
                patient_id=patient_id,
                doc_ref_id=doc_ref_id,
                filename=filename,
                triggered_by=triggered_by,
                parent_extraction_id=parent_extraction_id,
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
            "attach_and_extract: extraction complete",
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
    filename: str | None = None,
    triggered_by: Literal["initial", "auto_retry", "manual_retry"] = "initial",
    parent_extraction_id: int | None = None,
) -> Union[LabReport, IntakeForm, DoclingDoc]:
    """Async entry point: Stage 1 + optional Stage 2.

    Preferred over attach_and_extract() for async callers (FastAPI routes,
    LangGraph nodes) to avoid creating a new event loop via asyncio.run().

    Same behavior and args as attach_and_extract() — see its docstring.
    The filename keyword arg is forwarded to _run_haiku_extraction for
    template_id resolution; never emitted to spans or logs directly.

    Args (P3 additions):
        triggered_by:          Why this extraction was triggered. Forwarded to
                               _run_extraction_with_retry and emitted in the
                               haiku_schema_extraction Langfuse span.
                               Default: "initial".
        parent_extraction_id:  DB row ID of the prior extraction in this chain.
                               Forwarded to _run_extraction_with_retry. None
                               for the first extraction of a document.
                               Default: None.
    """
    docling_doc = _run_stage1_layout(patient_id, doc_ref_id, doc_type, pdf_path)

    if not stage1_only and doc_type in ("lab_pdf", "intake_form"):
        logger.info(
            "attach_and_extract_async: starting extraction (auto-retry ladder)",
            extra={
                "doc_ref_id": doc_ref_id,
                "doc_type": doc_type,
                "n_blocks": len(docling_doc.blocks),
            },
        )
        # _run_extraction_with_retry returns (result, stats); we discard stats
        # at this public boundary to preserve the pre-P3 return-type contract
        # for existing callers (LangGraph workers, etc.).  Callers that need
        # the metadata for HTTP response construction should use
        # attach_and_extract_with_metadata_async() below.
        result, _stats = await _run_extraction_with_retry(
            doc=docling_doc,
            doc_type=doc_type,
            patient_id=patient_id,
            doc_ref_id=doc_ref_id,
            filename=filename,
            triggered_by=triggered_by,
            parent_extraction_id=parent_extraction_id,
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
            "attach_and_extract_async: extraction complete",
            extra={
                "doc_ref_id": doc_ref_id,
                "doc_type": doc_type,
                "n_extracted_fields": n_fields,
            },
        )
        return result

    return docling_doc


async def attach_and_extract_with_metadata_async(
    patient_id: int,
    doc_ref_id: str,
    doc_type: str,
    pdf_path: Path | None = None,
    *,
    stage1_only: bool = False,
    filename: str | None = None,
    triggered_by: Literal["initial", "auto_retry", "manual_retry"] = "initial",
    parent_extraction_id: int | None = None,
) -> dict[str, Any]:
    """P3 variant of attach_and_extract_async that returns a richer payload.

    The HTTP /attach_and_extract endpoint needs the full Docling block inventory
    (for the "Possibly Missed" review-modal pane) and the per-field verifier
    verdicts (for co_pilot_extracted_fields persistence).  Both pieces of data
    exist inside the Stage-1 + Stage-2 pipeline but are not surfaced by the
    pre-P3 return type, which is just the validated LabReport / IntakeForm /
    DoclingDoc.

    Returns a dict with these keys (always present):
        "result":          LabReport | IntakeForm | DoclingDoc — same as the
                           pre-P3 return value.  Callers serialize it the same
                           way they always did.
        "docling_blocks":  list of {block_id, page (1-based), bbox: {x,y,w,h},
                           text_snippet (≤240 chars), block_type} dicts.
                           Includes EVERY Docling block (cited and uncited) so
                           the UI can render the "Possibly Missed" pane and
                           the "Assert from block" picker.
        "field_verdicts":  list of {field_path, source_block_id, status,
                           verifier_reason, bbox?} dicts — one per field the
                           parser processed.  "verified" entries have no bbox
                           key; "stripped" entries carry a bbox dict
                           {x,y,w,h} (or null when source_block_id is null
                           or no matching block exists) so the UI can
                           highlight the offending block without a client-
                           side join against docling_blocks.
                           Empty list when no Stage-2 ran.
        "original_values": flat {field_path: value} map for verified fields
                           only (P4 R2 addition). Keyed on the same
                           field_path strings as field_verdicts; value is
                           the JSON-serialisable original LLM-verified value.
                           Stripped fields are NOT present in this map —
                           they surface through field_verdicts.stripped only.

    The pre-P3 attach_and_extract_async() is preserved unchanged for callers
    (LangGraph workers, P2 retry-ladder tests) that don't need the metadata.

    Args mirror attach_and_extract_async exactly — see its docstring.
    """
    docling_doc = _run_stage1_layout(patient_id, doc_ref_id, doc_type, pdf_path)
    docling_blocks = _serialize_blocks_for_response(docling_doc)

    # Stage-1-only path: no Stage-2 verdicts to surface.
    if stage1_only or doc_type not in ("lab_pdf", "intake_form"):
        return {
            "result": docling_doc,
            "docling_blocks": docling_blocks,
            "field_verdicts": [],
            "original_values": {},
        }

    logger.info(
        "attach_and_extract_with_metadata_async: starting extraction (auto-retry ladder)",
        extra={
            "doc_ref_id": doc_ref_id,
            "doc_type": doc_type,
            "n_blocks": len(docling_doc.blocks),
        },
    )
    result, stats = await _run_extraction_with_retry(
        doc=docling_doc,
        doc_type=doc_type,
        patient_id=patient_id,
        doc_ref_id=doc_ref_id,
        filename=filename,
        triggered_by=triggered_by,
        parent_extraction_id=parent_extraction_id,
    )

    # Convert ExtractionStats.field_verdicts (list of tuples) into the wire
    # shape the HTTP response promises.  PHI-safe: field_path values are
    # static literals (parser-side guarantee, pinned by
    # test_field_verdicts_field_paths_are_static_literals_no_phi).
    #
    # P4 R2 — per-stripped-field bbox:
    #   Build a block_id → bbox dict ONCE for O(N) total cost across all
    #   stripped fields, rather than scanning docling_blocks per verdict.
    #   Only stripped fields get a bbox key (verified fields don't need it —
    #   the UI can locate them via the extraction itself).
    block_bbox_index: dict[str, dict[str, float]] = {
        b.block_id: {
            "x": b.bbox.x0,
            "y": b.bbox.y0,
            "w": b.bbox.x1 - b.bbox.x0,
            "h": b.bbox.y1 - b.bbox.y0,
        }
        for b in docling_doc.blocks
    }

    field_verdicts: list[dict[str, Any]] = []
    for fv in stats.field_verdicts:
        fp, sid, reason = fv[0], fv[1], fv[2]
        is_stripped = reason is not None
        entry: dict[str, Any] = {
            "field_path":      fp,
            "source_block_id": sid,
            "status":          "stripped" if is_stripped else "verified",
            "verifier_reason": reason,
        }
        if is_stripped:
            # Attach bbox directly so the UI does not need a client-side join.
            # null when source_block_id is absent or no matching block exists.
            entry["bbox"] = block_bbox_index.get(sid) if sid else None
        field_verdicts.append(entry)

    # P4 R2 — flat original_values map: verified field_path → value.
    # Assembly-only: reads from the already-validated result model_dump.
    # PHI discipline: must not be written to logs or spans.
    original_values: dict[str, Any] = build_original_values_map(result, field_verdicts)

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
        "attach_and_extract_with_metadata_async: extraction complete",
        extra={
            "doc_ref_id": doc_ref_id,
            "doc_type": doc_type,
            "n_extracted_fields": n_fields,
            "n_verdicts":         len(field_verdicts),
        },
    )

    return {
        "result": result,
        "docling_blocks": docling_blocks,
        "field_verdicts": field_verdicts,
        "original_values": original_values,
    }
