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

    // Note: ScriptFilterSubscriber may inject this script tag into multiple
    // OpenEMR pages (top window + several child iframes — main_info,
    // demographics, encounters, etc.), so the IIFE may run N times. We do
    // NOT guard at IIFE entry because each instance's init() walks frames
    // independently and discovers the document iframe (which lives in only
    // ONE frame). Instead, we make removeBanner() sweep across ALL same-
    // origin frames so the last-rendering instance dedupes the rest.

    // ---- 1. Module-level state -------------------------------------------

    /** Currently displayed doc_id (string). Null when no doc is open. */
    var currentDocId = null;

    /** Last fetched extraction data object. */
    var currentExtraction = null;

    /** True once the modal fragment has been fetched and injected. */
    var modalInjected = false;

    /** Whether a reprocess POST is in flight. */
    var reprocessing = false;

    /** Reference to the currently-rendered banner element (may live in any
     *  same-origin frame, so we track by reference rather than by id-lookup
     *  on hostDoc). Null when no banner is rendered. */
    var bannerEl = null;

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
        // Pattern A: Angular Documents controller iframe src — .../retrieve/id/{N}
        var m = url.match(/\/retrieve\/id\/(\d+)/);
        if (m) return m[1];
        // Pattern B: legacy ?doc_id={N} query param.
        var q = url.match(/[?&]doc_id=(\d+)/);
        if (q) return q[1];
        // Pattern C: OpenEMR Documents-controller URL — controller.php?document&retrieve&document_id={N}
        // (the `_id` and `id` distinguish from the path segment `&retrieve&` so we don't false-match).
        var d = url.match(/[?&]document_id=(\d+)/);
        if (d) return d[1];
        return null;
    }

    // ---- 4. Banner DOM helpers -------------------------------------------

    var BANNER_ID = 'oe-copilot-hitl-banner';

    /**
     * Locate the document-viewer's container + iframe across same-origin
     * frames. Tries the Angular Documents module pane first, then falls
     * back to the legacy controller.php viewer that older OpenEMR builds
     * (and the patient_file/summary/demographics.php documents tab) use.
     *
     * Returns { ownerDoc, container, iframe } or null.
     *
     * The ownerDoc is the document the iframe belongs to — banner DOM
     * elements MUST be created via that doc (not hostDoc) or the browser
     * will throw cross-document insert errors / styling won't apply.
     */
    function findInjectionPoint() {
        var found = null;

        function checkDoc(doc) {
            if (found) return;
            // Style A: Angular Documents module pane.
            var pane = doc.querySelector('div.doc-doc-ls-8-preview');
            var ngIframe = doc.getElementById('file_preview');
            if (pane && ngIframe && pane.contains(ngIframe)) {
                found = { ownerDoc: doc, container: pane, iframe: ngIframe };
                return;
            }
            // Style B: legacy controller.php viewer — <tr id="DocContents">
            // wraps a single <td> containing the document iframe.
            var legacyContents = doc.getElementById('DocContents');
            if (legacyContents) {
                var iframes = legacyContents.querySelectorAll('iframe');
                for (var i = 0; i < iframes.length; i++) {
                    var f = iframes[i];
                    var src = f.getAttribute('src') || '';
                    if (src.indexOf('controller.php') !== -1 && src.indexOf('document_id=') !== -1) {
                        // Inject above the iframe inside its containing <td>.
                        found = { ownerDoc: doc, container: f.parentElement, iframe: f };
                        return;
                    }
                }
            }
        }

        function walk(win) {
            if (found) return;
            try {
                checkDoc(win.document);
                var iframes = win.document.querySelectorAll('iframe');
                for (var i = 0; i < iframes.length && !found; i++) {
                    try {
                        if (iframes[i].contentWindow) {
                            walk(iframes[i].contentWindow);
                        }
                    } catch (e) { /* cross-origin frame — skip */ }
                }
            } catch (e) { /* cross-origin doc — skip */ }
        }

        walk(hostWin);
        return found;
    }

    function removeBanner() {
        if (bannerEl && bannerEl.parentNode) {
            bannerEl.parentNode.removeChild(bannerEl);
        }
        bannerEl = null;

        // Sweep ALL same-origin frames for orphan banners. Required when
        // multiple IIFE instances inject independently (each closure has
        // its own bannerEl ref and doesn't see the others).
        function sweep(win) {
            try {
                var doc = win.document;
                var orphans = doc.querySelectorAll('#' + BANNER_ID);
                for (var k = 0; k < orphans.length; k++) {
                    if (orphans[k].parentNode) {
                        orphans[k].parentNode.removeChild(orphans[k]);
                    }
                }
                var iframes = doc.querySelectorAll('iframe');
                for (var i = 0; i < iframes.length; i++) {
                    try {
                        if (iframes[i].contentWindow) sweep(iframes[i].contentWindow);
                    } catch (e) { /* cross-origin */ }
                }
            } catch (e) { /* cross-origin */ }
        }
        sweep(hostWin);
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

        // Find the document viewer in any same-origin frame (Angular pane
        // OR legacy controller.php viewer). The banner element is created
        // via the iframe's ownerDoc so it lives in the right document.
        var inj = findInjectionPoint();
        if (!inj) return;

        var ownerDoc = inj.ownerDoc;
        var container = inj.container;
        var iframe = inj.iframe;

        var banner = ownerDoc.createElement('div');
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

        var msgSpan = ownerDoc.createElement('span');
        msgSpan.className = 'hitl-banner-msg mr-auto';
        msgSpan.innerHTML = msg;
        banner.appendChild(msgSpan);

        if (showReviewBtn) {
            var reviewBtn = ownerDoc.createElement('button');
            reviewBtn.type = 'button';
            reviewBtn.className = 'btn btn-sm btn-outline-dark py-0';
            reviewBtn.style.cssText = 'font-size:12px;white-space:nowrap;';
            reviewBtn.textContent = reviewBtnText || 'Review what was missed';
            reviewBtn.setAttribute('data-hitl-action', 'review');
            banner.appendChild(reviewBtn);
        }

        if (showReprocessBtn) {
            var repBtn = ownerDoc.createElement('button');
            repBtn.type = 'button';
            repBtn.className = 'btn btn-sm btn-outline-dark py-0';
            repBtn.style.cssText = 'font-size:12px;white-space:nowrap;';
            repBtn.textContent = 'Reprocess';
            repBtn.setAttribute('data-hitl-action', 'reprocess');
            banner.appendChild(repBtn);
        }

        // Insert above the iframe — insertBefore is valid whether the
        // container is a <div> (Angular pane) or a <td> (legacy viewer);
        // both can contain a <div> child legally.
        container.insertBefore(banner, iframe);
        bannerEl = banner;

        // Wire up button clicks.
        banner.addEventListener('click', function (e) {
            // TEMPORARY: diagnostic logging — remove after demo works.
            console.log('[hitl-banner] click handler fired. target:', e.target.tagName);
            var btn = e.target.closest('[data-hitl-action]');
            if (!btn) {
                console.log('[hitl-banner] click target has no data-hitl-action ancestor; ignoring');
                return;
            }
            var action = btn.getAttribute('data-hitl-action');
            console.log('[hitl-banner] action=' + action + ' docId=' + docId + ' modalInjected=' + modalInjected + ' currentExtraction=' + (currentExtraction ? 'set' : 'null'));
            if (action === 'review') {
                console.log('[hitl-banner] calling openReviewModal');
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

    // ---- 6.5 Dynamic sidecar positioning (peer with Co-Pilot drawer) ----
    //
    // Reads the live Co-Pilot drawer's bounding rect and positions the
    // sidecar adjacent to it (left of drawer when open, right edge of
    // viewport when closed). Run on modal-show, on window resize, and
    // on any mutation to the drawer's aria-hidden / style / class.
    var sidecarPositionWatcher = null;

    function positionSidecar() {
        var modal = hostDoc.getElementById('hitl-review-modal');
        if (!modal) return;
        var dialog = modal.querySelector('.modal-dialog');
        if (!dialog) return;

        var drawer = hostDoc.getElementById('oe-copilot-drawer');
        var vw = hostWin.innerWidth || hostDoc.documentElement.clientWidth;
        var vh = hostWin.innerHeight || hostDoc.documentElement.clientHeight;

        if (drawer) {
            var rect = drawer.getBoundingClientRect();
            var hidden = drawer.getAttribute('aria-hidden') === 'true';
            var open = !hidden && rect.width > 10 && rect.left < vw;
            if (open) {
                // Drawer open: dock to LEFT of drawer, match top/bottom.
                dialog.style.top = rect.top + 'px';
                dialog.style.right = (vw - rect.left) + 'px';
                dialog.style.bottom = (vh - rect.bottom) + 'px';
                dialog.style.height = rect.height + 'px';
                dialog.style.maxHeight = rect.height + 'px';
                return;
            }
            // Drawer closed: dock to right edge but match drawer's vertical span.
            // The drawer keeps its top/bottom even when collapsed (just width 0).
            // Read its parent or fall back to body offsets.
            dialog.style.top = (rect.top > 0 ? rect.top : 90) + 'px';
            dialog.style.right = '0';
            dialog.style.bottom = (rect.bottom > 0 ? (vh - rect.bottom) : 0) + 'px';
            dialog.style.height = 'auto';
            dialog.style.maxHeight = (rect.bottom > 0 ? (rect.bottom - rect.top) : (vh - 90)) + 'px';
            return;
        }
        // No drawer at all — dock right with conservative defaults.
        dialog.style.top = '90px';
        dialog.style.right = '0';
        dialog.style.bottom = '0';
    }

    function watchDrawerForRepositioning() {
        if (sidecarPositionWatcher) return; // idempotent
        var drawer = hostDoc.getElementById('oe-copilot-drawer');
        if (!drawer) return;
        sidecarPositionWatcher = new MutationObserver(function () {
            positionSidecar();
        });
        sidecarPositionWatcher.observe(drawer, {
            attributes: true,
            attributeFilter: ['aria-hidden', 'style', 'class']
        });
        hostWin.addEventListener('resize', positionSidecar);
    }

    // ---- 7. Lazy-load the review modal ----------------------------------

    function openReviewModal(docId) {
        // TEMPORARY: diagnostic logging.
        console.log('[hitl-banner] openReviewModal called. docId=' + docId + ' modalInjected=' + modalInjected);
        if (!docId) {
            console.log('[hitl-banner] openReviewModal: no docId, returning');
            return;
        }

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
            console.log('[hitl-banner] modal already injected; calling showModal directly (no fetch)');
            showModal(currentExtraction);
            // Reposition each open since drawer state may have changed.
            hostWin.requestAnimationFrame(function () {
                positionSidecar();
                setTimeout(positionSidecar, 100);
                setTimeout(positionSidecar, 400);
            });
            return;
        }

        // First click: fetch the modal fragment and inject it.
        var url = MODULE_BASE + '/hitl-review-modal.php';
        console.log('[hitl-banner] fetching modal from:', url);
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

            // Browser quirk: setting innerHTML parses <script> tags into
            // the DOM but does NOT execute them. We must re-create each
            // script element so the browser runs it. This is what sets
            // window.OE_COPILOT_HITL_CONFIG (inline) and loads hitl-review.js
            // (external) which renders the modal body.
            //
            // Chain external scripts via onload so they execute in document
            // order: pdf.min.js → inline config setter → hitl-review.js.
            var scripts = Array.prototype.slice.call(container.querySelectorAll('script'));

            function loadNext(i, done) {
                if (i >= scripts.length) { done(); return; }
                var oldScript = scripts[i];
                var newScript = hostDoc.createElement('script');
                for (var a = 0; a < oldScript.attributes.length; a++) {
                    var attr = oldScript.attributes[a];
                    newScript.setAttribute(attr.name, attr.value);
                }
                newScript.textContent = oldScript.textContent;

                var src = oldScript.getAttribute('src');
                if (src) {
                    // External script: wait for load before continuing chain.
                    newScript.async = false;
                    newScript.onload = function () { loadNext(i + 1, done); };
                    newScript.onerror = function () {
                        console.log('[hitl-banner] script failed to load:', src);
                        loadNext(i + 1, done); // keep going; modal may still partial-render
                    };
                    if (oldScript.parentNode) {
                        oldScript.parentNode.replaceChild(newScript, oldScript);
                    }
                } else {
                    // Inline script: executes synchronously on insertion.
                    if (oldScript.parentNode) {
                        oldScript.parentNode.replaceChild(newScript, oldScript);
                    }
                    loadNext(i + 1, done);
                }
            }

            loadNext(0, function () {
                modalInjected = true;
                console.log('[hitl-banner] modal scripts all loaded; dispatching open event');
                showModal(currentExtraction);
                // Position the sidecar relative to the Co-Pilot drawer
                // and wire up MutationObserver for live repositioning.
                hostWin.requestAnimationFrame(function () {
                    positionSidecar();
                    watchDrawerForRepositioning();
                    // Bootstrap modal animation can take ~400ms; reposition
                    // after that to catch the final layout.
                    setTimeout(positionSidecar, 100);
                    setTimeout(positionSidecar, 400);
                });
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

        // Style A: Angular Documents module pane.
        var pane = doc.querySelector('div.doc-doc-ls-8-preview');
        if (pane) {
            watchPreviewPane(pane);
        }

        // Style B: legacy controller.php viewer — check immediately.
        checkLegacyViewer(doc);

        // MutationObserver to handle either pane appearing later (Angular
        // bootstrap or the documents tab being clicked).
        if (!doc.body) return;
        var bodyObserver = new MutationObserver(function () {
            var p = doc.querySelector('div.doc-doc-ls-8-preview');
            if (p && !p.__hitlWatched) {
                p.__hitlWatched = true;
                watchPreviewPane(p);
            }
            // Legacy viewer: re-check on every mutation since DocContents
            // may be re-rendered when the user clicks a different doc.
            checkLegacyViewer(doc);
        });
        bodyObserver.observe(doc.body, { childList: true, subtree: true });
    }

    /**
     * Detect the legacy <tr id="DocContents"> viewer in the given doc and,
     * if present, fire onDocumentOpened with its current doc_id and watch
     * the inner iframe for src changes.
     */
    function checkLegacyViewer(doc) {
        var contents = doc.getElementById('DocContents');
        if (!contents) return;
        var iframes = contents.querySelectorAll('iframe');
        for (var i = 0; i < iframes.length; i++) {
            var f = iframes[i];
            var src = f.getAttribute('src') || '';
            if (src.indexOf('controller.php') === -1) continue;
            if (src.indexOf('document_id=') === -1) continue;

            var initialDocId = docIdFromUrl(src);
            if (initialDocId) onDocumentOpened(initialDocId);

            // Watch for src changes on this iframe (user navigating to
            // a different doc within the legacy viewer).
            if (!f.__hitlWatched) {
                f.__hitlWatched = true;
                var attrObs = new MutationObserver(
                    (function (frame) {
                        return function (mutations) {
                            for (var j = 0; j < mutations.length; j++) {
                                if (mutations[j].attributeName !== 'src') continue;
                                var newSrc = frame.getAttribute('src') || '';
                                var newId = docIdFromUrl(newSrc);
                                if (newId && newId !== currentDocId) {
                                    onDocumentOpened(newId);
                                }
                            }
                        };
                    })(f)
                );
                attrObs.observe(f, { attributes: true, attributeFilter: ['src'] });
            }
            return; // first matching iframe wins
        }
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
