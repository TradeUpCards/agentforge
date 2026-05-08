"""P3 verifier-reason surfacing — quality gate tests.

Covers:
  - record_verified appends (field_path, source_block_id, None) to field_verdicts
  - record_failure appends (field_path, source_block_id, reason) to field_verdicts
  - confidence_oob is strip-and-continue, not ValueError
  - REASON_CONFIDENCE_OOB constant is emitted when confidence is OOB
  - field_paths in a parsed result are all static literals (PHI-safe)
  - parse_lab_report calls record_verified on every successfully verified field
  - parse_intake_form calls record_verified on every successfully verified field

PHI discipline (W2_ARCHITECTURE.md §8.2-8.3):
  - All sentinel patient_ids in [999100, 999199]
  - field_path assertions use static string literals, never interpolated LLM output
  - No raw field values or block text asserted or compared

W2_ARCHITECTURE.md references:
  §2.4  — extraction verifier (30% threshold, substring grounding)
  §8.2  — sentinel patient_id range 999100-999199
  §8.3  — no-PHI-in-logs
  P3 brief §1 — confidence_oob strip-and-continue
  P3 brief §2 — field_verdicts on ExtractionStats
  P3 brief §3 — static field_path literals
"""

from __future__ import annotations

import json

import pytest

from agent.document_schemas import (
    BBox,
    DoclingBlock,
    DoclingDoc,
    IntakeForm,
    LabReport,
)
from agent.extractors.haiku_extraction import (
    REASON_BLOCK_NOT_FOUND,
    REASON_CONFIDENCE_OOB,
    REASON_VALUE_NOT_SUBSTRING,
    ExtractionStats,
    parse_intake_form,
    parse_lab_report,
)

# ---------------------------------------------------------------------------
# Sentinel constants
# ---------------------------------------------------------------------------

_PID = 999_100
_DOC_REF_ID = "DocumentReference/test-p3-verifier-999100"
_MODEL = "claude-haiku-4-5"

# ---------------------------------------------------------------------------
# Field path allowlist for PHI-safety check
#
# These are ALL static string literals that the parsers may emit as
# field_path values.  No interpolated LLM output should appear here.
# If a new field is added to the parser, add its static pattern here too.
# ---------------------------------------------------------------------------

_LAB_FIELD_PATH_PATTERN_PREFIX = "results["
_INTAKE_FIELD_PATH_ALLOWLIST = frozenset({
    "demographics.name",
    "chief_concern",
    "confidence",
})
_INTAKE_FIELD_PATH_LIST_PREFIXES = (
    "current_medications[",
    "allergies[",
    "family_history[",
)

# ---------------------------------------------------------------------------
# Doc-builder helpers (mirror P1/P2 conventions)
# ---------------------------------------------------------------------------


def _make_bbox(page: int = 1) -> BBox:
    return BBox(page=page, x0=10.0, y0=10.0, x1=200.0, y1=25.0)


def _make_block(block_id: str, text: str, page: int = 1) -> DoclingBlock:
    return DoclingBlock(
        block_id=block_id,
        text=text,
        block_type="text",
        page=page,
        bbox=_make_bbox(page=page),
    )


def _make_lab_doc() -> DoclingDoc:
    """Minimal doc with grounded lab result text in each block."""
    return DoclingDoc(
        document_reference_id=_DOC_REF_ID,
        page_count=1,
        blocks=[
            _make_block("blk_0", "Hemoglobin A1c 7.8 % <5.7 H"),
            _make_block("blk_1", "Glucose, Fasting 142 mg/dL 70-99 H"),
            _make_block("blk_2", "eGFR 62 mL/min/1.73m2 >60"),
        ],
        docling_version="2.92.0",
    )


def _make_intake_doc() -> DoclingDoc:
    """Minimal doc for intake form extraction."""
    return DoclingDoc(
        document_reference_id=_DOC_REF_ID,
        page_count=1,
        blocks=[
            _make_block("blk_0", "Patient Information: Synthetic, Test A DOB: 1970-01-01"),
            _make_block("blk_1", "Chief Concern: Follow-up for diabetes management"),
            _make_block("blk_2", "Medications: Metformin 1000mg BID"),
            _make_block("blk_3", "Allergies: Penicillin - Rash (Moderate)"),
            _make_block("blk_4", "Family History: Father - Type 2 Diabetes"),
        ],
        docling_version="2.92.0",
    )


