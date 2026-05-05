"""Integration tests for POST /attach_and_extract using FastAPI TestClient.

All tests mock attach_and_extract_async so they run without Docling, Anthropic,
or any other live service — same fixture-mode discipline as the /chat endpoint
tests in test_chat_endpoint.py.

PHI discipline:
- Tests use sentinel patient_id 999101 (within 999100-999199 per §8.2).
- File bytes are minimal synthetic content — no real patient data.
- Error messages are asserted NOT to contain doc text or PHI tokens.

HMAC discipline:
- Helpers compute real SHA-256 HMACs matching the locked contract.
- Tests that verify signature rejection use structurally-wrong signatures
  (not just the wrong value) to confirm the verification path, not just
  a string-equality check.
"""

from __future__ import annotations

import hashlib
import hmac as _hmac
import json
import time
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from agent.config import get_settings
from agent.document_schemas import (
    BBox,
    DoclingBlock,
    DoclingDoc,
    ExtractMeta,
    IntakeForm,
    LabReport,
    LabResult,
    Demographics,
)
from agent.main import app

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SENTINEL_PID = 999_101
_DOC_REF_ID = "docref-test-lab-001"
_DOC_TYPE_LAB = "lab_pdf"
_USER_ID = 7
_HMAC_INJECTION_TOKEN = "Hemoglobin"  # recognizable doc token for PHI leak test

# Minimal synthetic PDF bytes (just the header; not a real PDF for layout
# engine purposes, but sufficient for multipart upload + hash computation).
_FAKE_PDF_BYTES = b"%PDF-1.4 synthetic-test-content"


# ---------------------------------------------------------------------------
# HMAC helpers
# ---------------------------------------------------------------------------


