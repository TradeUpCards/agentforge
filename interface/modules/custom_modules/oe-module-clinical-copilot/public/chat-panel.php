<?php

/**
 * Clinical Co-Pilot chat panel.
 *
 * Loaded when the clinician clicks the "Clinical Co-Pilot" tab in the
 * patient chart. Renders a minimal chat UI; all conversational state is
 * held client-side in `chat-panel.js` and round-trips through
 * `chat.php` on every send.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    AgentForge Team
 * @copyright Copyright (c) 2026 AgentForge
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

require_once __DIR__ . '/../../../../globals.php';

use OpenEMR\Common\Acl\AclMain;
use OpenEMR\Common\Csrf\CsrfUtils;
use OpenEMR\Common\Session\SessionWrapperFactory;
use OpenEMR\Core\Header;

$session = SessionWrapperFactory::getInstance()->getActiveSession();

if (!AclMain::aclCheckCore('patients', 'med')) {
    http_response_code(403);
    echo xlt('Access denied.');
    exit;
}

$pid = $session->get('pid');
if (empty($pid)) {
    echo xlt('You must select a patient before opening the Clinical Co-Pilot.');
    exit;
}

$csrfToken = CsrfUtils::collectCsrfToken(session: $session);
?>
<!doctype html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport"
        content="width=device-width, user-scalable=no, initial-scale=1.0, maximum-scale=1.0, minimum-scale=1.0">
    <title><?php echo xlt('Clinical Co-Pilot'); ?></title>
    <?php Header::setupHeader(['common']); ?>
    <link rel="stylesheet" href="chat-panel.css">
</head>
<body class="container-fluid">
    <div class="copilot-app d-flex flex-column">
        <header class="copilot-header py-2 border-bottom">
            <h3 class="m-0"><?php echo xlt('Clinical Co-Pilot'); ?></h3>
            <small class="text-muted">
                <?php echo xlt('Patient'); ?>: <span id="copilot-pid"><?php echo text((string) $pid); ?></span>
            </small>
        </header>

        <section id="copilot-messages"
                 class="copilot-messages flex-grow-1 p-3"
                 aria-live="polite"
                 aria-label="<?php echo xla('Conversation messages'); ?>">
            <div class="copilot-empty-state text-muted text-center">
                <?php echo xlt('Ask anything about this patient, or pick a starter below.'); ?>
            </div>
        </section>

        <section class="copilot-starters p-2 border-top">
            <button type="button"
                    class="btn btn-outline-primary btn-sm mr-2"
                    data-copilot-starter="<?php echo attr(xl('Generate a pre-visit brief for this patient.')); ?>">
                <?php echo xlt('Pre-visit brief'); ?>
            </button>
            <button type="button"
                    class="btn btn-outline-primary btn-sm"
                    data-copilot-starter="<?php echo attr(xl('Show what\'s changed since the last visit.')); ?>">
                <?php echo xlt("What's changed"); ?>
            </button>
        </section>

        <form id="copilot-form" class="copilot-form p-2 border-top d-flex" autocomplete="off">
            <input type="text"
                   id="copilot-input"
                   class="form-control mr-2"
                   placeholder="<?php echo xla('Ask the Co-Pilot…'); ?>"
                   maxlength="2000"
                   aria-label="<?php echo xla('Message'); ?>">
            <button type="submit" id="copilot-send" class="btn btn-primary">
                <?php echo xlt('Send'); ?>
            </button>
        </form>
    </div>

    <script>
        window.OE_COPILOT_CONFIG = {
            endpoint: <?php echo js_escape($GLOBALS['webroot'] . '/interface/modules/custom_modules/oe-module-clinical-copilot/public/chat.php'); ?>,
            csrfToken: <?php echo js_escape($csrfToken); ?>,
            patientId: <?php echo js_escape((string) $pid); ?>,
            labels: {
                thinking: <?php echo js_escape(xl('Thinking…')); ?>,
                refused: <?php echo js_escape(xl('The Co-Pilot declined to answer')); ?>,
                error: <?php echo js_escape(xl('Something went wrong. Please retry.')); ?>,
                you: <?php echo js_escape(xl('You')); ?>,
                copilot: <?php echo js_escape(xl('Co-Pilot')); ?>,
                source: <?php echo js_escape(xl('Source')); ?>,
                noRecord: <?php echo js_escape(xl('No record details available')); ?>
            }
        };
    </script>
    <script src="chat-panel.js"></script>
</body>
</html>
