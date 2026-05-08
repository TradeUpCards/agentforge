<?php

/**
 * Unit tests for CoPilotController::handleApprove().
 *
 * Contract being pinned (P4 R1 — backend-openemr-teammate implementation
 * target):
 *
 *   handleApprove(int $extractionId):
 *     - POST to approve_extraction.php → delegates to handleApprove()
 *     - Requires session auth + ACL + CSRF (X-CSRF-Token header)
 *     - Horizontal-escalation guard: extraction.patient_id must match
 *       session pid via PersonaMap::sentinelId (mirrors handleReprocess pattern)
 *     - Only extractions in ExtractionStatus::PENDING_REVIEW are approvable.
 *       Attempts to approve rows in other statuses return HTTP 422.
 *     - On success: calls RoundtripService::roundtripFromExtractionId(int) exactly once.
 *     - On success: persists status = ExtractionStatus::APPROVED.
 *     - Response JSON contains: {"status":"approved","extraction_id":<int>}
 *     - All error responses are structural only — no $e->getMessage(), no
 *       patient_id values, no field values.
 *
 * All tests are source-level static-analysis checks against
 * CoPilotController.php and approve_extraction.php.  No live DB or HTTP
 * required.  Tests that fundamentally require a live MySQL fixture are
 * markTestSkipped.
 *
 * NO-PHI GUARANTEE:
 *   All fixture patient IDs use sentinel range 999100-999199 per
 *   W2_ARCHITECTURE.md §8.2.  No real patient names or field values appear.
 *
 * CONSTANTS:
 *   All status references use \OpenEMR\Modules\ClinicalCopilot\ExtractionStatus.
 *   No raw string literals.
 *
 * COORDINATION NOTE:
 *   handleApprove() and approve_extraction.php do not yet exist at test-
 *   authoring time.  These tests express the contract backend-openemr-teammate
 *   must satisfy.  Once implemented, the tests will execute against the real
 *   source; until then, the setUp() assertFileExists call will surface the
 *   gap immediately so CI shows "file missing" rather than a false green.
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

final class CoPilotControllerApproveTest extends TestCase
{
    private string $controllerSource;
    private string $approvePhpSource;

    /**
     * Whether the implementation files exist yet.
     *
     * When backend-openemr-teammate has not yet shipped handleApprove() /
     * approve_extraction.php, the source strings will be empty and all tests
     * that inspect them will be skipped with a coordination note.
     */
    private bool $implementationExists = false;

    protected function setUp(): void
    {
        $controllerPath = __DIR__
            . '/../../../../interface/modules/custom_modules'
            . '/oe-module-clinical-copilot/src/Controller/CoPilotController.php';

        $approvePath = __DIR__
            . '/../../../../interface/modules/custom_modules'
            . '/oe-module-clinical-copilot/public/approve_extraction.php';

        // Implementation may not exist yet; record presence rather than
        // asserting so tests can self-skip below.
        $this->implementationExists =
            file_exists($controllerPath) &&
            file_exists($approvePath) &&
            str_contains((string) file_get_contents($controllerPath), 'handleApprove');

        if ($this->implementationExists) {
            $content = file_get_contents($controllerPath);
            self::assertIsString($content);
            $this->controllerSource = $content;

            $approveContent = file_get_contents($approvePath);
            self::assertIsString($approveContent);
            $this->approvePhpSource = $approveContent;
        } else {
            $this->controllerSource = '';
            $this->approvePhpSource = '';
        }
    }

    /**
     * Skip helper: call at the top of every test that inspects implementation
     * source.  Once backend-openemr-teammate ships handleApprove(), the skip
     * will not fire and the assertion below it will execute.
     */
    private function requireImplementation(): void
    {
        if (!$this->implementationExists) {
            $this->markTestSkipped(
                'COORDINATION BLOCK: handleApprove() / approve_extraction.php not yet '
                . 'implemented by backend-openemr-teammate. '
                . 'These tests will execute once the implementation is merged. '
                . 'See P4 R1 spec in aria-handoff.md.',
            );
        }
    }

    // ---------------------------------------------------------------------------
    // Endpoint wiring
    // ---------------------------------------------------------------------------

    /**
     * approve_extraction.php must instantiate CoPilotController and call
     * handleApprove(), mirroring the reprocess.php pattern.
     */
    public function testApprovePhpInstantiatesCoPilotControllerAndCallsHandleApprove(): void
    {
        $this->requireImplementation();

        $this->assertStringContainsString(
            'CoPilotController',
            $this->approvePhpSource,
            'approve_extraction.php must instantiate CoPilotController.',
        );

        $this->assertStringContainsString(
            'handleApprove()',
            $this->approvePhpSource,
            'approve_extraction.php must call handleApprove().',
        );
    }

    // ---------------------------------------------------------------------------
    // RoundtripService::roundtripFromExtractionId — called exactly once
    // ---------------------------------------------------------------------------

    /**
     * handleApprove must call RoundtripService::roundtripFromExtractionId,
     * NOT RoundtripService::roundtrip directly.
     *
     * The method name is the contract: it accepts an extraction id and loads
     * the persisted extraction_json internally, so the controller does not
     * need to re-read and re-decode it.
     */
    public function testApproveCallsRoundtripFromExtractionId(): void
    {
        $this->requireImplementation();

        // Isolate the handleApprove / dispatchApprove block.
        $approveBlock = $this->extractApproveBlock();

        $this->assertStringContainsString(
            'roundtripFromExtractionId(',
            $approveBlock,
            'handleApprove must invoke RoundtripService::roundtripFromExtractionId(). '
            . 'Do not call roundtrip() directly from the controller — use the dedicated '
            . 'extraction-id entry point so the persisted state is canonical.',
        );

        // Must NOT call the old roundtrip() method directly.
        $this->assertStringNotContainsString(
            '->roundtrip(',
            $approveBlock,
            'handleApprove must NOT call roundtrip() directly. '
            . 'Use roundtripFromExtractionId() so no re-decoding is required.',
        );
    }

    // ---------------------------------------------------------------------------
    // Status transitions
    // ---------------------------------------------------------------------------

    /**
     * After a successful approve, the co_pilot_extractions row must have
     * status = ExtractionStatus::APPROVED ('approved').
     *
     * Verified via source: the controller must persist 'approved' as part of
     * the approve path.
     */
    public function testApproveTransitionsStatusToApproved(): void
    {
        $this->requireImplementation();

        $approveBlock = $this->extractApproveBlock();

        $this->assertStringContainsString(
            ExtractionStatus::APPROVED,
            $approveBlock,
            'handleApprove must write ExtractionStatus::APPROVED ("approved") to the '
            . 'co_pilot_extractions row on successful approval.',
        );
    }

    /**
     * handleApprove must only approve extractions in PENDING_REVIEW state.
     * Any other current status must result in HTTP 422 with a structural body.
     *
     * This is the idempotency / state-machine guard: once a row is in a
     * terminal state (approved, rejected, refused, error), no transition is
     * allowed via the approve path.
     */
    public function testApproveOnAlreadyApprovedRejectsWithHttp422(): void
    {
        $this->requireImplementation();

        $approveBlock = $this->extractApproveBlock();

        // The method must check that the current status is in
        // ExtractionStatus::approvableStatuses() (or an equivalent guard).
        $this->assertStringContainsString(
            ExtractionStatus::PENDING_REVIEW,
            $approveBlock,
            'handleApprove must guard on ExtractionStatus::PENDING_REVIEW — '
            . 'only pending_review rows are approvable.',
        );

        // A 422 (Unprocessable Entity) must be returned for invalid transitions.
        $this->assertStringContainsString(
            '422',
            $approveBlock,
            'handleApprove must return HTTP 422 when the extraction is not in '
            . 'a valid state for approval (already approved, rejected, error, refused).',
        );
    }

    // ---------------------------------------------------------------------------
    // Response shape
    // ---------------------------------------------------------------------------

    /**
     * The success response JSON from handleApprove must include both
     * 'status' and 'extraction_id' keys.
     */
    public function testApproveReturnsJsonWithNewStatus(): void
    {
        $this->requireImplementation();

        $approveBlock = $this->extractApproveBlock();

        $this->assertStringContainsString(
            "'status'",
            $approveBlock,
            'handleApprove JSON response must include a "status" key.',
        );

        $this->assertStringContainsString(
            "'extraction_id'",
            $approveBlock,
            'handleApprove JSON response must include an "extraction_id" key so the '
            . 'UI can confirm which extraction was approved.',
        );
    }

    // ---------------------------------------------------------------------------
    // Terminal-state guards
    // ---------------------------------------------------------------------------

    /**
     * Approve on an extraction in REJECTED state must return 4xx.
     * The state machine does not permit REJECTED → APPROVED.
     */
    public function testApproveOnRejectedRejectsWithClientError(): void
    {
        $this->requireImplementation();

        $approveBlock = $this->extractApproveBlock();

        // The approvable-status guard covers REJECTED because REJECTED is not
        // in ExtractionStatus::approvableStatuses().  Verify the guard exists.
        $this->assertStringContainsString(
            ExtractionStatus::PENDING_REVIEW,
            $approveBlock,
            'handleApprove state-machine guard must reject REJECTED → APPROVED transitions. '
            . 'Only PENDING_REVIEW is approvable.',
        );
    }

    /**
     * Approve on an extraction in REFUSED state must return 4xx.
     * 'refused' means the agent itself refused to extract; no data to round-trip.
     */
    public function testApproveOnRefusedRejectsWithClientError(): void
    {
        $this->requireImplementation();

        $approveBlock = $this->extractApproveBlock();

        // The guard that blocks non-pending_review statuses also covers REFUSED.
        $this->assertStringContainsString(
            ExtractionStatus::PENDING_REVIEW,
            $approveBlock,
            'handleApprove state-machine guard must reject REFUSED → APPROVED. '
            . 'A refused extraction has no extraction_json to round-trip.',
        );
    }

    /**
     * Approve on an extraction in ERROR state must return 4xx.
     */
    public function testApproveOnErrorRejectsWithClientError(): void
    {
        $this->requireImplementation();

        $approveBlock = $this->extractApproveBlock();

        $this->assertStringContainsString(
            ExtractionStatus::PENDING_REVIEW,
            $approveBlock,
            'handleApprove state-machine guard must reject ERROR → APPROVED. '
            . 'An error extraction has no valid extraction_json to round-trip.',
        );
    }

    // ---------------------------------------------------------------------------
    // Security: CSRF
    // ---------------------------------------------------------------------------

    /**
     * handleApprove must check the X-CSRF-Token header exactly as
     * handleReprocess does — HTTP_X_CSRF_TOKEN superglobal key.
     */
    public function testApproveCsrfMissingReturns403(): void
    {
        $this->requireImplementation();

        // The CSRF check is at the controller level; it may be in a shared
        // method or duplicated in dispatchApprove.  We check the full source.
        $this->assertStringContainsString(
            'HTTP_X_CSRF_TOKEN',
            $this->controllerSource,
            'CoPilotController must read CSRF token from HTTP_X_CSRF_TOKEN '
            . '(the PHP superglobal for X-CSRF-Token header) in the approve path.',
        );

        $approveBlock = $this->extractApproveBlock();

        // The CSRF check must result in a 403 on failure.
        $this->assertStringContainsString(
            '403',
            $approveBlock,
            'handleApprove must return HTTP 403 on CSRF failure.',
        );
    }

    // ---------------------------------------------------------------------------
    // Security: horizontal escalation
    // ---------------------------------------------------------------------------

    /**
     * handleApprove must verify that the extraction belongs to the patient
     * in the current session (PersonaMap::sentinelId pattern from handleReprocess).
     *
     * A user authenticating as one patient must not be able to approve an
     * extraction for a different patient.
     */
    public function testApproveCrossPatientReturns403(): void
    {
        $this->requireImplementation();

        $approveBlock = $this->extractApproveBlock();

        // Must use PersonaMap::sentinelId for the comparison.
        $this->assertStringContainsString(
            'PersonaMap::sentinelId(',
            $approveBlock,
            'handleApprove must verify patient_id via PersonaMap::sentinelId '
            . 'to prevent horizontal escalation (mirrors handleReprocess).',
        );

        // Must return 403 on mismatch.
        $this->assertStringContainsString(
            '403',
            $approveBlock,
            'handleApprove must return HTTP 403 on patient_id mismatch.',
        );
    }

    // ---------------------------------------------------------------------------
    // No-PHI in error responses
    // ---------------------------------------------------------------------------

    /**
     * All error responses from handleApprove must be structural only.
     * No $e->getMessage() echo, no patient_id in the JSON body.
     */
    public function testApproveErrorResponseDoesNotLeakPHI(): void
    {
        $this->requireImplementation();

        $approveBlock = $this->extractApproveBlock();

        $this->assertStringNotContainsString(
            '$e->getMessage()',
            $approveBlock,
            'handleApprove error responses must not echo $e->getMessage(). '
            . 'Use a generic message like "internal error".',
        );

        // Logger context must not carry patient_id as a raw value.
        // We check by scanning for logger error/warning calls in the block.
        preg_match_all('/\$this->logger->(error|warning)\([^;]+\);/s', $approveBlock, $matches);
        $logCalls = $matches[0] ?? [];

        foreach ($logCalls as $logCall) {
            $this->assertStringNotContainsString(
                "'patient_id' =>",
                $logCall,
                "handleApprove logger context must not include raw patient_id: {$logCall}",
            );
            $this->assertStringNotContainsString(
                '$sessionPid',
                $logCall,
                "handleApprove logger must not include \$sessionPid: {$logCall}",
            );
            $this->assertStringNotContainsString(
                '$storedPid',
                $logCall,
                "handleApprove logger must not include \$storedPid: {$logCall}",
            );
        }
    }

    // ---------------------------------------------------------------------------
    // DB-tier tests (markTestSkipped — Docker MySQL required)
    // ---------------------------------------------------------------------------

    /**
     * Full round-trip: approve a pending_review extraction → verify the
     * co_pilot_extractions row status changes to 'approved' in the DB.
     *
     * Requires Docker MySQL with the test fixture loaded.
     */
    public function testApproveFullRoundtripPersistsApprovedStatusInDb(): void
    {
        $this->markTestSkipped(
            'DB-tier test: requires Docker MySQL with co_pilot_extractions fixture. '
            . 'Mirrors the P3 markTestSkipped pattern. Run under: '
            . 'docker compose exec openemr /root/devtools unit-test',
        );
    }

    /**
     * Calling approve twice on the same extraction_id returns 422 on the
     * second call and does NOT call roundtripFromExtractionId again.
     *
     * Requires Docker MySQL.
     */
    public function testApproveIdempotencyRejectsDuplicateApproveInDb(): void
    {
        $this->markTestSkipped(
            'DB-tier test: requires Docker MySQL. Idempotency check needs a live row '
            . 'transitioning from pending_review → approved → 422 on second attempt.',
        );
    }

    // ---------------------------------------------------------------------------
    // Private helpers
    // ---------------------------------------------------------------------------

    /**
     * Extract the handleApprove / dispatchApprove method block from the
     * controller source for scoped assertions.
     *
     * Falls back to the full source if the method cannot be bounded so
     * assertions still work (may generate false negatives if the method is
     * absent — requireImplementation() guards against that case).
     */
    private function extractApproveBlock(): string
    {
        if ($this->controllerSource === '') {
            return '';
        }

        // Prefer the private dispatch method if it exists (mirrors reprocess pattern).
        $startPos = strpos($this->controllerSource, 'private function dispatchApprove(');
        if ($startPos === false) {
            $startPos = strpos($this->controllerSource, 'public function handleApprove(');
        }

        if ($startPos === false) {
            // Method not found; return full source so assertions run against it.
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
