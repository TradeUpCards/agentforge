<?php

/**
 * Co-Pilot chat controller.
 *
 * Receives the AJAX POST issued by the chat panel, enforces the OpenEMR
 * session and ACL, derives the user/patient identity authoritatively from
 * the session (NEVER from the request body — see AUDIT.md S-2), computes
 * the HMAC the Python agent service expects, and forwards the request to
 * the agent. The agent's JSON response is returned to the browser as-is.
 *
 * Security notes:
 *  - Session check + ACL check are explicit (AUDIT.md S-1: never rely on
 *    route-level auth alone).
 *  - `patient_id` is sourced from `$_SESSION['pid']`, never from the
 *    request body (AUDIT.md S-2).
 *  - There is no `skip_acl_check` escape hatch (AUDIT.md S-3).
 *  - Raw exception messages are never returned to the browser
 *    (ARCHITECTURE.md §7).
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    AgentForge Team
 * @copyright Copyright (c) 2026 AgentForge
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\Controller;

use OpenEMR\Common\Acl\AclMain;
use OpenEMR\Common\Csrf\CsrfUtils;
use OpenEMR\Common\Logging\SystemLogger;
use OpenEMR\Common\Session\SessionWrapperFactory;
use OpenEMR\Modules\ClinicalCopilot\Bootstrap;
use Psr\Log\LoggerInterface;
use Throwable;

final class CoPilotController
{
    // Routes UI chat traffic through the supervisor + 2-workers + responder
    // graph (PRD §4 Core requirement). The legacy /chat endpoint bypasses
    // the supervisor and remains available for direct callers + the eval
    // suite's W1-baseline cases (01-30); /graph_chat is the path the UI
    // exercises so the graph is on the production runtime path.
    private const AGENT_CHAT_PATH = '/graph_chat';
    // Bumped from 60s on 2026-05-08 after the W2 supervisor + responder graph
    // landed (MR !37). Real-LLM end-to-end time on /graph_chat:
    //   supervisor.decide (×2 hops Haiku, ~5s each) + worker tools (~1s) +
    //   responder synthesis (Haiku ~21s) + verifier (~1s) +
    //   Sonnet escalation when verifier rejects (~25-30s) ≈ 60s.
    // 60s was hitting the wall exactly (curl timeout after 60002ms with 0
    // bytes received, observed 2026-05-08). 120s gives headroom for
    // worst-case escalation paths without sitting on the connection
    // indefinitely. Tighten back once the synthesis prompt is shrunk or
    // the verifier's substring matcher stops over-rejecting.
    private const AGENT_TIMEOUT_SECONDS = 120;
    private const MAX_REQUEST_BYTES = 1_048_576; // 1 MiB cap on inbound JSON.

    private readonly LoggerInterface $logger;

    public function __construct(?LoggerInterface $logger = null)
    {
        $this->logger = $logger ?? new SystemLogger();
    }

    /**
     * Handle the chat POST. Always emits a JSON response and exits.
     *
     * The caller (the public PHP entrypoint) is responsible for booting
     * OpenEMR globals before invoking this method so the session and
     * ACL helpers are available.
     */
    public function handle(): void
    {
        header('Content-Type: application/json');

        try {
            $this->dispatch();
        } catch (Throwable $e) {
            // Log full detail server-side, return generic message to client.
            $this->logger->error(
                'Clinical Co-Pilot chat handler failed',
                ['exception' => $e]
            );
            http_response_code(500);
            echo json_encode([
                'status' => 'error',
                'detail' => 'The agent encountered an internal error. Please retry.',
            ]);
        }
    }

    private function dispatch(): void
    {
        // 1. Session-authenticated request only.
        // OpenEMR namespaces session data inside a Symfony session bag (the
        // raw $_SESSION top level only has 'OpenEMR', '_sf2_meta',
        // '_symfony_flashes' — auth lives inside the bag). Read via the
        // factory to match what chat-panel.php and other in-app pages do.
        $session = SessionWrapperFactory::getInstance()->getActiveSession();
        $userId = (int) ($session->get('authUserID') ?? 0);
        $patientId = (int) ($session->get('pid') ?? 0);

        if ($userId <= 0) {
            http_response_code(401);
            echo json_encode([
                'status' => 'error',
                'detail' => 'Authentication required.',
            ]);
            return;
        }

        // 2. ACL check (AUDIT.md S-1).
        if (!AclMain::aclCheckCore('patients', 'med')) {
            http_response_code(403);
            echo json_encode([
                'status' => 'error',
                'detail' => 'forbidden',
            ]);
            return;
        }

        // 3. Method check.
        if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST') {
            http_response_code(405);
            echo json_encode([
                'status' => 'error',
                'detail' => 'Method not allowed.',
            ]);
            return;
        }

        // 4. CSRF check. The chat panel embeds a token into the page and
        //    the JS forwards it via the X-CSRF-Token header.
        $csrfToken = $_SERVER['HTTP_X_CSRF_TOKEN'] ?? '';
        $session = SessionWrapperFactory::getInstance()->getActiveSession();
        if (!is_string($csrfToken) || !CsrfUtils::verifyCsrfToken($csrfToken, $session)) {
            http_response_code(403);
            echo json_encode([
                'status' => 'error',
                'detail' => 'Invalid CSRF token.',
            ]);
            return;
        }

        // 5. Patient context must be present (AUDIT.md S-2: never trust
        //    a `patient_id` carried in the request body).
        if ($patientId <= 0) {
            http_response_code(400);
            echo json_encode([
                'status' => 'error',
                'detail' => 'No active patient in session.',
            ]);
            return;
        }

        // 6. Read request body.
        $rawBody = (string) file_get_contents('php://input', false, null, 0, self::MAX_REQUEST_BYTES + 1);
        if (strlen($rawBody) > self::MAX_REQUEST_BYTES) {
            http_response_code(413);
            echo json_encode([
                'status' => 'error',
                'detail' => 'Request payload too large.',
            ]);
            return;
        }

        $decoded = json_decode($rawBody, true);
        if (!is_array($decoded)) {
            http_response_code(400);
            echo json_encode([
                'status' => 'error',
                'detail' => 'Invalid JSON body.',
            ]);
            return;
        }

        $messages = $this->normalizeMessages($decoded['messages'] ?? null);
        if ($messages === null) {
            http_response_code(400);
            echo json_encode([
                'status' => 'error',
                'detail' => 'Invalid messages payload.',
            ]);
            return;
        }

        // 7. Resolve HMAC secret.
        $secret = Bootstrap::getHmacSecret();
        if ($secret === null) {
            $this->logger->error(
                'Clinical Co-Pilot HMAC secret is not configured (OPENEMR_HMAC_SECRET).'
            );
            http_response_code(500);
            echo json_encode([
                'status' => 'error',
                'detail' => 'The agent encountered an internal error. Please retry.',
            ]);
            return;
        }

        // Replay protection: the timestamp is signed alongside the rest of
        // the payload; the agent rejects any request whose timestamp is
        // more than 30 seconds off its clock in either direction. See
        // agent/agent.py:_HMAC_MAX_AGE_SECONDS for the window value.
        $timestamp = time();

        $hmac = $this->computeHmac($userId, $patientId, $timestamp, $messages, $secret);

        // session_id is browser-generated (UUID per chat-panel-open) and used
        // only as the Langfuse trace's session_id for multi-turn grouping.
        // Intentionally NOT in the HMAC payload — observability metadata,
        // not security-sensitive. Tampering would only corrupt trace
        // grouping, no auth/PHI impact.
        $sessionId = $decoded['session_id'] ?? null;
        if (is_string($sessionId) && $sessionId !== '' && strlen($sessionId) <= 128) {
            $sessionIdNormalized = $sessionId;
        } else {
            $sessionIdNormalized = null;
        }

        $payload = [
            'user_id' => $userId,
            'patient_id' => $patientId,
            'timestamp' => $timestamp,
            'hmac' => $hmac,
            'messages' => $messages,
            'session_id' => $sessionIdNormalized,
        ];

        // 8. Forward to the Python agent service.
        $agentUrl = Bootstrap::getAgentBaseUrl() . self::AGENT_CHAT_PATH;
        $result = $this->forwardToAgent($agentUrl, $payload);

        http_response_code($result['status']);
        echo $result['body'];
    }

    /**
     * Validate and normalize the inbound messages array.
     *
     * Expected shape: `[{"role": "user|assistant|system", "content": "..."}]`.
     *
     * Returns null when the input is malformed.
     *
     * @return list<array{role: string, content: string}>|null
     */
    private function normalizeMessages(mixed $raw): ?array
    {
        if (!is_array($raw)) {
            return null;
        }

        $allowedRoles = ['user', 'assistant', 'system'];
        $normalized = [];
        foreach ($raw as $message) {
            if (!is_array($message)) {
                return null;
            }
            $role = $message['role'] ?? null;
            $content = $message['content'] ?? null;
            if (!is_string($role) || !in_array($role, $allowedRoles, true)) {
                return null;
            }
            if (!is_string($content)) {
                return null;
            }
            $normalized[] = ['role' => $role, 'content' => $content];
        }

        if ($normalized === []) {
            return null;
        }

        return $normalized;
    }

    /**
     * Compute the HMAC the Python agent expects.
     *
     * Layout (must match agent.py:verify_hmac exactly):
     *   payload = "{user_id}|{patient_id}|{timestamp}|{m1.content}|{m2.content}|..."
     *   hmac    = hex(HMAC-SHA256(secret, payload))
     *
     * The timestamp is included inside the signed bytes so a captured
     * request body cannot be replayed past the 30-second freshness window
     * the Python agent enforces (`_HMAC_MAX_AGE_SECONDS`). Replay protection
     * tracks back to the 2026-05-02 ai-security-review production blocker.
     *
     * @param list<array{role: string, content: string}> $messages
     */
    private function computeHmac(int $userId, int $patientId, int $timestamp, array $messages, string $secret): string
    {
        $payload = $userId . '|' . $patientId . '|' . $timestamp . '|' . implode('|', array_map(
            static fn (array $m): string => $m['content'],
            $messages
        ));

        return hash_hmac('sha256', $payload, $secret);
    }

    /**
     * POST the payload to the agent service and return the raw response.
     *
     * @param array<string, mixed> $payload
     * @return array{status: int, body: string}
     */
    private function forwardToAgent(string $url, array $payload): array
    {
        $body = json_encode($payload, JSON_THROW_ON_ERROR);

        $ch = curl_init($url);
        if ($ch === false) {
            throw new \RuntimeException('Unable to initialize cURL handle.');
        }

        try {
            curl_setopt_array($ch, [
                CURLOPT_RETURNTRANSFER => true,
                CURLOPT_POST => true,
                CURLOPT_POSTFIELDS => $body,
                CURLOPT_HTTPHEADER => [
                    'Content-Type: application/json',
                    'Accept: application/json',
                ],
                CURLOPT_TIMEOUT => self::AGENT_TIMEOUT_SECONDS,
                CURLOPT_CONNECTTIMEOUT => 10,
                CURLOPT_FAILONERROR => false,
            ]);

            $response = curl_exec($ch);
            if ($response === false) {
                $err = curl_error($ch);
                $this->logger->error(
                    'Clinical Co-Pilot agent call failed at transport layer',
                    ['curl_error' => $err]
                );
                return [
                    'status' => 502,
                    'body' => json_encode([
                        'status' => 'error',
                        'detail' => 'The agent service is unreachable. Please retry.',
                    ]),
                ];
            }

            $httpStatus = (int) curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
            if ($httpStatus < 200 || $httpStatus >= 600) {
                $httpStatus = 502;
            }

            return [
                'status' => $httpStatus,
                'body' => is_string($response) ? $response : '',
            ];
        } finally {
            curl_close($ch);
        }
    }
}
