"""Unit tests for the eval-runner DSL — citation_has_quote + citation_has_page.

Added 2026-05-09 alongside Aria's HITL → citation-bbox-overlay pivot
(`feat/citation-bbox-overlay` branch). These two new DSL checks lock the
PRD §5 minimum citation shape population in for regression-defense.

Why a dedicated unit test file:
  Existing `agent/tests/eval/test_eval_cases.py` runs case YAMLs end-to-end
  through the FastAPI TestClient — slow (full agent dispatch) and not the
  right granularity for testing DSL logic. This file exercises `_evaluate()`
  directly with hand-built `EvalCase` + mock response dicts so we can prove
  each tautology guard fires precisely on its target stub form, and that the
  rubric is robust to citation-payload variations.
"""

from __future__ import annotations

import pytest

from agent.tests.eval.runner import EvalCase, _evaluate


def _make_case(*, expected: dict) -> EvalCase:
    """Minimal EvalCase whose only assertion is the DSL check under test.

    `expected` is the YAML's `expected:` block. We omit `status` to skip
    the status-equality check; tests focus on citation-shape DSL only.
    """
    return EvalCase(
        name="dsl-test-case",
        description="exercise citation-shape DSL only",
        patient_id=1,
        user_id=1,
        messages=[{"role": "user", "content": "test"}],
        expected=expected,
    )


# ---------------------------------------------------------------------------
# citation_has_quote
# ---------------------------------------------------------------------------


class TestCitationHasQuote:
    """`citation_has_quote: true` — quote_or_value populated AND not a tautology.

    Closes historic stub forms in three citation builders:
      - patient_record (evidence_retriever:309): quote == source_id
      - extracted_document LabReport (intake_extractor:193): quote == field_or_chunk_id
      - extracted_document IntakeForm (intake_extractor:206): "field_name:block_id"
      - legacy /chat path (agent.py:1244): empty string
    """

    def test_passes_when_all_citations_have_real_quote(self) -> None:
        case = _make_case(expected={"citation_has_quote": True})
        response = {
            "status": "ok",
            "citations": [
                {
                    "source_type": "patient_record",
                    "source_id": "lists:1408",
                    "field_or_chunk_id": "1408",
                    "quote_or_value": "Type 2 diabetes mellitus",  # real value, not echo
                    "page_or_section": None,
                },
                {
                    "source_type": "extracted_document",
                    "source_id": "doc-99",
                    "field_or_chunk_id": "block_33",
                    "quote_or_value": "A1C 7.8%",  # real extracted text
                    "page_or_section": "1",
                },
                {
                    "source_type": "guideline",
                    "source_id": "ada-2024-s2.3-chunk-7",
                    "field_or_chunk_id": "ada-2024-s2.3-chunk-7",
                    "quote_or_value": "Target A1C below 7% for most non-pregnant adults.",
                    "page_or_section": "S2.3",
                },
            ],
        }
        result = _evaluate(case, 200, response)
        assert not result.failures, result.failures

    def test_fails_on_patient_record_source_id_echo(self) -> None:
        """quote_or_value == source_id is the patient_record stub form."""
        case = _make_case(expected={"citation_has_quote": True})
        response = {
            "status": "ok",
            "citations": [
                {
                    "source_type": "patient_record",
                    "source_id": "lists:1408",
                    "field_or_chunk_id": "1408",
                    "quote_or_value": "lists:1408",  # ← stub: echoes source_id
                    "page_or_section": None,
                },
            ],
        }
        result = _evaluate(case, 200, response)
        assert any(
            "citation_has_quote" in f and "quote == source_id" in f
            for f in result.failures
        ), result.failures

    def test_fails_on_extracted_document_block_id_echo(self) -> None:
        """quote_or_value == field_or_chunk_id is the LabReport stub form."""
        case = _make_case(expected={"citation_has_quote": True})
        response = {
            "status": "ok",
            "citations": [
                {
                    "source_type": "extracted_document",
                    "source_id": "doc-99",
                    "field_or_chunk_id": "block_33",
                    "quote_or_value": "block_33",  # ← stub: echoes field_or_chunk_id
                    "page_or_section": "1",
                },
            ],
        }
        result = _evaluate(case, 200, response)
        assert any(
            "citation_has_quote" in f and "quote == field_or_chunk_id" in f
            for f in result.failures
        ), result.failures

    def test_fails_on_intake_form_field_block_composite(self) -> None:
        """quote_or_value == 'field_name:block_id' is the IntakeForm stub form."""
        case = _make_case(expected={"citation_has_quote": True})
        response = {
            "status": "ok",
            "citations": [
                {
                    "source_type": "extracted_document",
                    "source_id": "doc-99",
                    "field_or_chunk_id": "block_42",
                    "quote_or_value": "current_medications:block_42",  # ← stub
                    "page_or_section": "1",
                },
            ],
        }
        result = _evaluate(case, 200, response)
        assert any(
            "citation_has_quote" in f and "field_name:block_id" in f
            for f in result.failures
        ), result.failures

    def test_fails_on_empty_quote(self) -> None:
        """quote_or_value == '' is the legacy /chat path stub form (agent.py:1244)."""
        case = _make_case(expected={"citation_has_quote": True})
        response = {
            "status": "ok",
            "citations": [
                {
                    "source_type": "patient_record",
                    "source_id": "lists:1408",
                    "field_or_chunk_id": "1408",
                    "quote_or_value": "",  # ← legacy stub
                    "page_or_section": None,
                },
            ],
        }
        result = _evaluate(case, 200, response)
        assert any(
            "citation_has_quote" in f and "empty quote_or_value" in f
            for f in result.failures
        ), result.failures

    def test_does_not_fire_when_dsl_not_requested(self) -> None:
        """citation_has_quote: false (or absent) means no check runs.

        Defends against accidental enforcement on legacy cases that haven't
        been updated to the populated-citation contract.
        """
        # Absent — should not fire.
        case_absent = _make_case(expected={"min_claims": 0})
        response = {
            "status": "ok",
            "citations": [
                {
                    "source_type": "patient_record",
                    "source_id": "lists:1408",
                    "field_or_chunk_id": "1408",
                    "quote_or_value": "lists:1408",  # would fail if check were active
                },
            ],
        }
        result = _evaluate(case_absent, 200, response)
        assert not any("citation_has_quote" in f for f in result.failures), result.failures

        # Falsy — should not fire either.
        case_false = _make_case(expected={"citation_has_quote": False})
        result_false = _evaluate(case_false, 200, response)
        assert not any("citation_has_quote" in f for f in result_false.failures), result_false.failures

    def test_passes_when_citations_list_empty(self) -> None:
        """No citations → vacuously satisfies citation_has_quote.

        A different rubric (min_citations / min_guideline_citations) handles
        the "should have citations" assertion. citation_has_quote only checks
        the SHAPE of citations that are present.
        """
        case = _make_case(expected={"citation_has_quote": True})
        response = {"status": "ok", "citations": []}
        result = _evaluate(case, 200, response)
        assert not any("citation_has_quote" in f for f in result.failures), result.failures

    def test_reports_aggregate_failure_count(self) -> None:
        """Multiple stub citations report aggregate count + truncated preview."""
        case = _make_case(expected={"citation_has_quote": True})
        # Build 7 stub citations to verify truncation kicks in (cap = 5).
        stub_cites = [
            {
                "source_type": "patient_record",
                "source_id": f"lists:{i}",
                "field_or_chunk_id": str(i),
                "quote_or_value": f"lists:{i}",  # all stubs
            }
            for i in range(7)
        ]
        response = {"status": "ok", "citations": stub_cites}
        result = _evaluate(case, 200, response)
        assert any(
            "citation_has_quote: 7 citation(s)" in f and "+2 more" in f
            for f in result.failures
        ), result.failures


