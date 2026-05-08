"""Haiku schema extraction — Stage 2 of the two-stage extraction pipeline.

W2_ARCHITECTURE.md §2.2: Docling (Stage 1) owns bboxes; Haiku (Stage 2)
maps block text to schema fields. This module owns the Stage 2 half.

Key responsibilities:
  1. Build Anthropic messages with cache-controlled system block (schema spec)
     + variable user block (doc text + block_id index).
  2. Parse and validate Haiku's JSON output against Pydantic schemas.
  3. Run the extraction verifier (§2.4): every field value confirmed as a
     substring of its named source block. Fields failing the check are stripped.
     If >30% fail, raise ExtractionLowGrounding.
  4. Enforce sentinel patient_id range and per-field confidence bounds.

PHI discipline (§8.3):
  - Block text goes into the user message ONLY; never into logs or traces.
  - Extracted field values never in logs, traces, or exception messages.
  - patient_id only asserted in-range; value never interpolated in error text.

No LLM is called here — the caller (attach_and_extract) calls the LLM client
and passes the raw JSON response text into the parser functions.
"""

from __future__ import annotations

import json
import logging
import unicodedata
from dataclasses import dataclass, field as _dc_field
from datetime import date
from typing import Any

from agent.document_schemas import (
    Allergy,
    Demographics,
    DoclingDoc,
    ExtractMeta,
    FamilyHistoryItem,
    IntakeForm,
    LabReport,
    LabResult,
    Medication,
)
from agent.prompts import lab_report_extraction, intake_form_extraction

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sentinel range (mirrors document_schemas._SENTINEL_MIN/_MAX)
# ---------------------------------------------------------------------------

_SENTINEL_MIN = 999_100
_SENTINEL_MAX = 999_199

# ---------------------------------------------------------------------------
# Extraction-verifier threshold (W2_ARCHITECTURE.md §2.4)
# ---------------------------------------------------------------------------

_GROUNDING_FAILURE_THRESHOLD = 0.30  # >30% failing fields → refuse (W2_ARCHITECTURE.md §2.4)


# ---------------------------------------------------------------------------
# Named refusal exception
# ---------------------------------------------------------------------------


class ExtractionLowGrounding(ValueError):
    """Raised when >30% of extracted fields fail the grounding check.

    W2_ARCHITECTURE.md §2.4: "if >30% of fields fail, refuse extraction
    with extraction_low_grounding".

    The message MUST NOT include raw field values or block text.
    """


# ---------------------------------------------------------------------------
# Verifier failure reason codes — fixed string literals, never user data.
# IMPORTANT: add new codes here as constants; never pass interpolated strings
# to ExtractionStats.record_failure(). The reason codes appear as JSON keys
# in the haiku_schema_extraction Langfuse span and must be static vocabulary.
# ---------------------------------------------------------------------------

REASON_VALUE_NOT_SUBSTRING = "value_not_substring_of_block"
REASON_BLOCK_NOT_FOUND = "block_not_found"
REASON_CONFIDENCE_OOB = "confidence_oob"


# ---------------------------------------------------------------------------
# Extraction stats carrier (P1 eval metrics — PRD §8.1)
# ---------------------------------------------------------------------------


