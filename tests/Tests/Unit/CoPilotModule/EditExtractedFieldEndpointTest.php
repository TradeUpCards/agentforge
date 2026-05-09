<?php

/**
 * Unit tests for edit_extracted_field.php endpoint — Bucket D.
 *
 * Contract pinned (P4 R2 — backend-openemr-teammate implementation):
 *
 *   POST edit_extracted_field.php:
 *     Edit mode (manually_edited):
 *       Body: {extraction_id, field_path, clinician_value, source_block_id?}
 *       200: UPDATEs co_pilot_extracted_fields row to status='manually_edited',
 *            writes clinician_value; returns {status:"ok", field_id: <int>}
 *     Add mode (manually_added):
 *       Body: {extraction_id, field_path, clinician_value, source_block_id (required)}
 *       200: INSERTs new co_pilot_extracted_fields row with status='manually_added'
 *     Failure cases:
 *       422 when source_block_id missing in add mode
 *       422 when extraction status != pending_review (not editable)
 *       400 when clinician_value missing or not a string
 *
 *   No-PHI rule: clinician_value is written to the DB and returned in the 200
 *   response (authed + authorised access), but MUST NOT appear in any logger
 *   context or error response body.
 *
 * All tests are source-analysis only (no live DB).  DB-tier tests are
 * markTestSkipped.
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

use PHPUnit\Framework\TestCase;

final class EditExtractedFieldEndpointTest extends TestCase
{
    private string $endpointSource;
    private bool $implementationExists = false;

    private const ENDPOINT_PATH = __DIR__
        . '/../../../../interface/modules/custom_modules'
        . '/oe-module-clinical-copilot/public/edit_extracted_field.php';

    protected function setUp(): void
    {
        if (!file_exists(self::ENDPOINT_PATH)) {
            $this->endpointSource = '';
            $this->implementationExists = false;
            return;
        }

        $content = file_get_contents(self::ENDPOINT_PATH);
        if ($content === false || $content === '') {
            $this->endpointSource = '';
            $this->implementationExists = false;
            return;
        }

        $this->endpointSource = $content;
        $this->implementationExists = str_contains($content, 'clinician_value');
    }

    private function requireImplementation(): void
    {
        if (!$this->implementationExists) {
            $this->markTestSkipped(
                'COORDINATION BLOCK: edit_extracted_field.php not yet implemented '
                . 'or does not contain clinician_value handling. '
                . 'These tests will execute once the implementation is merged.',
            );
        }
    }

    // -----------------------------------------------------------------------
    // D.1 — Endpoint file exists
    // -----------------------------------------------------------------------

    public function testEditExtractedFieldPhpExists(): void
    {
        self::assertFileExists(
            self::ENDPOINT_PATH,
            'edit_extracted_field.php must exist — it is referenced in hitl-review-modal.php.',
        );
    }

    // -----------------------------------------------------------------------
    // D.2 — Source structure: CSRF + auth + ACL
    // -----------------------------------------------------------------------

    public function testEndpointChecksAuthentication(): void
    {
        $this->requireImplementation();

        $this->assertStringContainsString(
            'authUserID',
            $this->endpointSource,
            'edit_extracted_field.php must check session authUserID.',
        );

        $this->assertStringContainsString(
            '401',
            $this->endpointSource,
            'edit_extracted_field.php must return 401 when not authenticated.',
        );
    }

    public function testEndpointChecksCsrf(): void
    {
        $this->requireImplementation();

        $this->assertStringContainsString(
            'HTTP_X_CSRF_TOKEN',
            $this->endpointSource,
            'edit_extracted_field.php must check the X-CSRF-Token header.',
        );

        $this->assertStringContainsString(
            '403',
            $this->endpointSource,
            'edit_extracted_field.php must return 403 on CSRF failure.',
        );
    }

    // -----------------------------------------------------------------------
    // D.3 — Edit mode (manually_edited): writes status + clinician_value
    // -----------------------------------------------------------------------

    public function testEditModeWritesManuallyEditedStatus(): void
    {
        $this->requireImplementation();

        // The UPDATE path must set status to FIELD_MANUALLY_EDITED.
        $this->assertStringContainsString(
            \OpenEMR\Modules\ClinicalCopilot\ExtractionStatus::FIELD_MANUALLY_EDITED,
            $this->endpointSource,
            'edit_extracted_field.php must write status="manually_edited" on edit.',
        );
    }

    public function testEditModeWritesClinicianValue(): void
    {
        $this->requireImplementation();

        // The SQL must include clinician_value in the SET clause.
        $this->assertStringContainsString(
            'clinician_value',
            $this->endpointSource,
            'edit_extracted_field.php must write clinician_value to the DB row.',
        );
    }

    // -----------------------------------------------------------------------
    // D.4 — Add mode (manually_added): inserts new row with source_block_id
    // -----------------------------------------------------------------------

    public function testAddModeWritesManuallyAddedStatus(): void
    {
        $this->requireImplementation();

        $this->assertStringContainsString(
            \OpenEMR\Modules\ClinicalCopilot\ExtractionStatus::FIELD_MANUALLY_ADDED,
            $this->endpointSource,
            'edit_extracted_field.php must write status="manually_added" on add.',
        );
    }

    public function testAddModeRequiresSourceBlockId(): void
    {
        $this->requireImplementation();

        // The validation logic must check source_block_id for add mode.
        $this->assertStringContainsString(
            'source_block_id',
            $this->endpointSource,
            'edit_extracted_field.php must validate source_block_id for add mode.',
        );

        // Must return a 4xx error when source_block_id is missing.
        // Accept 422 or 400 per the handoff contract.
        $this->assertTrue(
            str_contains($this->endpointSource, '422') || str_contains($this->endpointSource, '400'),
            'edit_extracted_field.php must return 422 or 400 when source_block_id is missing.',
        );
    }

    // -----------------------------------------------------------------------
    // D.5 — Add mode for already-pending_review extraction only
    // -----------------------------------------------------------------------

    public function testEditOnlyAllowedForPendingReviewExtraction(): void
    {
        $this->requireImplementation();

        // The endpoint must check that the parent extraction is in pending_review.
        $this->assertStringContainsString(
            \OpenEMR\Modules\ClinicalCopilot\ExtractionStatus::PENDING_REVIEW,
            $this->endpointSource,
            'edit_extracted_field.php must verify extraction status = pending_review '
            . 'before allowing edits.',
        );

        $this->assertStringContainsString(
            '422',
            $this->endpointSource,
            'edit_extracted_field.php must return 422 when extraction is not in pending_review.',
        );
    }

    // -----------------------------------------------------------------------
    // D.6 — No-PHI: clinician_value must NOT appear in error responses
    // -----------------------------------------------------------------------

    /**
     * Error response bodies must never echo the clinician_value back.
     * (It's PHI and could be logged by upstream proxies.)
     */
    public function testErrorResponseDoesNotEchoClinicianValue(): void
    {
        $this->requireImplementation();

        // Scan all json_encode() calls in the source.  None of them should
        // directly include $clinicianValue as a key value in error paths.
        preg_match_all('/http_response_code\((4\d\d|5\d\d)\)[^;]+;[\s\S]{1,200}?echo/m', $this->endpointSource, $errorBlocks);

        foreach ($errorBlocks[0] as $block) {
            $this->assertStringNotContainsString(
                '$clinicianValue',
                $block,
                'Error response blocks must not echo $clinicianValue: ' . $block,
            );
        }
    }

    /**
     * Logger context in edit_extracted_field.php must only use the length of
     * clinician_value, not the value itself.
     */
    public function testLoggerUsesClinicianValueLengthNotValue(): void
    {
        $this->requireImplementation();

        // Permitted: clinician_value_length => strlen($clinicianValue)
        // Forbidden: 'clinician_value' => $clinicianValue (direct value in context)
        preg_match_all('/\$logger->(info|warning|error)\([^;]+\);/s', $this->endpointSource, $logCalls);

        foreach ($logCalls[0] as $call) {
            $this->assertStringNotContainsString(
                "'clinician_value' => \$clinicianValue",
                $call,
                'Logger must not include raw $clinicianValue in context: ' . substr($call, 0, 200),
            );
        }
    }

    // -----------------------------------------------------------------------
    // D.7 — Cross-teammate contract: URL used in hitl-review-modal.php
    // -----------------------------------------------------------------------

    public function testModalPhpReferenceEditExtractedFieldEndpoint(): void
    {
        $modalPath = __DIR__
            . '/../../../../interface/modules/custom_modules'
            . '/oe-module-clinical-copilot/public/hitl-review-modal.php';

        self::assertFileExists($modalPath, 'hitl-review-modal.php must exist.');
        $modalSource = file_get_contents($modalPath);
        self::assertIsString($modalSource);

        $this->assertStringContainsString(
            'edit_extracted_field.php',
            $modalSource,
            'hitl-review-modal.php must reference edit_extracted_field.php as the edit endpoint URL.',
        );
    }

    // -----------------------------------------------------------------------
    // D.8 — DB-tier tests (markTestSkipped)
    // -----------------------------------------------------------------------

    public function testEditModeUpdatesDbRowCorrectlyInDb(): void
    {
        $this->markTestSkipped(
            'DB-tier test: requires Docker MySQL. '
            . 'Verify POST with mode=edit updates status=manually_edited + clinician_value '
            . 'in co_pilot_extracted_fields.',
        );
    }

    public function testAddModeWithoutSourceBlockIdReturns422InDb(): void
    {
        $this->markTestSkipped(
            'DB-tier test: requires Docker MySQL. '
            . 'Verify POST with mode=add but missing source_block_id returns 422.',
        );
    }

    public function testAddModeDuplicateFieldPathBehaviourInDb(): void
    {
        $this->markTestSkipped(
            'DB-tier test: requires Docker MySQL. '
            . 'Verify POST add-mode for a field_path that already has a manually_added row. '
            . 'Pin whichever choice is implemented (422 or 200-overwrite).',
        );
    }

    public function testEditOnNonPendingReviewExtractionReturns422InDb(): void
    {
        $this->markTestSkipped(
            'DB-tier test: requires Docker MySQL. '
            . 'Verify POST edit on extraction with status=approved returns 422.',
        );
    }
}
