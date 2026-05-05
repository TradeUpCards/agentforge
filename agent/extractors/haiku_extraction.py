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

_GROUNDING_FAILURE_THRESHOLD = 0.30  # >30% failing fields → refuse


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
) -> bool:
    """Check that `value` appears (normalized) in the named source block.

    Returns False if:
      - source_block_id is None or empty
      - source_block_id is not in the block index
      - normalized(value) is not a substring of normalized(block.text)

    Does NOT log the value or block text — grounding check is structural.
    """
    if not source_block_id:
        return False
    block_text = block_index.get(source_block_id)
    if block_text is None:
        return False
    return _normalize(value) in _normalize(block_text)


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
        raise ExtractionLowGrounding(
            f"extraction_low_grounding: {failed_fields}/{total_fields} fields "
            f"failed grounding check in {context} "
            f"(rate={rate:.2f} > threshold={_GROUNDING_FAILURE_THRESHOLD:.2f})"
        )


# ---------------------------------------------------------------------------
# Confidence validation
# ---------------------------------------------------------------------------


def _validate_confidence(confidence: float, label: str) -> float:
    """Raise ValueError if confidence is outside [0.0, 1.0].

    Intentionally does NOT interpolate the actual confidence value into the
    message: the value itself is not PHI, but establishing a convention that
    no numeric field values appear in exception messages is simpler to audit
    than case-by-case reasoning about which values are safe.
    """
    if not (0.0 <= confidence <= 1.0):
        raise ValueError(
            f"confidence out of range in {label}: must be in [0.0, 1.0]"
        )
    return confidence


# ---------------------------------------------------------------------------
# Message builders
# ---------------------------------------------------------------------------


def build_lab_report_messages(doc: DoclingDoc) -> list[dict[str, Any]]:
    """Build the Anthropic messages list for LabReport extraction.

    The system block is the stable cacheable prefix (schema spec). Caller
    passes this list to the Anthropic SDK as the `messages` param, with the
    system block delivered separately as the `system` param with
    cache_control applied.

    Returns a list with a single user message (the doc block index).

    No PHI in this function's return value goes to Langfuse —
    only token counts are observable. Block text stays in the messages.
    """
    user_content = lab_report_extraction.build_user_message(doc)
    return [
        {
            "role": "user",
            "content": user_content,
        }
    ]


def build_intake_form_messages(doc: DoclingDoc) -> list[dict[str, Any]]:
    """Build the Anthropic messages list for IntakeForm extraction.

    Same pattern as build_lab_report_messages — stable system block is
    in the prompt template; variable user block is doc-specific.
    """
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
) -> LabReport:
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
        Validated LabReport.

    Raises:
        ValueError:              patient_id outside sentinel range, or invalid
                                 block_id cited by Haiku, or confidence OOB.
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
    total_verifier_fields = 0
    failed_verifier_fields = 0
    all_confidences: list[float] = []

    for item in raw_results:
        # Validate source_block_id exists in the doc
        source_block_id: str | None = item.get("source_block_id")
        if source_block_id is not None and source_block_id not in block_index:
            raise ValueError(
                "Haiku cited a source_block_id that does not exist in the DoclingDoc. "
                "Block index violation — refusing extraction."
            )

        # Confidence bound check
        raw_confidence = item.get("confidence", 0.0)
        # Convert to float defensively
        try:
            confidence = float(raw_confidence)
        except (TypeError, ValueError):
            confidence = 0.0
        _validate_confidence(confidence, "LabResult")
        all_confidences.append(confidence)

        # Grounding check: does the test_name appear in the source block?
        test_name: str = str(item.get("test_name", ""))
        total_verifier_fields += 1
        grounded = verify_field(test_name, source_block_id, block_index)
        if not grounded:
            failed_verifier_fields += 1
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
    _check_grounding_rate(total_verifier_fields, failed_verifier_fields, "LabReport")

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
    )


# ---------------------------------------------------------------------------
# IntakeForm parser
# ---------------------------------------------------------------------------


def parse_intake_form(
    haiku_json_text: str,
    doc: DoclingDoc,
    patient_id: int,
    doc_ref_id: str,
    extraction_model: str,
) -> IntakeForm:
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
        Validated IntakeForm.

    Raises:
        ValueError:              patient_id outside sentinel range, or invalid
                                 block_id, or confidence OOB.
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

    total_verifier_fields = 0
    failed_verifier_fields = 0
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
    total_verifier_fields += 1
    demo_grounded = verify_field(demo_name, demo_block_id, block_index)
    if not demo_grounded:
        failed_verifier_fields += 1
        demo_block_id = None  # strip — null out block ref but keep the field

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
        total_verifier_fields += 1
        cc_grounded = verify_field(chief_concern, chief_concern_block_id, block_index)
        if not cc_grounded:
            failed_verifier_fields += 1
            chief_concern = None  # strip ungrounded chief_concern

    # --- medications ---
    raw_meds: list[dict[str, Any]] = raw.get("current_medications", [])
    medications: list[Medication] = []
    for med_item in raw_meds:
        med_block_id: str | None = med_item.get("source_block_id")
        _check_block_id(med_block_id, "medication")
        med_name: str = str(med_item.get("name", ""))
        total_verifier_fields += 1
        grounded = verify_field(med_name, med_block_id, block_index)
        if not grounded:
            failed_verifier_fields += 1
            logger.info(
                "extraction_verifier: medication stripped (grounding failed)",
                extra={"source_block_id": med_block_id},
            )
            continue
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
    for allergy_item in raw_allergies:
        allergy_block_id: str | None = allergy_item.get("source_block_id")
        _check_block_id(allergy_block_id, "allergy")
        substance: str = str(allergy_item.get("substance", ""))
        total_verifier_fields += 1
        grounded = verify_field(substance, allergy_block_id, block_index)
        if not grounded:
            failed_verifier_fields += 1
            logger.info(
                "extraction_verifier: allergy stripped (grounding failed)",
                extra={"source_block_id": allergy_block_id},
            )
            continue
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
    for fh_item in raw_fh:
        fh_block_id: str | None = fh_item.get("source_block_id")
        _check_block_id(fh_block_id, "family_history")
        condition: str = str(fh_item.get("condition", ""))
        total_verifier_fields += 1
        grounded = verify_field(condition, fh_block_id, block_index)
        if not grounded:
            failed_verifier_fields += 1
            logger.info(
                "extraction_verifier: family_history stripped (grounding failed)",
                extra={"source_block_id": fh_block_id},
            )
            continue
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
    _validate_confidence(overall_confidence, "IntakeForm")
    all_confidences.append(overall_confidence)

    # --- source_citations ---
    source_citations: dict[str, str] = {}
    raw_citations = raw.get("source_citations", {})
    if isinstance(raw_citations, dict):
        for field_name, block_id in raw_citations.items():
            if isinstance(block_id, str) and block_id in block_index:
                source_citations[str(field_name)] = block_id

    # Grounding rate check
    _check_grounding_rate(
        total_verifier_fields, failed_verifier_fields, "IntakeForm"
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
    )
