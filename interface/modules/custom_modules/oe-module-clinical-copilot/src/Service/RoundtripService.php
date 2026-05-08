<?php

/**
 * FHIR round-trip service: persist extracted document facts as OpenEMR
 * clinical-table rows so they appear in the chart UI and are queryable
 * via the FHIR API.
 *
 * Implements W2 PRD §1 + §43 requirements:
 *   - Derived facts persisted as OpenEMR records (PRD §1's "or OpenEMR records")
 *   - Round-trip traceable via co_pilot_fhir_links table (PRD §43)
 *   - No duplicate rows on retry (cross-attempt natural-key lookup via
 *     co_pilot_extractions.doc_ref_id JOIN)
 *
 * SCOPE (this MR):
 *   - lab_pdf  → procedure_order + procedure_order_code + procedure_report
 *                + N × procedure_result rows (one per LabReport.results[i])
 *   - intake_form.allergies          → lists rows (type='allergy')
 *   - intake_form.current_medications → prescriptions rows
 *
 * DEFERRED:
 *   - intake_form.family_history → no clean OpenEMR write-path; OpenEMR's
 *     family-history UI uses history_data's wide-form columns
 *     (family_history_father, _mother, etc.) which doesn't match our
 *     row-per-condition extraction shape.  Documented as deferred.
 *   - intake_form.demographics + chief_concern → out of scope for round-trip
 *     (demographics already exist; chief_concern is free text).
 *   - LOINC / RxNorm / SNOMED code lookups → text-only fields for now;
 *     code columns left null.
 *
 * IDEMPOTENCY (P3 cross-attempt):
 *   For each clinical row, this service computes a natural key via
 *   FieldKeyExtractorFactory, then queries existing rows in the target
 *   clinical table that were written from ANY prior extraction for the same
 *   doc_ref_id (via JOIN through co_pilot_extractions.doc_ref_id).  If a
 *   matching row is found it is updated in place; otherwise a new row is
 *   inserted.  This replaces the P1/P2 approach of deduplicating only within
 *   a single extraction via co_pilot_fhir_links.
 *
 * NO-PHI LOGGING:
 *   Logs only structural counters (n_results, n_allergies, n_medications,
 *   target_table, target_record_id).  Never raw extracted values, drug
 *   names, allergen substances, or test results.
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
use OpenEMR\Modules\ClinicalCopilot\ExtractionStatus;
use OpenEMR\Modules\ClinicalCopilot\Service\FieldKeyExtractor\AllergyKeyExtractor;
use OpenEMR\Modules\ClinicalCopilot\Service\FieldKeyExtractor\LabResultKeyExtractor;
use OpenEMR\Modules\ClinicalCopilot\Service\FieldKeyExtractor\MedicationKeyExtractor;
use Psr\Log\LoggerInterface;
use Throwable;

/**
 * Persists derived facts from a co_pilot_extractions row into OpenEMR's
 * clinical tables.  Idempotent via cross-attempt natural-key lookup.
 *
 * Not declared `final` so DocumentSavedSubscriber unit tests can supply
 * a mocked instance via PHPUnit createMock() — no DB needed for the
 * subscriber-level event-flow assertions.  Production callers depend
 * on the public API only.
 */
class RoundtripService
{
    private readonly LoggerInterface $logger;

    private readonly FieldKeyExtractorFactory $extractorFactory;

    public function __construct(
        ?LoggerInterface $logger = null,
        ?FieldKeyExtractorFactory $extractorFactory = null,
    ) {
        $this->logger           = $logger ?? new SystemLogger();
        $this->extractorFactory = $extractorFactory ?? new FieldKeyExtractorFactory();
    }

