<?php

/**
 * Unit tests for ExtractionStatus constants — Bucket B.
 *
 * Validates:
 *   - FIELD_MANUALLY_EDITED and FIELD_MANUALLY_ADDED constants exist with
 *     the correct string values.
 *   - All constant values fit within VARCHAR(16).
 *   - The class doc-comment explains the dual purpose (extraction-row vs
 *     field-row statuses) so future readers are not confused.
 *   - clinicianOverrideStatuses() returns both new R2 constants.
 *   - No production PHP files outside sql/ reference the raw string literals
 *     'manually_edited' or 'manually_added' directly instead of the constants.
 *
 * All tests are source-analysis only — no live DB required.
 *
 * NO-PHI FIXTURES:
 *   This file contains only constant-value strings ('manually_edited',
 *   'manually_added'), not patient data.  No patient IDs used.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    AgentForge Team
 * @copyright Copyright (c) 2026 AgentForge
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Tests\Unit\CoPilotModule;

use PHPUnit\Framework\TestCase;

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

final class ExtractionStatusConstantsTest extends TestCase
{
    // -----------------------------------------------------------------------
    // Setup: load ExtractionStatus source for source-analysis assertions
    // -----------------------------------------------------------------------

    private string $statusSource;

    private const MODULE_SRC = __DIR__
        . '/../../../../interface/modules/custom_modules'
        . '/oe-module-clinical-copilot/src/';

    protected function setUp(): void
    {
        $path = self::MODULE_SRC . 'ExtractionStatus.php';
        self::assertFileExists($path, 'ExtractionStatus.php must exist.');
        $content = file_get_contents($path);
        self::assertIsString($content);
        $this->statusSource = $content;
    }

    // -----------------------------------------------------------------------
    // Bucket B.1 — FIELD_MANUALLY_EDITED constant
    // -----------------------------------------------------------------------

    /**
     * FIELD_MANUALLY_EDITED must exist with value 'manually_edited'.
     */
    public function testFieldManuallyEditedConstantHasCorrectValue(): void
    {
        // Runtime check via class constant.
        $this->assertSame(
            'manually_edited',
            \OpenEMR\Modules\ClinicalCopilot\ExtractionStatus::FIELD_MANUALLY_EDITED,
            'ExtractionStatus::FIELD_MANUALLY_EDITED must equal "manually_edited".',
        );
    }

    /**
     * FIELD_MANUALLY_EDITED value must fit within VARCHAR(16).
     */
    public function testFieldManuallyEditedFitsVarchar16(): void
    {
        $value = \OpenEMR\Modules\ClinicalCopilot\ExtractionStatus::FIELD_MANUALLY_EDITED;
        $this->assertLessThanOrEqual(
            16,
            strlen($value),
            'FIELD_MANUALLY_EDITED value "' . $value . '" exceeds VARCHAR(16) — '
            . 'the co_pilot_extracted_fields.status column is VARCHAR(16).',
        );
    }

    // -----------------------------------------------------------------------
    // Bucket B.2 — FIELD_MANUALLY_ADDED constant
    // -----------------------------------------------------------------------

    /**
     * FIELD_MANUALLY_ADDED must exist with value 'manually_added'.
     */
    public function testFieldManuallyAddedConstantHasCorrectValue(): void
    {
        $this->assertSame(
            'manually_added',
            \OpenEMR\Modules\ClinicalCopilot\ExtractionStatus::FIELD_MANUALLY_ADDED,
            'ExtractionStatus::FIELD_MANUALLY_ADDED must equal "manually_added".',
        );
    }

    /**
     * FIELD_MANUALLY_ADDED value must fit within VARCHAR(16).
     */
    public function testFieldManuallyAddedFitsVarchar16(): void
    {
        $value = \OpenEMR\Modules\ClinicalCopilot\ExtractionStatus::FIELD_MANUALLY_ADDED;
        $this->assertLessThanOrEqual(
            16,
            strlen($value),
            'FIELD_MANUALLY_ADDED value "' . $value . '" exceeds VARCHAR(16) — '
            . 'the co_pilot_extracted_fields.status column is VARCHAR(16).',
        );
    }

    // -----------------------------------------------------------------------
    // Bucket B.3 — ALL extraction-row status constants fit VARCHAR(16)
    // -----------------------------------------------------------------------

    /**
     * @return array<string, array{string}>
     *
     * @codeCoverageIgnore Data providers run before coverage instrumentation starts.
     */
    public static function allStatusValueProvider(): array
    {
        return [
            'PENDING_REVIEW'       => ['pending_review'],
            'APPROVED'             => ['approved'],
            'REJECTED'             => ['rejected'],
            'ERROR'                => ['error'],
            'REFUSED'              => ['refused'],
            'OK'                   => ['ok'],
            'FIELD_VERIFIED'       => ['verified'],
            'FIELD_STRIPPED'       => ['stripped'],
            'FIELD_MANUALLY_EDITED'=> ['manually_edited'],
            'FIELD_MANUALLY_ADDED' => ['manually_added'],
        ];
    }

    /**
     * Every status value fits within VARCHAR(16).
     *
     * @dataProvider allStatusValueProvider
     */
    public function testAllStatusValuesFitVarchar16(string $value): void
    {
        $this->assertLessThanOrEqual(
            16,
            strlen($value),
            "Status value \"{$value}\" exceeds VARCHAR(16) — schema column is VARCHAR(16).",
        );
    }

    // -----------------------------------------------------------------------
    // Bucket B.4 — Dual-purpose class is clearly documented
    // -----------------------------------------------------------------------

    /**
     * The ExtractionStatus class docblock must explain the dual purpose
     * (extraction-row vs field-row statuses) so future readers are not confused.
     */
    public function testClassDocblockExplainsDualPurpose(): void
    {
        // Check for the distinctive R2 decision note: single class, dual purpose.
        $this->assertStringContainsString(
            'DISTINCT',
            $this->statusSource,
            'ExtractionStatus class docblock must note that extraction-row statuses '
            . 'and field-row statuses are DISTINCT sets.',
        );

        // Must reference both tables to be unambiguous.
        $this->assertStringContainsString(
            'co_pilot_extractions',
            $this->statusSource,
            'ExtractionStatus docblock must reference co_pilot_extractions.status.',
        );
        $this->assertStringContainsString(
            'co_pilot_extracted_fields',
            $this->statusSource,
            'ExtractionStatus docblock must reference co_pilot_extracted_fields.status.',
        );
    }

    // -----------------------------------------------------------------------
    // Bucket B.5 — clinicianOverrideStatuses() returns both R2 constants
    // -----------------------------------------------------------------------

    /**
     * clinicianOverrideStatuses() must include both FIELD_MANUALLY_EDITED and
     * FIELD_MANUALLY_ADDED so the splice logic in roundtripFromExtractionId
     * picks up both override types.
     */
    public function testClinicianOverrideStatusesReturnsBothConstants(): void
    {
        $overrides = \OpenEMR\Modules\ClinicalCopilot\ExtractionStatus::clinicianOverrideStatuses();

        $this->assertContains(
            \OpenEMR\Modules\ClinicalCopilot\ExtractionStatus::FIELD_MANUALLY_EDITED,
            $overrides,
            'clinicianOverrideStatuses() must include FIELD_MANUALLY_EDITED.',
        );

        $this->assertContains(
            \OpenEMR\Modules\ClinicalCopilot\ExtractionStatus::FIELD_MANUALLY_ADDED,
            $overrides,
            'clinicianOverrideStatuses() must include FIELD_MANUALLY_ADDED.',
        );

        // Exactly the two R2 override statuses — no extra entries crept in.
        $this->assertCount(
            2,
            $overrides,
            'clinicianOverrideStatuses() must return exactly 2 entries (manually_edited + manually_added).',
        );
    }

    // -----------------------------------------------------------------------
    // Bucket B.6 — No raw string literals in production PHP files
    // -----------------------------------------------------------------------

    /**
     * No production PHP file (outside sql/) should reference the raw strings
     * 'manually_edited' or 'manually_added' as string literals instead of
     * using ExtractionStatus constants.
     *
     * This is a source-analysis grep test to catch constant-discipline regressions.
     */
    public function testNoRawManuallyEditedLiteralInProductionPhp(): void
    {
        $moduleRoot = realpath(
            __DIR__ . '/../../../../interface/modules/custom_modules/oe-module-clinical-copilot'
        );
        self::assertNotFalse($moduleRoot, 'Module root directory must be resolvable.');

        $this->assertNoPHPLiteralInDirectory(
            (string) $moduleRoot,
            'manually_edited',
            'Use ExtractionStatus::FIELD_MANUALLY_EDITED instead of the raw string "manually_edited".',
        );
    }

    public function testNoRawManuallyAddedLiteralInProductionPhp(): void
    {
        $moduleRoot = realpath(
            __DIR__ . '/../../../../interface/modules/custom_modules/oe-module-clinical-copilot'
        );
        self::assertNotFalse($moduleRoot, 'Module root directory must be resolvable.');

        $this->assertNoPHPLiteralInDirectory(
            (string) $moduleRoot,
            'manually_added',
            'Use ExtractionStatus::FIELD_MANUALLY_ADDED instead of the raw string "manually_added".',
        );
    }

    // -----------------------------------------------------------------------
    // Bucket B.7 — PHI note is present in field-row constant docblocks
    // -----------------------------------------------------------------------

    /**
     * The PHI custody note for clinician_value must appear in the class source.
     * This ensures any reader of the constants file understands the data governance
     * obligation — clinician_value is PHI-classified and must not be logged.
     */
    public function testPhiNoteIsPresentForManuallyEditedConstant(): void
    {
        $this->assertStringContainsString(
            'PHI',
            $this->statusSource,
            'ExtractionStatus must include a PHI note warning that clinician_value '
            . 'must not be logged or echoed outside the splice/round-trip path.',
        );

        // Specifically the "never logged" instruction.
        $this->assertStringContainsString(
            'MUST NOT',
            $this->statusSource,
            'PHI note must use emphatic language (MUST NOT) so the obligation is clear.',
        );
    }

    // -----------------------------------------------------------------------
    // Helpers
    // -----------------------------------------------------------------------

    /**
     * Assert that no PHP file in $directory (recursively) contains the raw
     * string $literal as a quoted string.  Excludes sql/ sub-directories
     * (where the literal is expected as SQL comment/column name).
     */
    private function assertNoPHPLiteralInDirectory(
        string $directory,
        string $literal,
        string $message,
    ): void {
        $iterator = new \RecursiveIteratorIterator(
            new \RecursiveDirectoryIterator($directory, \RecursiveDirectoryIterator::SKIP_DOTS),
        );

        foreach ($iterator as $file) {
            /** @var \SplFileInfo $file */
            if ($file->getExtension() !== 'php') {
                continue;
            }

            // Skip test files and sql subdirectories.
            $filePath = str_replace('\\', '/', (string) $file->getRealPath());
            if (str_contains($filePath, '/tests/') || str_contains($filePath, '/sql/')) {
                continue;
            }

            $content = file_get_contents($filePath);
            if ($content === false) {
                continue;
            }

            // Look for the literal as a single-quoted or double-quoted PHP string.
            // Exclude constant definition lines (the class that DEFINES the constant
            // is allowed to have the raw string on the right-hand side of '=').
            $lines = explode("\n", $content);
            foreach ($lines as $lineNo => $line) {
                // Allow: const FIELD_... = 'manually_...'
                if (preg_match('/\bconst\s+FIELD_/', $line) === 1) {
                    continue;
                }

                if (
                    str_contains($line, "'" . $literal . "'")
                    || str_contains($line, '"' . $literal . '"')
                ) {
                    $this->fail(
                        "Raw string literal '{$literal}' found in {$filePath}:"
                        . ($lineNo + 1) . ". {$message}",
                    );
                }
            }
        }

        // If we get here, no violations were found.
        $this->assertTrue(true, "No raw '{$literal}' literals found in production PHP.");
    }
}