def _lab_json_grounded() -> str:
    """All 3 lab results have valid confidence and ground correctly."""
    return json.dumps({
        "results": [
            {
                "test_name": "Hemoglobin A1c",
                "value": 7.8,
                "unit": "%",
                "reference_range": "<5.7",
                "abnormal": True,
                "collection_date": None,
                "source_block_id": "blk_0",
                "confidence": 0.95,
            },
            {
                "test_name": "Glucose, Fasting",
                "value": 142.0,
                "unit": "mg/dL",
                "reference_range": "70-99",
                "abnormal": True,
                "collection_date": None,
                "source_block_id": "blk_1",
                "confidence": 0.90,
            },
            {
                "test_name": "eGFR",
                "value": 62.0,
                "unit": "mL/min/1.73m2",
                "reference_range": ">60",
                "abnormal": False,
                "collection_date": None,
                "source_block_id": "blk_2",
                "confidence": 0.85,
            },
        ]
    })


def _intake_json_grounded() -> str:
    """All intake fields have valid confidence and ground correctly."""
    return json.dumps({
        "demographics": {
            "name": "Synthetic, Test A",
            "dob": "1970-01-01",
            "sex": "M",
            "source_block_id": "blk_0",
        },
        "chief_concern": "Follow-up for diabetes management",
        "chief_concern_block_id": "blk_1",
        "current_medications": [
            {
                "name": "Metformin",
                "dose": "1000mg",
                "frequency": "BID",
                "source_block_id": "blk_2",
            }
        ],
        "allergies": [
            {
                "substance": "Penicillin",
                "reaction": "Rash",
                "severity": "Moderate",
                "source_block_id": "blk_3",
            }
        ],
        "family_history": [
            {
                "condition": "Type 2 Diabetes",
                "relation": "Father",
                "source_block_id": "blk_4",
            }
        ],
        "source_citations": {},
        "confidence": 0.88,
    })


# ---------------------------------------------------------------------------
# §1 — record_verified appends (field_path, source_block_id, None)
# ---------------------------------------------------------------------------


class TestRecordVerified:
    def test_record_verified_appends_to_field_verdicts(self) -> None:
        """record_verified(field_path, source_block_id) appends tuple with None reason."""
        stats = ExtractionStats()
        stats.record_verified("results[0].test_name", "blk_0")

        assert len(stats.field_verdicts) == 1
        fp, sid, reason = stats.field_verdicts[0]
        assert fp == "results[0].test_name"
        assert sid == "blk_0"
        assert reason is None  # None = verified (not stripped)

    def test_record_verified_multiple_appends_in_order(self) -> None:
        """Multiple record_verified calls append in call order."""
        stats = ExtractionStats()
        stats.record_verified("results[0].test_name", "blk_0")
        stats.record_verified("results[1].test_name", "blk_1")

        assert len(stats.field_verdicts) == 2
        assert stats.field_verdicts[0][0] == "results[0].test_name"
        assert stats.field_verdicts[1][0] == "results[1].test_name"
        # Both have None reason (verified)
        assert all(v[2] is None for v in stats.field_verdicts)

    def test_record_verified_does_not_increment_stripped_fields(self) -> None:
        """record_verified must not affect stripped_fields count."""
        stats = ExtractionStats()
        stats.record_verified("results[0].test_name", "blk_0")
        assert stats.stripped_fields == 0

    def test_record_verified_source_block_id_can_be_none(self) -> None:
        """record_verified accepts None source_block_id (unresolved block)."""
        stats = ExtractionStats()
        stats.record_verified("demographics.name", None)
        assert stats.field_verdicts[0][1] is None
        assert stats.field_verdicts[0][2] is None  # still verified


# ---------------------------------------------------------------------------
# §2 — record_failure appends (field_path, source_block_id, reason)
# ---------------------------------------------------------------------------


class TestRecordFailure:
    def test_record_failure_appends_to_field_verdicts_with_reason(self) -> None:
        """record_failure appends tuple with the reason code."""
        stats = ExtractionStats()
        stats.total_fields += 1
        stats.record_failure(
            REASON_VALUE_NOT_SUBSTRING,
            field_path="results[0].test_name",
            source_block_id="blk_0",
        )

        assert len(stats.field_verdicts) == 1
        fp, sid, reason = stats.field_verdicts[0]
        assert fp == "results[0].test_name"
        assert sid == "blk_0"
        assert reason == REASON_VALUE_NOT_SUBSTRING

    def test_record_failure_block_not_found_reason(self) -> None:
        """record_failure with REASON_BLOCK_NOT_FOUND records correct reason."""
        stats = ExtractionStats()
        stats.total_fields += 1
        stats.record_failure(
            REASON_BLOCK_NOT_FOUND,
            field_path="results[1].test_name",
            source_block_id=None,
        )

        _, sid, reason = stats.field_verdicts[0]
        assert sid is None
        assert reason == REASON_BLOCK_NOT_FOUND

    def test_record_failure_increments_stripped_fields(self) -> None:
        """record_failure increments stripped_fields (existing behavior preserved)."""
        stats = ExtractionStats()
        stats.total_fields += 1
        stats.record_failure(REASON_VALUE_NOT_SUBSTRING, field_path="results[0].test_name")
        assert stats.stripped_fields == 1

    def test_record_failure_default_field_path_empty_string(self) -> None:
        """record_failure with no field_path uses empty string default."""
        stats = ExtractionStats()
        stats.total_fields += 1
        stats.record_failure(REASON_BLOCK_NOT_FOUND)
        fp, _, _ = stats.field_verdicts[0]
        assert fp == ""


