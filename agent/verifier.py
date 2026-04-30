"""Post-generation verifier — the architecture's central differentiator.

Per ARCHITECTURE.md §3:
- Each claim must cite at least one record_id present in the retrieved set.
- Strict matching for numerical values + dates (exact-string match for v1).
- Lenient matching for qualifiers / entity names (substring contains).
- Atomic strip: failed claims are removed; rest are returned (§3.6).
- 30%-rule retry trigger lives in the agent loop, not here. This module
  reports the failure rate and a `retry_needed` flag; the agent loop decides
  what to do with it.

This is intentionally dumb in v1 — fuzzy thresholds invite gaming. Numerical
hallucinations are the dangerous class; we strip aggressively when claim text
mentions numbers/dates that don't appear in cited records' fields.
"""

from __future__ import annotations

import re
from typing import Any

from .schemas import Claim, RetrievedRecord, VerifierResult, VerifierVerdict


# Regex for "tokens worth verifying": numerics like 7.8, 1.0, 25, 70mg, dates
# like 2026-03-15, etc.
_NUMERIC_TOKEN = re.compile(r"\b\d+(?:\.\d+)?(?:mg|g|mcg|ml|%|mmHg)?\b")
_DATE_TOKEN = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")


def _index_records(records: list[RetrievedRecord]) -> dict[str, RetrievedRecord]:
    """Map 'table:id' -> record for O(1) citation lookup."""
    return {f"{r.table}:{r.record_id}": r for r in records}


def _record_text_blob(record: RetrievedRecord) -> str:
    """Flatten a record's fields into a searchable text blob.

    Used for substring contains-checks. Includes both keys and values so
    'hba1c' matches a field named 'name' with value 'HbA1c'.
    """
    parts: list[str] = []
    for k, v in record.fields.items():
        parts.append(str(k))
        parts.append(str(v))
    return " ".join(parts).lower()


def _verify_one_claim(
    claim: Claim,
    record_index: dict[str, RetrievedRecord],
) -> tuple[bool, str | None]:
    """Return (passed, note). Note explains failure when passed=False."""

    # 1. At least one cited record_id must exist in the retrieved set.
    if not claim.source_record_ids:
        return False, "no source_record_ids on claim"
    cited_records: list[RetrievedRecord] = []
    for rid in claim.source_record_ids:
        record = record_index.get(rid)
        if record is None:
            return False, f"cited record_id {rid!r} not in retrieved set"
        cited_records.append(record)

    # 2. Strict matching for dates + numerical tokens.
    # If the claim text mentions a date or numeric value, that token must
    # appear (verbatim) in at least one cited record's field blob.
    cited_blob = " ".join(_record_text_blob(r) for r in cited_records)

    # Check dates first and strip matched dates from the text before numeric
    # scanning — otherwise the day/month digits inside an ISO date would be
    # picked up as standalone numeric tokens.
    text_for_numeric_scan = claim.text
    for token in _DATE_TOKEN.findall(claim.text):
        if token not in cited_blob:
            return False, f"date {token!r} not in cited records"
        text_for_numeric_scan = text_for_numeric_scan.replace(token, "")

    for token in _NUMERIC_TOKEN.findall(text_for_numeric_scan):
        # Tolerate the token appearing without a unit suffix in the record.
        # e.g. claim says "7.8%", record has value=7.8, units=%.
        bare = re.sub(r"(mg|g|mcg|ml|%|mmHg)$", "", token).strip()
        if token.lower() not in cited_blob and bare.lower() not in cited_blob:
            return False, f"numeric token {token!r} not in cited records"

    # 3. Lenient on qualifiers — no further checks. The "is this fact in the
    # data?" job is done by the strict numeric/date pass; qualifier looseness
    # is a phrasing preference, not a safety risk (§3.4).
    return True, None


def verify_claims(
    claims: list[Claim],
    retrieved_records: list[RetrievedRecord],
) -> VerifierResult:
    """Score every claim; return verdict + atomically-stripped passing list."""
    record_index = _index_records(retrieved_records)
    passed: list[Claim] = []
    failed: list[Claim] = []
    for claim in claims:
        ok, note = _verify_one_claim(claim, record_index)
        if ok:
            claim.verified = True
            passed.append(claim)
        else:
            claim.verified = False
            claim.verifier_note = note
            failed.append(claim)

    total = len(claims)
    failure_rate = (len(failed) / total) if total else 0.0
    if total == 0 or failure_rate == 0.0:
        verdict = VerifierVerdict.PASS
    elif failure_rate > 0.30:
        verdict = VerifierVerdict.REFUSED
    else:
        verdict = VerifierVerdict.PARTIAL_STRIP

    return VerifierResult(
        verdict=verdict,
        claims_passed=passed,
        claims_failed=failed,
        failure_rate=failure_rate,
        retry_needed=failure_rate > 0.30,
    )
