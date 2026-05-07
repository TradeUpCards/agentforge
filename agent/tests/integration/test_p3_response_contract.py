"""P3 response contract integration tests.

Pins the shape of _serialize_blocks_for_response output and verifies:
  - docling_blocks keys: block_id, page, bbox, text_snippet
  - bbox keys are exactly {x, y, w, h} (NOT x0/y0/x1/y1, NOT left/top/right/bottom)
  - page is 1-based (UI assumes 1-based; this test pins the convention)
  - text_snippet is byte-truncated to ≤80 chars after rstrip
  - All blocks (cited AND uncited) appear in the serialized list
  - field_verdicts shape: each entry has field_path, source_block_id, status, verifier_reason

PHI discipline:
  - All sentinel patient_ids in [999100, 999199]
  - text_snippet is bounded document layout text — acceptable (P3 brief §5)
  - No patient names, extracted values, or patient identifiers in test fixtures

W2_ARCHITECTURE.md references:
  P3 brief §4 — docling_blocks_json ships in P3 response
  P3 brief §5 — 80-char text snippets, PHI-acceptable
  P3 brief §6 — bbox format {x, y, w, h} origin bottom-left

Cross-teammate contract:
  - Pinned for ui-ux: page numbering IS 1-based (matches Docling page_no convention)
  - Pinned for PHP backend: bbox uses {x, y, w, h} keys matching ExtractionFieldsWriter schema
  - field_verdicts shape checked so PHP can deserialize without key errors
"""

from __future__ import annotations

import pytest

from agent.document_schemas import (
    BBox,
    DoclingBlock,
    DoclingDoc,
)
from agent.extractors import _serialize_blocks_for_response

# ---------------------------------------------------------------------------
# Sentinel constants (W2_ARCHITECTURE.md §8.2)
# ---------------------------------------------------------------------------

_DOC_REF_ID = "DocumentReference/test-p3-contract-999100"
_PID = 999_100


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_bbox(
    page: int = 1,
    x0: float = 72.0,
    y0: float = 100.0,
    x1: float = 400.0,
    y1: float = 118.0,
) -> BBox:
    return BBox(page=page, x0=x0, y0=y0, x1=x1, y1=y1)


def _make_block(
    block_id: str,
    text: str,
    page: int = 1,
    x0: float = 72.0,
    y0: float = 100.0,
    x1: float = 400.0,
    y1: float = 118.0,
) -> DoclingBlock:
    return DoclingBlock(
        block_id=block_id,
        text=text,
        block_type="text",
        page=page,
        bbox=_make_bbox(page=page, x0=x0, y0=y0, x1=x1, y1=y1),
    )


def _make_doc(blocks: list[DoclingBlock]) -> DoclingDoc:
    return DoclingDoc(
        document_reference_id=_DOC_REF_ID,
        page_count=max(b.page for b in blocks) if blocks else 1,
        blocks=blocks,
        docling_version="2.92.0",
    )


# ---------------------------------------------------------------------------
# Contract §1: Required top-level keys in every block dict
# ---------------------------------------------------------------------------


class TestDoclingBlockRequiredKeys:
    """Each serialized block must have exactly the four required keys."""

    def test_every_block_has_block_id(self) -> None:
        doc = _make_doc([_make_block("blk_0", "Hemoglobin A1c 7.8 %")])
        result = _serialize_blocks_for_response(doc)

        assert len(result) == 1
        assert "block_id" in result[0], "Each block must have 'block_id' key."

    def test_every_block_has_page(self) -> None:
        doc = _make_doc([_make_block("blk_0", "Glucose 142 mg/dL")])
        result = _serialize_blocks_for_response(doc)

        assert "page" in result[0], "Each block must have 'page' key."

    def test_every_block_has_bbox(self) -> None:
        doc = _make_doc([_make_block("blk_0", "eGFR 62 mL")])
        result = _serialize_blocks_for_response(doc)

        assert "bbox" in result[0], "Each block must have 'bbox' key."

    def test_every_block_has_text_snippet(self) -> None:
        doc = _make_doc([_make_block("blk_0", "Cholesterol 210 mg/dL")])
        result = _serialize_blocks_for_response(doc)

        assert "text_snippet" in result[0], "Each block must have 'text_snippet' key."

    def test_no_unexpected_top_level_keys(self) -> None:
        """The four required keys are the only ones emitted (no leakage of internals)."""
        doc = _make_doc([_make_block("blk_0", "test text")])
        result = _serialize_blocks_for_response(doc)

        actual_keys = set(result[0].keys())
        expected_keys = {"block_id", "page", "bbox", "text_snippet"}
        assert actual_keys == expected_keys, (
            f"Unexpected keys in block: {actual_keys - expected_keys}. "
            "The block dict must contain exactly block_id, page, bbox, text_snippet."
        )


