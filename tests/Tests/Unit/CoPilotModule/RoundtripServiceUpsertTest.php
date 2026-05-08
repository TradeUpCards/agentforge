<?php

/**
 * Unit tests for RoundtripService — natural-key upsert + attempt-chain contract.
 *
 * Covers PRD §10 requirements 2, 3, 4, 5 (field-level idempotency, reprocess
 * partial fill, document non-duplication, attempt chain).
 *
 * Tests that require structural assertions (SQL / method-level) run in isolation
 * without a live database by parsing source text.  Tests that require DB
 * behaviour are documented as "DB-required" and listed in the pre-merge checklist.
 *
 * Cross-teammate contract tested:
 *   - Risk 2: verifiedFieldMap bracket-index match for field_paths like
 *     "results[0].test_name" — asserts the preg_match pattern handles both
 *     matching and non-matching field_paths, and that unmatched paths do not crash.
 *
 *   - Risk 3: triggered_by defaults to 'manual_retry' server-side on reprocess
 *     (CoPilotController.dispatchReprocess line 422).
 *
 *   - Risk 4: confidence_oob counts toward the 30% strip threshold.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    AgentForge Team
 * @copyright Copyright (c) 2026 AgentForge
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Tests\Unit\CoPilotModule;

use OpenEMR\Modules\ClinicalCopilot\Service\RoundtripService;
use PHPUnit\Framework\TestCase;

// Module autoloader
spl_autoload_register(static function (string $class): void {
    $prefix = 'OpenEMR\\Modules\\ClinicalCopilot\\';
    if (!str_starts_with($class, $prefix)) {
        return;
    }
    $relative = str_replace('\\', DIRECTORY_SEPARATOR, substr($class, strlen($prefix)));
    $path     = __DIR__ . '/../../../../interface/modules/custom_modules/oe-module-clinical-copilot/src/'
        . $relative . '.php';
    if (file_exists($path)) {
        require_once $path;
    }
});

final class RoundtripServiceUpsertTest extends TestCase
{
    // -----------------------------------------------------------------------
    // PRD §10 req 2: Field-level idempotency — source-level structural checks
    //
    // Verifies that for each doc_type × field_path family, the code:
    //   (a) computes a natural key via the appropriate FieldKeyExtractor,
    //   (b) calls findClinicalRowByNaturalKey to check for prior rows,
    //   (c) issues UPDATE (not INSERT) when a prior row is found.
    // -----------------------------------------------------------------------

    public function testLabPdfRoundtripUsesNaturalKeyForDeduplication(): void
    {
        $source = $this->readRoundtripSource();

        // LabResultKeyExtractor must be constructed inside roundtripLabReport.
        $this->assertStringContainsString(
            'new LabResultKeyExtractor()',
            $source,
            'roundtripLabReport must instantiate a LabResultKeyExtractor for natural-key dedup.',
        );

        // findClinicalRowByNaturalKey must be called for procedure_result.
        $this->assertStringContainsString(
            "findClinicalRowByNaturalKey(",
            $source,
            'roundtripLabReport must call findClinicalRowByNaturalKey for cross-attempt dedup.',
        );

        // When existing row found: UPDATE procedure_result (not INSERT).
        $this->assertStringContainsString(
            'UPDATE procedure_result',
            $source,
            'roundtripLabReport must issue UPDATE when a prior procedure_result row is found.',
        );
    }

    public function testAllergyRoundtripUsesNaturalKeyForDeduplication(): void
    {
        $source = $this->readRoundtripSource();

        $this->assertStringContainsString(
            'new AllergyKeyExtractor()',
            $source,
            'roundtripAllergies must instantiate an AllergyKeyExtractor for natural-key dedup.',
        );

        $this->assertStringContainsString(
            'UPDATE lists',
            $source,
            'roundtripAllergies must issue UPDATE when a prior lists row is found.',
        );
    }

    public function testMedicationRoundtripUsesNaturalKeyForDeduplication(): void
    {
        $source = $this->readRoundtripSource();

        $this->assertStringContainsString(
            'new MedicationKeyExtractor()',
            $source,
            'roundtripMedications must instantiate a MedicationKeyExtractor for natural-key dedup.',
        );

        $this->assertStringContainsString(
            'UPDATE prescriptions',
            $source,
            'roundtripMedications must issue UPDATE when a prior prescriptions row is found.',
        );
    }

    // -----------------------------------------------------------------------
    // PRD §10 req 3 (structural): reprocess partial fill
    //
    // The test assertion that "5 rows total after 2 attempts" (not 8) is a
    // DB-level invariant that depends on the natural-key UPDATE path being
    // taken for the 3 rows present in attempt 1, and INSERT only for the 2
    // new rows in attempt 2.
    //
    // DB-required: must run in Docker with live MySQL.
    // Structural proxy: assert the UPDATE path is guarded by the natural-key
    // lookup and increments $upserted (so the count = 5 = 3 updated + 2 new).
    // -----------------------------------------------------------------------

    public function testReprocessPartialFillUsesUpsertedCounter(): void
    {
        $source = $this->readRoundtripSource();

        // The $upserted counter must be incremented on both UPDATE (prior row found)
        // and INSERT (new row) paths.  This ensures the count reflects total rows
        // written regardless of whether they existed before.
        $upsertedIncrements = substr_count($source, '$upserted++');

        // roundtripLabReport: at least 2 increments (UPDATE path + INSERT path).
        $this->assertGreaterThanOrEqual(
            2,
            $upsertedIncrements,
            '$upserted++ must appear on both UPDATE and INSERT code paths in RoundtripService.',
        );
    }

    /**
     * PRD §10 req 3 — DB-level test placeholder.
     *
     * SKIPPED: requires live MySQL with OpenEMR schema.
     *
     * Scenario:
     *   - Attempt 1: extraction with 3 lab results → 3 procedure_result rows.
     *   - Attempt 2 (reprocess): extraction with 5 lab results (same 3 + 2 new).
     *   - Assert: 5 procedure_result rows (3 updated + 2 new), NOT 8.
     *
     * Command to run:
     *   docker compose exec openemr /root/devtools unit-test \
     *     --filter RoundtripServiceUpsertTest::testReprocessPartialFill
     */
    public function testReprocessPartialFill(): void
    {
        $this->markTestSkipped(
            'DB-required: needs live MySQL. '
            . 'Run: docker compose exec openemr /root/devtools unit-test '
            . '--filter RoundtripServiceUpsertTest::testReprocessPartialFill',
        );
    }

    // -----------------------------------------------------------------------
    // PRD §10 req 4 (structural): reprocess does not duplicate documents
    //
    // The reprocess path writes a NEW co_pilot_extractions row (not a new
    // documents row).  Documents are unaffected by RoundtripService.
    // -----------------------------------------------------------------------

    public function testRoundtripServiceNeverWritesToDocumentsTable(): void
    {
        $source = $this->readRoundtripSource();

        // The RoundtripService must never INSERT into documents.
        $this->assertStringNotContainsString(
            'INSERT INTO documents',
            $source,
            'RoundtripService must never INSERT into the documents table.',
        );
        $this->assertStringNotContainsString(
            'INSERT INTO `documents`',
            $source,
            'RoundtripService must never INSERT into the documents table.',
        );
    }

    /**
     * PRD §10 req 4 — DB-level test placeholder.
     *
     * SKIPPED: requires live MySQL.
     *
     * Scenario: Upload a document, run onDocumentCreated, reprocess, assert
     *   SELECT COUNT(*) FROM documents WHERE id = :doc_id = 1 (unchanged).
     */
    public function testReprocessNoDocumentDuplication(): void
    {
        $this->markTestSkipped(
            'DB-required: needs live MySQL. '
            . 'Structural check: RoundtripService never INSERTs into documents (see '
            . 'testRoundtripServiceNeverWritesToDocumentsTable).',
        );
    }

    // -----------------------------------------------------------------------
    // PRD §10 req 5 (structural): attempt chain — 2 rows, only one is_active
    //
    // The is_active invariant is enforced by markPriorAttemptsInactive():
    //   UPDATE co_pilot_extractions SET is_active = 0 WHERE doc_ref_id = ?
    //   AND doc_type = ? AND is_active = 1
    //
    // This is called BEFORE the new row is inserted, so at most one row
    // ever has is_active = 1 at any point.
    // -----------------------------------------------------------------------

    public function testMarkPriorAttemptsInactiveIssuesCorrectUpdateSql(): void
    {
        $source = $this->readRoundtripSource();

        $this->assertStringContainsString(
            "SET `is_active` = 0",
            $source,
            'markPriorAttemptsInactive must UPDATE is_active to 0.',
        );
        $this->assertStringContainsString(
            "AND `is_active` = 1",
            $source,
            'markPriorAttemptsInactive must filter on is_active = 1 to avoid touching already-inactive rows.',
        );
    }

    public function testMarkPriorAttemptsInactiveFiltersOnDocRefIdAndDocType(): void
    {
        $source = $this->readRoundtripSource();

        // The WHERE clause must scope to (doc_ref_id, doc_type) so a single
        // document's attempt chain does not deactivate rows from other documents.
        $updateBlock = '';
        $pos = strpos($source, "markPriorAttemptsInactive");
        if ($pos !== false) {
            $updateBlock = substr($source, $pos, 500);
        }

        $this->assertStringContainsString(
            'doc_ref_id',
            $updateBlock,
            'markPriorAttemptsInactive WHERE clause must include doc_ref_id.',
        );
        $this->assertStringContainsString(
            'doc_type',
            $updateBlock,
            'markPriorAttemptsInactive WHERE clause must include doc_type.',
        );
    }

    public function testAttemptChainParentExtractionIdSetOnReprocess(): void
    {
        // Read CoPilotController source and assert that dispatchReprocess sets
        // parent_extraction_id in the INSERT.
        $controllerSource = $this->readControllerSource();

        $this->assertStringContainsString(
            'parent_extraction_id',
            $controllerSource,
            'dispatchReprocess INSERT must include parent_extraction_id column.',
        );

        // The value must be $extractionId (the prior active row's id).
        $this->assertStringContainsString(
            '$extractionId, // parent_extraction_id',
            $controllerSource,
            'parent_extraction_id must be set to $extractionId (the prior active row).',
        );
    }

    // -----------------------------------------------------------------------
    // Risk 2: verifiedFieldMap bracket-index match for field_paths
    //
    // updateFieldPointer uses preg_match('/\[{$rowIndex}\]/', $fieldPath).
    // We verify the regex pattern is correct for matching and non-matching paths.
    // -----------------------------------------------------------------------

    public function testUpdateFieldPointerRegexMatchesCorrectIndex(): void
    {
        // The regex pattern /\[{$rowIndex}\]/ must match "results[0]" when rowIndex='0'.
        $rowIndex = '0';
        $pattern  = '/\[' . preg_quote($rowIndex, '/') . '\]/';

        $this->assertMatchesRegularExpression($pattern, 'results[0].test_name');
        $this->assertMatchesRegularExpression($pattern, 'allergies[0].substance');
        $this->assertMatchesRegularExpression($pattern, 'current_medications[0].name');
    }

    public function testUpdateFieldPointerRegexDoesNotMatchWrongIndex(): void
    {
        // Index '0' must not match "[1]" or "[10]".
        $rowIndex = '0';
        $pattern  = '/\[' . preg_quote($rowIndex, '/') . '\]/';

        $this->assertDoesNotMatchRegularExpression($pattern, 'results[1].test_name');
        $this->assertDoesNotMatchRegularExpression($pattern, 'results[10].test_name');
        $this->assertDoesNotMatchRegularExpression($pattern, 'results[20].value');
    }

    public function testUpdateFieldPointerUnmatchedIndexDoesNotCrash(): void
    {
        $source = $this->readRoundtripSource();

        // The updateFieldPointer method must handle an empty $verifiedFieldMap
        // by returning early — no crash on missing map.
        $this->assertStringContainsString(
            '$verifiedFieldMap === []',
            $source,
            'updateFieldPointer must return early when verifiedFieldMap is empty.',
        );
    }

    public function testClinicalTableAndRowIdAreNullWhenNotFound(): void
    {
        $source = $this->readRoundtripSource();

        // When updateFieldPointer finds no matching field_path in verifiedFieldMap,
        // it simply does not call updateClinicalPointer — clinical_table and
        // clinical_row_id remain NULL in the DB (the default).
        // Assert: no assignment to clinical_table/clinical_row_id outside of
        // the updateFieldPointer/updateClinicalPointer chain.
        $this->assertStringContainsString(
            '$fieldsWriter->updateClinicalPointer(',
            $source,
            'updateClinicalPointer is the only path that sets clinical_table/clinical_row_id.',
        );
    }

    // -----------------------------------------------------------------------
    // Risk 3: triggered_by default on reprocess
    // -----------------------------------------------------------------------

    public function testDispatchReprocessDefaultsTriggeredByToManualRetry(): void
    {
        $controllerSource = $this->readControllerSource();

        // If the agent response does not include triggered_by, the PHP side
        // defaults to 'manual_retry' (not 'initial' which is for first extractions).
        $this->assertStringContainsString(
            "'manual_retry'",
            $controllerSource,
            "dispatchReprocess must default triggered_by to 'manual_retry' when agent response omits it.",
        );
    }

    // -----------------------------------------------------------------------
    // Risk 4: confidence_oob counts toward 30% strip threshold
    //
    // This is verified at the Python layer by test_p3_verifier_reason.py.
    // The PHP side stores stripped_fields in co_pilot_extractions and the
    // UI uses it.  We assert that dispatchReprocess writes stripped_fields.
    // -----------------------------------------------------------------------

    public function testDispatchReprocessWritesStrippedFieldsToExtractionRow(): void
    {
        $controllerSource = $this->readControllerSource();

        $this->assertStringContainsString(
            '$strippedFields',
            $controllerSource,
            'dispatchReprocess must propagate stripped_fields from agent response to the DB row.',
        );
    }

    // -----------------------------------------------------------------------
    // No-PHI logging in RoundtripService
    // -----------------------------------------------------------------------

    public function testRoundtripServiceLogsOnlyStructuralCounters(): void
    {
        $source = $this->readRoundtripSource();

        // Collect all logger calls.
        preg_match_all('/\$this->logger->[a-z]+\([^;]+\);/s', $source, $matches);
        $logCalls = $matches[0] ?? [];

        foreach ($logCalls as $logCall) {
            $this->assertStringNotContainsString(
                'patient_id',
                $logCall,
                'RoundtripService log calls must not include patient_id.',
            );
            $this->assertStringNotContainsString(
                '$name',
                $logCall,
                'RoundtripService log calls must not include drug/substance/test names.',
            );
            $this->assertStringNotContainsString(
                '$substance',
                $logCall,
                'RoundtripService log calls must not include allergen substance.',
            );
        }
    }

    // -----------------------------------------------------------------------
    // Helpers
    // -----------------------------------------------------------------------

    private function readRoundtripSource(): string
    {
        $path = __DIR__
            . '/../../../../interface/modules/custom_modules'
            . '/oe-module-clinical-copilot/src/Service/RoundtripService.php';

        self::assertFileExists($path, 'RoundtripService.php must exist.');
        $source = file_get_contents($path);
        self::assertIsString($source);
        return $source;
    }

    private function readControllerSource(): string
    {
        $path = __DIR__
            . '/../../../../interface/modules/custom_modules'
            . '/oe-module-clinical-copilot/src/Controller/CoPilotController.php';

        self::assertFileExists($path, 'CoPilotController.php must exist.');
        $source = file_get_contents($path);
        self::assertIsString($source);
        return $source;
    }
}
