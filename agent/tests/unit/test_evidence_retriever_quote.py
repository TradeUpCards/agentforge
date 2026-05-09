"""Unit tests for `_format_patient_record_quote` in evidence_retriever.

Closes the residual PRD §5 gap on `agent/graph/workers/evidence_retriever.py:309`
that Aria's 2026-05-09 population-fix commit `e8e29cf7c` did not cover. Aria's
fix populated `quote_or_value` for `extracted_document` (intake_extractor) and
the legacy /chat path (agent.py:1244-1266); this Bram-side fix populates the
/graph_chat path's `patient_record` citations from `evidence_retriever`.

Tests mirror Aria's per-table dispatch pattern from her intake_extractor
follow-up: positive cases per table, fallback behavior, truncation, and the
degraded-fallback signaling contract (empty fields → `table:record_id` echo
which the eval rubric flags as a tautology — intentional).
"""

from __future__ import annotations

from agent.graph.workers.evidence_retriever import _format_patient_record_quote
from agent.schemas import CitationStrength, RetrievedRecord


def _make_rec(table: str, record_id: str, fields: dict) -> RetrievedRecord:
    """Minimal RetrievedRecord factory for table + fields under test."""
    return RetrievedRecord(
        table=table,
        record_id=record_id,
        citation_strength=CitationStrength.STRUCTURED,
        fields=fields,
    )


# ---------------------------------------------------------------------------
# Per-table dispatch
# ---------------------------------------------------------------------------


class TestListsTable:
    """`lists` covers both medical problems AND allergies (type='allergy').

    The displayable label is in `title` for both. Diagnosis ICD code is a
    rare fallback — only used when title is unset.
    """

    def test_medical_problem_uses_title(self) -> None:
        rec = _make_rec(
            "lists",
            "1408",
            {
                "title": "Type 2 diabetes mellitus",
                "diagnosis": "ICD-10:E11.9",
                "type": "medical_problem",
            },
        )
        assert _format_patient_record_quote(rec) == "Type 2 diabetes mellitus"

    def test_allergy_uses_title(self) -> None:
        """Allergies live in lists with type='allergy'; title carries the substance."""
        rec = _make_rec(
            "lists",
            "5102",
            {
                "title": "Penicillin",
                "type": "allergy",
                "severity": "moderate",
                "reaction": "rash",
            },
        )
        assert _format_patient_record_quote(rec) == "Penicillin"

    def test_falls_back_to_diagnosis_when_title_absent(self) -> None:
        rec = _make_rec(
            "lists",
            "9999",
            {"title": None, "diagnosis": "ICD-10:I10", "type": "medical_problem"},
        )
        assert _format_patient_record_quote(rec) == "ICD-10:I10"


class TestPrescriptionsTable:
    def test_renders_drug_dosage_frequency(self) -> None:
        rec = _make_rec(
            "prescriptions",
            "2231",
            {
                "drug": "Metformin",
                "dosage": "1000mg",
                "frequency": "BID",
                "active": True,
            },
        )
        assert _format_patient_record_quote(rec) == "Metformin 1000mg BID"

    def test_skips_missing_parts_gracefully(self) -> None:
        """A prescription missing dosage still renders drug + frequency."""
        rec = _make_rec(
            "prescriptions",
            "2999",
            {"drug": "Aspirin", "dosage": None, "frequency": "QD"},
        )
        assert _format_patient_record_quote(rec) == "Aspirin QD"

    def test_drug_only(self) -> None:
        rec = _make_rec("prescriptions", "3001", {"drug": "Atorvastatin"})
        assert _format_patient_record_quote(rec) == "Atorvastatin"


class TestProcedureResultTable:
    def test_renders_name_value_units(self) -> None:
        rec = _make_rec(
            "procedure_result",
            "5421",
            {
                "loinc": "4548-4",
                "name": "HbA1c",
                "value": "7.8",
                "units": "%",
                "reference_range": "<5.7",
                "abnormal_flag": "H",
                "date": "2026-03-15",
            },
        )
        assert _format_patient_record_quote(rec) == "HbA1c 7.8 %"

    def test_lab_without_units(self) -> None:
        rec = _make_rec(
            "procedure_result",
            "5500",
            {"name": "Hemoglobin", "value": "13.2", "units": None},
        )
        assert _format_patient_record_quote(rec) == "Hemoglobin 13.2"


class TestFormEncounterTable:
    def test_renders_date_and_reason(self) -> None:
        rec = _make_rec(
            "form_encounter",
            "9012",
            {
                "date": "2026-01-22",
                "reason": "Diabetes follow-up",
                "assessment_plan": "Continue metformin",
            },
        )
        assert _format_patient_record_quote(rec) == "2026-01-22: Diabetes follow-up"

    def test_renders_reason_only_when_date_absent(self) -> None:
        rec = _make_rec(
            "form_encounter",
            "9013",
            {"date": None, "reason": "Annual physical"},
        )
        assert _format_patient_record_quote(rec) == "Annual physical"

    def test_renders_date_only_when_reason_absent(self) -> None:
        rec = _make_rec(
            "form_encounter", "9014", {"date": "2026-02-15", "reason": None}
        )
        assert _format_patient_record_quote(rec) == "2026-02-15"


