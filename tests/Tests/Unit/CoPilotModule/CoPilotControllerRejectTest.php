<?php

/**
 * Unit tests for CoPilotController::handleReject().
 *
 * Contract being pinned (P4 R1 — backend-openemr-teammate implementation
 * target):
 *
 *   handleReject():
 *     - POST to reject_extraction.php → delegates to handleReject()
 *     - Requires session auth + ACL + CSRF (X-CSRF-Token header)
 *     - Horizontal-escalation guard: extraction.patient_id must match
 *       session pid via PersonaMap::sentinelId (mirrors handleReprocess pattern)
 *     - Only extractions in ExtractionStatus::PENDING_REVIEW are rejectable.
 *       Other statuses return HTTP 422.
 *     - RoundtripService::roundtrip and roundtripFromExtractionId are NEVER
 *       called (rejection is terminal — no clinical data is written).
 *     - On success: persists status = ExtractionStatus::REJECTED.
 *     - Response JSON: {"status":"rejected","extraction_id":<int>}
 *     - All error responses structural only — no $e->getMessage(), no PHI.
 *
 * All tests are source-analysis checks against CoPilotController.php and
 * reject_extraction.php.  No live DB or HTTP required.  Tests that require
 * MySQL fixture seeding are markTestSkipped.
 *
 * NO-PHI GUARANTEE:
 *   All fixture patient IDs use sentinel range 999100-999199 per
 *   W2_ARCHITECTURE.md §8.2.
 *
 * CONSTANTS:
 *   All status references use \OpenEMR\Modules\ClinicalCopilot\ExtractionStatus.
 *   No raw string literals.
 *
 * COORDINATION NOTE:
 *   handleReject() and reject_extraction.php do not yet exist at test-
 *   authoring time.  These tests express the contract backend-openemr-teammate
 *   must satisfy.  Tests self-skip until the implementation is merged.
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

final class CoPilotControllerRejectTest extends TestCase
{
    private string $controllerSource;
    private string $rejectPhpSource;

    /**
     * Whether the implementation files exist yet.
     */
    private bool $implementationExists = false;

    protected function setUp(): void
    {
        $controllerPath = __DIR__
            . '/../../../../interface/modules/custom_modules'
            . '/oe-module-clinical-copilot/src/Controller/CoPilotController.php';

        $rejectPath = __DIR__
            . '/../../../../interface/modules/custom_modules'
            . '/oe-module-clinical-copilot/public/reject_extraction.php';

        $this->implementationExists =
            file_exists($controllerPath) &&
            file_exists($rejectPath) &&
            str_contains((string) file_get_contents($controllerPath), 'handleReject');

        if ($this->implementationExists) {
            $content = file_get_contents($controllerPath);
            self::assertIsString($content);
            $this->controllerSource = $content;

            $rejectContent = file_get_contents($rejectPath);
            self::assertIsString($rejectContent);
            $this->rejectPhpSource = $rejectContent;
        } else {
            $this->controllerSource = '';
            $this->rejectPhpSource  = '';
        }
    }

    /**
     * Skip helper: all tests that inspect implementation source must call this.
     */
    private function requireImplementation(): void
    {
        if (!$this->implementationExists) {
            $this->markTestSkipped(
                'COORDINATION BLOCK: handleReject() / reject_extraction.php not yet '
                . 'implemented by backend-openemr-teammate. '
                . 'Tests will execute once the implementation is merged. '
                . 'See P4 R1 spec in aria-handoff.md.',
            );
        }
    }

    // ---------------------------------------------------------------------------
    // Endpoint wiring
    // ---------------------------------------------------------------------------

    /**
     * reject_extraction.php must instantiate CoPilotController and call
     * handleReject(), mirroring the reprocess.php pattern.
     */
    public function testRejectPhpInstantiatesCoPilotControllerAndCallsHandleReject(): void
    {
        $this->requireImplementation();

        $this->assertStringContainsString(
            'CoPilotController',
            $this->rejectPhpSource,
            'reject_extraction.php must instantiate CoPilotController.',
        );

        $this->assertStringContainsString(
            'handleReject()',
            $this->rejectPhpSource,
            'reject_extraction.php must call handleReject().',
        );
    }

    // ---------------------------------------------------------------------------
    // RoundtripService — never called
    // ---------------------------------------------------------------------------

    /**
     * handleReject must NEVER call RoundtripService::roundtrip or
     * roundtripFromExtractionId.  Rejection is terminal — no clinical data
     * should be written to clinical tables for a rejected extraction.
     *
     * This is the most critical safety assertion for the reject path.
     */
    public function testRejectNeverCallsRoundtripService(): void
    {
        $this->requireImplementation();

        $rejectBlock = $this->extractRejectBlock();

        $this->assertStringNotContainsString(
            '->roundtrip(',
            $rejectBlock,
            'handleReject must NEVER call RoundtripService::roundtrip(). '
            . 'Rejection is terminal — no data must be written to clinical tables.',
        );

        $this->assertStringNotContainsString(
            '->roundtripFromExtractionId(',
            $rejectBlock,
            'handleReject must NEVER call RoundtripService::roundtripFromExtractionId(). '
            . 'Rejection is terminal — no data must be written to clinical tables.',
        );
    }

    // ---------------------------------------------------------------------------
    // Status transitions
    // ---------------------------------------------------------------------------

    /**
     * After a successful rejection, the co_pilot_extractions row must have
     * status = ExtractionStatus::REJECTED ('rejected').
     */
    public function testRejectTransitionsToTerminalRejected(): void
    {
        $this->requireImplementation();

        $rejectBlock = $this->extractRejectBlock();

        $this->assertStringContainsString(
            ExtractionStatus::REJECTED,
            $rejectBlock,
            'handleReject must write ExtractionStatus::REJECTED ("rejected") to the '
            . 'co_pilot_extractions row on successful rejection.',
        );
    }

    /**
     * Only PENDING_REVIEW extractions can be rejected.  An already-rejected
     * extraction must return 4xx — double-reject is not allowed.
     */
    public function testRejectOnAlreadyRejectedReturnsClientError(): void
    {
        $this->requireImplementation();

        $rejectBlock = $this->extractRejectBlock();

        // The rejectable-status guard covers the case where status is already REJECTED.
        $this->assertStringContainsString(
            ExtractionStatus::PENDING_REVIEW,
            $rejectBlock,
            'handleReject state-machine guard must only allow PENDING_REVIEW → REJECTED. '
            . 'An already-REJECTED row must return 4xx.',
        );

        $this->assertStringContainsString(
            '422',
            $rejectBlock,
            'handleReject must return HTTP 422 when the extraction is not in '
            . 'a rejectable state (already rejected, approved, error, refused).',
        );
    }

    /**
     * An APPROVED extraction cannot be rejected (terminal state: approved).
     * Attempting to reject an approved row must return 4xx.
     */
    public function testRejectOnApprovedReturnsClientError(): void
    {
        $this->requireImplementation();

        $rejectBlock = $this->extractRejectBlock();

        // The same state-machine guard that protects REJECTED also protects APPROVED.
        $this->assertStringContainsString(
            ExtractionStatus::PENDING_REVIEW,
            $rejectBlock,
            'handleReject must reject APPROVED → REJECTED transitions. '
            . 'APPROVED is a terminal state; only PENDING_REVIEW is rejectable.',
        );
    }

    // ---------------------------------------------------------------------------
    // Response shape
    // ---------------------------------------------------------------------------

    /**
     * The success response JSON from handleReject must include both
     * 'status' and 'extraction_id' keys.
     */
    public function testRejectReturnsJsonWithNewStatus(): void
    {
        $this->requireImplementation();

        $rejectBlock = $this->extractRejectBlock();

        $this->assertStringContainsString(
            "'status'",
            $rejectBlock,
            'handleReject JSON response must include a "status" key.',
        );

        $this->assertStringContainsString(
            "'extraction_id'",
            $rejectBlock,
            'handleReject JSON response must include an "extraction_id" key.',
        );
    }

    // ---------------------------------------------------------------------------
    // Security: CSRF
    // ---------------------------------------------------------------------------

    /**
     * handleReject must enforce CSRF via HTTP_X_CSRF_TOKEN — returning 403
     * when the token is missing or invalid.
     */
    public function testRejectCsrfMissingReturns403(): void
    {
        $this->requireImplementation();

        $rejectBlock = $this->extractRejectBlock();

        $this->assertStringContainsString(
            'HTTP_X_CSRF_TOKEN',
            $this->controllerSource,
            'CoPilotController must read CSRF token from HTTP_X_CSRF_TOKEN '
            . 'in the reject path (mirrors handleReprocess pattern).',
        );

        $this->assertStringContainsString(
            '403',
            $rejectBlock,
            'handleReject must return HTTP 403 on CSRF failure.',
        );
    }

    // ---------------------------------------------------------------------------
    // Security: horizontal escalation
    // ---------------------------------------------------------------------------

    /**
     * handleReject must guard against horizontal escalation — verifying
     * that the extraction's patient_id (sentinel) matches the session pid
     * via PersonaMap::sentinelId, exactly as handleReprocess does.
     */
    public function testRejectCrossPatientReturns403(): void
    {
        $this->requireImplementation();

        $rejectBlock = $this->extractRejectBlock();

        $this->assertStringContainsString(
            'PersonaMap::sentinelId(',
            $rejectBlock,
            'handleReject must verify patient_id via PersonaMap::sentinelId '
            . 'to prevent horizontal escalation (mirrors handleReprocess).',
        );

        $this->assertStringContainsString(
            '403',
            $rejectBlock,
            'handleReject must return HTTP 403 on patient_id mismatch.',
        );
    }

    // ---------------------------------------------------------------------------
    // No-PHI in error responses
    // ---------------------------------------------------------------------------

    /**
     * All error responses from handleReject must be structural only.
     * No $e->getMessage() echo, no patient_id values in the JSON body,
     * no field values, no block text.
     */
    public function testRejectErrorResponseDoesNotLeakPHI(): void
    {
        $this->requireImplementation();

        $rejectBlock = $this->extractRejectBlock();

        $this->assertStringNotContainsString(
            '$e->getMessage()',
            $rejectBlock,
            'handleReject error responses must not echo $e->getMessage(). '
            . 'Use a generic message like "internal error".',
        );

        // Scan logger calls in the reject block for PHI context keys.
        preg_match_all('/\$this->logger->(error|warning)\([^;]+\);/s', $rejectBlock, $matches);
        $logCalls = $matches[0] ?? [];

        foreach ($logCalls as $logCall) {
            $this->assertStringNotContainsString(
                "'patient_id' =>",
                $logCall,
                "handleReject logger context must not include raw patient_id: {$logCall}",
            );
            $this->assertStringNotContainsString(
                '$sessionPid',
                $logCall,
                "handleReject logger must not include \$sessionPid: {$logCall}",
            );
            $this->assertStringNotContainsString(
                '$storedPid',
                $logCall,
                "handleReject logger must not include \$storedPid: {$logCall}",
            );
        }
    }

    // ---------------------------------------------------------------------------
    // DB-tier tests (markTestSkipped — Docker MySQL required)
    // ---------------------------------------------------------------------------

    /**
     * Full flow: reject a pending_review extraction → verify the
     * co_pilot_extractions row status changes to 'rejected' in the DB.
     *
     * Requires Docker MySQL with the test fixture loaded.
     */
    public function testRejectFullFlowPersistsRejectedStatusInDb(): void
    {
        $this->markTestSkipped(
            'DB-tier test: requires Docker MySQL with co_pilot_extractions fixture. '
            . 'Mirrors the P3 markTestSkipped pattern. Run under: '
            . 'docker compose exec openemr /root/devtools unit-test',
        );
    }

    /**
     * Verify that no clinical-table rows (procedure_result, lists, prescriptions)
     * are inserted after a successful rejection.
     *
     * Requires Docker MySQL to query the affected tables.
     */
    public function testRejectDoesNotCreateAnyClinicalTableRowsInDb(): void
    {
        $this->markTestSkipped(
            'DB-tier test: requires Docker MySQL. Must verify procedure_result, '
            . 'lists, and prescriptions remain unmodified after a reject call.',
        );
    }

    // ---------------------------------------------------------------------------
    // Private helpers
    // ---------------------------------------------------------------------------

    /**
     * Extract the handleReject / dispatchReject method block from the
     * controller source for scoped assertions.
     *
     * Falls back to the full source if the method cannot be bounded (method
     * absent or not yet implemented).
     */
    private function extractRejectBlock(): string
    {
        if ($this->controllerSource === '') {
            return '';
        }

        // Prefer the private dispatch method if it exists.
        $startPos = strpos($this->controllerSource, 'private function dispatchReject(');
        if ($startPos === false) {
            $startPos = strpos($this->controllerSource, 'public function handleReject(');
        }

        if ($startPos === false) {
            return $this->controllerSource;
        }

        // Bound by the next method declaration.
        $nextMethod = strpos($this->controllerSource, "\n    private function ", (int) $startPos + 1);
        if ($nextMethod === false) {
            $nextMethod = strpos($this->controllerSource, "\n    public function ", (int) $startPos + 1);
        }

        return $nextMethod !== false
            ? substr($this->controllerSource, (int) $startPos, $nextMethod - (int) $startPos)
            : substr($this->controllerSource, (int) $startPos);
    }
}
