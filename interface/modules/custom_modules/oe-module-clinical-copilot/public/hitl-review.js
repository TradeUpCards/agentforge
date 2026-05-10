/**
 * HITL Review Modal controller.
 *
 * Lifecycle:
 *   1. hitl-banner.js injects hitl-review-modal.php (which includes this
 *      script as the last <script> tag in the fragment).
 *   2. This module listens for the `oe-copilot-hitl/open-modal` CustomEvent
 *      dispatched on `document`. The event detail carries `{ docId, extraction }`.
 *   3. On receive: populate the modal header, render the right-pane field
 *      lists, render the left-pane PDF with bbox SVG overlay, open the modal.
 *   4. Bbox highlight event bus: clicking a field row or an SVG rect emits a
 *      micro-event; all listeners highlight the matching rect + field row.
 *   5. Reprocess button: POST → spinner → re-fetch → re-render.
 *
 * PDF.js viewport math (Docling points → canvas pixels):
 *   Docling outputs bboxes in PDF point space where (0,0) is bottom-left.
 *   PDF.js `viewport.transform` is a 6-element matrix [a,b,c,d,e,f] that maps
 *   PDF user space to CSS/canvas pixels. For a standard unrotated viewport at
 *   scale S on a page of height H (points):
 *
 *     viewport.transform = [S, 0, 0, -S, 0, H*S]
 *
 *   The negative d component flips Y. To convert (px, py, pw, ph) in Docling
 *   point space to canvas pixel space (canvas-top-left origin):
 *
 *     canvasX = px * S
 *     canvasY = H*S - (py + ph) * S   // flip + shift by full-height
 *     canvasW = pw * S
 *     canvasH = ph * S
 *
 *   Worked example:
 *     Page height H = 792 pt, scale S = 1.5 (canvas = 1188px tall)
 *     Docling bbox: { x:72, y:540, w:240, h:18 }
 *
 *     canvasX = 72  * 1.5 = 108.0 px
 *     canvasY = (792 - 540 - 18) * 1.5 = 234 * 1.5 = 351.0 px
 *     canvasW = 240 * 1.5 = 360.0 px
 *     canvasH = 18  * 1.5 =  27.0 px
 *
 *   This matches visually when you overlay the SVG rect at those coordinates.
 *
 * PHI custody:
 *   - `stripped` field rows render field_path + verifier_reason ONLY.
 *     `value` is never accessed or rendered for stripped rows.
 *   - Error messages shown in the footer are structural (mapped from error
 *     codes). No patient data, doc_id, or extracted value is included.
 *   - Text snippets from docling_blocks are ≤240 chars (R2 bump), truncated
 *     byte-safe. Values in inline-edit inputs are never logged.
 *   - console.log is NEVER called with any extracted field value.
 *
 * Namespace: entire module in IIFE; no global exports except the config
 * object set by hitl-review-modal.php (`window.OE_COPILOT_HITL_CONFIG`).
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    AgentForge Team
 * @copyright Copyright (c) 2026 AgentForge
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

(function () {
    'use strict';

    var cfg = window.OE_COPILOT_HITL_CONFIG || {};
    var labels = cfg.labels || {};

    // ---- 0. Guard: wait for the modal element to exist ------------------

    var modal = document.getElementById('hitl-review-modal');
    if (!modal) {
        // Script injected before DOM settled; try once on next tick.
        setTimeout(function () {
            var m = document.getElementById('hitl-review-modal');
            if (m) bootstrap();
        }, 0);
        return;
    }

    bootstrap();

    function bootstrap() {

        // ---- 1. Wire PDF.js worker src ----------------------------------

        if (window.pdfjsLib && !window.pdfjsLib._isPlaceholder) {
            window.pdfjsLib.GlobalWorkerOptions.workerSrc =
                cfg.pdfWorkerSrc || '';
        }

        // ---- 2. DOM refs ------------------------------------------------

        var el = {
            modal:              document.getElementById('hitl-review-modal'),
            hdrDocType:         document.getElementById('hitl-hdr-doc-type'),
            hdrAttempt:         document.getElementById('hitl-hdr-attempt'),
            hdrModel:           document.getElementById('hitl-hdr-model'),
            hdrReextracted:     document.getElementById('hitl-hdr-reextracted'),
            pdfPane:            document.getElementById('hitl-pdf-pane'),
            pdfLoading:         document.getElementById('hitl-pdf-loading'),
            listCaptured:       document.getElementById('hitl-list-captured'),
            listMissed:         document.getElementById('hitl-list-missed'),
            listPossibly:       document.getElementById('hitl-list-possibly'),
            cntCaptured:        document.getElementById('hitl-count-captured'),
            cntMissed:          document.getElementById('hitl-count-missed'),
            cntPossibly:        document.getElementById('hitl-count-possibly'),
            footerMsg:          document.getElementById('hitl-footer-msg'),
            reprocessBtn:       document.getElementById('hitl-reprocess-btn'),
            discardEditsRow:    document.getElementById('hitl-discard-edits-row'),
            discardEditsChk:    document.getElementById('hitl-discard-edits-chk'),
            approveBtn:         document.getElementById('hitl-approve-btn'),
            rejectBtn:          document.getElementById('hitl-reject-btn'),
            // Confirmation modal elements
            confirmModal:       document.getElementById('hitl-confirm-approve-modal'),
            confirmFieldCount:  document.getElementById('hitl-confirm-field-count'),
            confirmFieldList:   document.getElementById('hitl-confirm-field-list'),
            confirmSeeAll:      document.getElementById('hitl-confirm-see-all'),
            confirmAckChk:      document.getElementById('hitl-confirm-ack-chk'),
            confirmCancelBtn:   document.getElementById('hitl-confirm-cancel-btn'),
            confirmApproveBtn:  document.getElementById('hitl-confirm-approve-btn'),
        };

        // ---- 2b. R2 module-level state ----------------------------------

        /**
         * Shadow records for clinician edits/additions in the current session.
         * Keyed by field_path. Values: { clinician_value, status, source_block_id? }
         *
         * These are merged into the extraction data for counting stripped fields
         * and for the reprocess-carry-over logic. They are NEVER logged.
         */
        var shadowEdits = {};

        /** Currently active extraction object (refreshed on each modal open or reprocess). */
        var currentExtraction = null;

        /** docId for the current modal session. */
        var currentDocId = null;

        // ---- 3. Micro event bus for bbox highlight ----------------------

        var highlightListeners = [];

        function onHighlight(fn) {
            highlightListeners.push(fn);
        }

        function emitHighlight(blockId) {
            for (var i = 0; i < highlightListeners.length; i++) {
                try { highlightListeners[i](blockId); } catch (e) { /* safe */ }
            }
        }

        // Reset listeners on each modal open (avoid stale refs from prev open).
        function clearHighlightListeners() {
            highlightListeners = [];
        }

        // ---- 4. Utility helpers -----------------------------------------

        function escapeHtml(v) {
            return String(v == null ? '' : v)
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;')
                .replace(/'/g, '&#39;');
        }

        /**
         * Truncate a string to at most `max` characters.
         * Trims trailing whitespace before appending the ellipsis.
         * Operates on UTF-16 code units (JS native), which is correct
         * for display truncation. Not byte-counting (PHP mb_strlen) —
         * the 80-char limit in the PRD is for display, not byte storage.
         */
        function truncate(text, max) {
            var s = String(text == null ? '' : text);
            if (s.length <= max) return s;
            return s.slice(0, max - 1).trimRight() + '…';
        }

        // Human-readable verifier_reason labels.
        var REASON_LABELS = {
            block_not_found:     'Block not found in document',
            low_confidence:      'Low confidence score',
            conflicting_sources: 'Conflicting sources',
            parse_error:         'Parse error',
            no_grounding:        'No grounding found',
        };

        function humanizeReason(code) {
            if (!code) return '';
            return REASON_LABELS[code] || code.replace(/_/g, ' ');
        }

        // ---- 5. Set footer state ----------------------------------------

        function setFooterMsg(text, modifier) {
            if (!el.footerMsg) return;
            el.footerMsg.textContent = text || '';
            el.footerMsg.className = 'hitl-footer-msg' +
                (modifier ? (' hitl-footer-msg--' + modifier) : '');
        }

        // ---- 6a. Footer button visibility ---------------------------------

        /**
         * Show/hide Approve + Reject based on the extraction status.
         * Both buttons are visible only when status === 'pending_review'.
         * Reprocess stays visible per existing P3 logic (disabled state
         * is managed separately by renderRightPane).
         *
         * @param {string} status  e.g. 'pending_review', 'approved', 'ok', …
         */
        function updateFooterButtons(status) {
            // Treat undefined/null status as pending_review to avoid lockout
            // on a load race — safe default keeps editing controls visible.
            var isPending = (!status || status === 'pending_review');
            if (el.approveBtn) {
                el.approveBtn.style.display = isPending ? '' : 'none';
                el.approveBtn.disabled = false;
                el.approveBtn.textContent = labels.approveBtn || 'Approve';
            }
            if (el.rejectBtn) {
                el.rejectBtn.style.display = isPending ? '' : 'none';
                el.rejectBtn.disabled = false;
                el.rejectBtn.textContent = labels.rejectBtn || 'Reject';
            }
            // Show discard-edits checkbox only when pending_review
            if (el.discardEditsRow) {
                el.discardEditsRow.style.display = isPending ? '' : 'none';
            }
        }

        // ---- 6b-pre. Stripped-field counter (R2) ---------------------------

        /**
         * Count `status='stripped'` rows that have NOT been resolved
         * (i.e. do not have a shadow manually_edited or manually_added record).
         *
         * @param {Object} extraction  The current extraction object.
         * @returns {string[]}  Array of unresolved field_path strings.
         */
        function unresolvedStrippedPaths(extraction) {
            var fields = Array.isArray(extraction && extraction.fields)
                ? extraction.fields : [];
            var paths = [];
            fields.forEach(function (f) {
                if (f.status !== 'stripped') return;
                // If the clinician has a shadow record for this path, it is resolved.
                var shadow = shadowEdits[f.field_path];
                if (shadow && (shadow.status === 'manually_edited' || shadow.status === 'manually_added')) {
                    return;
                }
                // Also check if the extraction data already carries manually_edited/manually_added
                // rows surfaced by backend (re-fetched extraction may include them).
                if (f.status === 'manually_edited' || f.status === 'manually_added') {
                    return;
                }
                paths.push(f.field_path);
            });
            return paths;
        }

        // ---- 6b. Approve POST flow + confirmation gate (R2) -----------------

        /**
         * Show the "N fields will not be saved" confirmation modal.
         * Called when clinician clicks Approve and there are unresolved strips.
         *
         * @param {string[]}  unresolved   Array of field_path strings.
         * @param {string}    docId
         * @param {number}    extractionId
         */
        function showApproveConfirmation(unresolved, docId, extractionId) {
            var n = unresolved.length;
            var PREVIEW_LIMIT = 5;

            if (el.confirmFieldCount) {
                el.confirmFieldCount.textContent =
                    n + ' ' + (labels.nFieldsNotSaved || 'field(s) will not be saved.');
            }

            if (el.confirmFieldList) {
                el.confirmFieldList.innerHTML = '';
                var showN = Math.min(n, PREVIEW_LIMIT);
                for (var i = 0; i < showN; i++) {
                    var li = document.createElement('li');
                    li.textContent = unresolved[i];
                    el.confirmFieldList.appendChild(li);
                }
            }

            if (el.confirmSeeAll) {
                if (n > PREVIEW_LIMIT) {
                    el.confirmSeeAll.style.display = '';
                    el.confirmSeeAll.textContent = labels.seeAll || 'See all';
                    var expanded = false;
                    // Replace onclick each time to avoid stale closure.
                    el.confirmSeeAll.onclick = function () {
                        expanded = !expanded;
                        el.confirmFieldList.innerHTML = '';
                        var limit2 = expanded ? n : PREVIEW_LIMIT;
                        for (var j = 0; j < limit2; j++) {
                            var li2 = document.createElement('li');
                            li2.textContent = unresolved[j];
                            el.confirmFieldList.appendChild(li2);
                        }
                        el.confirmSeeAll.textContent = expanded
                            ? (labels.seeLess || 'See less')
                            : (labels.seeAll  || 'See all');
                    };
                } else {
                    el.confirmSeeAll.style.display = 'none';
                }
            }

            // Reset checkbox + Approve anyway button.
            if (el.confirmAckChk) {
                el.confirmAckChk.checked = false;
            }
            if (el.confirmApproveBtn) {
                el.confirmApproveBtn.disabled = true;
            }

            // Store context on the Approve anyway button for its click handler.
            if (el.confirmApproveBtn) {
                el.confirmApproveBtn.setAttribute('data-doc-id', docId || '');
                el.confirmApproveBtn.setAttribute('data-extraction-id', String(extractionId || ''));
            }

            // Open the confirmation modal.
            if (typeof window.$ === 'function') {
                window.$('#hitl-confirm-approve-modal').modal('show');
            } else if (el.confirmModal) {
                el.confirmModal.classList.add('show');
                el.confirmModal.style.display = 'block';
                // Stacked backdrop with higher z-index (see CSS .hitl-confirm-backdrop).
                var cbk = document.createElement('div');
                cbk.id = 'hitl-confirm-backdrop';
                cbk.className = 'modal-backdrop fade show hitl-confirm-backdrop';
                document.body.appendChild(cbk);
            }
        }

        /**
         * POST to approve_extraction.php.
         * Body: { extraction_id: <int> }
         * On 200: show inline toast, dispatch refresh-banner event, close modal.
         * On error: show structural message, re-enable button.
         *
         * @param {string} docId
         * @param {number} extractionId
         */
        function triggerApprove(docId, extractionId) {
            if (!extractionId || !docId) return;

            var csrfToken  = cfg.csrfToken  || '';
            var approveUrl = cfg.approveUrl || '';

            if (el.approveBtn) {
                el.approveBtn.disabled = true;
                el.approveBtn.innerHTML =
                    '<span class="hitl-spinner"></span> ' +
                    (labels.approving || 'Approving…');
            }
            if (el.rejectBtn) el.rejectBtn.disabled = true;
            setFooterMsg(labels.approving || 'Approving…');

            var xhr = new XMLHttpRequest();
            xhr.open('POST', approveUrl, true);
            xhr.setRequestHeader('Content-Type', 'application/json');
            xhr.setRequestHeader('X-CSRF-Token', csrfToken);
            xhr.onreadystatechange = function () {
                if (xhr.readyState !== 4) return;

                // Parse body once. NEVER log or display patient data.
                var body = null;
                try { body = JSON.parse(xhr.responseText); } catch (parseErr) { /* ignore */ }

                if (xhr.status === 200) {
                    setFooterMsg(labels.approveOk || 'Approved — written to chart.', 'success');

                    // Dispatch refresh-banner event so hitl-banner.js re-fetches.
                    try {
                        var refreshEvent = new CustomEvent('oe-copilot-hitl/extraction-updated', {
                            bubbles: true,
                            detail: { docId: docId }
                        });
                        document.dispatchEvent(refreshEvent);
                    } catch (evtErr) { /* non-critical */ }

                    // Close modal after brief pause so the user sees the toast.
                    setTimeout(function () {
                        var modalEl = document.getElementById('hitl-review-modal');
                        if (modalEl && typeof window.$ === 'function') {
                            window.$('#hitl-review-modal').modal('hide');
                        } else if (modalEl) {
                            modalEl.classList.remove('show');
                            modalEl.style.display = 'none';
                            document.body.classList.remove('modal-open');
                        }
                    }, 1200);
                } else {
                    // Map known error tokens to structural messages.
                    var errMsg = labels.approveError || 'Approve failed. Please try again.';
                    if (body) {
                        var errCode = (typeof body.detail === 'string' ? body.detail : '') ||
                                      (typeof body.error  === 'string' ? body.error  : '');
                        if (errCode === 'forbidden' || errCode === 'access_denied') {
                            errMsg = labels.approveForbidden || 'Approve not allowed for this account.';
                        }
                        // All other backend detail strings fall through to the generic
                        // message so we never expose backend internals to the user.
                    }
                    setFooterMsg(errMsg, 'error');
                    if (el.approveBtn) {
                        el.approveBtn.textContent = labels.approveBtn || 'Approve';
                        el.approveBtn.disabled = false;
                    }
                    if (el.rejectBtn) el.rejectBtn.disabled = false;
                }
            };
            xhr.send(JSON.stringify({ extraction_id: extractionId }));
        }

        // ---- 6c. Reject POST flow -----------------------------------------

        /**
         * Confirm + POST to reject_extraction.php.
         * Body: { extraction_id: <int> }
         * On 200: dispatch refresh-banner event; close modal; show toast.
         * On error: show structural message, re-enable button.
         *
         * @param {string} docId
         * @param {number} extractionId
         */
        function triggerReject(docId, extractionId) {
            if (!extractionId || !docId) return;

            var confirmed = window.confirm(
                labels.confirmReject ||
                'Reject this extraction? It will not be written to the chart.'
            );
            if (!confirmed) return;

            var csrfToken = cfg.csrfToken  || '';
            var rejectUrl = cfg.rejectUrl  || '';

            if (el.rejectBtn) {
                el.rejectBtn.disabled = true;
                el.rejectBtn.innerHTML =
                    '<span class="hitl-spinner"></span> ' +
                    (labels.rejecting || 'Rejecting…');
            }
            if (el.approveBtn) el.approveBtn.disabled = true;
            setFooterMsg(labels.rejecting || 'Rejecting…');

            var xhr = new XMLHttpRequest();
            xhr.open('POST', rejectUrl, true);
            xhr.setRequestHeader('Content-Type', 'application/json');
            xhr.setRequestHeader('X-CSRF-Token', csrfToken);
            xhr.onreadystatechange = function () {
                if (xhr.readyState !== 4) return;

                var body = null;
                try { body = JSON.parse(xhr.responseText); } catch (parseErr) { /* ignore */ }

                if (xhr.status === 200) {
                    // Dispatch refresh-banner event.
                    try {
                        var refreshEvent = new CustomEvent('oe-copilot-hitl/extraction-updated', {
                            bubbles: true,
                            detail: { docId: docId }
                        });
                        document.dispatchEvent(refreshEvent);
                    } catch (evtErr) { /* non-critical */ }

                    setFooterMsg(labels.rejectOk || 'Extraction rejected.', 'success');

                    // Close modal.
                    setTimeout(function () {
                        var modalEl = document.getElementById('hitl-review-modal');
                        if (modalEl && typeof window.$ === 'function') {
                            window.$('#hitl-review-modal').modal('hide');
                        } else if (modalEl) {
                            modalEl.classList.remove('show');
                            modalEl.style.display = 'none';
                            document.body.classList.remove('modal-open');
                        }
                    }, 1200);
                } else {
                    var errMsg = labels.rejectError || 'Reject failed. Please try again.';
                    if (body) {
                        var errCode = (typeof body.detail === 'string' ? body.detail : '') ||
                                      (typeof body.error  === 'string' ? body.error  : '');
                        if (errCode === 'forbidden' || errCode === 'access_denied') {
                            errMsg = labels.rejectForbidden || 'Reject not allowed for this account.';
                        }
                    }
                    setFooterMsg(errMsg, 'error');
                    if (el.rejectBtn) {
                        el.rejectBtn.textContent = labels.rejectBtn || 'Reject';
                        el.rejectBtn.disabled = false;
                    }
                    if (el.approveBtn) el.approveBtn.disabled = false;
                }
            };
            xhr.send(JSON.stringify({ extraction_id: extractionId }));
        }

        // ---- 6d. Field-edit POST helper (R2) --------------------------------

        /**
         * POST a field edit or assertion to edit_extracted_field.php.
         *
         * @param {Object}   payload    { extraction_id, field_path, clinician_value, source_block_id? }
         * @param {Function} onSuccess  Called with the parsed 200 response body.
         * @param {Function} onError    Called with a structural error string.
         */
        function postFieldEdit(payload, onSuccess, onError) {
            var csrfToken    = cfg.csrfToken   || '';
            var editFieldUrl = cfg.editFieldUrl || '';

            var xhr = new XMLHttpRequest();
            xhr.open('POST', editFieldUrl, true);
            xhr.setRequestHeader('Content-Type', 'application/json');
            xhr.setRequestHeader('X-CSRF-Token', csrfToken);
            xhr.onreadystatechange = function () {
                if (xhr.readyState !== 4) return;
                var body = null;
                try { body = JSON.parse(xhr.responseText); } catch (e) { /* ignore */ }

                if (xhr.status === 200) {
                    onSuccess(body || {});
                } else {
                    var errCode = '';
                    if (body) {
                        errCode = (typeof body.detail === 'string' ? body.detail : '') ||
                                  (typeof body.error  === 'string' ? body.error  : '');
                    }
                    if (errCode === 'forbidden' || errCode === 'access_denied') {
                        onError('forbidden');
                    } else {
                        onError('generic');
                    }
                }
            };
            xhr.send(JSON.stringify(payload));
        }

        /**
         * Build a field row element.
         *
         * @param {'verified'|'stripped'|'manually_edited'|'manually_added'} status
         * @param {string}       fieldPath
         * @param {string|null}  value        ONLY passed for verified/edited rows.
         * @param {string|null}  blockId
         * @param {string|null}  reason
         * @param {Object}       opts         Optional: { extractionId, doclingBlocks, extractionIdStr, isCarriedOver, originalValue }
         * @returns {HTMLElement}
         */
        function buildFieldRow(status, fieldPath, value, blockId, reason, opts) {
            opts = opts || {};
            var extractionId   = opts.extractionId   || '';
            var isCarriedOver  = opts.isCarriedOver   || false;
            var originalValue  = opts.originalValue   != null ? opts.originalValue : null;
            var isPending      = opts.isPending       !== false; // default true

            var row = document.createElement('div');
            row.className = 'hitl-field-row';
            row.setAttribute('role', 'listitem');
            row.setAttribute('data-block-id', blockId || '');
            row.setAttribute('data-status', status);
            row.setAttribute('data-field-path', fieldPath || '');

            var pathEl = document.createElement('span');
            pathEl.className = 'hitl-field-path';
            pathEl.textContent = fieldPath || '';
            row.appendChild(pathEl);

            // Value: ONLY for verified/manually_edited rows per PHI rule.
            if ((status === 'verified' || status === 'manually_edited') && value != null) {
                var valEl = document.createElement('span');
                valEl.className = 'hitl-field-value';
                valEl.title = '';  // Never put PHI in title/attribute
                valEl.textContent = truncate(String(value), 40);
                row.appendChild(valEl);
            }

            // Verifier reason for stripped rows.
            if (status === 'stripped' && reason) {
                var reasonEl = document.createElement('span');
                reasonEl.className = 'hitl-reason';
                reasonEl.title = reason;
                reasonEl.textContent = humanizeReason(reason);
                row.appendChild(reasonEl);
            }

            // Status badges for clinician-edited / clinician-added / carried-over.
            if (status === 'manually_edited') {
                var editedBadge = document.createElement('span');
                editedBadge.className = 'hitl-badge--edited';
                editedBadge.textContent = labels.clinicianEdited || 'clinician edited';
                row.appendChild(editedBadge);
            }
            if (status === 'manually_added') {
                var addedBadge = document.createElement('span');
                addedBadge.className = 'hitl-badge--added';
                addedBadge.textContent = labels.clinicianAdded || 'clinician added';
                row.appendChild(addedBadge);
            }
            if (isCarriedOver) {
                var carriedBadge = document.createElement('span');
                carriedBadge.className = 'hitl-badge--carried-over';
                carriedBadge.textContent = labels.carriedOver || 'carried over';
                row.appendChild(carriedBadge);
            }

            // Block ID badge.
            var badgeStatus = status;
            if (status === 'manually_edited') badgeStatus = 'verified';
            if (status === 'manually_added')  badgeStatus = 'manually_added';
            var badge = document.createElement('span');
            badge.className = 'hitl-blk-badge hitl-blk-badge--' +
                (blockId ? badgeStatus : 'null');
            badge.setAttribute('data-block-id', blockId || '');
            badge.textContent = blockId ? blockId : 'no block';
            badge.setAttribute('aria-label', blockId ? ('Block ' + blockId) : 'No block ID');
            row.appendChild(badge);

            // Pencil-edit button — only for verified/manually_edited rows
            // when the session is pending_review.
            if (isPending && (status === 'verified' || status === 'manually_edited')) {
                var editBtn = document.createElement('button');
                editBtn.type = 'button';
                editBtn.className = 'hitl-edit-btn';
                editBtn.setAttribute('aria-label', 'Edit ' + escapeHtml(fieldPath || ''));
                editBtn.setAttribute('title', labels.editBtnLabel || 'Edit');
                editBtn.innerHTML = '&#9998;'; // pencil Unicode
                row.appendChild(editBtn);

                editBtn.addEventListener('click', function (e) {
                    e.stopPropagation();
                    openInlineEdit(row, fieldPath, value, blockId, extractionId);
                });
            }

            // "was: X | now: Y" history row for manually_edited — shown below
            // main row content. Only when originalValue is provided.
            if (status === 'manually_edited' && originalValue !== null) {
                var histRow = document.createElement('div');
                histRow.className = 'hitl-edit-history';

                var wasSpan = document.createElement('span');
                wasSpan.textContent = (labels.wasLabel || 'was:') + ' ';
                histRow.appendChild(wasSpan);

                var wasVal = document.createElement('span');
                wasVal.className = 'hitl-was-value';
                wasVal.textContent = truncate(String(originalValue), 30);
                histRow.appendChild(wasVal);

                var sepSpan = document.createElement('span');
                sepSpan.textContent = ' | ' + (labels.nowLabel || 'now:') + ' ';
                histRow.appendChild(sepSpan);

                var nowVal = document.createElement('span');
                nowVal.className = 'hitl-now-value';
                nowVal.textContent = truncate(String(value), 30);
                histRow.appendChild(nowVal);

                row.appendChild(histRow);
            }

            // Click on row or badge → emit highlight.
            function handleClick(e) {
                e.stopPropagation();
                if (blockId) emitHighlight(blockId);
            }
            row.addEventListener('click', handleClick);

            return row;
        }

        /**
         * Open an inline text-edit form inside a captured field row.
         * The form replaces the row's display content while active;
         * Cancel restores it. Save POSTs to edit_extracted_field.php.
         *
         * @param {HTMLElement} row
         * @param {string}      fieldPath
         * @param {string|null} currentValue
         * @param {string|null} blockId
         * @param {string}      extractionId
         */
        function openInlineEdit(row, fieldPath, currentValue, blockId, extractionId) {
            // Only one inline form open at a time per row.
            if (row.querySelector('.hitl-inline-edit')) return;

            var form = document.createElement('div');
            form.className = 'hitl-inline-edit';
            form.setAttribute('role', 'form');

            var input = document.createElement('input');
            input.type = 'text';
            input.className = 'hitl-inline-input';
            input.value = currentValue != null ? String(currentValue) : '';
            input.setAttribute('aria-label', 'New value for ' + escapeHtml(fieldPath || ''));
            form.appendChild(input);

            var saveBtn = document.createElement('button');
            saveBtn.type = 'button';
            saveBtn.className = 'hitl-inline-save-btn';
            saveBtn.textContent = 'Save';
            form.appendChild(saveBtn);

            var cancelBtn = document.createElement('button');
            cancelBtn.type = 'button';
            cancelBtn.className = 'hitl-inline-cancel-btn';
            cancelBtn.textContent = labels.editCancelLabel || 'Cancel';
            form.appendChild(cancelBtn);

            var msgEl = document.createElement('span');
            msgEl.style.cssText = 'font-size:11px;color:#6b7280;';
            form.appendChild(msgEl);

            row.appendChild(form);
            input.focus();
            input.select();

            cancelBtn.addEventListener('click', function () {
                if (form.parentNode) form.parentNode.removeChild(form);
            });

            saveBtn.addEventListener('click', function () {
                var newValue = input.value;
                // newValue may be empty string — that is valid for "clearing" a field.
                // PHI: value is sent to authed backend only; not logged.

                saveBtn.disabled = true;
                cancelBtn.disabled = true;
                msgEl.textContent = labels.editSave || 'Saving…';

                var payload = {
                    extraction_id:   Number(extractionId),
                    field_path:      fieldPath,
                    clinician_value: newValue
                };
                if (blockId) payload.source_block_id = blockId;

                postFieldEdit(payload, function () {
                    // Success — update shadow state.
                    shadowEdits[fieldPath] = {
                        status:           'manually_edited',
                        clinician_value:  newValue,
                        original_value:   currentValue
                    };

                    // Dispatch refresh event.
                    try {
                        var ev = new CustomEvent('oe-copilot-hitl/extraction-updated', {
                            bubbles: true,
                            detail: { docId: currentDocId }
                        });
                        document.dispatchEvent(ev);
                    } catch (evErr) { /* non-critical */ }

                    // Update the row in-place: replace value display + add edited badge.
                    if (form.parentNode) form.parentNode.removeChild(form);
                    rebuildRowAfterEdit(row, fieldPath, currentValue, newValue, blockId);
                }, function (errCode) {
                    msgEl.textContent = errCode === 'forbidden'
                        ? (labels.editSaveForbidden || 'Edit not allowed.')
                        : (labels.editSaveError || 'Save failed. Please try again.');
                    msgEl.style.color = '#dc2626';
                    saveBtn.disabled = false;
                    cancelBtn.disabled = false;
                });
            });

            // Enter key in input triggers Save.
            input.addEventListener('keydown', function (e) {
                if (e.key === 'Enter') { e.preventDefault(); saveBtn.click(); }
                if (e.key === 'Escape') { cancelBtn.click(); }
            });
        }

        /**
         * Update a field row in-place after a successful inline save.
         * Shows "was: X | now: Y" and the clinician-edited badge.
         *
         * @param {HTMLElement} row
         * @param {string}      fieldPath
         * @param {string|null} oldValue
         * @param {string}      newValue
         * @param {string|null} blockId
         */
        function rebuildRowAfterEdit(row, fieldPath, oldValue, newValue, blockId) {
            row.setAttribute('data-status', 'manually_edited');

            // Update or create the value span.
            var valEl = row.querySelector('.hitl-field-value');
            if (valEl) {
                valEl.title = '';
                valEl.textContent = truncate(String(newValue), 40);
            }

            // Remove any existing edited badge / history (idempotent on re-save).
            var existingBadge = row.querySelector('.hitl-badge--edited');
            if (existingBadge) existingBadge.parentNode.removeChild(existingBadge);
            var existingHist = row.querySelector('.hitl-edit-history');
            if (existingHist) existingHist.parentNode.removeChild(existingHist);

            // Insert the edited badge before the block-id badge.
            var blkBadge = row.querySelector('.hitl-blk-badge');
            var editedBadge = document.createElement('span');
            editedBadge.className = 'hitl-badge--edited';
            editedBadge.textContent = labels.clinicianEdited || 'clinician edited';
            if (blkBadge) {
                row.insertBefore(editedBadge, blkBadge);
            } else {
                row.appendChild(editedBadge);
            }

            // Append history row at bottom of row.
            if (oldValue !== null) {
                var histRow = document.createElement('div');
                histRow.className = 'hitl-edit-history';

                var wasSpan = document.createElement('span');
                wasSpan.textContent = (labels.wasLabel || 'was:') + ' ';
                histRow.appendChild(wasSpan);
                var wasVal = document.createElement('span');
                wasVal.className = 'hitl-was-value';
                wasVal.textContent = truncate(String(oldValue), 30);
                histRow.appendChild(wasVal);
                var sep = document.createElement('span');
                sep.textContent = ' | ' + (labels.nowLabel || 'now:') + ' ';
                histRow.appendChild(sep);
                var nowVal = document.createElement('span');
                nowVal.className = 'hitl-now-value';
                nowVal.textContent = truncate(String(newValue), 30);
                histRow.appendChild(nowVal);
                row.appendChild(histRow);
            }
        }

        /**
         * Build a “possibly missed” block row (uncited docling block).
         * Includes an “Assert from block” button when the session is pending_review.
         *
         * @param {Object}  block
         * @param {Object}  opts  { extractionId, fieldPath?, isPending, allBlocks }
         */
        function buildBlockRow(block, opts) {
            opts = opts || {};
            var isPending    = opts.isPending !== false;
            var extractionId = opts.extractionId || '';
            var allBlocks    = Array.isArray(opts.allBlocks) ? opts.allBlocks : [];

            var row = document.createElement('div');
            row.className = 'hitl-field-row';
            row.setAttribute('role', 'listitem');
            row.setAttribute('data-block-id', block.block_id || '');
            row.setAttribute('data-status', 'uncited');

            var badge = document.createElement('span');
            badge.className = 'hitl-blk-badge hitl-blk-badge--possibly';
            badge.textContent = block.block_id || '';
            row.appendChild(badge);

            var pageLabel = document.createElement('span');
            pageLabel.style.cssText = 'font-size:11px;color:#9ca3af;margin-left:4px;';
            pageLabel.textContent = 'p.' + (block.page || '?');
            row.appendChild(pageLabel);

            if (block.text_snippet) {
                var snippet = document.createElement('span');
                snippet.className = 'hitl-block-snippet';
                // Truncate to 240 chars display (R2 bump from 80).
                snippet.textContent = '”' + truncate(block.text_snippet, 240) + '”';
                row.appendChild(snippet);
            }

            row.addEventListener('click', function () {
                if (block.block_id) emitHighlight(block.block_id);
            });

            // “Assert from block” affordance — only in pending_review.
            // For a Possibly-Missed block row, we don't have a pre-existing field_path
            // so we let the clinician supply both value and the block as the source.
            // We create a synthetic assert form with a field_path input.
            if (isPending) {
                var assertBtn = document.createElement('button');
                assertBtn.type = 'button';
                assertBtn.className = 'hitl-assert-btn';
                assertBtn.textContent = labels.assertBtnLabel || '+ Assert from block';
                row.appendChild(assertBtn);

                assertBtn.addEventListener('click', function (e) {
                    e.stopPropagation();
                    openBlockAssertForm(row, block, null, extractionId, allBlocks);
                });
            }

            return row;
        }

        /**
         * Build a Missed row (stripped field) with “Assert from block” affordance.
         *
         * @param {Object} field          The stripped field descriptor.
         * @param {Object} opts           { extractionId, allBlocks, isPending }
         * @returns {HTMLElement}
         */
        function buildMissedRow(field, opts) {
            opts = opts || {};
            var isPending    = opts.isPending !== false;
            var extractionId = opts.extractionId || '';
            var allBlocks    = Array.isArray(opts.allBlocks) ? opts.allBlocks : [];

            var row = document.createElement('div');
            row.className = 'hitl-field-row';
            row.setAttribute('role', 'listitem');
            row.setAttribute('data-block-id', field.source_block_id || '');
            row.setAttribute('data-status', 'stripped');
            row.setAttribute('data-field-path', field.field_path || '');

            var pathEl = document.createElement('span');
            pathEl.className = 'hitl-field-path';
            pathEl.textContent = field.field_path || '';
            row.appendChild(pathEl);

            if (field.verifier_reason) {
                var reasonEl = document.createElement('span');
                reasonEl.className = 'hitl-reason';
                reasonEl.title = field.verifier_reason;
                reasonEl.textContent = humanizeReason(field.verifier_reason);
                row.appendChild(reasonEl);
            }

            var badge = document.createElement('span');
            badge.className = 'hitl-blk-badge hitl-blk-badge--stripped';
            badge.setAttribute('data-block-id', field.source_block_id || '');
            badge.textContent = field.source_block_id ? field.source_block_id : 'no block';
            badge.setAttribute('aria-label', field.source_block_id
                ? ('Block ' + field.source_block_id)
                : 'No block ID');
            row.appendChild(badge);

            if (field.source_block_id) {
                row.addEventListener('click', function () {
                    emitHighlight(field.source_block_id);
                });
            }

            // “Assert from block” affordance — only in pending_review.
            if (isPending) {
                var assertBtn = document.createElement('button');
                assertBtn.type = 'button';
                assertBtn.className = 'hitl-assert-btn';
                assertBtn.textContent = labels.assertBtnLabel || '+ Assert from block';
                row.appendChild(assertBtn);

                assertBtn.addEventListener('click', function (e) {
                    e.stopPropagation();
                    openBlockAssertForm(row, null, field.field_path, extractionId, allBlocks);
                });
            }

            return row;
        }

        /**
         * Open an “Assert from block” form inside a Missed or Possibly-Missed row.
         *
         * The form shows:
         *   1. A block picker list (text/table blocks filtered, grouped by page).
         *   2. A text input for the asserted value.
         *   3. Save / Cancel buttons.
         *
         * Hover over a picker item → emitHighlight on the PDF overlay.
         * Select a picker item → pre-fill context, unhide Save.
         *
         * @param {HTMLElement}  row           The row to append the form into.
         * @param {Object|null}  preselBlock   If known (block row), pre-select this block.
         * @param {string|null}  knownFieldPath  If known (missed row), pre-fill.
         * @param {string}       extractionId
         * @param {Object[]}     allBlocks     All docling_blocks from the current extraction.
         */
        function openBlockAssertForm(row, preselBlock, knownFieldPath, extractionId, allBlocks) {
            // Only one form open at a time per row.
            if (row.querySelector('.hitl-assert-form')) return;

            // Filter to text + table blocks.
            var PERMITTED_TYPES = { text: true, table: true, paragraph: true,
                                     section_header: true, list_item: true };
            var filteredBlocks = allBlocks.filter(function (b) {
                if (!b.block_type) return true; // unknown type — include
                return PERMITTED_TYPES[String(b.block_type).toLowerCase()] === true;
            });

            var form = document.createElement('div');
            form.className = 'hitl-assert-form';

            // Build block picker.
            var picker = document.createElement('div');
            picker.className = 'hitl-block-picker';
            picker.setAttribute('role', 'listbox');
            picker.setAttribute('aria-label', 'Source block picker');

            var selectedBlockId = preselBlock ? (preselBlock.block_id || null) : null;

            // Group blocks by page for display.
            filteredBlocks.forEach(function (blk) {
                var item = document.createElement('div');
                item.className = 'hitl-block-picker-item';
                item.setAttribute('role', 'option');
                item.setAttribute('data-block-id', blk.block_id || '');
                item.setAttribute('aria-selected', blk.block_id === selectedBlockId ? 'true' : 'false');

                var pageSpan = document.createElement('span');
                pageSpan.className = 'hitl-picker-page';
                pageSpan.textContent = 'p.' + (blk.page || '?');
                item.appendChild(pageSpan);

                if (blk.block_type) {
                    var typeSpan = document.createElement('span');
                    typeSpan.className = 'hitl-picker-type';
                    typeSpan.textContent = blk.block_type;
                    item.appendChild(typeSpan);
                }

                if (blk.text_snippet) {
                    var snip = document.createElement('span');
                    snip.className = 'hitl-picker-snippet';
                    snip.textContent = '”' + truncate(blk.text_snippet, 240) + '”';
                    item.appendChild(snip);
                }

                // Hover → highlight on PDF overlay.
                item.addEventListener('mouseenter', function () {
                    if (blk.block_id) emitHighlight(blk.block_id);
                });

                // Click → select this block.
                item.addEventListener('click', function (e) {
                    e.stopPropagation();
                    selectedBlockId = blk.block_id || null;
                    // Update aria-selected on all items.
                    var allItems = picker.querySelectorAll('.hitl-block-picker-item');
                    for (var ii = 0; ii < allItems.length; ii++) {
                        allItems[ii].classList.remove('is-selected');
                        allItems[ii].setAttribute('aria-selected', 'false');
                    }
                    item.classList.add('is-selected');
                    item.setAttribute('aria-selected', 'true');
                    if (blk.block_id) emitHighlight(blk.block_id);
                });

                if (preselBlock && blk.block_id === selectedBlockId) {
                    item.classList.add('is-selected');
                    item.setAttribute('aria-selected', 'true');
                }

                picker.appendChild(item);
            });

            form.appendChild(picker);

            // Value row.
            var valueRow = document.createElement('div');
            valueRow.className = 'hitl-assert-value-row';

            // Field path input (only shown if knownFieldPath is null — block rows).
            var fieldPathInput = null;
            if (!knownFieldPath) {
                var fpLabel = document.createElement('span');
                fpLabel.className = 'hitl-assert-label';
                fpLabel.textContent = 'Field path:';
                valueRow.appendChild(fpLabel);

                fieldPathInput = document.createElement('input');
                fieldPathInput.type = 'text';
                fieldPathInput.className = 'hitl-assert-input';
                fieldPathInput.placeholder = 'e.g. patient.name';
                fieldPathInput.setAttribute('aria-label', 'Field path');
                valueRow.appendChild(fieldPathInput);
            }

            var valLabel = document.createElement('span');
            valLabel.className = 'hitl-assert-label';
            valLabel.textContent = labels.valueLabel || 'Value:';
            valueRow.appendChild(valLabel);

            var valInput = document.createElement('input');
            valInput.type = 'text';
            valInput.className = 'hitl-assert-input';
            valInput.setAttribute('aria-label', 'Asserted value');
            // PHI: value is user-supplied and sent only to authed backend.
            valueRow.appendChild(valInput);

            var saveBtn = document.createElement('button');
            saveBtn.type = 'button';
            saveBtn.className = 'hitl-assert-save-btn';
            saveBtn.textContent = 'Save';
            valueRow.appendChild(saveBtn);

            var cancelBtn = document.createElement('button');
            cancelBtn.type = 'button';
            cancelBtn.className = 'hitl-assert-cancel-btn';
            cancelBtn.textContent = labels.assertCancelLabel || 'Cancel';
            valueRow.appendChild(cancelBtn);

            var msgEl = document.createElement('span');
            msgEl.style.cssText = 'font-size:11px;color:#6b7280;';
            valueRow.appendChild(msgEl);

            form.appendChild(valueRow);
            row.appendChild(form);

            if (valInput) valInput.focus();

            cancelBtn.addEventListener('click', function () {
                if (form.parentNode) form.parentNode.removeChild(form);
            });

            saveBtn.addEventListener('click', function () {
                var fieldPath = knownFieldPath || (fieldPathInput ? fieldPathInput.value.trim() : '');
                var newValue  = valInput.value;

                if (!fieldPath) {
                    msgEl.textContent = 'Field path is required.';
                    msgEl.style.color = '#dc2626';
                    return;
                }
                if (!selectedBlockId) {
                    msgEl.textContent = labels.assertPickBlock || 'Select a source block above.';
                    msgEl.style.color = '#dc2626';
                    return;
                }

                saveBtn.disabled  = true;
                cancelBtn.disabled = true;
                msgEl.textContent = labels.assertSave || 'Asserting…';
                msgEl.style.color = '#6b7280';

                var payload = {
                    extraction_id:   Number(extractionId),
                    field_path:      fieldPath,
                    clinician_value: newValue,
                    source_block_id: selectedBlockId
                };

                postFieldEdit(payload, function () {
                    // Success — update shadow state.
                    shadowEdits[fieldPath] = {
                        status:          'manually_added',
                        clinician_value: newValue,
                        source_block_id: selectedBlockId
                    };

                    // Dispatch refresh event — renderRightPane will re-run.
                    try {
                        var ev = new CustomEvent('oe-copilot-hitl/extraction-updated', {
                            bubbles: true,
                            detail: { docId: currentDocId }
                        });
                        document.dispatchEvent(ev);
                    } catch (evErr) { /* non-critical */ }

                    // Move row visually from Missed/Possibly-Missed to Captured.
                    if (form.parentNode) form.parentNode.removeChild(form);
                    convertRowToAdded(row, fieldPath, selectedBlockId);
                }, function (errCode) {
                    msgEl.textContent = errCode === 'forbidden'
                        ? (labels.editSaveForbidden || 'Edit not allowed.')
                        : (labels.assertSaveError || 'Assert failed. Please try again.');
                    msgEl.style.color = '#dc2626';
                    saveBtn.disabled  = false;
                    cancelBtn.disabled = false;
                });
            });

            // Keyboard nav.
            valInput.addEventListener('keydown', function (e) {
                if (e.key === 'Enter') { e.preventDefault(); saveBtn.click(); }
                if (e.key === 'Escape') { cancelBtn.click(); }
            });
        }

        /**
         * After a successful assert-from-block save, move the row from
         * Missed/Possibly-Missed into the Captured section.
         *
         * @param {HTMLElement} row
         * @param {string}      fieldPath
         * @param {string}      blockId
         */
        function convertRowToAdded(row, fieldPath, blockId) {
            row.setAttribute('data-status', 'manually_added');
            row.setAttribute('data-block-id', blockId || '');

            // Remove the stripped badge + reason if present.
            var strippedBadge = row.querySelector('.hitl-blk-badge--stripped');
            if (strippedBadge) strippedBadge.parentNode.removeChild(strippedBadge);
            var reasonEl = row.querySelector('.hitl-reason');
            if (reasonEl) reasonEl.parentNode.removeChild(reasonEl);
            var assertBtn = row.querySelector('.hitl-assert-btn');
            if (assertBtn) assertBtn.parentNode.removeChild(assertBtn);

            // Add manually-added badge.
            var addedBadge = document.createElement('span');
            addedBadge.className = 'hitl-badge--added';
            addedBadge.textContent = labels.clinicianAdded || 'clinician added';

            // Add new block ID badge.
            var newBlkBadge = document.createElement('span');
            newBlkBadge.className = 'hitl-blk-badge hitl-blk-badge--manually_added';
            newBlkBadge.setAttribute('data-block-id', blockId || '');
            newBlkBadge.textContent = blockId || '';

            var pathEl = row.querySelector('.hitl-field-path');
            if (pathEl) {
                if (pathEl.textContent !== fieldPath) {
                    pathEl.textContent = fieldPath;
                }
            }

            row.appendChild(addedBadge);
            row.appendChild(newBlkBadge);

            // Move row to captured list.
            var capturedList = document.getElementById('hitl-list-captured');
            if (capturedList) {
                var emptyEl = capturedList.querySelector('.hitl-empty-section');
                if (emptyEl) capturedList.removeChild(emptyEl);
                capturedList.appendChild(row);
            }

            // Update count badges.
            refreshCountBadges();
        }

        /**
         * Re-count field rows in each section and update the count badges.
         * Called after in-place mutations (assert-from-block move, etc.).
         */
        function refreshCountBadges() {
            if (el.listCaptured && el.cntCaptured) {
                el.cntCaptured.textContent = String(
                    el.listCaptured.querySelectorAll('.hitl-field-row').length
                );
            }
            if (el.listMissed && el.cntMissed) {
                el.cntMissed.textContent = String(
                    el.listMissed.querySelectorAll('.hitl-field-row').length
                );
            }
            if (el.listPossibly && el.cntPossibly) {
                el.cntPossibly.textContent = String(
                    el.listPossibly.querySelectorAll('.hitl-field-row').length
                );
            }
        }

        function renderRightPane(extraction) {
            var fields = Array.isArray(extraction.fields) ? extraction.fields : [];
            var blocks = Array.isArray(extraction.docling_blocks) ? extraction.docling_blocks : [];
            var extractionId = String((extraction && extraction.extraction_id) || '');
            var extractionStatus = (extraction && typeof extraction.status === 'string')
                ? extraction.status : '';
            var isPending = (!extractionStatus || extractionStatus === 'pending_review');

            // Build a set of cited block IDs (including manually_added).
            var citedBlockIds = {};
            fields.forEach(function (f) {
                if (f.source_block_id) citedBlockIds[f.source_block_id] = true;
            });
            // Also cite blocks for in-session shadow additions.
            Object.keys(shadowEdits).forEach(function (fp) {
                var s = shadowEdits[fp];
                if (s.source_block_id) citedBlockIds[s.source_block_id] = true;
            });

            // Partition fields into display buckets.
            // manually_edited / manually_added rows come from backend
            // (from a prior session) or from this session's shadow.
            var captured = [];
            var missed   = [];

            fields.forEach(function (f) {
                var effectiveStatus = f.status;
                // Check if this session has a shadow for this field.
                var shadow = shadowEdits[f.field_path];
                if (shadow) {
                    effectiveStatus = shadow.status;
                }
                if (effectiveStatus === 'verified' ||
                    effectiveStatus === 'manually_edited' ||
                    effectiveStatus === 'manually_added') {
                    captured.push({
                        field_path:     f.field_path,
                        value:          shadow ? shadow.clinician_value : f.value,
                        source_block_id: f.source_block_id,
                        status:          effectiveStatus,
                        original_value:  shadow ? f.value : null,
                        is_carried_over: f.is_carried_over || false
                    });
                } else if (effectiveStatus === 'stripped') {
                    missed.push(f);
                }
            });

            // Also surface shadow-added entries that are NOT yet in the fields array
            // (i.e., the user just added them in this session before a re-fetch).
            Object.keys(shadowEdits).forEach(function (fp) {
                var shadow = shadowEdits[fp];
                if (shadow.status !== 'manually_added') return;
                var alreadyInFields = fields.some(function (f) {
                    return f.field_path === fp;
                });
                if (!alreadyInFields) {
                    captured.push({
                        field_path:      fp,
                        value:           shadow.clinician_value,
                        source_block_id: shadow.source_block_id || null,
                        status:          'manually_added',
                        original_value:  null,
                        is_carried_over: false
                    });
                }
            });

            var possibly = blocks.filter(function (b) { return !citedBlockIds[b.block_id]; });

            // Update count badges.
            if (el.cntCaptured) el.cntCaptured.textContent = String(captured.length);
            if (el.cntMissed)   el.cntMissed.textContent   = String(missed.length);
            if (el.cntPossibly) el.cntPossibly.textContent  = String(possibly.length);

            // Render captured list.
            if (el.listCaptured) {
                el.listCaptured.innerHTML = '';
                if (captured.length === 0) {
                    el.listCaptured.innerHTML = '<div class="hitl-empty-section">None</div>';
                } else {
                    captured.forEach(function (f) {
                        el.listCaptured.appendChild(
                            buildFieldRow(f.status, f.field_path, f.value,
                                          f.source_block_id, null, {
                                extractionId:  extractionId,
                                isPending:     isPending,
                                originalValue: f.original_value,
                                isCarriedOver: f.is_carried_over
                            })
                        );
                    });
                }
            }

            // Render missed list — NO value rendered (PHI rule).
            if (el.listMissed) {
                el.listMissed.innerHTML = '';
                if (missed.length === 0) {
                    el.listMissed.innerHTML = '<div class="hitl-empty-section">None</div>';
                } else {
                    missed.forEach(function (f) {
                        el.listMissed.appendChild(
                            buildMissedRow(f, {
                                extractionId: extractionId,
                                allBlocks:    blocks,
                                isPending:    isPending
                            })
                        );
                    });
                }
            }

            // Render possibly-missed list.
            if (el.listPossibly) {
                el.listPossibly.innerHTML = '';
                if (possibly.length === 0) {
                    el.listPossibly.innerHTML = '<div class="hitl-empty-section">None</div>';
                } else {
                    possibly.forEach(function (b) {
                        el.listPossibly.appendChild(
                            buildBlockRow(b, {
                                extractionId: extractionId,
                                allBlocks:    blocks,
                                isPending:    isPending
                            })
                        );
                    });
                }
            }

            // Reprocess button: enable only if there are stripped fields or
            // if in pending_review (always allow reprocess on pending).
            if (el.reprocessBtn) {
                var hasStripped = missed.length > 0;
                el.reprocessBtn.disabled = !hasStripped;
                el.reprocessBtn.title = hasStripped
                    ? ''
                    : (labels.noFieldsMissed || 'No missed fields — reprocess is not needed.');
            }

            // Right-pane highlight listener: toggle .is-highlighted on rows.
            onHighlight(function (blockId) {
                var allRows = document.querySelectorAll(
                    '#hitl-list-captured .hitl-field-row, ' +
                    '#hitl-list-missed .hitl-field-row, ' +
                    '#hitl-list-possibly .hitl-field-row'
                );
                for (var i = 0; i < allRows.length; i++) {
                    var row = allRows[i];
                    var match = row.getAttribute('data-block-id') === blockId;
                    row.classList.toggle('is-highlighted', match);
                    if (match) {
                        row.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
                    }
                }
            });
        }

        // ---- 7. Render PDF left pane + SVG overlay ----------------------

        /**
         * Compute SVG rect coordinates from a Docling bbox in PDF point
         * space, given the PDF.js viewport for the page.
         *
         * Docling bbox: { x, y, w, h } — origin bottom-left, Y increases up.
         * PDF.js viewport for standard (unrotated) page:
         *   transform = [S, 0, 0, -S, 0, H*S]
         *   where S = scale, H = page height in points.
         *
         * Conversion (flip Y axis):
         *   rectX = x * S
         *   rectY = (H - y - h) * S
         *   rectW = w * S
         *   rectH = h * S
         *
         * This is equivalent to:
         *   [cx, cy] = viewport.convertToViewportPoint(x, y + h)
         *   and reading off cx, cy for the top-left of the rect.
         * We use the explicit formula to avoid a PDF.js API dependency
         * that changed between major versions.
         */
        function bboxToSvgRect(bbox, viewport) {
            var S = viewport.scale;
            // viewport.height is the canvas height in CSS pixels.
            // For a non-rotated page: viewport.height = H * S.
            var H_px = viewport.height; // in canvas pixels

            var x  = (bbox.x       ) * S;
            var y  = H_px - (bbox.y + bbox.h) * S;
            var w  = bbox.w * S;
            var h  = bbox.h * S;

            return { x: x, y: y, w: w, h: h };
        }

        /**
         * Determine the stroke status for a given block_id based on
         * whether it appears as a source for any field, and if so which
         * status (verified/stripped/manually_added).
         *
         * Checks session-level shadowEdits first so in-session asserts are
         * reflected on the PDF overlay immediately after save.
         *
         * Returns: 'verified' | 'stripped' | 'uncited' | 'manually_added'
         */
        function blockStatus(blockId, fields) {
            // Check shadow state first (in-session asserts).
            var shadowKeys = Object.keys(shadowEdits);
            for (var s = 0; s < shadowKeys.length; s++) {
                var shadow = shadowEdits[shadowKeys[s]];
                if (shadow.source_block_id === blockId) {
                    return 'manually_added';
                }
            }
            for (var i = 0; i < fields.length; i++) {
                if (fields[i].source_block_id === blockId) {
                    var st = fields[i].status;
                    if (st === 'manually_added') return 'manually_added';
                    return st === 'verified' ? 'verified' : 'stripped';
                }
            }
            return 'uncited';
        }

        function renderPdfPane(docId, extraction) {
            var fields = Array.isArray(extraction.fields) ? extraction.fields : [];
            var blocks = Array.isArray(extraction.docling_blocks) ? extraction.docling_blocks : [];

            // Detach the legend before clearing so it survives the wipe.
            var legendEl = document.getElementById('hitl-bbox-legend');
            if (legendEl && legendEl.parentNode === el.pdfPane) {
                el.pdfPane.removeChild(legendEl);
            }

            // Clear existing pages.
            while (el.pdfPane.firstChild) {
                el.pdfPane.removeChild(el.pdfPane.firstChild);
            }

            // Show loading indicator.
            var loadingEl = document.createElement('div');
            loadingEl.className = 'hitl-pdf-loading';
            loadingEl.id = 'hitl-pdf-loading';
            loadingEl.setAttribute('aria-live', 'polite');
            loadingEl.textContent = labels.loadingDoc || 'Loading document…';
            el.pdfPane.appendChild(loadingEl);

            // Guard: placeholder PDF.js → degrade gracefully.
            if (!window.pdfjsLib || window.pdfjsLib._isPlaceholder) {
                loadingEl.textContent = '';
                var errEl = document.createElement('div');
                errEl.className = 'hitl-pdf-error';
                errEl.textContent = labels.pdfPlaceholder ||
                    'PDF.js not loaded — install vendor/pdfjs/ bundle.';
                el.pdfPane.appendChild(errEl);
                // Re-append legend even on placeholder path.
                if (legendEl) el.pdfPane.appendChild(legendEl);
                // Still render right-pane without PDF context.
                return;
            }

            // Document URL: same path the Documents module iframe uses.
            // Angular controller navigates to:
            //   /interface/modules/zend_modules/index.php/Documents/retrieve/id/{doc_id}
            var pdfUrl = window.location.origin +
                '/interface/modules/zend_modules/index.php/Documents/retrieve/id/' +
                encodeURIComponent(docId);

            var loadTask = window.pdfjsLib.getDocument({
                url: pdfUrl,
                withCredentials: true,
            });

            loadTask.promise.then(function (pdfDoc) {
                // Remove loading indicator.
                if (loadingEl.parentNode) {
                    loadingEl.parentNode.removeChild(loadingEl);
                }

                var numPages = pdfDoc.numPages;
                var pagePromises = [];

                for (var pageNum = 1; pageNum <= numPages; pageNum++) {
                    pagePromises.push(renderPage(pdfDoc, pageNum, fields, blocks));
                }

                // Re-append legend after pages (Promise.all is async but legend
                // can be re-appended after page renders resolve).
                return Promise.all(pagePromises).then(function () {
                    if (legendEl) el.pdfPane.appendChild(legendEl);
                });
            }).catch(function () {
                loadingEl.textContent = '';
                var err = document.createElement('div');
                err.className = 'hitl-pdf-error';
                err.textContent = labels.pdfError || 'Unable to render PDF preview.';
                el.pdfPane.appendChild(err);
                // Re-append legend even on error.
                if (legendEl) el.pdfPane.appendChild(legendEl);
            });

            // Left-pane highlight listener: toggle .is-highlighted on SVG rects.
            onHighlight(function (blockId) {
                var allRects = el.pdfPane.querySelectorAll('rect[data-block-id]');
                for (var i = 0; i < allRects.length; i++) {
                    var r = allRects[i];
                    var match = r.getAttribute('data-block-id') === blockId;
                    r.classList.toggle('is-highlighted', match);
                    if (match) {
                        r.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
                    }
                }
            });
        }

        function renderPage(pdfDoc, pageNum, fields, blocks) {
            return pdfDoc.getPage(pageNum).then(function (page) {
                // Target width ≈ left pane inner width. We measure the pane
                // and fit the page to its width at 1:1 CSS pixels.
                var paneWidth = el.pdfPane.clientWidth || 600;
                var desiredWidth = Math.max(paneWidth - 16, 200); // 8px padding each side

                var naturalViewport = page.getViewport({ scale: 1.0 });
                var scale = desiredWidth / naturalViewport.width;
                var viewport = page.getViewport({ scale: scale });

                // Wrapper div (position:relative for SVG overlay).
                var wrap = document.createElement('div');
                wrap.className = 'hitl-pdf-page-wrap';
                wrap.style.width  = Math.round(viewport.width)  + 'px';
                wrap.style.height = Math.round(viewport.height) + 'px';
                wrap.setAttribute('data-page', String(pageNum));

                // Canvas.
                var canvas = document.createElement('canvas');
                canvas.className = 'hitl-pdf-canvas';
                canvas.width  = Math.round(viewport.width);
                canvas.height = Math.round(viewport.height);
                canvas.style.width  = Math.round(viewport.width)  + 'px';
                canvas.style.height = Math.round(viewport.height) + 'px';
                wrap.appendChild(canvas);

                // SVG overlay (same dimensions as canvas).
                var svgNS = 'http://www.w3.org/2000/svg';
                var svg = document.createElementNS(svgNS, 'svg');
                svg.setAttribute('xmlns', svgNS);
                svg.setAttribute('width',  String(Math.round(viewport.width)));
                svg.setAttribute('height', String(Math.round(viewport.height)));
                svg.setAttribute('viewBox', '0 0 ' +
                    Math.round(viewport.width) + ' ' + Math.round(viewport.height));
                svg.className = 'hitl-pdf-overlay';
                svg.style.width  = Math.round(viewport.width)  + 'px';
                svg.style.height = Math.round(viewport.height) + 'px';
                wrap.appendChild(svg);

                el.pdfPane.appendChild(wrap);

                // Render the page to canvas.
                var renderCtx = {
                    canvasContext: canvas.getContext('2d'),
                    viewport: viewport,
                };
                var renderTask = page.render(renderCtx);

                return renderTask.promise.then(function () {
                    // Draw bboxes for docling_blocks on this page.
                    var pageBlocks = blocks.filter(function (b) {
                        return Number(b.page) === pageNum && b.bbox;
                    });

                    pageBlocks.forEach(function (block) {
                        var bbox = block.bbox;
                        var coords = bboxToSvgRect(bbox, viewport);
                        var status = blockStatus(block.block_id, fields);

                        var rect = document.createElementNS(svgNS, 'rect');
                        rect.setAttribute('x',      String(Math.round(coords.x)));
                        rect.setAttribute('y',      String(Math.round(coords.y)));
                        rect.setAttribute('width',  String(Math.round(coords.w)));
                        rect.setAttribute('height', String(Math.round(coords.h)));
                        rect.setAttribute('data-block-id', block.block_id || '');
                        rect.setAttribute('data-bbox-status', status);
                        rect.setAttribute('role', 'button');
                        rect.setAttribute('aria-label',
                            'Block ' + (block.block_id || '?') + ' (' + status + ')');
                        rect.setAttribute('tabindex', '0');

                        rect.addEventListener('click', function () {
                            if (block.block_id) emitHighlight(block.block_id);
                        });
                        rect.addEventListener('keydown', function (e) {
                            if (e.key === 'Enter' || e.key === ' ') {
                                e.preventDefault();
                                if (block.block_id) emitHighlight(block.block_id);
                            }
                        });

                        svg.appendChild(rect);
                    });
                });
            });
        }

        // ---- 8. Populate modal header -----------------------------------

        function renderHeader(extraction) {
            if (el.hdrDocType) {
                el.hdrDocType.textContent = extraction.doc_type || '—';
            }
            if (el.hdrAttempt) {
                var n   = extraction.attempt_n   != null ? extraction.attempt_n   : '—';
                var max = extraction.max_attempts != null ? extraction.max_attempts : '3';
                el.hdrAttempt.textContent = 'attempt ' + n + '/' + max;
            }
            if (el.hdrModel && extraction.model) {
                el.hdrModel.textContent = extraction.model;
                el.hdrModel.style.display = '';
            } else if (el.hdrModel) {
                el.hdrModel.style.display = 'none';
            }
        }

        // ---- 9. Open the modal ------------------------------------------

        function openModal(docId, extraction) {
            clearHighlightListeners();
            setFooterMsg('');
            if (el.reprocessBtn) el.reprocessBtn.disabled = true;

            // Reset R2 session state on every modal open.
            shadowEdits    = {};
            currentDocId   = docId || null;
            currentExtraction = extraction || null;

            // Reset discard-edits checkbox (must never persist between sessions).
            if (el.discardEditsChk) el.discardEditsChk.checked = false;

            // Hide "just re-extracted" pill on fresh open.
            if (el.hdrReextracted) el.hdrReextracted.style.display = 'none';

            // Show/hide Approve + Reject based on current extraction status.
            var extractionStatus = (extraction && typeof extraction.status === 'string')
                ? extraction.status : '';
            updateFooterButtons(extractionStatus);

            // Store docId + extractionId on approve/reject buttons so their
            // click handlers can read them (mirrors the reprocess button pattern).
            var extractionId = String((extraction && extraction.extraction_id) ? extraction.extraction_id : '');
            if (el.approveBtn) {
                el.approveBtn.setAttribute('data-doc-id', docId || '');
                el.approveBtn.setAttribute('data-extraction-id', extractionId);
            }
            if (el.rejectBtn) {
                el.rejectBtn.setAttribute('data-doc-id', docId || '');
                el.rejectBtn.setAttribute('data-extraction-id', extractionId);
            }

            renderHeader(extraction || {});
            renderRightPane(extraction || { fields: [], docling_blocks: [] });

            // Bootstrap 4 modal open — use jQuery if available (OpenEMR
            // ships jQuery 3.7 globally), otherwise dispatch show event.
            var modalEl = document.getElementById('hitl-review-modal');
            if (!modalEl) return;

            if (typeof window.$ === 'function' && typeof window.$.fn === 'object') {
                window.$('#hitl-review-modal').modal('show');
            } else {
                // Minimal Bootstrap 4 modal trigger fallback.
                modalEl.classList.add('show');
                modalEl.style.display = 'block';
                document.body.classList.add('modal-open');
                var backdrop = document.getElementById('hitl-modal-backdrop');
                if (!backdrop) {
                    backdrop = document.createElement('div');
                    backdrop.id = 'hitl-modal-backdrop';
                    backdrop.className = 'modal-backdrop fade show';
                    document.body.appendChild(backdrop);
                }
            }

            // Render PDF after modal is visible so clientWidth is correct.
            if (extraction) {
                renderPdfPane(docId, extraction);
            }
        }

        // ---- 10. Reprocess from modal footer ----------------------------

        function triggerReprocess(docId, extractionId) {
            if (!extractionId || !docId) return;

            var csrfToken    = cfg.csrfToken    || '';
            var reprocessUrl = cfg.reprocessUrl || '';

            // Read discard-manual-edits opt-in.
            var discardManualEdits = !!(el.discardEditsChk && el.discardEditsChk.checked);

            if (el.reprocessBtn) {
                el.reprocessBtn.disabled = true;
                el.reprocessBtn.innerHTML =
                    '<span class="hitl-spinner"></span> ' +
                    (labels.reprocessing || 'Reprocessing… (may take up to 30s)');
            }
            setFooterMsg(labels.reprocessing || 'Reprocessing… (may take up to 30s)');

            var xhr = new XMLHttpRequest();
            xhr.open('POST', reprocessUrl, true);
            xhr.setRequestHeader('Content-Type', 'application/json');
            xhr.setRequestHeader('X-CSRF-Token', csrfToken);
            xhr.onreadystatechange = function () {
                if (xhr.readyState !== 4) return;

                if (el.reprocessBtn) {
                    el.reprocessBtn.textContent = 'Reprocess this document';
                }

                // Parse body once for both status branches. NEVER echo patient
                // data from xhr.responseText — only structural fields.
                var body = null;
                try { body = JSON.parse(xhr.responseText); } catch (parseErr) { /* ignore */ }

                // R2 contract: backend now writes the new attempt with
                // status='pending_review' and returns 200 with the new
                // extraction data (no auto-round-trip). The response status
                // field will be 'pending_review' (not 'ok') for a successful
                // reprocess. We re-fetch the extraction to get the full data.
                //
                // Legacy 'ok' shape is kept as a fallback in case an older
                // backend is encountered.
                //
                // Both shapes:
                //   1. HTTP 200: { status:"pending_review"|"ok"|"refused"|"error",
                //                  new_extraction_id, ... }
                //   2. HTTP non-2xx: { status:"error", detail:"<token>" }

                var agentStatus = body && typeof body.status === 'string' ? body.status : null;
                var isSuccess   = xhr.status === 200 &&
                                  (agentStatus === 'pending_review' || agentStatus === 'ok');

                if (isSuccess) {
                    setFooterMsg(labels.reprocessOk || 'Reprocessed successfully.', 'success');
                    // Re-fetch and re-render in-place so the modal stays open.
                    var url = (cfg.extractionForDocUrl || '') +
                              '?doc_id=' + encodeURIComponent(docId);
                    var xhr2 = new XMLHttpRequest();
                    xhr2.open('GET', url, true);
                    xhr2.setRequestHeader('Accept', 'application/json');
                    xhr2.onreadystatechange = function () {
                        if (xhr2.readyState !== 4) return;
                        if (xhr2.status === 200) {
                            var data;
                            try { data = JSON.parse(xhr2.responseText); } catch (e) { return; }

                            // Clear session shadow edits if discard was checked,
                            // otherwise carry them over.
                            if (discardManualEdits) {
                                shadowEdits = {};
                            }
                            // Reset the discard checkbox.
                            if (el.discardEditsChk) el.discardEditsChk.checked = false;

                            currentExtraction = data;

                            // Update footer buttons (new attempt = pending_review).
                            var newStatus = (data && typeof data.status === 'string')
                                ? data.status : 'pending_review';
                            updateFooterButtons(newStatus);

                            // Update approve/reject button data attrs.
                            var newExtractionId = String((data && data.extraction_id) || '');
                            if (el.approveBtn) {
                                el.approveBtn.setAttribute('data-doc-id', docId);
                                el.approveBtn.setAttribute('data-extraction-id', newExtractionId);
                            }
                            if (el.rejectBtn) {
                                el.rejectBtn.setAttribute('data-doc-id', docId);
                                el.rejectBtn.setAttribute('data-extraction-id', newExtractionId);
                            }
                            if (el.reprocessBtn) {
                                el.reprocessBtn.setAttribute('data-doc-id', docId);
                                el.reprocessBtn.setAttribute('data-extraction-id', newExtractionId);
                            }

                            // Show "just re-extracted" pill.
                            if (el.hdrReextracted) {
                                el.hdrReextracted.style.display = '';
                            }

                            clearHighlightListeners();
                            renderHeader(data);
                            renderRightPane(data);
                            renderPdfPane(docId, data);
                            setFooterMsg('');

                            // Also refresh the banner in the parent document.
                            try {
                                var bannerEvent = new CustomEvent('oe-copilot-hitl/extraction-updated', {
                                    bubbles: true,
                                    detail: { docId: docId, extraction: data }
                                });
                                document.dispatchEvent(bannerEvent);
                            } catch (e2) { /* non-critical */ }
                        }
                    };
                    xhr2.send();
                } else {
                    // Map error tokens to structural messages. NEVER echo
                    // patient data, free-form detail strings, or sentence-shaped
                    // backend messages — only known tokens are surfaced.
                    var errMsg = labels.errorGeneric || 'Reprocess failed. Please try again.';
                    if (body) {
                        var errCode = (typeof body.detail === 'string' ? body.detail : '') ||
                                      (typeof body.error  === 'string' ? body.error  : '');
                        if (xhr.status === 200 && agentStatus === 'refused') {
                            errMsg = labels.errorRefused || 'Reprocess refused: extraction did not produce a confident result.';
                        } else if (xhr.status === 200 && agentStatus === 'error') {
                            errMsg = labels.errorAgent || 'Reprocess failed: the extraction service encountered an error.';
                        } else if (errCode === 'forbidden' || errCode === 'access_denied') {
                            errMsg = labels.errorForbidden || 'Reprocess not allowed for this account.';
                        } else if (errCode === 'cost_ceiling_exceeded') {
                            errMsg = labels.errorCostCap || 'Reprocess refused: cost ceiling exceeded.';
                        } else if (errCode === 'extraction_low_grounding') {
                            errMsg = labels.errorLowGround || 'Reprocess refused: extraction low grounding.';
                        }
                        // For all other backend detail strings (which may be
                        // sentence-shaped), fall through to the generic message
                        // so we never expose backend internals to the user.
                    }
                    setFooterMsg(errMsg, 'error');
                    // Re-enable button so the user can retry.
                    if (el.reprocessBtn) el.reprocessBtn.disabled = false;
                }
            };
            var reprocessPayload = { extraction_id: extractionId };
            if (discardManualEdits) reprocessPayload.discard_manual_edits = true;
            xhr.send(JSON.stringify(reprocessPayload));
        }

        // ---- 11. Event listeners ----------------------------------------

        // Listen for the open-modal event dispatched by hitl-banner.js.
        document.addEventListener('oe-copilot-hitl/open-modal', function (e) {
            var detail = e.detail || {};
            openModal(detail.docId, detail.extraction);
        });

        // Listen for post-reprocess banner refresh.
        document.addEventListener('oe-copilot-hitl/extraction-updated', function (e) {
            // hitl-banner.js handles the banner side. No action needed here.
        });

        // Reprocess button click.
        if (el.reprocessBtn) {
            el.reprocessBtn.addEventListener('click', function () {
                // Read current doc context from the open-modal event or
                // from the current extraction stored on the button's data.
                var docId = el.reprocessBtn.getAttribute('data-doc-id') || '';
                var extractionId = el.reprocessBtn.getAttribute('data-extraction-id') || '';
                if (!docId || !extractionId) return;

                var confirmed = window.confirm(
                    labels.confirmReprocess ||
                    'Reprocess this document? This will consume API credits.'
                );
                if (!confirmed) return;
                triggerReprocess(docId, Number(extractionId));
            });
        }

        // Store docId/extractionId on the reprocess button when modal opens
        // so the button handler above can read them.
        // Also keep currentExtraction / currentDocId in sync (openModal already
        // does this, but this listener fires before openModal in some code paths).
        document.addEventListener('oe-copilot-hitl/open-modal', function (e) {
            var detail = e.detail || {};
            if (el.reprocessBtn && detail.extraction) {
                el.reprocessBtn.setAttribute('data-doc-id', detail.docId || '');
                el.reprocessBtn.setAttribute(
                    'data-extraction-id',
                    String(detail.extraction.extraction_id || '')
                );
            }
        });

        // Approve button click — with N-fields-not-saved confirmation gate.
        if (el.approveBtn) {
            el.approveBtn.addEventListener('click', function () {
                var docId        = el.approveBtn.getAttribute('data-doc-id') || '';
                var extractionId = el.approveBtn.getAttribute('data-extraction-id') || '';
                if (!docId || !extractionId) return;

                // Count unresolved stripped fields.
                var unresolved = unresolvedStrippedPaths(currentExtraction || {});
                if (unresolved.length > 0) {
                    // Show confirmation modal — do NOT call triggerApprove yet.
                    showApproveConfirmation(unresolved, docId, Number(extractionId));
                } else {
                    // No unresolved strips — proceed directly.
                    triggerApprove(docId, Number(extractionId));
                }
            });
        }

        // Reject button click.
        if (el.rejectBtn) {
            el.rejectBtn.addEventListener('click', function () {
                var docId        = el.rejectBtn.getAttribute('data-doc-id') || '';
                var extractionId = el.rejectBtn.getAttribute('data-extraction-id') || '';
                if (!docId || !extractionId) return;
                triggerReject(docId, Number(extractionId));
            });
        }

        // ---- Confirmation modal wiring ------------------------------------

        // Acknowledgement checkbox → enable/disable "Approve anyway" button.
        if (el.confirmAckChk) {
            el.confirmAckChk.addEventListener('change', function () {
                if (el.confirmApproveBtn) {
                    el.confirmApproveBtn.disabled = !el.confirmAckChk.checked;
                }
            });
        }

        // "See all" expander click via keyboard (Space/Enter).
        if (el.confirmSeeAll) {
            el.confirmSeeAll.addEventListener('keydown', function (e) {
                if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    el.confirmSeeAll.click();
                }
            });
        }

        // Cancel → close confirmation modal only; review modal stays open.
        if (el.confirmCancelBtn) {
            el.confirmCancelBtn.addEventListener('click', function () {
                if (typeof window.$ === 'function') {
                    window.$('#hitl-confirm-approve-modal').modal('hide');
                } else if (el.confirmModal) {
                    el.confirmModal.classList.remove('show');
                    el.confirmModal.style.display = 'none';
                    var cbk2 = document.getElementById('hitl-confirm-backdrop');
                    if (cbk2 && cbk2.parentNode) cbk2.parentNode.removeChild(cbk2);
                }
                // Re-enable the Approve button in the review modal footer.
                if (el.approveBtn) {
                    el.approveBtn.disabled = false;
                    el.approveBtn.textContent = labels.approveBtn || 'Approve';
                }
                if (el.rejectBtn) el.rejectBtn.disabled = false;
                setFooterMsg('');
            });
        }

        // "Approve anyway" click → close confirm modal + POST approve.
        if (el.confirmApproveBtn) {
            el.confirmApproveBtn.addEventListener('click', function () {
                var docId        = el.confirmApproveBtn.getAttribute('data-doc-id') || '';
                var extractionId = el.confirmApproveBtn.getAttribute('data-extraction-id') || '';
                if (!docId || !extractionId) return;

                // Close the confirmation modal first.
                if (typeof window.$ === 'function') {
                    window.$('#hitl-confirm-approve-modal').modal('hide');
                } else if (el.confirmModal) {
                    el.confirmModal.classList.remove('show');
                    el.confirmModal.style.display = 'none';
                    var cbk3 = document.getElementById('hitl-confirm-backdrop');
                    if (cbk3 && cbk3.parentNode) cbk3.parentNode.removeChild(cbk3);
                }

                // Now trigger the actual approve POST.
                triggerApprove(docId, Number(extractionId));
            });
        }

        // Bootstrap 4 modal close — clean up backdrop (fallback path).
        var modalEl = document.getElementById('hitl-review-modal');
        if (modalEl) {
            modalEl.addEventListener('hidden.bs.modal', function () {
                var backdrop = document.getElementById('hitl-modal-backdrop');
                if (backdrop && backdrop.parentNode) {
                    backdrop.parentNode.removeChild(backdrop);
                }
            });
        }

    } // end bootstrap()

}());