# ---------------------------------------------------------------------------
# Fallback + edge cases
# ---------------------------------------------------------------------------


class TestGenericFallback:
    """Unknown tables fall through to first-non-empty-field-value."""

    def test_unknown_table_uses_first_non_empty_field(self) -> None:
        rec = _make_rec(
            "future_table_v2",
            "42",
            {"identifier": None, "label": "Some value", "extra": "ignored"},
        )
        assert _format_patient_record_quote(rec) == "Some value"

    def test_unknown_table_coerces_non_string_field(self) -> None:
        rec = _make_rec("future_table_v2", "43", {"count": 7})
        assert _format_patient_record_quote(rec) == "7"


class TestDegradedFallback:
    """Empty fields → `table:record_id` echo (DSL flags it as tautology, which
    correctly signals an empty record rather than silently producing a
    misleading non-empty quote).
    """

    def test_empty_fields_returns_table_record_id_tautology(self) -> None:
        rec = _make_rec("lists", "777", {})
        # Echo signals "no displayable content" — Bram's eval DSL flags it.
        assert _format_patient_record_quote(rec) == "lists:777"

    def test_all_none_fields_returns_table_record_id_tautology(self) -> None:
        rec = _make_rec(
            "prescriptions", "888", {"drug": None, "dosage": None, "frequency": None}
        )
        assert _format_patient_record_quote(rec) == "prescriptions:888"

    def test_whitespace_only_falls_back(self) -> None:
        """Strip whitespace before the empty check — '   ' is effectively empty."""
        rec = _make_rec("lists", "999", {"title": "   "})
        assert _format_patient_record_quote(rec) == "lists:999"


class TestTruncation:
    """80-char cap matches Aria's pattern in agent.py:1244-1266."""

    def test_long_title_truncated_to_80_chars(self) -> None:
        long_title = "a" * 200
        rec = _make_rec("lists", "1", {"title": long_title})
        result = _format_patient_record_quote(rec)
        assert len(result) == 80
        assert result == "a" * 80

    def test_title_at_exactly_80_chars_not_truncated(self) -> None:
        title_80 = "a" * 80
        rec = _make_rec("lists", "1", {"title": title_80})
        assert _format_patient_record_quote(rec) == title_80

    def test_title_under_80_chars_unchanged(self) -> None:
        rec = _make_rec("lists", "1", {"title": "Hypertension"})
        assert _format_patient_record_quote(rec) == "Hypertension"


# ---------------------------------------------------------------------------
# Bram's DSL guard interaction (defense-by-construction)
# ---------------------------------------------------------------------------


class TestDSLGuardInteraction:
    """Verify the function returns shapes that Bram's `citation_has_quote`
    DSL accepts. The DSL fails when:
      - quote == source_id (here: f"{rec.table}:{rec.record_id}")
      - quote == field_or_chunk_id (here: rec.record_id)
      - quote is empty
      - quote == 'field_name:block_id' shape (IntakeForm-specific, not
         applicable to patient_record)

    These tests prove the function avoids those tautologies in the happy
    path AND that the degraded fallback DELIBERATELY hits guard #1 — the
    eval signal we want for an empty record.
    """

    def test_happy_path_avoids_source_id_tautology(self) -> None:
        rec = _make_rec("lists", "1408", {"title": "Type 2 diabetes mellitus"})
        quote = _format_patient_record_quote(rec)
        source_id = f"{rec.table}:{rec.record_id}"
        assert quote != source_id

    def test_happy_path_avoids_field_or_chunk_id_tautology(self) -> None:
        rec = _make_rec("prescriptions", "2231", {"drug": "Metformin"})
        quote = _format_patient_record_quote(rec)
        # field_or_chunk_id is rec.record_id at the citation level
        assert quote != rec.record_id

    def test_degraded_fallback_intentionally_trips_dsl_guard(self) -> None:
        """Empty record → table:record_id echo → eval rubric flags as tautology.

        This is intentional: the rubric failure correctly signals "this
        record has no displayable content" rather than masking it with a
        meaningless non-empty quote like 'unknown' or '(empty)'.
        """
        rec = _make_rec("lists", "777", {})
        quote = _format_patient_record_quote(rec)
        source_id = f"{rec.table}:{rec.record_id}"
        # The fallback EQUALS source_id — Bram's DSL guard #2 trips → eval
        # surfaces the empty-record case in the report.
        assert quote == source_id
