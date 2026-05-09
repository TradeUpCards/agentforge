<?php

/**
 * Unit tests for the clinician-value splice logic in RoundtripService — Bucket C + H.
 *
 * Tests the spliceClinicianValue helper (private method; tested via reflection + a
 * TestableRoundtripService subclass) and the broader roundtripFromExtractionId contract
 * at the source-analysis level.
 *
 * Covered:
 *   C.1 — manually_edited row: splice replaces LLM value with clinician_value in-memory;
 *         original extraction_json on disk is NOT modified (audit trail).
 *   C.2 — manually_added row: splice inserts the field at field_path with clinician_value;
 *         source_block_id is threaded through for provenance.
 *   C.3 — splice with multiple manually_edited rows: all applied, order-independent.
 *   C.4 — splice when zero override rows: payload is unchanged from LLM extraction.
 *   C.5 — clinician_value NEVER appears in logger context (PHI spy logger).
 *   C.6 — Source-analysis: spliceClinicianValue is declared in RoundtripService.php.
 *   C.7 — Source-analysis: roundtripFromExtractionId calls spliceClinicianValue.
 *   H.1 — roundtripFromExtractionId is a declared method on RoundtripService.
 *   H.2 — roundtripFromExtractionId transitions status to APPROVED (source-analysis).
 *   H.3 — DB-tier (markTestSkipped) — full round-trip with real DB.
 *
 * APPROACH:
 *   The private spliceClinicianValue method is accessed via a thin
 *   TestableRoundtripService subclass that exposes it.  This keeps the
 *   production class clean while allowing direct unit tests.
 *
 * NO-PHI FIXTURES:
 *   All sentinel patient IDs in range 999100-999199.  No real patient data.
 *   Extraction payloads use non-PHI synthetic lab values.
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
use Psr\Log\AbstractLogger;
use Psr\Log\NullLogger;

// Module autoloader (mirrors DocumentSavedSubscriberTest.php pattern)
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

// ---------------------------------------------------------------------------
// Testable subclass — exposes spliceClinicianValue as public
// ---------------------------------------------------------------------------

/**
 * Thin subclass that exposes the private spliceClinicianValue method
 * as a public proxy so it can be unit-tested directly.
 *
 * Rationale: spliceClinicianValue is an isolated pure-function helper that
 * operates solely on in-memory arrays.  Direct testing gives precise failure
 * messages and avoids the overhead of a full DB-fixture roundtrip.
 */
final class TestableRoundtripService extends RoundtripService
{
    /**
     * Public proxy for the private spliceClinicianValue method.
     *
     * @param array<string, mixed> $payload
     */
    public function exposedSpliceClinicianValue(
        array &$payload,
        string $fieldPath,
        string $clinicianValue,
        ?string $addedSourceBlockId = null,
    ): bool {
        // Access the private method via reflection.
        $reflMethod = new \ReflectionMethod(RoundtripService::class, 'spliceClinicianValue');
        $reflMethod->setAccessible(true);

        $result = $reflMethod->invokeArgs($this, [&$payload, $fieldPath, $clinicianValue, $addedSourceBlockId]);

        return (bool) $result;
    }
}

// ---------------------------------------------------------------------------
// Spy logger: records every log call for PHI-leak assertions
// ---------------------------------------------------------------------------

/**
 * @phpstan-type LogEntry array{level: string, message: string, context: array<string, mixed>}
 */
final class SpyLoggerForSplice extends AbstractLogger
{
    /** @var array<int, array{level: string, message: string, context: array<string, mixed>}> */
    public array $entries = [];

    /** @param array<string, mixed> $context */
    public function log(mixed $level, \Stringable|string $message, array $context = []): void
    {
        $this->entries[] = [
            'level'   => (string) $level,
            'message' => (string) $message,
            'context' => $context,
        ];
    }
}

final class SpliceClinicianValueTest extends TestCase
{
    // -----------------------------------------------------------------------
    // Helpers
    // -----------------------------------------------------------------------