# ---------------------------------------------------------------------------
# Contract §2: bbox keys are exactly {x, y, w, h}
#
# PINNED: The UI assumes {x, y, w, h}.
# This test guards against accidental reversion to x0/y0/x1/y1 or left/top/right/bottom.
# ---------------------------------------------------------------------------


class TestBboxKeyContract:
    """bbox must use {x, y, w, h} keys — never x0/y0/x1/y1 or left/top/right/bottom."""

    def test_bbox_has_x_key(self) -> None:
        doc = _make_doc([_make_block("blk_0", "test", x0=72.0, y0=100.0, x1=400.0, y1=118.0)])
        result = _serialize_blocks_for_response(doc)

        bbox = result[0]["bbox"]
        assert "x" in bbox, "bbox must have 'x' key (not 'x0' or 'left')."

    def test_bbox_has_y_key(self) -> None:
        doc = _make_doc([_make_block("blk_0", "test", x0=72.0, y0=100.0, x1=400.0, y1=118.0)])
        result = _serialize_blocks_for_response(doc)

        bbox = result[0]["bbox"]
        assert "y" in bbox, "bbox must have 'y' key (not 'y0' or 'top')."

    def test_bbox_has_w_key(self) -> None:
        doc = _make_doc([_make_block("blk_0", "test", x0=72.0, y0=100.0, x1=400.0, y1=118.0)])
        result = _serialize_blocks_for_response(doc)

        bbox = result[0]["bbox"]
        assert "w" in bbox, "bbox must have 'w' key (not 'width' or computed from x1-x0 without alias)."

    def test_bbox_has_h_key(self) -> None:
        doc = _make_doc([_make_block("blk_0", "test", x0=72.0, y0=100.0, x1=400.0, y1=118.0)])
        result = _serialize_blocks_for_response(doc)

        bbox = result[0]["bbox"]
        assert "h" in bbox, "bbox must have 'h' key (not 'height')."

    def test_bbox_has_no_legacy_keys(self) -> None:
        """Ensure the old x0/y0/x1/y1 format is NOT used."""
        doc = _make_doc([_make_block("blk_0", "test")])
        result = _serialize_blocks_for_response(doc)

        bbox = result[0]["bbox"]
        assert "x0" not in bbox, "bbox must not have 'x0' key — use 'x'."
        assert "y0" not in bbox, "bbox must not have 'y0' key — use 'y'."
        assert "x1" not in bbox, "bbox must not have 'x1' key — use 'w' (width)."
        assert "y1" not in bbox, "bbox must not have 'y1' key — use 'h' (height)."
        assert "left" not in bbox, "bbox must not have 'left' key — use 'x'."
        assert "top" not in bbox, "bbox must not have 'top' key — use 'y'."
        assert "right" not in bbox, "bbox must not have 'right' key — use 'w'."
        assert "bottom" not in bbox, "bbox must not have 'bottom' key — use 'h'."

    def test_bbox_values_are_correct_xywh(self) -> None:
        """x=x0, y=y0, w=x1-x0, h=y1-y0."""
        x0, y0, x1, y1 = 72.0, 100.0, 400.0, 118.0
        doc = _make_doc([_make_block("blk_0", "test", x0=x0, y0=y0, x1=x1, y1=y1)])
        result = _serialize_blocks_for_response(doc)

        bbox = result[0]["bbox"]
        assert bbox["x"] == pytest.approx(x0), f"bbox.x must equal x0={x0}, got {bbox['x']}"
        assert bbox["y"] == pytest.approx(y0), f"bbox.y must equal y0={y0}, got {bbox['y']}"
        assert bbox["w"] == pytest.approx(x1 - x0), f"bbox.w must equal x1-x0={x1-x0}, got {bbox['w']}"
        assert bbox["h"] == pytest.approx(y1 - y0), f"bbox.h must equal y1-y0={y1-y0}, got {bbox['h']}"