    /**
     * Round-trip an extraction into OpenEMR clinical tables.
     *
     * Dispatches by doc_type.  Returns counts of inserted rows per
     * resource kind for the caller's audit log.  Failures inside one
     * resource type do not cancel the others — best-effort partial
     * persistence with structured per-resource error logging.
     *
     * @param int                    $extractionId    co_pilot_extractions.id (FK target)
     * @param int                    $patientId       OpenEMR pid (NOT the sentinel)
     * @param string                 $docType         "lab_pdf" | "intake_form"
     * @param array<string, mixed>   $extraction      Decoded extraction_json payload
     * @param string                 $docRefId        OpenEMR documents.id (string)
     * @param array<string, int>     $verifiedFieldMap field_path → ef_row_id from ExtractedFieldsWriter
     * @param ExtractedFieldsWriter|null $fieldsWriter  For back-filling clinical pointers; null disables
     *
     * @return array{
     *     observations: int,
     *     allergies:    int,
     *     medications:  int,
     *     errors:       list<string>
     * }
     */
    public function roundtrip(
        int $extractionId,
        int $patientId,
        string $docType,
        array $extraction,
        string $docRefId = '',
        array $verifiedFieldMap = [],
        ?ExtractedFieldsWriter $fieldsWriter = null,
    ): array {
        $counts = [
            'observations' => 0,
            'allergies'    => 0,
            'medications'  => 0,
            'errors'       => [],
        ];

        try {
            if ($docType === 'lab_pdf') {
                $counts['observations'] = $this->roundtripLabReport(
                    $extractionId,
                    $patientId,
                    $extraction,
                    $docRefId,
                    $verifiedFieldMap,
                    $fieldsWriter,
                );
            } elseif ($docType === 'intake_form') {
                $counts['allergies']   = $this->roundtripAllergies(
                    $extractionId,
                    $patientId,
                    (array) ($extraction['allergies'] ?? []),
                    $docRefId,
                    $verifiedFieldMap,
                    $fieldsWriter,
                );
                $counts['medications'] = $this->roundtripMedications(
                    $extractionId,
                    $patientId,
                    (array) ($extraction['current_medications'] ?? []),
                    $docRefId,
                    $verifiedFieldMap,
                    $fieldsWriter,
                );
                // family_history deferred — see file docblock
            }
        } catch (Throwable $e) {
            // Structural-only log — class + file:line, no exception message.
            $this->logger->error('ClinicalCopilot/RoundtripService: dispatch failed', [
                'extraction_id'   => $extractionId,
                'doc_type'        => $docType,
                'exception_class' => get_class($e),
                'file'            => basename($e->getFile()),
                'line'            => $e->getLine(),
            ]);
            $counts['errors'][] = 'dispatch_failed';
        }

        $this->logger->info('ClinicalCopilot/RoundtripService: complete', [
            'extraction_id' => $extractionId,
            'doc_type'      => $docType,
            'observations'  => $counts['observations'],
            'allergies'     => $counts['allergies'],
            'medications'   => $counts['medications'],
            'n_errors'      => count($counts['errors']),
        ]);

        return $counts;
    }

    // -----------------------------------------------------------------------
    // Attempt-chain helpers
    // -----------------------------------------------------------------------

    /**
     * Mark all prior active extractions for this (doc_ref_id, doc_type) as inactive.
     *
     * Enforces the application-layer invariant that at most one row in
     * co_pilot_extractions has is_active = 1 per (doc_ref_id, doc_type).
     * Called from DocumentSavedSubscriber immediately BEFORE resolveFilePath
     * and any agent call (not after), so the invariant holds even on error paths.
     *
     * For the very first attempt there are no prior rows, so the UPDATE
     * matches zero rows and is a no-op — this is safe and correct.
     *
     * PHI note: only the structural identifier (doc_ref_id) and the affected
     * row count are logged.  No patient_id, no extracted values.
     *
     * @param non-empty-string $docRefId
     * @param non-empty-string $docType
     */
    public function markPriorAttemptsInactive(string $docRefId, string $docType): void
    {
        QueryUtils::sqlStatementThrowException(
            'UPDATE `co_pilot_extractions`
                SET `is_active` = 0
              WHERE `doc_ref_id` = ?
                AND `doc_type` = ?
                AND `is_active` = 1',
            [$docRefId, $docType],
        );

        $this->logger->info('ClinicalCopilot/RoundtripService: prior attempts marked inactive', [
            'doc_ref_id' => $docRefId,
            'doc_type'   => $docType,
        ]);
    }

    // -----------------------------------------------------------------------
    // P4 R1 — on-demand round-trip driven from persisted extraction state
    // -----------------------------------------------------------------------