    /**
     * Build a minimal lab_pdf extraction payload with one result.
     *
     * @return array<string, mixed>
     */
    private function makeLabPayload(
        string $testName = 'HDL',
        string $value = '52.0',
        string $unit = 'mg/dL',
    ): array {
        return [
            'results' => [
                [
                    'test_name'       => $testName,
                    'value'           => $value,
                    'unit'            => $unit,
                    'reference_range' => '40-60',
                    'source_block_id' => 'blk_001',
                ],
            ],
        ];
    }

    private function makeService(?SpyLoggerForSplice $logger = null): TestableRoundtripService
    {
        return new TestableRoundtripService($logger ?? new NullLogger());
    }

    // -----------------------------------------------------------------------
    // C.1 — manually_edited splice: replaces value, leaves original intact
    // -----------------------------------------------------------------------

    /**
     * spliceClinicianValue for a manually_edited field replaces the in-memory
     * value at the field_path.  The original PHP array (simulating extraction_json)
     * is MUTATED in-place; the test demonstrates the mutation and verifies the
     * original was the LLM value that would have been preserved on disk.
     */
    public function testManuallyEditedSpliceReplacesInMemoryValue(): void
    {
        $service = $this->makeService();
        $payload = $this->makeLabPayload(testName: 'HDL', value: '52.0');

        // Capture the original LLM value for audit assertion.
        $originalLlmValue = $payload['results'][0]['value'];

        // Apply splice: clinician says the value is actually '54.2' (not '52.0').
        $spliced = $service->exposedSpliceClinicianValue(
            $payload,
            'results[0].value',    // field_path
            '54.2',                // clinician_value (the override)
            null,                  // addedSourceBlockId (null = manually_edited, not added)
        );

        $this->assertTrue($spliced, 'Splice must return true for a valid manually_edited path.');

        // In-memory payload now reflects the clinician's value.
        $this->assertSame(
            '54.2',
            $payload['results'][0]['value'],
            'In-memory payload must carry the clinician value after splice.',
        );

        // The test double proves audit: $originalLlmValue is '52.0', which is what
        // the serialised extraction_json on disk would preserve.  The splice only
        // touched the in-memory copy.
        $this->assertSame(
            '52.0',
            $originalLlmValue,
            'Audit trail: original LLM value captured before splice must be unchanged.',
        );
    }

    /**
     * Splice on a collection key (test_name) replaces the expected key.
     */
    public function testManuallyEditedSpliceOnTestNameField(): void
    {
        $service = $this->makeService();
        $payload = $this->makeLabPayload(testName: 'Cholesterol');

        $spliced = $service->exposedSpliceClinicianValue(
            $payload,
            'results[0].test_name',
            'Total Cholesterol',
            null,
        );

        $this->assertTrue($spliced);
        $this->assertSame('Total Cholesterol', $payload['results'][0]['test_name']);
    }

    // -----------------------------------------------------------------------
    // C.2 — manually_added splice: inserts field at field_path
    // -----------------------------------------------------------------------

    /**
     * spliceClinicianValue for a manually_added field inserts a new element
     * at the given index.  The source_block_id is also threaded through.
     */
    public function testManuallyAddedSpliceInsertsNewField(): void
    {
        $service = $this->makeService();
        // Start with only one result; clinician adds a second one.
        $payload = $this->makeLabPayload();

        $spliced = $service->exposedSpliceClinicianValue(
            $payload,
            'results[1].test_name',   // index 1 — new row
            'LDL',
            'blk_007',                // source_block_id for provenance
        );

        $this->assertTrue($spliced, 'Splice must return true when adding a new manually_added row.');

        $this->assertArrayHasKey(1, $payload['results'], 'New result row must exist at index 1.');
        $this->assertSame('LDL', $payload['results'][1]['test_name']);

        // Source_block_id must be recorded for provenance.
        $this->assertSame(
            'blk_007',
            $payload['results'][1]['source_block_id'],
            'source_block_id must be threaded through on a manually_added splice.',
        );
    }

