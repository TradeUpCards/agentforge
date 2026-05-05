"""Document extraction package — Stage 1 skeleton.

Public surface: attach_and_extract()

This module implements Stage 1 of the two-stage extraction pipeline
(W2_ARCHITECTURE.md §2.2):

    Stage 1: Docling layout engine → DoclingDoc (real bounding boxes)
    Stage 2: Haiku schema extraction → LabReport / IntakeForm  [NOT YET]

Stage 2 (Haiku / Anthropic call) is deliberately absent from this PR.
The extractor returns a DoclingDoc with real block coordinates so the
smoke test can verify bboxes are non-zero, distinct, and within page bounds.

Architectural hard constraints enforced here:
- No LLM call at any point in this module (two-stage discipline).
- No VLM bounding-box generation (coordinates from Docling only).
- patient_id must be in sentinel range 999100-999199 (validated by schemas).
- No raw document text in logs (block count and page count only).

W2_ARCHITECTURE.md §2.5: the tool signature is
    attach_and_extract(patient_id, doc_ref_id, doc_type)
This module receives those arguments and returns a DoclingDoc.
"""

from __future__ import annotations

import importlib.metadata
import logging
from pathlib import Path
from typing import Literal

from agent.document_schemas import BBox, DoclingBlock, DoclingDoc

logger = logging.getLogger(__name__)

DocType = Literal["lab_pdf", "intake_form"]

_SENTINEL_MIN = 999_100
_SENTINEL_MAX = 999_199


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


def attach_and_extract(
    patient_id: int,
    doc_ref_id: str,
    doc_type: DocType,
    pdf_path: Path | None = None,
) -> DoclingDoc:
    """Stage 1: run Docling layout extraction and return a DoclingDoc.

    This is the public entry point for the document ingestion pipeline.
    It performs layout extraction only — no LLM schema extraction (Stage 2)
    is performed in this PR.

    Args:
        patient_id:  Must be in the W2 sentinel range 999100-999199.
        doc_ref_id:  FHIR DocumentReference ID (content-stable across re-runs).
        doc_type:    One of "lab_pdf" or "intake_form".
        pdf_path:    Path to the local PDF file. When None, the function
                     expects the PDF to be retrievable via doc_ref_id from
                     OpenEMR storage (full integration — not yet wired in
                     Stage 1 skeleton).

    Returns:
        DoclingDoc with all layout blocks and their real bounding boxes.

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
            "OpenEMR storage integration is not yet wired in Stage 1. "
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
