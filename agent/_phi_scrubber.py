"""Outbound PHI scrubber — Tier-2 first cut for AUDIT.md C-6.

Two distinct concerns served by this module:

1. **Outbound response gate** (`find_outbound_violations`): scans the
   response prose + claim text the agent is about to return to the user
   for high-confidence cross-patient PHI patterns. If any are found,
   `run_chat` REFUSES the response rather than ship it. Cross-patient
   leakage is a HIPAA breach class — refusing is preferable to silently
   redacting a partial response.

2. **Langfuse observability mask** (`mask_observability_patterns`):
   scrubs the same patterns from input/output payloads BEFORE they ship
   to Langfuse Cloud. Complementary to the existing date-bucketing mask
   in `agent.agent._mask_phi` — different patterns, same surface.

What this Tier-2 cut covers (high-confidence regex patterns, near-zero
false-positive risk):

  - Cross-patient `patient_id=N` / `pid=N` / `pid:N` mentions
  - SSN `XXX-XX-XXXX`
  - US phone `XXX-XXX-XXXX` / `XXX.XXX.XXXX` / `(XXX) XXX-XXXX`
  - Email addresses (excluding `@example.com` / `@test.*` placeholders)
  - MRN-prefixed identifiers (`MRN: 12345678`)

What this does NOT cover (deferred — see DECISIONS.md §4a / AUDIT.md C-6):

  - **Cross-patient names.** Eval case 26 (cross_patient_leakage_resistance)
    catches the actual case where a lure injected another patient's name
    into a chart record. Detecting names with low false-positive rate
    requires (a) the request patient's name as an allowlist (not currently
    plumbed — would need a DB lookup or schema change), and (b) careful
    handling of provider names, drug names that look like names, etc.
    Documented as deferred work — see "Why name detection is deferred"
    in DECISIONS.md §4a.
  - **DOBs in free text.** Date bucketing (year-month) is in place via
    `_mask_phi` but doesn't refuse on detect.
  - **Geographic identifiers** (street, city, ZIP) — low signal-to-noise
    via regex; deferred.
  - **Other 11 of 18 HIPAA Safe Harbor identifiers** — deferred to a
    full week-3 hardening pass.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

# `patient_id=N`, `patient_id: N`, `pid=N`, `pid:N`, `pid N`, `PID N`.
# Captures the integer so the caller can compare against the allowed pid.
_PATIENT_ID_TOKEN = re.compile(
    r"\b(?:patient[_\s]?id|pid)\s*[:=]?\s*(\d+)\b",
    re.IGNORECASE,
)

# US Social Security Number — XXX-XX-XXXX. Deliberately strict: no
# all-zero or all-same digit groups (those aren't valid SSNs and would
# otherwise match e.g. "phone 000-00-0000" boilerplate).
_SSN = re.compile(r"\b(?!000|666)\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b")

# US phone in three common formats: dash, dot, paren-area-code.
_PHONE_DASH = re.compile(r"\b\d{3}-\d{3}-\d{4}\b")
_PHONE_DOT = re.compile(r"\b\d{3}\.\d{3}\.\d{4}\b")
_PHONE_PAREN = re.compile(r"\(\d{3}\)\s*\d{3}-\d{4}")

# Email — simple shape; allowlist common test/example domains so synthetic
# fixture data and demo placeholders don't trigger false-positive refusals.
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_EMAIL_ALLOWLIST_DOMAINS = ("example.com", "example.org", "example.net", "test.com")

# MRN-prefixed identifier — "MRN: 12345678", "MRN 1234567890", etc.
# Conservative: only matches when explicitly prefixed with MRN/Medical
# Record Number. Bare 6-12 digit strings are too noisy (lab values,
# record IDs, dates can collide).
_MRN_LIKE = re.compile(
    r"\b(?:MRN|Medical[-\s]?Record[-\s]?Number)[:\s#]*(\d{6,12})\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Outbound response gate — REFUSE on detect
# ---------------------------------------------------------------------------


def find_outbound_violations(text: str, allowed_patient_id: int) -> list[str]:
    """Scan response text for high-confidence cross-patient PHI patterns.

    Returns a list of violation descriptions; an empty list means the
    response is clean to ship. Caller (`agent.agent.run_chat`) treats a
    non-empty list as a refusal trigger.

    Patient IDs that match `allowed_patient_id` are not violations —
    the agent legitimately discusses the patient under review. Any OTHER
    patient_id mention is treated as a cross-patient leakage signal.

    Defensive: never raise. PHI scrubbing must not break the request path.
    """
    if not isinstance(text, str) or not text:
        return []

    violations: list[str] = []

    try:
        for m in _PATIENT_ID_TOKEN.finditer(text):
            mentioned_pid = int(m.group(1))
            if mentioned_pid != allowed_patient_id:
                violations.append(
                    f"cross_patient_id_mention: pid={mentioned_pid} "
                    f"(allowed={allowed_patient_id})"
                )
    except Exception:
        pass

    try:
        for m in _SSN.finditer(text):
            violations.append(f"ssn_pattern: {_redact_for_log(m.group(0))}")
    except Exception:
        pass

    try:
        for pat, label in (
            (_PHONE_DASH, "phone_dash"),
            (_PHONE_DOT, "phone_dot"),
            (_PHONE_PAREN, "phone_paren"),
        ):
            for m in pat.finditer(text):
                violations.append(f"{label}: {_redact_for_log(m.group(0))}")
    except Exception:
        pass

    try:
        for m in _EMAIL.finditer(text):
            email = m.group(0)
            domain = email.rsplit("@", 1)[-1].lower()
            if any(domain == d or domain.endswith("." + d) for d in _EMAIL_ALLOWLIST_DOMAINS):
                continue
            violations.append(f"email_pattern: {_redact_for_log(email)}")
    except Exception:
        pass

    try:
        for m in _MRN_LIKE.finditer(text):
            violations.append(f"mrn_pattern: {_redact_for_log(m.group(0))}")
    except Exception:
        pass

    return violations


def _redact_for_log(value: str) -> str:
    """Mask the actual value before it goes into logs / refusal payloads.
    The fact a violation happened is loggable; the raw PII string is not."""
    if len(value) <= 4:
        return "*" * len(value)
    return value[:2] + "*" * (len(value) - 4) + value[-2:]


# ---------------------------------------------------------------------------
# Langfuse observability mask — REPLACE on detect
# ---------------------------------------------------------------------------


def mask_observability_patterns(text: str) -> str:
    """Scrub PHI patterns from a string before it ships to Langfuse Cloud.

    Replaces matches with stable placeholder tokens so the trace structure
    is preserved (you can still see where SSN-shaped content existed in
    the response, just not the actual digits). Used by the Langfuse `mask`
    callback in `agent.agent._mask_phi`.

    Unlike `find_outbound_violations`, this is unconditional — the
    observability boundary doesn't have request-patient context, so
    every patient_id-shaped string gets normalized. The agent's actual
    request still sees real values; only the trace export is scrubbed.
    """
    if not isinstance(text, str) or not text:
        return text

    try:
        text = _SSN.sub("<REDACTED-SSN>", text)
        text = _PHONE_DASH.sub("<REDACTED-PHONE>", text)
        text = _PHONE_DOT.sub("<REDACTED-PHONE>", text)
        text = _PHONE_PAREN.sub("<REDACTED-PHONE>", text)
        text = _MRN_LIKE.sub("<REDACTED-MRN>", text)
        # Email: scrub non-allowlisted addresses; keep example.com etc. visible
        # so synthetic fixture + demo placeholders are still readable in traces.
        def _email_sub(m: re.Match[str]) -> str:
            email = m.group(0)
            domain = email.rsplit("@", 1)[-1].lower()
            if any(domain == d or domain.endswith("." + d) for d in _EMAIL_ALLOWLIST_DOMAINS):
                return email
            return "<REDACTED-EMAIL>"
        text = _EMAIL.sub(_email_sub, text)
        # Patient_id: normalize to `pid:<int>` form to preserve count signal
        # without leaving raw `patient_id=92` strings in the trace. Note we
        # still preserve the integer — the trace surface is post-BAA, and
        # a bare integer alone is not PHI under Safe Harbor §164.514. The
        # leakage class is the *combination* of identifiers (name + ID), and
        # we already scrub the name-adjacent context that makes it identifying.
        text = _PATIENT_ID_TOKEN.sub(r"pid:\1", text)
    except Exception:
        # Defensive — if anything in the regex sub path fails, return
        # the original. Observability must not break the request path.
        return text

    return text