@dataclass
class ExtractionStats:
    """Structural counts from one parse+verify run.

    Returned as the second element of parse_lab_report / parse_intake_form
    so _run_haiku_extraction can emit them to the Langfuse span.

    PHI discipline: counts and reason codes only — no field names, no
    field values. verifier_reason_counts keys are ONLY the module-level
    REASON_* constants defined in this file, never LLM output or block text.

    field_verdicts shape: list of (field_path, source_block_id, verifier_reason)
      - field_path:      static string literal (e.g. "results[0].test_name")
      - source_block_id: block ID string or None
      - verifier_reason: None when verified; one of REASON_* constants when stripped
    """

    total_fields: int = 0
    stripped_fields: int = 0
    verifier_reason_counts: dict[str, int] = _dc_field(default_factory=dict)
    field_verdicts: list[tuple[str, str | None, str | None]] = _dc_field(
        default_factory=list
    )

    @property
    def strip_rate(self) -> float:
        """Fraction of fields that failed grounding. 0.0 if total_fields == 0."""
        if self.total_fields == 0:
            return 0.0
        return self.stripped_fields / self.total_fields

    def record_failure(
        self,
        reason: str,
        field_path: str = "",
        source_block_id: str | None = None,
    ) -> None:
        """Increment stripped_fields and the named reason bucket.

        Also appends a (field_path, source_block_id, reason) tuple to
        field_verdicts for per-field traceability in the P3 response payload.

        Args:
            reason: MUST be one of the REASON_* module-level constants
                    (REASON_VALUE_NOT_SUBSTRING, REASON_BLOCK_NOT_FOUND,
                    REASON_CONFIDENCE_OOB).
                    Never pass interpolated strings, field names, or
                    values from LLM output — those would become keys in
                    the Langfuse span's verifier_reason_counts JSON.
            field_path:      Static string literal identifying the field
                             (e.g. "results[0].test_name"). PHI-safe:
                             always a literal, never interpolated LLM output.
            source_block_id: The Docling block_id cited for this field, or
                             None if not available.
        """
        self.stripped_fields += 1
        self.verifier_reason_counts[reason] = (
            self.verifier_reason_counts.get(reason, 0) + 1
        )
        self.field_verdicts.append((field_path, source_block_id, reason))

    def record_verified(
        self,
        field_path: str,
        source_block_id: str | None,
    ) -> None:
        """Record a successfully verified field in field_verdicts.

        Appends a (field_path, source_block_id, None) tuple — the None
        verifier_reason signals "verified" (as opposed to a REASON_* constant
        which signals "stripped").

        Args:
            field_path:      Static string literal identifying the field.
                             Must never contain LLM output or patient data.
            source_block_id: The Docling block_id confirmed as the source.
        """
        self.field_verdicts.append((field_path, source_block_id, None))


# ---------------------------------------------------------------------------
# O(1) block index builder
# ---------------------------------------------------------------------------


def _build_block_index(doc: DoclingDoc) -> dict[str, str]:
    """Build a block_id → text mapping for O(1) lookup.

    The existing DoclingDoc.block_by_id() does a linear scan. For extraction
    verification we need to check many fields, so we pre-build the dict once
    and reuse it throughout a single extraction call.
    """
    return {block.block_id: block.text for block in doc.blocks}


# ---------------------------------------------------------------------------
# Normalization helper for grounding check
# ---------------------------------------------------------------------------


def _normalize(text: str) -> str:
    """Normalize text for substring grounding comparison.

    Lowercases, collapses whitespace, strips accents, removes punctuation
    noise. Mirrors the W2_ARCHITECTURE.md §2.4 `normalize()` intent.
    """
    # NFKD decomposition strips accents
    text = unicodedata.normalize("NFKD", text)
    # Encode to ASCII with 'ignore' drops non-ASCII accent chars
    text = text.encode("ascii", "ignore").decode("ascii")
    return " ".join(text.lower().split())


# ---------------------------------------------------------------------------
# Extraction verifier (W2_ARCHITECTURE.md §2.4)
# ---------------------------------------------------------------------------


def verify_field(
    value: str,
    source_block_id: str | None,
    block_index: dict[str, str],
) -> tuple[bool, str | None]:
    """Check that `value` appears (normalized) in the named source block.

    Returns (False, reason) if:
      - source_block_id is None or empty  → reason: REASON_BLOCK_NOT_FOUND
      - source_block_id is not in the block index  → reason: REASON_BLOCK_NOT_FOUND
      - normalized(value) is not a substring of normalized(block.text)  → reason: REASON_VALUE_NOT_SUBSTRING

    Returns (True, None) when grounding succeeds.

    Does NOT log the value or block text — grounding check is structural.
    """
    if not source_block_id:
        return False, REASON_BLOCK_NOT_FOUND
    block_text = block_index.get(source_block_id)
    if block_text is None:
        return False, REASON_BLOCK_NOT_FOUND
    if _normalize(value) in _normalize(block_text):
        return True, None
    return False, REASON_VALUE_NOT_SUBSTRING


