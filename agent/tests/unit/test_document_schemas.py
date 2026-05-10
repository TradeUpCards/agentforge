"""Unit tests for document_schemas.py — W2 Stage 1.

Validates LabReport, IntakeForm, BBox, DoclingDoc, and the extended Citation
model against sentinel fixture data.

Assertion strategy per W2_ARCHITECTURE.md §8.3 (no-PHI-in-logs rubric):
- Assert structure: types, field presence, sentinel-id, BBox shape
- Do NOT assert on raw extracted text values
- Do NOT print patient-identifiable strings in failure messages

Tests run without Docker and without Docling installed (pure Pydantic).
"""

from __future__ import annotations

import pytest
from datetime import date

from agent.document_schemas import (
    Allergy,
    BBox,
    Demographics,
    DoclingBlock,
    DoclingDoc,
    ExtractMeta,
    ExtractedField,
    FamilyHistoryItem,
    IntakeForm,
    LabReport,
    LabResult,
    Medication,
)

# ---------------------------------------------------------------------------
# Sentinel constants
# ---------------------------------------------------------------------------

# Sentinel range was [999_100, 999_199] until 2026-05-09 when it expanded
# to [999_001, 999_999] to support all 200 patients in the demo install
# (real pid p auto-maps to sentinel 999_000 + p via PersonaMap).  Test
# constants follow the new range — VALID stays at 999_100 since that's
# still in range; BELOW + ABOVE are tight-boundary values (one below the
# new lower bound, one above the new upper bound) so an off-by-one in
# the schema validator is caught immediately.
_PATIENT_ID_VALID = 999_100
_PATIENT_ID_BELOW = 999_000      # one below new lower bound 999_001 → rejected
_PATIENT_ID_ABOVE = 1_000_000    # one above new upper bound 999_999 → rejected
_DOC_REF_ID = "DocumentReference/test-lab-999100"


# ---------------------------------------------------------------------------
# Helper builders
# ---------------------------------------------------------------------------


def _make_extract_meta(
    extraction_model: str = "",
    page_count: int = 2,
    confidence: float | None = None,
) -> ExtractMeta:
    return ExtractMeta(
        docling_version="2.92.0",
        extraction_model=extraction_model,
        page_count=page_count,
        extraction_confidence_avg=confidence,
    )


def _make_bbox(
    page: int = 1,
    x0: float = 72.0,
    y0: float = 100.0,
    x1: float = 400.0,
    y1: float = 115.0,
) -> BBox:
    return BBox(page=page, x0=x0, y0=y0, x1=x1, y1=y1)


def _make_lab_result(block_id: str = "block_0") -> LabResult:
    return LabResult(
        test_name="Hemoglobin A1c",
        value=7.8,
        unit="%",
        reference_range="<5.7",
        abnormal=True,
        collection_date=date(2026, 4, 15),
        source_block_id=block_id,
        confidence=0.95,
    )


# ---------------------------------------------------------------------------
# BBox tests
# ---------------------------------------------------------------------------


class TestBBox:
    def test_valid_bbox_round_trips(self) -> None:
        bbox = _make_bbox()
        assert bbox.page == 1
        assert bbox.x0 == pytest.approx(72.0)
        assert bbox.x1 == pytest.approx(400.0)
        assert bbox.y1 > bbox.y0

    def test_x1_must_be_greater_than_x0(self) -> None:
        with pytest.raises(ValueError, match="x1"):
            BBox(page=1, x0=400.0, y0=100.0, x1=72.0, y1=115.0)

    def test_y1_must_be_greater_than_y0(self) -> None:
        with pytest.raises(ValueError, match="y1"):
            BBox(page=1, x0=72.0, y0=200.0, x1=400.0, y1=100.0)

    def test_page_must_be_positive(self) -> None:
        with pytest.raises(ValueError):
            BBox(page=0, x0=72.0, y0=100.0, x1=400.0, y1=115.0)

    def test_x0_negative_raises(self) -> None:
        with pytest.raises(ValueError):
            BBox(page=1, x0=-1.0, y0=100.0, x1=400.0, y1=115.0)


# ---------------------------------------------------------------------------
# LabReport tests
# ---------------------------------------------------------------------------


