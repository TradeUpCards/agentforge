"""P3 Docling block inventory — quality gate tests.

Covers:
  - _serialize_blocks_for_response returns the full block inventory
  - Text snippets are truncated to 80 chars (bytewise after rstrip)
  - Uncited blocks ARE present in the serialized inventory
  - Block IDs in the serialized list match field_verdict source_block_ids
  - All fields of the block dict are present (block_id, page, bbox, text_snippet)

PHI discipline (W2_ARCHITECTURE.md §8.2-8.3):
  - All sentinel patient_ids in [999100, 999199]
  - text_snippet is 80-char-bounded original document content — acceptable
    because the clinician already sees the original document in the UI

W2_ARCHITECTURE.md references:
  §2.2  — two-stage pipeline; Docling owns bboxes
  §7.3  — bbox format
  P3 brief §4 — docling_blocks_json ships in P3
  P3 brief §5 — 80-char text snippets, PHI-acceptable
"""

from __future__ import annotations

import json

import pytest

from agent.document_schemas import (
    BBox,
    DoclingBlock,
    DoclingDoc,
)
from agent.extractors import _serialize_blocks_for_response

# ---------------------------------------------------------------------------
# Sentinel constants
# ---------------------------------------------------------------------------

_DOC_REF_ID = "DocumentReference/test-p3-blocks-999100"
_PID = 999_100
_MODEL = "claude-haiku-4-5"

# ---------------------------------------------------------------------------
# Doc-builder helpers
# ---------------------------------------------------------------------------


def _make_bbox(
    page: int = 1,
    x0: float = 72.0,
    y0: float = 100.0,
    x1: float = 400.0,
    y1: float = 115.0,
) -> BBox:
    return BBox(page=page, x0=x0, y0=y0, x1=x1, y1=y1)


def _make_block(
    block_id: str,
    text: str,
    page: int = 1,
    x0: float = 72.0,
    y0: float = 100.0,
    x1: float = 400.0,
    y1: float = 115.0,
) -> DoclingBlock:
    return DoclingBlock(
        block_id=block_id,
        text=text,
        block_type="text",
        page=page,
        bbox=_make_bbox(page=page, x0=x0, y0=y0, x1=x1, y1=y1),
    )


def _make_doc(blocks: list[DoclingBlock] | None = None) -> DoclingDoc:
    if blocks is None:
        blocks = [
            _make_block("blk_0", "Hemoglobin A1c  7.8  %  <5.7  H"),
            _make_block("blk_1", "Glucose, Fasting  142  mg/dL  70-99  H"),
            _make_block("blk_2", "eGFR  62  mL/min/1.73m2  >60"),
        ]
    return DoclingDoc(
        document_reference_id=_DOC_REF_ID,
        page_count=1,
        blocks=blocks,
        docling_version="2.92.0",
    )


# ---------------------------------------------------------------------------
# §1 — _serialize_blocks_for_response returns the full block inventory
# ---------------------------------------------------------------------------


