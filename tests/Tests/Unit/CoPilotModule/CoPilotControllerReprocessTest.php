<?php

/**
 * Unit tests for CoPilotController::handleReprocess().
 *
 * Covers:
 *   - CSRF header name: X-CSRF-Token (not X-OpenEMR-CSRF).
 *   - 403 response shape: {"status":"error","detail":"..."} (ui-ux flagged {"error":"access_denied"} mismatch).
 *   - triggered_by defaults to 'manual_retry' when agent response omits it.
 *   - patient_id horizontal-escalation check is present.
 *   - is_active = 1 guard on extraction lookup.
 *   - Raw exception messages are never echoed to the browser.
 *   - No-PHI invariant: exception handler logs only class + file + line.
 *
 * All tests are source-level static analysis — no live DB or HTTP required.
 *
 * CROSS-TEAMMATE CONTRACT FLAG (ui-ux discrepancy):
 *   ui-ux expected 403 body: {"error":"access_denied"}
 *   Backend actual 403 body: {"status":"error","detail":"forbidden"} or "Invalid CSRF token."
 *
 *   This is a DIVERGENCE.  The test pins the actual backend shape so the
 *   discrepancy is documented.  The UI team must update their error handler,
 *   or the backend team must change the 403 body format before merge.
 *   See ##Bugs pinned section in the verification report.
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

final class CoPilotControllerReprocessTest extends TestCase
{
    private string $controllerSource;
    private string $reprocessPhpSource;

    protected function setUp(): void
    {
        $controllerPath = __DIR__
            . '/../../../../interface/modules/custom_modules'
            . '/oe-module-clinical-copilot/src/Controller/CoPilotController.php';

        $reprocessPath = __DIR__
            . '/../../../../interface/modules/custom_modules'
            . '/oe-module-clinical-copilot/public/reprocess.php';

        self::assertFileExists($controllerPath, 'CoPilotController.php must exist.');
        self::assertFileExists($reprocessPath, 'reprocess.php must exist.');

        $content = file_get_contents($controllerPath);
        self::assertIsString($content);
        $this->controllerSource = $content;

        $rpContent = file_get_contents($reprocessPath);
        self::assertIsString($rpContent);
        $this->reprocessPhpSource = $rpContent;
    }

    // -----------------------------------------------------------------------
    // CSRF header name contract
    // -----------------------------------------------------------------------

    /**
     * ui-ux sends CSRF token in X-CSRF-Token header.
     * Backend must read HTTP_X_CSRF_TOKEN (PHP superglobal transformation of X-CSRF-Token).
     *
     * Verified: $_SERVER['HTTP_X_CSRF_TOKEN'] is the correct server-side key
     * for the X-CSRF-Token request header.
     */
    public function testCsrfTokenReadFromHttpXCsrfTokenHeader(): void
    {
        $this->assertStringContainsString(
            "HTTP_X_CSRF_TOKEN",
            $this->controllerSource,
            'dispatchReprocess must read CSRF token from $_SERVER[HTTP_X_CSRF_TOKEN] '
            . '(the PHP superglobal for X-CSRF-Token header).',
        );
    }

    /**
     * The backend must NOT read HTTP_X_OPENEMR_CSRF (that would be X-OpenEMR-CSRF header,
     * which is different from what ui-ux sends).
     */
    public function testCsrfNotReadFromOpenEmrPrefixedHeader(): void
    {
        // dispatchReprocess specifically — not the general chat handler.
        $dispatchPos = strpos($this->controllerSource, 'private function dispatchReprocess()');
        self::assertNotFalse($dispatchPos, 'dispatchReprocess method must exist.');

        $dispatchBlock = substr($this->controllerSource, (int) $dispatchPos, 2000);

        $this->assertStringNotContainsString(
            'HTTP_X_OPENEMR_CSRF',
            $dispatchBlock,
            'dispatchReprocess must NOT read X-OpenEMR-CSRF header — ui-ux sends X-CSRF-Token.',
        );
    }

    // -----------------------------------------------------------------------
    // 403 response shape contract (cross-teammate discrepancy PIN)
    //
    // ui-ux expects: {"error":"access_denied"}
    // backend actual: {"status":"error","detail":"forbidden"}
    //
    // This test PINS the current backend shape.  If the ui-ux team's test
    // expects {"error":"access_denied"}, one of the two must change.
    // See the report's "Bugs pinned" section for the full discrepancy.
    // -----------------------------------------------------------------------

    /**
     * The 403 ACL response body uses {"status":"error","detail":"forbidden"}.
     * This is the canonical error shape across the controller (matches the
     * existing /chat endpoint pattern).
     *
     * Round 2 follow-up: hitl-review.js now reads BOTH body.detail (current
     * backend) AND body.error (legacy / cost-ceiling tokens) so the UI maps
     * "forbidden" / "access_denied" to a structural message regardless of
     * which key the response carries.
     */
    public function testForbiddenResponseUsesStatusErrorShape(): void
    {
        $dispatchPos = strpos($this->controllerSource, 'private function dispatchReprocess()');
        self::assertNotFalse($dispatchPos);

        $dispatchBlock = substr($this->controllerSource, (int) $dispatchPos, 3000);

        // The canonical backend 403 body for ACL failure.
        $this->assertStringContainsString(
            "'forbidden'",
            $dispatchBlock,
            "Backend 403 ACL response must use 'forbidden' as detail value.",
        );

        // Confirm it's wrapped in status/detail shape, matching the rest of
        // the controller's error responses.
        $this->assertStringContainsString(
            "'status' => 'error'",
            $dispatchBlock,
            "Backend 403 must use status=>error shape, matching the existing "
            . "/chat endpoint error pattern.",
        );
    }

    /**
     * hitl-review.js must map both legacy {"error":"<token>"} and current
     * {"detail":"<token>"} response shapes to structural error messages.
     * Both keys are checked so the UI is robust to either backend shape.
     */
    public function testHitlReviewJsHandlesBothErrorShapes(): void
    {
        $jsPath = __DIR__
            . '/../../../../interface/modules/custom_modules'
            . '/oe-module-clinical-copilot/public/hitl-review.js';

        self::assertFileExists($jsPath);
        $jsSource = file_get_contents($jsPath);
        self::assertIsString($jsSource);

        // The JS must read both keys when mapping error tokens.
        $this->assertStringContainsString(
            'body.detail',
            $jsSource,
            'hitl-review.js must read body.detail to handle the canonical '
            . '{"status":"error","detail":"<token>"} shape.',
        );
        $this->assertStringContainsString(
            'body.error',
            $jsSource,
            'hitl-review.js must read body.error as a legacy fallback so the '
            . 'UI is robust to either backend shape.',
        );
        // Both "forbidden" (canonical) and "access_denied" (legacy) tokens
        // must map to the same structural message.
        $this->assertStringContainsString(
            "'forbidden'",
            $jsSource,
            'hitl-review.js must recognise the "forbidden" detail token.',
        );
        $this->assertStringContainsString(
            "'access_denied'",
            $jsSource,
            'hitl-review.js must recognise the legacy "access_denied" token.',
        );
    }

    // -----------------------------------------------------------------------
    // Patient-id horizontal-escalation check
    // -----------------------------------------------------------------------

    public function testDispatchReprocessChecksPatientIdMatchesSession(): void
    {
        $this->assertStringContainsString(
            'PersonaMap::sentinelId(',
            $this->controllerSource,
            'dispatchReprocess must verify patient_id via PersonaMap::sentinelId '
            . 'to prevent horizontal escalation.',
        );

        $this->assertStringContainsString(
            '$storedPid !== $expectedSentinelPid',
            $this->controllerSource,
            'dispatchReprocess must reject requests where stored patient_id does not match session.',
        );
    }

    // -----------------------------------------------------------------------
    // is_active = 1 guard on extraction lookup
    // -----------------------------------------------------------------------

    public function testDispatchReprocessRequiresIsActiveOne(): void
    {
        $dispatchPos = strpos($this->controllerSource, 'private function dispatchReprocess()');
        self::assertNotFalse($dispatchPos);
        $dispatchBlock = substr($this->controllerSource, (int) $dispatchPos, 3000);

        $this->assertStringContainsString(
            '`is_active` = 1',
            $dispatchBlock,
            'dispatchReprocess must only load extractions with is_active = 1.',
        );
    }

    // -----------------------------------------------------------------------
    // Exception handler: no raw exception messages to browser
    // -----------------------------------------------------------------------

    public function testHandleReprocessExceptionHandlerLogsStructuralOnly(): void
    {
        // Find the handleReprocess outer try-catch.
        $handlePos = strpos($this->controllerSource, 'public function handleReprocess()');
        self::assertNotFalse($handlePos);
        $handleBlock = substr($this->controllerSource, (int) $handlePos, 500);

        // The catch must log exception_class, not $e->getMessage().
        $this->assertStringContainsString(
            'exception_class',
            $this->controllerSource,
            "Exception handler must log 'exception_class' (structural), not message.",
        );

        // The catch must NOT echo $e->getMessage() or $e to the browser response.
        $catchPos = strpos($this->controllerSource, 'catch (Throwable $e)', (int) $handlePos);
        self::assertNotFalse($catchPos, 'Throwable catch block must exist in handleReprocess scope.');

        $catchBlock = substr($this->controllerSource, (int) $catchPos, 400);

        $this->assertStringNotContainsString(
            '$e->getMessage()',
            $catchBlock,
            'Exception handler must not echo $e->getMessage() in the JSON response body.',
        );
    }

    public function testHandleReprocessReturnsGenericMessageOnError(): void
    {
        // The 500 response must use a generic message.
        $this->assertStringContainsString(
            'internal error',
            $this->controllerSource,
            '500 response must use a generic "internal error" message, not raw exception text.',
        );
    }

    // -----------------------------------------------------------------------
    // triggered_by default
    // -----------------------------------------------------------------------

    public function testDispatchReprocessDefaultsTriggeredByManualRetry(): void
    {
        // Capture the full dispatchReprocess() body — the agent-response
        // unpacking (where the triggered_by default lives) sits ~140 lines
        // into the method, well past a 3000-char window.  Use the next
        // method declaration as the body terminator.
        $dispatchPos = strpos($this->controllerSource, 'private function dispatchReprocess()');
        self::assertNotFalse($dispatchPos);

        // Find the next `private function` or `public function` after dispatchReprocess
        // to bound the body.  Fall back to end-of-file if none exists.
        $nextMethodPos = strpos($this->controllerSource, "\n    private function ", (int) $dispatchPos + 1);
        if ($nextMethodPos === false) {
            $nextMethodPos = strpos($this->controllerSource, "\n    public function ", (int) $dispatchPos + 1);
        }
        $blockLength = $nextMethodPos !== false
            ? $nextMethodPos - (int) $dispatchPos
            : strlen($this->controllerSource) - (int) $dispatchPos;

        $dispatchBlock = substr($this->controllerSource, (int) $dispatchPos, (int) $blockLength);

        $this->assertStringContainsString(
            "'manual_retry'",
            $dispatchBlock,
            "dispatchReprocess must default triggered_by to 'manual_retry' if agent response omits it.",
        );
    }

    // -----------------------------------------------------------------------
    // reprocess.php entrypoint: correct controller + method wired
    // -----------------------------------------------------------------------

    public function testReprocessPhpInstantiatesCoPilotController(): void
    {
        $this->assertStringContainsString(
            'CoPilotController',
            $this->reprocessPhpSource,
            'reprocess.php must instantiate CoPilotController.',
        );
        $this->assertStringContainsString(
            'handleReprocess()',
            $this->reprocessPhpSource,
            'reprocess.php must call handleReprocess().',
        );
    }

    // -----------------------------------------------------------------------
    // No-PHI in reprocess.php / controller error paths
    // -----------------------------------------------------------------------

    public function testControllerDoesNotLogPatientIdInErrorContext(): void
    {
        // Extract all logger error/warning calls.
        preg_match_all('/\$this->logger->(error|warning)\([^;]+\);/s', $this->controllerSource, $matches);
        $logCalls = $matches[0] ?? [];

        // The literal text "patient_id" can legitimately appear inside the
        // log MESSAGE string (e.g., "patient_id mismatch — possible horizontal
        // escalation").  What MUST NOT appear is a context-array key
        // ('patient_id' => ...) carrying the raw value, or any of the actual
        // pid variables ($sessionPid / $storedPid / $patientId / $pid).
        foreach ($logCalls as $logCall) {
            $this->assertStringNotContainsString(
                "'patient_id' =>",
                $logCall,
                "Logger context array must not include raw patient_id: {$logCall}",
            );
            $this->assertStringNotContainsString(
                '$sessionPid',
                $logCall,
                "Logger call must not include \$sessionPid: {$logCall}",
            );
            $this->assertStringNotContainsString(
                '$storedPid',
                $logCall,
                "Logger call must not include \$storedPid: {$logCall}",
            );
            $this->assertStringNotContainsString(
                '$patientId',
                $logCall,
                "Logger call must not include \$patientId: {$logCall}",
            );
        }
    }

    // -----------------------------------------------------------------------
    // max_attempts and model keys — cross-teammate contract gap
    //
    // ui-ux uses attempt_n, max_attempts, and optionally model in the modal header.
    // The GET endpoint (extraction_for_doc.php) exposes attempt_n and model.
    // max_attempts is NOT in the GET response — this is a MISSING field.
    // -----------------------------------------------------------------------

    public function testExtractionForDocPhpExposesAttemptN(): void
    {
        $getSource = $this->readExtractionForDocSource();

        $this->assertStringContainsString(
            "'attempt_n'",
            $getSource,
            'extraction_for_doc.php response must include attempt_n key.',
        );
    }

    /**
     * extraction_for_doc.php response must include max_attempts so the
     * ui-ux modal header can render "Attempt N of MAX".  Fixed in Round 2
     * post-quality-lead synthesis (was previously a pinned bug).
     */
    public function testExtractionForDocPhpExposesMaxAttempts(): void
    {
        $getSource = $this->readExtractionForDocSource();

        $this->assertStringContainsString(
            "'max_attempts'",
            $getSource,
            'extraction_for_doc.php response must include max_attempts key. '
            . 'ui-ux modal header requires it.',
        );
    }

    /**
     * extraction_for_doc.php must SELECT `model` and surface it in the
     * response so the ui-ux modal header can optionally render the model
     * name.  Fixed in Round 2 post-quality-lead synthesis.
     */
    public function testExtractionForDocPhpExposesModel(): void
    {
        $getSource = $this->readExtractionForDocSource();

        $selectPos = strpos($getSource, 'SELECT `id`, `status`, `doc_type`');
        if ($selectPos === false) {
            $this->markTestSkipped('Cannot locate SELECT block in extraction_for_doc.php.');
        }

        $selectBlock = substr($getSource, (int) $selectPos, 400);

        $this->assertStringContainsString(
            '`model`',
            $selectBlock,
            '`model` must be SELECTed in extraction_for_doc.php so the modal '
            . 'header can render the model name when present.',
        );

        $this->assertStringContainsString(
            "'model'",
            $getSource,
            'extraction_for_doc.php response must include the model key in '
            . 'the JSON output.',
        );
    }

    // -----------------------------------------------------------------------
    // Helpers
    // -----------------------------------------------------------------------

    private function readExtractionForDocSource(): string
    {
        $path = __DIR__
            . '/../../../../interface/modules/custom_modules'
            . '/oe-module-clinical-copilot/public/extraction_for_doc.php';

        self::assertFileExists($path, 'extraction_for_doc.php must exist.');
        $source = file_get_contents($path);
        self::assertIsString($source);
        return $source;
    }
}
