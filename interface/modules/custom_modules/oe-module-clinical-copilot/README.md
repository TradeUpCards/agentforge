# AgentForge Clinical Co-Pilot — OpenEMR Module

OpenEMR module that adds a "Clinical Co-Pilot" tab to the patient chart. The
tab opens a chat panel that talks to the AgentForge Python agent service for
pre-visit briefs, change summaries, and free-text clinical questions.

## What this module does

1. Subscribes to `PatientMenuEvent::MENU_UPDATE` and injects a single
   "Clinical Co-Pilot" entry into the patient chart's tab menu.
2. The tab loads `public/chat-panel.php`, which renders a Bootstrap chat
   UI with two starter buttons ("Pre-visit brief", "What's changed") and
   a free-text input.
3. Every send POSTs the full conversation history to
   `public/chat.php`, which:
   - verifies the OpenEMR session (`$_SESSION['authUserID']`),
   - enforces `AclMain::aclCheckCore('patients', 'med')`,
   - validates the OpenEMR CSRF token (sent as `X-CSRF-Token`),
   - derives `patient_id` from `$_SESSION['pid']` (never from the
     request body — see AUDIT.md S-2),
   - computes the HMAC the agent expects, and
   - forwards `{user_id, patient_id, hmac, messages}` to the Python
     agent at `${AGENT_BASE_URL}/chat`.
4. The agent's JSON response (`status: "ok" | "refused" | "error"`) is
   returned to the browser unchanged.

## Configuration

Configuration is read from environment variables at request time so the
same module can run in dev and prod without code changes:

| Variable                | Default                | Purpose |
| ----------------------- | ---------------------- | ------- |
| `AGENT_BASE_URL`        | `http://agent:8000`    | Base URL of the Python agent service. Override with `http://host.docker.internal:8000` for local dev. |
| `OPENEMR_HMAC_SECRET`   | _required_             | Shared secret used to authenticate the OpenEMR -> agent call. The Python agent reads the same env var. |

If `OPENEMR_HMAC_SECRET` is unset the controller returns a generic 500
error (the secret is never echoed to the client).

## Installation

1. Drop the module directory at
   `interface/modules/custom_modules/oe-module-clinical-copilot/`.
2. Log in as an admin and register the module via
   **Modules → Manage Modules**, then enable it.
3. Set `OPENEMR_HMAC_SECRET` (and optionally `AGENT_BASE_URL`) in the
   OpenEMR container environment (`/opt/agentforge/.env` on the
   deployed stack).
4. Open any patient chart — a "Clinical Co-Pilot" tab should appear in
   the patient menu.

## Security model

- Session check, ACL check, and CSRF check are all explicit in
  `CoPilotController::dispatch()` (AUDIT.md S-1: never rely on
  route-level auth alone).
- `patient_id` is sourced from the session, never from the request
  body (AUDIT.md S-2).
- There is no `skip_acl_check` escape hatch (AUDIT.md S-3).
- Raw exception messages are never returned to the browser; failures
  surface as `{"status":"error","detail":"…generic message…"}`
  (ARCHITECTURE.md §7).

## Files

```
oe-module-clinical-copilot/
├── info.txt
├── openemr.bootstrap.php
├── README.md
├── public/
│   ├── chat-panel.php   # rendered when the menu tab is clicked
│   ├── chat-panel.js    # client-side conversation state + rendering
│   ├── chat-panel.css   # panel styling
│   └── chat.php         # AJAX endpoint -> CoPilotController
└── src/
    ├── Bootstrap.php
    ├── Controller/
    │   └── CoPilotController.php
    └── EventSubscriber/
        └── PatientMenuSubscriber.php
```