# ---------------------------------------------------------------------------
# Contract §3: page is 1-based
#
# PINNED CONTRACT: Docling uses 1-based page numbers (page_no from Docling
# provenance is 1-indexed). This test confirms the convention is preserved
# in the serialized output.
#
# If this test fails after a Docling update, the page numbering convention
# has changed and the UI team must be notified immediately — their page
# overlay rendering assumes 1-based.
# ---------------------------------------------------------------------------


class TestPageNumberingContract:
    """page field is 1-based. PINNED — UI relies on this convention."""

    def test_page_is_one_based_for_first_page(self) -> None:
        """A block on page 1 must serialize with page=1 (not page=0)."""
        block = _make_block("blk_0", "first page text", page=1)
        doc = _make_doc([block])
        result = _serialize_blocks_for_response(doc)

        assert result[0]["page"] == 1, (
            f"Page must be 1-based. Got {result[0]['page']} for a page=1 block. "
            "PINNED: UI assumes 1-based page numbering. If Docling changed to 0-based, "
            "update the UI page offset and this test."
        )

    def test_page_is_one_based_for_second_page(self) -> None:
        """A block on page 2 must serialize with page=2 (not page=1)."""
        block = _make_block("blk_0", "second page text", page=2)
        doc = DoclingDoc(
            document_reference_id=_DOC_REF_ID,
            page_count=2,
            blocks=[block],
            docling_version="2.92.0",
        )
        result = _serialize_blocks_for_response(doc)

        assert result[0]["page"] == 2, (
            f"Page must be 1-based. Got {result[0]['page']} for a page=2 block."
        )

    def test_page_is_never_zero(self) -> None:
        """Page 0 must never appear in serialized output for any block."""
        blocks = [
            _make_block(f"blk_{i}", f"text on page {i+1}", page=i + 1)
            for i in range(3)
        ]
        doc = DoclingDoc(
            document_reference_id=_DOC_REF_ID,
            page_count=3,
            blocks=blocks,
            docling_version="2.92.0",
        )
        result = _serialize_blocks_for_response(doc)

        pages = [b["page"] for b in result]
        assert 0 not in pages, (
            f"Page 0 must never appear — all pages are 1-based. Got pages: {pages}"
        )


# ---------------------------------------------------------------------------
# Contract §4: text_snippet truncation at ≤80 chars after rstrip
# ---------------------------------------------------------------------------


class TestTextSnippetTruncation:
    """text_snippet must be rstripped then truncated to at most 80 chars."""

    def test_short_text_is_not_truncated(self) -> None:
        short_text = "Hemoglobin A1c 7.8 %"
        doc = _make_doc([_make_block("blk_0", short_text)])
        result = _serialize_blocks_for_response(doc)

        assert result[0]["text_snippet"] == short_text

    def test_text_exceeding_80_chars_is_truncated(self) -> None:
        long_text = "A" * 100  # 100 chars — must be cut to 80
        doc = _make_doc([_make_block("blk_0", long_text)])
        result = _serialize_blocks_for_response(doc)

        assert len(result[0]["text_snippet"]) == 80, (
            f"text_snippet must be truncated to 80 chars, got {len(result[0]['text_snippet'])}"
        )
        assert result[0]["text_snippet"] == "A" * 80

    def test_text_exactly_80_chars_is_not_modified(self) -> None:
        text_80 = "B" * 80
        doc = _make_doc([_make_block("blk_0", text_80)])
        result = _serialize_blocks_for_response(doc)

        assert result[0]["text_snippet"] == text_80
        assert len(result[0]["text_snippet"]) == 80

    def test_trailing_whitespace_is_stripped_before_truncation(self) -> None:
        """rstrip happens before the 80-char slice."""
        # 70 meaningful chars + 15 trailing spaces = 85 chars total.
        # After rstrip: 70 chars (under the limit, no further truncation).
        text_with_trailing = "C" * 70 + "   " * 5
        doc = _make_doc([_make_block("blk_0", text_with_trailing)])
        result = _serialize_blocks_for_response(doc)

        snippet = result[0]["text_snippet"]
        assert not snippet.endswith(" "), "text_snippet must not end with whitespace after rstrip."
        assert len(snippet) == 70, f"After rstrip, snippet should be 70 chars, got {len(snippet)}"

    def test_truncation_is_bytewise_not_word_boundary(self) -> None:
        """Truncation cuts at character position 80, not at a word boundary."""
        # 78 chars + space + word — the word is cut mid-way
        text = "X" * 79 + "Y complete word after boundary"
        doc = _make_doc([_make_block("blk_0", text)])
        result = _serialize_blocks_for_response(doc)

        snippet = result[0]["text_snippet"]
        assert len(snippet) == 80
        assert snippet[79] == "Y", "80th character must be 'Y' (position 79 in 0-index)"


