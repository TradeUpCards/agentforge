<?php

/**
 * HITL Review Modal — HTML fragment endpoint.
 *
 * Fetched lazily by hitl-banner.js on first click of "Review what was missed".
 * Returns ONLY the Bootstrap modal markup + inline config object. Does NOT
 * output <html>, <head>, or <body> tags — the fragment is injected directly
 * into the top-level document body by hitl-banner.js.
 *
 * Auth gate: requires an authenticated session with patients/med ACL.
 * Returns 403 on failure; hitl-banner.js maps this to a structural error
 * message and never displays patient data.
 *
 * PHI custody: this file outputs NO patient data. All patient-derived
 * content is fetched client-side from extraction_for_doc.php and rendered
 * by hitl-review.js according to the PHI rules in PRD §3.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    AgentForge Team
 * @copyright Copyright (c) 2026 AgentForge
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

require_once __DIR__ . '/../../../../globals.php';

use OpenEMR\Common\Acl\AclMain;
use OpenEMR\Common\Csrf\CsrfUtils;
use OpenEMR\Common\Session\SessionWrapperFactory;

$session = SessionWrapperFactory::getInstance()->getActiveSession();

if (!AclMain::aclCheckCore('patients', 'med')) {
    http_response_code(403);
    // Structural error only — no patient details.
    echo json_encode(['error' => 'access_denied']);
    exit;
}

$csrfToken = CsrfUtils::collectCsrfToken(session: $session);

// Derive the module's public root URL so JS can construct endpoint URLs
// without hardcoding the webroot. Mirrors chat-panel.php pattern.
$publicRoot = $GLOBALS['webroot']
    . '/interface/modules/custom_modules/oe-module-clinical-copilot/public';

// Cache-bust vendor and review assets by mtime.
$pdfJsVer      = @filemtime(__DIR__ . '/vendor/pdfjs/pdf.min.js') ?: time();
$reviewCssVer  = @filemtime(__DIR__ . '/hitl-review.css') ?: time();
$reviewJsVer   = @filemtime(__DIR__ . '/hitl-review.js') ?: time();

header('Content-Type: text/html; charset=UTF-8');
// No-store: this fragment contains a CSRF token.
header('Cache-Control: no-store');
?>
<!-- HITL Review Modal fragment — injected by hitl-banner.js -->

<link rel="stylesheet"
      href="<?php echo attr($publicRoot . '/hitl-review.css?v=' . $reviewCssVer); ?>">

<script src="<?php echo attr($publicRoot . '/vendor/pdfjs/pdf.min.js?v=' . $pdfJsVer); ?>"></script>

<div class="modal fade"
     id="hitl-review-modal"
     data-hitl-sidecar="1"
     data-backdrop="false"
     tabindex="-1"
     role="dialog"
     aria-labelledby="hitl-review-modal-label"
     aria-modal="false">
    <div class="modal-dialog modal-xl" role="document">
        <div class="modal-content">

            <!-- Header -->
            <div class="modal-header">
                <h5 class="modal-title hitl-modal-title" id="hitl-review-modal-label">
                    <?php echo xlt('Review Extraction'); ?>
                    <span class="hitl-chip hitl-chip--doc-type"
                          id="hitl-hdr-doc-type"
                          aria-label="<?php echo xla('Document type'); ?>">
                        &mdash;
                    </span>
                    <span class="hitl-chip hitl-chip--attempt"
                          id="hitl-hdr-attempt"
                          aria-label="<?php echo xla('Attempt number'); ?>">
                        &mdash;
                    </span>
                    <span class="hitl-chip hitl-chip--model"
                          id="hitl-hdr-model"
                          style="display:none;"
                          aria-label="<?php echo xla('Model'); ?>">
                    </span>
                    <!-- "Just re-extracted" pill — shown by JS after a reprocess -->
                    <span class="hitl-chip hitl-chip--reextracted"
                          id="hitl-hdr-reextracted"
                          style="display:none;"
                          aria-label="<?php echo xla('Just re-extracted'); ?>">
                        &#8635; <?php echo xlt('Just re-extracted'); ?>
                    </span>
                </h5>
                <button type="button"
                        class="close"
                        data-dismiss="modal"
                        aria-label="<?php echo xla('Close'); ?>">
                    <span aria-hidden="true">&times;</span>
                </button>
            </div>

            <!-- Body: two-pane layout -->
            <div class="modal-body">
                <div class="hitl-body">
                    <!-- Left pane: PDF + bbox overlay -->
                    <div class="hitl-pane-left" id="hitl-pdf-pane" role="region"
                         aria-label="<?php echo xla('Document preview with extraction highlights'); ?>">
                        <div class="hitl-pdf-loading" id="hitl-pdf-loading"
                             aria-live="polite">
                            <?php echo xlt('Loading document…'); ?>
                        </div>
                        <!-- hitl-review.js appends .hitl-pdf-page-wrap elements here -->

                        <!-- Bbox colour legend (sticky bottom of left pane) -->
                        <div class="hitl-bbox-legend" id="hitl-bbox-legend"
                             role="note"
                             aria-label="<?php echo xla('Bbox colour legend'); ?>">
                            <span class="hitl-bbox-legend-item">
                                <span class="hitl-bbox-swatch hitl-bbox-swatch--verified"></span>
                                <?php echo xlt('Verified'); ?>
                            </span>
                            <span class="hitl-bbox-legend-item">
                                <span class="hitl-bbox-swatch hitl-bbox-swatch--stripped"></span>
                                <?php echo xlt('Stripped'); ?>
                            </span>
                            <span class="hitl-bbox-legend-item">
                                <span class="hitl-bbox-swatch hitl-bbox-swatch--uncited"></span>
                                <?php echo xlt('Uncited'); ?>
                            </span>
                            <span class="hitl-bbox-legend-item">
                                <span class="hitl-bbox-swatch hitl-bbox-swatch--manually_added"></span>
                                <?php echo xlt('Clinician added'); ?>
                            </span>
                        </div>
                    </div>

                    <!-- Right pane: field sections -->
                    <div class="hitl-pane-right" id="hitl-field-pane" role="region"
                         aria-label="<?php echo xla('Extracted field list'); ?>">

                        <!-- Captured section -->
                        <div class="hitl-section-heading">
                            <?php echo xlt('Captured'); ?>
                            <span class="hitl-count-badge hitl-count-badge--captured"
                                  id="hitl-count-captured"
                                  aria-label="<?php echo xla('Captured field count'); ?>">
                                0
                            </span>
                        </div>
                        <div id="hitl-list-captured" role="list"
                             aria-label="<?php echo xla('Captured fields'); ?>">
                            <div class="hitl-empty-section">
                                <?php echo xlt('None'); ?>
                            </div>
                        </div>

                        <hr class="hitl-section-divider">

                        <!-- Missed section -->
                        <div class="hitl-section-heading">
                            <?php echo xlt('Missed'); ?>
                            <span class="hitl-count-badge hitl-count-badge--missed"
                                  id="hitl-count-missed"
                                  aria-label="<?php echo xla('Missed field count'); ?>">
                                0
                            </span>
                        </div>
                        <div id="hitl-list-missed" role="list"
                             aria-label="<?php echo xla('Missed fields'); ?>">
                            <div class="hitl-empty-section">
                                <?php echo xlt('None'); ?>
                            </div>
                        </div>

                        <hr class="hitl-section-divider">

                        <!-- Possibly missed section -->
                        <div class="hitl-section-heading">
                            <?php echo xlt('Possibly missed'); ?>
                            <span class="hitl-count-badge hitl-count-badge--possibly"
                                  id="hitl-count-possibly"
                                  aria-label="<?php echo xla('Possibly missed block count'); ?>">
                                0
                            </span>
                        </div>
                        <div id="hitl-list-possibly" role="list"
                             aria-label="<?php echo xla('Possibly missed document blocks'); ?>">
                            <div class="hitl-empty-section">
                                <?php echo xlt('None'); ?>
                            </div>
                        </div>

                    </div><!-- .hitl-pane-right -->
                </div><!-- .hitl-body -->
            </div><!-- .modal-body -->

            <!-- Footer -->
            <div class="modal-footer" id="hitl-modal-footer">
                <span class="hitl-footer-msg" id="hitl-footer-msg" aria-live="polite"></span>
                <button type="button"
                        class="btn btn-secondary btn-sm"
                        data-dismiss="modal">
                    <?php echo xlt('Close'); ?>
                </button>
                <!-- Discard manual edits checkbox — shown above Reprocess. Reset
                     to unchecked on every modal open (JS handles this). -->
                <label class="hitl-discard-edits-row" id="hitl-discard-edits-row"
                       style="display:none;">
                    <input type="checkbox" id="hitl-discard-edits-chk">
                    <?php echo xlt('Discard manual edits on reprocess'); ?>
                </label>
                <button type="button"
                        class="btn btn-warning btn-sm"
                        id="hitl-reprocess-btn"
                        disabled
                        title="<?php echo xla('No missed fields — reprocess is not needed'); ?>">
                    <?php echo xlt('Reprocess this document'); ?>
                </button>
                <!-- Approve + Reject: visible only when extraction status === pending_review.
                     JS shows/hides these based on data-extraction-status on this footer. -->
                <button type="button"
                        class="btn btn-success btn-sm hitl-approve-btn"
                        id="hitl-approve-btn"
                        style="display:none;"
                        aria-label="<?php echo xla('Approve extraction and write to chart'); ?>">
                    <?php echo xlt('Approve'); ?>
                </button>
                <button type="button"
                        class="btn btn-outline-danger btn-sm hitl-reject-btn"
                        id="hitl-reject-btn"
                        style="display:none;"
                        aria-label="<?php echo xla('Reject extraction — will not be written to chart'); ?>">
                    <?php echo xlt('Reject'); ?>
                </button>
            </div>

        </div><!-- .modal-content -->
    </div><!-- .modal-dialog -->
</div><!-- #hitl-review-modal -->

<!-- ====================================================================
     Confirmation modal — "N fields will not be saved"
     Stacked above the review modal. data-backdrop="static" and
     data-keyboard="false" force explicit clinician choice.
     z-index override in hitl-review.css (.hitl-confirm-backdrop + #hitl-confirm-approve-modal).
     ==================================================================== -->
<div class="modal fade"
     id="hitl-confirm-approve-modal"
     tabindex="-1"
     role="dialog"
     data-backdrop="static"
     data-keyboard="false"
     aria-labelledby="hitl-confirm-approve-label"
     aria-modal="true">
    <div class="modal-dialog" role="document">
        <div class="modal-content">
            <div class="modal-header">
                <h5 class="modal-title" id="hitl-confirm-approve-label">
                    <?php echo xlt('Confirm approval'); ?>
                </h5>
                <!-- No close X — static backdrop; must pick Cancel or Approve anyway -->
            </div>
            <div class="modal-body">
                <!-- JS writes the count + field list into these elements -->
                <p id="hitl-confirm-field-count" class="mb-1" style="font-weight:600;">
                    &mdash;
                </p>
                <ul class="hitl-confirm-field-list"
                    id="hitl-confirm-field-list"
                    aria-label="<?php echo xla('Fields that will not be saved'); ?>">
                </ul>
                <span id="hitl-confirm-see-all"
                      class="hitl-confirm-see-all"
                      style="display:none;"
                      role="button"
                      tabindex="0">
                    <?php echo xlt('See all'); ?>
                </span>

                <!-- Required acknowledgement checkbox -->
                <div class="hitl-confirm-checkbox-row">
                    <input type="checkbox"
                           id="hitl-confirm-ack-chk"
                           aria-required="true">
                    <label for="hitl-confirm-ack-chk" style="margin:0;cursor:pointer;">
                        <?php echo xlt('I understand these fields will not be saved to the chart'); ?>
                    </label>
                </div>
            </div>
            <div class="modal-footer">
                <button type="button"
                        class="btn btn-secondary btn-sm"
                        id="hitl-confirm-cancel-btn">
                    <?php echo xlt('Cancel'); ?>
                </button>
                <button type="button"
                        class="btn btn-success btn-sm hitl-confirm-approve-btn"
                        id="hitl-confirm-approve-btn"
                        disabled
                        aria-label="<?php echo xla('Approve despite missing fields'); ?>">
                    <?php echo xlt('Approve anyway'); ?>
                </button>
            </div>
        </div><!-- .modal-content -->
    </div><!-- .modal-dialog -->
</div><!-- #hitl-confirm-approve-modal -->

<script>
/**
 * HITL config block — mirrors OE_COPILOT_CONFIG pattern from chat-panel.php.
 * Consumed by hitl-review.js. CSRF token is per-session; no-store header
 * above prevents caching of this fragment.
 */