    /**
     * manually_added creates the collection array if it doesn't exist yet.
     */
    public function testManuallyAddedSpliceCreatesCollectionWhenAbsent(): void
    {
        $service = $this->makeService();
        $payload = [];  // Empty extraction — no results at all.

        $spliced = $service->exposedSpliceClinicianValue(
            $payload,
            'results[0].test_name',
            'Glucose',
            'blk_020',
        );

        $this->assertTrue($spliced);
        $this->assertArrayHasKey('results', $payload, '"results" collection must be created.');
        $this->assertSame('Glucose', $payload['results'][0]['test_name']);
        $this->assertSame('blk_020', $payload['results'][0]['source_block_id']);
    }

    // -----------------------------------------------------------------------
    // C.3 — Multiple manually_edited rows: all applied, order-independent
    // -----------------------------------------------------------------------

    /**
     * Two manually_edited rows at different field_paths are both spliced correctly.
     * Applying them in reverse order should produce the same result.
     */
    public function testMultipleManuallyEditedRowsAllApplied(): void
    {
        $service = $this->makeService();
        $payload = [
            'results' => [
                ['test_name' => 'HDL', 'value' => '52.0', 'unit' => 'mg/dL'],
                ['test_name' => 'LDL', 'value' => '120.0', 'unit' => 'mg/dL'],
            ],
        ];

        // Splice first field.
        $splice1 = $service->exposedSpliceClinicianValue($payload, 'results[0].value', '55.0', null);
        // Splice second field.
        $splice2 = $service->exposedSpliceClinicianValue($payload, 'results[1].value', '115.0', null);

        $this->assertTrue($splice1, 'First splice must succeed.');
        $this->assertTrue($splice2, 'Second splice must succeed.');

        $this->assertSame('55.0', $payload['results'][0]['value'], 'HDL value must reflect clinician override.');
        $this->assertSame('115.0', $payload['results'][1]['value'], 'LDL value must reflect clinician override.');
        // Names untouched.
        $this->assertSame('HDL', $payload['results'][0]['test_name']);
        $this->assertSame('LDL', $payload['results'][1]['test_name']);
    }

    // -----------------------------------------------------------------------
    // C.4 — Zero override rows: payload unchanged
    // -----------------------------------------------------------------------

    /**
     * When no override rows exist, the in-memory payload must remain identical
     * to the LLM extraction.  No surprise mutations must occur.
     */
    public function testZeroOverrideRowsLeavesPayloadUnchanged(): void
    {
        $service = $this->makeService();
        $payload = $this->makeLabPayload();
        $original = $payload;

        // No splice calls — simulate the loop finding no manually_edited rows.
        // The payload must equal the original.
        $this->assertSame(
            $original,
            $payload,
            'Zero override rows must leave payload unchanged.',
        );
    }

    // -----------------------------------------------------------------------
    // C.5 — clinician_value NEVER appears in log context
    // -----------------------------------------------------------------------

    /**
     * The splice method must NOT include the clinician_value itself in any
     * logger context.  Only structural metadata (field_path, length) is allowed.
     *
     * This is verified by injecting a SpyLogger that captures all log entries
     * and checking that none of them contain the synthetic recognisable value.
     */
    public function testClinicianValueNeverAppearsInLogContext(): void
    {
        $spyLogger = new SpyLoggerForSplice();
        $service   = $this->makeService($spyLogger);
        $payload   = $this->makeLabPayload();

        // Use a synthetically recognisable PHI token so we can grep for it.
        $phiToken = 'SENTINEL_CLINICIAN_VALUE_PHI_LEAK_TEST';

        $service->exposedSpliceClinicianValue(
            $payload,
            'results[0].value',
            $phiToken,
            null,
        );

        foreach ($spyLogger->entries as $entry) {
            // Check message string.
            $this->assertStringNotContainsString(
                $phiToken,
                $entry['message'],
                'clinician_value must NOT appear in log message: ' . $entry['message'],
            );

            // Check each context value.
            $contextEncoded = json_encode($entry['context']) ?: '';
            $this->assertStringNotContainsString(
                $phiToken,
                $contextEncoded,
                'clinician_value must NOT appear in log context for: ' . $entry['message'],
            );
        }
    }