    /**
     * Execute the FHIR round-trip for a persisted extraction row.
     *
     * Called by CoPilotController::handleApprove when the clinician approves
     * an extraction that is currently in status=pending_review.
     *
     * Preconditions (enforced by the controller, not here):
     *   - The extraction row exists and is in ExtractionStatus::PENDING_REVIEW.
     *   - The row has is_active = 1.
     *
     * This method:
     *  1. Loads the co_pilot_extractions row.
     *  2. Loads co_pilot_extracted_fields rows for this extraction.
     *  3. Reconstructs verifiedFieldMap from those rows (field_path → ef_row_id).
     *  4. Dispatches into the existing per-doc_type round-trip logic.
     *  5. Transitions the row status to ExtractionStatus::APPROVED.
     *
     * @param int $extractionId  co_pilot_extractions.id
     * @param int $realPatientId Real OpenEMR pid (from session — NOT the sentinel).
     *                           Clinical-table FK must reference the actual patient row.
     * @param ExtractedFieldsWriter|null $fieldsWriter  For back-filling clinical pointers.
     *
     * @return array{
     *     observations: int,
     *     allergies:    int,
     *     medications:  int,
     *     errors:       list<string>
     * }
     *
     * @throws \RuntimeException when the extraction row is not found or has no
     *         extraction_json to round-trip from.
     */
    public function roundtripFromExtractionId(
        int $extractionId,
        int $realPatientId,
        ?ExtractedFieldsWriter $fieldsWriter = null,
    ): array {
        // 1. Load the extraction row.
        $rows = QueryUtils::fetchRecords(
            'SELECT `id`, `doc_ref_id`, `doc_type`, `extraction_json`
               FROM `co_pilot_extractions`
              WHERE `id` = ?
                AND `is_active` = 1
              LIMIT 1',
            [$extractionId],
        );

        if ($rows === []) {
            throw new \RuntimeException('Extraction row not found or not active.');
        }

        $row            = $rows[0];
        $docRefId       = (string) $row['doc_ref_id'];
        $docType        = (string) $row['doc_type'];
        $extractionJson = isset($row['extraction_json']) ? (string) $row['extraction_json'] : null;

        if ($extractionJson === null || $extractionJson === '') {
            throw new \RuntimeException('Extraction row has no extraction_json to round-trip from.');
        }

        $extractionPayload = json_decode($extractionJson, true);
        if (!is_array($extractionPayload)) {
            throw new \RuntimeException('Extraction row extraction_json could not be decoded.');
        }

        // 2. Load co_pilot_extracted_fields to reconstruct verifiedFieldMap.
        $fieldRows = QueryUtils::fetchRecords(
            'SELECT `id`, `field_path`, `status`
               FROM `co_pilot_extracted_fields`
              WHERE `extraction_id` = ?
              ORDER BY `id` ASC',
            [$extractionId],
        );

        // 3. Reconstruct verifiedFieldMap (field_path → ef_row_id) for verified rows only.
        /** @var array<string, int> $verifiedFieldMap */
        $verifiedFieldMap = [];
        foreach ($fieldRows as $fieldRow) {
            if ((string) ($fieldRow['status'] ?? '') === 'verified') {
                $verifiedFieldMap[(string) $fieldRow['field_path']] = (int) $fieldRow['id'];
            }
        }

        // 4. Dispatch into existing round-trip logic.
        $counts = $this->roundtrip(
            extractionId:     $extractionId,
            patientId:        $realPatientId,
            docType:          $docType,
            extraction:       $extractionPayload,
            docRefId:         $docRefId,
            verifiedFieldMap: $verifiedFieldMap,
            fieldsWriter:     $fieldsWriter,
        );

        // 5. Transition status to approved.
        $this->updateExtractionStatus($extractionId, ExtractionStatus::APPROVED);

        $this->logger->info('ClinicalCopilot/RoundtripService: roundtripFromExtractionId complete', [
            'extraction_id' => $extractionId,
            'doc_type'      => $docType,
            'new_status'    => ExtractionStatus::APPROVED,
            'observations'  => $counts['observations'],
            'allergies'     => $counts['allergies'],
            'medications'   => $counts['medications'],
            'n_errors'      => count($counts['errors']),
        ]);

        return $counts;
    }

    /**
     * Update the status column on a co_pilot_extractions row.
     *
     * Used by P4 R1 approve/reject flows.  Only acts on the active row
     * (is_active = 1) to prevent accidental status changes on superseded
     * attempt rows.
     *
     * @param non-empty-string $newStatus  One of the ExtractionStatus constants.
     */
    public function updateExtractionStatus(int $extractionId, string $newStatus): void
    {
        QueryUtils::sqlStatementThrowException(
            'UPDATE `co_pilot_extractions`
                SET `status` = ?
              WHERE `id` = ?
                AND `is_active` = 1',
            [$newStatus, $extractionId],
        );

        $this->logger->info('ClinicalCopilot/RoundtripService: extraction status updated', [
            'extraction_id' => $extractionId,
            'new_status'    => $newStatus,
        ]);
    }

    // -----------------------------------------------------------------------
    // LabReport → procedure_order + procedure_order_code + procedure_report
    //                + N × procedure_result
    // -----------------------------------------------------------------------

