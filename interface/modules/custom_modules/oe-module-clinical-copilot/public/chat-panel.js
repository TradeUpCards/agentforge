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
            return '<p>' + block.replace(/\n/g, '<br>') + '</p>';
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
        // Bracketed form first so we don't double-wrap.
        html = html.replace(/\[([a-z_][a-z0-9_]*:[A-Za-z0-9_\-]+)\]/g, function (_match, id) {
            return renderCitationBadge(id);
        });
        // Bare form: only when not already inside a tag attribute.
        html = html.replace(
            /(^|[\s(>])([a-z_][a-z0-9_]{2,}:[0-9]+)(?=$|[\s.,;:)<])/g,
            function (_match, prefix, id) {
                return prefix + renderCitationBadge(id);
            }
        );
        return html;
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
        $messagesEl.scrollTop = $messagesEl.scrollHeight;
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
        $messagesEl.scrollTop = $messagesEl.scrollHeight;
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

    function showCitationDetails(badge) {
        var id = badge.getAttribute('data-record-id');
        var record = lastRetrievedRecords[id];

        // Toggle: if a popover already exists on this badge, remove it.
        var existing = badge.nextElementSibling;
        if (existing && existing.classList.contains('copilot-citation__popover')) {
            existing.parentNode.removeChild(existing);
            return;
        }
        // Close any other open popover.
        Array.prototype.forEach.call(
            document.querySelectorAll('.copilot-citation__popover'),
            function (el) { el.parentNode.removeChild(el); }
        );

        var popover = document.createElement('div');
        popover.className = 'copilot-citation__popover';

        if (!record) {
            popover.textContent = labels.noRecord || 'No record details available';
        } else {
            var header = document.createElement('div');
            header.className = 'copilot-citation__header';
            header.textContent = (labels.source || 'Source') + ': ' + id +
                (record.citation_strength ? ' (' + record.citation_strength + ')' : '');
            popover.appendChild(header);

            if (record.fields && typeof record.fields === 'object') {
                var dl = document.createElement('dl');
                Object.keys(record.fields).forEach(function (key) {
                    var dt = document.createElement('dt');
                    dt.textContent = key;
                    var dd = document.createElement('dd');
                    dd.textContent = String(record.fields[key]);
                    dl.appendChild(dt);
                    dl.appendChild(dd);
                });
                popover.appendChild(dl);
            }
        }

        badge.parentNode.insertBefore(popover, badge.nextSibling);
    }

    function sendChat() {
        if (!config.endpoint) {
            appendStatus(labels.error || 'Configuration error.', 'error');
            return;
        }

        setBusy(true);
        var thinkingNode = appendStatus(labels.thinking || 'Thinking…', 'thinking');

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
            } else if (body.status === 'refused') {
                indexRetrievedRecords([]);
                var reason = typeof body.reason === 'string' && body.reason
                    ? body.reason
                    : (labels.refused || 'The Co-Pilot declined to answer');
                appendStatus(reason, 'refused');
            } else if (body.status === 'error' && typeof body.detail === 'string') {
                appendStatus(body.detail, 'error');
            } else {
                appendStatus(labels.error || 'Something went wrong.', 'error');
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
            var target = event.target;
            if (target && target.classList && target.classList.contains('copilot-citation')) {
                event.preventDefault();
                showCitationDetails(target);
            }
        });
    }
}());
