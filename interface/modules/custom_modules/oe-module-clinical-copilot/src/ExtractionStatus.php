<?php

/**
 * Canonical status values for co_pilot_extractions.status.
 *
 * Use these constants everywhere; never scatter raw string literals.
 *
 * Lifecycle:
 *   (upload)       → pending_review   (new in P4 R1; replaces auto-roundtrip)
 *   (approve)      → approved         (new in P4 R1; roundtrip runs on approve)
 *   (reject)       → rejected         (new in P4 R1; terminal, no roundtrip)
 *   (agent error)  → error            (pre-existing)
 *   (agent refuse) → refused          (pre-existing; >30% strip rate)
 *   (legacy ok)    → ok               (pre-existing; kept for backward compat)
 *
 * TODO P4 R3: add under_correction when "Reopen for review" is implemented.
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
 * Closed set of status values for co_pilot_extractions.status (VARCHAR 16).
 *
 * Terminal states (no further transition possible):
 *   - REJECTED
 *   - ERROR
 *   - REFUSED
 *
 * Non-terminal states that can progress to APPROVED or REJECTED:
 *   - PENDING_REVIEW
 *
 * Legacy state (pre-P4; extraction was round-tripped synchronously):
 *   - OK
 *
 * Under-correction state (deferred to P4 R3):
 *   - UNDER_CORRECTION (not yet used; defined here for forward reference)
 */
final class ExtractionStatus
{
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
}
