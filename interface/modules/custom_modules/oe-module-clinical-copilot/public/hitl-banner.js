/**
 * HITL extraction-quality banner — injected into the Documents module
 * preview pane.
 *
 * Strategy:
 *   1. Load on every authenticated page via ScriptFilterSubscriber.
 *   2. Observe the top document for the Documents module's preview area
 *      (`div.doc-doc-ls-8-preview`) and the `#file_preview` iframe
 *      that appears when a document is opened.
 *   3. When the iframe src changes (user clicks a document), read the
 *      doc_id from the URL and fetch the extraction summary.
 *   4. Render a yellow (partial miss) or red (full refusal) banner above
 *      the iframe. If the GET returns 404, stay silent — pre-P3 document.
 *   5. "Review what was missed" button lazy-loads the modal fragment and
 *      hands off to hitl-review.js.
 *   6. "Reprocess" button (on banner) → confirm → POST → refresh banner.
 *
 * PHI custody: this file never logs doc_id, patient_id, or any extracted
 * value. Error UI shows only structural messages.
 *
 * Namespace: all state is in the IIFE closure. One symbol on window:
 * `window.OE_COPILOT_HITL_CONFIG` (written by hitl-review-modal.php).
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    AgentForge Team
 * @copyright Copyright (c) 2026 AgentForge
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

(function () {
    'use strict';

    // ---- 0. Resolve the top-level document --------------------------------

    var hostWin = window;
    try {
        if (window.top && window.top.document && window.top !== window) {
            hostWin = window.top;
        }
    } catch (e) {
        hostWin = window;
    }
    var hostDoc = hostWin.document;

    // ---- 1. Module-level state -------------------------------------------

    /** Currently displayed doc_id (string). Null when no doc is open. */
    var currentDocId = null;

    /** Last fetched extraction data object. */
    var currentExtraction = null;

    /** True once the modal fragment has been fetched and injected. */
    var modalInjected = false;

    /** Whether a reprocess POST is in flight. */
    var reprocessing = false;

    // ---- 2. Derive module base URL from our own script src ----------------

    function resolveModuleBase() {
        var scripts = document.getElementsByTagName('script');
        for (var i = scripts.length - 1; i >= 0; i--) {
            var src = scripts[i].src || '';
            if (src.indexOf('hitl-banner.js') !== -1) {
                return src.replace(/\/hitl-banner\.js.*$/, '');
            }
        }
        return '/interface/modules/custom_modules/oe-module-clinical-copilot/public';
    }

    var MODULE_BASE = resolveModuleBase();

    // ---- 3. Utility helpers ----------------------------------------------

    function escapeHtml(v) {
        return String(v == null ? '' : v)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    /**
     * Extract doc_id from the retrieve URL that the Documents Angular
     * controller sets as the iframe src:
     *   .../Documents/retrieve/id/{doc_id}
     *   .../Documents/retrieve/id/{doc_id}/...
     *
     * Falls back to a ?doc_id= query param for alternative routing.
     */
    function docIdFromUrl(url) {
        if (!url) return null;
        var m = url.match(/\/retrieve\/id\/(\d+)/);
        if (m) return m[1];
        var q = url.match(/[?&]doc_id=(\d+)/);
        if (q) return q[1];
        return null;
    }

    // ---- 4. Banner DOM helpers -------------------------------------------

    var BANNER_ID = 'oe-copilot-hitl-banner';

    function removeBanner() {
        var existing = hostDoc.getElementById(BANNER_ID);
        if (existing && existing.parentNode) {
            existing.parentNode.removeChild(existing);
        }
    }

    /**
     * Inject (or replace) the banner above the #file_preview iframe.
     *
     * @param {string}      level           Bootstrap alert level class(es).
     * @param {string}      msg             Escaped message HTML.
     * @param {string|null} docId           doc_id this banner is for.
     * @param {boolean}     showReviewBtn
     * @param {boolean}     showReprocessBtn
     * @param {string}      [reviewBtnText] Optional override for review button label.
     */
    function renderBanner(level, msg, docId, showReviewBtn, showReprocessBtn, reviewBtnText) {
        removeBanner();

        var previewContainer = hostDoc.querySelector('div.doc-doc-ls-8-preview');
        if (!previewContainer) return;

        var iframe = hostDoc.getElementById('file_preview');
        if (!iframe) return;

        var banner = hostDoc.createElement('div');
        banner.id = BANNER_ID;
        banner.setAttribute('role', 'alert');
        banner.setAttribute('data-doc-id', escapeHtml(docId || ''));
        banner.className = 'alert alert-' + level + ' d-flex align-items-center mb-1 py-1 px-2';
        banner.style.cssText = [
            'font-size:13px',
            'border-radius:4px',
            'gap:8px',
            'flex-wrap:wrap',
        ].join(';');

        var msgSpan = hostDoc.createElement('span');
        msgSpan.className = 'hitl-banner-msg mr-auto';
        msgSpan.innerHTML = msg;
        banner.appendChild(msgSpan);

        if (showReviewBtn) {
            var reviewBtn = hostDoc.createElement('button');
            reviewBtn.type = 'button';
            reviewBtn.className = 'btn btn-sm btn-outline-dark py-0';
            reviewBtn.style.cssText = 'font-size:12px;white-space:nowrap;';
            reviewBtn.textContent = reviewBtnText || 'Review what was missed';
            reviewBtn.setAttribute('data-hitl-action', 'review');
            banner.appendChild(reviewBtn);
        }

        if (showReprocessBtn) {
            var repBtn = hostDoc.createElement('button');
            repBtn.type = 'button';
            repBtn.className = 'btn btn-sm btn-outline-dark py-0';
            repBtn.style.cssText = 'font-size:12px;white-space:nowrap;';
            repBtn.textContent = 'Reprocess';
            repBtn.setAttribute('data-hitl-action', 'reprocess');
            banner.appendChild(repBtn);
        }

        // Insert above the iframe — insertBefore relative to iframe's
        // position in previewContainer.
        previewContainer.insertBefore(banner, iframe);

        // Wire up button clicks.
        banner.addEventListener('click', function (e) {
            var btn = e.target.closest('[data-hitl-action]');
            if (!btn) return;
            var action = btn.getAttribute('data-hitl-action');
            if (action === 'review') {
                openReviewModal(docId);
            } else if (action === 'reprocess') {
                triggerReprocessFromBanner(docId);
            }
        });
    }

    // ---- 5. Fetch extraction summary ------------------------------------

    function fetchExtraction(docId, onSuccess, onNotFound, onError) {
        var url = MODULE_BASE + '/extraction_for_doc.php?doc_id=' + encodeURIComponent(docId);
        var xhr = new XMLHttpRequest();
        xhr.open('GET', url, true);
        xhr.setRequestHeader('Accept', 'application/json');
        xhr.onreadystatechange = function () {
            if (xhr.readyState !== 4) return;
            if (xhr.status === 404) {
                onNotFound();
                return;
            }
            if (xhr.status !== 200) {
                onError(xhr.status);
                return;
            }
            var data;
            try {
                data = JSON.parse(xhr.responseText);
            } catch (e) {
                onError('parse_error');
                return;
            }
            onSuccess(data);
        };
        xhr.send();
    }

    // ---- 6. Handle a document being opened/changed ----------------------

    function onDocumentOpened(docId) {
        if (docId === currentDocId) return;
        currentDocId = docId;
        currentExtraction = null;
        removeBanner();

        fetchExtraction(
            docId,
            function (data) {
                currentExtraction = data;
                renderExtractionBanner(docId, data);
            },
            function () {
                // 404 → no extraction rows — graceful no-op (pre-P3 doc).
                // No banner, no console error.
            },
            function () {
                // Network / server error — stay silent. Not a PHI concern
                // but showing a broken banner creates noise.
            }
        );
    }

    function renderExtractionBanner(docId, data) {
        if (!data) return;

        var status = data.status;
        var total = Number(data.total_fields) || 0;
        var stripped = Number(data.stripped_fields) || 0;

        // pending_review → blue alert-info: clinician must approve before round-trip.
        if (status === 'pending_review') {
            renderBanner(
                'info',
                '<strong>Review before saving.</strong> This extraction has not been written to the chart yet.',
                docId,
                true,               // show review button
                false,              // no Reprocess button on pending_review
                'Review extraction' // button label per spec
            );
            return;
        }

        // approved → quiet green alert-success: round-trip already fired.
        if (status === 'approved') {
            renderBanner(
                'success hitl-banner--approved',
                'Extraction approved and written to chart.',
                docId,
                false,  // no review button
                false   // no reprocess button
            );
            return;
        }

        if (status === 'refused' || status === 'error') {
            renderBanner(
                'danger',
                'This extraction was refused (all 3 attempts failed).',
                docId,
                true,   // show Review attempts
                true    // show Reprocess
            );
            return;
        }

        if (status === 'ok' && stripped > 0) {
            var msg = 'This extraction missed <strong>' + escapeHtml(String(stripped)) +
                      '</strong> of <strong>' + escapeHtml(String(total)) +
                      '</strong> field' + (total === 1 ? '' : 's') + '.';
            renderBanner('warning', msg, docId, true, true);
            return;
        }

        // status === 'ok' and stripped === 0 → no banner needed.
    }

    // ---- 7. Lazy-load the review modal ----------------------------------

    function openReviewModal(docId) {
        if (!docId) return;

        function showModal(extraction) {
            // Hand off to hitl-review.js. The modal is already injected;
            // hitl-review.js listens for the custom event.
            var event;
            try {
                event = new CustomEvent('oe-copilot-hitl/open-modal', {
                    bubbles: true,
                    detail: { docId: docId, extraction: extraction }
                });
            } catch (e) {
                // IE11 fallback (OpenEMR still supports it in some configs)
                event = hostDoc.createEvent('CustomEvent');
                event.initCustomEvent('oe-copilot-hitl/open-modal', true, false, {
                    docId: docId,
                    extraction: extraction
                });
            }
            hostDoc.dispatchEvent(event);
        }

        if (modalInjected) {
            showModal(currentExtraction);
            return;
        }

        // First click: fetch the modal fragment and inject it.
        var url = MODULE_BASE + '/hitl-review-modal.php';
        var xhr = new XMLHttpRequest();
        xhr.open('GET', url, true);
        xhr.setRequestHeader('Accept', 'text/html');
        xhr.onreadystatechange = function () {
            if (xhr.readyState !== 4) return;
            if (xhr.status === 403) {
                // Auth failure — structural only.
                renderBanner('danger',
                    'Access denied for HITL review.',
                    docId, false, false);
                return;
            }
            if (xhr.status !== 200) {
                renderBanner('danger',
                    'Could not load review panel.',
                    docId, false, false);
                return;
            }
            var container = hostDoc.createElement('div');
            container.id = 'oe-copilot-hitl-modal-root';
            container.innerHTML = xhr.responseText;
            hostDoc.body.appendChild(container);
            modalInjected = true;

            // Give injected scripts time to execute, then fire the open.
            // requestAnimationFrame gives the browser one paint cycle.
            hostWin.requestAnimationFrame(function () {
                showModal(currentExtraction);
            });
        };
        xhr.send();
    }

    // ---- 8. Reprocess from banner (without opening modal) ---------------

    function triggerReprocessFromBanner(docId) {
        if (reprocessing || !docId) return;
        if (!currentExtraction) return;

        var confirmed = hostWin.confirm(
            'Reprocess this document? This will consume API credits.'
        );
        if (!confirmed) return;

        var cfg = hostWin.OE_COPILOT_HITL_CONFIG || {};
        var csrfToken = cfg.csrfToken || '';
        var reprocessUrl = cfg.reprocessUrl || (MODULE_BASE + '/reprocess.php');

        reprocessing = true;

        // Disable both buttons while in-flight.
        var banner = hostDoc.getElementById(BANNER_ID);
        if (banner) {
            var btns = banner.querySelectorAll('[data-hitl-action]');
            for (var i = 0; i < btns.length; i++) {
                btns[i].disabled = true;
            }
            var msgEl = banner.querySelector('.hitl-banner-msg');
            if (msgEl) {
                msgEl.textContent = 'Reprocessing… (may take up to 30s)';
            }
        }

        var xhr = new XMLHttpRequest();
        xhr.open('POST', reprocessUrl, true);
        xhr.setRequestHeader('Content-Type', 'application/json');
        xhr.setRequestHeader('X-CSRF-Token', csrfToken);
        xhr.onreadystatechange = function () {
            if (xhr.readyState !== 4) return;
            reprocessing = false;

            if (xhr.status === 200) {
                // Re-fetch extraction summary and redraw banner.
                currentDocId = null; // force re-fetch
                onDocumentOpened(docId);
            } else {
                // Structural error — never echo patient data.
                var errMsg = 'Reprocess failed.';
                try {
                    var body = JSON.parse(xhr.responseText);
                    if (body.error === 'cost_ceiling_exceeded') {
                        errMsg = 'Reprocess refused: cost ceiling exceeded.';
                    } else if (body.error === 'extraction_low_grounding') {
                        errMsg = 'Reprocess refused: extraction low grounding.';
                    }
                } catch (e2) { /* ignore parse errors */ }
                renderBanner('danger', escapeHtml(errMsg), docId, false, false);
            }
        };
        xhr.send(JSON.stringify({ extraction_id: currentExtraction.extraction_id }));
    }

    // ---- 9. Observe the Documents module for iframe src changes ---------

    /**
     * Wire up a MutationObserver on the preview pane so we detect when
     * the Angular controller sets a new `src` on `#file_preview`.
     *
     * We also handle the initial state where the iframe already has a src
     * (e.g. page loaded with a hash-selected document).
     */
    function watchPreviewPane(previewEl) {
        var iframe = previewEl.querySelector('#file_preview');

        function checkIframeSrc(el) {
            if (!el) return;
            var src = el.getAttribute('src') || '';
            var docId = docIdFromUrl(src);
            if (docId) onDocumentOpened(docId);
        }

        // Observe src attribute changes on the iframe itself.
        if (iframe) {
            checkIframeSrc(iframe);
            var attrObserver = new MutationObserver(function (mutations) {
                for (var i = 0; i < mutations.length; i++) {
                    if (mutations[i].attributeName === 'src') {
                        checkIframeSrc(mutations[i].target);
                    }
                }
            });
            attrObserver.observe(iframe, { attributes: true, attributeFilter: ['src'] });
        }

        // Also observe childList changes in the preview container in case
        // Angular re-creates the iframe element entirely.
        var childObserver = new MutationObserver(function (mutations) {
            for (var i = 0; i < mutations.length; i++) {
                var addedNodes = mutations[i].addedNodes;
                for (var j = 0; j < addedNodes.length; j++) {
                    var node = addedNodes[j];
                    if (node.nodeType !== 1) continue;
                    if (node.id === 'file_preview') {
                        checkIframeSrc(node);
                        // Re-attach attribute observer to new iframe.
                        attrObserver.observe(node, { attributes: true, attributeFilter: ['src'] });
                    }
                }
            }
        });
        childObserver.observe(previewEl, { childList: true, subtree: true });
    }

    /**
     * The Documents module is loaded inside an iframe on most chart pages.
     * We must detect when `div.doc-doc-ls-8-preview` appears in any
     * same-origin frame, not just in `hostDoc`.
     *
     * Strategy: watch the host document for iframes being added; for each
     * same-origin iframe, check for the Documents module sentinel after load.
     */
    function attachToDocumentsModule(win) {
        var doc = win.document;
        var pane = doc.querySelector('div.doc-doc-ls-8-preview');
        if (pane) {
            watchPreviewPane(pane);
        }

        // MutationObserver to handle the pane appearing later (Angular
        // bootstrap can lag behind DOMContentLoaded).
        if (!doc.body) return;
        var bodyObserver = new MutationObserver(function () {
            var p = doc.querySelector('div.doc-doc-ls-8-preview');
            if (p && !p.__hitlWatched) {
                p.__hitlWatched = true;
                watchPreviewPane(p);
            }
        });
        bodyObserver.observe(doc.body, { childList: true, subtree: true });
    }

    function tryAttachToFrame(iframeEl) {
        // Best-effort; cross-origin frames will throw.
        try {
            var fw = iframeEl.contentWindow;
            if (!fw || !fw.document) return;
            // Wait for the frame to finish loading if needed.
            if (iframeEl.contentDocument && iframeEl.contentDocument.readyState === 'complete') {
                attachToDocumentsModule(fw);
            } else {
                iframeEl.addEventListener('load', function () {
                    try { attachToDocumentsModule(iframeEl.contentWindow); } catch (e2) { /* cross-origin */ }
                });
            }
        } catch (e) {
            // Cross-origin; not the Documents module pane.
        }
    }

    function scanFrames(root) {
        var iframes = root.querySelectorAll('iframe');
        for (var i = 0; i < iframes.length; i++) {
            tryAttachToFrame(iframes[i]);
        }
    }

    // ---- 10. Bootstrap on DOMContentLoaded/ready -------------------------

    function init() {
        // Try the current document first (handles the case where this
        // script runs directly inside the Documents module frame).
        attachToDocumentsModule(hostWin);

        // Also scan all child iframes of the top document.
        scanFrames(hostDoc);

        // Watch for new iframes being added (e.g. when the user navigates
        // to the Documents tab for the first time after loading the chart).
        var topBodyObserver = new MutationObserver(function (mutations) {
            for (var i = 0; i < mutations.length; i++) {
                var added = mutations[i].addedNodes;
                for (var j = 0; j < added.length; j++) {
                    var n = added[j];
                    if (n.nodeType !== 1) continue;
                    if (n.tagName === 'IFRAME') {
                        tryAttachToFrame(n);
                    } else if (n.querySelectorAll) {
                        var nested = n.querySelectorAll('iframe');
                        for (var k = 0; k < nested.length; k++) {
                            tryAttachToFrame(nested[k]);
                        }
                    }
                }
            }
        });
        if (hostDoc.body) {
            topBodyObserver.observe(hostDoc.body, { childList: true, subtree: true });
        }
    }

    // ---- 11. Listen for post-approve/reject banner refresh ---------------

    // hitl-review.js dispatches `oe-copilot-hitl/extraction-updated` after a
    // successful approve or reject POST.  Force a re-fetch so the banner
    // transitions to the new status (approved → green, rejected → silent).
    hostDoc.addEventListener('oe-copilot-hitl/extraction-updated', function (e) {
        var detail = (e && e.detail) ? e.detail : {};
        var updatedDocId = detail.docId || null;
        if (!updatedDocId) return;
        // Force re-fetch regardless of currentDocId cache.
        currentDocId = null;
        onDocumentOpened(updatedDocId);
    });

    if (hostDoc.readyState === 'loading') {
        hostDoc.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

}());
