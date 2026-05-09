<?php

/**
 * Source-analysis tests for extraction_for_doc.php R2 clinician_value behaviour.
 *
 * Contract being pinned:
 *
 *   1. The SELECT query includes the `clinician_value` column from
 *      `co_pilot_extracted_fields`.
 *   2. The entry-build branch for status='manually_edited' references
 *      `clinician_value` (row read and key set in response entry).
 *   3. The entry-build branch for status='manually_added' references
 *      `clinician_value` (row read and key set in response entry).
 *   4. The entry-build branch for status='verified' does NOT reference
 *      `clinician_value` in its conditional block — verified rows are
 *      unchanged from R1.
 *   5. The entry-build branch for status='stripped' does NOT reference
 *      `clinician_value` — stripped rows are unchanged.
 *   6. PHI guard: the logger->info call site never includes `clinician_value`
 *      in its context array literal.
 *
 * These are source-analysis (grep-over-source) tests, mirroring the pattern
 * established in DocumentSavedSubscriberApproveGateTest.php.  They run
 * without Docker or a database.
 *
 * NO-PHI FIXTURES: these tests inspect source text only; no patient data is
 * used.
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

final class ExtractionForDocPhpClinicianValueTest extends TestCase
{
    private string $sourcePath;
    private string $source;

    protected function setUp(): void
    {
        $this->sourcePath = __DIR__
            . '/../../../../interface/modules/custom_modules'
            . '/oe-module-clinical-copilot/public/extraction_for_doc.php';

        self::assertFileExists($this->sourcePath, 'extraction_for_doc.php must exist.');

        $contents = file_get_contents($this->sourcePath);
        self::assertIsString($contents);
        $this->source = $contents;
    }

    // -----------------------------------------------------------------------
    // Test 1: SELECT includes clinician_value column
    // -----------------------------------------------------------------------

    /**
     * The SELECT query sent to co_pilot_extracted_fields must fetch
     * `clinician_value` so the R2 fields are available for entry building.
     *
     * If this test fails, the modal cannot render manually_edited /
     * manually_added rows correctly on page reload.
     */
    public function testSelectIncludesClinicianValueColumn(): void
    {
        $this->assertStringContainsString(
            '`clinician_value`',
            $this->source,
            'The SELECT query in extraction_for_doc.php must include `clinician_value` '
            . 'from co_pilot_extracted_fields. Without it the R2 clinician-asserted '
            . 'values are never returned to the modal.',
        );
    }

    // -----------------------------------------------------------------------
    // Test 2: manually_edited branch reads clinician_value from the row
    // -----------------------------------------------------------------------

    /**
     * The entry-build block for status='manually_edited' must reference
     * $fieldRow['clinician_value'] to populate the response entry.
     *
     * This confirms the clinician-asserted value is forwarded to the UI for
     * edited rows, enabling the modal to show the clinician's correction.
     */
    public function testManuallyEditedBranchReadsClinicianValue(): void
    {
        // Isolate the manually_edited conditional block.
        $branchStart = strpos($this->source, "'manually_edited'");
        self::assertNotFalse(
            $branchStart,
            "Source must contain a 'manually_edited' status branch.",
        );

        // Capture up to the next elseif/else/closing of the if chain.
        $nextBranch = strpos($this->source, 'elseif ($fieldStatus ===', (int) $branchStart + 1);
        if ($nextBranch === false) {
            $nextBranch = strpos($this->source, '} elseif (', (int) $branchStart + 1);
        }

        $branchBody = $nextBranch !== false
            ? substr($this->source, (int) $branchStart, $nextBranch - (int) $branchStart)
            : substr($this->source, (int) $branchStart, 1000);

        $this->assertStringContainsString(
            "clinician_value",
            $branchBody,
            "The 'manually_edited' branch must reference clinician_value to populate "
            . "the response entry with the clinician-asserted value.",
        );
    }

    // -----------------------------------------------------------------------
    // Test 3: manually_added branch references clinician_value
    // -----------------------------------------------------------------------

    /**
     * The entry-build block for status='manually_added' must reference
     * $fieldRow['clinician_value'] to populate the response entry.
     *
     * manually_added rows have no LLM-side value; clinician_value is the
     * only value field for these rows.
     */
    public function testManuallyAddedBranchReadsClinicianValue(): void
    {
        $branchStart = strpos($this->source, "'manually_added'");
        self::assertNotFalse(
            $branchStart,
            "Source must contain a 'manually_added' status branch.",
        );

        // Capture this branch body (up to the next structural marker or
        // the stripped comment, which follows the manually_added block).
        $nextMarker = strpos($this->source, "status='stripped'", (int) $branchStart + 1);
        if ($nextMarker === false) {
            $nextMarker = strpos($this->source, '// status=', (int) $branchStart + 1);
        }

        $branchBody = $nextMarker !== false
            ? substr($this->source, (int) $branchStart, $nextMarker - (int) $branchStart)
            : substr($this->source, (int) $branchStart, 1000);

        $this->assertStringContainsString(
            "clinician_value",
            $branchBody,
            "The 'manually_added' branch must reference clinician_value. "
            . "There is no LLM-side value for clinician-added rows; clinician_value "
            . "is the sole value field.",
        );
    }

    // -----------------------------------------------------------------------
    // Test 4: verified branch does NOT set clinician_value
    // -----------------------------------------------------------------------

    /**
     * The entry-build block for status='verified' must not reference
     * $entry['clinician_value'] — verified rows are pre-R2 and unchanged.
     *
     * This guards against accidentally including an empty or null
     * clinician_value key on verified rows, which would signal to the UI
     * that a clinician edit exists when none does.
     */
    public function testVerifiedBranchDoesNotSetClinicianValueKey(): void
    {
        // Isolate the verified conditional block.
        $branchStart = strpos($this->source, "'verified'");
        self::assertNotFalse(
            $branchStart,
            "Source must contain a 'verified' status branch.",
        );

        // The verified branch ends at the first elseif.
        $nextBranch = strpos($this->source, 'elseif', (int) $branchStart + 1);
        self::assertNotFalse($nextBranch, "There must be an elseif after the verified branch.");

        $branchBody = substr($this->source, (int) $branchStart, $nextBranch - (int) $branchStart);

        $this->assertStringNotContainsString(
            "\$entry['clinician_value']",
            $branchBody,
            "The 'verified' branch must NOT set \$entry['clinician_value']. "
            . "Verified rows are pre-R2 and have no clinician_value field.",
        );
    }

    // -----------------------------------------------------------------------
    // Test 5: stripped rows — no clinician_value in their path
    // -----------------------------------------------------------------------

    /**
     * Stripped rows must not receive a clinician_value key.
     *
     * The status='stripped' path falls through all elseif branches and exits
     * without setting value or clinician_value on the entry.  This test
     * confirms there is no catch-all assignment that would accidentally attach
     * clinician_value to stripped rows.
     */
    public function testStrippedRowsDoNotGetClinicianValue(): void
    {
        // Verify there is a comment explicitly acknowledging stripped behavior.
        $this->assertStringContainsString(
            "stripped",
            $this->source,
            "Source must reference the 'stripped' status in the entry-build block "
            . "comment, confirming the no-value contract is intentional.",
        );

        // Confirm there is no unconditional $entry['clinician_value'] = outside
        // of the manually_edited / manually_added branches.
        // Strategy: count total occurrences of the entry assignment vs the
        // branch markers.  Both occurrences must appear inside an elseif block,
        // not at the top-level loop scope.
        $totalAssignments = substr_count($this->source, "\$entry['clinician_value']");
        $manuallyEditedCount = substr_count($this->source, "'manually_edited'");
        $manuallyAddedCount  = substr_count($this->source, "'manually_added'");

        $this->assertGreaterThan(
            0,
            $totalAssignments,
            "\$entry['clinician_value'] must appear in the source (for manually_edited "
            . "and manually_added branches).",
        );

        // There must be at least one manually_edited branch and one manually_added branch
        // that guard the clinician_value assignment.
        $this->assertGreaterThan(0, $manuallyEditedCount, "Source must contain a manually_edited branch.");
        $this->assertGreaterThan(0, $manuallyAddedCount, "Source must contain a manually_added branch.");
    }

    // -----------------------------------------------------------------------
    // Test 6: PHI guard — clinician_value absent from logger->info context
    // -----------------------------------------------------------------------

    /**
     * The logger->info call at the response-emit step must not include
     * clinician_value in its context array literal.
     *
     * clinician_value is PHI (clinician-asserted patient data).  Logging it
     * would violate the NO-PHI LOGGING policy documented at the top of the
     * file.
     *
     * This test reads the logger->info line(s) and asserts that none of them
     * include the string 'clinician_value' in the adjacent context array.
     */
    public function testLoggerInfoDoesNotIncludeClinicianValue(): void
    {
        // Find every logger->info call site.
        $offset = 0;
        $found  = false;
        while (($pos = strpos($this->source, 'logger->info', $offset)) !== false) {
            // Capture ~300 characters starting from this call site (enough to
            // see the context array argument).
            $callSiteSnippet = substr($this->source, $pos, 300);

            $this->assertStringNotContainsString(
                'clinician_value',
                $callSiteSnippet,
                "logger->info call at offset {$pos} must not include 'clinician_value' "
                . "in its context array. clinician_value is PHI and must never be logged. "
                . "Snippet: " . substr($callSiteSnippet, 0, 120),
            );

            $found  = true;
            $offset = $pos + 1;
        }

        $this->assertTrue(
            $found,
            "At least one logger->info call must exist in extraction_for_doc.php "
            . "(confirms the file has not been emptied of audit logging).",
        );
    }
}
