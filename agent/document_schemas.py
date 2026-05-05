"""Pydantic document schemas for W2 multimodal ingestion.

Two top-level schemas — LabReport and IntakeForm — plus shared primitives.
Every extracted field carries a source_block_id (Docling block ID) so the
bbox citation contract is maintained end-to-end.

Architectural references:
- W2_ARCHITECTURE.md §7.1 (LabReport)
- W2_ARCHITECTURE.md §7.2 (IntakeForm)
- W2_ARCHITECTURE.md §2.2 (two-stage pipeline: Docling layout → Haiku schema)
- W2_ARCHITECTURE.md §8.2 (sentinel patient_id range 999100-999199)

NO Haiku / Anthropic calls in this module — schemas only.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Sentinel patient_id guard
# ---------------------------------------------------------------------------

_SENTINEL_MIN = 999_100
_SENTINEL_MAX = 999_199


def _assert_sentinel(patient_id: int) -> int:
    """Reject patient_ids outside the W2 synthetic sentinel range.

    W2_ARCHITECTURE.md §8.2: all W2 document fixtures must use patient_id
    in [999100, 999199]. IDs below 999100 may collide with Synthea demo
    data; IDs above 999199 are outside the reserved W2 block.
    """
    if not (_SENTINEL_MIN <= patient_id <= _SENTINEL_MAX):
        raise ValueError(
            f"patient_id is outside the W2 sentinel range "
            f"[{_SENTINEL_MIN}, {_SENTINEL_MAX}]. "
            "Use a value in 999100–999199 for synthetic fixtures."
        )
    return patient_id


# ---------------------------------------------------------------------------
# Bounding box primitive
# ---------------------------------------------------------------------------


class BBox(BaseModel):
    """Page-coordinate bounding box produced by Docling layout engine.

    All coordinates are in points (PDF coordinate system, origin at
    bottom-left of the page). The layout engine owns these values;
    no LLM pass ever writes a BBox.

    W2_ARCHITECTURE.md §7.3: "bbox: BBox | None — populated for
    extracted_document citations."
    """

    page: int = Field(ge=1, description="1-indexed page number")
    x0: float = Field(ge=0.0, description="Left edge in points")
    y0: float = Field(ge=0.0, description="Bottom edge in points")
    x1: float = Field(description="Right edge in points")
    y1: float = Field(description="Top edge in points")

    @field_validator("x1")
    @classmethod
    def x1_gt_x0(cls, v: float, info: object) -> float:
        # info.data is populated in order of field declaration
        data = getattr(info, "data", {})
        x0 = data.get("x0", 0.0)
        if v <= x0:
            raise ValueError(f"x1 ({v}) must be greater than x0 ({x0})")
        return v

    @field_validator("y1")
    @classmethod
    def y1_gt_y0(cls, v: float, info: object) -> float:
        data = getattr(info, "data", {})
        y0 = data.get("y0", 0.0)
        if v <= y0:
            raise ValueError(f"y1 ({v}) must be greater than y0 ({y0})")
        return v


# ---------------------------------------------------------------------------
# Extraction metadata (shared by LabReport and IntakeForm)
# ---------------------------------------------------------------------------


class ExtractMeta(BaseModel):
    """Provenance metadata recorded at extraction time.

    Stored alongside every extracted document so reviewers can answer:
    - What version of the layout engine produced the blocks?
    - What model mapped blocks to schema fields?
    - How many pages did the document have?
    """

    docling_version: str
    """Version string of the Docling library used for layout extraction."""

    extraction_model: str
    """Model used for schema extraction (e.g. 'claude-haiku-4-5').
    Empty string for Stage 1 skeleton (no LLM pass yet)."""

    page_count: int = Field(ge=1)
    """Total pages in the source document."""

    extraction_confidence_avg: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Average confidence across all extracted fields (0-1). "
        "None when no LLM extraction pass has run.",
    )


# ---------------------------------------------------------------------------
# LabReport schema (W2_ARCHITECTURE.md §7.1)
# ---------------------------------------------------------------------------


class LabResult(BaseModel):
    """A single lab test result extracted from a scanned lab PDF.

    Every field that the Haiku extraction pass fills must include a
    source_block_id pointing back to the Docling block that contains the
    value. The extraction verifier (§2.4) uses this to confirm the value
    appears in the named block's text.
    """

    test_name: str = Field(description="e.g. 'Hemoglobin A1c'")
    value: float
    unit: str = Field(description="e.g. '%'")
    reference_range: str | None = Field(
        default=None, description="e.g. '<5.7' or '4.0-5.6'"
    )
    abnormal: bool | None = None
    collection_date: date | None = None
    source_block_id: str = Field(
        description="Docling block_id; carries the BBox for click-to-source."
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Extractor confidence for this field (0-1).",
    )


class LabReport(BaseModel):
    """Structured extraction of a scanned lab report PDF.

    patient_id must be in the W2 sentinel range [999100, 999199] for all
    synthetic fixtures used in tests and eval cases.
    """

    patient_id: int
    document_reference_id: str = Field(
        description="FHIR DocumentReference ID linking back to the source PDF."
    )
    results: list[LabResult] = Field(default_factory=list)
    extraction_metadata: ExtractMeta

    @field_validator("patient_id")
    @classmethod
    def patient_id_in_sentinel_range(cls, v: int) -> int:
        return _assert_sentinel(v)


# ---------------------------------------------------------------------------
# IntakeForm schema (W2_ARCHITECTURE.md §7.2)
# ---------------------------------------------------------------------------


class Demographics(BaseModel):
    """Allowlisted demographic fields.

    Name is included because it is required for form identity. DOB and sex
    are clinical fields. No SSN, no phone, no address — per §8.3 no-PHI
    posture for extracted fields sent to the LLM.
    """

    name: str = Field(description="Patient name as it appears on the form.")
    dob: date | None = None
    sex: str | None = Field(
        default=None, description="As recorded on the form (e.g. 'M', 'F', 'Male')."
    )
    source_block_id: str = Field(
        description="Docling block_id for the demographics section."
    )


class Medication(BaseModel):
    name: str
    dose: str | None = None
    frequency: str | None = None
    source_block_id: str


class Allergy(BaseModel):
    substance: str
    reaction: str | None = None
    severity: str | None = None
    source_block_id: str


class FamilyHistoryItem(BaseModel):
    condition: str
    relation: str | None = None
    source_block_id: str


class IntakeForm(BaseModel):
    """Structured extraction of a patient intake form.

    patient_id must be in the W2 sentinel range [999100, 999199] for all
    synthetic fixtures.
    """

    patient_id: int
    document_reference_id: str = Field(
        description="FHIR DocumentReference ID linking back to the source form."
    )
    demographics: Demographics
    chief_concern: str | None = None
    current_medications: list[Medication] = Field(default_factory=list)
    allergies: list[Allergy] = Field(default_factory=list)
    family_history: list[FamilyHistoryItem] = Field(default_factory=list)
    source_citations: dict[str, str] = Field(
        default_factory=dict,
        description="Maps field_name → source_block_id for top-level fields.",
    )
    extraction_metadata: ExtractMeta

    @field_validator("patient_id")
    @classmethod
    def patient_id_in_sentinel_range(cls, v: int) -> int:
        return _assert_sentinel(v)


# ---------------------------------------------------------------------------
# Extracted field wrapper (for Haiku extraction pass — Stage 2)
# ---------------------------------------------------------------------------


class ExtractedField(BaseModel):
    """Output shape for a single field from the Haiku schema extraction pass.

    The extraction verifier (W2_ARCHITECTURE.md §2.4) uses source_block_id
    to look up the Docling block and confirms the value appears in its text.
    Fields with source_block_id=None are treated as ungrounded and stripped.
    """

    field_name: str
    value: str
    source_block_id: str | None = Field(
        default=None,
        description="None means Haiku could not locate a source block.",
    )
    confidence: float = Field(ge=0.0, le=1.0)


# ---------------------------------------------------------------------------
# DoclingBlock — internal representation of a Docling layout element
# ---------------------------------------------------------------------------


class DoclingBlock(BaseModel):
    """A single layout element produced by the Docling layout engine.

    This is the internal canonical form for passing Docling output between
    the layout stage and the schema extraction stage. Haiku receives a list
    of these and maps field values back to block_ids.

    The bbox field carries the real coordinates from Docling — never
    computed or estimated by an LLM.
    """

    block_id: str = Field(description="Stable identifier within this document.")
    text: str = Field(description="OCR'd / extracted text content of this block.")
    block_type: Literal[
        "text", "table", "figure", "header", "footer", "list_item", "unknown"
    ] = "text"
    page: int = Field(ge=1)
    bbox: BBox


# ---------------------------------------------------------------------------
# DoclingDoc — the full output of a Docling layout pass
# ---------------------------------------------------------------------------


class DoclingDoc(BaseModel):
    """Full structured output of Docling layout extraction for one document.

    This is what Stage 1 (attach_and_extract skeleton) returns. Stage 2
    (Haiku extraction) receives this and produces LabReport / IntakeForm.
    """

    document_reference_id: str
    page_count: int = Field(ge=1)
    blocks: list[DoclingBlock] = Field(default_factory=list)
    docling_version: str

    def block_by_id(self, block_id: str) -> DoclingBlock | None:
        """Look up a block by its stable ID.

        Used by the extraction verifier (§2.4) to confirm extracted field
        values appear in the named block's text.
        """
        for block in self.blocks:
            if block.block_id == block_id:
                return block
        return None
