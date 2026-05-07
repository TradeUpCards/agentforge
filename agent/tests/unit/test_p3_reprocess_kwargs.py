"""P3 Reprocess kwargs threading — quality gate tests.

Covers:
  - attach_and_extract_async accepts triggered_by kwarg
  - attach_and_extract_async accepts parent_extraction_id kwarg
  - Default triggered_by is "initial"
  - Default parent_extraction_id is None
  - Both kwargs flow through to _run_extraction_with_retry

PHI discipline (W2_ARCHITECTURE.md §8.2-8.3):
  - All sentinel patient_ids in [999100, 999199]
  - No raw document text or extracted values in assertions

W2_ARCHITECTURE.md references:
  §2.2  — two-stage pipeline
  §8.2  — sentinel patient_id range 999100-999199
  P3 brief §3 — kwarg threading through attach_and_extract_async
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from agent.document_schemas import (
    BBox,
    DoclingBlock,
    DoclingDoc,
    ExtractMeta,
    LabReport,
    LabResult,
)
from agent.extractors import attach_and_extract_async

# ---------------------------------------------------------------------------
# Sentinel constants
# ---------------------------------------------------------------------------

_PID = 999_100
_DOC_REF_ID = "DocumentReference/test-p3-kwargs-999100"
_MODEL = "claude-haiku-4-5"

# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------


def _make_bbox() -> BBox:
    return BBox(page=1, x0=72.0, y0=100.0, x1=400.0, y1=115.0)


def _make_block(block_id: str, text: str) -> DoclingBlock:
    return DoclingBlock(
        block_id=block_id,
        text=text,
        block_type="text",
        page=1,
        bbox=_make_bbox(),
    )


def _minimal_docling_doc() -> DoclingDoc:
    return DoclingDoc(
        document_reference_id=_DOC_REF_ID,
        page_count=1,
        blocks=[_make_block("blk_0", "Hemoglobin A1c 7.8 %")],
        docling_version="2.92.0",
    )


def _minimal_lab_report() -> LabReport:
    result = LabResult(
        test_name="Hemoglobin A1c",
        value=7.8,
        unit="%",
        source_block_id="blk_0",
        confidence=0.95,
    )
    meta = ExtractMeta(
        docling_version="2.92.0",
        extraction_model=_MODEL,
        page_count=1,
    )
    return LabReport(
        patient_id=_PID,
        document_reference_id=_DOC_REF_ID,
        results=[result],
        extraction_metadata=meta,
    )


# ---------------------------------------------------------------------------
# §1 — Signature inspection: kwargs are declared
# ---------------------------------------------------------------------------


class TestSignatureKwargs:
    def test_attach_and_extract_async_accepts_triggered_by_kwarg(self) -> None:
        """triggered_by is a keyword-only parameter on attach_and_extract_async."""
        sig = inspect.signature(attach_and_extract_async)
        assert "triggered_by" in sig.parameters, (
            "attach_and_extract_async must declare triggered_by as a keyword arg"
        )
        param = sig.parameters["triggered_by"]
        assert param.kind in (
            inspect.Parameter.KEYWORD_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        )

    def test_attach_and_extract_async_accepts_parent_extraction_id_kwarg(self) -> None:
        """parent_extraction_id is a keyword-only parameter on attach_and_extract_async."""
        sig = inspect.signature(attach_and_extract_async)
        assert "parent_extraction_id" in sig.parameters, (
            "attach_and_extract_async must declare parent_extraction_id as a keyword arg"
        )

    def test_default_triggered_by_is_initial(self) -> None:
        """triggered_by default must be 'initial'."""
        sig = inspect.signature(attach_and_extract_async)
        param = sig.parameters["triggered_by"]
        assert param.default == "initial", (
            f"triggered_by default must be 'initial', got {param.default!r}"
        )

    def test_default_parent_extraction_id_is_null(self) -> None:
        """parent_extraction_id default must be None."""
        sig = inspect.signature(attach_and_extract_async)
        param = sig.parameters["parent_extraction_id"]
        assert param.default is None, (
            f"parent_extraction_id default must be None, got {param.default!r}"
        )


# ---------------------------------------------------------------------------
# §2 — kwargs flow into _run_extraction_with_retry
# ---------------------------------------------------------------------------


class TestKwargsFlowIntoRunExtractionWithRetry:
    """Verify that triggered_by and parent_extraction_id are forwarded to
    _run_extraction_with_retry when attach_and_extract_async runs Stage 2.

    We mock _run_stage1_layout to return a synthetic DoclingDoc, and
    _run_extraction_with_retry to capture its kwargs without making LLM calls.
    """

    @pytest.mark.asyncio
    async def test_kwargs_flow_into_run_extraction_with_retry(self) -> None:
        """triggered_by='manual_retry' and parent_extraction_id=42 reach the retry wrapper."""
        mock_doc = _minimal_docling_doc()
        mock_result = _minimal_lab_report()

        mock_retry = AsyncMock(return_value=mock_result)

        with (
            patch("agent.extractors._run_stage1_layout", return_value=mock_doc),
            patch("agent.extractors._run_extraction_with_retry", mock_retry),
        ):
            await attach_and_extract_async(
                patient_id=_PID,
                doc_ref_id=_DOC_REF_ID,
                doc_type="lab_pdf",
                pdf_path=Path("/synthetic/test.pdf"),
                triggered_by="manual_retry",
                parent_extraction_id=42,
            )

        # _run_extraction_with_retry must have been called exactly once
        assert mock_retry.call_count == 1, (
            f"Expected _run_extraction_with_retry to be called once, "
            f"got {mock_retry.call_count}"
        )

        call_kwargs = mock_retry.call_args.kwargs
        assert call_kwargs.get("triggered_by") == "manual_retry", (
            f"triggered_by not forwarded: {call_kwargs.get('triggered_by')!r}"
        )
        assert call_kwargs.get("parent_extraction_id") == 42, (
            f"parent_extraction_id not forwarded: {call_kwargs.get('parent_extraction_id')!r}"
        )

    @pytest.mark.asyncio
    async def test_default_triggered_by_forwarded_as_initial(self) -> None:
        """When triggered_by is not specified, 'initial' is forwarded to retry wrapper."""
        mock_doc = _minimal_docling_doc()
        mock_result = _minimal_lab_report()

        mock_retry = AsyncMock(return_value=mock_result)

        with (
            patch("agent.extractors._run_stage1_layout", return_value=mock_doc),
            patch("agent.extractors._run_extraction_with_retry", mock_retry),
        ):
            await attach_and_extract_async(
                patient_id=_PID,
                doc_ref_id=_DOC_REF_ID,
                doc_type="lab_pdf",
                pdf_path=Path("/synthetic/test.pdf"),
                # triggered_by not specified — must default to "initial"
            )

        call_kwargs = mock_retry.call_args.kwargs
        assert call_kwargs.get("triggered_by") == "initial", (
            f"Default triggered_by must be forwarded as 'initial', "
            f"got {call_kwargs.get('triggered_by')!r}"
        )

    @pytest.mark.asyncio
    async def test_default_parent_extraction_id_forwarded_as_none(self) -> None:
        """When parent_extraction_id is not specified, None is forwarded."""
        mock_doc = _minimal_docling_doc()
        mock_result = _minimal_lab_report()

        mock_retry = AsyncMock(return_value=mock_result)

        with (
            patch("agent.extractors._run_stage1_layout", return_value=mock_doc),
            patch("agent.extractors._run_extraction_with_retry", mock_retry),
        ):
            await attach_and_extract_async(
                patient_id=_PID,
                doc_ref_id=_DOC_REF_ID,
                doc_type="lab_pdf",
                pdf_path=Path("/synthetic/test.pdf"),
            )

        call_kwargs = mock_retry.call_args.kwargs
        assert call_kwargs.get("parent_extraction_id") is None, (
            f"Default parent_extraction_id must be None, "
            f"got {call_kwargs.get('parent_extraction_id')!r}"
        )

    @pytest.mark.asyncio
    async def test_auto_retry_triggered_by_forwarded(self) -> None:
        """triggered_by='auto_retry' is forwarded correctly."""
        mock_doc = _minimal_docling_doc()
        mock_result = _minimal_lab_report()

        mock_retry = AsyncMock(return_value=mock_result)

        with (
            patch("agent.extractors._run_stage1_layout", return_value=mock_doc),
            patch("agent.extractors._run_extraction_with_retry", mock_retry),
        ):
            await attach_and_extract_async(
                patient_id=_PID,
                doc_ref_id=_DOC_REF_ID,
                doc_type="lab_pdf",
                pdf_path=Path("/synthetic/test.pdf"),
                triggered_by="auto_retry",
                parent_extraction_id=7,
            )

        call_kwargs = mock_retry.call_args.kwargs
        assert call_kwargs.get("triggered_by") == "auto_retry"
        assert call_kwargs.get("parent_extraction_id") == 7

    @pytest.mark.asyncio
    async def test_stage1_only_skips_retry_wrapper(self) -> None:
        """When stage1_only=True, _run_extraction_with_retry is never called."""
        mock_doc = _minimal_docling_doc()

        mock_retry = AsyncMock()

        with (
            patch("agent.extractors._run_stage1_layout", return_value=mock_doc),
            patch("agent.extractors._run_extraction_with_retry", mock_retry),
        ):
            result = await attach_and_extract_async(
                patient_id=_PID,
                doc_ref_id=_DOC_REF_ID,
                doc_type="lab_pdf",
                pdf_path=Path("/synthetic/test.pdf"),
                stage1_only=True,
                triggered_by="manual_retry",
                parent_extraction_id=99,
            )

        # Stage 1 only — no LLM call
        assert mock_retry.call_count == 0, (
            "stage1_only=True must skip _run_extraction_with_retry"
        )
        assert isinstance(result, DoclingDoc)
