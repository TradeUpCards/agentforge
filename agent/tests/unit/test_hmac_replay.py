"""Unit tests for HMAC replay protection in agent/agent.py:verify_hmac.

Covers the timestamp-window leg of HMAC verification — captured request
bodies must not be replayable past the 30-second freshness window. Per
ai-security-review production blocker #3 (2026-05-02 audit pass).

Time injection (`now=` parameter) means every test runs without sleeping
and is deterministic across hosts. The signature path is also verified
end-to-end here so a regression in either leg of `verify_hmac` shows up
in this file rather than only the broader integration test.
"""

from __future__ import annotations

import hashlib
import hmac as _hmac

import pytest

from agent.agent import _HMAC_MAX_AGE_SECONDS, verify_hmac
from agent.schemas import ChatRequest, Message, Role


_SECRET = "test-fixture-secret-not-for-production"


def _build_signed_request(
    *,
    user_id: int = 1,
    patient_id: int = 1,
    timestamp: int = 1_000_000,
    secret: str = _SECRET,
    messages: list[str] | None = None,
) -> ChatRequest:
    """Construct a ChatRequest with a valid HMAC for the given fields.

    Helper rather than a fixture so each test can vary one input without
    fixture-parameter clutter."""
    messages = messages or ["hello"]
    payload = (
        f"{user_id}|{patient_id}|{timestamp}|" + "|".join(messages)
    )
    sig = _hmac.new(
        secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return ChatRequest(
        user_id=user_id,
        patient_id=patient_id,
        timestamp=timestamp,
        hmac=sig,
        messages=[Message(role=Role.USER, content=m) for m in messages],
    )


def test_valid_signature_inside_window_returns_none():
    req = _build_signed_request(timestamp=1_000_000)
    # Verify exactly at the moment the request was signed.
    assert verify_hmac(req, _SECRET, now=1_000_000.0) is None


def test_valid_signature_at_window_boundary_returns_none():
    """The check is strictly greater-than (`abs(age) > MAX_AGE`), so a
    request exactly at the boundary is still accepted."""
    req = _build_signed_request(timestamp=1_000_000)
    boundary = 1_000_000 + _HMAC_MAX_AGE_SECONDS
    assert verify_hmac(req, _SECRET, now=float(boundary)) is None


def test_stale_request_returns_timestamp_skew():
    """Captured-and-replayed request body — same secret, same signature,
    but timestamp older than the window. Must be rejected."""
    req = _build_signed_request(timestamp=1_000_000)
    too_old = 1_000_000 + _HMAC_MAX_AGE_SECONDS + 1
    assert verify_hmac(req, _SECRET, now=float(too_old)) == "hmac_timestamp_skew"


def test_far_future_request_returns_timestamp_skew():
    """Future-dated request — likely a clock-skew problem, but also
    blocks an attacker who tries to extend their replay window by
    pre-signing requests with later timestamps."""
    req = _build_signed_request(timestamp=1_000_000)
    too_early = 1_000_000 - _HMAC_MAX_AGE_SECONDS - 1
    assert verify_hmac(req, _SECRET, now=float(too_early)) == "hmac_timestamp_skew"


def test_signature_mismatch_returns_signature_mismatch():
    """Timestamp is fresh, but the hex bytes don't match the payload.
    Distinct from the skew path so audit logs / Langfuse traces can
    distinguish replay attempts from cryptographic tampering."""
    req = _build_signed_request(timestamp=1_000_000)
    req = req.model_copy(update={"hmac": "deadbeef" * 8})
    assert verify_hmac(req, _SECRET, now=1_000_000.0) == "hmac_signature_mismatch"


def test_empty_secret_fails_closed():
    """Defense in depth: a deploy that forgot to set the HMAC secret
    must reject everything, not silently allow."""
    req = _build_signed_request(timestamp=1_000_000)
    assert verify_hmac(req, "", now=1_000_000.0) == "hmac_no_secret_configured"


def test_wrong_secret_returns_signature_mismatch():
    """Signed with secret A, verified against secret B."""
    req = _build_signed_request(secret="secret-A", timestamp=1_000_000)
    assert (
        verify_hmac(req, "secret-B", now=1_000_000.0)
        == "hmac_signature_mismatch"
    )


def test_tampered_message_after_signing_returns_signature_mismatch():
    """The HMAC is over the messages too — modifying message content
    after signing must invalidate the signature."""
    req = _build_signed_request(messages=["original"], timestamp=1_000_000)
    req = req.model_copy(update={
        "messages": [Message(role=Role.USER, content="tampered")],
    })
    assert (
        verify_hmac(req, _SECRET, now=1_000_000.0)
        == "hmac_signature_mismatch"
    )


def test_tampered_timestamp_without_resigning_returns_signature_mismatch():
    """Signing with timestamp T then changing the request's timestamp
    field to T+1 (without re-signing) must trip the signature check —
    confirms the timestamp is part of the signed bytes, not a free-text
    field next to the signature."""
    req = _build_signed_request(timestamp=1_000_000)
    req = req.model_copy(update={"timestamp": 1_000_001})
    # Verify at a `now` that keeps both T and T+1 inside the window —
    # so the failure is purely the signature mismatch, not skew.
    assert (
        verify_hmac(req, _SECRET, now=1_000_000.5)
        == "hmac_signature_mismatch"
    )
