"""P1 Eval Metrics — Quality gate tests.

Covers:
  - ExtractionStats strip-rate bookkeeping (parse_lab_report, parse_intake_form)
  - Template-ID resolution (resolve_template_id)
  - Langfuse span attribute emission (_run_haiku_extraction)
  - Cost computation (compute_cost_usd)
  - verify_field reason-code return value

PHI discipline (W2_ARCHITECTURE.md §8.2-8.3):
  - All sentinel patient_ids in [999100, 999199]
  - No raw extracted values in assertions; only counts and field names
  - Filenames from TEMPLATE_BY_FILENAME or synthetic placeholders

W2_ARCHITECTURE.md references:
  §2.4  — extraction verifier (30% threshold, substring grounding)
  §5.1  — >5% category regression → build-fail
  §5.5  — per-rubric meta-tests and adversarial regression cases
  §8.2  — sentinel patient_id range 999100-999199
  §8.3  — no-PHI-in-logs (no raw values in test output / assertions)

All P1 signature changes are now landed:
  - parse_lab_report / parse_intake_form return tuple[Schema, ExtractionStats]
  - verify_field returns tuple[bool, str | None]
  - _run_haiku_extraction accepts `filename` and populates all 10 P1 span keys
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.document_schemas import (
    BBox,
    DoclingBlock,
    DoclingDoc,
    IntakeForm,
    LabReport,
)

# ---------------------------------------------------------------------------
# Sentinel constants (W2_ARCHITECTURE.md §8.2)
# ---------------------------------------------------------------------------

_PID = 999_100          # sentinel — must be in [999100, 999199]
_DOC_REF_ID = "DocumentReference/test-p1-999100"
_MODEL = "claude-haiku-4-5"

# ---------------------------------------------------------------------------
# Shared doc-builder helpers (copied from test_haiku_extraction.py conventions)
# ---------------------------------------------------------------------------


def _make_bbox(page: int = 1) -> BBox:
    return BBox(page=page, x0=72.0, y0=100.0, x1=400.0, y1=115.0)


def _make_block(block_id: str, text: str, page: int = 1) -> DoclingBlock:
    return DoclingBlock(
        block_id=block_id,
        text=text,
        block_type="text",
        page=page,
        bbox=_make_bbox(page=page),
    )


def _make_doc(blocks: list[DoclingBlock]) -> DoclingDoc:
    return DoclingDoc(
        document_reference_id=_DOC_REF_ID,
        page_count=1,
        blocks=blocks,
        docling_version="2.92.0",
    )


# ---------------------------------------------------------------------------
# Test 1: strip-rate bookkeeping — LabReport
# (depends on parse_lab_report → tuple[LabReport, ExtractionStats])
# ---------------------------------------------------------------------------


class TestStripRateLabReport:
    """Verify ExtractionStats counts from parse_lab_report.

    Pins the new tuple return: parse_lab_report returns
    (LabReport, ExtractionStats). Previously was a strict-xfail until the
    signature change landed; the change is in place as of P1.
    """

    def _build_doc_and_response(self) -> tuple[DoclingDoc, str]:
        """5 blocks: 0-3 contain grounded values, 4 is unrelated.
        Response: 5 results — 4 pass grounding, 1 fails → 20% strip rate.
        """
        blocks = [
            _make_block("block_0", "Hemoglobin A1c 7.8 %"),
            _make_block("block_1", "Glucose, Fasting 142 mg/dL"),
            _make_block("block_2", "eGFR 62 mL/min"),
            _make_block("block_3", "Triglycerides 180 mg/dL"),
            _make_block("block_4", "Patient demographics section — no lab values here"),
        ]
        doc = _make_doc(blocks)

        # Result 0-3: test_name IS substring of cited block → PASS
        # Result 4: test_name NOT in block_4 text → FAIL grounding → stripped
        response = json.dumps({
            "results": [
                {
                    "test_name": "Hemoglobin A1c",
                    "value": 7.8,
                    "unit": "%",
                    "reference_range": None,
                    "abnormal": None,
                    "collection_date": None,
                    "source_block_id": "block_0",
                    "confidence": 0.95,
                },
                {
                    "test_name": "Glucose, Fasting",
                    "value": 142.0,
                    "unit": "mg/dL",
                    "reference_range": None,
                    "abnormal": None,
                    "collection_date": None,
                    "source_block_id": "block_1",
                    "confidence": 0.90,
                },
                {
                    "test_name": "eGFR",
                    "value": 62.0,
                    "unit": "mL/min",
                    "reference_range": None,
                    "abnormal": None,
                    "collection_date": None,
                    "source_block_id": "block_2",
                    "confidence": 0.88,
                },
                {
                    "test_name": "Triglycerides",
                    "value": 180.0,
                    "unit": "mg/dL",
                    "reference_range": None,
                    "abnormal": None,
                    "collection_date": None,
                    "source_block_id": "block_3",
                    "confidence": 0.85,
                },
                {
                    # "Invented Analyte" is NOT in block_4 ("Patient demographics…")
                    "test_name": "Invented Analyte",
                    "value": 99.0,
                    "unit": "units",
                    "reference_range": None,
                    "abnormal": None,
                    "collection_date": None,
                    "source_block_id": "block_4",
                    "confidence": 0.50,
                },
            ]
        })
        return doc, response

    def test_stats_counts(self) -> None:
        """5 results, 1 fails grounding → total=5, stripped=1, rate=0.2."""
        from agent.extractors.haiku_extraction import parse_lab_report

        doc, response = self._build_doc_and_response()
        # New signature: returns (LabReport, ExtractionStats)
        result = parse_lab_report(
            haiku_json_text=response,
            doc=doc,
            patient_id=_PID,
            doc_ref_id=_DOC_REF_ID,
            extraction_model=_MODEL,
        )
        # Unpack tuple — this is the API change that triggers xfail until landed
        report, stats = result

        assert isinstance(report, LabReport)
        # Structural counts — no raw values in assertions
        assert stats.total_fields == 5
        assert stats.stripped_fields == 1
        assert abs(stats.strip_rate - 0.2) < 1e-9
        # 4 passing results remain
        assert len(report.results) == 4

    def test_stats_reason_counts(self) -> None:
        """The single stripped field must be recorded under value_not_substring_of_block."""
        from agent.extractors.haiku_extraction import (
            REASON_VALUE_NOT_SUBSTRING,
            parse_lab_report,
        )

        doc, response = self._build_doc_and_response()
        _report, stats = parse_lab_report(
            haiku_json_text=response,
            doc=doc,
            patient_id=_PID,
            doc_ref_id=_DOC_REF_ID,
            extraction_model=_MODEL,
        )
        assert stats.verifier_reason_counts == {REASON_VALUE_NOT_SUBSTRING: 1}


# ---------------------------------------------------------------------------
# Adversarial regression: high strip rate → ExtractionLowGrounding
# Passes immediately — ExtractionLowGrounding is already implemented.
# Regression class: verifier-strictness reduction (W2_ARCHITECTURE.md §5.5)
# If someone raises the threshold from 30% to 50%, this test fires.
# ---------------------------------------------------------------------------


def test_high_strip_rate_lab_raises_extraction_low_grounding() -> None:
    """5 results, 2 fail grounding (40% > 30%) → ExtractionLowGrounding raised.

    Adversarial regression guard: any weakening of the 30% threshold
    (e.g., raising it to 50%) would cause this test to stop raising,
    detecting the regression immediately (W2_ARCHITECTURE.md §5.5 — R2:
    verifier-strictness reduction regression class).

    PHI discipline: error message must not contain field names or values.
    """
    from agent.extractors.haiku_extraction import (
        ExtractionLowGrounding,
        parse_lab_report,
    )

    blocks = [
        _make_block("block_0", "Hemoglobin A1c 7.8 %"),
        _make_block("block_1", "Glucose 142 mg/dL"),
        _make_block("block_2", "eGFR 62 mL/min"),
        _make_block("block_3", "Unrelated patient info only"),
        _make_block("block_4", "Clinical notes section — no analytes"),
    ]
    doc = _make_doc(blocks)

    response = json.dumps({
        "results": [
            {
                "test_name": "Hemoglobin A1c",
                "value": 7.8, "unit": "%",
                "reference_range": None, "abnormal": None, "collection_date": None,
                "source_block_id": "block_0", "confidence": 0.95,
            },
            {
                "test_name": "Glucose",
                "value": 142.0, "unit": "mg/dL",
                "reference_range": None, "abnormal": None, "collection_date": None,
                "source_block_id": "block_1", "confidence": 0.90,
            },
            {
                "test_name": "eGFR",
                "value": 62.0, "unit": "mL/min",
                "reference_range": None, "abnormal": None, "collection_date": None,
                "source_block_id": "block_2", "confidence": 0.88,
            },
            {
                # NOT in block_3 — fails grounding (1/5 so far)
                "test_name": "Phantom Analyte X",
                "value": 55.0, "unit": "units",
                "reference_range": None, "abnormal": None, "collection_date": None,
                "source_block_id": "block_3", "confidence": 0.50,
            },
            {
                # NOT in block_4 — fails grounding (2/5 = 40% > threshold)
                "test_name": "Phantom Analyte Y",
                "value": 22.0, "unit": "units",
                "reference_range": None, "abnormal": None, "collection_date": None,
                "source_block_id": "block_4", "confidence": 0.50,
            },
        ]
    })

    with pytest.raises(ExtractionLowGrounding) as exc_info:
        parse_lab_report(
            haiku_json_text=response,
            doc=doc,
            patient_id=_PID,
            doc_ref_id=_DOC_REF_ID,
            extraction_model=_MODEL,
        )

    error_msg = str(exc_info.value)
    assert "extraction_low_grounding" in error_msg
    # PHI discipline: field names and values must not appear in the error
    assert "Phantom Analyte X" not in error_msg
    assert "Phantom Analyte Y" not in error_msg


# ---------------------------------------------------------------------------
# Test 2: strip-rate bookkeeping — IntakeForm
# (depends on parse_intake_form → tuple[IntakeForm, ExtractionStats])
# ---------------------------------------------------------------------------


class TestStripRateIntakeForm:
    """Verify ExtractionStats counts from parse_intake_form.

    Field verification in parse_intake_form counts:
      demographics.name     → 1 field
      chief_concern         → 1 field (when non-null)
      each medication.name  → 1 field each
      each allergy.substance → 1 field each
      each fh.condition     → 1 field each

    Fixture: 7 verifiable fields total.
    1 medication fails grounding → stripped_fields=1, strip_rate≈1/7.
    """

    def _build_doc_and_response(self) -> tuple[DoclingDoc, str]:
        blocks = [
            _make_block("block_0", "Patient Information: Synthetic, Test B"),
            _make_block("block_1", "Chief Concern: Follow-up for hypertension management"),
            _make_block("block_2", "Medications: Metformin 1000mg BID"),
            # block_3 has UNRELATED text — Lisinopril NOT mentioned here
            _make_block("block_3", "Insurance and billing information section"),
            _make_block("block_4", "Allergies: Penicillin - Rash"),
            _make_block("block_5", "Allergies: Sulfa - Hives"),
            _make_block("block_6", "Family History: Father - Hypertension"),
        ]
        doc = _make_doc(blocks)

        # Medications:
        #   "Metformin" IS in block_2  → PASS
        #   "Lisinopril" is NOT in block_3 → FAIL (stripped)
        response = json.dumps({
            "demographics": {
                "name": "Synthetic, Test B",    # in block_0 → PASS
                "dob": "1965-03-15",
                "sex": "F",
                "source_block_id": "block_0",
            },
            "chief_concern": "Follow-up for hypertension management",   # in block_1 → PASS
            "chief_concern_block_id": "block_1",
            "current_medications": [
                {
                    "name": "Metformin",        # in block_2 → PASS
                    "dose": "1000mg",
                    "frequency": "BID",
                    "source_block_id": "block_2",
                },
                {
                    "name": "Lisinopril",       # NOT in block_3 → FAIL
                    "dose": "10mg",
                    "frequency": "QD",
                    "source_block_id": "block_3",
                },
            ],
            "allergies": [
                {
                    "substance": "Penicillin",  # in block_4 → PASS
                    "reaction": "Rash",
                    "severity": "Moderate",
                    "source_block_id": "block_4",
                },
                {
                    "substance": "Sulfa",       # in block_5 → PASS
                    "reaction": "Hives",
                    "severity": "Mild",
                    "source_block_id": "block_5",
                },
            ],
            "family_history": [
                {
                    "condition": "Hypertension",    # in block_6 → PASS
                    "relation": "Father",
                    "source_block_id": "block_6",
                }
            ],
            "source_citations": {
                "demographics": "block_0",
                "chief_concern": "block_1",
            },
            "confidence": 0.87,
        })
        return doc, response

    def test_stats_counts(self) -> None:
        """7 verifiable fields, 1 fails → total=7, stripped=1, rate≈1/7."""
        from agent.extractors.haiku_extraction import parse_intake_form

        doc, response = self._build_doc_and_response()
        # New signature: returns (IntakeForm, ExtractionStats)
        result = parse_intake_form(
            haiku_json_text=response,
            doc=doc,
            patient_id=_PID,
            doc_ref_id=_DOC_REF_ID,
            extraction_model=_MODEL,
        )
        form, stats = result

        assert isinstance(form, IntakeForm)
        assert stats.total_fields == 7
        assert stats.stripped_fields == 1
        # strip_rate ≈ 1/7
        assert abs(stats.strip_rate - (1 / 7)) < 1e-9

    def test_medication_stripped_from_form(self) -> None:
        """The failed medication must not appear in form.current_medications."""
        from agent.extractors.haiku_extraction import parse_intake_form

        doc, response = self._build_doc_and_response()
        form, _stats = parse_intake_form(
            haiku_json_text=response,
            doc=doc,
            patient_id=_PID,
            doc_ref_id=_DOC_REF_ID,
            extraction_model=_MODEL,
        )
        # Only Metformin passed grounding; Lisinopril was stripped
        assert len(form.current_medications) == 1
        # Structural assertion: count of passing medications; no raw name comparison

    def test_stats_reason_counts(self) -> None:
        """Stripped medication reason must be value_not_substring_of_block."""
        from agent.extractors.haiku_extraction import (
            REASON_VALUE_NOT_SUBSTRING,
            parse_intake_form,
        )

        doc, response = self._build_doc_and_response()
        _form, stats = parse_intake_form(
            haiku_json_text=response,
            doc=doc,
            patient_id=_PID,
            doc_ref_id=_DOC_REF_ID,
            extraction_model=_MODEL,
        )
        assert stats.verifier_reason_counts == {REASON_VALUE_NOT_SUBSTRING: 1}


# ---------------------------------------------------------------------------
# Test 3: template_id resolution (parametrized) — passes immediately
# ---------------------------------------------------------------------------


class TestTemplateIdResolution:
    """resolve_template_id maps known filenames and returns 'unknown' for others."""

    @pytest.mark.parametrize(
        "filename, expected",
        [
            # All 8 known entries from TEMPLATE_BY_FILENAME
            ("p01-chen-lipid-panel.pdf",  "labcorp_lipid_v1"),
            ("p02-whitaker-cbc.pdf",      "labcorp_cbc_v1"),
            ("p03-reyes-hba1c.png",       "quest_hba1c_v1"),
            ("p04-kowalski-cmp.pdf",      "labcorp_cmp_v1"),
            ("p01-chen-intake-typed.pdf", "intake_form_v1"),
            ("p02-whitaker-intake.pdf",   "intake_form_v1"),
            ("p03-reyes-intake.png",      "intake_form_v1"),
            ("p04-kowalski-intake.png",   "intake_form_v1"),
            # Unknown filenames → "unknown"
            ("random_scan_001.pdf",       "unknown"),
            ("mystery_document.pdf",      "unknown"),
            # None input → "unknown"
            (None,                        "unknown"),
            # Full path — basename matching
            (
                "/uploads/p01-chen-lipid-panel.pdf",
                "labcorp_lipid_v1",
            ),
        ],
    )
    def test_resolve(self, filename: str | None, expected: str) -> None:
        from agent.extractors.template_id import resolve_template_id

        result = resolve_template_id(filename)
        assert result == expected, (
            f"resolve_template_id({filename!r}) returned {result!r}, "
            f"expected {expected!r}"
        )

    def test_template_by_filename_has_eight_entries(self) -> None:
        """Guard: TEMPLATE_BY_FILENAME must have exactly 8 canonical entries.

        This is a meta-test: if the dict grows without this test being updated,
        the CI rubric coverage falls behind and a regression can slip through
        undetected (W2_ARCHITECTURE.md §5.5).
        """
        from agent.extractors.template_id import TEMPLATE_BY_FILENAME

        assert len(TEMPLATE_BY_FILENAME) == 8, (
            f"TEMPLATE_BY_FILENAME has {len(TEMPLATE_BY_FILENAME)} entries; "
            "expected 8. Update this test if entries are intentionally added/removed."
        )

    def test_all_values_are_non_empty_strings(self) -> None:
        """All template_id values must be non-empty strings (no None, no empty)."""
        from agent.extractors.template_id import TEMPLATE_BY_FILENAME

        for filename, template_id in TEMPLATE_BY_FILENAME.items():
            assert isinstance(template_id, str) and template_id, (
                f"template_id for {filename!r} is empty or not a string"
            )

    def test_windows_path_separator_resolved(self) -> None:
        """Backslash path separators (Windows uploads) also resolve correctly."""
        from agent.extractors.template_id import resolve_template_id

        # Synthetic Windows-style path — no patient-identifying info
        result = resolve_template_id("C:\\uploads\\p01-chen-lipid-panel.pdf")
        assert result == "labcorp_lipid_v1"

    def test_empty_string_returns_unknown(self) -> None:
        from agent.extractors.template_id import resolve_template_id

        assert resolve_template_id("") == "unknown"


# ---------------------------------------------------------------------------
# Test 4: Langfuse span attributes emitted by _run_haiku_extraction
# (depends on agent-rag adding `filename` param and all 10 P1 span keys)
# ---------------------------------------------------------------------------


class TestLangfuseSpanAttributes:
    """_run_haiku_extraction populates all 10 P1 metadata keys on the span.

    Previously a strict-xfail until `filename` param + the 10 P1 span keys
    landed; both are in place as of P1. Patch target reflects how the imports
    happen inside the function body:

      * `from agent.llm_client import build_llm_client` (patch the source
        module — `agent.llm_client.build_llm_client` — not `agent.extractors`)
      * `from langfuse import get_client` (source module: `langfuse.get_client`)
    """

    # All 10 required P1 keys per W2_ARCHITECTURE.md §5.1
    _REQUIRED_SPAN_KEYS: frozenset[str] = frozenset({
        "total_fields",
        "stripped_fields",
        "strip_rate",
        "attempt_n",
        "prompt_variant",
        "model",
        "triggered_by",
        "template_id",
        "cost_usd",
        "verifier_reason_counts",
    })

    def _build_minimal_doc(self) -> DoclingDoc:
        """One block containing a grounded lab value."""
        return _make_doc([
            _make_block("block_0", "Hemoglobin A1c 7.8 %"),
        ])

    def _build_minimal_lab_response(self) -> str:
        """One result that grounds correctly against block_0."""
        return json.dumps({
            "results": [
                {
                    "test_name": "Hemoglobin A1c",
                    "value": 7.8,
                    "unit": "%",
                    "reference_range": None,
                    "abnormal": None,
                    "collection_date": None,
                    "source_block_id": "block_0",
                    "confidence": 0.95,
                }
            ]
        })

    @pytest.mark.asyncio
    async def test_span_update_called_with_all_p1_keys(self) -> None:
        """All 10 P1 keys must appear in the metadata dict passed to span.update()."""
        from agent.extractors import _run_haiku_extraction

        doc = self._build_minimal_doc()
        llm_response_text = self._build_minimal_lab_response()

        # Build a mock Anthropic SDK response
        mock_content_block = MagicMock()
        mock_content_block.type = "text"
        mock_content_block.text = llm_response_text

        mock_usage = MagicMock()
        mock_usage.input_tokens = 500
        mock_usage.output_tokens = 120
        mock_usage.cache_read_input_tokens = 0
        mock_usage.cache_creation_input_tokens = 0

        mock_response = MagicMock()
        mock_response.content = [mock_content_block]
        mock_response.usage = mock_usage
        mock_response.stop_reason = "end_turn"

        mock_llm_client = MagicMock()
        mock_llm_client.create = AsyncMock(return_value=mock_response)

        mock_span_obj = MagicMock()
        mock_lf_client = MagicMock()
        mock_lf_client.start_observation = MagicMock(return_value=mock_span_obj)

        # Langfuse is imported inside _run_haiku_extraction:
        #   from langfuse import get_client
        # Patch at the langfuse module level since it's imported inside the fn.
        with (
            patch("langfuse.get_client", return_value=mock_lf_client),
            patch("agent.llm_client.build_llm_client", return_value=mock_llm_client),
        ):
            await _run_haiku_extraction(
                doc=doc,
                doc_type="lab_pdf",
                patient_id=_PID,
                doc_ref_id=_DOC_REF_ID,
                filename="p01-chen-lipid-panel.pdf",   # new P1 param
            )

        # Span lifecycle
        assert mock_span_obj.update.called, "span.update() was never called"
        assert mock_span_obj.end.called, "span.end() was never called"

        # Extract the metadata dict from the last update() call
        update_call_kwargs: dict[str, Any] = mock_span_obj.update.call_args.kwargs
        metadata: dict[str, Any] = update_call_kwargs.get("metadata", {})

        # All 10 P1 keys must be present
        missing_keys = self._REQUIRED_SPAN_KEYS - metadata.keys()
        assert not missing_keys, (
            f"Langfuse span.update() metadata is missing P1 keys: {missing_keys!r}"
        )

    @pytest.mark.asyncio
    async def test_span_metadata_values(self) -> None:
        """Verify concrete values of the deterministic P1 span fields."""
        from agent.extractors import _run_haiku_extraction

        doc = self._build_minimal_doc()
        llm_response_text = self._build_minimal_lab_response()

        mock_content_block = MagicMock()
        mock_content_block.type = "text"
        mock_content_block.text = llm_response_text

        mock_usage = MagicMock()
        mock_usage.input_tokens = 1_000
        mock_usage.output_tokens = 200
        mock_usage.cache_read_input_tokens = 0
        mock_usage.cache_creation_input_tokens = 0

        mock_response = MagicMock()
        mock_response.content = [mock_content_block]
        mock_response.usage = mock_usage
        mock_response.stop_reason = "end_turn"

        mock_llm_client = MagicMock()
        mock_llm_client.create = AsyncMock(return_value=mock_response)

        mock_span_obj = MagicMock()
        mock_lf_client = MagicMock()
        mock_lf_client.start_observation = MagicMock(return_value=mock_span_obj)

        with (
            patch("langfuse.get_client", return_value=mock_lf_client),
            patch("agent.llm_client.build_llm_client", return_value=mock_llm_client),
        ):
            await _run_haiku_extraction(
                doc=doc,
                doc_type="lab_pdf",
                patient_id=_PID,
                doc_ref_id=_DOC_REF_ID,
                filename="p01-chen-lipid-panel.pdf",
            )

        update_call_kwargs = mock_span_obj.update.call_args.kwargs
        metadata = update_call_kwargs.get("metadata", {})

        # Deterministic assertions (no raw extracted values)
        assert metadata["template_id"] == "labcorp_lipid_v1"
        assert metadata["attempt_n"] == 1
        assert metadata["prompt_variant"] == "default"
        assert metadata["triggered_by"] == "initial"
        assert metadata["total_fields"] == 1
        assert metadata["stripped_fields"] == 0
        assert metadata["strip_rate"] == 0.0
        # cost_usd must be a float (model is known — claude-haiku-4-5)
        assert isinstance(metadata["cost_usd"], float), (
            f"cost_usd should be float, got {type(metadata['cost_usd'])}"
        )
        assert metadata["cost_usd"] > 0.0


# ---------------------------------------------------------------------------
# Test 5: compute_cost_usd — known model returns correct float
# (passes immediately — cost.py is already implemented)
# ---------------------------------------------------------------------------


class TestComputeCostUsdKnownModel:
    """compute_cost_usd returns correct USD for known models."""

    def test_haiku_cost_arithmetic(self) -> None:
        from agent.extractors.cost import MODEL_PRICING, compute_cost_usd

        model = "claude-haiku-4-5"
        input_tokens = 1_000
        output_tokens = 200

        result = compute_cost_usd(model, input_tokens, output_tokens)

        assert result is not None, "Expected float, got None for known model"

        pricing = MODEL_PRICING[model]
        expected = (
            input_tokens * pricing["input_per_million"] / 1_000_000
            + output_tokens * pricing["output_per_million"] / 1_000_000
        )
        assert abs(result - expected) < 1e-9, (
            f"compute_cost_usd({model!r}, {input_tokens}, {output_tokens}) "
            f"returned {result}, expected {expected}"
        )

    def test_sonnet_cost_arithmetic(self) -> None:
        from agent.extractors.cost import MODEL_PRICING, compute_cost_usd

        model = "claude-sonnet-4-6"
        input_tokens = 2_000
        output_tokens = 500

        result = compute_cost_usd(model, input_tokens, output_tokens)

        assert result is not None
        pricing = MODEL_PRICING[model]
        expected = (
            input_tokens * pricing["input_per_million"] / 1_000_000
            + output_tokens * pricing["output_per_million"] / 1_000_000
        )
        assert abs(result - expected) < 1e-9

    def test_zero_tokens_returns_zero(self) -> None:
        from agent.extractors.cost import compute_cost_usd

        result = compute_cost_usd("claude-haiku-4-5", 0, 0)
        assert result == 0.0

    def test_model_pricing_dict_has_two_entries(self) -> None:
        """Guard: MODEL_PRICING must cover both haiku and sonnet models."""
        from agent.extractors.cost import MODEL_PRICING

        assert "claude-haiku-4-5" in MODEL_PRICING
        assert "claude-sonnet-4-6" in MODEL_PRICING

    def test_pricing_keys_present(self) -> None:
        """Each model entry must have input_per_million and output_per_million."""
        from agent.extractors.cost import MODEL_PRICING

        for model_name, pricing in MODEL_PRICING.items():
            assert "input_per_million" in pricing, (
                f"MODEL_PRICING[{model_name!r}] missing 'input_per_million'"
            )
            assert "output_per_million" in pricing, (
                f"MODEL_PRICING[{model_name!r}] missing 'output_per_million'"
            )
            assert pricing["input_per_million"] > 0
            assert pricing["output_per_million"] > 0


# ---------------------------------------------------------------------------
# Test 6: compute_cost_usd — unknown model returns None
# (passes immediately)
# ---------------------------------------------------------------------------


class TestComputeCostUsdUnknownModel:
    """compute_cost_usd returns None for unrecognized model identifiers."""

    def test_unknown_model_returns_none(self) -> None:
        from agent.extractors.cost import compute_cost_usd

        result = compute_cost_usd("gpt-unknown-model", 500, 100)
        assert result is None

    def test_empty_string_returns_none(self) -> None:
        from agent.extractors.cost import compute_cost_usd

        assert compute_cost_usd("", 100, 50) is None

    def test_partial_model_name_returns_none(self) -> None:
        """Substring of a valid model name is NOT a valid model."""
        from agent.extractors.cost import compute_cost_usd

        # "haiku" alone is not a key in MODEL_PRICING
        assert compute_cost_usd("haiku", 100, 50) is None


# ---------------------------------------------------------------------------
# Test 7: verify_field reason-code return value
# (partially passes now — current verify_field returns bool, not tuple)
# ---------------------------------------------------------------------------


class TestVerifyFieldReturnsReason:
    """verify_field returns (bool, reason_code | None).

    Previously a strict-xfail until the signature change landed; the change
    is in place as of P1.
    """

    def test_passes_when_value_in_block(self) -> None:
        from agent.extractors.haiku_extraction import verify_field

        block_index = {
            "block_0": "Hemoglobin A1c 7.8 %",
            "block_1": "Glucose 142 mg/dL",
        }
        ok, reason = verify_field("Hemoglobin A1c", "block_0", block_index)
        assert ok is True
        assert reason is None

    def test_fails_value_not_substring(self) -> None:
        from agent.extractors.haiku_extraction import (
            REASON_VALUE_NOT_SUBSTRING,
            verify_field,
        )

        block_index = {
            "block_0": "Hemoglobin A1c 7.8 %",
        }
        ok, reason = verify_field("Invented Value", "block_0", block_index)
        assert ok is False
        assert reason == REASON_VALUE_NOT_SUBSTRING

    def test_fails_block_not_found(self) -> None:
        from agent.extractors.haiku_extraction import (
            REASON_BLOCK_NOT_FOUND,
            verify_field,
        )

        block_index = {
            "block_0": "Hemoglobin A1c 7.8 %",
        }
        ok, reason = verify_field("Anything", "block_99", block_index)
        assert ok is False
        assert reason == REASON_BLOCK_NOT_FOUND

    def test_fails_none_block_id(self) -> None:
        from agent.extractors.haiku_extraction import (
            REASON_BLOCK_NOT_FOUND,
            verify_field,
        )

        block_index = {
            "block_0": "Hemoglobin A1c 7.8 %",
        }
        ok, reason = verify_field("Anything", None, block_index)
        assert ok is False
        assert reason == REASON_BLOCK_NOT_FOUND

    def test_case_insensitive_match_returns_no_reason(self) -> None:
        """Normalized match still passes; reason must be None."""
        from agent.extractors.haiku_extraction import verify_field

        block_index = {"block_0": "Hemoglobin A1c 7.8 %"}
        ok, reason = verify_field("hemoglobin a1c", "block_0", block_index)
        assert ok is True
        assert reason is None


# ---------------------------------------------------------------------------
# Meta-test: ExtractionStats dataclass contract
# (passes immediately — ExtractionStats already exists)
# ---------------------------------------------------------------------------


class TestExtractionStatsDataclass:
    """Structural/contract tests for ExtractionStats.

    These guard the eval engine itself (W2_ARCHITECTURE.md §5.5):
    if ExtractionStats is accidentally broken, these fire before the
    integration tests even get a chance to hide the regression.
    """

    def test_zero_total_strip_rate_is_zero(self) -> None:
        from agent.extractors.haiku_extraction import ExtractionStats

        stats = ExtractionStats()
        assert stats.strip_rate == 0.0

    def test_strip_rate_property(self) -> None:
        from agent.extractors.haiku_extraction import ExtractionStats

        stats = ExtractionStats(total_fields=10, stripped_fields=3)
        assert abs(stats.strip_rate - 0.3) < 1e-9

    def test_record_failure_increments_counts(self) -> None:
        from agent.extractors.haiku_extraction import (
            REASON_BLOCK_NOT_FOUND,
            REASON_VALUE_NOT_SUBSTRING,
            ExtractionStats,
        )

        stats = ExtractionStats(total_fields=5)
        stats.record_failure(REASON_VALUE_NOT_SUBSTRING)
        stats.record_failure(REASON_BLOCK_NOT_FOUND)
        stats.record_failure(REASON_VALUE_NOT_SUBSTRING)

        assert stats.stripped_fields == 3
        assert stats.verifier_reason_counts[REASON_VALUE_NOT_SUBSTRING] == 2
        assert stats.verifier_reason_counts[REASON_BLOCK_NOT_FOUND] == 1

    def test_reason_constants_are_static_strings(self) -> None:
        """Reason codes must be plain strings, not interpolated from LLM output."""
        from agent.extractors.haiku_extraction import (
            REASON_BLOCK_NOT_FOUND,
            REASON_VALUE_NOT_SUBSTRING,
        )

        assert isinstance(REASON_VALUE_NOT_SUBSTRING, str)
        assert isinstance(REASON_BLOCK_NOT_FOUND, str)
        # Concrete values for CI rubric snapshot
        assert REASON_VALUE_NOT_SUBSTRING == "value_not_substring_of_block"
        assert REASON_BLOCK_NOT_FOUND == "block_not_found"