# ---------------------------------------------------------------------------
# citation_has_page
# ---------------------------------------------------------------------------


class TestCitationHasPage:
    """`citation_has_page: true` — page_or_section populated for extracted_document.

    Scoped to extracted_document only by design:
      - patient_record: N/A (no PDF coordinate)
      - guideline: scoped out (uses section identifier; population path was
        already correct pre-pivot — separate concern)
    """

    def test_passes_when_extracted_document_has_page(self) -> None:
        case = _make_case(expected={"citation_has_page": True})
        response = {
            "status": "ok",
            "citations": [
                {
                    "source_type": "extracted_document",
                    "source_id": "doc-99",
                    "field_or_chunk_id": "block_33",
                    "page_or_section": "1",
                    "quote_or_value": "A1C 7.8%",
                },
            ],
        }
        result = _evaluate(case, 200, response)
        assert not result.failures, result.failures

    def test_fails_when_extracted_document_page_is_none(self) -> None:
        """intake_extractor.py:191/204 stub: page_or_section=None."""
        case = _make_case(expected={"citation_has_page": True})
        response = {
            "status": "ok",
            "citations": [
                {
                    "source_type": "extracted_document",
                    "source_id": "doc-99",
                    "field_or_chunk_id": "block_33",
                    "page_or_section": None,  # ← stub
                    "quote_or_value": "A1C 7.8%",
                },
            ],
        }
        result = _evaluate(case, 200, response)
        assert any(
            "citation_has_page" in f and "doc-99#block_33" in f
            for f in result.failures
        ), result.failures

    def test_fails_when_extracted_document_page_is_empty_string(self) -> None:
        case = _make_case(expected={"citation_has_page": True})
        response = {
            "status": "ok",
            "citations": [
                {
                    "source_type": "extracted_document",
                    "source_id": "doc-99",
                    "field_or_chunk_id": "block_33",
                    "page_or_section": "",  # ← also a fail
                    "quote_or_value": "A1C 7.8%",
                },
            ],
        }
        result = _evaluate(case, 200, response)
        assert any("citation_has_page" in f for f in result.failures), result.failures

    def test_patient_record_citations_skip_page_check(self) -> None:
        """patient_record citations have page_or_section=None by design — N/A."""
        case = _make_case(expected={"citation_has_page": True})
        response = {
            "status": "ok",
            "citations": [
                {
                    "source_type": "patient_record",
                    "source_id": "lists:1408",
                    "field_or_chunk_id": "1408",
                    "page_or_section": None,  # ← N/A for patient_record, not a fail
                    "quote_or_value": "Type 2 diabetes mellitus",
                },
            ],
        }
        result = _evaluate(case, 200, response)
        assert not result.failures, result.failures

    def test_guideline_citations_skip_page_check(self) -> None:
        """guideline citations are scoped out — population path was correct pre-pivot."""
        case = _make_case(expected={"citation_has_page": True})
        response = {
            "status": "ok",
            "citations": [
                {
                    "source_type": "guideline",
                    "source_id": "ada-2024-s2.3-chunk-7",
                    "field_or_chunk_id": "ada-2024-s2.3-chunk-7",
                    "page_or_section": None,  # ← N/A here per scoping rule
                    "quote_or_value": "Target A1C below 7% for most non-pregnant adults.",
                },
            ],
        }
        result = _evaluate(case, 200, response)
        assert not result.failures, result.failures

    def test_does_not_fire_when_dsl_not_requested(self) -> None:
        """citation_has_page absent or false → no check runs."""
        case_absent = _make_case(expected={"min_claims": 0})
        response = {
            "status": "ok",
            "citations": [
                {
                    "source_type": "extracted_document",
                    "source_id": "doc-99",
                    "field_or_chunk_id": "block_33",
                    "page_or_section": None,  # would fail if check active
                    "quote_or_value": "A1C 7.8%",
                },
            ],
        }
        result = _evaluate(case_absent, 200, response)
        assert not any("citation_has_page" in f for f in result.failures), result.failures

    def test_mixed_source_types_only_flags_extracted_document(self) -> None:
        """Mixed citation list — only extracted_document with null page fails."""
        case = _make_case(expected={"citation_has_page": True})
        response = {
            "status": "ok",
            "citations": [
                {
                    "source_type": "patient_record",
                    "source_id": "lists:1",
                    "field_or_chunk_id": "1",
                    "page_or_section": None,  # OK (N/A)
                    "quote_or_value": "Hypertension",
                },
                {
                    "source_type": "extracted_document",
                    "source_id": "doc-99",
                    "field_or_chunk_id": "block_5",
                    "page_or_section": None,  # ← FAIL
                    "quote_or_value": "A1C 7.8%",
                },
                {
                    "source_type": "guideline",
                    "source_id": "ada-x",
                    "field_or_chunk_id": "ada-x",
                    "page_or_section": None,  # OK (scoped out)
                    "quote_or_value": "Target …",
                },
            ],
        }
        result = _evaluate(case, 200, response)
        page_failures = [f for f in result.failures if "citation_has_page" in f]
        assert len(page_failures) == 1
        assert "doc-99#block_5" in page_failures[0]
        # The other two source types should NOT appear in the failure message.
        assert "lists:1" not in page_failures[0]
        assert "ada-x" not in page_failures[0]


