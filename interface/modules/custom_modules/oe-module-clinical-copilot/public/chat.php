<?php

/**
 * Clinical Co-Pilot chat endpoint.
 *
 * AJAX target for the chat panel. Bootstraps OpenEMR globals (so the
 * session, ACL, and CSRF helpers are wired up) and delegates the request
 * to `CoPilotController::handle()`.
 *
 * Logical URL exposed to the browser:
 *   {webroot}/interface/modules/custom_modules/oe-module-clinical-copilot/public/chat.php
 *
 * The original spec referred to `/apis/copilot/chat`; that path lives
 * under OpenEMR's OAuth/JWT-protected REST dispatcher, which would
 * bypass the session-based authentication this module relies on. The
 * chat panel is an in-app, session-authenticated UI, so the endpoint is
 * implemented as a regular module entrypoint and protected explicitly
 * via session, ACL, and CSRF checks (see CoPilotController).
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    AgentForge Team
 * @copyright Copyright (c) 2026 AgentForge
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

// Bootstrap OpenEMR's session, ACL, and CSRF helpers. Same pattern as
// `library/ajax/dated_reminders_counter.php` and other in-app AJAX
// endpoints — let globals.php read the existing session from the request
// cookie. No `$sessionAllowWrite` (the controller doesn't write session
// state) and no `$_GET['site']` override (the existing session already
// knows which site it belongs to).
require_once __DIR__ . '/../../../../globals.php';

use OpenEMR\Modules\ClinicalCopilot\Controller\CoPilotController;

(new CoPilotController())->handle();
