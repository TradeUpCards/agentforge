<?php

/**
 * Clinical Co-Pilot client-side metric submission endpoint.
 *
 * Browser → here → Python agent's /score → Langfuse trace.
 *
 * Used by chat-panel.js to record visual-render latency (click event to
 * DOM update) onto the same Langfuse trace that captured the agent-side
 * latency for the request. End-to-end timing lives next to agent timing.
 *
 * Security posture mirrors chat.php — session + ACL + CSRF + HMAC.
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
use OpenEMR\Common\Logging\SystemLogger;
use OpenEMR\Common\Session\SessionWrapperFactory;
use OpenEMR\Modules\ClinicalCopilot\Bootstrap;

header('Content-Type: application/json');

$logger = new SystemLogger();

try {
    $session = SessionWrapperFactory::getInstance()->getActiveSession();

    $userId = (int) ($session->get('authUserID') ?? 0);
    $patientId = (int) ($session->get('pid') ?? 0);

    if ($userId <= 0) {
        http_response_code(401);
        echo json_encode(['status' => 'error', 'detail' => 'Authentication required.']);
        exit;
    }

    if (!AclMain::aclCheckCore('patients', 'med')) {
        http_response_code(403);
        echo json_encode(['status' => 'error', 'detail' => 'forbidden']);
        exit;
    }

    if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST') {
        http_response_code(405);
        echo json_encode(['status' => 'error', 'detail' => 'Method not allowed.']);
        exit;
    }

    $csrfToken = $_SERVER['HTTP_X_CSRF_TOKEN'] ?? '';
    if (!is_string($csrfToken) || !CsrfUtils::verifyCsrfToken($csrfToken, $session)) {
        http_response_code(403);
        echo json_encode(['status' => 'error', 'detail' => 'Invalid CSRF token.']);
        exit;
    }

    if ($patientId <= 0) {
        http_response_code(400);
        echo json_encode(['status' => 'error', 'detail' => 'No active patient in session.']);
        exit;
    }

    $rawBody = (string) file_get_contents('php://input', false, null, 0, 4096);
    $decoded = json_decode($rawBody, true);
    if (!is_array($decoded)) {
        http_response_code(400);
        echo json_encode(['status' => 'error', 'detail' => 'Invalid JSON body.']);
        exit;
    }

    $traceId = $decoded['trace_id'] ?? null;
    $name = $decoded['name'] ?? null;
    $value = $decoded['value'] ?? null;
    $comment = $decoded['comment'] ?? null;

    if (!is_string($traceId) || $traceId === '' || !is_string($name) || $name === '') {
        http_response_code(400);
        echo json_encode(['status' => 'error', 'detail' => 'trace_id and name are required.']);
        exit;
    }
    if (!is_numeric($value)) {
        http_response_code(400);
        echo json_encode(['status' => 'error', 'detail' => 'value must be numeric.']);
        exit;
    }
    if ($comment !== null && !is_string($comment)) {
        http_response_code(400);
        echo json_encode(['status' => 'error', 'detail' => 'comment must be a string.']);
        exit;
    }

    $secret = Bootstrap::getHmacSecret();
    if ($secret === null) {
        $logger->error('Clinical Co-Pilot HMAC secret is not configured (OPENEMR_HMAC_SECRET).');
        http_response_code(500);
        echo json_encode(['status' => 'error', 'detail' => 'Internal configuration error.']);
        exit;
    }

    // HMAC payload must match agent.verify_score_hmac() byte-for-byte.
    $valueFloat = (float) $value;
    $payload = $userId . '|' . $patientId . '|' . $traceId . '|' . $name . '|' . $valueFloat;
    $hmac = hash_hmac('sha256', $payload, $secret);

    $body = json_encode([
        'user_id' => $userId,
        'patient_id' => $patientId,
        'hmac' => $hmac,
        'trace_id' => $traceId,
        'name' => $name,
        'value' => $valueFloat,
        'comment' => $comment,
    ], JSON_THROW_ON_ERROR);

    $url = Bootstrap::getAgentBaseUrl() . '/score';
    $ch = curl_init($url);
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_POST => true,
        CURLOPT_POSTFIELDS => $body,
        CURLOPT_HTTPHEADER => ['Content-Type: application/json', 'Accept: application/json'],
        CURLOPT_TIMEOUT => 10,
        CURLOPT_CONNECTTIMEOUT => 5,
    ]);
    $response = curl_exec($ch);
    $httpStatus = (int) curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
    curl_close($ch);

    if ($response === false) {
        // Score is non-essential — log and return ok rather than failing the
        // user's flow. Observability should never break the user path.
        $logger->error('Clinical Co-Pilot score forward failed (transport).');
        echo json_encode(['status' => 'ok', 'note' => 'score-forward-failed']);
        exit;
    }

    http_response_code(($httpStatus >= 200 && $httpStatus < 300) ? 200 : $httpStatus);
    echo is_string($response) ? $response : json_encode(['status' => 'ok']);
} catch (Throwable $e) {
    $logger->error('Clinical Co-Pilot score handler failed', ['exception' => $e]);
    http_response_code(500);
    echo json_encode(['status' => 'error', 'detail' => 'Internal error.']);
}
