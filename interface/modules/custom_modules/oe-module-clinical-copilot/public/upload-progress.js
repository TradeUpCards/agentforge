/**
 * Upload-progress UX for OpenEMR Documents-page auto-extract uploads.
 *
 * When a clinician uploads a document under an "auto-extract" category
 * — Lab Result (auto-extract, parent_id=9000) or Intake Form
 * (auto-extract, parent_id=9001) — the form POST triggers a synchronous
 * agent extraction taking 30–90 seconds. Without a UI cue the page sits
 * frozen and users re-click thinking it failed. This script attaches to
 * the upload form and:
 *
 *   - Disables the submit button on submit
 *   - Replaces the button label with "Uploading…"
 *   - Inserts a spinner + phase-progress text node next to the button
 *   - Drives the phase text on a 1s interval based on elapsed time:
 *       0–3s:    "Uploading document to OpenEMR…"
 *       3–35s:   "Reading document layout (Docling)…"
 *       35–90s:  "Extracting clinical data with Co-Pilot…"
 *       90s+:    "Almost there — finalizing chart records…"
 *
 *   Phase timings calibrated against real Langfuse-traced agent latencies
 *   (Docling 30-60s, Haiku schema extraction 5-20s, round-trip <1s).
 *
 * Scope filter: only auto-extract category uploads. Non-extraction
 * document uploads keep OpenEMR's default UI so users don't see a
 * misleading "Extracting clinical data…" message for a routine PDF.
 *
 * Form-submit cycle: this script does NOT preventDefault. OpenEMR's
 * normal POST cycle handles submission and redirects to the post-upload
 * Documents page on success — that page reload restores the default UI
 * naturally, so we don't need to track the form's response.
 *
 * Frame-context handling: OpenEMR's Documents page sometimes loads
 * inside an iframe. We walk same-origin frames to find the form, mirror-
 * ing the pattern in hitl-banner.js.
 *
 * No-PHI discipline: this file never logs document names, patient ids,
 * or extracted values. Phase strings are static.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    AgentForge Team
 * @copyright Copyright (c) 2026 AgentForge
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

(function () {
    'use strict';

    var AUTO_EXTRACT_CATEGORY_IDS = ['9000', '9001'];
    var WIDGET_ID = 'oe-copilot-upload-progress';
    var ATTACHED_FLAG = '__oeCopilotUploadProgressAttached';

    /** Phase boundaries — sorted by `atMs` ascending. */
    var PHASES = [
        { atMs: 0,     msg: 'Uploading document to OpenEMR…' },
        { atMs: 3000,  msg: 'Reading document layout (Docling)…' },
        { atMs: 35000, msg: 'Extracting clinical data with Co-Pilot…' },
        { atMs: 90000, msg: 'Almost there — finalizing chart records…' },
    ];

    // ---- 1. URL gate ------------------------------------------------------

    /**
     * Match the OpenEMR Documents-page upload URL ONLY when the
     * destination category is one of the auto-extract categories.
     */
    function urlIsAutoExtractUpload(url) {
        if (!url) return false;
        if (url.indexOf('controller.php') === -1) return false;
        // The Documents controller routes via query-string segments
        // joined with `&` rather than a single key=value pair, e.g.
        // `controller.php?document&upload&patient_id=…&parent_id=…`.
        if (url.indexOf('document') === -1) return false;
        if (url.indexOf('upload') === -1) return false;
        var m = url.match(/[?&]parent_id=(\d+)/);
        if (!m) return false;
        return AUTO_EXTRACT_CATEGORY_IDS.indexOf(m[1]) !== -1;
    }

    function phaseFor(elapsedMs) {
        var current = PHASES[0].msg;
        for (var i = 0; i < PHASES.length; i++) {
            if (elapsedMs >= PHASES[i].atMs) current = PHASES[i].msg;
        }
        return current;
    }

    // ---- 2. Form attach ---------------------------------------------------

    /**
     * Wire up the submit handler on a found upload form. Idempotent:
     * the per-form `__oeCopilotUploadProgressAttached` flag prevents
     * double-attachment when MutationObserver fires multiple times.
     */
    function attachToForm(doc, win, form) {
        if (form[ATTACHED_FLAG]) return;
        form[ATTACHED_FLAG] = true;

        var submitBtn = form.querySelector(
            'input[type="submit"], button[type="submit"]'
        );
        if (!submitBtn) return;

        form.addEventListener('submit', function () {
            // Do NOT preventDefault — let OpenEMR's normal POST proceed.

            // Disable the submit so re-clicks are blocked.
            submitBtn.disabled = true;
            submitBtn.style.opacity = '0.6';
            submitBtn.style.pointerEvents = 'none';

            // Replace the submit's label so the change is visible on the
            // button itself even if the phase widget falls off-screen.
            if (submitBtn.tagName === 'INPUT') {
                submitBtn.value = 'Uploading…';
            } else {
                submitBtn.textContent = 'Uploading…';
            }

            // Drop any prior widget (defensive — only happens if the
            // form was submitted twice somehow).
            var existing = doc.getElementById(WIDGET_ID);
            if (existing && existing.parentNode) {
                existing.parentNode.removeChild(existing);
            }

            // Build the spinner + phase-text widget.
            var widget = doc.createElement('span');
            widget.id = WIDGET_ID;
            widget.setAttribute('role', 'status');
            widget.setAttribute('aria-live', 'polite');
            widget.style.cssText = [
                'display:inline-flex',
                'align-items:center',
                'gap:6px',
                'margin-left:12px',
                'font-size:13px',
                'color:#555',
            ].join(';');

            var spinner = doc.createElement('i');
            spinner.className = 'fa fa-spinner fa-spin';
            spinner.setAttribute('aria-hidden', 'true');
            widget.appendChild(spinner);

            var phaseText = doc.createElement('span');
            phaseText.className = 'oe-copilot-upload-phase-text';
            phaseText.textContent = phaseFor(0);
            widget.appendChild(phaseText);

            // Insert directly after the submit button.
            var parent = submitBtn.parentNode;
            if (parent) {
                if (submitBtn.nextSibling) {
                    parent.insertBefore(widget, submitBtn.nextSibling);
                } else {
                    parent.appendChild(widget);
                }
            }

            // Drive the phase text on a 1s interval. The page reload
            // after the POST tears this down naturally — no manual
            // cleanup needed.
            var startMs = Date.now();
            var lastMsg = phaseText.textContent;
            win.setInterval(function () {
                var elapsed = Date.now() - startMs;
                var msg = phaseFor(elapsed);
                if (msg !== lastMsg) {
                    phaseText.textContent = msg;
                    lastMsg = msg;
                }
            }, 1000);
        });
    }

    /**
     * Find the main upload form in a document. The general upload
     * template renders two `enctype="multipart/form-data"` forms — the
     * primary (this one) and the dropzone fallback. The dropzone form's
     * action points at `library/ajax/upload.php`, so we filter that out.
     */
    function findUploadForm(doc) {
        var forms = doc.querySelectorAll('form[enctype="multipart/form-data"]');
        for (var i = 0; i < forms.length; i++) {
            var f = forms[i];
            var action = f.getAttribute('action') || '';
            if (action.indexOf('library/ajax/upload.php') !== -1) continue;
            if (f.classList && f.classList.contains('dropzone')) continue;
            if (!f.querySelector('input[type="file"][name="file[]"]')) continue;
            return f;
        }
        return null;
    }

    // ---- 3. Frame walking -------------------------------------------------

    function attachIfRelevant(win) {
        try {
            var doc = win.document;
            if (!doc) return;
            var url = win.location ? win.location.href : '';
            if (!urlIsAutoExtractUpload(url)) return;

            var form = findUploadForm(doc);
            if (form) {
                attachToForm(doc, win, form);
                return;
            }

            // Form not in DOM yet — observe.
            if (!doc.body) return;
            var observer = new MutationObserver(function () {
                var f = findUploadForm(doc);
                if (f) {
                    observer.disconnect();
                    attachToForm(doc, win, f);
                }
            });
            observer.observe(doc.body, { childList: true, subtree: true });
        } catch (e) {
            // Cross-origin frame or document access denied — skip.
        }
    }

    function tryAttachToFrame(iframeEl) {
        try {
            var fw = iframeEl.contentWindow;
            if (!fw) return;
            if (iframeEl.contentDocument
                && iframeEl.contentDocument.readyState === 'complete') {
                attachIfRelevant(fw);
            } else {
                iframeEl.addEventListener('load', function () {
                    try {
                        attachIfRelevant(iframeEl.contentWindow);
                    } catch (e2) {
                        // Cross-origin; ignore.
                    }
                });
            }
        } catch (e) {
            // Cross-origin; ignore.
        }
    }

    // ---- 4. Bootstrap -----------------------------------------------------

    function init() {
        var hostWin = window;
        try {
            if (window.top && window.top.document && window.top !== window) {
                hostWin = window.top;
            }
        } catch (e) {
            hostWin = window;
        }

        // Try host first, then current frame.
        attachIfRelevant(hostWin);
        if (hostWin !== window) {
            attachIfRelevant(window);
        }

        // Walk same-origin iframes the host can see.
        var hostDoc = hostWin.document || document;
        var iframes = hostDoc.querySelectorAll('iframe');
        for (var i = 0; i < iframes.length; i++) {
            tryAttachToFrame(iframes[i]);
        }

        // Watch for new iframes (Documents tab opened after page load).
        if (hostDoc.body) {
            var bodyObserver = new MutationObserver(function (mutations) {
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
            bodyObserver.observe(hostDoc.body, { childList: true, subtree: true });
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
}());