window.OE_COPILOT_HITL_CONFIG = {
    csrfToken:           <?php echo js_escape($csrfToken); ?>,
    extractionForDocUrl: <?php echo js_escape($publicRoot . '/extraction_for_doc.php'); ?>,
    reprocessUrl:        <?php echo js_escape($publicRoot . '/reprocess.php'); ?>,
    approveUrl:          <?php echo js_escape($publicRoot . '/approve_extraction.php'); ?>,
    rejectUrl:           <?php echo js_escape($publicRoot . '/reject_extraction.php'); ?>,
    editFieldUrl:        <?php echo js_escape($publicRoot . '/edit_extracted_field.php'); ?>,
    pdfWorkerSrc:        <?php echo js_escape($publicRoot . '/vendor/pdfjs/pdf.worker.min.js?v=' . $pdfJsVer); ?>,
    labels: {
        loadingDoc:           <?php echo js_escape(xl('Loading document…')); ?>,
        pdfError:             <?php echo js_escape(xl('Unable to render PDF preview.')); ?>,
        pdfPlaceholder:       <?php echo js_escape(xl('PDF.js not loaded — install vendor/pdfjs/ bundle.')); ?>,
        reprocessing:         <?php echo js_escape(xl('Reprocessing… (may take up to 30s)')); ?>,
        reprocessOk:          <?php echo js_escape(xl('Reprocessed successfully.')); ?>,
        errorCostCap:         <?php echo js_escape(xl('Reprocess refused: cost ceiling exceeded.')); ?>,
        errorLowGround:       <?php echo js_escape(xl('Reprocess refused: extraction low grounding.')); ?>,
        errorGeneric:         <?php echo js_escape(xl('Reprocess failed. Please try again.')); ?>,
        noFieldsMissed:       <?php echo js_escape(xl('No missed fields — reprocess is not needed.')); ?>,
        confirmReprocess:     <?php echo js_escape(xl('Reprocess this document? This will consume API credits.')); ?>,
        approving:            <?php echo js_escape(xl('Approving…')); ?>,
        approveOk:            <?php echo js_escape(xl('Approved — written to chart.')); ?>,
        approveError:         <?php echo js_escape(xl('Approve failed. Please try again.')); ?>,
        approveForbidden:     <?php echo js_escape(xl('Approve not allowed for this account.')); ?>,
        confirmReject:        <?php echo js_escape(xl('Reject this extraction? It will not be written to the chart.')); ?>,
        rejecting:            <?php echo js_escape(xl('Rejecting…')); ?>,
        rejectOk:             <?php echo js_escape(xl('Extraction rejected.')); ?>,
        rejectError:          <?php echo js_escape(xl('Reject failed. Please try again.')); ?>,
        rejectForbidden:      <?php echo js_escape(xl('Reject not allowed for this account.')); ?>,
        errorForbidden:       <?php echo js_escape(xl('Action not allowed for this account.')); ?>,
        editSave:             <?php echo js_escape(xl('Saving…')); ?>,
        editSaveOk:           <?php echo js_escape(xl('Saved.')); ?>,
        editSaveError:        <?php echo js_escape(xl('Save failed. Please try again.')); ?>,
        editSaveForbidden:    <?php echo js_escape(xl('Edit not allowed for this account.')); ?>,
        assertSave:           <?php echo js_escape(xl('Asserting…')); ?>,
        assertSaveOk:         <?php echo js_escape(xl('Field asserted.')); ?>,
        assertSaveError:      <?php echo js_escape(xl('Assert failed. Please try again.')); ?>,
        assertPickBlock:      <?php echo js_escape(xl('Select a source block above, then enter the value.')); ?>,
        assertBtnLabel:       <?php echo js_escape(xl('+ Assert from block')); ?>,
        editBtnLabel:         <?php echo js_escape(xl('Edit')); ?>,
        editCancelLabel:      <?php echo js_escape(xl('Cancel')); ?>,
        assertCancelLabel:    <?php echo js_escape(xl('Cancel')); ?>,
        clinicianEdited:      <?php echo js_escape(xl('clinician edited')); ?>,
        clinicianAdded:       <?php echo js_escape(xl('clinician added')); ?>,
        carriedOver:          <?php echo js_escape(xl('carried over')); ?>,
        wasLabel:             <?php echo js_escape(xl('was:')); ?>,
        nowLabel:             <?php echo js_escape(xl('now:')); ?>,
        valueLabel:           <?php echo js_escape(xl('Value:')); ?>,
        nFieldsNotSaved:      <?php echo js_escape(xl('field(s) will not be saved.')); ?>,
        confirmApproveBtn:    <?php echo js_escape(xl('Approve anyway')); ?>,
        seeAll:               <?php echo js_escape(xl('See all')); ?>,
        seeLess:              <?php echo js_escape(xl('See less')); ?>
    }
};
</script>

<script src="<?php echo attr($publicRoot . '/hitl-review.js?v=' . $reviewJsVer); ?>"></script>