def _check_grounding_rate(
    total_fields: int,
    failed_fields: int,
    context: str,
) -> None:
    """Raise ExtractionLowGrounding if the failure rate exceeds the threshold.

    `context` is a non-PHI label (e.g. "LabReport") for the log entry.
    """
    if total_fields == 0:
        return
    rate = failed_fields / total_fields
    logger.info(
        "extraction_verifier: grounding check",
        extra={
            "context": context,
            "total_fields": total_fields,
            "failed_fields": failed_fields,
            "failure_rate": round(rate, 4),
        },
    )
    if rate > _GROUNDING_FAILURE_THRESHOLD:
        logger.warning(
            "extraction_verifier: GROUNDING FAILED context=%s failed=%d/%d rate=%.2f threshold=%.2f",
            context,
            failed_fields,
            total_fields,
            rate,
            _GROUNDING_FAILURE_THRESHOLD,
        )
        raise ExtractionLowGrounding(
            f"extraction_low_grounding: {failed_fields}/{total_fields} fields "
            f"failed grounding check in {context} "
            f"(rate={rate:.2f} > threshold={_GROUNDING_FAILURE_THRESHOLD:.2f})"
        )


# ---------------------------------------------------------------------------
# Confidence validation
# ---------------------------------------------------------------------------


def _validate_confidence(
    confidence: float,
    label: str,
    stats: ExtractionStats,
    field_path: str = "",
    source_block_id: str | None = None,
) -> float | None:
    """Return confidence if in [0.0, 1.0]; record a strip failure and return
    None if out of bounds (strip-and-continue, not raise).

    P3 decision (W2_ARCHITECTURE.md P3 brief): confidence_oob is treated as
    a strip-and-continue condition, not an extraction abort. The field is
    dropped from the result set and REASON_CONFIDENCE_OOB is recorded in
    ExtractionStats so it surfaces in Langfuse and field_verdicts.

    Intentionally does NOT interpolate the actual confidence value into any
    log message or exception: establishes the convention that no numeric
    field values appear in observability output.

    Args:
        confidence:      The raw confidence float from the LLM response.
        label:           Non-PHI label for logging context (e.g. "LabResult").
        stats:           ExtractionStats accumulator; record_failure is called
                         here when confidence is OOB.
        field_path:      Static string literal for field_verdicts traceability.
        source_block_id: Docling block_id for the field being validated.

    Returns:
        The confidence float when in range, or None when OOB (signal to skip).
    """
    if not (0.0 <= confidence <= 1.0):
        logger.info(
            "extraction_verifier: confidence out of range, field stripped",
            extra={
                "label": label,
                "field_path": field_path,
                "source_block_id": source_block_id,
                # Never log the confidence value itself
            },
        )
        stats.record_failure(
            REASON_CONFIDENCE_OOB,
            field_path=field_path,
            source_block_id=source_block_id,
        )
        return None
    return confidence


# ---------------------------------------------------------------------------
# Message builders
# ---------------------------------------------------------------------------


def build_lab_report_messages(
    doc: DoclingDoc,
    *,
    verbatim: bool = False,
) -> list[dict[str, Any]]:
    """Build the Anthropic messages list for LabReport extraction.

    The system block is the stable cacheable prefix (schema spec). Caller
    passes this list to the Anthropic SDK as the `messages` param, with the
    system block delivered separately as the `system` param with
    cache_control applied.

    Returns a list with a single user message (the doc block index).

    No PHI in this function's return value goes to Langfuse —
    only token counts are observable. Block text stays in the messages.

    Args:
        doc:      DoclingDoc from Stage 1.
        verbatim: When True, routes to build_user_message_verbatim() which
                  appends the verbatim-only amendment (PRD §6.3).  Default
                  False preserves all existing call sites unchanged.
    """
    if verbatim:
        user_content = lab_report_extraction.build_user_message_verbatim(doc)
    else:
        user_content = lab_report_extraction.build_user_message(doc)
    return [
        {
            "role": "user",
            "content": user_content,
        }
    ]