    /**
     * Persist a LabReport's results into OpenEMR's procedure_* tables.
     *
     * Modeled on Cda/CdaTemplateImportDispose.php lines 1620-1680.
     *
     * Creates ONE parent procedure_order + procedure_order_code +
     * procedure_report per extraction (so all results from the same lab
     * PDF are grouped under one logical "order"), then ONE
     * procedure_result per LabReport.results[i].
     *
     * Cross-attempt deduplication: for each result, we compute a natural key
     * (doc_ref_id + ":lab:" + test_name) and check whether any procedure_result
     * row linked to ANY prior extraction for the same doc_ref_id already exists.
     * If so, we UPDATE the existing row; otherwise we INSERT a new one.
     *
     * @param array<string, int>     $verifiedFieldMap  field_path → ef_row_id
     * @param ExtractedFieldsWriter|null $fieldsWriter
     * @return int  number of procedure_result rows inserted or updated
     */
    private function roundtripLabReport(
        int $extractionId,
        int $patientId,
        array $extraction,
        string $docRefId,
        array $verifiedFieldMap,
        ?ExtractedFieldsWriter $fieldsWriter,
    ): int {
        $results = (array) ($extraction['results'] ?? []);
        if ($results === []) {
            return 0;
        }

        // Idempotency: parent procedure_order is identified by the
        // co_pilot_extraction_id with source_block_id=null.
        $existingParent = $this->findLink(
            $extractionId,
            'procedure_order',
            sourceBlockId: null,
        );

        if ($existingParent !== null) {
            $orderId  = (int) $existingParent['target_record_id'];
            $reportId = $this->findReportIdForOrder($orderId);
        } else {
            // Best-effort encounter linkage: most recent encounter for
            // this patient.  Lab orders need to live under SOMETHING in
            // the encounter list to render in the Labs UI; a missing
            // encounter row makes the result invisible.
            $encounterId = $this->resolveLatestEncounterId($patientId);

            // Use the panel's date if available; else today.
            $orderDate = $this->firstResultDate($results) ?? date('Y-m-d H:i:s');

            // procedure_order parent.  provider_id='' is acceptable —
            // CDA importer also passes an empty provider_id when the
            // source document doesn't carry one.
            $orderId = (int) QueryUtils::sqlInsert(
                "INSERT INTO procedure_order
                    (provider_id, patient_id, encounter_id, date_collected,
                     date_ordered, order_priority, order_status, activity,
                     procedure_order_type)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'laboratory_test')",
                ['', $patientId, $encounterId, $orderDate, $orderDate,
                 'normal', 'complete', 1],
            );

            QueryUtils::sqlStatementThrowException(
                "INSERT INTO procedure_order_code
                    (procedure_order_id, procedure_order_seq, procedure_code,
                     procedure_name, diagnoses, procedure_order_title,
                     procedure_type)
                 VALUES (?, 1, '', ?, '', 'laboratory_test', 'laboratory_test')",
                [$orderId, $this->panelTitleFromResults($results)],
            );

            // procedure_report — wraps the actual result rows.
            $reportId = (int) QueryUtils::sqlInsert(
                "INSERT INTO procedure_report
                    (procedure_order_id, date_collected, date_report,
                     report_status, review_status)
                 VALUES (?, ?, ?, 'final', 'reviewed')",
                [$orderId, $orderDate, $orderDate],
            );

            $this->insertLink(
                $extractionId,
                'procedure_order',
                $orderId,
                sourceBlockId: null,
                resourceKind: 'procedure_order_parent',
            );
        }

        $labExtractor = new LabResultKeyExtractor();

        // Per-result inserts.  Each result_text comes from the extracted
        // test_name; the test_value goes into the `result` column.
        $upserted = 0;
        foreach ($results as $idx => $row) {
            $row = (array) $row;
            $sourceBlockId = isset($row['source_block_id'])
                ? (string) $row['source_block_id']
                : null;

            // P3 cross-attempt deduplication via natural key.
            $naturalKey = '';
            if ($docRefId !== '') {
                $naturalKey = $labExtractor->naturalKey($docRefId, $row);
                $existingResultId = $this->findClinicalRowByNaturalKey(
                    $docRefId,
                    'procedure_result',
                    $naturalKey,
                );
                if ($existingResultId !== null) {
                    // Row already exists from a prior attempt — update in place.
                    QueryUtils::sqlStatementThrowException(
                        "UPDATE procedure_result
                            SET units = ?, result = ?, `range` = ?,
                                result_text = ?, result_status = 'final',
                                abnormal = ?
                          WHERE procedure_result_id = ?",
                        [
                            (string) ($row['unit'] ?? ''),
                            (string) ($row['value'] ?? ''),
                            (string) ($row['reference_range'] ?? ''),
                            (string) ($row['test_name'] ?? ''),
                            $this->mapAbnormal($row['abnormal'] ?? null),
                            $existingResultId,
                        ],
                    );
                    $this->updateFieldPointer(
                        $fieldsWriter,
                        $verifiedFieldMap,
                        (string) $idx,
                        'procedure_result',
                        $existingResultId,
                    );
                    $upserted++;
                    continue;
                }
            }

            // Fallback P1/P2 idempotency: fingerprint within extraction.
            $fingerprint = $sourceBlockId !== null
                ? $sourceBlockId . ':' . (string) ($row['test_name'] ?? '')
                : null;

            if ($fingerprint !== null && $this->findLink(
                $extractionId,
                'procedure_result',
                sourceBlockId: $fingerprint,
            ) !== null) {
                continue;
            }

            $resultId = (int) QueryUtils::sqlInsert(
                "INSERT INTO procedure_result
                    (procedure_report_id, result_code, date, units, result,
                     `range`, result_text, result_status, abnormal)
                 VALUES (?, '', ?, ?, ?, ?, ?, 'final', ?)",
                [
                    $reportId,
                    $this->isoDate($row['collection_date'] ?? null) ?? date('Y-m-d'),
                    (string) ($row['unit'] ?? ''),
                    (string) ($row['value'] ?? ''),
                    (string) ($row['reference_range'] ?? ''),
                    (string) ($row['test_name'] ?? ''),
                    $this->mapAbnormal($row['abnormal'] ?? null),
                ],
            );

            $this->insertLink(
                $extractionId,
                'procedure_result',
                $resultId,
                sourceBlockId: $fingerprint,
                resourceKind: 'observation',
            );

            if ($docRefId !== '' && $naturalKey !== '') {
                $this->insertNaturalKeyIndex(
                    $extractionId,
                    'procedure_result',
                    $resultId,
                    $naturalKey,
                );
            }

            $this->updateFieldPointer(
                $fieldsWriter,
                $verifiedFieldMap,
                (string) $idx,
                'procedure_result',
                $resultId,
            );
            $upserted++;
        }