def _sign_attach(
    *,
    user_id: int = _USER_ID,
    patient_id: int = _SENTINEL_PID,
    doc_ref_id: str = _DOC_REF_ID,
    doc_type: str = _DOC_TYPE_LAB,
    timestamp: int,
    file_bytes: bytes = _FAKE_PDF_BYTES,
    secret: str | None = None,
) -> str:
    """Compute a valid HMAC for the locked /attach_and_extract contract.

    Payload: f"{user_id}|{patient_id}|{doc_ref_id}|{doc_type}|{timestamp}|{file_sha256_hex}"
    """
    if secret is None:
        secret = get_settings().openemr_hmac_secret
    file_sha256_hex = hashlib.sha256(file_bytes).hexdigest()
    payload = f"{user_id}|{patient_id}|{doc_ref_id}|{doc_type}|{timestamp}|{file_sha256_hex}"
    return _hmac.new(
        secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _make_multipart_request(
    client: TestClient,
    *,
    user_id: int = _USER_ID,
    patient_id: int = _SENTINEL_PID,
    doc_ref_id: str = _DOC_REF_ID,
    doc_type: str = _DOC_TYPE_LAB,
    timestamp: int | None = None,
    hmac_override: str | None = None,
    file_bytes: bytes = _FAKE_PDF_BYTES,
    secret: str | None = None,
) -> Any:
    """Post a multipart request to /attach_and_extract.

    Computes a valid HMAC unless hmac_override is given (used by negative tests).
    timestamp defaults to now() so replay checks pass by default.
    """
    if timestamp is None:
        timestamp = int(time.time())
    sig = (
        hmac_override
        if hmac_override is not None
        else _sign_attach(
            user_id=user_id,
            patient_id=patient_id,
            doc_ref_id=doc_ref_id,
            doc_type=doc_type,
            timestamp=timestamp,
            file_bytes=file_bytes,
            secret=secret,
        )
    )
    return client.post(
        "/attach_and_extract",
        data={
            "patient_id": str(patient_id),
            "doc_ref_id": doc_ref_id,
            "doc_type": doc_type,
        },
        files={"file": ("test.pdf", file_bytes, "application/pdf")},
        headers={
            "X-OpenEMR-User-Id": str(user_id),
            "X-OpenEMR-Timestamp": str(timestamp),
            "X-OpenEMR-HMAC": sig,
        },
    )


# ---------------------------------------------------------------------------
# Synthetic extraction fixtures (returned by the mock)
# ---------------------------------------------------------------------------


def _make_lab_report_fixture() -> LabReport:
    """Minimal LabReport that passes Pydantic validation and has non-empty results."""
    return LabReport(
        patient_id=_SENTINEL_PID,
        document_reference_id=_DOC_REF_ID,
        results=[
            LabResult(
                test_name="Hemoglobin A1c",
                value=7.2,
                unit="%",
                reference_range="<5.7",
                abnormal=True,
                collection_date=None,
                source_block_id="block_0",
                confidence=0.92,
            ),
            LabResult(
                test_name="Fasting Glucose",
                value=128.0,
                unit="mg/dL",
                reference_range="70-99",
                abnormal=True,
                collection_date=None,
                source_block_id="block_1",
                confidence=0.88,
            ),
        ],
        extraction_metadata=ExtractMeta(
            docling_version="2.0.0",
            extraction_model="claude-haiku-4-5",
            page_count=1,
            extraction_confidence_avg=0.90,
        ),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_attach_happy_path_lab_pdf() -> None:
    """Valid multipart + valid HMAC → 200, status='ok', extraction non-empty.

    attach_and_extract_async is mocked to return a synthetic LabReport so
    the test runs without Docling or Anthropic.
    """
    client = TestClient(app)
    fixture = _make_lab_report_fixture()

    with patch(
        "agent.main.attach_and_extract_async",
        new_callable=AsyncMock,
        return_value=fixture,
    ):
        resp = _make_multipart_request(client)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ok"
    assert body["doc_ref_id"] == _DOC_REF_ID
    assert body["doc_type"] == _DOC_TYPE_LAB
    assert "request_id" in body
    assert body["n_blocks"] > 0, "Expected at least one extracted block/result"
    assert body["n_pages"] >= 1
    assert isinstance(body["extraction_confidence_avg"], float)
    # The extraction dict must have results (LabReport shape).
    extraction = body["extraction"]
    assert "results" in extraction
    assert len(extraction["results"]) > 0
    first = extraction["results"][0]
    assert "test_name" in first


def test_attach_invalid_hmac_returns_401() -> None:
    """Wrong HMAC → 401, status='refused', reason='invalid_signature'.

    Confirms that a structurally-valid but wrong signature is rejected and
    does NOT echo the HMAC value in the response body.
    """
    client = TestClient(app)
    wrong_sig = "deadbeef" * 8  # 64 hex chars, wrong value

    with patch("agent.main.attach_and_extract_async", new_callable=AsyncMock):
        resp = _make_multipart_request(client, hmac_override=wrong_sig)

    assert resp.status_code == 401, resp.text
    body = resp.json()
    assert body["status"] == "refused"
    assert body["reason"] == "invalid_signature"
    # Security: the HMAC value must NOT appear in the response.
    resp_text = resp.text
    assert wrong_sig not in resp_text


def test_attach_stale_timestamp_returns_401() -> None:
    """Timestamp >300s old → 401, reason='stale_timestamp'.

    Replay-window check fires before HMAC verification so even a correctly
    signed stale request is rejected.
    """
    client = TestClient(app)
    stale_ts = int(time.time()) - 400  # 400s ago, outside the 300s window
    sig = _sign_attach(timestamp=stale_ts)

    with patch("agent.main.attach_and_extract_async", new_callable=AsyncMock):
        resp = _make_multipart_request(client, timestamp=stale_ts, hmac_override=sig)

    assert resp.status_code == 401, resp.text
    body = resp.json()
    assert body["status"] == "refused"
    assert body["reason"] == "stale_timestamp"


def test_attach_out_of_sentinel_pid_returns_400() -> None:
    """patient_id outside sentinel range → 400, reason='patient_id_out_of_sentinel_range'.

    Error message must NOT echo the offending patient_id value
    (Stage-2 fix #2 pattern).
    """
    client = TestClient(app)
    bad_pid = 12345  # below sentinel minimum 999100

    # Compute a valid HMAC for the bad pid so the rejection is from the
    # sentinel check, not the signature check.
    timestamp = int(time.time())
    sig = _sign_attach(patient_id=bad_pid, timestamp=timestamp)

    with patch("agent.main.attach_and_extract_async", new_callable=AsyncMock):
        resp = _make_multipart_request(
            client,
            patient_id=bad_pid,
            timestamp=timestamp,
            hmac_override=sig,
        )

    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["status"] == "refused"
    assert body["reason"] == "patient_id_out_of_sentinel_range"
    # Security: the offending pid value must NOT appear in the response body.
    assert str(bad_pid) not in resp.text


def test_attach_unsupported_doc_type_returns_400() -> None:
    """doc_type outside allowlist → 400, reason='unsupported_doc_type'."""
    client = TestClient(app)
    bad_type = "receipt"

    timestamp = int(time.time())
    sig = _sign_attach(doc_type=bad_type, timestamp=timestamp)

    with patch("agent.main.attach_and_extract_async", new_callable=AsyncMock):
        resp = _make_multipart_request(
            client,
            doc_type=bad_type,
            timestamp=timestamp,
            hmac_override=sig,
        )

    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["status"] == "refused"
    assert body["reason"] == "unsupported_doc_type"


def test_attach_invalid_json_in_extraction_does_not_leak_pdf_text() -> None:
    """If attach_and_extract_async raises json.JSONDecodeError containing
    recognizable doc tokens (e.g. 'Hemoglobin'), the response must not
    contain that token or a raw stack trace.

    Validates Stage-2 fix #1 pattern: raise from None + mask_observability_patterns
    ensures the chain is broken and PHI does not surface in the error response.
    """
    import json as _json_mod

    client = TestClient(app)

    # Inject a JSONDecodeError whose .doc field contains a recognizable token.
    # json.JSONDecodeError(msg, doc, pos) — .doc is the 'document' that failed.
    injected_exc = _json_mod.JSONDecodeError(
        f"Unexpected token near '{_HMAC_INJECTION_TOKEN} 7.2%'",
        f"{{bad json with {_HMAC_INJECTION_TOKEN} value}}",
        0,
    )

    with patch(
        "agent.main.attach_and_extract_async",
        new_callable=AsyncMock,
        side_effect=injected_exc,
    ):
        resp = _make_multipart_request(client)

    # Endpoint must return an error response, not crash.
    assert resp.status_code in (500, 400), resp.text
    body = resp.json()
    assert body["status"] == "error"

    # Core assertion: the recognizable PHI token must not appear anywhere in
    # the response text, confirming the raise-from-None chain-break works.
    assert _HMAC_INJECTION_TOKEN not in resp.text, (
        f"PHI token '{_HMAC_INJECTION_TOKEN}' leaked into the response. "
        "Verify the raise-from-None + mask_observability_patterns pattern "
        "in the /attach_and_extract exception handler."
    )
    # Stack traces should not appear either.
    assert "Traceback" not in resp.text
    assert "File " not in resp.text
