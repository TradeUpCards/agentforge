<?php

/**
 * Canonical status values for the Co-Pilot extraction tables.
 *
 * This class holds TWO distinct sets of status constants:
 *
 * ── Extraction-row statuses (co_pilot_extractions.status VARCHAR 16) ────────
 *   Use these constants everywhere; never scatter raw string literals.
 *
 *   Lifecycle:
 *     (upload)       → pending_review   (new in P4 R1; replaces auto-roundtrip)
 *     (approve)      → approved         (new in P4 R1; roundtrip runs on approve)
 *     (reject)       → rejected         (new in P4 R1; terminal, no roundtrip)
 *     (agent error)  → error            (pre-existing)
 *     (agent refuse) → refused          (pre-existing; >30% strip rate)
 *     (legacy ok)    → ok               (pre-existing; kept for backward compat)
 *
 *   TODO P4 R3: add under_correction when "Reopen for review" is implemented.
 *
 * ── Field-row statuses (co_pilot_extracted_fields.status VARCHAR 16) ────────
 *   These track the per-field verdict + any clinician override.  They are
 *   DISTINCT from the extraction-row statuses above — do not confuse them.
 *
 *   Values:
 *     verified        — field passed the verifier; has a bbox citation
 *     stripped        — field rejected by the verifier; no bbox citation
 *     manually_edited — clinician edited an existing verified field value
 *                       (pencil-edit affordance); clinician_value holds the
 *                       asserted text; original LLM value preserved in
 *                       extraction_json (audit trail)
 *     manually_added  — clinician added a field not extracted by the LLM
 *                       (assert-from-block affordance); clinician_value holds
 *                       the asserted text; source_block_id links to the chosen
 *                       Docling block
 *
 *   PHI note: manually_edited / manually_added rows carry clinician-asserted
 *   values in co_pilot_extracted_fields.clinician_value.  That column MUST NOT
 *   be echoed into logs, error responses, or non-PHI-cleared paths.  Only
 *   structural metadata (field_path, length, extraction_id) may be logged.
 *
 * Decision (P4 R2): the two sets are kept in this single class (not split into
 * ExtractionStatus + FieldRowStatus) to minimize downstream changes.  The dual
 * purpose is clearly documented with section headings and PHPDoc.  If a future
 * phase adds enough field-row constants to justify a split, extract them then.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    AgentForge Team
 * @copyright Copyright (c) 2026 AgentForge
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot;

/**
 * Status constants for co_pilot_extractions.status and co_pilot_extracted_fields.status.
 *
 * See file docblock above for the full lifecycle description and PHI guidance.
 */
final class ExtractionStatus
{
    // =========================================================================
    // co_pilot_extractions.status constants  (VARCHAR 16)
    // =========================================================================

    /** Extraction succeeded; awaiting clinician review before round-trip. */
    public const PENDING_REVIEW = 'pending_review';

    /** Clinician approved; round-trip has been run. */
    public const APPROVED = 'approved';

    /** Clinician rejected; terminal — no round-trip will ever run. */
    public const REJECTED = 'rejected';

    /**
     * Agent reported an internal error.  Terminal for round-trip purposes;
     * a new extraction (via reprocess) is required.
     */
    public const ERROR = 'error';

    /**
     * Agent refused to extract (>30 % strip rate after all ladder attempts).
     * Terminal for round-trip; reprocess is required.
     */
    public const REFUSED = 'refused';

    /**
     * Legacy status written by pre-P4 code when roundtrip was synchronous.
     * Still present on rows from before P4 R1 merged.  Treat as "already
     * round-tripped" — equivalent to APPROVED from an approval-gate
     * perspective.
     */
    public const OK = 'ok';

    // TODO P4 R3: UNDER_CORRECTION = 'under_correction'
    //   Used when a clinician reopens an APPROVED extraction for post-approval
    //   correction.  Requires clinician+admin ACL gate (locked policy decision
    //   from P4+P5 planning session 2026-05-08).

    // =========================================================================
    // co_pilot_extracted_fields.status constants  (VARCHAR 16)
    // =========================================================================

    /**
     * Field passed the agent verifier; a bounding-box citation exists.
     * Written by ExtractedFieldsWriter::writeVerified().
     */
    public const FIELD_VERIFIED = 'verified';

    /**
     * Field was rejected by the agent verifier; no bounding-box citation.
     * Written by ExtractedFieldsWriter::writeStripped().
     */
    public const FIELD_STRIPPED = 'stripped';

    /**
     * Clinician edited an existing verified field value (pencil-edit UI).
     * The original LLM-extracted value is preserved in extraction_json;
     * the clinician's override is in co_pilot_extracted_fields.clinician_value.
     *
     * PHI rule: clinician_value MUST NOT be logged or echoed outside the
     * splice/round-trip path.
     *
     * VARCHAR length: 14 chars — fits within the VARCHAR(16) status column.
     */
    public const FIELD_MANUALLY_EDITED = 'manually_edited';

    /**
     * Clinician added a field not extracted by the LLM (assert-from-block UI).
     * The clinician's value is in co_pilot_extracted_fields.clinician_value;
     * source_block_id links to the chosen Docling block.
     *
     * PHI rule: same as FIELD_MANUALLY_EDITED above.
     *
     * VARCHAR length: 14 chars — fits within the VARCHAR(16) status column.
     */
    public const FIELD_MANUALLY_ADDED = 'manually_added';

    // =========================================================================
    // Extraction-row transition guards
    // =========================================================================

    /**
     * Returns the set of status values from which an approve transition is
     * valid.  Anything outside this set must return HTTP 422.
     *
     * @return list<string>
     */
    public static function approvableStatuses(): array
    {
        return [self::PENDING_REVIEW];
    }

    /**
     * Returns the set of status values from which a reject transition is
     * valid.  Terminal states (error, refused, rejected) are not re-rejectable.
     *
     * @return list<string>
     */
    public static function rejectableStatuses(): array
    {
        return [self::PENDING_REVIEW];
    }

    /**
     * Returns the set of co_pilot_extracted_fields.status values that indicate
     * a clinician override is present and its clinician_value must be spliced
     * into the extraction object before the round-trip runs.
     *
     * @return list<string>
     */
    public static function clinicianOverrideStatuses(): array
    {
        return [self::FIELD_MANUALLY_EDITED, self::FIELD_MANUALLY_ADDED];
    }
}
