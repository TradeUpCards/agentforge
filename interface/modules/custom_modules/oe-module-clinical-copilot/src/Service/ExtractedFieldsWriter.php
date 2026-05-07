<?php

/**
 * Encapsulates writes to the co_pilot_extracted_fields table.
 *
 * Each co_pilot_extractions row can have many co_pilot_extracted_fields rows —
 * one per field_verdicts entry in the agent response.  This service owns all
 * INSERT and UPDATE operations on that table so the logic is not duplicated
 * between DocumentSavedSubscriber and the reprocess endpoint.
 *
 * Two write paths:
 *  - writeStripped()   — field was rejected by the verifier; no clinical row yet.
 *  - writeVerified()   — field passed the verifier; returns the new row id so
 *                        RoundtripService can back-fill clinical_table +
 *                        clinical_row_id via updateClinicalPointer().
 *
 * A third method, updateClinicalPointer(), fills in the clinical linkage columns
 * after RoundtripService has written the corresponding OpenEMR clinical-table row.
 *
 * NO-PHI LOGGING:
 *   Only structural identifiers (extraction_id, field_path length, row id) appear
 *   in logs.  Field values, bbox coordinates, and verifier reasons are not logged.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    AgentForge Team
 * @copyright Copyright (c) 2026 AgentForge
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\Service;

use OpenEMR\Common\Database\QueryUtils;
use OpenEMR\Common\Logging\SystemLogger;
use Psr\Log\LoggerInterface;

final class ExtractedFieldsWriter
{
    private readonly LoggerInterface $logger;

    public function __construct(?LoggerInterface $logger = null)
    {
        $this->logger = $logger ?? new SystemLogger();
    }

    /**
     * Persist a stripped field verdict row.
     *
     * A "stripped" field was rejected by the verifier because no bounding-box
     * citation could be found.  It has no clinical_table or clinical_row_id.
     *
     * @param int         $extractionId    co_pilot_extractions.id
     * @param string      $fieldPath       e.g. "results[1].value"
     * @param string|null $sourceBlockId   Docling block id, or null
     * @param array<string,float>|null $bbox  {x,y,w,h} object, or null
     * @param string      $verifierReason  Reason code from agent verifier
     */
    public function writeStripped(
        int $extractionId,
        string $fieldPath,
        ?string $sourceBlockId,
        ?array $bbox,
        string $verifierReason,
    ): void {
        $bboxJson = $bbox !== null ? json_encode($bbox, JSON_UNESCAPED_UNICODE) : null;

        QueryUtils::sqlStatementThrowException(
            'INSERT INTO `co_pilot_extracted_fields`
                (`extraction_id`, `field_path`, `status`, `source_block_id`,
                 `bbox_json`, `verifier_reason`)
             VALUES (?, ?, \'stripped\', ?, ?, ?)',
            [$extractionId, $fieldPath, $sourceBlockId, $bboxJson, $verifierReason],
        );

        $this->logger->info('ClinicalCopilot/ExtractedFieldsWriter: stripped field written', [
            'extraction_id' => $extractionId,
        ]);
    }

    /**
     * Persist a verified field verdict row.
     *
     * A "verified" field has a bounding-box citation.  Returns the new row id
     * so the caller can pass it to updateClinicalPointer() after writing the
     * corresponding clinical-table row.
     *
     * @param int         $extractionId   co_pilot_extractions.id
     * @param string      $fieldPath      e.g. "results[0].test_name"
     * @param string|null $sourceBlockId  Docling block id, or null
     * @param array<string,float>|null $bbox  {x,y,w,h} object, or null
     * @return int  The new co_pilot_extracted_fields.id
     */
    public function writeVerified(
        int $extractionId,
        string $fieldPath,
        ?string $sourceBlockId,
        ?array $bbox,
    ): int {
        $bboxJson = $bbox !== null ? json_encode($bbox, JSON_UNESCAPED_UNICODE) : null;

        $rowId = (int) QueryUtils::sqlInsert(
            'INSERT INTO `co_pilot_extracted_fields`
                (`extraction_id`, `field_path`, `status`, `source_block_id`, `bbox_json`)
             VALUES (?, ?, \'verified\', ?, ?)',
            [$extractionId, $fieldPath, $sourceBlockId, $bboxJson],
        );

        $this->logger->info('ClinicalCopilot/ExtractedFieldsWriter: verified field written', [
            'extraction_id' => $extractionId,
            'row_id'        => $rowId,
        ]);

        return $rowId;
    }

    /**
     * Back-fill the clinical linkage columns on an existing verified field row.
     *
     * Called by RoundtripService after it has written (or located) the OpenEMR
     * clinical-table row that corresponds to this extracted field.
     *
     * @param int    $fieldRowId     co_pilot_extracted_fields.id
     * @param string $clinicalTable  e.g. "procedure_result", "lists", "prescriptions"
     * @param int    $clinicalRowId  PK of the corresponding row in $clinicalTable
     */
    public function updateClinicalPointer(
        int $fieldRowId,
        string $clinicalTable,
        int $clinicalRowId,
    ): void {
        QueryUtils::sqlStatementThrowException(
            'UPDATE `co_pilot_extracted_fields`
                SET `clinical_table` = ?,
                    `clinical_row_id` = ?
              WHERE `id` = ?',
            [$clinicalTable, $clinicalRowId, $fieldRowId],
        );
    }

    /**
     * Write all field verdict rows for one extraction in a single pass.
     *
     * Convenience method used by both DocumentSavedSubscriber and the reprocess
     * endpoint.  The caller passes the raw agent response arrays; this method
     * joins verdicts against the docling_blocks map and dispatches to
     * writeStripped() / writeVerified().
     *
     * Returns a map of field_path → extracted_fields row id for every
     * "verified" verdict so RoundtripService can call updateClinicalPointer()
     * after writing the clinical row.
     *
     * @param int                                  $extractionId
     * @param list<array<string, mixed>>           $fieldVerdicts   agent response field_verdicts
     * @param list<array<string, mixed>>           $doclingBlocks   agent response docling_blocks
     * @return array<string, int>  field_path → ef_row_id (verified fields only)
     */
    public function writeAll(
        int $extractionId,
        array $fieldVerdicts,
        array $doclingBlocks,
    ): array {
        // Build a lookup: block_id → bbox array
        /** @var array<string, array<string, float>> $blockBboxMap */
        $blockBboxMap = [];
        foreach ($doclingBlocks as $block) {
            $block = (array) $block;
            $blockId = isset($block['block_id']) ? (string) $block['block_id'] : null;
            if ($blockId === null) {
                continue;
            }
            $bboxRaw = $block['bbox'] ?? null;
            if (is_array($bboxRaw)) {
                $blockBboxMap[$blockId] = $bboxRaw;
            }
        }

        /** @var array<string, int> $verifiedMap */
        $verifiedMap = [];

        foreach ($fieldVerdicts as $verdict) {
            $verdict = (array) $verdict;
            $fieldPath      = isset($verdict['field_path'])      ? (string) $verdict['field_path']      : '';
            $status         = isset($verdict['status'])          ? (string) $verdict['status']          : 'stripped';
            $sourceBlockId  = isset($verdict['source_block_id']) ? (string) $verdict['source_block_id'] : null;
            $verifierReason = isset($verdict['verifier_reason']) ? (string) $verdict['verifier_reason'] : '';

            if ($fieldPath === '') {
                continue;
            }

            $bbox = ($sourceBlockId !== null && isset($blockBboxMap[$sourceBlockId]))
                ? $blockBboxMap[$sourceBlockId]
                : null;

            if ($status === 'verified') {
                $rowId = $this->writeVerified($extractionId, $fieldPath, $sourceBlockId, $bbox);
                $verifiedMap[$fieldPath] = $rowId;
            } else {
                $this->writeStripped(
                    $extractionId,
                    $fieldPath,
                    $sourceBlockId,
                    $bbox,
                    $verifierReason !== '' ? $verifierReason : 'unknown',
                );
            }
        }

        return $verifiedMap;
    }
}