# ---------------------------------------------------------------------------
# Combined behavior
# ---------------------------------------------------------------------------


class TestBothChecksTogether:
    """Both DSL checks can be requested in one case; failures aggregate independently."""

    def test_both_pass_on_fully_populated_citations(self) -> None:
        case = _make_case(
            expected={"citation_has_quote": True, "citation_has_page": True}
        )
        response = {
            "status": "ok",
            "citations": [
                {
                    "source_type": "extracted_document",
                    "source_id": "doc-99",
                    "field_or_chunk_id": "block_33",
                    "page_or_section": "2",
                    "quote_or_value": "A1C 7.8% (high)",
                },
            ],
        }
        result = _evaluate(case, 200, response)
        assert not result.failures, result.failures

    def test_both_fail_independently_on_double_stub(self) -> None:
        """A single citation that's stub on BOTH fields produces TWO distinct failures."""
        case = _make_case(
            expected={"citation_has_quote": True, "citation_has_page": True}
        )
        response = {
            "status": "ok",
            "citations": [
                {
                    "source_type": "extracted_document",
                    "source_id": "doc-99",
                    "field_or_chunk_id": "block_33",
                    "page_or_section": None,  # ← page stub
                    "quote_or_value": "block_33",  # ← quote stub (echoes field_or_chunk_id)
                },
            ],
        }
        result = _evaluate(case, 200, response)
        assert any("citation_has_quote" in f for f in result.failures)
        assert any("citation_has_page" in f for f in result.failures)
