"""Unit tests for the outbound PHI scrubber.

Covers two surfaces:

1. `find_outbound_violations` — the response-gate detector that triggers
   refusal when the agent is about to ship cross-patient PHI to the user.
2. `mask_observability_patterns` — the Langfuse mask helper that scrubs
   the same patterns from input/output payloads before they ship to
   Langfuse Cloud.

Per AUDIT.md C-6 first cut + DECISIONS.md §4a callout. Name detection
is deliberately out of scope here (deferred with rationale in §4a).
"""

from __future__ import annotations

from agent._phi_scrubber import (
    find_outbound_violations,
    mask_observability_patterns,
)


# ---------------------------------------------------------------------------
# find_outbound_violations — REFUSE cases
# ---------------------------------------------------------------------------


def test_detects_other_patient_id_explicit() -> None:
    text = "Patient discussed shares notes with patient_id=99 from last visit."
    violations = find_outbound_violations(text, allowed_patient_id=42)
    assert any("cross_patient_id_mention" in v for v in violations)
    assert any("pid=99" in v for v in violations)


def test_detects_pid_colon_format() -> None:
    text = "See pid:7 for related history."
    violations = find_outbound_violations(text, allowed_patient_id=42)
    assert any("cross_patient_id_mention" in v and "pid=7" in v for v in violations)


def test_detects_pid_space_format() -> None:
    text = "Cross-reference pid 13 for medication overlap."
    violations = find_outbound_violations(text, allowed_patient_id=42)
    assert any("pid=13" in v for v in violations)


def test_allows_request_patient_id_in_prose() -> None:
    text = "Brief for patient_id=42 — see chart for details."
    violations = find_outbound_violations(text, allowed_patient_id=42)
    # No cross-patient violations because the only pid mentioned IS the request's.
    assert not any("cross_patient_id_mention" in v for v in violations)


def test_detects_ssn() -> None:
    text = "SSN on file: 123-45-6789."
    violations = find_outbound_violations(text, allowed_patient_id=1)
    assert any("ssn_pattern" in v for v in violations)


def test_skips_invalid_ssn_shapes() -> None:
    # 000-00-0000 and 666-XX-XXXX are not valid SSNs; the regex skips them.
    text = "Boilerplate placeholder 000-00-0000 and 666-12-3456."
    violations = find_outbound_violations(text, allowed_patient_id=1)
    assert not any("ssn_pattern" in v for v in violations)


def test_detects_phone_dash() -> None:
    text = "Patient contact: 555-123-4567."
    violations = find_outbound_violations(text, allowed_patient_id=1)
    assert any("phone_dash" in v for v in violations)


def test_detects_phone_paren() -> None:
    text = "Reach at (555) 123-4567 between 9-5."
    violations = find_outbound_violations(text, allowed_patient_id=1)
    assert any("phone_paren" in v for v in violations)


def test_detects_email_real() -> None:
    text = "Patient email: jane.doe@gmail.com."
    violations = find_outbound_violations(text, allowed_patient_id=1)
    assert any("email_pattern" in v for v in violations)


def test_allows_example_domain_emails() -> None:
    # Allowlisted test/example domains shouldn't trigger.
    text = "Synthetic placeholder test@example.com or contact@example.org."
    violations = find_outbound_violations(text, allowed_patient_id=1)
    assert not any("email_pattern" in v for v in violations)


def test_detects_mrn_with_prefix() -> None:
    text = "MRN: 12345678 — see legacy record."
    violations = find_outbound_violations(text, allowed_patient_id=1)
    assert any("mrn_pattern" in v for v in violations)


def test_skips_bare_digits_no_mrn_prefix() -> None:
    # Bare 8-digit string without "MRN" prefix is too noisy to refuse on
    # (collides with lab values, record IDs). Conservative scope.
    text = "Lab value 12345678 — see procedure_result."
    violations = find_outbound_violations(text, allowed_patient_id=1)
    assert not any("mrn_pattern" in v for v in violations)


def test_clean_response_returns_no_violations() -> None:
    text = (
        "Pre-visit brief: Type 2 diabetes since 2024. On metformin 1000mg BID. "
        "Last A1c 7.8 on 2026-03. No allergies on file. Last visit 2026-01."
    )
    violations = find_outbound_violations(text, allowed_patient_id=42)
    assert violations == []


def test_multiple_violations_returned_separately() -> None:
    text = (
        "See patient_id=99 for context. "
        "Their SSN is 123-45-6789 and phone is 555-867-5309."
    )
    violations = find_outbound_violations(text, allowed_patient_id=1)
    assert len(violations) >= 3
    kinds = {v.split(":")[0] for v in violations}
    assert "cross_patient_id_mention" in kinds
    assert "ssn_pattern" in kinds
    assert "phone_dash" in kinds


def test_handles_empty_string() -> None:
    assert find_outbound_violations("", allowed_patient_id=1) == []


def test_handles_non_string_input_safely() -> None:
    # Defensive — never raise on bad input.
    assert find_outbound_violations(None, allowed_patient_id=1) == []  # type: ignore[arg-type]
    assert find_outbound_violations(42, allowed_patient_id=1) == []  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# mask_observability_patterns — REPLACE cases (Langfuse export hygiene)
# ---------------------------------------------------------------------------


def test_mask_replaces_ssn_with_placeholder() -> None:
    out = mask_observability_patterns("SSN 123-45-6789 on file.")
    assert "<REDACTED-SSN>" in out
    assert "123-45-6789" not in out


def test_mask_replaces_phone_with_placeholder() -> None:
    out = mask_observability_patterns("Reach at 555-867-5309 anytime.")
    assert "<REDACTED-PHONE>" in out
    assert "555-867-5309" not in out


def test_mask_replaces_real_email_with_placeholder() -> None:
    out = mask_observability_patterns("Email jane@gmail.com today.")
    assert "<REDACTED-EMAIL>" in out
    assert "jane@gmail.com" not in out


def test_mask_preserves_example_domain_email() -> None:
    out = mask_observability_patterns("Demo placeholder test@example.com.")
    assert "test@example.com" in out
    assert "<REDACTED-EMAIL>" not in out


def test_mask_normalizes_patient_id_form() -> None:
    out = mask_observability_patterns("Brief for patient_id=42.")
    # Should normalize to pid:42 — preserves the integer signal but
    # removes the verbose `patient_id=` form.
    assert "pid:42" in out


def test_mask_returns_unchanged_for_clean_text() -> None:
    text = "Clean prose, no PHI patterns here."
    assert mask_observability_patterns(text) == text


def test_mask_handles_empty_safely() -> None:
    assert mask_observability_patterns("") == ""


def test_mask_handles_non_string_safely() -> None:
    # Defensive — return unchanged on non-string inputs.
    assert mask_observability_patterns(42) == 42  # type: ignore[arg-type]
    assert mask_observability_patterns(None) is None  # type: ignore[arg-type]
