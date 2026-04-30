"""Unit tests for the verifier (Phase 3).

Tests the matching logic without an LLM: build claims + retrieved records by
hand, run verify_claims, assert the verdict + counts.

Per ARCHITECTURE.md §3.6 / prd.md §6.C:
- All-pass: every claim cites an existing record_id and tokens match.
- Strip: 1 of N claims fails → verdict=PARTIAL_STRIP, that one in claims_failed.
- Refused: >30% claims fail → verdict=REFUSED, retry_needed=True.
- Numeric mismatch: claim says "A1c 7.2", record has 7.8 → fails.
- Date mismatch: claim says "on 2026-01-01", record date is 2025-12-10 → fails.
- Empty source_record_ids: claim cites nothing → fails.
- Cited record not in retrieved set: fails.
"""

from __future__ import annotations

from agent.schemas import (
    CitationStrength,
    Claim,
    ClaimType,
    RetrievedRecord,
    VerifierVerdict,
)
from agent.verifier import verify_claims


def _record(table: str, rid: str, **fields) -> RetrievedRecord:
    return RetrievedRecord(
        table=table,
        record_id=rid,
        citation_strength=CitationStrength.STRUCTURED,
        fields=fields,
    )


def _claim(text: str, *ids: str, claim_type: ClaimType = ClaimType.FACT) -> Claim:
    return Claim(text=text, claim_type=claim_type, source_record_ids=list(ids))


def test_all_pass_when_claims_cite_existing_records() -> None:
    records = [
        _record("lists", "1408", title="Type 2 diabetes mellitus"),
        _record("prescriptions", "2231", drug="Metformin", dosage="1000mg"),
    ]
    claims = [
        _claim("Has type 2 diabetes", "lists:1408", claim_type=ClaimType.HISTORY),
        _claim("On metformin", "prescriptions:2231"),
    ]
    result = verify_claims(claims, records)
    assert result.verdict == VerifierVerdict.PASS
    assert len(result.claims_passed) == 2
    assert len(result.claims_failed) == 0
    assert result.failure_rate == 0.0
    assert result.retry_needed is False


def test_partial_strip_when_one_of_many_fails() -> None:
    records = [
        _record("lists", "1408", title="T2DM"),
        _record("lists", "1411", title="HTN"),
        _record("prescriptions", "2231", drug="Metformin"),
    ]
    claims = [
        _claim("Has T2DM", "lists:1408", claim_type=ClaimType.HISTORY),
        _claim("Has HTN", "lists:1411", claim_type=ClaimType.HISTORY),
        _claim("On metformin", "prescriptions:2231"),
        _claim("On glipizide", "prescriptions:9999"),  # not in retrieved set
    ]
    result = verify_claims(claims, records)
    # 1 of 4 = 25% failure → partial strip, no retry
    assert result.verdict == VerifierVerdict.PARTIAL_STRIP
    assert len(result.claims_passed) == 3
    assert len(result.claims_failed) == 1
    assert 0.24 < result.failure_rate < 0.26
    assert result.retry_needed is False


def test_refused_when_more_than_30pct_fail() -> None:
    records = [_record("lists", "1408", title="T2DM")]
    claims = [
        _claim("Has T2DM", "lists:1408", claim_type=ClaimType.HISTORY),
        _claim("On metformin", "prescriptions:9001"),  # bad
        _claim("BP 120/80", "form_vitals:9002"),       # bad
    ]
    result = verify_claims(claims, records)
    # 2 of 3 = 67% failure → refused
    assert result.verdict == VerifierVerdict.REFUSED
    assert result.retry_needed is True


def test_numeric_mismatch_strips_claim() -> None:
    records = [
        _record(
            "procedure_result",
            "5421",
            name="HbA1c",
            value="7.8",
            units="%",
            date="2026-03-15",
        ),
    ]
    claims = [
        # Wrong value — record has 7.8, claim says 7.2
        _claim(
            "HbA1c 7.2 on 2026-03-15",
            "procedure_result:5421",
            claim_type=ClaimType.LAB_VALUE,
        ),
    ]
    result = verify_claims(claims, records)
    assert result.verdict == VerifierVerdict.REFUSED
    assert len(result.claims_failed) == 1
    assert "7.2" in (result.claims_failed[0].verifier_note or "")


def test_numeric_match_passes_with_or_without_unit_suffix() -> None:
    records = [
        _record(
            "procedure_result",
            "5421",
            name="HbA1c",
            value="7.8",
            units="%",
            date="2026-03-15",
        ),
    ]
    # Claim writes "7.8%" but record stores "7.8" + "%" separately — should pass.
    claims = [
        _claim(
            "HbA1c was 7.8% on 2026-03-15",
            "procedure_result:5421",
            claim_type=ClaimType.LAB_VALUE,
        ),
    ]
    result = verify_claims(claims, records)
    assert result.verdict == VerifierVerdict.PASS


def test_date_mismatch_strips_claim() -> None:
    records = [
        _record(
            "procedure_result",
            "5421",
            name="HbA1c",
            value="7.8",
            date="2026-03-15",
        ),
    ]
    claims = [
        _claim(
            "HbA1c 7.8 on 2026-01-01",  # wrong date
            "procedure_result:5421",
            claim_type=ClaimType.LAB_VALUE,
        ),
    ]
    result = verify_claims(claims, records)
    assert result.verdict == VerifierVerdict.REFUSED
    assert "2026-01-01" in (result.claims_failed[0].verifier_note or "")


def test_empty_source_record_ids_fails() -> None:
    records = [_record("lists", "1408", title="T2DM")]
    claims = [_claim("Has diabetes")]  # no source ids
    result = verify_claims(claims, records)
    assert result.verdict == VerifierVerdict.REFUSED
    assert len(result.claims_failed) == 1


def test_cited_id_not_in_retrieved_set_fails() -> None:
    records = [_record("lists", "1408", title="T2DM")]
    claims = [_claim("Has T1DM", "lists:9999")]  # 9999 not retrieved
    result = verify_claims(claims, records)
    assert result.verdict == VerifierVerdict.REFUSED
    assert "9999" in (result.claims_failed[0].verifier_note or "")


def test_no_claims_at_all_passes_trivially() -> None:
    records: list[RetrievedRecord] = []
    claims: list[Claim] = []
    result = verify_claims(claims, records)
    assert result.verdict == VerifierVerdict.PASS
    assert result.failure_rate == 0.0


def test_qualifier_claim_passes_without_strict_match() -> None:
    """Qualifiers like 'elevated' pass as long as the cited record exists.

    Per §3.4: lenient on qualifiers, strict on numerics/dates.
    """
    records = [
        _record(
            "procedure_result",
            "5421",
            name="HbA1c",
            value="7.8",
            units="%",
        ),
    ]
    claims = [
        _claim(
            "HbA1c is elevated",  # no specific value/date — qualifier only
            "procedure_result:5421",
            claim_type=ClaimType.LAB_VALUE,
        ),
    ]
    result = verify_claims(claims, records)
    assert result.verdict == VerifierVerdict.PASS
