"""Pytest configuration for the eval suite.

Key behaviors:
- Fixture-mode mock for attach_and_extract_async AND
  attach_and_extract_with_metadata_async (P3): when USE_FIXTURE_EXTRACTION=true,
  intercepts both functions and returns a pre-built LabReport or IntakeForm
  from agent/fixtures/patients/.  Lets extraction eval cases run in CI without
  Docling installed.
- Skip rules for live-LLM and live-DB cases are handled in test_eval_cases.py.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from agent.document_schemas import IntakeForm, LabReport

_FIXTURES_DIR = Path(__file__).parent.parent.parent / "fixtures" / "patients"

# ---------------------------------------------------------------------------
# Extraction fixture mock
# ---------------------------------------------------------------------------


def _load_extraction_fixture(patient_id: int, doc_type: str) -> LabReport | IntakeForm:
    """Load a pre-built extraction fixture for the given patient + doc_type.

    Fixture naming convention:
      {patient_id}_lab_pdf*.json  -> LabReport
      {patient_id}_intake_form*.json -> IntakeForm

    Raises FileNotFoundError if no matching fixture exists.
    """
    pattern = f"{patient_id}_{doc_type}*.json"
    matches = sorted(_FIXTURES_DIR.glob(pattern))
    if not matches:
        raise FileNotFoundError(
            f"No extraction fixture found for patient_id={patient_id}, "
            f"doc_type={doc_type!r}. Expected a file matching "
            f"agent/fixtures/patients/{pattern}"
        )
    data = json.loads(matches[0].read_text(encoding="utf-8"))
    if doc_type == "lab_pdf":
        return LabReport.model_validate(data)
    if doc_type == "intake_form":
        return IntakeForm.model_validate(data)
    raise ValueError(f"Unknown doc_type {doc_type!r}")


@pytest.fixture(autouse=True)
def mock_extraction_in_fixture_mode(monkeypatch: pytest.MonkeyPatch) -> Any:
    """When USE_FIXTURE_EXTRACTION=true, mock the extraction entry points.

    Intercepts the extraction pipeline (Docling + Haiku) and returns the
    pre-built fixture for the sentinel patient.  Lets smoke + full tier
    extraction cases run in CI without Docling or a live LLM.

    Two functions are patched:

      * ``attach_and_extract_async`` — pre-P3 entry point used by LangGraph
        workers and any caller that just wants the validated LabReport /
        IntakeForm / DoclingDoc.
      * ``attach_and_extract_with_metadata_async`` (P3) — used by
        ``/attach_and_extract`` so the HTTP response can include
        ``docling_blocks`` and ``field_verdicts``.  In fixture mode we
        return empty arrays for both metadata fields — eval cases assert
        on the parsed extraction shape, not on the metadata, so empties
        keep the response shape valid without needing to synthesize block
        inventories per fixture.
    """
    if os.environ.get("USE_FIXTURE_EXTRACTION", "").lower() != "true":
        yield  # Not in fixture-extraction mode — run real extraction
        return

    async def _mock_async(
        patient_id: int,
        doc_ref_id: str,
        doc_type: str,
        pdf_path: Any = None,
        *,
        stage1_only: bool = False,
        # P3 kwargs accepted (and ignored) so the mock signature matches
        # the production function — the eval runner doesn't pass these
        # today, but the new reprocess path may, and Python rejects unknown
        # kwargs at the call boundary.
        filename: str | None = None,
        triggered_by: str = "initial",
        parent_extraction_id: int | None = None,
    ) -> LabReport | IntakeForm:
        return _load_extraction_fixture(patient_id, doc_type)

    async def _mock_async_with_metadata(
        patient_id: int,
        doc_ref_id: str,
        doc_type: str,
        pdf_path: Any = None,
        *,
        stage1_only: bool = False,
        filename: str | None = None,
        triggered_by: str = "initial",
        parent_extraction_id: int | None = None,
    ) -> dict[str, Any]:
        # Return the P3 dict shape with empty metadata — eval cases don't
        # assert on docling_blocks or field_verdicts, so empties are safe.
        return {
            "result": _load_extraction_fixture(patient_id, doc_type),
            "docling_blocks": [],
            "field_verdicts": [],
        }

    # Patch both the module-level symbol AND the name already imported into
    # agent.main (from .extractors import attach_and_extract_async creates a
    # local binding that a single patch("agent.extractors.*") cannot intercept).
    with patch(
        "agent.extractors.attach_and_extract_async",
        side_effect=_mock_async,
    ), patch(
        "agent.main.attach_and_extract_async",
        side_effect=_mock_async,
    ), patch(
        "agent.extractors.attach_and_extract_with_metadata_async",
        side_effect=_mock_async_with_metadata,
    ), patch(
        "agent.main.attach_and_extract_with_metadata_async",
        side_effect=_mock_async_with_metadata,
    ):
        yield
