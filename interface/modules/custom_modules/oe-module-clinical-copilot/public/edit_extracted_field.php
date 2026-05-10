<?php

/**
 * Clinical Co-Pilot — edit extracted field endpoint.
 *
 * Handles two operations (distinguished by whether the field_path already has
 * a row in co_pilot_extracted_fields for this extraction):
 *
 *   PUT-like EDIT  — update an existing field row with a clinician value.
 *     Sets status='manually_edited', writes clinician_value,
 *     optionally updates source_block_id.
 *
 *   POST-like ADD  — insert a new field row that the LLM did not extract.
 *     Sets status='manually_added', writes clinician_value,
 *     requires source_block_id (the chosen Docling block for provenance).
 *     If a manually_added row already exists at the same field_path, the
 *     row is overwritten (idempotent second-add = overwrite).
 *
 * The operation is selected automatically:
 *   - If a co_pilot_extracted_fields row already exists for (extraction_id,
 *     field_path) with any status EXCEPT manually_added → EDIT path.
 *   - If a manually_added row already exists → overwrite (treat as EDIT).
 *   - If no row exists → ADD path (source_block_id becomes required).
 *
 * Logical URL:
 *   {webroot}/interface/modules/custom_modules/oe-module-clinical-copilot/
 *   public/edit_extracted_field.php
 *
 * Expected request: POST with Content-Type: application/json
 *   Body:
 *     {
 *       "extraction_id":   <int>,     // required
 *       "field_path":      <string>,  // required; e.g. "results[0].test_name"
 *       "clinician_value": <string>,  // required; the asserted value (PHI)
 *       "source_block_id": <string>   // required for ADD; optional for EDIT
 *     }
 *   Headers: X-CSRF-Token: <token>
 *
 * Success 200 response:
 *   {
 *     "status":       "manually_edited" | "manually_added",
 *     "field_row_id": <int>
 *   }
 *
 * Error codes:
 *   400 — missing/invalid fields
 *   401 — not authenticated
 *   403 — ACL or CSRF failure, or patient-id mismatch
 *   404 — extraction not found / not active
 *   405 — not POST
 *   413 — body > 1 MiB
 *   422 — extraction not in pending_review state (edits only allowed
 *          before approval; post-approval correction is P4 R3)
 *   500 — internal error
 *
 * PHI custody:
 *   clinician_value is PHI-classified.  It is written to
 *   co_pilot_extracted_fields.clinician_value and MUST NOT appear in:
 *     - PSR-3 log context
 *     - error response bodies
 *     - non-PHI-cleared code paths
 *   Only structural metadata (field_path, extraction_id, field_row_id,
 *   operation, clinician_value_length) may be logged.
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

use OpenEMR\Common\Acl\AclMain;
use OpenEMR\Common\Csrf\CsrfUtils;
use OpenEMR\Common\Database\QueryUtils;
use OpenEMR\Common\Logging\SystemLogger;
use OpenEMR\Common\Session\SessionWrapperFactory;
use OpenEMR\Modules\ClinicalCopilot\ExtractionStatus;
use OpenEMR\Modules\ClinicalCopilot\Service\PersonaMap;
use Throwable;

header('Content-Type: application/json');

$logger = new SystemLogger();

const MAX_REQUEST_BYTES = 1_048_576; // 1 MiB cap, mirrors CoPilotController.

try {
    dispatchEditExtractedField($logger);
} catch (Throwable $e) {
    $logger->error('ClinicalCopilot/edit_extracted_field: handler failed', [
        'exception_class' => get_class($e),
        'file'            => basename($e->getFile()),
        'line'            => $e->getLine(),
    ]);
    http_response_code(500);
    echo json_encode([
        'status' => 'error',
        'detail' => 'The server encountered an internal error. Please retry.',
    ]);
}

/**
 * Core dispatch logic for the edit-extracted-field endpoint.
 * Separated from the top-level try/catch so PHPStan can narrow types cleanly.
 */
