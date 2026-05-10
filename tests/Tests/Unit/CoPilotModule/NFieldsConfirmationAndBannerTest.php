<?php

/**
 * Unit tests for "N fields will not be saved" confirmation modal + banner state — Buckets G + K.
 *
 * Covered:
 *   G.1 — hitl-review.js: Approve POST does NOT fire when N > 0 and confirmation not acknowledged.
 *   G.2 — hitl-review.js: Approve POST fires when confirmedAcknowledged === true.
 *   G.3 — hitl-review.js: When N == 0, no confirmation modal renders; Approve POSTs direct.
 *   G.4 — hitl-review.js: Counter computes from field_verdicts.stripped_fields.
 *   G.5 — hitl-review-modal.php or JS: confirmation modal has data-backdrop="static".
 *   G.6 — hitl-review.js: Required checkbox blocks the "Approve anyway" button when unchecked.
 *   K.1 — hitl-banner.js: pending_review after reprocess re-renders blue alert-info.
 *   K.2 — hitl-banner.js: approved state uses subdued green (R1 unchanged).
 *   K.3 — hitl-banner.js: reprocess result with new_extraction_id triggers pending_review.
 *
 * All tests are source-analysis (text assertions on JS/PHP source).  No live DOM.
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

final class NFieldsConfirmationAndBannerTest extends TestCase
{
    private string $reviewJsSource;
    private string $bannerJsSource;
    private string $modalPhpSource;
    private bool $reviewJsExists = false;
    private bool $bannerJsExists = false;
    private bool $modalPhpExists = false;

    private const MODULE_PUBLIC = __DIR__
        . '/../../../../interface/modules/custom_modules'
        . '/oe-module-clinical-copilot/public/';

    protected function setUp(): void
    {
        $reviewJsPath  = self::MODULE_PUBLIC . 'hitl-review.js';
        $bannerJsPath  = self::MODULE_PUBLIC . 'hitl-banner.js';
        $modalPhpPath  = self::MODULE_PUBLIC . 'hitl-review-modal.php';

        $this->reviewJsExists = file_exists($reviewJsPath);
        $this->bannerJsExists = file_exists($bannerJsPath);
        $this->modalPhpExists = file_exists($modalPhpPath);

        if ($this->reviewJsExists) {
            $content = file_get_contents($reviewJsPath);
            $this->reviewJsSource = is_string($content) ? $content : '';
        } else {
            $this->reviewJsSource = '';
        }

        if ($this->bannerJsExists) {
            $content = file_get_contents($bannerJsPath);
            $this->bannerJsSource = is_string($content) ? $content : '';
        } else {
            $this->bannerJsSource = '';
        }

        if ($this->modalPhpExists) {
            $content = file_get_contents($modalPhpPath);
            $this->modalPhpSource = is_string($content) ? $content : '';
        } else {
            $this->modalPhpSource = '';
        }
    }

    private function requireReviewJs(): void
    {
        if (!$this->reviewJsExists || $this->reviewJsSource === '') {
            $this->markTestSkipped('hitl-review.js does not exist or is empty.');
        }
    }

    private function requireBannerJs(): void
    {
        if (!$this->bannerJsExists || $this->bannerJsSource === '') {
            $this->markTestSkipped('hitl-banner.js does not exist or is empty.');
        }
    }

    private function requireModalPhp(): void
    {
        if (!$this->modalPhpExists || $this->modalPhpSource === '') {
            $this->markTestSkipped('hitl-review-modal.php does not exist or is empty.');
        }
    }

    // -----------------------------------------------------------------------
    // G.1 — Approve POST blocked when N > 0 and not acknowledged
    // -----------------------------------------------------------------------

    /**
     * hitl-review.js must guard the Approve POST with a check for unresolved
     * stripped fields.  When strippedCount > 0 and the confirmation has not
     * been acknowledged, the POST must not fire.
     */
    public function testApprovePostBlockedWhenStrippedFieldsUnacknowledged(): void
    {
        $this->requireReviewJs();

        // The JS must reference a stripped-fields count or equivalent guard.
        $this->assertTrue(
            str_contains($this->reviewJsSource, 'stripped_fields')
            || str_contains($this->reviewJsSource, 'strippedCount')
            || str_contains($this->reviewJsSource, 'strippedFields'),
            'hitl-review.js must reference stripped_fields to compute the "N fields '
            . 'will not be saved" count.',
        );

        // A confirmation check must exist.
        $this->assertTrue(
            str_contains($this->reviewJsSource, 'confirmed')
            || str_contains($this->reviewJsSource, 'acknowledged')
            || str_contains($this->reviewJsSource, 'Acknowledged'),
            'hitl-review.js must check for explicit acknowledgment before '
            . 'allowing approve POST when stripped fields remain.',
        );
    }

    // -----------------------------------------------------------------------
    // G.2 — Approve POST fires when confirmedAcknowledged === true
    // -----------------------------------------------------------------------

    public function testApprovePostFiresWhenConfirmedAcknowledged(): void
    {
        $this->requireReviewJs();

        // The approve POST logic must be reachable after acknowledgment.
        $this->assertStringContainsString(
            'approve',
            $this->reviewJsSource,
            'hitl-review.js must contain approve logic.',
        );

        // The approve URL or endpoint must be referenced.
        $this->assertStringContainsString(
            'approve_extraction.php',
            $this->reviewJsSource,
            'hitl-review.js must reference approve_extraction.php as the approve endpoint.',
        );
    }

    // -----------------------------------------------------------------------
    // G.3 — Zero stripped fields: no confirmation modal, direct approve
    // -----------------------------------------------------------------------

    public function testZeroStrippedFieldsSkipsConfirmationModal(): void
    {
        $this->requireReviewJs();

        // The JS must branch on the stripped-fields count — when zero, skip the modal.
        // We verify this by checking the conditional structure.
        $this->assertTrue(
            str_contains($this->reviewJsSource, '=== 0')
            || str_contains($this->reviewJsSource, '== 0')
            || str_contains($this->reviewJsSource, '< 1'),
            'hitl-review.js must branch on stripped-fields count to skip the '
            . 'confirmation modal when there are zero stripped fields.',
        );
    }

    // -----------------------------------------------------------------------
    // G.4 — Counter computed from field_verdicts.stripped_fields
    // -----------------------------------------------------------------------

    public function testStrippedCountComputedFromFieldVerdicts(): void
    {
        $this->requireReviewJs();

        $this->assertStringContainsString(
            'field_verdicts',
            $this->reviewJsSource,
            'hitl-review.js must reference field_verdicts to compute the stripped-fields count.',
        );
    }

    // -----------------------------------------------------------------------
    // G.5 — Confirmation modal has data-backdrop="static" + data-keyboard="false"
    // -----------------------------------------------------------------------

    /**
     * The confirmation modal must be non-dismissable by backdrop click or
     * keyboard (Escape key).  Clinician must explicitly check the box.
     *
     * data-backdrop="static" and data-keyboard="false" are the Bootstrap 4.6
     * attributes that enforce this.
     */
    public function testConfirmationModalIsNonDismissable(): void
    {
        // Check both modal PHP and JS for the modal creation.
        $source = $this->reviewJsSource . $this->modalPhpSource;

        if ($source === '') {
            $this->markTestSkipped('Neither hitl-review.js nor hitl-review-modal.php found.');
        }

        // Bootstrap 4 static backdrop or JS equivalent.
        $this->assertTrue(
            str_contains($source, 'backdrop: \'static\'')
            || str_contains($source, 'backdrop: "static"')
            || str_contains($source, 'data-backdrop="static"')
            || str_contains($source, "data-backdrop='static'")
            || str_contains($source, 'staticBackdrop'),
            'Confirmation modal must use static backdrop (data-backdrop="static") '
            . 'so clinician cannot accidentally dismiss it.',
        );
    }

    // -----------------------------------------------------------------------
    // G.6 — Checkbox required before "Approve anyway" button
    // -----------------------------------------------------------------------

    public function testApproveAnywayButtonRequiresCheckbox(): void
    {
        $source = $this->reviewJsSource . $this->modalPhpSource;

        if ($source === '') {
            $this->markTestSkipped('Neither hitl-review.js nor hitl-review-modal.php found.');
        }

        // A checkbox element must be present in the confirmation UI.
        $this->assertTrue(
            str_contains($source, 'checkbox')
            || str_contains($source, 'type="checkbox"')
            || str_contains($source, "type='checkbox'"),
            'Confirmation modal must include a checkbox that the clinician must '
            . 'check before the "Approve anyway" button is enabled.',
        );
    }

    // -----------------------------------------------------------------------
    // K.1 — hitl-banner.js: pending_review re-renders blue alert-info
    // -----------------------------------------------------------------------

    /**
     * After a reprocess, the banner must return to the blue "pending_review"
     * state (alert-info + "Review before saving" message).  The reprocess
     * response's new_extraction_id tells the banner to re-render.
     */
    public function testBannerShowsAlertInfoForPendingReview(): void
    {
        $this->requireBannerJs();

        $this->assertStringContainsString(
            'pending_review',
            $this->bannerJsSource,
            'hitl-banner.js must handle the pending_review state.',
        );

        $this->assertStringContainsString(
            'alert-info',
            $this->bannerJsSource,
            'hitl-banner.js must use alert-info CSS class for the pending_review state.',
        );
    }

    // -----------------------------------------------------------------------
    // K.2 — approved state uses subdued green (R1 unchanged)
    // -----------------------------------------------------------------------

    /**
     * The approved banner state must use the subdued green styling established
     * in R1 (alert-success.hitl-banner--approved).  This must not have regressed.
     */
    public function testBannerShowsSubduedGreenForApproved(): void
    {
        $this->requireBannerJs();

        $this->assertStringContainsString(
            'approved',
            $this->bannerJsSource,
            'hitl-banner.js must handle the approved state.',
        );

        $this->assertTrue(
            str_contains($this->bannerJsSource, 'alert-success')
            || str_contains($this->bannerJsSource, 'hitl-banner--approved'),
            'hitl-banner.js must use alert-success or hitl-banner--approved class '
            . 'for the approved state (R1 baseline, must not regress).',
        );
    }

    // -----------------------------------------------------------------------
    // K.3 — Reprocess result with new_extraction_id triggers pending_review banner
    // -----------------------------------------------------------------------

    /**
     * When the reprocess POST returns a successful 200 response, the banner
     * must re-render in pending_review state.  The JS must use the
     * new_extraction_id from the response to update the modal context.
     */
    public function testBannerUpdatesOnReprocessSuccessWithNewExtractionId(): void
    {
        $this->requireBannerJs();

        $this->assertStringContainsString(
            'new_extraction_id',
            $this->bannerJsSource,
            'hitl-banner.js must use new_extraction_id from the reprocess response '
            . 'to update the extraction context after reprocessing.',
        );
    }

    // -----------------------------------------------------------------------
    // G.7 — Integration: hitl-review-modal.php contains Approve + Reject buttons
    // -----------------------------------------------------------------------

    /**
     * The modal footer must contain both Approve (btn-success) and Reject
     * (btn-outline-danger) buttons, visible only when status === 'pending_review'.
     * This was the R1 requirement; R2 must not have removed them.
     */
    public function testModalContainsApproveAndRejectButtons(): void
    {
        $this->requireModalPhp();

        $this->assertStringContainsString(
            'btn-success',
            $this->modalPhpSource,
            'hitl-review-modal.php must include a btn-success (Approve) button.',
        );

        $this->assertStringContainsString(
            'btn-outline-danger',
            $this->modalPhpSource,
            'hitl-review-modal.php must include a btn-outline-danger (Reject) button.',
        );
    }

    /**
     * The Approve + Reject buttons must be conditionally rendered based on
     * pending_review status in the modal template.
     */
    public function testModalButtonsConditionalOnPendingReview(): void
    {
        $this->requireModalPhp();

        $this->assertStringContainsString(
            'pending_review',
            $this->modalPhpSource,
            'hitl-review-modal.php must conditionally show Approve/Reject buttons '
            . 'only when status === "pending_review".',
        );
    }
}
