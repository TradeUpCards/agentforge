"""Docling layout smoke test — W2 Stage 1.

Verifies that bbox values produced by the Docling layout engine are REAL:
  - Non-zero width and height per block
  - Distinct bboxes across blocks (no all-same-coords hallucination)
  - All coordinates within plausible page bounds

Intended to run inside the agent Docker container (or locally with Docling
installed). If Docling is not installed, the test is skipped with an
informative message — it does NOT fail, because the CI gate may run in an
environment where Docling downloads are disabled. The Docker smoke run is
the authoritative check.

NO assertions on extracted text values — that would be PHI-adjacent for
real documents. We assert structure (types, coordinate shapes, sentinel-id)
only, per W2_ARCHITECTURE.md §8.3.

Run:
    pytest agent/tests/smoke/docling_layout_test.py -v
"""

from __future__ import annotations

import sys

import pytest
from pathlib import Path

_FIXTURE_PDF = (
    Path(__file__).parent.parent / "fixtures" / "synthetic_lab_999100.pdf"
)

_SENTINEL_PATIENT_ID = 999_100
_DOC_REF_ID = "DocumentReference/synthetic-lab-999100-smoke"
_DOC_TYPE = "lab_pdf"

# Maximum plausible page dimension in PDF points (US Letter = 612x792).
# We use a generous upper bound (3000 pts ≈ 42 inches) to accommodate
# large-format documents without hardcoding letter-size assumptions.
_MAX_PAGE_DIM = 3000.0


def _docling_available() -> bool:
    """Return True if Docling can be imported successfully."""
    try:
        import docling  # noqa: F401
        return True
    except ImportError:
        return False


@pytest.mark.skipif(
    not _FIXTURE_PDF.exists(),
    reason=f"Fixture PDF not found at {_FIXTURE_PDF}. Run _generate_lab_pdf.py first.",
)
@pytest.mark.skipif(
    not _docling_available(),
    reason="Docling not installed. Run inside agent Docker container or install docling>=2.0.0.",
)
def test_docling_produces_nonzero_distinct_bboxes() -> None:
    """Layout extraction returns blocks with real, distinct bounding boxes.

    Assertion strategy (no PHI, structure only):
    1. At least 5 blocks extracted (document has multiple sections).
    2. Every block has x1 > x0 and y1 > y0 (non-zero area).
    3. All coordinates within [0, _MAX_PAGE_DIM].
    4. At least 3 distinct bbox tuples (blocks are not all at same coords).
    5. Pages are within [1, page_count].
    6. block_ids are unique strings.
    """
    from agent.extractors import attach_and_extract

    doc = attach_and_extract(
        patient_id=_SENTINEL_PATIENT_ID,
        doc_ref_id=_DOC_REF_ID,
        doc_type=_DOC_TYPE,
        pdf_path=_FIXTURE_PDF,
    )

    # Basic document structure
    assert doc.document_reference_id == _DOC_REF_ID
    assert doc.page_count >= 1
    assert len(doc.blocks) >= 5, (
        f"Expected at least 5 blocks, got {len(doc.blocks)}. "
        "Docling may not have extracted any text from the fixture PDF."
    )

    # Per-block bbox sanity
    for block in doc.blocks:
        bbox = block.bbox
        # Non-zero dimensions
        assert bbox.x1 > bbox.x0, (
            f"Block {block.block_id}: x1={bbox.x1} must be > x0={bbox.x0}"
        )
        assert bbox.y1 > bbox.y0, (
            f"Block {block.block_id}: y1={bbox.y1} must be > y0={bbox.y0}"
        )
        # Within plausible page bounds
        assert 0.0 <= bbox.x0 < _MAX_PAGE_DIM, f"Block {block.block_id}: x0 out of bounds"
        assert 0.0 <= bbox.y0 < _MAX_PAGE_DIM, f"Block {block.block_id}: y0 out of bounds"
        assert bbox.x1 <= _MAX_PAGE_DIM, f"Block {block.block_id}: x1 out of bounds"
        assert bbox.y1 <= _MAX_PAGE_DIM, f"Block {block.block_id}: y1 out of bounds"
        # Page number within declared page_count
        assert 1 <= block.page <= doc.page_count, (
            f"Block {block.block_id}: page={block.page} outside [1, {doc.page_count}]"
        )
        # block_id is a non-empty string
        assert isinstance(block.block_id, str) and block.block_id, (
            f"block_id must be a non-empty string, got {block.block_id!r}"
        )

    # Distinct bboxes — confirm we are not returning the same coordinates
    # for every block (which would indicate a layout engine failure or stub)
    bbox_tuples = {
        (b.bbox.page, b.bbox.x0, b.bbox.y0, b.bbox.x1, b.bbox.y1)
        for b in doc.blocks
    }
    assert len(bbox_tuples) >= 3, (
        f"Expected at least 3 distinct bboxes, got {len(bbox_tuples)}. "
        "Blocks may be returning identical coordinates."
    )

    # Unique block_ids
    block_ids = [b.block_id for b in doc.blocks]
    assert len(block_ids) == len(set(block_ids)), "block_ids must be unique"

    # Log a bbox sample for the return block (non-PHI: numbers only).
    # Uses print() so pytest -s shows it without polluting structured output.
    sample = doc.blocks[:3]
    print("\nSmoke test bbox sample (non-PHI):")
    for b in sample:
        print(
            f"  {b.block_id}: page={b.bbox.page} "
            f"({b.bbox.x0:.1f}, {b.bbox.y0:.1f}, {b.bbox.x1:.1f}, {b.bbox.y1:.1f})"
        )


