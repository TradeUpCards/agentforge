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
 *   - Text snippets from docling_blocks are ≤80 chars, truncated byte-safe.
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
            modal:         document.getElementById('hitl-review-modal'),
            hdrDocType:    document.getElementById('hitl-hdr-doc-type'),
            hdrAttempt:    document.getElementById('hitl-hdr-attempt'),
            hdrModel:      document.getElementById('hitl-hdr-model'),
            pdfPane:       document.getElementById('hitl-pdf-pane'),
            pdfLoading:    document.getElementById('hitl-pdf-loading'),
            listCaptured:  document.getElementById('hitl-list-captured'),
            listMissed:    document.getElementById('hitl-list-missed'),
            listPossibly:  document.getElementById('hitl-list-possibly'),
            cntCaptured:   document.getElementById('hitl-count-captured'),
            cntMissed:     document.getElementById('hitl-count-missed'),
            cntPossibly:   document.getElementById('hitl-count-possibly'),
            footerMsg:     document.getElementById('hitl-footer-msg'),
            reprocessBtn:  document.getElementById('hitl-reprocess-btn'),
        };

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

        // ---- 6. Render right pane ----------------------------------------

        /**
         * Build a field row element.
         *
         * @param {'verified'|'stripped'} status
         * @param {string}  fieldPath
         * @param {string|null}  value       ONLY passed for verified rows.
         * @param {string|null}  blockId
         * @param {string|null}  reason
         * @returns {HTMLElement}
         */
        function buildFieldRow(status, fieldPath, value, blockId, reason) {
            var row = document.createElement('div');
            row.className = 'hitl-field-row';
            row.setAttribute('role', 'listitem');
            row.setAttribute('data-block-id', blockId || '');
            row.setAttribute('data-status', status);

            var pathEl = document.createElement('span');
            pathEl.className = 'hitl-field-path';
            pathEl.textContent = fieldPath || '';
            row.appendChild(pathEl);

            // Value: ONLY for verified rows per PHI rule.
            if (status === 'verified' && value != null) {
                var valEl = document.createElement('span');
                valEl.className = 'hitl-field-value';
                valEl.title = String(value);
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

            // Block ID badge.
            var badge = document.createElement('span');
            badge.className = 'hitl-blk-badge hitl-blk-badge--' +
                (blockId ? status : 'null');
            badge.setAttribute('data-block-id', blockId || '');
            badge.textContent = blockId ? blockId : 'no block';
            badge.setAttribute('aria-label', blockId ? ('Block ' + blockId) : 'No block ID');
            row.appendChild(badge);

            // Click on row or badge → emit highlight.
            function handleClick(e) {
                e.stopPropagation();
                if (blockId) emitHighlight(blockId);
            }
            row.addEventListener('click', handleClick);

            return row;
        }

        /**
         * Build a "possibly missed" block row (uncited docling block).
         */
        function buildBlockRow(block) {
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
                // Truncate to 80 chars display (UTF-16 units, sufficient
                // for display purposes per PRD §3).
                snippet.textContent = '“' + truncate(block.text_snippet, 80) + '”';
                row.appendChild(snippet);
            }

            row.addEventListener('click', function () {
                if (block.block_id) emitHighlight(block.block_id);
            });

            return row;
        }

        function renderRightPane(extraction) {
            var fields = Array.isArray(extraction.fields) ? extraction.fields : [];
            var blocks = Array.isArray(extraction.docling_blocks) ? extraction.docling_blocks : [];

            // Build a set of cited block IDs.
            var citedBlockIds = {};
            fields.forEach(function (f) {
                if (f.source_block_id) citedBlockIds[f.source_block_id] = true;
            });

            var captured = fields.filter(function (f) { return f.status === 'verified'; });
            var missed   = fields.filter(function (f) { return f.status === 'stripped'; });
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
                            buildFieldRow('verified', f.field_path, f.value, f.source_block_id, null)
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
                            buildFieldRow('stripped', f.field_path, null, f.source_block_id, f.verifier_reason)
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
                        el.listPossibly.appendChild(buildBlockRow(b));
                    });
                }
            }

            // Reprocess button: enable only if there are stripped fields.
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
         * status (verified/stripped).
         *
         * Returns: 'verified' | 'stripped' | 'uncited'
         */
        function blockStatus(blockId, fields) {
            for (var i = 0; i < fields.length; i++) {
                if (fields[i].source_block_id === blockId) {
                    return fields[i].status === 'verified' ? 'verified' : 'stripped';
                }
            }
            return 'uncited';
        }

        function renderPdfPane(docId, extraction) {
            var fields = Array.isArray(extraction.fields) ? extraction.fields : [];
            var blocks = Array.isArray(extraction.docling_blocks) ? extraction.docling_blocks : [];

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

                return Promise.all(pagePromises);
            }).catch(function () {
                loadingEl.textContent = '';
                var err = document.createElement('div');
                err.className = 'hitl-pdf-error';
                err.textContent = labels.pdfError || 'Unable to render PDF preview.';
                el.pdfPane.appendChild(err);
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

                // The backend reprocess endpoint emits two distinct shapes:
                //   1. HTTP 200 success:  {status:"ok"|"refused"|"error",
                //                          new_extraction_id, total_fields,
                //                          stripped_fields}  — status echoes the
                //                          agent's verdict; "ok" means a new
                //                          active extraction landed, anything
                //                          else means the ladder refused.
                //   2. HTTP non-2xx:      {status:"error", detail:"<token>"}  —
                //                          pre-flight failures (auth, CSRF,
                //                          patient-id mismatch, etc.).
                // Both shapes are handled here. Older agent error codes
                // (cost_ceiling_exceeded, extraction_low_grounding) are kept
                // as legacy fallbacks in case they ever surface via body.error
                // or body.detail in a future propagation.

                var agentStatus = body && typeof body.status === 'string' ? body.status : null;

                if (xhr.status === 200 && agentStatus === 'ok') {
                    setFooterMsg(labels.reprocessOk || 'Reprocessed successfully.', 'success');
                    // Re-fetch and re-render.
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
                            clearHighlightListeners();
                            renderHeader(data);
                            renderRightPane(data);
                            renderPdfPane(docId, data);
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
            xhr.send(JSON.stringify({ extraction_id: extractionId }));
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
