<?php

/**
 * GET endpoint: resolve a Co-Pilot citation back to its source document
 * (page + block + bbox), enabling click-to-source visual provenance for
 * PRD §5 ("A visual PDF bounding-box overlay is required").
 *
 * Logical URL:
 *   GET {webroot}/interface/modules/custom_modules/oe-module-clinical-copilot/public/resolve_citation.php
 *       ?source_type=patient_record
 *       &source_id=procedure_result:58027
 *       [&field_or_chunk_id=block_33]
 *
 * Pure SQL traversal — no LLM, no agent call, no Langfuse spans.  Two paths:
 *
 *   patient_record citation (e.g. procedure_result:58027) → traverse:
 *     procedure_result.id=58027
 *       → co_pilot_extracted_fields WHERE clinical_table='procedure_result'
 *           AND clinical_row_id=58027
 *       → co_pilot_extractions.doc_ref_id + docling_blocks_json
 *       → block lookup by source_block_id from the field row
 *
 *   extracted_document citation (source_id=doc_ref_id, field_or_chunk_id=block_id):
 *     co_pilot_extractions WHERE doc_ref_id=X AND is_active=1
 *       → docling_blocks_json
 *       → block lookup by block_id (the field_or_chunk_id query param)
 *
 *   guideline citation: returns 404 (no document trace by design).
 *
 * Response 200:
 *   {
 *     "document_id": 9842,
 *     "page": 1,
 *     "block_id": "block_33",
 *     "bbox": {"x0": 70.4, "y0": 477.8, "x1": 540.9, "y1": 611.1},
 *     "snippet": "first ≤240 chars of block text..."
 *   }
 *
 * Response 404:
 *   {"error": "not_resolvable"} — citation doesn't trace to any document
 *   (guideline source_type, patient_record without extracted-from-doc
 *   provenance, or doc/block not found).
 *
 * Response 400/401/403/405: standard auth + validation.
 *
 * SECURITY:
 *   - OpenEMR session authentication (userId from session).
 *   - ACL: aclCheckCore('patients', 'med') — same as extraction_for_doc.php.
 *   - No CSRF required for GET (read-only, idempotent).
 *
 * NO-PHI LOGGING (W2_ARCHITECTURE.md §8.3, AUDIT.md C-6):
 *   - error_log()/logger calls include ONLY structural metadata:
 *     source_type, http_status, document_id (FK identifier, not PHI).
 *   - NEVER log the response body (bbox, snippet, source_id values can
 *     reflect chart content tied to a patient).
 *   - The response BODY contains PHI by design (snippet from chart-bound
 *     document blocks); auth-gated via session + ACL.  Same posture as
 *     extraction_for_doc.php.
 *   - Zero Langfuse spans on this path — confirmed by Bram (coordination
 *     thread, 2026-05-09 A4): no_phi_in_logs rubric stays scoped to
 *     agent-side observability where it belongs.
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

// 4. Parse and validate query params.
$sourceType = $_GET['source_type'] ?? null;
$sourceId   = $_GET['source_id'] ?? null;
$fieldOrChunkId = $_GET['field_or_chunk_id'] ?? null;

if (!is_string($sourceType) || $sourceType === '') {
    http_response_code(400);
    echo json_encode(['error' => 'missing_source_type']);
    exit;
}
if (!in_array($sourceType, ['patient_record', 'extracted_document', 'guideline'], true)) {
    http_response_code(400);
    echo json_encode(['error' => 'invalid_source_type']);
    exit;
}
if (!is_string($sourceId) || $sourceId === '') {
    http_response_code(400);
    echo json_encode(['error' => 'missing_source_id']);
    exit;
}

// Sanity-cap input lengths to keep query/log surface small.
if (strlen($sourceId) > 256) {
    http_response_code(400);
    echo json_encode(['error' => 'source_id_too_long']);
    exit;
}
if (is_string($fieldOrChunkId) && strlen($fieldOrChunkId) > 64) {
    http_response_code(400);
    echo json_encode(['error' => 'field_or_chunk_id_too_long']);
    exit;
}

// 5. Guideline citations have no document trace — short-circuit.
if ($sourceType === 'guideline') {
    http_response_code(404);
    echo json_encode(['error' => 'not_resolvable', 'reason' => 'guideline_no_document_trace']);
    exit;
}

// ---------------------------------------------------------------------------
// Helper: decode bbox_json column ("{x0,y0,x1,y1}" or similar) → array|null.
// Returns null on any decode failure.
// ---------------------------------------------------------------------------

/**
 * @return array<string, float>|null
 */