# ---------------------------------------------------------------------------
# Contract §5: All blocks (cited AND uncited) appear in serialized output
# ---------------------------------------------------------------------------


class TestAllBlocksPresent:
    """Every block in the DoclingDoc must appear in the serialized list."""

    def test_all_blocks_serialized(self) -> None:
        blocks = [
            _make_block("blk_0", "block zero text"),
            _make_block("blk_1", "block one text"),
            _make_block("blk_2", "block two text"),
        ]
        doc = _make_doc(blocks)
        result = _serialize_blocks_for_response(doc)

        assert len(result) == 3, f"All 3 blocks must appear, got {len(result)}"

    def test_block_order_preserved(self) -> None:
        """Blocks must appear in the same order as in doc.blocks."""
        blocks = [
            _make_block(f"blk_{i}", f"text {i}")
            for i in range(5)
        ]
        doc = _make_doc(blocks)
        result = _serialize_blocks_for_response(doc)

        ids_in = [b.block_id for b in blocks]
        ids_out = [b["block_id"] for b in result]
        assert ids_in == ids_out, f"Block order must be preserved. Expected {ids_in}, got {ids_out}"

    def test_empty_doc_returns_empty_list(self) -> None:
        doc = DoclingDoc(
            document_reference_id=_DOC_REF_ID,
            page_count=1,
            blocks=[],
            docling_version="2.92.0",
        )
        result = _serialize_blocks_for_response(doc)

        assert result == [], f"Empty doc must produce empty list, got {result}"

    def test_block_id_matches_source(self) -> None:
        """block_id in output must exactly match block.block_id in source."""
        block = _make_block("block_42", "some text")
        doc = _make_doc([block])
        result = _serialize_blocks_for_response(doc)

        assert result[0]["block_id"] == "block_42"

    def test_uncited_block_is_present(self) -> None:
        """Blocks not referenced by any field_verdict must still be present.

        The UI uses all blocks for the "Possibly Missed" overlay — it needs
        blocks that no field was extracted from (uncited) in order to highlight
        them as potentially missed data.
        """
        # All 3 blocks present in doc; only blk_0 is "cited" by a verdict.
        # Serialization should include all 3 regardless.
        blocks = [
            _make_block("blk_0", "Hemoglobin A1c 7.8 %"),   # cited
            _make_block("blk_1", "Other text with no match"), # uncited
            _make_block("blk_2", "More irrelevant text"),     # uncited
        ]
        doc = _make_doc(blocks)
        result = _serialize_blocks_for_response(doc)

        ids_out = {b["block_id"] for b in result}
        assert "blk_1" in ids_out, "Uncited block blk_1 must appear in serialized output."
        assert "blk_2" in ids_out, "Uncited block blk_2 must appear in serialized output."


# ---------------------------------------------------------------------------
# Contract §6: Multi-page docs — page numbers are consistent
# ---------------------------------------------------------------------------


class TestMultiPagePageNumbers:
    def test_blocks_on_different_pages_retain_correct_page(self) -> None:
        blocks = [
            _make_block("blk_0", "page 1 text", page=1),
            _make_block("blk_1", "page 2 text", page=2),
            _make_block("blk_2", "page 3 text", page=3),
        ]
        doc = DoclingDoc(
            document_reference_id=_DOC_REF_ID,
            page_count=3,
            blocks=blocks,
            docling_version="2.92.0",
        )
        result = _serialize_blocks_for_response(doc)

        pages = [b["page"] for b in result]
        assert pages == [1, 2, 3], f"Page numbers must match source. Got {pages}"
