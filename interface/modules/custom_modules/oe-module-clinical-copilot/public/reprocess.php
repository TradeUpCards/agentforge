<?php

/**
 * Clinical Co-Pilot reprocess endpoint.
 *
 * AJAX target for the HITL reprocess action.  Bootstraps OpenEMR globals
 * (so the session, ACL, and CSRF helpers are wired up) and delegates the
 * request to `CoPilotController::handleReprocess()`.
 *
 * Logical URL:
 *   {webroot}/interface/modules/custom_modules/oe-module-clinical-copilot/public/reprocess.php
 *
 * Expected request: POST with Content-Type: application/json
 *   Body:    {
 *               "extraction_id":       <int>,         // required
 *               "discard_manual_edits": <bool>        // optional; default false
 *            }
 *   Headers: X-CSRF-Token: <token>
 *
 * P4 R2 CONTRACT CHANGE (closes R1 reprocess-bypasses-gate hole):
 *   The FHIR round-trip is NO LONGER run synchronously on reprocess.
 *   The new agent attempt is now persisted with status='pending_review'.
 *   The clinician must approve/reject via the existing approve_extraction.php /
 *   reject_extraction.php endpoints before any clinical-table writes occur.
 *
 *   Success 200 response shape (changed from R1):
 *     {
 *       "status":            "pending_review",   // was the raw agent status in R1
 *       "new_extraction_id": <int>,
 *       "total_fields":      <int|null>,
 *       "stripped_fields":   <int|null>
 *     }
 *
 *   When the agent returns 'refused' or 'error', status carries those values
 *   unchanged (no approve gate applies — there is nothing to round-trip).
 *
 *   discard_manual_edits (default false):
 *     When false, manually_edited / manually_added field rows from the prior
 *     attempt are copied into the new attempt, preserving clinician overrides.
 *     When true, the new attempt contains only LLM-extracted fields.
 *
 * Security controls are enforced inside the controller:
 *   - OpenEMR session authentication
 *   - ACL: aclCheckCore('patients', 'med')
 *   - CSRF token verification (X-CSRF-Token header)
 *   - Patient-id horizontal-escalation check
 *   - is_active = 1 guard on the extraction row
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    AgentForge Team
 * @copyright Copyright (c) 2026 AgentForge
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

// Bootstrap OpenEMR's session, ACL, and CSRF helpers.
require_once __DIR__ . '/../../../../globals.php';

use OpenEMR\Modules\ClinicalCopilot\Controller\CoPilotController;

(new CoPilotController())->handleReprocess();