function decodeBboxJson(?string $raw): ?array
{
    if ($raw === null || $raw === '') {
        return null;
    }
    $decoded = json_decode($raw, true);
    if (!is_array($decoded)) {
        return null;
    }
    // Coerce numeric keys to floats; drop missing.
    $out = [];
    foreach (['x0', 'y0', 'x1', 'y1'] as $key) {
        if (!isset($decoded[$key]) || !is_numeric($decoded[$key])) {
            return null;
        }
        $out[$key] = (float) $decoded[$key];
    }
    return $out;
}

/**
 * Find a block by block_id in a decoded docling_blocks list.
 *
 * @param  list<array<string, mixed>> $blocks
 * @return array<string, mixed>|null
 */
function findBlockById(array $blocks, string $blockId): ?array
{
    foreach ($blocks as $block) {
        if (!is_array($block)) {
            continue;
        }
        if (isset($block['block_id']) && (string) $block['block_id'] === $blockId) {
            return $block;
        }
    }
    return null;
}

/**
 * Extract bbox from a docling_blocks entry.  Accepts either nested
 * `bbox: {x0,y0,x1,y1}` or top-level x0/y0/x1/y1 (the agent's
 * _serialize_blocks_for_response shape uses nested bbox).
 *
 * @param  array<string, mixed> $block
 * @return array<string, float>|null
 */
function bboxFromBlock(array $block): ?array
{
    if (isset($block['bbox']) && is_array($block['bbox'])) {
        $out = [];
        foreach (['x0', 'y0', 'x1', 'y1', 'x', 'y', 'w', 'h'] as $key) {
            if (isset($block['bbox'][$key]) && is_numeric($block['bbox'][$key])) {
                $out[$key] = (float) $block['bbox'][$key];
            }
        }
        // Normalise {x,y,w,h} → {x0,y0,x1,y1} if necessary.
        if (isset($out['x'], $out['y'], $out['w'], $out['h']) && !isset($out['x0'])) {
            $out = [
                'x0' => $out['x'],
                'y0' => $out['y'],
                'x1' => $out['x'] + $out['w'],
                'y1' => $out['y'] + $out['h'],
            ];
        }
        if (isset($out['x0'], $out['y0'], $out['x1'], $out['y1'])) {
            return [
                'x0' => $out['x0'],
                'y0' => $out['y0'],
                'x1' => $out['x1'],
                'y1' => $out['y1'],
            ];
        }
    }
    // Fallback: top-level coords.
    $out = [];
    foreach (['x0', 'y0', 'x1', 'y1'] as $key) {
        if (isset($block[$key]) && is_numeric($block[$key])) {
            $out[$key] = (float) $block[$key];
        }
    }
    if (count($out) === 4) {
        return $out;
    }
    return null;
}

// ---------------------------------------------------------------------------
// 6. Path A — patient_record citation.
//    source_id format: "{table_name}:{row_id}"  e.g. "procedure_result:58027"
// ---------------------------------------------------------------------------