# ---------------------------------------------------------------------------
# §3 — confidence_oob strips field, does NOT raise ValueError
# ---------------------------------------------------------------------------


class TestConfidenceOobStripAndContinue:
    def test_confidence_oob_strips_field_not_raises(self) -> None:
        """Fields with confidence > 1.0 are stripped (no ValueError raised).

        Uses 5 fields: 1 OOB + 4 valid → strip rate = 20% < 30% threshold.
        """
        doc = DoclingDoc(
            document_reference_id=_DOC_REF_ID,
            page_count=1,
            blocks=[
                _make_block("b0", "Hemoglobin A1c 7.8 %"),
                _make_block("b1", "Glucose Fasting 142 mg/dL"),
                _make_block("b2", "eGFR 62 mL/min"),
                _make_block("b3", "Cholesterol 210 mg/dL"),
                _make_block("b4", "Triglycerides 150 mg/dL"),
            ],
            docling_version="2.92.0",
        )
        payload = json.dumps({
            "results": [
                {
                    "test_name": "Hemoglobin A1c",
                    "value": 7.8,
                    "unit": "%",
                    "source_block_id": "b0",
                    "confidence": 1.5,  # OOB — must strip, not raise
                },
                {
                    "test_name": "Glucose Fasting",
                    "value": 142.0,
                    "unit": "mg/dL",
                    "source_block_id": "b1",
                    "confidence": 0.9,
                },
                {
                    "test_name": "eGFR",
                    "value": 62.0,
                    "unit": "mL/min",
                    "source_block_id": "b2",
                    "confidence": 0.85,
                },
                {
                    "test_name": "Cholesterol",
                    "value": 210.0,
                    "unit": "mg/dL",
                    "source_block_id": "b3",
                    "confidence": 0.88,
                },
                {
                    "test_name": "Triglycerides",
                    "value": 150.0,
                    "unit": "mg/dL",
                    "source_block_id": "b4",
                    "confidence": 0.92,
                },
            ]
        })

        # Must NOT raise ValueError (P3 strip-and-continue)
        report, stats = parse_lab_report(
            haiku_json_text=payload,
            doc=doc,
            patient_id=_PID,
            doc_ref_id=_DOC_REF_ID,
            extraction_model=_MODEL,
        )
        assert isinstance(report, LabReport)
        # 4 valid results remain (1 stripped)
        assert len(report.results) == 4

    def test_confidence_oob_records_failure_with_constant(self) -> None:
        """REASON_CONFIDENCE_OOB constant is recorded in verifier_reason_counts."""
        doc = DoclingDoc(
            document_reference_id=_DOC_REF_ID,
            page_count=1,
            blocks=[
                _make_block("b0", "Hemoglobin A1c 7.8 %"),
                _make_block("b1", "Glucose Fasting 142 mg/dL"),
                _make_block("b2", "eGFR 62 mL/min"),
                _make_block("b3", "Cholesterol 210 mg/dL"),
                _make_block("b4", "Triglycerides 150 mg/dL"),
            ],
            docling_version="2.92.0",
        )
        payload = json.dumps({
            "results": [
                {
                    "test_name": "Hemoglobin A1c",
                    "value": 7.8,
                    "unit": "%",
                    "source_block_id": "b0",
                    "confidence": 2.0,  # OOB
                },
                {
                    "test_name": "Glucose Fasting",
                    "value": 142.0,
                    "unit": "mg/dL",
                    "source_block_id": "b1",
                    "confidence": 0.9,
                },
                {
                    "test_name": "eGFR",
                    "value": 62.0,
                    "unit": "mL/min",
                    "source_block_id": "b2",
                    "confidence": 0.85,
                },
                {
                    "test_name": "Cholesterol",
                    "value": 210.0,
                    "unit": "mg/dL",
                    "source_block_id": "b3",
                    "confidence": 0.88,
                },
                {
                    "test_name": "Triglycerides",
                    "value": 150.0,
                    "unit": "mg/dL",
                    "source_block_id": "b4",
                    "confidence": 0.92,
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
        # The REASON_CONFIDENCE_OOB constant must appear in verifier_reason_counts
        assert REASON_CONFIDENCE_OOB in stats.verifier_reason_counts
        assert stats.verifier_reason_counts[REASON_CONFIDENCE_OOB] == 1
        # The field_verdicts must contain a tuple with the OOB reason
        oob_verdicts = [v for v in stats.field_verdicts if v[2] == REASON_CONFIDENCE_OOB]
        assert len(oob_verdicts) == 1


# ---------------------------------------------------------------------------
# §4 — field_path strings are static literals (no PHI, no LLM output)
# ---------------------------------------------------------------------------


class TestFieldPathsAreStaticLiterals:
    """PHI-safety constraint: field_path strings must be drawn from a
    closed vocabulary of static literals.

    The parser produces paths like "results[0].test_name",
    "demographics.name", "current_medications[0].name", etc.
    These index positions are ordinal integers (0, 1, 2...) from the
    iteration loop — never derived from LLM output or block text.

    This test parses a known fixture and asserts every field_path in
    field_verdicts matches the expected pattern. It fails immediately if
    an LLM value ever leaks into a field_path.
    """

    def test_field_verdicts_field_paths_are_static_literals_no_phi(self) -> None:
        """All field_paths in a parsed lab example are in the expected allowlist."""
        doc = _make_lab_doc()
        _, stats = parse_lab_report(
            haiku_json_text=_lab_json_grounded(),
            doc=doc,
            patient_id=_PID,
            doc_ref_id=_DOC_REF_ID,
            extraction_model=_MODEL,
        )

        for fp, _sid, _reason in stats.field_verdicts:
            # Must start with the "results[" prefix
            assert fp.startswith(_LAB_FIELD_PATH_PATTERN_PREFIX), (
                f"field_path {fp!r} does not start with expected prefix "
                f"{_LAB_FIELD_PATH_PATTERN_PREFIX!r}. "
                "LLM output must never be interpolated into field_path."
            )
            # Must end with ".test_name" (the only lab field we verify)
            assert fp.endswith(".test_name"), (
                f"field_path {fp!r} has unexpected suffix — expected '.test_name'"
            )
            # The index portion between brackets must be a pure integer
            start = fp.index("[") + 1
            end = fp.index("]")
            index_str = fp[start:end]
            assert index_str.isdigit(), (
                f"field_path {fp!r}: index {index_str!r} is not a pure integer. "
                "Index must be the loop ordinal, never LLM output."
            )

    def test_intake_form_field_paths_are_static_literals(self) -> None:
        """All field_paths from a parsed intake example are from the static allowlist."""
        doc = _make_intake_doc()
        _, stats = parse_intake_form(
            haiku_json_text=_intake_json_grounded(),
            doc=doc,
            patient_id=_PID,
            doc_ref_id=_DOC_REF_ID,
            extraction_model=_MODEL,
        )

        for fp, _sid, _reason in stats.field_verdicts:
            # Must be either a known top-level field or a known list pattern
            is_known_top_level = fp in _INTAKE_FIELD_PATH_ALLOWLIST
            is_known_list = any(
                fp.startswith(prefix)
                for prefix in _INTAKE_FIELD_PATH_LIST_PREFIXES
            )
            assert is_known_top_level or is_known_list, (
                f"Unexpected field_path {fp!r} in intake form verdicts. "
                "All field_paths must be static literals from the known vocabulary."
            )
            # For list paths, assert the index is a pure integer
            if is_known_list:
                start = fp.index("[") + 1
                end = fp.index("]")
                index_str = fp[start:end]
                assert index_str.isdigit(), (
                    f"List field_path {fp!r}: index {index_str!r} is not a pure integer"
                )


# ---------------------------------------------------------------------------
# §5 — parse_lab_report records verified verdicts on success
# ---------------------------------------------------------------------------


class TestLabReportParserRecordsVerified:
    def test_lab_report_parser_records_verified_on_success(self) -> None:
        """parse_lab_report records a verified verdict for every passing field."""
        doc = _make_lab_doc()
        _, stats = parse_lab_report(
            haiku_json_text=_lab_json_grounded(),
            doc=doc,
            patient_id=_PID,
            doc_ref_id=_DOC_REF_ID,
            extraction_model=_MODEL,
        )

        # All 3 fields should have passed → 3 verified verdicts
        verified = [v for v in stats.field_verdicts if v[2] is None]
        assert len(verified) == 3, (
            f"Expected 3 verified verdicts, got {len(verified)}: {stats.field_verdicts}"
        )

        # No stripped verdicts for this grounded fixture
        stripped = [v for v in stats.field_verdicts if v[2] is not None]
        assert len(stripped) == 0, (
            f"Expected 0 stripped verdicts, got {len(stripped)}"
        )

        # Each verified verdict carries a non-None source_block_id
        for fp, sid, reason in verified:
            assert sid is not None, (
                f"Verified field_path={fp!r} has None source_block_id"
            )
            assert reason is None


# ---------------------------------------------------------------------------
# §6 — parse_intake_form records verified verdicts on success
# ---------------------------------------------------------------------------


class TestIntakeFormParserRecordsVerified:
    def test_intake_form_parser_records_verified_on_success(self) -> None:
        """parse_intake_form records a verified verdict for every passing field."""
        doc = _make_intake_doc()
        _, stats = parse_intake_form(
            haiku_json_text=_intake_json_grounded(),
            doc=doc,
            patient_id=_PID,
            doc_ref_id=_DOC_REF_ID,
            extraction_model=_MODEL,
        )

        # Expected verified fields: demographics.name, chief_concern,
        # current_medications[0].name, allergies[0].substance,
        # family_history[0].condition = 5 verified verdicts
        verified = [v for v in stats.field_verdicts if v[2] is None]
        assert len(verified) >= 5, (
            f"Expected >=5 verified verdicts, got {len(verified)}: {stats.field_verdicts}"
        )

        # No stripped verdicts for this grounded fixture
        stripped = [v for v in stats.field_verdicts if v[2] is not None]
        assert len(stripped) == 0, (
            f"Expected 0 stripped verdicts for grounded fixture, "
            f"got {len(stripped)}: {stripped}"
        )

    def test_intake_form_stripped_field_records_reason(self) -> None:
        """When a medication name fails grounding, its reason is recorded.

        Strategy: 1 ungrounded medication + 4 grounded fields = 5 total fields.
        Strip rate = 1/5 = 20% < 30%, so ExtractionLowGrounding is not raised.
        The 4 grounded fields are: demographics.name, chief_concern,
        allergies[0].substance, family_history[0].condition.
        """
        doc = DoclingDoc(
            document_reference_id=_DOC_REF_ID,
            page_count=1,
            blocks=[
                _make_block("blk_0", "Patient Information: Synthetic, Test A"),
                _make_block("blk_1", "Chief Concern: Follow-up for diabetes management"),
                _make_block("blk_2", "Medications: only irrelevant text here"),
                _make_block("blk_3", "Allergies: Penicillin - Rash"),
                _make_block("blk_4", "Family History: Father - Type 2 Diabetes"),
            ],
            docling_version="2.92.0",
        )
        # Medication name "Metformin" is NOT in blk_2 text — grounding fails for it
        payload = json.dumps({
            "demographics": {
                "name": "Synthetic, Test A",
                "dob": None,
                "sex": None,
                "source_block_id": "blk_0",
            },
            "chief_concern": "Follow-up for diabetes management",
            "chief_concern_block_id": "blk_1",
            "current_medications": [
                {
                    "name": "Metformin",  # NOT in blk_2 — grounding fails
                    "dose": None,
                    "frequency": None,
                    "source_block_id": "blk_2",
                }
            ],
            "allergies": [
                {
                    "substance": "Penicillin",  # in blk_3 — grounding passes
                    "reaction": "Rash",
                    "severity": None,
                    "source_block_id": "blk_3",
                }
            ],
            "family_history": [
                {
                    "condition": "Type 2 Diabetes",  # in blk_4 — grounding passes
                    "relation": "Father",
                    "source_block_id": "blk_4",
                }
            ],
            "source_citations": {},
            "confidence": 0.8,
        })

        _, stats = parse_intake_form(
            haiku_json_text=payload,
            doc=doc,
            patient_id=_PID,
            doc_ref_id=_DOC_REF_ID,
            extraction_model=_MODEL,
        )

        # The medication field_verdict should have a non-None reason
        med_verdicts = [
            v for v in stats.field_verdicts
            if v[0].startswith("current_medications[")
        ]
        assert len(med_verdicts) == 1
        fp, sid, reason = med_verdicts[0]
        assert reason == REASON_VALUE_NOT_SUBSTRING, (
            f"Expected REASON_VALUE_NOT_SUBSTRING, got {reason!r}"
        )
        assert sid == "blk_2"