class TestSerializeBlocksForResponse:
    def test_response_includes_docling_blocks_field(self) -> None:
        """_serialize_blocks_for_response returns a non-empty list of dicts."""
        doc = _make_doc()
        blocks = _serialize_blocks_for_response(doc)

        assert isinstance(blocks, list)
        assert len(blocks) == len(doc.blocks)

    def test_each_block_dict_has_required_keys(self) -> None:
        """Each block dict must have block_id, page, bbox, text_snippet."""
        doc = _make_doc()
        blocks = _serialize_blocks_for_response(doc)

        for b in blocks:
            assert "block_id" in b, f"Missing block_id in {b!r}"
            assert "page" in b, f"Missing page in {b!r}"
            assert "bbox" in b, f"Missing bbox in {b!r}"
            assert "text_snippet" in b, f"Missing text_snippet in {b!r}"

    def test_bbox_dict_has_x_y_w_h_keys(self) -> None:
        """BBox in the serialized output uses {x, y, w, h} format per P3 contract."""
        doc = _make_doc()
        blocks = _serialize_blocks_for_response(doc)

        for b in blocks:
            bbox = b["bbox"]
            assert isinstance(bbox, dict), f"Expected bbox dict, got {type(bbox)!r}"
            assert set(bbox.keys()) == {"x", "y", "w", "h"}, (
                f"Expected {{x, y, w, h}} keys in bbox, got {set(bbox.keys())!r}"
            )

    def test_bbox_values_are_floats(self) -> None:
        """BBox x, y, w, h values must be floats (not ints)."""
        doc = _make_doc()
        blocks = _serialize_blocks_for_response(doc)

        for b in blocks:
            bbox = b["bbox"]
            for key in ("x", "y", "w", "h"):
                assert isinstance(bbox[key], float), (
                    f"BBox.{key} should be float, got {type(bbox[key])!r}"
                )

    def test_bbox_width_height_positive(self) -> None:
        """BBox w and h must be positive (derived from valid DoclingBlock)."""
        doc = _make_doc()
        blocks = _serialize_blocks_for_response(doc)

        for b in blocks:
            bbox = b["bbox"]
            assert bbox["w"] > 0.0, f"BBox width must be positive, got {bbox['w']}"
            assert bbox["h"] > 0.0, f"BBox height must be positive, got {bbox['h']}"

    def test_block_ids_match_docling_doc_block_ids(self) -> None:
        """Serialized block_ids must match the DoclingDoc block_ids in order."""
        doc = _make_doc()
        blocks = _serialize_blocks_for_response(doc)

        serialized_ids = [b["block_id"] for b in blocks]
        expected_ids = [block.block_id for block in doc.blocks]
        assert serialized_ids == expected_ids, (
            f"Block ID order mismatch: {serialized_ids} != {expected_ids}"
        )

    def test_page_numbers_correct(self) -> None:
        """Serialized page numbers must match the block's page attribute."""
        blocks_input = [
            _make_block("blk_0", "Page 1 content", page=1),
            _make_block(
                "blk_1", "Page 2 content", page=2,
                y0=100.0, y1=115.0,
            ),
        ]
        # blk_1 must use page=2 coords in BBox
        blocks_input[1] = DoclingBlock(
            block_id="blk_1",
            text="Page 2 content",
            block_type="text",
            page=2,
            bbox=BBox(page=2, x0=72.0, y0=100.0, x1=400.0, y1=115.0),
        )
        doc = DoclingDoc(
            document_reference_id=_DOC_REF_ID,
            page_count=2,
            blocks=blocks_input,
            docling_version="2.92.0",
        )
        blocks = _serialize_blocks_for_response(doc)

        assert blocks[0]["page"] == 1
        assert blocks[1]["page"] == 2

    def test_empty_doc_returns_empty_list(self) -> None:
        """A DoclingDoc with no blocks returns an empty list."""
        doc = DoclingDoc(
            document_reference_id=_DOC_REF_ID,
            page_count=1,
            blocks=[],
            docling_version="2.92.0",
        )
        blocks = _serialize_blocks_for_response(doc)
        assert blocks == []


# ---------------------------------------------------------------------------
# §2 — Text snippet truncation (80 chars bytewise after rstrip)
# ---------------------------------------------------------------------------