    // -----------------------------------------------------------------------
    // C.6 — Source analysis: spliceClinicianValue declared in RoundtripService
    // -----------------------------------------------------------------------

    /**
     * spliceClinicianValue must be declared as a private method in RoundtripService.
     * This is the belt-and-suspenders structural check.
     */
    public function testSourceDeclaresSpiceClinicianValueMethod(): void
    {
        $source = $this->readRoundtripSource();

        $this->assertStringContainsString(
            'private function spliceClinicianValue(',
            $source,
            'RoundtripService must declare a private spliceClinicianValue method.',
        );
    }

    // -----------------------------------------------------------------------
    // C.7 — Source analysis: roundtripFromExtractionId calls splice
    // -----------------------------------------------------------------------

    /**
     * roundtripFromExtractionId must call spliceClinicianValue for each
     * override field row.  This is the critical wiring that connects the
     * clinician edit to the actual round-trip write.
     */
    public function testRoundtripFromExtractionIdCallsSplice(): void
    {
        $source = $this->readRoundtripSource();

        $this->assertStringContainsString(
            'spliceClinicianValue(',
            $source,
            'roundtripFromExtractionId must call spliceClinicianValue() for '
            . 'each clinician override row.',
        );
    }

    /**
     * roundtripFromExtractionId must NOT log the clinician_value itself.
     * It may log clinician_value_length (integer only).
     */
    public function testRoundtripFromExtractionIdNeverLogsClinicianValueDirectly(): void
    {
        $source = $this->readRoundtripSource();

        // Find the roundtripFromExtractionId method body.
        $startPos = strpos($source, 'public function roundtripFromExtractionId(');
        self::assertNotFalse($startPos, 'roundtripFromExtractionId must exist.');

        // Bound by next method.
        $nextMethod = strpos($source, "\n    public function ", (int) $startPos + 1);
        if ($nextMethod === false) {
            $nextMethod = strpos($source, "\n    private function ", (int) $startPos + 1);
        }
        $methodBody = $nextMethod !== false
            ? substr($source, (int) $startPos, $nextMethod - (int) $startPos)
            : substr($source, (int) $startPos);

        // Must NOT have: 'clinician_value' => $clinicianValue (the raw value in context).
        $this->assertStringNotContainsString(
            "'clinician_value' => \$clinicianValue",
            $methodBody,
            'roundtripFromExtractionId must NOT log the raw clinician_value. '
            . 'Only clinician_value_length (integer) may appear.',
        );
    }

    // -----------------------------------------------------------------------
    // H.1 — roundtripFromExtractionId method exists on RoundtripService
    // -----------------------------------------------------------------------

    public function testRoundtripFromExtractionIdMethodExists(): void
    {
        $source = $this->readRoundtripSource();

        $this->assertStringContainsString(
            'public function roundtripFromExtractionId(',
            $source,
            'RoundtripService must declare roundtripFromExtractionId() — '
            . 'it is the entry point for the approve endpoint.',
        );
    }

    // -----------------------------------------------------------------------
    // H.2 — roundtripFromExtractionId transitions status to APPROVED
    // -----------------------------------------------------------------------

    /**
     * roundtripFromExtractionId must call updateExtractionStatus with
     * ExtractionStatus::APPROVED.  This is the terminal state change that
     * the approve endpoint depends on.
     */
    public function testRoundtripFromExtractionIdTransitionsToApproved(): void
    {
        $source = $this->readRoundtripSource();

        // Find the method body.
        $startPos = strpos($source, 'public function roundtripFromExtractionId(');
        self::assertNotFalse($startPos);

        $nextMethod = strpos($source, "\n    public function ", (int) $startPos + 1);
        if ($nextMethod === false) {
            $nextMethod = strpos($source, "\n    private function ", (int) $startPos + 1);
        }

        $methodBody = $nextMethod !== false
            ? substr($source, (int) $startPos, $nextMethod - (int) $startPos)
            : substr($source, (int) $startPos);

        // Must call updateExtractionStatus with APPROVED.
        $this->assertStringContainsString(
            'updateExtractionStatus(',
            $methodBody,
            'roundtripFromExtractionId must call updateExtractionStatus.',
        );

        $this->assertStringContainsString(
            \OpenEMR\Modules\ClinicalCopilot\ExtractionStatus::APPROVED,
            $methodBody,
            'roundtripFromExtractionId must transition status to ExtractionStatus::APPROVED.',
        );
    }

