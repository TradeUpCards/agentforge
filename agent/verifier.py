"""Post-generation verifier — the architecture's central differentiator.

Per ARCHITECTURE.md §3:
- Each claim must cite at least one record_id present in the retrieved set.
- Strict matching for numerical values + dates (multi-format dates
  normalized to ISO before comparison).
- (value, date) pairs in claim text must come from the SAME cited record
  (not value-from-record-A paired with date-from-record-B).
- Lenient matching for qualifiers / entity names (substring contains).
- Atomic strip: failed claims are removed; rest are returned (§3.6).
- 30%-rule retry trigger lives in the agent loop, not here. This module
  reports the failure rate and a `retry_needed` flag; the agent loop decides
  what to do with it.

The pair-and-normalize behavior was added 2026-04-30 after live testing
on Synthea data revealed two gaps documented in DECISIONS.md §2:
  - non-ISO date formats decomposed into bare digits
  - (value, date) pairs not validated as a tuple from a single record
"""

from __future__ import annotations

import re
from typing import Iterable

from .schemas import Claim, ClaimType, RetrievedRecord, VerifierResult, VerifierVerdict


# Regex for "tokens worth verifying": numerics like 7.8, 1.0, 25, 70mg.
_NUMERIC_TOKEN = re.compile(r"\b\d+(?:\.\d+)?(?:mg|g|mcg|ml|%|mmHg)?\b")

# Date regex with named groups for each supported format. Order matters:
# ISO is tried first so 2025-08-19 isn't mis-parsed as us_dash. The us_dash
# alternative would otherwise greedily match 8-19-2025, which is fine.
_DATE_REGEX = re.compile(
    r"(?P<iso>\b\d{4}-\d{2}-\d{2}\b)"
    r"|(?P<us_slash>\b\d{1,2}/\d{1,2}/\d{4}\b)"
    r"|(?P<us_dash>\b\d{1,2}-\d{1,2}-\d{4}\b)"
)

# Maximum character distance between a value and a date for them to be
# considered a paired claim. ~60 chars ≈ one short clause. Beyond that we
# treat them as independent tokens.
_PAIR_DISTANCE_THRESHOLD = 60


def _normalize_date(match: re.Match) -> str | None:
    """Convert a date regex match into canonical ISO YYYY-MM-DD."""
    iso = match.group("iso")
    if iso:
        return iso
    us_slash = match.group("us_slash")
    if us_slash:
        m, d, y = us_slash.split("/")
        return f"{y}-{int(m):02d}-{int(d):02d}"
    us_dash = match.group("us_dash")
    if us_dash:
        m, d, y = us_dash.split("-")
        return f"{y}-{int(m):02d}-{int(d):02d}"
    return None


def _normalized_dates_in(text: str) -> set[str]:
    """All dates in text, normalized to ISO. Empty set if none."""
    out: set[str] = set()
    for match in _DATE_REGEX.finditer(text):
        nd = _normalize_date(match)
        if nd:
            out.add(nd)
    return out


def _strip_dates(text: str) -> str:
    """Return text with date tokens replaced by spaces (preserves positions
    so subsequent regex matches keep their offsets meaningful)."""
    return _DATE_REGEX.sub(lambda m: " " * (m.end() - m.start()), text)


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


def _split_sentences(text: str) -> list[str]:
    """Split claim text into sentence-ish chunks for proximity-based pair
    extraction. Splits on full stops, semicolons, newlines, but not on `.`
    inside decimal numbers like 7.8 — that period is a decimal separator,
    not a sentence boundary."""
    # A `.` is a sentence boundary only when NOT flanked by digits on both
    # sides. Use a regex that splits on `.` followed by non-digit (or EOL),
    # or `!?\n;` unconditionally.
    return [s for s in re.split(r"(?:(?<!\d)\.|\.(?!\d)|[!?\n;])+", text) if s.strip()]