class TestTextSnippetTruncation:
    def test_block_text_snippets_truncated_to_80_chars(self) -> None:
        """text_snippet must be at most 80 characters long."""
        long_text = "A" * 120  # 120 chars — exceeds 80
        doc = DoclingDoc(
            document_reference_id=_DOC_REF_ID,
            page_count=1,
            blocks=[_make_block("blk_0", long_text)],
            docling_version="2.92.0",
        )
        blocks = _serialize_blocks_for_response(doc)
        assert len(blocks[0]["text_snippet"]) <= 80, (
            f"text_snippet length {len(blocks[0]['text_snippet'])} exceeds 80 chars"
        )

    def test_short_text_not_padded(self) -> None:
        """text_snippet shorter than 80 chars is not padded or altered."""
        short_text = "Hemoglobin A1c 7.8 %"
        doc = DoclingDoc(
            document_reference_id=_DOC_REF_ID,
            page_count=1,
            blocks=[_make_block("blk_0", short_text)],
            docling_version="2.92.0",
        )
        blocks = _serialize_blocks_for_response(doc)
        assert blocks[0]["text_snippet"] == short_text

    def test_exactly_80_char_text_unchanged(self) -> None:
        """text_snippet exactly 80 chars passes through unchanged."""
        text_80 = "B" * 80
        doc = DoclingDoc(
            document_reference_id=_DOC_REF_ID,
            page_count=1,
            blocks=[_make_block("blk_0", text_80)],
            docling_version="2.92.0",
        )
        blocks = _serialize_blocks_for_response(doc)
        assert blocks[0]["text_snippet"] == text_80
        assert len(blocks[0]["text_snippet"]) == 80

    def test_trailing_whitespace_rstripped_before_truncation(self) -> None:
        """Text is rstripped before truncation (trailing spaces removed)."""
        text_with_trailing = "Cholesterol, Total   210 mg/dL   " + "   "
        doc = DoclingDoc(
            document_reference_id=_DOC_REF_ID,
            page_count=1,
            blocks=[_make_block("blk_0", text_with_trailing)],
            docling_version="2.92.0",
        )
        blocks = _serialize_blocks_for_response(doc)
        snippet = blocks[0]["text_snippet"]
        # Must not end with whitespace
        assert snippet == snippet.rstrip(), (
            f"text_snippet {snippet!r} has trailing whitespace after rstrip"
        )

    def test_truncation_preserves_original_case_and_punctuation(self) -> None:
        """No normalization beyond rstrip+truncate: case and punctuation preserved."""
        text = "Cholesterol, Total          210 mg/dL"  # < 80 chars
        doc = DoclingDoc(
            document_reference_id=_DOC_REF_ID,
            page_count=1,
            blocks=[_make_block("blk_0", text)],
            docling_version="2.92.0",
        )
        blocks = _serialize_blocks_for_response(doc)
        assert blocks[0]["text_snippet"] == text

    def test_truncation_at_exactly_80_chars_of_rstripped_text(self) -> None:
        """Truncation is applied AFTER rstrip: 80+ char rstripped text truncates."""
        # 85 non-whitespace chars then trailing spaces
        base = "C" * 85
        text = base + "   "
        doc = DoclingDoc(
            document_reference_id=_DOC_REF_ID,
            page_count=1,
            blocks=[_make_block("blk_0", text)],
            docling_version="2.92.0",
        )
        blocks = _serialize_blocks_for_response(doc)
        # Should be first 80 chars of the rstripped text (85 C's → first 80)
        assert blocks[0]["text_snippet"] == "C" * 80
        assert len(blocks[0]["text_snippet"]) == 80


# ---------------------------------------------------------------------------
# §3 — Uncited blocks ARE present in the serialized inventory
# ---------------------------------------------------------------------------


class TestUncitedBlocksPresent:
    def test_uncited_blocks_present_in_response(self) -> None:
        """All blocks appear in the serialized inventory, not only cited ones.

        Strategy: 3 blocks where only blk_0 is typically cited by a lab result;
        blk_1 and blk_2 are uncited. All 3 must appear in the output.
        """
        doc = _make_doc()
        blocks = _serialize_blocks_for_response(doc)

        # All 3 blocks must appear
        assert len(blocks) == 3, (
            f"Expected 3 blocks in inventory (including uncited), got {len(blocks)}"
        )
        block_ids = {b["block_id"] for b in blocks}
        assert block_ids == {"blk_0", "blk_1", "blk_2"}

    def test_inventory_contains_every_block_in_doc(self) -> None:
        """The serialized count equals the block count in the DoclingDoc."""
        blocks_input = [
            _make_block(f"blk_{i}", f"Content block {i}")
            for i in range(10)
        ]
        doc = DoclingDoc(
            document_reference_id=_DOC_REF_ID,
            page_count=1,
            blocks=blocks_input,
            docling_version="2.92.0",
        )
        blocks = _serialize_blocks_for_response(doc)
        assert len(blocks) == 10, (
            f"Expected 10 blocks in inventory, got {len(blocks)}"
        )


