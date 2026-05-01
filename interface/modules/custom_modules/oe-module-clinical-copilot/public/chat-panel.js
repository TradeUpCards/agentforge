/**
 * Clinical Co-Pilot chat panel client-side controller.
 *
 * Maintains the conversation as a JavaScript array (`messages`) and
 * round-trips the entire history to the OpenEMR endpoint on each turn.
 * Renders assistant replies with very lightweight markdown (paragraphs,
 * bold, bullet lists) and detects record-id citation patterns
 * (e.g. `lists:1408` or `[lists:1408]`) so they can be turned into
 * clickable badges that reveal the matching `retrieved_records` entry.
 *
 * @package   OpenEMR
 * @author    AgentForge Team
 * @copyright Copyright (c) 2026 AgentForge
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

(function () {
    'use strict';

    var config = window.OE_COPILOT_CONFIG || {};
    var labels = config.labels || {};

    // Announce our pid to the parent shell so chart-bootstrap.js can
    // update its `chatRenderedForPid` tracker. Fires on every load —
    // initial open, manual refresh after the patient-changed banner,
    // browser back/forward, etc. The parent uses this to reconcile its
    // expectations against reality (vs guessing via timing).
    try {
        var pidValue = config.patientId || (function () {
            var el = document.getElementById('copilot-patient-name');
            return el ? el.getAttribute('data-pid') : null;
        })();
        if (pidValue && window.parent && window.parent !== window) {
            window.parent.postMessage(
                { type: 'oe-copilot/panel-loaded', pid: String(pidValue) },
                '*'
            );
        }
    } catch (e) { /* observability must not break the user path */ }

    /** @type {Array<{role: string, content: string}>} */
    var messages = [];

    /** @type {Object<string, Object>} record_id -> retrieved_records entry. */
    var lastRetrievedRecords = {};

    var $messagesEl = document.getElementById('copilot-messages');
    var $form = document.getElementById('copilot-form');
    var $input = document.getElementById('copilot-input');
    var $send = document.getElementById('copilot-send');
    var $emptyState = $messagesEl ? $messagesEl.querySelector('.copilot-empty-state') : null;

    function escapeHtml(value) {
        return String(value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    /**
     * Very small markdown -> HTML renderer.
     * Supports paragraphs, **bold**, *italics*, `- ` bullet lists, and
     * preserves citation tokens like `[table:id]` so they can be wired
     * up afterwards.
     */
    function renderMarkdown(text) {
        if (typeof text !== 'string' || text === '') {
            return '';
        }

        var safe = escapeHtml(text);

        // Bold and italics.
        safe = safe.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
        safe = safe.replace(/(^|[^*])\*([^*]+)\*(?!\*)/g, '$1<em>$2</em>');

        // Block-level processing: split into paragraphs by blank lines.
        var blocks = safe.split(/\n{2,}/);
        var rendered = blocks.map(function (block) {
            var lines = block.split('\n');
            var bulletLines = lines.filter(function (l) {
                return /^\s*[-*]\s+/.test(l);
            });
            if (bulletLines.length === lines.length && lines.length > 0) {
                var items = lines.map(function (l) {
                    return '<li>' + l.replace(/^\s*[-*]\s+/, '') + '</li>';
                }).join('');
                return '<ul>' + items + '</ul>';
            }
            // <div> instead of <p> so the citation popover (also a
             // <div>) can be inserted inline as a sibling without
             // triggering the browser's "block-inside-paragraph"
             // rendering quirks (the empty-on-first-click bug).
             return '<div class="copilot-paragraph">' + block.replace(/\n/g, '<br>') + '</div>';
        });

        return rendered.join('');
    }

    /**
     * Replace citation tokens of the form `[table:id]` (or bare
     * `table:id` when surrounded by word boundaries) with clickable
     * badges. The badge is bound to the matching retrieved_records entry
     * if one is available.
     */
    function linkCitations(html) {
        // Only match the bracketed form `[table:id]`. The bare-form
        // fallback we used to have here matched inside the badge HTML
        // we'd just produced (the `>` in the preceding-char class
        // matched the closing `>` of the button's opening tag), wrapping
        // every bracketed citation in a SECOND identical button. That
        // produced nested <button><button>...</button>...</button> in
        // the DOM, and any popover insert ended up inside the outer
        // button. The system prompt instructs the LLM to use brackets,
        // so the bare-form fallback isn't needed.
        return html.replace(/\[([a-z_][a-z0-9_]*:[A-Za-z0-9_\-]+)\]/g, function (_match, id) {
            return renderCitationBadge(id);
        });
    }

    function renderCitationBadge(recordId) {
        var safeId = escapeHtml(recordId);
        return '<button type="button" class="copilot-citation badge badge-info" data-record-id="' +
            safeId + '">' + safeId + '</button>';
    }

    function appendMessageNode(role, html) {
        if ($emptyState && $emptyState.parentNode) {
            $emptyState.parentNode.removeChild($emptyState);
            $emptyState = null;
        }
        var wrapper = document.createElement('div');
        wrapper.className = 'copilot-message copilot-message--' + role;

        var roleLabel = document.createElement('div');
        roleLabel.className = 'copilot-message__role';
        roleLabel.textContent = role === 'user' ? (labels.you || 'You') : (labels.copilot || 'Co-Pilot');

        var body = document.createElement('div');
        body.className = 'copilot-message__body';
        body.innerHTML = html;

        wrapper.appendChild(roleLabel);
        wrapper.appendChild(body);
        $messagesEl.appendChild(wrapper);
        if (role === 'user') {
            // Anchor the viewport to the new user message so the question
            // stays at the top and the response renders visibly below it.
            // Without this, long responses push the question off-screen
            // and the user has to scroll back up to read it.
            $messagesEl.scrollTop = wrapper.offsetTop;
        }
        // Assistant appends deliberately leave scroll alone — see above.
        return wrapper;
    }

    function appendUser(text) {
        appendMessageNode('user', '<p>' + escapeHtml(text).replace(/\n/g, '<br>') + '</p>');
    }

    function appendAssistant(text) {
        var rendered = renderMarkdown(text);
        rendered = linkCitations(rendered);
        return appendMessageNode('assistant', rendered);
    }

    function appendStatus(text, modifier) {
        var wrapper = document.createElement('div');
        wrapper.className = 'copilot-status copilot-status--' + (modifier || 'info');
        wrapper.textContent = text;
        $messagesEl.appendChild(wrapper);
        // No auto-scroll: the viewport is already anchored to the user's
        // most recent question (see appendMessageNode); status nodes
        // ("Thinking…", errors) appear below it and remain visible
        // without pushing the question out of view.
        return wrapper;
    }

    function setBusy(busy) {
        $send.disabled = busy;
        $input.disabled = busy;
        Array.prototype.forEach.call(
            document.querySelectorAll('[data-copilot-starter]'),
            function (btn) { btn.disabled = busy; }
        );
    }

    function indexRetrievedRecords(records) {
        lastRetrievedRecords = {};
        if (!Array.isArray(records)) {
            return;
        }
        records.forEach(function (rec) {
            if (!rec || typeof rec !== 'object') {
                return;
            }
            var key = (rec.table && rec.record_id)
                ? rec.table + ':' + rec.record_id
                : rec.record_id;
            if (key) {
                lastRetrievedRecords[String(key)] = rec;
            }
        });
    }

    var openPopover = null;

    function buildCitationPopover(recordId) {
        var record = lastRetrievedRecords[recordId];
        var popover = document.createElement('div');
        popover.className = 'copilot-citation__popover';

        if (!record) {
            popover.textContent = labels.noRecord || 'No record details available';
            return popover;
        }

        var header = document.createElement('div');
        header.className = 'copilot-citation__header';
        header.textContent = (labels.source || 'Source') + ': ' + recordId +
            (record.citation_strength ? ' (' + record.citation_strength + ')' : '');
        popover.appendChild(header);

        if (record.fields && typeof record.fields === 'object') {
            var grid = document.createElement('div');
            grid.className = 'copilot-citation__fields';
            Object.keys(record.fields).forEach(function (key) {
                var labelEl = document.createElement('div');
                labelEl.className = 'copilot-citation__field-label';
                labelEl.textContent = key;
                var valueEl = document.createElement('div');
                valueEl.className = 'copilot-citation__field-value';
                valueEl.textContent = String(record.fields[key]);
                grid.appendChild(labelEl);
                grid.appendChild(valueEl);
            });
            popover.appendChild(grid);
        }
        return popover;
    }

    function closeOpenPopover() {
        if (openPopover && openPopover.popover && openPopover.popover.parentNode) {
            openPopover.popover.parentNode.removeChild(openPopover.popover);
        }
        if (openPopover) {
            requestChartHighlightClear();
        }
        openPopover = null;
    }

    function showCitationDetails(badge) {
        var id = badge.getAttribute('data-record-id');

        if (openPopover && openPopover.badge === badge) {
            closeOpenPopover();
            return;
        }
        closeOpenPopover();

        var popover = buildCitationPopover(id);
        var parent = badge.parentNode;
        if (parent) {
            if (badge.nextSibling) {
                parent.insertBefore(popover, badge.nextSibling);
            } else {
                parent.appendChild(popover);
            }
        }

        openPopover = { badge: badge, popover: popover, recordId: id };
        requestChartHighlight(id);
    }

    /**
     * Send a postMessage to the parent (top) window asking it to
     * highlight the cited record in the chart iframe. Best-effort —
     * chart-bootstrap.js scans the active iframe's DOM for matching
     * elements and adds a yellow-flash class. Silent no-op if no
     * match is found, since the chart's HTML structure varies by tab.
     */
    function requestChartHighlight(recordId) {
        try {
            var record = lastRetrievedRecords[recordId];
            var parts = String(recordId).split(':');
            var table = parts[0] || '';
            var bareId = parts.slice(1).join(':') || '';
            window.parent.postMessage({
                type: 'oe-copilot/highlight-record',
                recordId: recordId,
                table: table,
                bareId: bareId,
                fields: record && record.fields ? record.fields : null,
            }, '*');
        } catch (_) { /* parent unreachable — silent no-op */ }
    }

    function requestChartHighlightClear() {
        try {
            window.parent.postMessage({ type: 'oe-copilot/clear-highlight' }, '*');
        } catch (_) { /* silent */ }
    }

    /**
     * Subtle verifier-status indicator in the panel header. After each
     * successful assistant turn, shows "✓ N verified" (and " · M stripped"
     * if any claims were atomically stripped before the response shipped).
     * Lets the clinician see the verifier doing its job on every turn,
     * not just on refusals — supports the "verifiable trust" defense.
     */
    function updateVerifierStatus(verifiedCount, strippedCount) {
        var el = document.getElementById('copilot-verifier-status');
        if (!el) return;
        var parts = ['✓ ' + verifiedCount + ' verified'];
        if (strippedCount > 0) {
            parts.push(strippedCount + ' stripped');
        }
        el.textContent = parts.join(' · ');
        el.style.display = '';
        el.style.color = strippedCount > 0 ? '#b45309' : '#059669';
    }

    /**
     * Render a refusal: the LLM declined to answer (verifier 30% rule
     * fired, HMAC bad, tools failed, etc). Shows the reason AND the
     * `searched` list so the PCP knows exactly what the agent attempted
     * before refusing — per ARCHITECTURE.md §6: "Refusal is informative,
     * not blank. Refusal tells the PCP what was searched and points them
     * at the chart."
     */
    function appendRefusal(reason, searched) {
        if (!$messagesEl) return;
        var node = document.createElement('div');
        node.className = 'copilot-status copilot-status--refused';

        var h = document.createElement('div');
        h.style.cssText = 'font-weight: 600; margin-bottom: 6px;';
        h.textContent = reason;
        node.appendChild(h);

        if (Array.isArray(searched) && searched.length > 0) {
            var label = document.createElement('div');
            label.style.cssText = 'margin-top: 8px; margin-bottom: 4px; color: #6b7280; font-size: 12px;';
            label.textContent = 'What I attempted:';
            node.appendChild(label);

            var list = document.createElement('ul');
            list.style.cssText = 'margin: 0 0 6px 0; padding-left: 20px; font-size: 12px;';
            searched.forEach(function (t) {
                var li = document.createElement('li');
                var name = t && t.tool_name ? t.tool_name : 'unknown';
                var count = (t && typeof t.record_count === 'number') ? t.record_count : 0;
                var ms = (t && typeof t.latency_ms === 'number') ? t.latency_ms : null;
                var success = t && t.success !== false;
                var summary = name + ' — ' + count + ' record' + (count === 1 ? '' : 's');
                if (ms !== null) summary += ' (' + ms + ' ms)';
                if (!success) summary += ' (failed: ' + (t.error || 'error') + ')';
                li.textContent = summary;
                if (!success) li.style.color = '#b91c1c';
                list.appendChild(li);
            });
            node.appendChild(list);

            var hint = document.createElement('div');
            hint.style.cssText = 'margin-top: 8px; color: #6b7280; font-size: 12px; font-style: italic;';
            hint.textContent = 'Review the chart manually — the agent will not make a claim it cannot verify.';
            node.appendChild(hint);
        }

        $messagesEl.appendChild(node);
        // Refusal node renders below the user's message; keep the user's
        // question anchored at the top so they can read it alongside
        // the explanation rather than scrolling back up.
    }

    /**
     * Post a client-side metric to the score endpoint, which forwards to
     * the Python agent's /score endpoint and attaches it to the existing
     * Langfuse trace. Fire-and-forget — never blocks the UI, never
     * surfaces errors to the user (observability must not break the
     * happy path).
     */
    function postScore(traceId, name, value) {
        if (!config.scoreEndpoint || !traceId) {
            return;
        }
        var headers = { 'Content-Type': 'application/json', 'Accept': 'application/json' };
        if (config.csrfToken) {
            headers['X-CSRF-Token'] = config.csrfToken;
        }
        fetch(config.scoreEndpoint, {
            method: 'POST',
            credentials: 'same-origin',
            headers: headers,
            body: JSON.stringify({ trace_id: traceId, name: name, value: value })
        }).catch(function () { /* swallow */ });
    }

    /**
     * Render a small "answered in N.Ns" footer under the most recent
     * assistant turn. Cheap UX signal that doesn't require leaving the
     * panel to see latency.
     */
    function appendLatencyFooter(seconds) {
        if (!$messagesEl) return;
        var node = document.createElement('div');
        node.className = 'copilot-latency-footer text-muted small';
        node.style.cssText = 'margin: 4px 0 12px 8px; font-style: italic;';
        node.textContent = 'answered in ' + seconds.toFixed(1) + 's';
        $messagesEl.appendChild(node);
    }

    function sendChat() {
        if (!config.endpoint) {
            appendStatus(labels.error || 'Configuration error.', 'error');
            return;
        }

        setBusy(true);
        var thinkingNode = appendStatus(labels.thinking || 'Thinking…', 'thinking');

        // t0 — click → fetch start. Bracket the entire user-perceived
        // latency window (network + agent + render).
        var t0 = (performance && performance.now) ? performance.now() : Date.now();

        var headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        };
        if (config.csrfToken) {
            headers['X-CSRF-Token'] = config.csrfToken;
        }

        fetch(config.endpoint, {
            method: 'POST',
            credentials: 'same-origin',
            headers: headers,
            body: JSON.stringify({ messages: messages })
        }).then(function (response) {
            return response.text().then(function (text) {
                var parsed = null;
                try { parsed = JSON.parse(text); } catch (e) { /* swallow */ }
                return { ok: response.ok, status: response.status, body: parsed };
            });
        }).then(function (result) {
            if (thinkingNode && thinkingNode.parentNode) {
                thinkingNode.parentNode.removeChild(thinkingNode);
            }

            var body = result.body;
            if (!body || typeof body !== 'object') {
                appendStatus(labels.error || 'Something went wrong.', 'error');
                return;
            }

            if (body.status === 'ok' && body.message && typeof body.message.content === 'string') {
                indexRetrievedRecords(body.retrieved_records);
                appendAssistant(body.message.content);
                messages.push({ role: 'assistant', content: body.message.content });
                updateVerifierStatus(
                    Array.isArray(body.claims) ? body.claims.length : 0,
                    typeof body.claims_stripped === 'number' ? body.claims_stripped : 0
                );
            } else if (body.status === 'refused') {
                indexRetrievedRecords([]);
                var reason = typeof body.reason === 'string' && body.reason
                    ? body.reason
                    : (labels.refused || 'The Co-Pilot declined to answer');
                appendRefusal(reason, body.searched || []);
            } else if (body.status === 'error' && typeof body.detail === 'string') {
                appendStatus(body.detail, 'error');
            } else {
                appendStatus(labels.error || 'Something went wrong.', 'error');
            }

            // t1 — DOM updated. Compute delta, surface in the panel,
            // log to console, and post to Langfuse score endpoint.
            var t1 = (performance && performance.now) ? performance.now() : Date.now();
            var deltaMs = Math.round(t1 - t0);
            var deltaSec = deltaMs / 1000;
            appendLatencyFooter(deltaSec);
            if (window.console && console.log) {
                console.log('[Co-Pilot] visual_latency_ms=' + deltaMs +
                    ' trace_id=' + (body && body.trace_id ? body.trace_id : 'null'));
            }
            if (body && body.trace_id) {
                postScore(body.trace_id, 'visual_latency_ms', deltaMs);
            }
        }).catch(function () {
            if (thinkingNode && thinkingNode.parentNode) {
                thinkingNode.parentNode.removeChild(thinkingNode);
            }
            appendStatus(labels.error || 'Something went wrong.', 'error');
        }).then(function () {
            setBusy(false);
            $input.focus();
        });
    }

    function pushUserAndSend(text) {
        var trimmed = (text || '').trim();
        if (trimmed === '') {
            return;
        }
        messages.push({ role: 'user', content: trimmed });
        appendUser(trimmed);
        sendChat();
    }

    // --- Wiring ---------------------------------------------------------

    /**
     * Show a "patient context changed" banner at the top of the chat
     * panel when the parent shell tells us via postMessage. Click the
     * refresh button → reload the iframe so we start fresh against the
     * new patient.
     */
    /**
     * Disable all interactive controls in the chat panel — used when the
     * patient context has changed and we're waiting for the user to
     * refresh. Cleaner than letting them type a message that would be
     * answered against a soon-to-be-discarded conversation.
     */
    function disableInteraction(reason) {
        if ($input) {
            $input.disabled = true;
            $input.placeholder = reason || 'Refresh to continue';
        }
        if ($send) $send.disabled = true;
        var starters = document.querySelectorAll('[data-copilot-starter]');
        for (var i = 0; i < starters.length; i++) {
            starters[i].disabled = true;
        }
    }

    /**
     * Show a "patient context changed" banner at the top of the chat
     * panel when the parent shell tells us via postMessage. Click the
     * refresh button → reload the iframe so we start fresh against the
     * new patient. Input is disabled while the banner is up — clicking
     * Refresh is the only forward path.
     */
    window.addEventListener('message', function (event) {
        if (!event.data || event.data.type !== 'oe-copilot/patient-changed') return;
        // Avoid duplicate banners if multiple events fire.
        if (document.getElementById('copilot-patient-changed-banner')) return;

        var banner = document.createElement('div');
        banner.id = 'copilot-patient-changed-banner';
        banner.style.cssText =
            'background:#fef3c7; border-bottom:1px solid #fbbf24; ' +
            'padding:10px 14px; font-size:13px; color:#78350f; ' +
            'display:flex; align-items:center; justify-content:space-between; gap:12px;';

        var msg = document.createElement('span');
        msg.textContent = 'Patient context changed in OpenEMR. Refresh chat to reason about the new patient.';

        var btn = document.createElement('button');
        btn.type = 'button';
        btn.textContent = 'Refresh';
        btn.style.cssText =
            'background:#f59e0b; color:#fff; border:none; ' +
            'padding:6px 12px; border-radius:4px; font-size:12px; ' +
            'font-weight:600; cursor:pointer; flex-shrink:0;';
        btn.addEventListener('click', function () {
            // Tell the parent we're reloading BEFORE the reload fires.
            // Parent sets `chatRenderedForPid = null` on receipt, which
            // suppresses the polling-based banner-trigger in the brief
            // window between reload start and the new page's
            // `panel-loaded` message arriving. Without this, the parent
            // sees `shellPid (Phil) ≠ chatRenderedForPid (Carlos)` again
            // and re-fires the banner mid-reload — race condition.
            try {
                window.parent.postMessage(
                    { type: 'oe-copilot/about-to-reload' }, '*'
                );
            } catch (e) { /* swallow */ }
            window.location.reload();
        });

        banner.appendChild(msg);
        banner.appendChild(btn);

        // Insert at the very top of body so it's the first thing the
        // clinician sees in the chat panel.
        document.body.insertBefore(banner, document.body.firstChild);

        // Lock the chat — the only meaningful action now is to refresh.
        disableInteraction('Refresh to continue with the new patient');
    });

    if ($form) {
        $form.addEventListener('submit', function (event) {
            event.preventDefault();
            var value = $input.value;
            $input.value = '';
            pushUserAndSend(value);
        });
    }

    Array.prototype.forEach.call(
        document.querySelectorAll('[data-copilot-starter]'),
        function (button) {
            button.addEventListener('click', function () {
                var prompt = button.getAttribute('data-copilot-starter') || '';
                pushUserAndSend(prompt);
            });
        }
    );

    if ($messagesEl) {
        $messagesEl.addEventListener('click', function (event) {
            var badge = event.target && event.target.closest
                ? event.target.closest('.copilot-citation')
                : null;
            if (badge) {
                event.preventDefault();
                event.stopPropagation();
                event.stopImmediatePropagation();
                showCitationDetails(badge);
            }
        });
    }
}());