        return $upserted;
    }

    /**
     * Build a panel title from the test names in the result list.
     * Used as procedure_order_code.procedure_name.
     *
     * For a lipid panel, returns "Cholesterol, Total + 4 more".  For a
     * single result, returns the test_name verbatim.  Empty results list
     * returns "Lab Panel".
     */
    private function panelTitleFromResults(array $results): string
    {
        if ($results === []) {
            return 'Lab Panel';
        }
        $first = (string) ((array) $results[0])['test_name'] ?? 'Lab Panel';
        $extras = count($results) - 1;
        return $extras > 0 ? $first . ' + ' . $extras . ' more' : $first;
    }

    /**
     * Pick the earliest `collection_date` from the result list to use
     * as the order/report date.  Returns null if no result has one.
     */
    private function firstResultDate(array $results): ?string
    {
        foreach ($results as $row) {
            $row = (array) $row;
            $iso = $this->isoDate($row['collection_date'] ?? null);
            if ($iso !== null) {
                return $iso . ' 00:00:00';
            }
        }
        return null;
    }

    /**
     * Look up procedure_report.id for a given order id (used when the
     * round-trip resumes after a partial earlier run).
     */
    private function findReportIdForOrder(int $orderId): int
    {
        $rows = QueryUtils::fetchRecords(
            "SELECT procedure_report_id FROM procedure_report
              WHERE procedure_order_id = ?
              ORDER BY procedure_report_id ASC LIMIT 1",
            [$orderId],
        );
        if ($rows === [] || !isset($rows[0]['procedure_report_id'])) {
            // Should never happen if we created order+report atomically.
            // Defensive: create a fresh report row tied to the existing
            // order so result inserts can proceed.
            $now = date('Y-m-d H:i:s');
            return (int) QueryUtils::sqlInsert(
                "INSERT INTO procedure_report
                    (procedure_order_id, date_collected, date_report,
                     report_status, review_status)
                 VALUES (?, ?, ?, 'final', 'reviewed')",
                [$orderId, $now, $now],
            );
        }
        return (int) $rows[0]['procedure_report_id'];
    }

    // -----------------------------------------------------------------------
    // Allergies → lists rows (type='allergy')
    // -----------------------------------------------------------------------

    /**
     * Persist allergy entries into the lists table (type='allergy').
     *
     * Modeled on Cda/CdaTemplateImportDispose.php InsertAllergies (lines 165-...).
     * No RXNORM lookup — `diagnosis` column left empty.
     *
     * Cross-attempt deduplication: natural key is doc_ref_id + ":allergy:" + substance.
     *
     * @param list<array<string,mixed>>  $allergies
     * @param array<string, int>         $verifiedFieldMap
     * @return int  number of rows inserted or updated
     */
    private function roundtripAllergies(
        int $extractionId,
        int $patientId,
        array $allergies,
        string $docRefId,
        array $verifiedFieldMap,
        ?ExtractedFieldsWriter $fieldsWriter,
    ): int {
        $allergyExtractor = new AllergyKeyExtractor();
        $upserted = 0;

        foreach ($allergies as $idx => $row) {
            $row = (array) $row;
            $sourceBlockId = isset($row['source_block_id'])
                ? (string) $row['source_block_id']
                : null;
            $substance = (string) ($row['substance'] ?? '');
            if ($substance === '') {
                continue;
            }

            // P3 cross-attempt deduplication.
            $naturalKey = '';
            if ($docRefId !== '') {
                $naturalKey = $allergyExtractor->naturalKey($docRefId, $row);
                $existingListId = $this->findClinicalRowByNaturalKey(
                    $docRefId,
                    'lists',
                    $naturalKey,
                );
                if ($existingListId !== null) {
                    QueryUtils::sqlStatementThrowException(
                        "UPDATE lists
                            SET title = ?, severity_al = ?, reaction = ?
                          WHERE id = ?",
                        [
                            $substance,
                            $this->mapSeverity((string) ($row['severity'] ?? '')),
                            (string) ($row['reaction'] ?? ''),
                            $existingListId,
                        ],
                    );
                    $this->updateFieldPointer(
                        $fieldsWriter,
                        $verifiedFieldMap,
                        (string) $idx,
                        'lists',
                        $existingListId,
                    );
                    $upserted++;
                    continue;
                }
            }

            // Per-row fingerprint so multiple allergies from the same
            // table block don't collide on (extraction, table, block).
            $fingerprint = $sourceBlockId !== null
                ? $sourceBlockId . ':' . $substance
                : null;

            if ($fingerprint !== null && $this->findLink(
                $extractionId,
                'lists',
                sourceBlockId: $fingerprint,
            ) !== null) {
                continue;
            }

            $listId = (int) QueryUtils::sqlInsert(
                "INSERT INTO lists
                    (pid, date, begdate, type, title, diagnosis, severity_al,
                     activity, reaction)
                 VALUES (?, NOW(), ?, 'allergy', ?, '', ?, 1, ?)",
                [
                    $patientId,
                    date('Y-m-d'),
                    $substance,
                    $this->mapSeverity((string) ($row['severity'] ?? '')),
                    (string) ($row['reaction'] ?? ''),
                ],
            );

            $this->insertLink(
                $extractionId,
                'lists',
                $listId,
                sourceBlockId: $fingerprint,
                resourceKind: 'allergy',
            );

            if ($docRefId !== '' && $naturalKey !== '') {
                $this->insertNaturalKeyIndex(
                    $extractionId,
                    'lists',
                    $listId,
                    $naturalKey,
                );
            }

            $this->updateFieldPointer(
                $fieldsWriter,
                $verifiedFieldMap,
                (string) $idx,
                'lists',
                $listId,
            );
            $upserted++;
        }
        return $upserted;
    }

    // -----------------------------------------------------------------------
    // Medications → prescriptions table
    // -----------------------------------------------------------------------

    /**
     * Persist current medications into the prescriptions table.
     *
     * Modeled on Cda/CdaTemplateImportDispose.php lines 1275+.  No
     * RXNORM lookup — `rxnorm_drugcode` column left empty.
     *
     * Cross-attempt deduplication: natural key is doc_ref_id + ":medication:" + name.
     *
     * @param list<array<string,mixed>>  $medications
     * @param array<string, int>         $verifiedFieldMap
     * @return int  number of rows inserted or updated
     */
    private function roundtripMedications(
        int $extractionId,
        int $patientId,
        array $medications,
        string $docRefId,
        array $verifiedFieldMap,
        ?ExtractedFieldsWriter $fieldsWriter,
    ): int {
        $medExtractor = new MedicationKeyExtractor();
        $upserted = 0;

        foreach ($medications as $idx => $row) {
            $row = (array) $row;
            $sourceBlockId = isset($row['source_block_id'])
                ? (string) $row['source_block_id']
                : null;
            $name = (string) ($row['name'] ?? '');
            if ($name === '') {
                continue;
            }

            // P3 cross-attempt deduplication.
            $naturalKey = '';
            if ($docRefId !== '') {
                $naturalKey = $medExtractor->naturalKey($docRefId, $row);
                $existingPrescriptionId = $this->findClinicalRowByNaturalKey(
                    $docRefId,
                    'prescriptions',
                    $naturalKey,
                );
                if ($existingPrescriptionId !== null) {
                    QueryUtils::sqlStatementThrowException(
                        "UPDATE prescriptions
                            SET drug = ?, dosage = ?
                          WHERE id = ?",
                        [
                            $name,
                            (string) ($row['dose'] ?? '') . ' ' . (string) ($row['frequency'] ?? ''),
                            $existingPrescriptionId,
                        ],
                    );
                    $this->updateFieldPointer(
                        $fieldsWriter,
                        $verifiedFieldMap,
                        (string) $idx,
                        'prescriptions',
                        $existingPrescriptionId,
                    );
                    $upserted++;
                    continue;
                }
            }

            $fingerprint = $sourceBlockId !== null
                ? $sourceBlockId . ':' . $name
                : null;

            if ($fingerprint !== null && $this->findLink(
                $extractionId,
                'prescriptions',
                sourceBlockId: $fingerprint,
            ) !== null) {
                continue;
            }

            $prescriptionId = (int) QueryUtils::sqlInsert(
                "INSERT INTO prescriptions
                    (patient_id, date_added, active, drug, dosage,
                     rxnorm_drugcode, provider_id, medication, request_intent)
                 VALUES (?, ?, 1, ?, ?, '', 0, 1, 'order')",
                [
                    $patientId,
                    date('Y-m-d'),
                    $name,
                    (string) ($row['dose'] ?? '') . ' '
                        . (string) ($row['frequency'] ?? ''),
                ],
            );

            $this->insertLink(
                $extractionId,
                'prescriptions',
                $prescriptionId,
                sourceBlockId: $fingerprint,
                resourceKind: 'medication',
            );

            if ($docRefId !== '' && $naturalKey !== '') {
                $this->insertNaturalKeyIndex(
                    $extractionId,
                    'prescriptions',
                    $prescriptionId,
                    $naturalKey,
                );
            }

            $this->updateFieldPointer(
                $fieldsWriter,
                $verifiedFieldMap,
                (string) $idx,
                'prescriptions',
                $prescriptionId,
            );
            $upserted++;
        }
        return $upserted;
    }

    // -----------------------------------------------------------------------
    // Traceback table helpers
    // -----------------------------------------------------------------------

    /**
     * Look up a co_pilot_fhir_links row by its UNIQUE-keyed tuple.
     * Returns the row as an associative array, or null if not found.
     *
     * @return array<string, mixed>|null
     */
    private function findLink(
        int $extractionId,
        string $targetTable,
        ?string $sourceBlockId,
    ): ?array {
        // The UNIQUE constraint includes source_block_id; null and
        // string are distinct slots.  MySQL treats NULL != NULL in
        // UNIQUE keys, so we have to use IS NULL explicitly.
        if ($sourceBlockId === null) {
            $rows = QueryUtils::fetchRecords(
                "SELECT * FROM co_pilot_fhir_links
                  WHERE co_pilot_extraction_id = ?
                    AND target_table = ?
                    AND source_block_id IS NULL
                  LIMIT 1",
                [$extractionId, $targetTable],
            );
        } else {
            $rows = QueryUtils::fetchRecords(
                "SELECT * FROM co_pilot_fhir_links
                  WHERE co_pilot_extraction_id = ?
                    AND target_table = ?
                    AND source_block_id = ?
                  LIMIT 1",
                [$extractionId, $targetTable, $sourceBlockId],
            );
        }
        return $rows[0] ?? null;
    }

    /**
     * Write a co_pilot_fhir_links row.  Caller must have checked
     * findLink() first (or be inserting a parent that's keyed on
     * source_block_id=null and known to be absent).
     */
    private function insertLink(
        int $extractionId,
        string $targetTable,
        int $targetRecordId,
        ?string $sourceBlockId,
        string $resourceKind,
    ): void {
        QueryUtils::sqlStatementThrowException(
            "INSERT INTO co_pilot_fhir_links
                (co_pilot_extraction_id, target_table, target_record_id,
                 source_block_id, resource_kind)
             VALUES (?, ?, ?, ?, ?)",
            [$extractionId, $targetTable, $targetRecordId,
             $sourceBlockId, $resourceKind],
        );
    }

    // -----------------------------------------------------------------------
    // Cross-attempt natural-key helpers
    // -----------------------------------------------------------------------

    /**
     * Look up a clinical-table row id that was written from ANY extraction
     * for the same doc_ref_id, identified by a stable natural key stored in
     * co_pilot_fhir_links.natural_key.
     *
     * Returns the target_record_id (clinical row PK) if found, null otherwise.
     *
     * SCHEMA NOTE: co_pilot_fhir_links does not yet have a `natural_key`
     * column — it is written by insertNaturalKeyIndex() below using the
     * source_block_id slot as the carrier (prefixed with "nk:").  This avoids
     * a schema migration for the P3 MR while keeping the cross-attempt lookup
     * contained within the fhir_links table.
     */
    private function findClinicalRowByNaturalKey(
        string $docRefId,
        string $targetTable,
        string $naturalKey,
    ): ?int {
        // The natural key is stored in source_block_id with a "nk:" prefix so
        // it can be distinguished from real Docling block ids.
        $storedKey = 'nk:' . $naturalKey;

        $rows = QueryUtils::fetchRecords(
            'SELECT cfl.target_record_id
               FROM co_pilot_fhir_links cfl
               JOIN co_pilot_extractions cpe
                 ON cpe.id = cfl.co_pilot_extraction_id
              WHERE cpe.doc_ref_id = ?
                AND cfl.target_table = ?
                AND cfl.source_block_id = ?
              LIMIT 1',
            [$docRefId, $targetTable, $storedKey],
        );

        if ($rows === [] || !isset($rows[0]['target_record_id'])) {
            return null;
        }

        return (int) $rows[0]['target_record_id'];
    }

    /**
     * Write a co_pilot_fhir_links row that carries the natural key.
     *
     * The natural key is stored in the source_block_id column with a "nk:"
     * prefix.  The resource_kind is set to "natural_key_index" to
     * distinguish it from real Docling-block link rows.
     */
    private function insertNaturalKeyIndex(
        int $extractionId,
        string $targetTable,
        int $targetRecordId,
        string $naturalKey,
    ): void {
        $storedKey = 'nk:' . $naturalKey;

        // INSERT IGNORE so a concurrent duplicate (unlikely but possible) does
        // not raise a duplicate-key error.
        QueryUtils::sqlStatementThrowException(
            "INSERT IGNORE INTO co_pilot_fhir_links
                (co_pilot_extraction_id, target_table, target_record_id,
                 source_block_id, resource_kind)
             VALUES (?, ?, ?, ?, 'natural_key_index')",
            [$extractionId, $targetTable, $targetRecordId, $storedKey],
        );
    }

    // -----------------------------------------------------------------------
    // Clinical-pointer back-fill helper
    // -----------------------------------------------------------------------

    /**
     * Attempt to back-fill the clinical_table + clinical_row_id columns on a
     * co_pilot_extracted_fields row for the current result index.
     *
     * The verifiedFieldMap carries field_path → ef_row_id pairs written by
     * ExtractedFieldsWriter::writeAll().  The field_path for result rows uses
     * a "results[{idx}].*" pattern; we match on index prefix.
     *
     * This is best-effort: if no matching ef_row_id is found, we log a debug
     * notice and continue without failing the round-trip.
     *
     * @param array<string, int> $verifiedFieldMap
     */
    private function updateFieldPointer(
        ?ExtractedFieldsWriter $fieldsWriter,
        array $verifiedFieldMap,
        string $rowIndex,
        string $clinicalTable,
        int $clinicalRowId,
    ): void {
        if ($fieldsWriter === null || $verifiedFieldMap === []) {
            return;
        }

        // Match any field_path that begins with "results[{rowIndex}]",
        // "allergies[{rowIndex}]", or "current_medications[{rowIndex}]".
        foreach ($verifiedFieldMap as $fieldPath => $efRowId) {
            // Accept any path whose bracketed index matches $rowIndex.
            if (preg_match('/\[' . preg_quote($rowIndex, '/') . '\]/', $fieldPath) === 1) {
                $fieldsWriter->updateClinicalPointer($efRowId, $clinicalTable, $clinicalRowId);
            }
        }
    }

    // -----------------------------------------------------------------------
    // Field mappers + small helpers
    // -----------------------------------------------------------------------

    /**
     * Find the most recent encounter id for a patient.  Returns 0 if
     * the patient has no encounters — procedure_order accepts 0 and
     * the row will still appear in the Labs tab (just without an
     * encounter linkage).
     */
    private function resolveLatestEncounterId(int $patientId): int
    {
        $rows = QueryUtils::fetchRecords(
            "SELECT encounter FROM form_encounter
              WHERE pid = ?
              ORDER BY date DESC LIMIT 1",
            [$patientId],
        );
        return (int) ($rows[0]['encounter'] ?? 0);
    }

    /**
     * Convert a possibly-loose date string to ISO YYYY-MM-DD.  Returns
     * null on parse failure (caller falls back to today's date).
     */
    private function isoDate(mixed $raw): ?string
    {
        if (!is_string($raw) || $raw === '') {
            return null;
        }
        $ts = strtotime($raw);
        return $ts !== false ? date('Y-m-d', $ts) : null;
    }

    /**
     * Map our extraction's boolean abnormal flag to procedure_result's
     * `abnormal` column convention ('yes' | 'no' | '').
     */
    private function mapAbnormal(mixed $abnormal): string
    {
        if ($abnormal === true) {
            return 'yes';
        }
        if ($abnormal === false) {
            return 'no';
        }
        return '';
    }

    /**
     * Map our extraction's free-text severity ("Mild", "Moderate",
     * "Severe") to OpenEMR's `severity_al` column convention.  Empty
     * string for unknown.
     */
    private function mapSeverity(string $severity): string
    {
        $s = strtolower(trim($severity));
        return match (true) {
            str_contains($s, 'mild')     => 'mild',
            str_contains($s, 'moderate') => 'moderate',
            str_contains($s, 'severe')   => 'severe',
            default                       => '',
        };
    }
}
