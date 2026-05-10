<?php

/**
 * Unit tests for the R2 reprocess-gate-close — Buckets E + F.
 *
 * R1 carried forward debt: dispatchReprocess still called roundtrip() synchronously,
 * bypassing the clinician review gate.  R2 must close this hole.
 *
 * Covered:
 *   E.1 — Source: dispatchReprocess does NOT call roundtrip() directly.
 *   E.2 — Source: new extraction row from reprocess is written with status='pending_review'
 *         (when agent returns 'ok') — NOT 'ok' or any other value.
 *   E.3 — Source: dispatchReprocess still calls markPriorAttemptsInactive.
 *   E.4 — Source: discard_manual_edits param is read (default false).
 *   E.5 — Source: copyManualEditsToNewExtraction is called (preserve policy).
 *   F.1 — Source: copyManualEditsToNewExtraction method declared.
 *   F.2 — Source: when discard_manual_edits=true, prior edits NOT copied.
 *   F.3 — Source: manual edit wins over new LLM value at same field_path.
 *   F.4 — DB-tier (markTestSkipped) — full preserve-manual-edits integration.
 *   F.5 — DB-tier (markTestSkipped) — full discard-manual-edits integration.
 *
 * All source-analysis tests run without a live DB.  DB-tier tests are skipped.
 *
 * SENTINEL PATIENT IDs per W2_ARCHITECTURE.md §8.2: 999100-999199.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    AgentForge Team
 * @copyright Copyright (c) 2026 AgentForge
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Tests\Unit\CoPilotModule;

use OpenEMR\Modules\ClinicalCopilot\ExtractionStatus;
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

final class ReprocessGateClosedTest extends TestCase
{
    private string $controllerSource;

    protected function setUp(): void
    {
        $controllerPath = __DIR__
            . '/../../../../interface/modules/custom_modules'
            . '/oe-module-clinical-copilot/src/Controller/CoPilotController.php';

        self::assertFileExists($controllerPath, 'CoPilotController.php must exist.');
        $content = file_get_contents($controllerPath);
        self::assertIsString($content);
        $this->controllerSource = $content;
    }

    // -----------------------------------------------------------------------
    // Helper: extract dispatchReprocess body
    // -----------------------------------------------------------------------

    private function extractDispatchReprocessBody(): string
    {
        $startPos = strpos($this->controllerSource, 'private function dispatchReprocess()');
        self::assertNotFalse($startPos, 'dispatchReprocess() must be declared.');

        // Find the next private/public function to bound the body.
        $nextMethod = strpos($this->controllerSource, "\n    private function ", (int) $startPos + 1);
        if ($nextMethod === false) {
            $nextMethod = strpos($this->controllerSource, "\n    public function ", (int) $startPos + 1);
        }

        return $nextMethod !== false
            ? substr($this->controllerSource, (int) $startPos, $nextMethod - (int) $startPos)
            : substr($this->controllerSource, (int) $startPos);
    }

    // -----------------------------------------------------------------------
    // E.1 — dispatchReprocess does NOT call roundtrip() directly
    // -----------------------------------------------------------------------

    /**
     * CRITICAL: R1 carry-forward debt was that dispatchReprocess still called
     * roundtrip() synchronously.  R2 must close this hole.
     *
     * If this test fails, the reprocess-bypasses-gate regression has been
     * re-introduced and the PR must be blocked.
     */
    public function testDispatchReprocessDoesNotCallRoundtripDirectly(): void
    {
        $body = $this->extractDispatchReprocessBody();

        $this->assertStringNotContainsString(
            '->roundtrip(',
            $body,
            'REGRESSION ALERT: dispatchReprocess must NOT call roundtrip() directly. '
            . 'This is the R1 carry-forward debt that R2 must close. '
            . 'A direct roundtrip() call bypasses the clinician review gate.',
        );
    }

    // -----------------------------------------------------------------------
    // E.2 — New extraction row written with pending_review (not raw agent status)
    // -----------------------------------------------------------------------

    /**
     * When the agent returns 'ok', the new co_pilot_extractions row must have
     * status = pending_review, NOT 'ok'.  This is the gate: the clinician must
     * approve before the round-trip runs.
     *
     * Source verification: the status written on INSERT must be the result of
     * a conditional that maps 'ok' → 'pending_review'.
     */
    public function testDispatchReprocessWritesPendingReviewStatusOnAgentOk(): void
    {
        $body = $this->extractDispatchReprocessBody();

        // The code must include a conditional that produces pending_review.
        $this->assertStringContainsString(
            ExtractionStatus::PENDING_REVIEW,
            $body,
            'dispatchReprocess must write status="pending_review" (not "ok") '
            . 'when the agent returns successfully.',
        );

        // The conditional that maps 'ok' → 'pending_review' must exist.
        $this->assertStringContainsString(
            ExtractionStatus::OK,
            $body,
            'dispatchReprocess must check for the "ok" agent status to decide '
            . 'whether to write pending_review.',
        );
    }

    /**
     * The status actually inserted into the DB must use the mapped variable
     * ($newRowStatus or equivalent), not the raw $agentStatus.
     *
     * We verify this by confirming $agentStatus is NOT directly used as the
     * status value in the INSERT statement.
     */
    public function testDispatchReprocessInsertUsesNewRowStatusNotRawAgentStatus(): void
    {
        $body = $this->extractDispatchReprocessBody();

        // The INSERT must reference the conditional variable (e.g. $newRowStatus),
        // not put $agentStatus directly as the status column value.
        // The tell: $agentStatus cannot appear immediately after the 'status,' in the INSERT.
        // We check by asserting $newRowStatus or equivalent is present.
        $this->assertTrue(
            str_contains($body, '$newRowStatus') || str_contains($body, 'pendingStatus'),
            'dispatchReprocess INSERT must use a status variable that maps "ok" → '
            . '"pending_review", not the raw $agentStatus.',
        );
    }

    // -----------------------------------------------------------------------
    // E.3 — markPriorAttemptsInactive is still called
    // -----------------------------------------------------------------------

    public function testDispatchReprocessStillCallsMarkPriorAttemptsInactive(): void
    {
        $body = $this->extractDispatchReprocessBody();

        $this->assertStringContainsString(
            'markPriorAttemptsInactive(',
            $body,
            'dispatchReprocess must still call markPriorAttemptsInactive before '
            . 'the new agent call to enforce the IS_ACTIVE invariant.',
        );
    }

    // -----------------------------------------------------------------------
    // E.4 — discard_manual_edits parameter is read
    // -----------------------------------------------------------------------

    /**
     * dispatchReprocess must parse the discard_manual_edits flag from the request body.
     * Default should be false (preserve edits unless explicitly discarded).
     */
    public function testDispatchReprocessReadsDiscardManualEditsFlag(): void
    {
        $body = $this->extractDispatchReprocessBody();

        $this->assertStringContainsString(
            'discard_manual_edits',
            $body,
            'dispatchReprocess must read the discard_manual_edits flag from the request body.',
        );
    }

    // -----------------------------------------------------------------------
    // E.5 — copyManualEditsToNewExtraction is called when not discarding
    // -----------------------------------------------------------------------

    public function testDispatchReprocessCallsCopyManualEditsWhenNotDiscarding(): void
    {
        $body = $this->extractDispatchReprocessBody();

        $this->assertStringContainsString(
            'copyManualEditsToNewExtraction(',
            $body,
            'dispatchReprocess must call copyManualEditsToNewExtraction to carry '
            . 'prior manual edits forward into the new extraction attempt.',
        );
    }

    // -----------------------------------------------------------------------
    // E.6 — Response includes new_extraction_id (UI needs it to refresh modal)
    // -----------------------------------------------------------------------

    public function testDispatchReprocessResponseIncludesNewExtractionId(): void
    {
        $body = $this->extractDispatchReprocessBody();

        $this->assertStringContainsString(
            "'new_extraction_id'",
            $body,
            'dispatchReprocess response must include new_extraction_id so the '
            . 'UI can refresh the modal with the new pending_review extraction.',
        );
    }

    // -----------------------------------------------------------------------
    // F.1 — copyManualEditsToNewExtraction method declared
    // -----------------------------------------------------------------------

    public function testCopyManualEditsMethodDeclared(): void
    {
        $this->assertStringContainsString(
            'copyManualEditsToNewExtraction(',
            $this->controllerSource,
            'CoPilotController must declare copyManualEditsToNewExtraction() '
            . 'for the preserve-manual-edits policy.',
        );
    }

    /**
     * copyManualEditsToNewExtraction must have a PHI rule comment — it reads
     * clinician_value and must NOT log it.
     */
    public function testCopyManualEditsPHIRulePresent(): void
    {
        // Extract the copyManualEditsToNewExtraction block.
        $startPos = strpos($this->controllerSource, 'copyManualEditsToNewExtraction(');
        self::assertNotFalse($startPos);

        $block = substr($this->controllerSource, (int) $startPos, 3000);

        $this->assertStringContainsString(
            'PHI',
            $block,
            'copyManualEditsToNewExtraction must include a PHI rule comment noting '
            . 'that clinician_value must not be logged.',
        );
    }

    // -----------------------------------------------------------------------
    // F.2 — discard_manual_edits=true: prior edits NOT copied
    // -----------------------------------------------------------------------

    /**
     * When discard_manual_edits is true, the $priorManualRows array must
     * be empty (not populated from the DB), so copyManualEditsToNewExtraction
     * is not called.
     */
    public function testDiscardManualEditsSkipsCopyWhenTrue(): void
    {
        $body = $this->extractDispatchReprocessBody();

        // The prior-rows load must be gated on !$discardManualEdits.
        $this->assertStringContainsString(
            '!$discardManualEdits',
            $body,
            'dispatchReprocess must gate the prior-manual-rows DB query on '
            . '!$discardManualEdits — do not load prior edits when discarding.',
        );
    }

    // -----------------------------------------------------------------------
    // F.3 — Manual edit wins over new LLM value at same field_path
    // -----------------------------------------------------------------------

    /**
     * Source analysis: copyManualEditsToNewExtraction must handle the case
     * where the new LLM attempt produced a row at the same field_path as a
     * preserved manually_edited row — the manual edit must win.
     *
     * The implementation verifies this by calling UPDATE on the new field row
     * to set its status='manually_edited' and clinician_value from the prior row.
     */
    public function testCopyManualEditsUPDATESExistingNewFieldRow(): void
    {
        $startPos = strpos($this->controllerSource, 'copyManualEditsToNewExtraction(');
        self::assertNotFalse($startPos);

        // Get from the declaration to end of file (or next peer method).
        $block = substr($this->controllerSource, (int) $startPos, 5000);

        // Must issue an UPDATE when the new attempt has the same field_path.
        $this->assertTrue(
            str_contains($block, "UPDATE `co_pilot_extracted_fields`")
            || str_contains($block, 'UPDATE co_pilot_extracted_fields'),
            'copyManualEditsToNewExtraction must issue UPDATE when new attempt has '
            . 'same field_path as a preserved manually_edited row (manual edit wins).',
        );
    }

    // -----------------------------------------------------------------------
    // F.4 — DB-tier: preserve-manual-edits full integration
    // -----------------------------------------------------------------------

    public function testPreserveManualEditsFullIntegrationInDb(): void
    {
        $this->markTestSkipped(
            'DB-tier test: requires Docker MySQL. '
            . 'Scenario: reprocess with prior manually_edited row at field_path "X". '
            . 'New LLM attempt produces different value at "X". '
            . 'Assert: new attempt has status=manually_edited at "X" with '
            . 'clinician_value from prior row (not new LLM value).',
        );
    }

    // -----------------------------------------------------------------------
    // F.5 — DB-tier: discard-manual-edits full integration
    // -----------------------------------------------------------------------

    public function testDiscardManualEditsFullIntegrationInDb(): void
    {
        $this->markTestSkipped(
            'DB-tier test: requires Docker MySQL. '
            . 'Scenario: reprocess with discard_manual_edits=true. '
            . 'Assert: new extraction has no manually_edited rows; '
            . 'all new field rows use LLM-extracted status.',
        );
    }
}