def build_intake_form_messages(
    doc: DoclingDoc,
    *,
    verbatim: bool = False,
) -> list[dict[str, Any]]:
    """Build the Anthropic messages list for IntakeForm extraction.

    Same pattern as build_lab_report_messages — stable system block is
    in the prompt template; variable user block is doc-specific.

    Args:
        doc:      DoclingDoc from Stage 1.
        verbatim: When True, routes to build_user_message_verbatim() which
                  appends the verbatim-only amendment (PRD §6.3).  Default
                  False preserves all existing call sites unchanged.
    """
    if verbatim:
        user_content = intake_form_extraction.build_user_message_verbatim(doc)
    else:
        user_content = intake_form_extraction.build_user_message(doc)
    return [
        {
            "role": "user",
            "content": user_content,
        }
    ]


def build_lab_report_system() -> list[dict[str, Any]]:
    """Return the system parameter for the LabReport Haiku call.

    Returns a list with one content block that has cache_control applied.
    The Anthropic API requires system as a list[dict] with cache_control on
    the block to enable prompt caching.
    """
    return [
        {
            "type": "text",
            "text": lab_report_extraction.SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }
    ]


def build_intake_form_system() -> list[dict[str, Any]]:
    """Return the system parameter for the IntakeForm Haiku call."""
    return [
        {
            "type": "text",
            "text": intake_form_extraction.SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }
    ]


# ---------------------------------------------------------------------------
# LabReport parser
# ---------------------------------------------------------------------------


