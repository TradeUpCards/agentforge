/**
 * Citation-source sidecar — PRD §5 visual PDF bounding-box overlay.
 *
 * Runs in the top frame.  Listens for `postMessage` events from the
 * Co-Pilot chat iframe (`chat-panel.js`) of type
 * `oe-copilot/show-citation-source`.  On message, opens (or updates) a
 * fixed-position drawer docked to the LEFT of the Co-Pilot drawer
 * (`#oe-copilot-drawer`), renders the cited PDF page via PDF.js, and
 * draws a green SVG bbox over the cited Docling block.
 *
 * Click-to-source UX:
 *   - Clinician asks Co-Pilot a clinical question
 *   - Co-Pilot answer cites `[procedure_result:58027]` etc
 *   - Clinician clicks the citation badge in the chat
 *   - chat-panel.js fetches /resolve_citation.php (already shipped in
 *     commit 6d554f6f9) → gets {document_id, page, block_id, bbox, snippet}
 *   - chat-panel.js postMessage's the parent window with the resolver result
 *   - This script catches that message and shows the sidecar
 *   - Subsequent citation clicks UPDATE the sidecar in place — no
 *     close/reopen cycle (multi-citation comparison workflow)
 *
 * PHI custody (W2_ARCHITECTURE.md §8.3, AUDIT.md C-6):
 *   - Sidecar fetches the PDF directly from /controller.php which is
 *     already auth-gated by the user's OpenEMR session.  Same posture
 *     as opening the document in the Documents tab.
 *   - Document content is rendered to a CANVAS — not exfiltrated to
 *     external services.
 *   - No console.log of doc content, snippet, bbox values.
 *   - No Langfuse spans (this is OpenEMR-side, not agent-side).
 *
 * Bbox math: Docling emits bboxes in PDF point space with a bottom-left
 * origin.  PDF.js's viewport gives us a top-left-origin pixel transform
 * at the chosen scale.  The Y axis flips: canvasY = (pageHeight - y1) * scale.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    AgentForge Team
 * @copyright Copyright (c) 2026 AgentForge
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

(function () {
    'use strict';

    // ---- 0. Top-frame check + host resolution ----------------------------
    //
    // The sidecar lives in the TOP frame so it can be positioned alongside
    // the Co-Pilot drawer (which is also in the top frame, injected by
    // chart-bootstrap.js).  ScriptFilterSubscriber may inject this file
    // into multiple iframes; the IIFE bails on non-top contexts.
    var hostWin;
    try {
        hostWin = window.top || window;
        if (!hostWin || !hostWin.document) hostWin = window;
    } catch (e) {
        hostWin = window;
    }
    if (hostWin !== window) {
        // We're in a child frame — only the top-frame instance does the work.
        // Still need to relay any postMessage from this frame upward, but
        // window.postMessage already bubbles to top, so nothing to do.
        return;
    }
    var hostDoc = hostWin.document;

    // Single-instance guard — if injected twice somehow, bail on the second.
    if (hostWin.__OE_COPILOT_CITATION_SIDECAR_LOADED__) return;
    hostWin.__OE_COPILOT_CITATION_SIDECAR_LOADED__ = true;

    // ---- 1. Resolve the module base URL ----------------------------------

    var MODULE_BASE = (function () {
        var scripts = document.getElementsByTagName('script');
        for (var i = scripts.length - 1; i >= 0; i--) {
            var src = scripts[i].src || '';
            if (src.indexOf('citation-sidecar.js') !== -1) {
                return src.replace(/\/citation-sidecar\.js.*$/, '');
            }
        }
        return '/interface/modules/custom_modules/oe-module-clinical-copilot/public';
    })();

    // ---- 2. PDF.js worker config -----------------------------------------
    //
    // Configure once at script load.  The pdf.worker.min.js bundle is
    // vendored alongside pdf.min.js under public/vendor/pdfjs/.  PDF.js
    // requires this set BEFORE the first getDocument() call.
    function configurePdfJs() {
        if (!hostWin.pdfjsLib) return false;
        if (hostWin.pdfjsLib._isPlaceholder) return false;
        try {
            hostWin.pdfjsLib.GlobalWorkerOptions.workerSrc =
                MODULE_BASE + '/vendor/pdfjs/pdf.worker.min.js';
        } catch (e) { /* readonly in some PDF.js builds — ignore */ }
        return true;
    }

    // ---- 3. Sidecar DOM scaffold -----------------------------------------

    var SIDECAR_ID = 'oe-copilot-citation-sidecar';
    var sidecarEl = null;          // root <aside>
    var sidecarBodyEl = null;      // scrollable body holding the canvas
    var sidecarMetaEl = null;      // header meta text ("Doc 9842 · page 1")
    var sidecarLoadingEl = null;   // loading indicator
    var resizeObserverActive = false;

    function buildSidecar() {
        if (sidecarEl) return sidecarEl;

        sidecarEl = hostDoc.createElement('aside');
        sidecarEl.id = SIDECAR_ID;
        sidecarEl.className = 'oe-citation-sidecar oe-citation-sidecar--hidden';
        sidecarEl.setAttribute('role', 'complementary');
        sidecarEl.setAttribute('aria-label', 'Source document viewer');
        sidecarEl.setAttribute('aria-hidden', 'true');

        // Header
        var header = hostDoc.createElement('div');
        header.className = 'oe-citation-sidecar__header';

        var title = hostDoc.createElement('span');
        title.className = 'oe-citation-sidecar__title';
        title.textContent = 'Source';
        header.appendChild(title);

        sidecarMetaEl = hostDoc.createElement('span');
        sidecarMetaEl.className = 'oe-citation-sidecar__meta';
        sidecarMetaEl.textContent = '';
        header.appendChild(sidecarMetaEl);

        var openBtn = hostDoc.createElement('button');
        openBtn.type = 'button';
        openBtn.className = 'oe-citation-sidecar__open-tab';
        openBtn.title = 'Open document in a new tab';
        openBtn.textContent = '↗';
        openBtn.addEventListener('click', function () {
            var url = sidecarEl.getAttribute('data-doc-url') || '';
            if (url) {
                hostWin.open(url, '_blank', 'noopener,noreferrer');
            }
        });
        header.appendChild(openBtn);

        var closeBtn = hostDoc.createElement('button');
        closeBtn.type = 'button';
        closeBtn.className = 'oe-citation-sidecar__close';
        closeBtn.setAttribute('aria-label', 'Close source viewer');
        closeBtn.textContent = '×';
        closeBtn.addEventListener('click', closeSidecar);
        header.appendChild(closeBtn);

        sidecarEl.appendChild(header);

        // Body
        sidecarBodyEl = hostDoc.createElement('div');
        sidecarBodyEl.className = 'oe-citation-sidecar__body';

        sidecarLoadingEl = hostDoc.createElement('div');
        sidecarLoadingEl.className = 'oe-citation-sidecar__loading';
        sidecarLoadingEl.textContent = 'Loading document…';
        sidecarBodyEl.appendChild(sidecarLoadingEl);

        sidecarEl.appendChild(sidecarBodyEl);

        // Collapse handle on the LEFT edge — clicking shrinks the sidecar
        // back to a peek tab while keeping it mounted.  Click again to
        // expand.  (Optional UX polish — handles rapid multi-citation
        // verification without losing the loaded PDF.)
        var handle = hostDoc.createElement('button');
        handle.type = 'button';
        handle.className = 'oe-citation-sidecar__handle';
        handle.setAttribute('aria-label', 'Collapse / expand source viewer');
        handle.innerHTML = '<span class="oe-citation-sidecar__handle-chevron">›</span>';
        handle.addEventListener('click', toggleCollapsed);
        sidecarEl.appendChild(handle);

        hostDoc.body.appendChild(sidecarEl);

        return sidecarEl;
    }

    function toggleCollapsed() {
        if (!sidecarEl) return;
        sidecarEl.classList.toggle('oe-citation-sidecar--collapsed');
        positionSidecar();
    }

    function closeSidecar() {
        if (!sidecarEl) return;
        sidecarEl.classList.add('oe-citation-sidecar--hidden');
        sidecarEl.setAttribute('aria-hidden', 'true');
    }

    function showSidecar() {
        buildSidecar();
        sidecarEl.classList.remove('oe-citation-sidecar--hidden');
        sidecarEl.setAttribute('aria-hidden', 'false');
        positionSidecar();
    }

    // ---- 4. Positioning relative to the Co-Pilot drawer ------------------

    function positionSidecar() {
        if (!sidecarEl) return;
        var drawer = hostDoc.getElementById('oe-copilot-drawer');
        var vw = hostWin.innerWidth || hostDoc.documentElement.clientWidth;
        var vh = hostWin.innerHeight || hostDoc.documentElement.clientHeight;

        var top = 90;     // below OpenEMR top nav
        var bottom = 0;
        var rightPx = 0;  // dock to right edge by default

        if (drawer) {
            var rect = drawer.getBoundingClientRect();
            var hidden = drawer.getAttribute('aria-hidden') === 'true';
            var open = !hidden && rect.width > 10 && rect.left < vw;
            if (open) {
                // Dock the LEFT edge of the sidecar against the LEFT edge of
                // the Co-Pilot drawer (so they sit side-by-side).
                top = rect.top;
                bottom = vh - rect.bottom;
                rightPx = vw - rect.left;
            } else {
                // Drawer is collapsed — match its vertical span if known.
                if (rect.top > 0 && rect.bottom > 0) {
                    top = rect.top;
                    bottom = vh - rect.bottom;
                }
            }
        }

        sidecarEl.style.top = top + 'px';
        sidecarEl.style.bottom = bottom + 'px';
        sidecarEl.style.right = rightPx + 'px';
        sidecarEl.style.height = 'auto';
    }

    // Watch the Co-Pilot drawer for state/position changes so the sidecar
    // tracks alongside it when the user toggles the drawer.
    function watchCopilotDrawer() {
        if (resizeObserverActive) return;
        resizeObserverActive = true;
        var drawer = hostDoc.getElementById('oe-copilot-drawer');
        if (drawer) {
            try {
                new MutationObserver(function () {
                    if (sidecarEl && !sidecarEl.classList.contains('oe-citation-sidecar--hidden')) {
                        positionSidecar();
                    }
                }).observe(drawer, {
                    attributes: true,
                    attributeFilter: ['aria-hidden', 'style', 'class'],
                });
            } catch (e) { /* MutationObserver fail — silent */ }
        }
        hostWin.addEventListener('resize', function () {
            if (sidecarEl && !sidecarEl.classList.contains('oe-citation-sidecar--hidden')) {
                positionSidecar();
            }
        });
    }

    // ---- 5. PDF rendering + bbox overlay ---------------------------------

    /**
     * @typedef {Object} CitationPayload
     * @property {number|string} documentId
     * @property {number}        page
     * @property {string|null}   blockId
     * @property {{x0:number, y0:number, x1:number, y1:number}} bbox  Docling-native (PDF point space, bottom-left)
     * @property {string}        snippet
     * @property {string|null}   patientId   Required to build the doc URL.
     */

    function buildPdfUrl(citation) {
        if (!citation || !citation.documentId || !citation.patientId) return null;
        return '/controller.php?document&retrieve' +
            '&patient_id=' + encodeURIComponent(String(citation.patientId)) +
            '&document_id=' + encodeURIComponent(String(citation.documentId)) +
            '&as_file=false';
    }

    function setLoading(text, isError) {
        if (!sidecarLoadingEl) return;
        sidecarLoadingEl.textContent = text;
        sidecarLoadingEl.style.display = '';
        sidecarLoadingEl.classList.toggle('oe-citation-sidecar__loading--error', !!isError);
    }

    function clearPagePane() {
        if (!sidecarBodyEl) return;
        // Remove all children except the loading indicator.
        var children = Array.prototype.slice.call(sidecarBodyEl.children);
        for (var i = 0; i < children.length; i++) {
            if (children[i] !== sidecarLoadingEl) {
                sidecarBodyEl.removeChild(children[i]);
            }
        }
    }

    /**
     * Render the cited page + bbox into the sidecar body.
     * @param {CitationPayload} citation
     */
    function renderCitation(citation) {
        if (!sidecarEl) buildSidecar();
        showSidecar();
        watchCopilotDrawer();

        // Remember the doc URL for the "open in new tab" header button.
        var pdfUrl = buildPdfUrl(citation);
        sidecarEl.setAttribute('data-doc-url', pdfUrl || '');

        // Update header meta.
        var docLabel = String(citation.documentId == null ? '?' : citation.documentId);
        var pageLabel = String(citation.page == null ? '?' : citation.page);
        sidecarMetaEl.textContent = 'Doc ' + docLabel + ' · page ' + pageLabel;

        clearPagePane();
        setLoading('Loading document…', false);

        if (!pdfUrl) {
            setLoading('Cannot load document — missing patient or document id.', true);
            return;
        }
        if (!configurePdfJs()) {
            setLoading('PDF.js not loaded — cannot render document preview.', true);
            return;
        }

        var loadTask;
        try {
            loadTask = hostWin.pdfjsLib.getDocument({
                url: pdfUrl,
                withCredentials: true,
            });
        } catch (e) {
            setLoading('Failed to start document load.', true);
            return;
        }

        loadTask.promise.then(function (pdfDoc) {
            var pageNum = Number(citation.page) || 1;
            if (pageNum < 1) pageNum = 1;
            if (pageNum > pdfDoc.numPages) pageNum = pdfDoc.numPages;
            return pdfDoc.getPage(pageNum).then(function (page) {
                renderPageWithBbox(page, citation);
            });
        }).catch(function (err) {
            setLoading(
                'Could not render document preview' +
                    (err && err.name ? ' (' + err.name + ')' : '') + '.',
                true,
            );
        });
    }

    function renderPageWithBbox(page, citation) {
        // Hide the loading indicator now that we have a page.
        if (sidecarLoadingEl) sidecarLoadingEl.style.display = 'none';

        // Width-fit to the body pane.  Account for the padding declared in CSS.
        var paneWidth = sidecarBodyEl.clientWidth || 600;
        var desiredWidth = Math.max(paneWidth - 24, 240); // 12px each side padding

        var naturalViewport = page.getViewport({ scale: 1.0 });
        var scale = desiredWidth / naturalViewport.width;
        var viewport = page.getViewport({ scale: scale });

        var wrap = hostDoc.createElement('div');
        wrap.className = 'oe-citation-sidecar__page-wrap';
        wrap.style.position = 'relative';
        wrap.style.width = Math.round(viewport.width) + 'px';
        wrap.style.height = Math.round(viewport.height) + 'px';
        wrap.style.margin = '0 auto';

        var canvas = hostDoc.createElement('canvas');
        canvas.className = 'oe-citation-sidecar__canvas';
        canvas.width = Math.round(viewport.width);
        canvas.height = Math.round(viewport.height);
        canvas.style.width = Math.round(viewport.width) + 'px';
        canvas.style.height = Math.round(viewport.height) + 'px';
        wrap.appendChild(canvas);

        var svgNS = 'http://www.w3.org/2000/svg';
        var svg = hostDoc.createElementNS(svgNS, 'svg');
        svg.setAttribute('xmlns', svgNS);
        svg.setAttribute('width', String(Math.round(viewport.width)));
        svg.setAttribute('height', String(Math.round(viewport.height)));
        svg.setAttribute('viewBox',
            '0 0 ' + Math.round(viewport.width) + ' ' + Math.round(viewport.height));
        svg.style.position = 'absolute';
        svg.style.top = '0';
        svg.style.left = '0';
        svg.style.pointerEvents = 'none';
        wrap.appendChild(svg);

        sidecarBodyEl.appendChild(wrap);

        // Render the page to canvas.
        //
        // Three layered fixes for PDF.js's pink-tint behavior on certain lab
        // PDFs (observed: Pacific Diagnostics lipid panel renders rose-pink
        // in our sidecar but beige in OpenEMR's native viewer):
        //
        //   1. annotationMode: DISABLE — suppresses PDF.js's default
        //      form-field highlight rendering (translucent pink/magenta over
        //      input rectangles).  Some lab PDFs are authored as fillable
        //      forms; without this flag every form field area gets a colored
        //      overlay that visually competes with our own bbox highlight.
        //
        //   2. intent: 'print' — strips non-printing Optional Content Groups
        //      (PDF layers tagged "ViewOnly: true / PrintAlways: false") and
        //      certain non-printing annotation overlays that survive
        //      annotationMode: DISABLE.  Print intent matches how a clean
        //      printout would look.
        //
        //   3. pageColors — explicit white background + black foreground.
        //      Overrides any inherited or system-color rendering the page
        //      might otherwise pick up; immune to OS-level dark mode or
        //      forced-colors media queries that occasionally bleed into
        //      PDF.js canvas paints.
        //
        // Note: a residual color shift may remain on PDFs authored in pure
        // CMYK with non-standard ICC profiles — PDF.js uses approximate
        // CMYK→RGB conversion (no full ICC profile path) so warm beige
        // values can render rose-tinted relative to Chrome's native viewer.
        // That residual is a PDF.js engine limitation, not a fix we can
        // make from our render-call settings.
        var annotationMode = (
            hostWin.pdfjsLib && hostWin.pdfjsLib.AnnotationMode
                ? hostWin.pdfjsLib.AnnotationMode.DISABLE
                : 0
        );
        page.render({
            canvasContext: canvas.getContext('2d'),
            viewport: viewport,
            annotationMode: annotationMode,
            intent: 'print',
            pageColors: {
                background: 'rgb(255, 255, 255)',
                foreground: 'rgb(0, 0, 0)',
            },
        }).promise.then(function () {
            drawBboxOverlay(svg, naturalViewport, scale, citation);
            // Scroll to the bbox after render so the highlight is in view.
            try { wrap.scrollIntoView({ block: 'start', behavior: 'smooth' }); } catch (e) { /* ignore */ }
        });
    }

    /**
     * Draw a green bbox overlay rectangle on the SVG.
     *
     * Docling bbox is in PDF point space with bottom-left origin:
     *   x0, y0, x1, y1
     *
     * PDF.js's viewport renders top-left origin at the chosen scale.
     * The Y axis flips: canvasY = (pageHeight - y1) * scale.
     */
    function drawBboxOverlay(svg, naturalViewport, scale, citation) {
        if (!citation || !citation.bbox) return;
        var bbox = citation.bbox;
        if (
            typeof bbox.x0 !== 'number' || typeof bbox.y0 !== 'number' ||
            typeof bbox.x1 !== 'number' || typeof bbox.y1 !== 'number'
        ) {
            return;
        }

        var pageHeight = naturalViewport.height;
        var canvasX = bbox.x0 * scale;
        var canvasY = (pageHeight - bbox.y1) * scale;
        var canvasW = (bbox.x1 - bbox.x0) * scale;
        var canvasH = (bbox.y1 - bbox.y0) * scale;

        var svgNS = 'http://www.w3.org/2000/svg';
        var rect = hostDoc.createElementNS(svgNS, 'rect');
        rect.setAttribute('x', String(canvasX));
        rect.setAttribute('y', String(canvasY));
        rect.setAttribute('width', String(canvasW));
        rect.setAttribute('height', String(canvasH));
        rect.setAttribute('fill', 'rgba(34, 197, 94, 0.18)'); // green @ 18%
        rect.setAttribute('stroke', '#16a34a');
        rect.setAttribute('stroke-width', '2');
        rect.setAttribute('rx', '2');
        rect.setAttribute('class', 'oe-citation-sidecar__bbox');
        svg.appendChild(rect);

        // Block id label (small chip in the top-left corner of the bbox).
        if (citation.blockId) {
            var label = hostDoc.createElementNS(svgNS, 'text');
            label.setAttribute('x', String(canvasX + 4));
            label.setAttribute('y', String(Math.max(canvasY - 4, 12)));
            label.setAttribute('font-family', 'system-ui, sans-serif');
            label.setAttribute('font-size', '11');
            label.setAttribute('font-weight', '600');
            label.setAttribute('fill', '#16a34a');
            label.setAttribute('class', 'oe-citation-sidecar__bbox-label');
            label.textContent = String(citation.blockId);
            svg.appendChild(label);
        }
    }

    // ---- 6. PostMessage listener -----------------------------------------

    /**
     * Expected payload from chat-panel.js:
     *   {
     *     type: 'oe-copilot/show-citation-source',
     *     citation: {
     *       documentId: 9842,
     *       page: 1,
     *       blockId: 'block_33',
     *       bbox: {x0, y0, x1, y1},
     *       snippet: '...',
     *       patientId: '1'
     *     }
     *   }
     */
    hostWin.addEventListener('message', function (e) {
        if (!e.data || typeof e.data !== 'object') return;
        if (e.data.type !== 'oe-copilot/show-citation-source') return;
        var citation = e.data.citation;
        if (!citation || typeof citation !== 'object') return;
        renderCitation(citation);
    });

    // ---- 7. Configure PDF.js worker eagerly ------------------------------
    // First-citation latency benefits from the worker being warm on page
    // load.  No-op if the bundle isn't present yet (e.g. ScriptFilter
    // ordering).  Re-tries lazily on first renderCitation call.
    configurePdfJs();
}());
