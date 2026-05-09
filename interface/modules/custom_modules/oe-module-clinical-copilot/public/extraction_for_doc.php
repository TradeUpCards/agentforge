<?php

/**
 * GET endpoint: return extraction summary + field verdicts for a document.
 *
 * Used by the HITL UI banner and modal to display extraction status and
 * per-field verification results with bounding-box citations.
 *
 * Logical URL:
 *   GET {webroot}/interface/modules/custom_modules/oe-module-clinical-copilot/public/extraction_for_doc.php?doc_id={id}
 *
 * Response 200 — extraction exists:
 * {
 *   "extraction_id": 42,
 *   "status": "ok",
 *   "doc_type": "lab_pdf",
 *   "total_fields": 7,
 *   "stripped_fields": 2,
 *   "attempt_n": 2,
 *   "triggered_by": "auto_retry",
 *   "fields": [
 *     {
 *       "field_path": "results[0].test_name",
 *       "status": "verified",
 *       "source_block_id": "blk_4",
 *       "bbox_json": {"x":72.0,"y":540.0,"w":240.0,"h":18.0},
 *       "verifier_reason": null,
 *       "value": "Cholesterol, Total",     // ONLY for status=verified (post-approve)
 *       "clinical_table": "procedure_result",
 *       "clinical_row_id": 1234
 *     },
 *     {
 *       "field_path": "allergies[0].substance",
 *       "status": "manually_edited",        // R2: clinician edited the LLM value
 *       "source_block_id": "blk_2",
 *       "bbox_json": null,
 *       "verifier_reason": null,
 *       "value": "Penicillin",              // LLM-verified value (only if clinical_row_id set, i.e. post-approve)
 *       "clinician_value": "Penicillin G",  // clinician-asserted value (always present for manually_edited)
 *       "clinical_table": "lists",
 *       "clinical_row_id": 88              // null if pre-approve
 *     },
 *     {
 *       "field_path": "allergies[1].substance",
 *       "status": "manually_added",         // R2: clinician-asserted row; no LLM value
 *       "source_block_id": null,
 *       "bbox_json": null,
 *       "verifier_reason": null,
 *       "clinician_value": "Aspirin",       // clinician-asserted value (always present for manually_added)
 *       "clinical_table": null,
 *       "clinical_row_id": null            // null if pre-approve
 *     },
 *     ...
 *   ],
 *   "docling_blocks": [...]
 * }
 *
 * Response 404 — no extraction for doc_id:
 * {"error":"no_extraction"}
 *
 * PHI custody rule (PRD §3):
 *   - For status=verified fields, the "value" is sourced from the clinical
 *     table row (NOT from extraction_json), so what the UI sees is what was
 *     actually persisted in OpenEMR — no unverified agent output shown.
 *   - For status=manually_edited fields, "clinician_value" (always present) is
 *     the clinician-asserted value; "value" (the LLM-side verified value) is
 *     included only if clinical_row_id is set (post-approve).
 *   - For status=manually_added fields, only "clinician_value" is present; no
 *     "value" field (there is no LLM-side value for a clinician-added row).
 *   - For status=stripped fields, no "value" and no "clinician_value" is included.
 *   - The raw extraction_json column is never returned to the browser.
 *   - docling_blocks text_snippet is included (it is document layout text,
 *     not agent-extracted field values).
 *
 * Security controls:
 *   - OpenEMR session authentication (userId from session).
 *   - ACL: aclCheckCore('patients', 'med').
 *   - No CSRF required for GET (read-only, idempotent).
 *
 * NO-PHI LOGGING:
 *   Only doc_id and extraction_id appear in logs; no field values, no
 *   patient names, no extracted content.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    AgentForge Team
 * @copyright Copyright (c) 2026 AgentForge
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

require_once __DIR__ . '/../../../../globals.php';

use OpenEMR\Common\Acl\AclMain;
use OpenEMR\Common\Database\QueryUtils;
use OpenEMR\Common\Logging\SystemLogger;
use OpenEMR\Common\Session\SessionWrapperFactory;

header('Content-Type: application/json');

$logger = new SystemLogger();

// 1. Session check.
$session = SessionWrapperFactory::getInstance()->getActiveSession();
$userId  = (int) ($session->get('authUserID') ?? 0);

if ($userId <= 0) {
    http_response_code(401);
    echo json_encode(['error' => 'unauthenticated']);
    exit;
}

// 2. ACL.
if (!AclMain::aclCheckCore('patients', 'med')) {
    http_response_code(403);
    echo json_encode(['error' => 'forbidden']);
    exit;
}

// 3. Method.
if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'GET') {
    http_response_code(405);
    echo json_encode(['error' => 'method_not_allowed']);
    exit;
}

// 4. Parse doc_id.
$docIdRaw = $_GET['doc_id'] ?? null;
if (!is_string($docIdRaw) || !ctype_digit($docIdRaw) || (int) $docIdRaw <= 0) {
    http_response_code(400);
    echo json_encode(['error' => 'invalid_doc_id']);
    exit;
}
$docId = (int) $docIdRaw;

// 5. Load the active extraction for this document.
//    `model` and `max_attempts` are surfaced for the HITL review modal header
//    ("attempt N of MAX, model X").  `max_attempts` is a constant derived from
//    the agent's retry-ladder length (Haiku-default → Haiku-verbatim → Sonnet
//    = 3 attempts as of P2/P3); kept as a literal here to avoid coupling the
//    PHP layer to the Python-side ladder definition.
$extractionRows = QueryUtils::fetchRecords(
    'SELECT `id`, `status`, `doc_type`, `total_fields`, `stripped_fields`,
            `attempt_n`, `triggered_by`, `model`, `docling_blocks_json`
       FROM `co_pilot_extractions`
      WHERE `doc_ref_id` = ?
        AND `is_active` = 1
      LIMIT 1',
    [(string) $docId],
);

if ($extractionRows === []) {
    http_response_code(404);
    echo json_encode(['error' => 'no_extraction']);
    exit;
}

$ext          = $extractionRows[0];
$extractionId = (int) $ext['id'];

// 6. Load field verdicts.
$fieldRows = QueryUtils::fetchRecords(
    'SELECT `id`, `field_path`, `status`, `source_block_id`,
            `bbox_json`, `verifier_reason`,
            `clinical_table`, `clinical_row_id`,
            `clinician_value`
       FROM `co_pilot_extracted_fields`
      WHERE `extraction_id` = ?
      ORDER BY `id` ASC',
    [$extractionId],
);

// 7. Build field list, fetching clinical values for verified rows.
$fields = [];
foreach ($fieldRows as $fieldRow) {
    $fieldStatus   = (string) $fieldRow['status'];
    $clinicalTable = isset($fieldRow['clinical_table']) && $fieldRow['clinical_table'] !== ''
        ? (string) $fieldRow['clinical_table']
        : null;
    $clinicalRowId = isset($fieldRow['clinical_row_id']) && $fieldRow['clinical_row_id'] !== null
        ? (int) $fieldRow['clinical_row_id']
        : null;

    $bboxDecoded = null;
    if ($fieldRow['bbox_json'] !== null && $fieldRow['bbox_json'] !== '') {
        $decoded = json_decode((string) $fieldRow['bbox_json'], true);
        if (is_array($decoded)) {
            $bboxDecoded = $decoded;
        }
    }

    $entry = [
        'field_path'      => (string) $fieldRow['field_path'],
        'status'          => $fieldStatus,
        'source_block_id' => isset($fieldRow['source_block_id']) && $fieldRow['source_block_id'] !== ''
            ? (string) $fieldRow['source_block_id']
            : null,
        'bbox_json'       => $bboxDecoded,
        'verifier_reason' => isset($fieldRow['verifier_reason']) && $fieldRow['verifier_reason'] !== ''
            ? (string) $fieldRow['verifier_reason']
            : null,
    ];

    // PHI custody: "value" is sourced from the clinical table row only (NOT
    // from extraction_json).  clinician_value surfaces the clinician-asserted
    // value for manually_edited / manually_added rows.
    if ($fieldStatus === 'verified' && $clinicalTable !== null && $clinicalRowId !== null) {
        // Verified: value from clinical table; no clinician_value.
        $clinicalValue = fetchClinicalValue($clinicalTable, $clinicalRowId);
        if ($clinicalValue !== null) {
            $entry['value'] = $clinicalValue;
        }
        $entry['clinical_table']  = $clinicalTable;
        $entry['clinical_row_id'] = $clinicalRowId;
    } elseif ($fieldStatus === 'manually_edited') {
        // Manually edited: clinician_value always present.
        // LLM-side value only if clinical_row_id is set (post-approve).
        $clinicianValue = isset($fieldRow['clinician_value']) && $fieldRow['clinician_value'] !== null
            ? (string) $fieldRow['clinician_value']
            : null;
        if ($clinicianValue !== null) {
            $entry['clinician_value'] = $clinicianValue;
        }
        if ($clinicalTable !== null && $clinicalRowId !== null) {
            $llmValue = fetchClinicalValue($clinicalTable, $clinicalRowId);
            if ($llmValue !== null) {
                $entry['value'] = $llmValue;
            }
            $entry['clinical_table']  = $clinicalTable;
            $entry['clinical_row_id'] = $clinicalRowId;
        }
    } elseif ($fieldStatus === 'manually_added') {
        // Manually added: clinician_value always present; no LLM-side value.
        $clinicianValue = isset($fieldRow['clinician_value']) && $fieldRow['clinician_value'] !== null
            ? (string) $fieldRow['clinician_value']
            : null;
        if ($clinicianValue !== null) {
            $entry['clinician_value'] = $clinicianValue;
        }
        if ($clinicalTable !== null && $clinicalRowId !== null) {
            $entry['clinical_table']  = $clinicalTable;
            $entry['clinical_row_id'] = $clinicalRowId;
        }
    }
    // status='stripped': no value, no clinician_value — entry is already complete.

    $fields[] = $entry;
}

// 8. Decode Docling blocks.
$doclingBlocks = [];
$blocksJson    = $ext['docling_blocks_json'] ?? null;
if (is_string($blocksJson) && $blocksJson !== '') {
    $decoded = json_decode($blocksJson, true);
    if (is_array($decoded)) {
        $doclingBlocks = $decoded;
    }
}

// 9. Emit response.
$logger->info('ClinicalCopilot/extraction_for_doc: served', [
    'doc_id'        => $docId,
    'extraction_id' => $extractionId,
]);

echo json_encode([
    'extraction_id'  => $extractionId,
    'status'         => (string) $ext['status'],
    'doc_type'       => (string) $ext['doc_type'],
    'total_fields'   => $ext['total_fields'] !== null ? (int) $ext['total_fields'] : null,
    'stripped_fields'=> $ext['stripped_fields'] !== null ? (int) $ext['stripped_fields'] : null,
    'attempt_n'      => (int) ($ext['attempt_n'] ?? 1),
    'max_attempts'   => 3,
    'triggered_by'   => (string) ($ext['triggered_by'] ?? 'initial'),
    'model'          => (string) ($ext['model'] ?? ''),
    'fields'         => $fields,
    'docling_blocks' => $doclingBlocks,
], JSON_UNESCAPED_UNICODE);

// ---------------------------------------------------------------------------
// Helper: fetch the display value for a verified clinical row.
//
// Table-specific: we know the relevant text column for each supported table.
// Returns null when the table is not recognised or the row is not found.
// ---------------------------------------------------------------------------

/**
 * Fetch the display value for a verified clinical row.
 *
 * Supported tables and their text columns:
 *   procedure_result → result_text  (the test name)
 *   lists            → title        (the allergy substance)
 *   prescriptions    → drug         (the medication name)
 *
 * Returns null when the table is unsupported or the row does not exist.
 *
 * NO-PHI LOGGING: only table + row id; never the returned value.
 */
function fetchClinicalValue(string $table, int $rowId): ?string
{
    $allowedTables = [
        'procedure_result' => ['pk' => 'procedure_result_id', 'value_col' => 'result_text'],
        'lists'            => ['pk' => 'id',                  'value_col' => 'title'],
        'prescriptions'    => ['pk' => 'id',                  'value_col' => 'drug'],
    ];

    if (!isset($allowedTables[$table])) {
        return null;
    }

    $meta = $allowedTables[$table];
    // Column and table names are from a closed allowlist — not user-supplied.
    $rows = QueryUtils::fetchRecords(
        "SELECT `{$meta['value_col']}` FROM `{$table}` WHERE `{$meta['pk']}` = ? LIMIT 1",
        [$rowId],
    );

    if ($rows === [] || !isset($rows[0][$meta['value_col']])) {
        return null;
    }

    $val = $rows[0][$meta['value_col']];
    return is_string($val) ? $val : null;
}