function dispatchEditExtractedField(SystemLogger $logger): void
{
    // 1. Session check.
    $session    = SessionWrapperFactory::getInstance()->getActiveSession();
    $userId     = (int) ($session->get('authUserID') ?? 0);
    $sessionPid = (int) ($session->get('pid') ?? 0);

    if ($userId <= 0) {
        http_response_code(401);
        echo json_encode(['status' => 'error', 'detail' => 'Authentication required.']);
        return;
    }

    // 2. ACL — same gate as approve/reject.
    if (!AclMain::aclCheckCore('patients', 'med')) {
        http_response_code(403);
        echo json_encode(['status' => 'error', 'detail' => 'forbidden']);
        return;
    }

    // 3. Method.
    if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST') {
        http_response_code(405);
        echo json_encode(['status' => 'error', 'detail' => 'Method not allowed.']);
        return;
    }

    // 4. CSRF.
    $csrfToken = $_SERVER['HTTP_X_CSRF_TOKEN'] ?? '';
    if (!is_string($csrfToken) || !CsrfUtils::verifyCsrfToken($csrfToken, $session)) {
        http_response_code(403);
        echo json_encode(['status' => 'error', 'detail' => 'Invalid CSRF token.']);
        return;
    }

    // 5. Patient context.
    if ($sessionPid <= 0) {
        http_response_code(400);
        echo json_encode(['status' => 'error', 'detail' => 'No active patient in session.']);
        return;
    }

    // 6. Parse body.
    $rawBody = (string) file_get_contents('php://input', false, null, 0, MAX_REQUEST_BYTES + 1);
    if (strlen($rawBody) > MAX_REQUEST_BYTES) {
        http_response_code(413);
        echo json_encode(['status' => 'error', 'detail' => 'Request payload too large.']);
        return;
    }

    $decoded = json_decode($rawBody, true);
    if (!is_array($decoded)) {
        http_response_code(400);
        echo json_encode(['status' => 'error', 'detail' => 'Invalid JSON body.']);
        return;
    }

    // 7. Validate required fields.
    $extractionIdRaw  = $decoded['extraction_id'] ?? null;
    $fieldPathRaw     = $decoded['field_path'] ?? null;
    $clinicianValueRaw = $decoded['clinician_value'] ?? null;
    $sourceBlockIdRaw = $decoded['source_block_id'] ?? null;

    if (!is_int($extractionIdRaw) || $extractionIdRaw <= 0) {
        http_response_code(400);
        echo json_encode(['status' => 'error', 'detail' => 'extraction_id must be a positive integer.']);
        return;
    }

    if (!is_string($fieldPathRaw) || $fieldPathRaw === '') {
        http_response_code(400);
        echo json_encode(['status' => 'error', 'detail' => 'field_path must be a non-empty string.']);
        return;
    }

    if (!is_string($clinicianValueRaw)) {
        http_response_code(400);
        echo json_encode(['status' => 'error', 'detail' => 'clinician_value must be a string.']);
        return;
    }

    $extractionId  = $extractionIdRaw;
    $fieldPath     = $fieldPathRaw;
    $clinicianValue = $clinicianValueRaw; // PHI — never log this value
    $sourceBlockId = (is_string($sourceBlockIdRaw) && $sourceBlockIdRaw !== '')
        ? $sourceBlockIdRaw
        : null;

    // 8. Load extraction row (active only) — verify it exists and belongs to the session patient.
    $extractionRows = QueryUtils::fetchRecords(
        'SELECT `id`, `patient_id`, `status`
           FROM `co_pilot_extractions`
          WHERE `id` = ?
            AND `is_active` = 1
          LIMIT 1',
        [$extractionId],
    );

    if ($extractionRows === []) {
        http_response_code(404);
        echo json_encode(['status' => 'error', 'detail' => 'Extraction not found or not active.']);
        return;
    }

    $extractionRow = $extractionRows[0];
    $storedPid     = (int) $extractionRow['patient_id'];
    $extractionStatus = (string) ($extractionRow['status'] ?? '');

    // 9. Patient-id horizontal-escalation check (mirrors approve/reject pattern).
    $expectedSentinelPid = PersonaMap::sentinelId($sessionPid);
    if ($expectedSentinelPid === null || $storedPid !== $expectedSentinelPid) {
        $logger->warning('ClinicalCopilot/edit_extracted_field: patient_id mismatch — possible horizontal escalation', [
            'extraction_id' => $extractionId,
        ]);
        http_response_code(403);
        echo json_encode(['status' => 'error', 'detail' => 'Patient context mismatch.']);
        return;
    }

    // 10. State guard: edits are only valid while the extraction is in
    //     pending_review.  Post-approval correction (R3 under_correction) is
    //     a separate gate added in P4 R3.
    if (!in_array($extractionStatus, ExtractionStatus::approvableStatuses(), true)) {
        http_response_code(422);
        echo json_encode([
            'status'         => 'error',
            'detail'         => 'Field edits are only allowed while the extraction is pending_review.',
            'current_status' => $extractionStatus,
        ]);
        return;
    }

    // 11. Look up existing field row for this (extraction_id, field_path).
    $existingFieldRows = QueryUtils::fetchRecords(
        'SELECT `id`, `status`
           FROM `co_pilot_extracted_fields`
          WHERE `extraction_id` = ?
            AND `field_path`    = ?
          LIMIT 1',
        [$extractionId, $fieldPath],
    );

    // 12. Determine operation and execute.
    //
    //     EDIT path: a field row exists (any status, including manually_added).
    //       → UPDATE status='manually_edited', clinician_value=<value>,
    //               source_block_id=<provided or existing> .
    //
    //     ADD path: no field row exists.
    //       → source_block_id is required (422 if absent).
    //       → INSERT with status='manually_added'.
    //
    //     PHI rule: $clinicianValue is written to the DB only; never echoed in
    //     logs or responses.  Only structural metadata (field_row_id, operation,
    //     clinician_value_length) appears in log context.

    if ($existingFieldRows !== []) {
        // EDIT path — overwrite with clinician value.
        $fieldRowId       = (int) $existingFieldRows[0]['id'];
        $newStatus        = ExtractionStatus::FIELD_MANUALLY_EDITED;

        $updateParams = [$newStatus, $clinicianValue];
        $setSql       = '`status` = ?, `clinician_value` = ?';

        // Update source_block_id if the caller supplied one.
        if ($sourceBlockId !== null) {
            $setSql        .= ', `source_block_id` = ?';
            $updateParams[] = $sourceBlockId;
        }

        $updateParams[] = $fieldRowId;

        QueryUtils::sqlStatementThrowException(
            'UPDATE `co_pilot_extracted_fields`
                SET ' . $setSql . '
              WHERE `id` = ?',
            $updateParams,
        );

        $logger->info('ClinicalCopilot/edit_extracted_field: field edited', [
            'extraction_id'          => $extractionId,
            'field_path'             => $fieldPath,
            'field_row_id'           => $fieldRowId,
            'operation'              => 'edit',
            'clinician_value_length' => strlen($clinicianValue),
        ]);

        http_response_code(200);
        echo json_encode([
            'status'       => $newStatus,
            'field_row_id' => $fieldRowId,
        ]);
        return;
    }

    // ADD path — no existing row.
    if ($sourceBlockId === null) {
        http_response_code(422);
        echo json_encode([
            'status' => 'error',
            'detail' => 'source_block_id is required when adding a new field (no existing row for this field_path).',
        ]);
        return;
    }

    $newStatus  = ExtractionStatus::FIELD_MANUALLY_ADDED;
    $fieldRowId = (int) QueryUtils::sqlInsert(
        'INSERT INTO `co_pilot_extracted_fields`
            (`extraction_id`, `field_path`, `status`, `source_block_id`, `clinician_value`)
         VALUES (?, ?, ?, ?, ?)',
        [$extractionId, $fieldPath, $newStatus, $sourceBlockId, $clinicianValue],
    );

    $logger->info('ClinicalCopilot/edit_extracted_field: field added', [
        'extraction_id'          => $extractionId,
        'field_path'             => $fieldPath,
        'field_row_id'           => $fieldRowId,
        'operation'              => 'add',
        'clinician_value_length' => strlen($clinicianValue),
    ]);

    http_response_code(200);
    echo json_encode([
        'status'       => $newStatus,
        'field_row_id' => $fieldRowId,
    ]);
}
