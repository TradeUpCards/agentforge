/**
 * Clinical Co-Pilot chart-page bootstrap.
 *
 * Loaded on every OpenEMR page via ScriptFilterSubscriber. Self-determines
 * whether to render the floating launcher + drawer (only on patient chart
 * pages) and idempotently injects into the top-most window so the drawer
 * floats over the chart's iframe layout instead of being trapped inside
 * a sub-frame.
 *
 * UX model: drawer slides in from the right ~520px wide, leaving the chart
 * visible underneath. PCP can dismiss with the X button, the backdrop, or
 * the Escape key. Drawer iframes chat-panel.php so the existing chat UI
 * (with its full session/CSRF/HMAC plumbing) is reused unchanged.
 *
 * @package   OpenEMR
 * @author    AgentForge Team
 * @copyright Copyright (c) 2026 AgentForge
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

(function () {
    'use strict';

    // ---- 0. Header-icon wiring (runs in EVERY frame) ----------------------
    //
    // The page-heading row (rendered server-side via PageHeadingSubscriber)
    // includes a Co-Pilot toggle icon. That row lives inside an iframe on
    // most chart tabs, but the drawer state lives in the TOP window — so
    // every frame needs to (a) wire its local icon's click to the top
    // window's OE_COPILOT.toggle(), and (b) reflect the top window's
    // current open/closed state on its local icon's appearance.
    //
    // This block runs BEFORE the launcher-already-exists bail because that
    // bail short-circuits secondary frames; the icon wiring still needs
    // to happen in those frames.

    function setIconActive(iconEl, isActive) {
        if (!iconEl) return;
        iconEl.classList.toggle('is-active', !!isActive);
        var i = iconEl.querySelector('i');
        if (!i) return;
        if (isActive) {
            i.classList.remove('far');
            i.classList.add('fas');
        } else {
            i.classList.remove('fas');
            i.classList.add('far');
        }
    }

    function wireHeaderIconInThisFrame() {
        var icon = document.getElementById('oe-copilot-icon');
        if (!icon || icon.__oeWired) return;
        icon.__oeWired = true;
        icon.addEventListener('click', function (e) {
            e.preventDefault();
            try {
                if (window.top && window.top.OE_COPILOT && window.top.OE_COPILOT.toggle) {
                    window.top.OE_COPILOT.toggle();
                }
            } catch (_) { /* cross-origin or not initialized yet */ }
        });
        // Sync initial visual state from the top window.
        try {
            var topHtml = window.top.document.documentElement;
            setIconActive(icon, topHtml.classList.contains('oe-copilot-open'));
        } catch (_) { /* cross-origin — leave at default outline */ }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', wireHeaderIconInThisFrame);
    } else {
        wireHeaderIconInThisFrame();
    }

    // ---- 1. Resolve the host window (top frame, same-origin tolerant) -----

    var hostWin = window;
    try {
        if (window.top && window.top.document && window.top !== window) {
            hostWin = window.top;
        }
    } catch (e) {
        // Cross-origin top window — stay in current frame.
        hostWin = window;
    }
    var hostDoc = hostWin.document;

    // Idempotent: if some other iframe already injected the launcher into
    // the top window, do nothing. Only the first runner wins. (Header-icon
    // wiring above already ran for this frame, so secondary frames still
    // get their icon hooked up.)
    if (hostDoc.getElementById('oe-copilot-launcher')) {
        return;
    }

    // ---- 2. Decide whether to render --------------------------------------

    /**
     * Heuristic for "is this a patient chart context?":
     * - The top-frame URL path is under /interface/main/ (the OpenEMR shell)
     *   AND a patient is currently selected (we'll detect this via
     *   the presence of patient-related DOM elements once they render).
     *
     * Concretely: render the launcher on every shell page; the click
     * handler builds the chat-panel.php URL which itself enforces a
     * session pid check and shows "select a patient" if none.
     *
     * This way we don't have to maintain a list of chart-page basenames.
     */
    function isShellContext() {
        var path = '';
        try { path = hostWin.location.pathname || ''; } catch (e) { return false; }
        // OpenEMR's shell + chart pages all live under /interface/. We
        // exclude /interface/login and the standalone module-only pages.
        if (!/\/interface\//.test(path)) return false;
        if (/login_screen|\/oauth2/.test(path)) return false;
        return true;
    }

    if (!isShellContext()) {
        return;
    }

    // ---- 3. Resolve the panel URL (webroot is sniffed from current src) --

    function resolvePanelUrl() {
        // Find the script tag that loaded this file; use its src to derive
        // the public-dir path. Avoids hardcoding webroot.
        var scripts = hostDoc.getElementsByTagName('script');
        for (var i = scripts.length - 1; i >= 0; i--) {
            var src = scripts[i].src || '';
            if (src.indexOf('chart-bootstrap.js') !== -1) {
                return src.replace(/chart-bootstrap\.js.*$/, 'chat-panel.php?embedded=1');
            }
        }
        // Fallback: assume default install layout.
        return '/interface/modules/custom_modules/oe-module-clinical-copilot/public/chat-panel.php?embedded=1';
    }

    // ---- 4. Build DOM: backdrop, drawer, launcher -------------------------

    function buildDom() {
        // Backdrop
        var backdrop = hostDoc.createElement('div');
        backdrop.className = 'oe-copilot-backdrop';
        backdrop.id = 'oe-copilot-backdrop';

        // Drawer
        var drawer = hostDoc.createElement('aside');
        drawer.className = 'oe-copilot-drawer';
        drawer.id = 'oe-copilot-drawer';
        drawer.setAttribute('role', 'dialog');
        drawer.setAttribute('aria-label', 'Clinical Co-Pilot');
        drawer.setAttribute('aria-hidden', 'true');

        var header = hostDoc.createElement('div');
        header.className = 'oe-copilot-drawer__header';

        var title = hostDoc.createElement('h2');
        title.className = 'oe-copilot-drawer__title';
        title.textContent = 'Clinical Co-Pilot';

        var closeBtn = hostDoc.createElement('button');
        closeBtn.className = 'oe-copilot-drawer__close';
        closeBtn.type = 'button';
        closeBtn.setAttribute('aria-label', 'Close');
        closeBtn.textContent = '×'; // ×

        header.appendChild(title);
        header.appendChild(closeBtn);

        var body = hostDoc.createElement('div');
        body.className = 'oe-copilot-drawer__body';

        // The iframe is created lazily on first open so we don't pay the
        // load cost (or hit chat-panel.php's session check) until the user
        // actually summons the panel.
        drawer.appendChild(header);
        drawer.appendChild(body);

        // Persistent chevron toggle. Lives outside the drawer so it stays
        // visible in both open and closed states (CSS positions it at the
        // viewport right edge when closed, at the drawer's left edge when
        // open). Single click toggles. The inner icon span rotates 180°
        // between states (◂ open-me, ▸ close-me).
        var chevron = hostDoc.createElement('button');
        chevron.className = 'oe-copilot-chevron';
        chevron.id = 'oe-copilot-chevron';
        chevron.type = 'button';
        chevron.setAttribute('aria-label', 'Toggle Clinical Co-Pilot');
        chevron.title = 'Toggle Clinical Co-Pilot';
        var chevronIcon = hostDoc.createElement('span');
        chevronIcon.className = 'oe-copilot-chevron__icon';
        chevronIcon.innerHTML = '&#9666;'; // ◂ (default: closed → click to open)
        chevron.appendChild(chevronIcon);

        // Launcher button
        var launcher = hostDoc.createElement('button');
        launcher.className = 'oe-copilot-launcher';
        launcher.id = 'oe-copilot-launcher';
        launcher.type = 'button';
        launcher.setAttribute('aria-label', 'Open Clinical Co-Pilot');
        launcher.innerHTML =
            '<span class="oe-copilot-launcher__icon">✨</span>' +
            '<span class="oe-copilot-launcher__label">Co-Pilot</span>';

        hostDoc.body.appendChild(backdrop);
        hostDoc.body.appendChild(drawer);
        hostDoc.body.appendChild(chevron);
        hostDoc.body.appendChild(launcher);

        return {
            backdrop: backdrop,
            drawer: drawer,
            body: body,
            closeBtn: closeBtn,
            chevron: chevron,
            launcher: launcher,
        };
    }

    // ---- 5. Wiring --------------------------------------------------------

    /**
     * Find the largest visible iframe in the host document — i.e. the
     * active tab's content area in OpenEMR's tab shell. We use that
     * iframe's bounding rect to align the drawer (top edge below the
     * banner + tab strip; height matching the chart area).
     */
    function findActiveTabIframe() {
        var iframes = hostDoc.querySelectorAll('iframe');
        var bestIframe = null;
        var bestArea = 0;
        for (var i = 0; i < iframes.length; i++) {
            var f = iframes[i];
            // Skip iframes that aren't laid out (display:none or detached).
            if (f.offsetParent === null) continue;
            var rect = f.getBoundingClientRect();
            // Skip tiny iframes (probably hidden trackers or chrome bits).
            if (rect.width < 200 || rect.height < 200) continue;
            var area = rect.width * rect.height;
            if (area > bestArea) {
                bestArea = area;
                bestIframe = f;
            }
        }
        return bestIframe;
    }

    /**
     * Set CSS variables so the drawer aligns with the active iframe's
     * top edge (rather than the viewport's). Called on open + on resize.
     * Falls back to viewport-full if no iframe is found.
     */
    function syncDrawerToActiveIframe() {
        var f = findActiveTabIframe();
        if (!f) {
            // No iframe found — use viewport defaults (the drawer's
            // CSS fallbacks: top: 0, bottom: 0, chevron centered).
            hostDoc.documentElement.style.removeProperty('--oe-copilot-drawer-top');
            hostDoc.documentElement.style.removeProperty('--oe-copilot-drawer-bottom');
            hostDoc.documentElement.style.removeProperty('--oe-copilot-chevron-top');
            return;
        }
        var rect = f.getBoundingClientRect();
        var viewportH = hostWin.innerHeight || hostDoc.documentElement.clientHeight;
        hostDoc.documentElement.style.setProperty('--oe-copilot-drawer-top', rect.top + 'px');
        hostDoc.documentElement.style.setProperty('--oe-copilot-drawer-bottom', Math.max(0, viewportH - rect.bottom) + 'px');
        hostDoc.documentElement.style.setProperty('--oe-copilot-chevron-top', (rect.top + rect.height / 2) + 'px');
    }

    /**
     * Detect the currently-selected patient. OpenEMR is inconsistent
     * about how it surfaces this — sometimes `?pid=N` in iframe URLs,
     * sometimes only in session, sometimes in a global like
     * `top.window.pid_value`. We try multiple strategies and return
     * the first hit.
     *
     * Returns the pid as a string, or null if none found.
     */
    function detectActivePid() {
        // Strategy 1: any iframe URL containing pid=N or set_pid=N
        var iframes = hostDoc.querySelectorAll('iframe');
        for (var i = iframes.length - 1; i >= 0; i--) {
            var f = iframes[i];
            if (f.offsetParent === null) continue;
            var src = '';
            try {
                src = f.src || '';
                if (!src && f.contentWindow && f.contentWindow.location) {
                    src = f.contentWindow.location.href || '';
                }
            } catch (e) { /* cross-origin */ }
            var m = src.match(/[?&](?:set_)?pid=(\d+)/);
            if (m) return m[1];
        }
        // Strategy 2: top window's URL itself
        try {
            var topMatch = (hostWin.location.href || '').match(/[?&](?:set_)?pid=(\d+)/);
            if (topMatch) return topMatch[1];
        } catch (e) { /* nothing */ }
        // Strategy 3: OpenEMR may stash the pid in well-known places
        try {
            if (hostWin.pid_value) return String(hostWin.pid_value);
            if (hostWin.opener && hostWin.opener.pid_value) return String(hostWin.opener.pid_value);
        } catch (e) { /* nothing */ }
        // Strategy 4: walk into iframe content windows for their globals
        for (var j = 0; j < iframes.length; j++) {
            var fr = iframes[j];
            try {
                var w = fr.contentWindow;
                if (w && w.pid_value) return String(w.pid_value);
                if (w && w.document && w.document.body) {
                    var bodyPid = w.document.body.getAttribute('data-pid');
                    if (bodyPid) return bodyPid;
                }
            } catch (e) { /* cross-origin */ }
        }
        return null;
    }

    /**
     * Push the current open/closed state out to every Co-Pilot icon
     * rendered in any same-origin frame (the page-heading row in chart
     * tabs). Called whenever the drawer opens, closes, or an iframe
     * finishes loading (so newly-rendered icons pick up the correct
     * state without needing their own polling).
     */
    function propagateIconStateToFrames(isOpen) {
        // Update icon in the top doc itself (rare but possible — some
        // pages render their heading directly in the top frame).
        var topIcon = hostDoc.getElementById('oe-copilot-icon');
        if (topIcon) setIconActive(topIcon, isOpen);

        var iframes = hostDoc.querySelectorAll('iframe');
        for (var i = 0; i < iframes.length; i++) {
            var f = iframes[i];
            if (f.classList && f.classList.contains('oe-copilot-drawer__iframe')) continue;
            try {
                var doc = f.contentDocument;
                if (!doc) continue;
                var icon = doc.getElementById('oe-copilot-icon');
                if (icon) setIconActive(icon, isOpen);
            } catch (_) { /* cross-origin — silently skip */ }
        }
    }

    function init() {
        var dom = buildDom();
        var iframeLoaded = false;

        // Source of truth: what pid the chat iframe was actually rendered
        // for. Set when the iframe is created/reloaded; never silently
        // mutated by shell-navigation events. Comparing the OpenEMR
        // shell's *current* pid against this tells us if the chat is
        // stale relative to the chart the user is now looking at.
        var chatRenderedForPid = null;

        // Initial sync so the chevron is correctly placed even before the
        // drawer is opened for the first time.
        syncDrawerToActiveIframe();

        // Re-sync on viewport resize (drawer + chevron stay aligned with
        // the chart iframe even when the user resizes the window).
        hostWin.addEventListener('resize', syncDrawerToActiveIframe);

        // Layout alignment + pid drift detection on a slow interval
        // (1.5s). Cheap (just reads bounding rects + iframe URLs). The
        // iframe `load` event is the primary pid-change signal but
        // OpenEMR sometimes navigates within the same iframe in ways
        // that don't fire load — polling closes that gap.
        setInterval(function () {
            syncDrawerToActiveIframe();
            onIframeLoad();
        }, 1500);

        // ----- Event-driven patient-context tracking -----
        //
        // Every time an OpenEMR chart iframe finishes loading, we re-read
        // its URL for `pid=N`. If that pid differs from the one the chat
        // panel was rendered for (lastSeenPid), the user has navigated
        // to a different patient. We surface that to the user via a
        // banner inside the drawer (postMessage) so they can click to
        // refresh the chat — vs silently letting the LLM build replies
        // from a stale conversation history.

        var _lastDebugLoggedPid = null;
        function onIframeLoad() {
            // A freshly-loaded iframe may have rendered its own copy of
            // the page-heading icon — sync it to the current drawer
            // state so it reflects open/closed without per-frame polling.
            propagateIconStateToFrames(dom.drawer.classList.contains('is-open'));

            // Compare the OpenEMR shell's current pid against the pid
            // the chat iframe was *rendered for*. If the chat wasn't
            // rendered yet (drawer never opened), there's nothing to
            // be stale. If they match, nothing to do.
            var shellPid = detectActivePid();
            if (shellPid !== _lastDebugLoggedPid) {
                if (hostWin.console && hostWin.console.log) {
                    hostWin.console.log(
                        '[Co-Pilot] detected pid change: shellPid=' + shellPid +
                        ' chatRenderedForPid=' + chatRenderedForPid
                    );
                }
                _lastDebugLoggedPid = shellPid;
            }
            if (!shellPid || !chatRenderedForPid) return;
            if (shellPid === chatRenderedForPid) return;
            // Drift: chat is for a different patient than the chart
            // currently selected in OpenEMR. Surface it.
            onPatientChanged(chatRenderedForPid, shellPid);
        }

        function attachIframeListeners(root) {
            var scope = root || hostDoc;
            var iframes = scope.querySelectorAll ? scope.querySelectorAll('iframe') : [];
            for (var i = 0; i < iframes.length; i++) {
                var f = iframes[i];
                // Skip our own drawer iframe — it's not navigation.
                if (f.classList && f.classList.contains('oe-copilot-drawer__iframe')) continue;
                // Idempotent — only attach once per iframe.
                if (f.__oeCopilotListening) continue;
                f.__oeCopilotListening = true;
                f.addEventListener('load', onIframeLoad);
            }
        }

        // Initial pass: attach to whatever iframes exist now.
        attachIframeListeners();

        // The chat panel iframe announces its pid every time it loads
        // (initial open, manual Refresh after a patient-change banner,
        // etc). Authoritative signal that "the chat is now rendered for
        // pid N" — supersedes any pid we'd guess from polling.
        //
        // It also signals BEFORE a reload ("about-to-reload") so we can
        // suppress the polling-based detection during the reload window.
        // Without that, polling sees the still-stale chatRenderedForPid
        // and re-fires the banner mid-reload (the race the user hit).
        hostWin.addEventListener('message', function (e) {
            if (!e.data) return;
            if (e.data.type === 'oe-copilot/panel-loaded' && e.data.pid) {
                chatRenderedForPid = String(e.data.pid);
            } else if (e.data.type === 'oe-copilot/about-to-reload') {
                // Clear so detection short-circuits on `!chatRenderedForPid`
                // until the next panel-loaded message lands.
                chatRenderedForPid = null;
            }
        });

        // Catch iframes that are added dynamically (OpenEMR's tab system
        // creates new iframes when a tab is opened for the first time).
        if (typeof MutationObserver !== 'undefined') {
            var observer = new MutationObserver(function (mutations) {
                for (var i = 0; i < mutations.length; i++) {
                    var added = mutations[i].addedNodes;
                    for (var j = 0; j < added.length; j++) {
                        var n = added[j];
                        if (n.nodeType !== 1) continue;
                        if (n.tagName === 'IFRAME') {
                            attachIframeListeners({ querySelectorAll: function () { return [n]; } });
                        } else if (n.querySelectorAll) {
                            attachIframeListeners(n);
                        }
                    }
                }
            });
            observer.observe(hostDoc.body, { childList: true, subtree: true });
        }

        function onPatientChanged(oldPid, newPid) {
            // If the drawer is closed, no UI to update — the next open()
            // will detect the pid change and reload the iframe.
            if (!dom.drawer.classList.contains('is-open')) {
                return;
            }
            // Drawer is open with a stale-context risk. Post a banner
            // message into the drawer iframe; chat-panel.js renders it
            // and offers a one-click refresh.
            try {
                var iframe = dom.body.querySelector('iframe');
                if (iframe && iframe.contentWindow) {
                    iframe.contentWindow.postMessage(
                        { type: 'oe-copilot/patient-changed', oldPid: oldPid, newPid: newPid },
                        '*'
                    );
                }
            } catch (e) {
                // Cross-origin or iframe-gone; silent fail (observability
                // must not break the user path).
            }
        }

        function ensureIframe(forceReload) {
            if (iframeLoaded && !forceReload) return;
            // Tear down any existing iframe so the new chat-panel.php
            // load gets a fresh JS context (empty messages array, fresh
            // CSRF token, fresh patient-name header).
            while (dom.body.firstChild) dom.body.removeChild(dom.body.firstChild);
            var iframe = hostDoc.createElement('iframe');
            iframe.className = 'oe-copilot-drawer__iframe';
            iframe.title = 'Clinical Co-Pilot chat panel';
            iframe.src = resolvePanelUrl() + '&_t=' + Date.now();
            dom.body.appendChild(iframe);
            iframeLoaded = true;
            // The iframe will read $_SESSION['pid'] on load; record what
            // we expect it to be rendering for. Used to detect drift
            // when the user navigates the OpenEMR shell to a different
            // patient.
            chatRenderedForPid = detectActivePid();
        }

        function open() {
            // If the shell's current pid differs from what the chat
            // iframe was last rendered for, force-reload so the chat
            // starts fresh against the now-selected patient. Otherwise
            // keep the existing conversation.
            var currentPid = detectActivePid();
            var staleChat = currentPid && currentPid !== chatRenderedForPid;
            ensureIframe(staleChat);

            // Resync the drawer's top/bottom to the active iframe RIGHT
            // before opening so the drawer aligns with whatever tab is
            // currently active (Dashboard, Reports, History, etc.).
            syncDrawerToActiveIframe();
            // Toggle the html-level class — CSS uses it to (a) shrink
            // the chart iframe via max-width and (b) hide the floating
            // launcher. Push behavior is iframe-only; banner + tab strip
            // stay full width.
            hostDoc.documentElement.classList.add('oe-copilot-open');
            dom.drawer.classList.add('is-open');
            dom.drawer.setAttribute('aria-hidden', 'false');
            propagateIconStateToFrames(true);
        }

        function close() {
            hostDoc.documentElement.classList.remove('oe-copilot-open');
            dom.drawer.classList.remove('is-open');
            dom.drawer.setAttribute('aria-hidden', 'true');
            propagateIconStateToFrames(false);
        }

        function toggle() {
            if (dom.drawer.classList.contains('is-open')) {
                close();
            } else {
                open();
            }
        }

        dom.launcher.addEventListener('click', open);
        dom.closeBtn.addEventListener('click', close);
        dom.chevron.addEventListener('click', toggle);
        hostDoc.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && dom.drawer.classList.contains('is-open')) {
                close();
            }
        });

        // Expose for the patient menu item — clicking the menu hash trigger
        // (#oe-copilot-open) opens the drawer instead of navigating.
        hostWin.OE_COPILOT = { open: open, close: close, toggle: toggle };
        hostWin.addEventListener('hashchange', function () {
            if (hostWin.location.hash === '#oe-copilot-open') {
                open();
                try {
                    hostWin.history.replaceState(null, '', hostWin.location.pathname + hostWin.location.search);
                } catch (_) {}
            }
        });

        // Honor the hash if the page was loaded with it already set
        // (e.g. user clicked the menu item when chart-bootstrap hadn't
        // loaded yet on a previous page).
        if (hostWin.location.hash === '#oe-copilot-open') {
            open();
        }
    }

    if (hostDoc.readyState === 'loading') {
        hostDoc.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