class TestLabReport:
    def test_valid_lab_report_constructs(self) -> None:
        report = LabReport(
            patient_id=_PATIENT_ID_VALID,
            document_reference_id=_DOC_REF_ID,
            results=[_make_lab_result("block_0"), _make_lab_result("block_1")],
            extraction_metadata=_make_extract_meta(),
        )
        assert report.patient_id == _PATIENT_ID_VALID
        assert report.document_reference_id == _DOC_REF_ID
        assert len(report.results) == 2

    def test_lab_result_fields_are_typed(self) -> None:
        result = _make_lab_result()
        assert isinstance(result.value, float)
        assert isinstance(result.confidence, float)
        assert 0.0 <= result.confidence <= 1.0
        assert isinstance(result.source_block_id, str)

    def test_patient_id_below_sentinel_rejected(self) -> None:
        with pytest.raises(ValueError, match="sentinel range"):
            LabReport(
                patient_id=_PATIENT_ID_BELOW,
                document_reference_id=_DOC_REF_ID,
                results=[],
                extraction_metadata=_make_extract_meta(),
            )

    def test_patient_id_above_sentinel_rejected(self) -> None:
        with pytest.raises(ValueError, match="sentinel range"):
            LabReport(
                patient_id=_PATIENT_ID_ABOVE,
                document_reference_id=_DOC_REF_ID,
                results=[],
                extraction_metadata=_make_extract_meta(),
            )

    def test_patient_id_1_rejected(self) -> None:
        """Real-looking ID (collides with Synthea demo data) is rejected."""
        with pytest.raises(ValueError, match="sentinel range"):
            LabReport(
                patient_id=1,
                document_reference_id=_DOC_REF_ID,
                results=[],
                extraction_metadata=_make_extract_meta(),
            )

    def test_empty_results_list_is_valid(self) -> None:
        """A lab report with no results (e.g. failed OCR) still validates."""
        report = LabReport(
            patient_id=_PATIENT_ID_VALID,
            document_reference_id=_DOC_REF_ID,
            results=[],
            extraction_metadata=_make_extract_meta(),
        )
        assert report.results == []

    def test_all_sentinel_ids_in_range_are_accepted(self) -> None:
        for pid in (999_100, 999_150, 999_199):
            report = LabReport(
                patient_id=pid,
                document_reference_id=_DOC_REF_ID,
                results=[],
                extraction_metadata=_make_extract_meta(),
            )
            assert report.patient_id == pid


# ---------------------------------------------------------------------------
# IntakeForm tests
# ---------------------------------------------------------------------------


class TestIntakeForm:
    def _make_demographics(self) -> Demographics:
        return Demographics(
            name="Synthetic, Test A",
            dob=date(1970, 1, 1),
            sex="M",
            source_block_id="block_0",
        )

    def test_valid_intake_form_constructs(self) -> None:
        form = IntakeForm(
            patient_id=_PATIENT_ID_VALID,
            document_reference_id=_DOC_REF_ID,
            demographics=self._make_demographics(),
            chief_concern="Follow-up for diabetes management",
            current_medications=[
                Medication(name="Metformin", dose="1000mg", frequency="BID", source_block_id="block_5"),
            ],
            allergies=[
                Allergy(substance="Penicillin", reaction="Rash", severity="Moderate", source_block_id="block_7"),
            ],
            family_history=[
                FamilyHistoryItem(condition="Type 2 Diabetes", relation="Father", source_block_id="block_9"),
            ],
            source_citations={"demographics": "block_0", "chief_concern": "block_3"},
            extraction_metadata=_make_extract_meta(),
        )
        assert form.patient_id == _PATIENT_ID_VALID
        assert len(form.current_medications) == 1
        assert len(form.allergies) == 1
        assert len(form.family_history) == 1
        assert form.source_citations["demographics"] == "block_0"

    def test_patient_id_below_sentinel_rejected(self) -> None:
        with pytest.raises(ValueError, match="sentinel range"):
            IntakeForm(
                patient_id=_PATIENT_ID_BELOW,
                document_reference_id=_DOC_REF_ID,
                demographics=self._make_demographics(),
                extraction_metadata=_make_extract_meta(),
            )

    def test_optional_fields_default_to_empty(self) -> None:
        form = IntakeForm(
            patient_id=_PATIENT_ID_VALID,
            document_reference_id=_DOC_REF_ID,
            demographics=self._make_demographics(),
            extraction_metadata=_make_extract_meta(),
        )
        assert form.chief_concern is None
        assert form.current_medications == []
        assert form.allergies == []
        assert form.family_history == []
        assert form.source_citations == {}


# ---------------------------------------------------------------------------
# DoclingDoc tests
# ---------------------------------------------------------------------------