# ---------------------------------------------------------------------------
# §4 — Cited blocks match field_verdict block IDs
# ---------------------------------------------------------------------------


class TestCitedBlocksMatchFieldVerdicts:
    def test_cited_blocks_match_field_verdict_block_ids(self) -> None:
        """Block IDs referenced in field_verdicts are a subset of the serialized
        block inventory (all cited blocks must be present in docling_blocks).

        Strategy: parse a lab report and compare field_verdict source_block_ids
        against the block IDs returned by _serialize_blocks_for_response.
        """
        from agent.extractors.haiku_extraction import parse_lab_report

        doc = DoclingDoc(
            document_reference_id=_DOC_REF_ID,
            page_count=1,
            blocks=[
                _make_block("blk_0", "Hemoglobin A1c 7.8 % <5.7"),
                _make_block("blk_1", "Glucose, Fasting 142 mg/dL 70-99"),
                _make_block("blk_2", "eGFR 62 mL/min/1.73m2"),
                # blk_3 is uncited — present in inventory but not cited
                _make_block("blk_3", "Patient header line"),
            ],
            docling_version="2.92.0",
        )
        payload = json.dumps({
            "results": [
                {
                    "test_name": "Hemoglobin A1c",
                    "value": 7.8,
                    "unit": "%",
                    "source_block_id": "blk_0",
                    "confidence": 0.95,
                },
                {
                    "test_name": "Glucose, Fasting",
                    "value": 142.0,
                    "unit": "mg/dL",
                    "source_block_id": "blk_1",
                    "confidence": 0.90,
                },
                {
                    "test_name": "eGFR",
                    "value": 62.0,
                    "unit": "mL/min/1.73m2",
                    "source_block_id": "blk_2",
                    "confidence": 0.85,
                },
            ]
        })

        _, stats = parse_lab_report(
            haiku_json_text=payload,
            doc=doc,
            patient_id=_PID,
            doc_ref_id=_DOC_REF_ID,
            extraction_model=_MODEL,
        )

        # Serialize the full block inventory
        inventory = _serialize_blocks_for_response(doc)
        inventory_ids = {b["block_id"] for b in inventory}

        # All non-None source_block_ids from field_verdicts must be in the inventory
        for fp, sid, reason in stats.field_verdicts:
            if sid is not None:
                assert sid in inventory_ids, (
                    f"field_verdict source_block_id={sid!r} (field_path={fp!r}) "
                    f"is not in the docling_blocks inventory. "
                    f"All cited blocks must appear in the full block list."
                )

        # Uncited block (blk_3) must also be in the inventory
        assert "blk_3" in inventory_ids, (
            "Uncited block blk_3 must be in the docling_blocks inventory"
        )
        # Total inventory includes all 4 blocks
        assert len(inventory) == 4


# ---------------------------------------------------------------------------
# §5 — docling_blocks omitted on refusal (acceptable behavior)
# ---------------------------------------------------------------------------


class TestDoclingBlocksOmittedOnRefusal:
    def test_docling_blocks_omitted_on_refusal(self) -> None:
        """When extraction is refused (ExtractionLowGrounding), the caller
        may omit docling_blocks from the response.

        This test verifies that _serialize_blocks_for_response can still be
        called on the DoclingDoc even if the extraction failed — the block
        inventory is independent of whether extraction succeeded.
        The decision to omit it from the HTTP response when status='refused'
        is main.py's responsibility (held until Bram's MR merges).

        Here we verify the structural contract: the serializer always returns
        a valid list when called with a valid DoclingDoc, regardless of whether
        extraction succeeded.
        """
        doc = _make_doc()
        # _serialize_blocks_for_response is pure — always returns a list
        blocks = _serialize_blocks_for_response(doc)
        # Must be a list (possibly empty, but never None or an exception)
        assert isinstance(blocks, list)
        assert len(blocks) == len(doc.blocks)