if ($sourceType === 'patient_record') {
    if (!preg_match('/^([A-Za-z0-9_]+):(\d+)$/', $sourceId, $m)) {
        http_response_code(400);
        echo json_encode(['error' => 'invalid_source_id_format']);
        exit;
    }
    $clinicalTable = $m[1];
    $clinicalRowId = (int) $m[2];

    // Closed allowlist of clinical tables we recognise — must match
    // RoundtripService's writers (see W2_ARCHITECTURE.md §2.7).
    //
    // Added 2026-05-09 along with PRD §2 round-trip expansion:
    //   - form_encounter: roundtripChiefConcern writes chief_concern to
    //     form_encounter.reason for the visit.
    //   - history_data: roundtripFamilyHistory writes family_history items
    //     to the patient's history_data row's wide-form columns.
    // Both go through the same co_pilot_extracted_fields → docling_blocks
    // chain as the original three tables, so no traversal-logic changes
    // are required — only the allowlist needs to be extended.
    $allowedTables = [
        'procedure_result',
        'lists',
        'prescriptions',
        'form_encounter',
        'history_data',
    ];
    if (!in_array($clinicalTable, $allowedTables, true)) {
        http_response_code(400);
        echo json_encode(['error' => 'unrecognised_clinical_table']);
        exit;
    }

    // Find the field row for this clinical pointer.  Most recent wins
    // (in case of multiple extractions writing to the same clinical row,
    // though that shouldn't normally happen).
    $fieldRows = QueryUtils::fetchRecords(
        'SELECT `id`, `extraction_id`, `field_path`, `source_block_id`, `bbox_json`
           FROM `co_pilot_extracted_fields`
          WHERE `clinical_table` = ?
            AND `clinical_row_id` = ?
          ORDER BY `id` DESC
          LIMIT 1',
        [$clinicalTable, $clinicalRowId],
    );

    if ($fieldRows === []) {
        http_response_code(404);
        echo json_encode(['error' => 'not_resolvable', 'reason' => 'no_field_row_for_clinical_pointer']);
        exit;
    }
    $fieldRow = $fieldRows[0];
    $extractionId  = (int) $fieldRow['extraction_id'];
    $sourceBlockId = isset($fieldRow['source_block_id']) ? (string) $fieldRow['source_block_id'] : '';
    $bboxFromField = decodeBboxJson(isset($fieldRow['bbox_json']) ? (string) $fieldRow['bbox_json'] : null);

    // Look up the extraction to get doc_ref_id + docling_blocks_json.
    // JOIN documents to surface the source file's mimetype so the sidecar
    // can branch its renderer (image/* → <img>, application/pdf → PDF.js).
    $extractionRows = QueryUtils::fetchRecords(
        'SELECT cpe.`doc_ref_id`, cpe.`docling_blocks_json`, d.`mimetype`
           FROM `co_pilot_extractions` cpe
           LEFT JOIN `documents` d ON d.`id` = cpe.`doc_ref_id`
          WHERE cpe.`id` = ?
          LIMIT 1',
        [$extractionId],
    );
    if ($extractionRows === []) {
        http_response_code(404);
        echo json_encode(['error' => 'not_resolvable', 'reason' => 'no_extraction_row']);
        exit;
    }
    $docRefId        = (string) $extractionRows[0]['doc_ref_id'];
    $blocksJsonRaw   = $extractionRows[0]['docling_blocks_json'] ?? null;
    $mimetype        = isset($extractionRows[0]['mimetype'])
        ? (string) $extractionRows[0]['mimetype']
        : 'application/pdf';

    // Find the block by source_block_id (may be empty if field had no cited block).
    $blocks = is_string($blocksJsonRaw) && $blocksJsonRaw !== ''
        ? json_decode($blocksJsonRaw, true)
        : [];
    if (!is_array($blocks)) {
        $blocks = [];
    }

    $block = $sourceBlockId !== '' ? findBlockById($blocks, $sourceBlockId) : null;

    // Page — prefer block.page (canonical), fall back to bbox.page on the field row.
    $page = 1;
    if (is_array($block) && isset($block['page']) && is_numeric($block['page'])) {
        $page = (int) $block['page'];
    }

    // BBox — prefer the field-row bbox_json (already verified at extraction
    // time); fall back to the block's bbox if the field row's is missing.
    $bbox = $bboxFromField;
    if ($bbox === null && is_array($block)) {
        $bbox = bboxFromBlock($block);
    }
    if ($bbox === null) {
        http_response_code(404);
        echo json_encode(['error' => 'not_resolvable', 'reason' => 'no_bbox_available']);
        exit;
    }

    // Snippet — from the block's text_snippet (≤240 chars).  PHI-bearing
    // but authed; matches the response posture of extraction_for_doc.php.
    $snippet = '';
    if (is_array($block) && isset($block['text_snippet']) && is_string($block['text_snippet'])) {
        $snippet = $block['text_snippet'];
    }

    $logger->info('ClinicalCopilot/resolve_citation: patient_record served', [
        'source_type'   => $sourceType,
        'extraction_id' => $extractionId,
        'document_id'   => $docRefId,
        // No bbox values, no source_id raw, no snippet — structural only.
    ]);

    echo json_encode([
        'document_id' => $docRefId === '' ? null : (ctype_digit($docRefId) ? (int) $docRefId : $docRefId),
        'page'        => $page,
        'block_id'    => $sourceBlockId !== '' ? $sourceBlockId : null,
        'bbox'        => $bbox,
        'snippet'     => $snippet,
        // Surface the source-document mimetype so the citation sidecar can
        // pick its renderer: image/* → <img> + SVG bbox overlay; PDF →
        // PDF.js path.  Without this, image documents (handwritten intake
        // PNGs etc.) fail with InvalidPDFException when PDF.js tries to
        // parse them.
        'mimetype'    => $mimetype,
    ], JSON_UNESCAPED_UNICODE);
    exit;
}