def _verify_one_claim(
    claim: Claim,
    record_index: dict[str, RetrievedRecord],
) -> tuple[bool, str | None]:
    """Return (passed, note). Note explains failure when passed=False."""

    # Per ARCHITECTURE.md §3.7: absence claims are verifiable when the
    # corresponding tool was actually called and returned an empty result.
    if claim.claim_type == ClaimType.ABSENCE and not record_index:
        return True, None

    # 1. At least one cited record_id must exist in the retrieved set.
    if not claim.source_record_ids:
        return False, "no source_record_ids on claim"
    cited_records: list[RetrievedRecord] = []
    for rid in claim.source_record_ids:
        record = record_index.get(rid)
        if record is None:
            return False, f"cited record_id {rid!r} not in retrieved set"
        cited_records.append(record)

    # Per-record blobs and normalized-date sets. This is the "is the data
    # actually in this specific record?" view — vs the cross-record union
    # that we use for standalone date/value checks.
    record_blobs = [_record_text_blob(r) for r in cited_records]
    record_dates_normalized = [_normalized_dates_in(b) for b in record_blobs]
    all_record_dates: set[str] = set().union(*record_dates_normalized) if record_dates_normalized else set()
    combined_blob = " ".join(record_blobs)

    # 2. (value, date) pair check — both members of a pair must come from
    # the same cited record. A claim like "GFR 77.3 (08/19/2025)" is a
    # pair; "On metformin since 10/04/2025" is also a pair. Without this
    # check, value-from-record-A + date-from-record-B citations slip
    # through (DECISIONS.md §2).
    paired_value_offsets: set[tuple[int, int]] = set()
    paired_date_offsets: set[tuple[int, int]] = set()

    for sentence in _split_sentences(claim.text):
        date_matches = list(_DATE_REGEX.finditer(sentence))
        if not date_matches:
            continue
        sentence_no_dates = _strip_dates(sentence)
        value_matches = list(_NUMERIC_TOKEN.finditer(sentence_no_dates))
        if not value_matches:
            continue

        for v in value_matches:
            v_pos = (v.start() + v.end()) // 2
            nearest = min(
                date_matches,
                key=lambda d, vp=v_pos: min(abs(d.start() - vp), abs(d.end() - vp)),
            )
            distance = min(abs(nearest.start() - v_pos), abs(nearest.end() - v_pos))
            if distance > _PAIR_DISTANCE_THRESHOLD:
                continue
            value_token = v.group()
            normalized = _normalize_date(nearest)
            if normalized is None:
                continue

            # Pair must co-locate in the SAME record.
            ok = False
            bare = re.sub(r"(mg|g|mcg|ml|%|mmHg)$", "", value_token).strip().lower()
            for blob, dates in zip(record_blobs, record_dates_normalized):
                blob_lower = blob.lower()
                value_present = (value_token.lower() in blob_lower) or (bare in blob_lower)
                if value_present and normalized in dates:
                    ok = True
                    break
            if not ok:
                return False, (
                    f"({value_token!r}, {normalized!r}) not co-located in "
                    f"any single cited record"
                )

            paired_value_offsets.add((v.start(), v.end()))
            paired_date_offsets.add((nearest.start(), nearest.end()))

    # 3. Standalone date check (any date in the claim that wasn't part of
    # a pair must still exist in the union of cited records' dates).
    for match in _DATE_REGEX.finditer(claim.text):
        normalized = _normalize_date(match)
        if normalized is None:
            continue
        if normalized not in all_record_dates:
            return False, f"date {normalized!r} not in cited records"

    # 4. Standalone numeric check (values not part of a pair).
    text_no_dates = _strip_dates(claim.text)
    combined_blob_lower = combined_blob.lower()
    for v in _NUMERIC_TOKEN.finditer(text_no_dates):
        token = v.group()
        bare = re.sub(r"(mg|g|mcg|ml|%|mmHg)$", "", token).strip()
        if token.lower() not in combined_blob_lower and bare.lower() not in combined_blob_lower:
            return False, f"numeric token {token!r} not in cited records"

    # 5. Lenient on qualifiers — no further checks (§3.4).
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