class TestDoclingDoc:
    def _make_block(self, block_id: str, page: int = 1) -> DoclingBlock:
        return DoclingBlock(
            block_id=block_id,
            text=f"Content of {block_id}",
            block_type="text",
            page=page,
            bbox=_make_bbox(page=page),
        )

    def test_block_by_id_returns_correct_block(self) -> None:
        doc = DoclingDoc(
            document_reference_id=_DOC_REF_ID,
            page_count=2,
            blocks=[self._make_block("block_0"), self._make_block("block_1", page=2)],
            docling_version="2.92.0",
        )
        found = doc.block_by_id("block_1")
        assert found is not None
        assert found.block_id == "block_1"
        assert found.page == 2

    def test_block_by_id_returns_none_for_missing(self) -> None:
        doc = DoclingDoc(
            document_reference_id=_DOC_REF_ID,
            page_count=1,
            blocks=[self._make_block("block_0")],
            docling_version="2.92.0",
        )
        assert doc.block_by_id("nonexistent") is None

    def test_empty_blocks_list_is_valid(self) -> None:
        doc = DoclingDoc(
            document_reference_id=_DOC_REF_ID,
            page_count=1,
            blocks=[],
            docling_version="2.92.0",
        )
        assert doc.blocks == []
        assert doc.block_by_id("any") is None


# ---------------------------------------------------------------------------
# Citation extension tests (W2_ARCHITECTURE.md §7.3)
# ---------------------------------------------------------------------------


class TestCitationExtension:
    def test_citation_patient_record_type(self) -> None:
        """patient_record citation does not require bbox."""
        from agent.schemas import Citation

        cit = Citation(
            source_type="patient_record",
            source_id="prescriptions:573",
            field_or_chunk_id="drug",
            quote_or_value="Metformin 1000mg",
        )
        assert cit.source_type == "patient_record"
        assert cit.bbox is None

    def test_citation_guideline_type(self) -> None:
        """guideline citation carries page_or_section and chunk_id."""
        from agent.schemas import Citation

        cit = Citation(
            source_type="guideline",
            source_id="ada-2024-standards",
            page_or_section="S2.3",
            field_or_chunk_id="ada-2024-s2-3-chunk-7",
            quote_or_value="The A1C target for most nonpregnant adults is <7%.",
        )
        assert cit.source_type == "guideline"
        assert cit.page_or_section == "S2.3"
        assert cit.bbox is None

    def test_citation_extracted_document_with_bbox(self) -> None:
        """extracted_document citation can carry a BBox from Docling."""
        from agent.schemas import Citation

        bbox = _make_bbox(page=1, x0=72.0, y0=200.0, x1=400.0, y1=215.0)
        cit = Citation(
            source_type="extracted_document",
            source_id=_DOC_REF_ID,
            page_or_section="1",
            field_or_chunk_id="block_3",
            quote_or_value="7.8",
            bbox=bbox,
        )
        assert cit.source_type == "extracted_document"
        assert cit.bbox is not None
        assert cit.bbox.page == 1
        assert cit.bbox.x0 == pytest.approx(72.0)

    def test_citation_source_type_discriminator_rejects_unknown(self) -> None:
        """Invalid source_type is rejected by Pydantic."""
        from agent.schemas import Citation

        with pytest.raises(ValueError):
            Citation(
                source_type="unknown_type",  # type: ignore[arg-type]
                source_id="some-id",
                field_or_chunk_id="field",
                quote_or_value="value",
            )

    def test_citation_without_bbox_validates(self) -> None:
        """BBox field defaults to None — not required."""
        from agent.schemas import Citation

        cit = Citation(
            source_type="extracted_document",
            source_id=_DOC_REF_ID,
            field_or_chunk_id="block_0",
            quote_or_value="7.8",
        )
        assert cit.bbox is None


# ---------------------------------------------------------------------------
# ExtractedField tests (Haiku extraction pass shape — Stage 2 prep)
# ---------------------------------------------------------------------------


class TestExtractedField:
    def test_valid_extracted_field(self) -> None:
        field = ExtractedField(
            field_name="test_name",
            value="Hemoglobin A1c",
            source_block_id="block_0",
            confidence=0.95,
        )
        assert field.source_block_id == "block_0"
        assert 0.0 <= field.confidence <= 1.0

    def test_null_source_block_id_is_valid(self) -> None:
        """Haiku returns source_block_id=None when it cannot locate a block."""
        field = ExtractedField(
            field_name="test_name",
            value="unknown",
            source_block_id=None,
            confidence=0.0,
        )
        assert field.source_block_id is None

    def test_confidence_out_of_range_rejected(self) -> None:
        with pytest.raises(ValueError):
            ExtractedField(
                field_name="test",
                value="x",
                source_block_id="block_0",
                confidence=1.5,  # > 1.0
            )