// ---------------------------------------------------------------------------
// 7. Path B — extracted_document citation.
//    source_id     = doc_ref_id (numeric string)
//    field_or_chunk_id = block_id (e.g. "block_33") — REQUIRED for this path
// ---------------------------------------------------------------------------

if ($sourceType === 'extracted_document') {
    if (!is_string($fieldOrChunkId) || $fieldOrChunkId === '') {
        http_response_code(400);
        echo json_encode(['error' => 'missing_field_or_chunk_id']);
        exit;
    }
    if (!ctype_digit($sourceId)) {
        http_response_code(400);
        echo json_encode(['error' => 'invalid_source_id_format']);
        exit;
    }
    $docRefId = $sourceId;

    $extractionRows = QueryUtils::fetchRecords(
        'SELECT cpe.`id`, cpe.`docling_blocks_json`, d.`mimetype`
           FROM `co_pilot_extractions` cpe
           LEFT JOIN `documents` d ON d.`id` = cpe.`doc_ref_id`
          WHERE cpe.`doc_ref_id` = ?
            AND cpe.`is_active` = 1
          ORDER BY cpe.`id` DESC
          LIMIT 1',
        [$docRefId],
    );
    if ($extractionRows === []) {
        http_response_code(404);
        echo json_encode(['error' => 'not_resolvable', 'reason' => 'no_active_extraction_for_document']);
        exit;
    }
    $blocksJsonRaw = $extractionRows[0]['docling_blocks_json'] ?? null;
    $mimetype      = isset($extractionRows[0]['mimetype'])
        ? (string) $extractionRows[0]['mimetype']
        : 'application/pdf';
    $blocks = is_string($blocksJsonRaw) && $blocksJsonRaw !== ''
        ? json_decode($blocksJsonRaw, true)
        : [];
    if (!is_array($blocks)) {
        $blocks = [];
    }

    $block = findBlockById($blocks, $fieldOrChunkId);
    if ($block === null) {
        http_response_code(404);
        echo json_encode(['error' => 'not_resolvable', 'reason' => 'block_id_not_found']);
        exit;
    }

    $bbox = bboxFromBlock($block);
    if ($bbox === null) {
        http_response_code(404);
        echo json_encode(['error' => 'not_resolvable', 'reason' => 'block_has_no_bbox']);
        exit;
    }

    $page = 1;
    if (isset($block['page']) && is_numeric($block['page'])) {
        $page = (int) $block['page'];
    }

    $snippet = '';
    if (isset($block['text_snippet']) && is_string($block['text_snippet'])) {
        $snippet = $block['text_snippet'];
    }

    $logger->info('ClinicalCopilot/resolve_citation: extracted_document served', [
        'source_type'   => $sourceType,
        'document_id'   => $docRefId,
        'block_id'      => $fieldOrChunkId,
        // No bbox values, no snippet — structural only.
    ]);

    echo json_encode([
        'document_id' => ctype_digit($docRefId) ? (int) $docRefId : $docRefId,
        'page'        => $page,
        'block_id'    => $fieldOrChunkId,
        'bbox'        => $bbox,
        'snippet'     => $snippet,
        'mimetype'    => $mimetype,
    ], JSON_UNESCAPED_UNICODE);
    exit;
}

// Should be unreachable given the source_type validation above.
http_response_code(500);
echo json_encode(['error' => 'unhandled_source_type']);