@pytest.mark.skipif(
    not _FIXTURE_PDF.exists(),
    reason=f"Fixture PDF not found at {_FIXTURE_PDF}.",
)
@pytest.mark.skipif(
    not _docling_available(),
    reason="Docling not installed.",
)
@pytest.mark.xfail(
    sys.platform == "win32",
    reason=(
        "Docling std::bad_alloc on repeated in-process ML model loads (Windows). "
        "Docker (Linux) is the authoritative smoke environment."
    ),
    strict=False,
)
def test_docling_multipage_tracking() -> None:
    """Verify that blocks from page 2 are correctly labeled page=2.

    The fixture PDF has 2 pages. If all blocks report page=1, the page
    tracking in _run_docling_layout() is broken.

    On Windows, Docling's pipeline can exhaust memory reloading ML models
    in the same process (repeated calls after test_docling_produces_*).
    The decorator-level xfail is the correct guard — an in-function
    try/except is unreliable because Docling swallows std::bad_alloc
    internally and may return partial results without re-raising.
    """
    from agent.extractors import attach_and_extract

    doc = attach_and_extract(
        patient_id=_SENTINEL_PATIENT_ID,
        doc_ref_id=_DOC_REF_ID,
        doc_type=_DOC_TYPE,
        pdf_path=_FIXTURE_PDF,
    )

    assert doc.page_count >= 2, (
        f"Expected fixture PDF to have >=2 pages, got {doc.page_count}"
    )

    pages_seen = {b.page for b in doc.blocks}
    assert 2 in pages_seen, (
        f"No blocks from page 2 found. Pages seen: {sorted(pages_seen)}. "
        "Multi-page tracking may be broken."
    )


@pytest.mark.skipif(
    not _FIXTURE_PDF.exists(),
    reason=f"Fixture PDF not found at {_FIXTURE_PDF}.",
)
@pytest.mark.skipif(
    not _docling_available(),
    reason="Docling not installed.",
)
def test_sentinel_patient_id_enforced() -> None:
    """attach_and_extract rejects patient_ids outside the sentinel range.

    This test does NOT call Docling — the sentinel check fires before any
    PDF processing. Safe to run multiple times in the same process.
    """
    from agent.extractors import attach_and_extract

    with pytest.raises(ValueError, match="sentinel range"):
        attach_and_extract(
            patient_id=1,  # real-looking ID — should be rejected
            doc_ref_id=_DOC_REF_ID,
            doc_type=_DOC_TYPE,
            pdf_path=_FIXTURE_PDF,
        )

    with pytest.raises(ValueError, match="sentinel range"):
        attach_and_extract(
            patient_id=999200,  # outside W2 block — should be rejected
            doc_ref_id=_DOC_REF_ID,
            doc_type=_DOC_TYPE,
            pdf_path=_FIXTURE_PDF,
        )
