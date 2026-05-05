"""Unit tests for agent.agent.verify_attach_hmac.

Covers:
- Correct signature verifies (happy path)
- Wrong signature is rejected (fail-closed)
- Canonical payload is stable (regression guard against payload-shape drift)

Structurally identical to test_hmac_replay.py which covers verify_hmac and
verify_score_hmac — same constant-time-comparison pattern, different payload.
"""

from __future__ import annotations

import hashlib
import hmac as _hmac

from agent.agent import verify_attach_hmac


_SECRET = "test-attach-fixture-secret-not-for-production"

# Stable fixture inputs for regression/canonical tests.
_FIXTURE_USER_ID = 42
_FIXTURE_PATIENT_ID = 999_101
_FIXTURE_DOC_REF_ID = "docref-abc123"
_FIXTURE_DOC_TYPE = "lab_pdf"
_FIXTURE_TIMESTAMP = 1_700_000_000
_FIXTURE_FILE_SHA256 = "a" * 64  # 64 hex chars, stands in for a real SHA-256

# Pre-computed expected digest for the fixture inputs above.
# Computed as: sha256(b"42|999101|docref-abc123|lab_pdf|1700000000|" + b"a"*64)
_FIXTURE_PAYLOAD = (
    f"{_FIXTURE_USER_ID}|{_FIXTURE_PATIENT_ID}|{_FIXTURE_DOC_REF_ID}|"
    f"{_FIXTURE_DOC_TYPE}|{_FIXTURE_TIMESTAMP}|{_FIXTURE_FILE_SHA256}"
)
_FIXTURE_EXPECTED_DIGEST = _hmac.new(
    _SECRET.encode("utf-8"),
    _FIXTURE_PAYLOAD.encode("utf-8"),
    hashlib.sha256,
).hexdigest()


def test_verify_attach_hmac_correct_signature() -> None:
    """Happy path: given known inputs + secret, signature verifies as True."""
    result = verify_attach_hmac(
        user_id=_FIXTURE_USER_ID,
        patient_id=_FIXTURE_PATIENT_ID,
        doc_ref_id=_FIXTURE_DOC_REF_ID,
        doc_type=_FIXTURE_DOC_TYPE,
        timestamp=_FIXTURE_TIMESTAMP,
        file_sha256_hex=_FIXTURE_FILE_SHA256,
        hmac_str=_FIXTURE_EXPECTED_DIGEST,
        secret=_SECRET,
    )
    assert result is True


def test_verify_attach_hmac_wrong_signature_rejected() -> None:
    """Known-bad signature (flipped last char) returns False.

    Confirms constant-time comparison fires and a crafted-but-wrong HMAC
    is always rejected regardless of how close it is to the real one.
    """
    # Flip the last hex char to produce a different but syntactically valid hex string.
    bad_sig = _FIXTURE_EXPECTED_DIGEST[:-1] + (
        "0" if _FIXTURE_EXPECTED_DIGEST[-1] != "0" else "1"
    )
    result = verify_attach_hmac(
        user_id=_FIXTURE_USER_ID,
        patient_id=_FIXTURE_PATIENT_ID,
        doc_ref_id=_FIXTURE_DOC_REF_ID,
        doc_type=_FIXTURE_DOC_TYPE,
        timestamp=_FIXTURE_TIMESTAMP,
        file_sha256_hex=_FIXTURE_FILE_SHA256,
        hmac_str=bad_sig,
        secret=_SECRET,
    )
    assert result is False


def test_verify_attach_hmac_empty_secret_fails_closed() -> None:
    """Defense in depth: empty secret must always return False (fail-closed).

    A misconfigured deploy that never set OPENEMR_HMAC_SECRET must reject
    every request rather than silently accept them all.
    """
    result = verify_attach_hmac(
        user_id=_FIXTURE_USER_ID,
        patient_id=_FIXTURE_PATIENT_ID,
        doc_ref_id=_FIXTURE_DOC_REF_ID,
        doc_type=_FIXTURE_DOC_TYPE,
        timestamp=_FIXTURE_TIMESTAMP,
        file_sha256_hex=_FIXTURE_FILE_SHA256,
        hmac_str=_FIXTURE_EXPECTED_DIGEST,
        secret="",
    )
    assert result is False


def test_attach_hmac_payload_is_canonical() -> None:
    """Regression guard: verify the canonical payload shape has not drifted.

    If the payload composition formula in verify_attach_hmac ever changes
    (e.g. field order, separator), this test catches it before a deploy
    where the PHP implementer is building to the locked contract.

    Canonical: f"{user_id}|{patient_id}|{doc_ref_id}|{doc_type}|{timestamp}|{file_sha256_hex}"
    Separator: pipe (|)
    Hash: SHA-256 HMAC
    """
    # Recompute the digest from first principles using only the published formula.
    expected_payload = (
        f"{_FIXTURE_USER_ID}|{_FIXTURE_PATIENT_ID}|{_FIXTURE_DOC_REF_ID}|"
        f"{_FIXTURE_DOC_TYPE}|{_FIXTURE_TIMESTAMP}|{_FIXTURE_FILE_SHA256}"
    )
    expected_digest = _hmac.new(
        _SECRET.encode("utf-8"),
        expected_payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    # The pre-computed constant at the top of this file must match.
    assert expected_digest == _FIXTURE_EXPECTED_DIGEST, (
        "Canonical payload constant has drifted from computed value. "
        "Update _FIXTURE_EXPECTED_DIGEST or fix the payload formula."
    )

    # And the function under test must agree with both.
    assert verify_attach_hmac(
        user_id=_FIXTURE_USER_ID,
        patient_id=_FIXTURE_PATIENT_ID,
        doc_ref_id=_FIXTURE_DOC_REF_ID,
        doc_type=_FIXTURE_DOC_TYPE,
        timestamp=_FIXTURE_TIMESTAMP,
        file_sha256_hex=_FIXTURE_FILE_SHA256,
        hmac_str=expected_digest,
        secret=_SECRET,
    )
