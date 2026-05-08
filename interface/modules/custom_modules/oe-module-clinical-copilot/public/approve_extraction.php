<?php

/**
 * Clinical Co-Pilot approve-extraction endpoint.
 *
 * AJAX target for the HITL approve action (P4 R1).  Bootstraps OpenEMR globals
 * (so the session, ACL, and CSRF helpers are wired up) and delegates the
 * request to `CoPilotController::handleApprove()`.
 *
 * Logical URL:
 *   {webroot}/interface/modules/custom_modules/oe-module-clinical-copilot/public/approve_extraction.php
 *
 * Expected request: POST with Content-Type: application/json
 *   Body: {"extraction_id": <int>}
 *   Headers: X-CSRF-Token: <token>
 *
 * Security controls are enforced inside the controller:
 *   - OpenEMR session authentication
 *   - ACL: aclCheckCore('patients', 'med')
 *   - CSRF token verification (X-CSRF-Token header)
 *   - Patient-id horizontal-escalation check
 *   - is_active = 1 guard on the extraction row
 *   - status = pending_review guard (422 for wrong state)
 *
 * Success response (200):
 *   {"status": "approved", "extraction_id": <int>,
 *    "observations": <int>, "allergies": <int>, "medications": <int>}
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

(new CoPilotController())->handleApprove();