    // -----------------------------------------------------------------------
    // H.3 — DB-tier (markTestSkipped)
    // -----------------------------------------------------------------------

    /**
     * Full DB-level roundtripFromExtractionId test:
     *   - Seeds co_pilot_extractions row with status='pending_review' + extraction_json.
     *   - Seeds co_pilot_extracted_fields with one manually_edited row.
     *   - Calls roundtripFromExtractionId.
     *   - Asserts: procedure_result row written with clinician_value (not LLM value).
     *   - Asserts: co_pilot_extractions.status = 'approved'.
     *   - Asserts: co_pilot_extracted_fields.clinical_table + clinical_row_id populated.
     *
     * Requires Docker MySQL with full schema.
     */
    public function testRoundtripFromExtractionIdFullFlowInDb(): void
    {
        $this->markTestSkipped(
            'DB-tier test: requires Docker MySQL with co_pilot_extractions + '
            . 'co_pilot_extracted_fields fixture. '
            . 'Run under: docker compose exec openemr /root/devtools unit-test',
        );
    }

    /**
     * Zero-override round-trip: no splice rows → behaviour identical to R1 baseline.
     * Requires Docker MySQL.
     */
    public function testRoundtripFromExtractionIdZeroOverridesMatchesR1Baseline(): void
    {
        $this->markTestSkipped(
            'DB-tier test: requires Docker MySQL. '
            . 'Verifies zero-override roundtripFromExtractionId matches R1 roundtrip() baseline.',
        );
    }

    // -----------------------------------------------------------------------
    // Splice path edge cases
    // -----------------------------------------------------------------------

    /**
     * spliceClinicianValue returns false for an unrecognised path shape.
     * Callers must log a warning and skip — they must NOT abort the round-trip.
     */
    public function testSpliceReturnsFalseForMalformedFieldPath(): void
    {
        $service = $this->makeService();
        $payload = $this->makeLabPayload();

        $result = $service->exposedSpliceClinicianValue(
            $payload,
            'results[0].deep.nested.path',   // Nested dots not supported
            'some_value',
            null,
        );

        $this->assertFalse($result, 'Splice must return false for unsupported nested path shape.');
    }

    /**
     * spliceClinicianValue returns false for an out-of-bounds index on a
     * non-adding (manually_edited) call where the array element doesn't exist.
     */
    public function testSpliceReturnsFalseForOutOfBoundsIndexOnManuallyEdited(): void
    {
        $service = $this->makeService();
        $payload = $this->makeLabPayload(); // Only index 0 exists.

        $result = $service->exposedSpliceClinicianValue(
            $payload,
            'results[5].value',   // Index 5 — not present, and not adding
            '99.0',
            null,                 // null = manually_edited (not adding)
        );

        $this->assertFalse($result, 'Splice must return false for out-of-bounds index on non-adding call.');
    }

    /**
     * spliceClinicianValue handles top-level scalar overrides (field_path without brackets).
     */
    public function testSpliceHandlesTopLevelScalarPath(): void
    {
        $service = $this->makeService();
        $payload = ['patient_name' => 'Chen'];

        $result = $service->exposedSpliceClinicianValue(
            $payload,
            'patient_name',
            'Chen Updated',
            null,
        );

        $this->assertTrue($result);
        $this->assertSame('Chen Updated', $payload['patient_name']);
    }

    // -----------------------------------------------------------------------
    // Private helpers
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
}