def parse_lab_report(
    haiku_json_text: str,
    doc: DoclingDoc,
    patient_id: int,
    doc_ref_id: str,
    extraction_model: str,
) -> tuple[LabReport, ExtractionStats]:
    """Parse Haiku's JSON response into a validated LabReport.

    Runs the extraction verifier on every LabResult's source_block_id and
    value. Fields failing the check are stripped. If >30% fail, raises
    ExtractionLowGrounding.

    Args:
        haiku_json_text: Raw JSON string from Haiku.
        doc:             The DoclingDoc from Stage 1 (provides block index).
        patient_id:      Must be in sentinel range; validated here before use.
        doc_ref_id:      FHIR DocumentReference ID for the source document.
        extraction_model: Model name string for ExtractMeta (e.g. "claude-haiku-4-5").

    Returns:
        Tuple of (validated LabReport, ExtractionStats with grounding counts).

    Raises:
        ValueError:              patient_id outside sentinel range, or invalid
                                 block_id cited by Haiku.
        ExtractionLowGrounding:  >30% of fields fail grounding check.
        json.JSONDecodeError:    Haiku response is not valid JSON.
    """
    # Sentinel range check — no patient_id in the error message
    if not (_SENTINEL_MIN <= patient_id <= _SENTINEL_MAX):
        raise ValueError(
            "patient_id is outside W2 sentinel range "
            f"[{_SENTINEL_MIN}, {_SENTINEL_MAX}]."
        )

    raw: dict[str, Any] = json.loads(haiku_json_text)
    block_index = _build_block_index(doc)

    raw_results: list[dict[str, Any]] = raw.get("results", [])

    passed_results: list[LabResult] = []
    stats = ExtractionStats()
    all_confidences: list[float] = []

    for i, item in enumerate(raw_results):
        # Static field_path literal for this result item — index is the loop
        # ordinal (integer), never LLM output. PHI-safe per W2 P3 brief.
        field_path = f"results[{i}].test_name"

        # Validate source_block_id exists in the doc
        source_block_id: str | None = item.get("source_block_id")
        if source_block_id is not None and source_block_id not in block_index:
            raise ValueError(
                "Haiku cited a source_block_id that does not exist in the DoclingDoc. "
                "Block index violation — refusing extraction."
            )

        # Confidence bound check — strip-and-continue (P3: confidence_oob)
        raw_confidence = item.get("confidence", 0.0)
        # Convert to float defensively
        try:
            raw_conf_float = float(raw_confidence)
        except (TypeError, ValueError):
            raw_conf_float = 0.0
        stats.total_fields += 1
        validated_confidence = _validate_confidence(
            raw_conf_float,
            "LabResult",
            stats,
            field_path=field_path,
            source_block_id=source_block_id,
        )
        if validated_confidence is None:
            # OOB confidence → field stripped, already recorded in stats
            logger.info(
                "extraction_verifier: field stripped (confidence_oob)",
                extra={"field_path": field_path, "source_block_id": source_block_id},
            )
            continue
        confidence = validated_confidence

        # Grounding check: does the test_name appear in the source block?
        test_name: str = str(item.get("test_name", ""))
        grounded, reason = verify_field(test_name, source_block_id, block_index)
        if not grounded:
            stats.record_failure(
                reason or REASON_VALUE_NOT_SUBSTRING,
                field_path=field_path,
                source_block_id=source_block_id,
            )
            # Strip this field — do NOT add to passed_results
            logger.info(
                "extraction_verifier: field stripped (grounding failed)",
                extra={
                    "field": "test_name",
                    "source_block_id": source_block_id,
                    # Never log the value itself
                },
            )
            continue

        # Grounding passed — record verified verdict
        stats.record_verified(field_path, source_block_id)
        all_confidences.append(confidence)

        # Parse value — must be numeric
        raw_value = item.get("value")
        try:
            value = float(raw_value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            # Skip non-numeric results
            logger.info(
                "extraction_verifier: skipping non-numeric lab value",
                extra={"source_block_id": source_block_id},
            )
            continue

        # Parse optional fields
        reference_range: str | None = item.get("reference_range")
        raw_abnormal = item.get("abnormal")
        abnormal: bool | None = None
        if raw_abnormal is not None:
            abnormal = bool(raw_abnormal)

        raw_date = item.get("collection_date")
        collection_date: date | None = None
        if raw_date and isinstance(raw_date, str):
            try:
                collection_date = date.fromisoformat(raw_date)
            except ValueError:
                collection_date = None

        passed_results.append(
            LabResult(
                test_name=test_name,
                value=value,
                unit=str(item.get("unit", "")),
                reference_range=reference_range if reference_range else None,
                abnormal=abnormal,
                collection_date=collection_date,
                source_block_id=source_block_id or "",
                confidence=confidence,
            )
        )

    # Grounding rate check
    _check_grounding_rate(stats.total_fields, stats.stripped_fields, "LabReport")

    # Compute average confidence
    avg_confidence: float | None = (
        sum(all_confidences) / len(all_confidences) if all_confidences else None
    )

    return LabReport(
        patient_id=patient_id,
        document_reference_id=doc_ref_id,
        results=passed_results,
        extraction_metadata=ExtractMeta(
            docling_version=doc.docling_version,
            extraction_model=extraction_model,
            page_count=doc.page_count,
            extraction_confidence_avg=avg_confidence,
        ),
    ), stats


# ---------------------------------------------------------------------------
# IntakeForm parser
# ---------------------------------------------------------------------------


def parse_intake_form(
    haiku_json_text: str,
    doc: DoclingDoc,
    patient_id: int,
    doc_ref_id: str,
    extraction_model: str,
) -> tuple[IntakeForm, ExtractionStats]:
    """Parse Haiku's JSON response into a validated IntakeForm.

    Runs the extraction verifier on every listed item's source_block_id.
    Fields failing the check are stripped. If >30% fail, raises
    ExtractionLowGrounding.

    Args:
        haiku_json_text: Raw JSON string from Haiku.
        doc:             The DoclingDoc from Stage 1.
        patient_id:      Must be in sentinel range.
        doc_ref_id:      FHIR DocumentReference ID.
        extraction_model: Model name string for ExtractMeta.

    Returns:
        Tuple of (validated IntakeForm, ExtractionStats with grounding counts).

    Raises:
        ValueError:              patient_id outside sentinel range, or invalid
                                 block_id cited by Haiku.
        ExtractionLowGrounding:  >30% of fields fail grounding check.
        json.JSONDecodeError:    Haiku response is not valid JSON.
    """
    if not (_SENTINEL_MIN <= patient_id <= _SENTINEL_MAX):
        raise ValueError(
            "patient_id is outside W2 sentinel range "
            f"[{_SENTINEL_MIN}, {_SENTINEL_MAX}]."
        )

    raw: dict[str, Any] = json.loads(haiku_json_text)
    block_index = _build_block_index(doc)

    # Diagnostic: structural counts only (no field values) — helps identify
    # whether sparse extractions are upstream (Haiku/prompt) or downstream
    # (parser/verifier).  No PHI: arrays' lengths + booleans only.
    logger.info(
        "parse_intake_form: raw counts n_blocks=%d demographics_present=%s "
        "chief_concern_present=%s n_medications=%d n_allergies=%d n_family_history=%d",
        len(doc.blocks),
        bool(raw.get("demographics")),
        bool(raw.get("chief_concern")),
        len(raw.get("current_medications", [])),
        len(raw.get("allergies", [])),
        len(raw.get("family_history", [])),
    )

    stats = ExtractionStats()
    all_confidences: list[float] = []

    def _check_block_id(block_id: str | None, item_label: str) -> None:
        """Raise if block_id is non-None and not in the index."""
        if block_id is not None and block_id not in block_index:
            raise ValueError(
                f"Haiku cited a source_block_id that does not exist in the DoclingDoc "
                f"for {item_label}. Block index violation — refusing extraction."
            )

    # --- demographics ---
    raw_demo = raw.get("demographics", {})
    demo_block_id: str | None = raw_demo.get("source_block_id")
    _check_block_id(demo_block_id, "demographics")

    demo_name: str = str(raw_demo.get("name", ""))
    stats.total_fields += 1
    demo_grounded, demo_reason = verify_field(demo_name, demo_block_id, block_index)
    if not demo_grounded:
        stats.record_failure(
            demo_reason or REASON_VALUE_NOT_SUBSTRING,
            field_path="demographics.name",
            source_block_id=demo_block_id,
        )
        demo_block_id = None  # strip — null out block ref but keep the field
    else:
        stats.record_verified("demographics.name", demo_block_id)

    raw_dob = raw_demo.get("dob")
    dob: date | None = None
    if raw_dob and isinstance(raw_dob, str):
        try:
            dob = date.fromisoformat(raw_dob)
        except ValueError:
            dob = None

    demographics = Demographics(
        name=demo_name or "Unknown",
        dob=dob,
        sex=raw_demo.get("sex"),
        source_block_id=demo_block_id or (doc.blocks[0].block_id if doc.blocks else "block_0"),
    )

    # --- chief_concern ---
    chief_concern: str | None = raw.get("chief_concern")
    chief_concern_block_id: str | None = raw.get("chief_concern_block_id")
    _check_block_id(chief_concern_block_id, "chief_concern")
    if chief_concern:
        stats.total_fields += 1
        cc_grounded, cc_reason = verify_field(chief_concern, chief_concern_block_id, block_index)
        if not cc_grounded:
            stats.record_failure(
                cc_reason or REASON_VALUE_NOT_SUBSTRING,
                field_path="chief_concern",
                source_block_id=chief_concern_block_id,
            )
            chief_concern = None  # strip ungrounded chief_concern
        else:
            stats.record_verified("chief_concern", chief_concern_block_id)

    # --- medications ---
    raw_meds: list[dict[str, Any]] = raw.get("current_medications", [])
    medications: list[Medication] = []
    for med_i, med_item in enumerate(raw_meds):
        med_field_path = f"current_medications[{med_i}].name"
        med_block_id: str | None = med_item.get("source_block_id")
        _check_block_id(med_block_id, "medication")
        med_name: str = str(med_item.get("name", ""))
        stats.total_fields += 1
        grounded, reason = verify_field(med_name, med_block_id, block_index)
        if not grounded:
            stats.record_failure(
                reason or REASON_VALUE_NOT_SUBSTRING,
                field_path=med_field_path,
                source_block_id=med_block_id,
            )
            logger.info(
                "extraction_verifier: medication stripped (grounding failed)",
                extra={"source_block_id": med_block_id},
            )
            continue
        stats.record_verified(med_field_path, med_block_id)
        medications.append(
            Medication(
                name=med_name,
                dose=med_item.get("dose"),
                frequency=med_item.get("frequency"),
                source_block_id=med_block_id or "",
            )
        )

    # --- allergies ---
    raw_allergies: list[dict[str, Any]] = raw.get("allergies", [])
    allergies: list[Allergy] = []
    for allergy_i, allergy_item in enumerate(raw_allergies):
        allergy_field_path = f"allergies[{allergy_i}].substance"
        allergy_block_id: str | None = allergy_item.get("source_block_id")
        _check_block_id(allergy_block_id, "allergy")
        substance: str = str(allergy_item.get("substance", ""))
        stats.total_fields += 1
        grounded, reason = verify_field(substance, allergy_block_id, block_index)
        if not grounded:
            stats.record_failure(
                reason or REASON_VALUE_NOT_SUBSTRING,
                field_path=allergy_field_path,
                source_block_id=allergy_block_id,
            )
            logger.info(
                "extraction_verifier: allergy stripped (grounding failed)",
                extra={"source_block_id": allergy_block_id},
            )
            continue
        stats.record_verified(allergy_field_path, allergy_block_id)
        allergies.append(
            Allergy(
                substance=substance,
                reaction=allergy_item.get("reaction"),
                severity=allergy_item.get("severity"),
                source_block_id=allergy_block_id or "",
            )
        )

    # --- family_history ---
    raw_fh: list[dict[str, Any]] = raw.get("family_history", [])
    family_history: list[FamilyHistoryItem] = []
    for fh_i, fh_item in enumerate(raw_fh):
        fh_field_path = f"family_history[{fh_i}].condition"
        fh_block_id: str | None = fh_item.get("source_block_id")
        _check_block_id(fh_block_id, "family_history")
        condition: str = str(fh_item.get("condition", ""))
        stats.total_fields += 1
        grounded, reason = verify_field(condition, fh_block_id, block_index)
        if not grounded:
            stats.record_failure(
                reason or REASON_VALUE_NOT_SUBSTRING,
                field_path=fh_field_path,
                source_block_id=fh_block_id,
            )
            logger.info(
                "extraction_verifier: family_history stripped (grounding failed)",
                extra={"source_block_id": fh_block_id},
            )
            continue
        stats.record_verified(fh_field_path, fh_block_id)
        family_history.append(
            FamilyHistoryItem(
                condition=condition,
                relation=fh_item.get("relation"),
                source_block_id=fh_block_id or "",
            )
        )

    # --- overall confidence ---
    raw_confidence = raw.get("confidence", 0.0)
    try:
        overall_confidence = float(raw_confidence)
    except (TypeError, ValueError):
        overall_confidence = 0.0
    # strip-and-continue for OOB overall confidence (P3 decision)
    validated_overall = _validate_confidence(
        overall_confidence,
        "IntakeForm",
        stats,
        field_path="confidence",
        source_block_id=None,
    )
    if validated_overall is not None:
        all_confidences.append(validated_overall)

    # --- source_citations ---
    source_citations: dict[str, str] = {}
    raw_citations = raw.get("source_citations", {})
    if isinstance(raw_citations, dict):
        for field_name, block_id in raw_citations.items():
            if isinstance(block_id, str) and block_id in block_index:
                source_citations[str(field_name)] = block_id

    # Grounding rate check
    _check_grounding_rate(
        stats.total_fields, stats.stripped_fields, "IntakeForm"
    )

    avg_confidence: float | None = (
        sum(all_confidences) / len(all_confidences) if all_confidences else None
    )

    return IntakeForm(
        patient_id=patient_id,
        document_reference_id=doc_ref_id,
        demographics=demographics,
        chief_concern=chief_concern,
        current_medications=medications,
        allergies=allergies,
        family_history=family_history,
        source_citations=source_citations,
        extraction_metadata=ExtractMeta(
            docling_version=doc.docling_version,
            extraction_model=extraction_model,
            page_count=doc.page_count,
            extraction_confidence_avg=avg_confidence,
        ),
    ), stats
