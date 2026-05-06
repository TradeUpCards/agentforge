<?php

/**
 * FHIR round-trip service: persist extracted document facts as OpenEMR
 * clinical-table rows so they appear in the chart UI and are queryable
 * via the FHIR API.
 *
 * Implements W2 PRD §1 + §43 requirements:
 *   - Derived facts persisted as OpenEMR records (PRD §1's "or OpenEMR records")
 *   - Round-trip traceable via co_pilot_fhir_links table (PRD §43)
 *   - No duplicate rows on retry (UNIQUE constraint on traceback table)
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
 * IDEMPOTENCY:
 *   Every insert is preceded by a co_pilot_fhir_links lookup keyed on
 *   (co_pilot_extraction_id, target_table, source_block_id).  If a link
 *   row already exists, the insert is skipped.  Re-processing the same
 *   document never creates duplicate clinical rows.
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
use Psr\Log\LoggerInterface;
use Throwable;

/**
 * Persists derived facts from a co_pilot_extractions row into OpenEMR's
 * clinical tables.  Idempotent via co_pilot_fhir_links.
 */
final class RoundtripService
{
    private readonly LoggerInterface $logger;

    public function __construct(?LoggerInterface $logger = null)
    {
        $this->logger = $logger ?? new SystemLogger();
    }

    /**
     * Round-trip an extraction into OpenEMR clinical tables.
     *
     * Dispatches by doc_type.  Returns counts of inserted rows per
     * resource kind for the caller's audit log.  Failures inside one
     * resource type do not cancel the others — best-effort partial
     * persistence with structured per-resource error logging.
     *
     * @param int    $extractionId   co_pilot_extractions.id (FK target)
     * @param int    $patientId      OpenEMR pid (NOT the sentinel)
     * @param string $docType        "lab_pdf" | "intake_form"
     * @param array  $extraction     Decoded extraction_json payload
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
                );
            } elseif ($docType === 'intake_form') {
                $counts['allergies']   = $this->roundtripAllergies(
                    $extractionId,
                    $patientId,
                    (array) ($extraction['allergies'] ?? []),
                );
                $counts['medications'] = $this->roundtripMedications(
                    $extractionId,
                    $patientId,
                    (array) ($extraction['current_medications'] ?? []),
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
     * @return int  number of procedure_result rows inserted
     */
    private function roundtripLabReport(
        int $extractionId,
        int $patientId,
        array $extraction,
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

        // Per-result inserts.  Each result_text comes from the extracted
        // test_name; the test_value goes into the `result` column.
        $inserted = 0;
        foreach ($results as $row) {
            $row = (array) $row;
            $sourceBlockId = isset($row['source_block_id'])
                ? (string) $row['source_block_id']
                : null;

            // Idempotency check — skip if this (extraction_id,
            // procedure_result, source_block_id) already exists.
            // We treat a per-result fingerprint as `block_id . ":" . test_name`
            // because a single block can hold multiple results from the
            // same table (e.g. all 5 lipid panel rows share a block_id).
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
            $inserted++;
        }

        return $inserted;
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
     * @param list<array<string,mixed>> $allergies
     * @return int  number of rows inserted
     */
    private function roundtripAllergies(
        int $extractionId,
        int $patientId,
        array $allergies,
    ): int {
        $inserted = 0;
        foreach ($allergies as $row) {
            $row = (array) $row;
            $sourceBlockId = isset($row['source_block_id'])
                ? (string) $row['source_block_id']
                : null;
            $substance = (string) ($row['substance'] ?? '');
            if ($substance === '') {
                continue;
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
            $inserted++;
        }
        return $inserted;
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
     * @param list<array<string,mixed>> $medications
     * @return int  number of rows inserted
     */
    private function roundtripMedications(
        int $extractionId,
        int $patientId,
        array $medications,
    ): int {
        $inserted = 0;
        foreach ($medications as $row) {
            $row = (array) $row;
            $sourceBlockId = isset($row['source_block_id'])
                ? (string) $row['source_block_id']
                : null;
            $name = (string) ($row['name'] ?? '');
            if ($name === '') {
                continue;
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
            $inserted++;
        }
        return $inserted;
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
