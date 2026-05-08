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
use OpenEMR\Common\Database\QueryUtils;
use OpenEMR\Common\Logging\SystemLogger;
use OpenEMR\Common\Session\SessionWrapperFactory;
use OpenEMR\Modules\ClinicalCopilot\Bootstrap;
use OpenEMR\Modules\ClinicalCopilot\ExtractionStatus;
use OpenEMR\Modules\ClinicalCopilot\Service\AgentClient;
use OpenEMR\Modules\ClinicalCopilot\Service\DocumentPathResolver;
use OpenEMR\Modules\ClinicalCopilot\Service\ExtractedFieldsWriter;
use OpenEMR\Modules\ClinicalCopilot\Service\PersonaMap;
use OpenEMR\Modules\ClinicalCopilot\Service\RoundtripService;
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

    private readonly AgentClient $agentClient;

    private readonly DocumentPathResolver $pathResolver;

    private readonly RoundtripService $roundtripService;

    private readonly ExtractedFieldsWriter $fieldsWriter;

    public function __construct(
        ?LoggerInterface $logger = null,
        ?AgentClient $agentClient = null,
        ?DocumentPathResolver $pathResolver = null,
        ?RoundtripService $roundtripService = null,
        ?ExtractedFieldsWriter $fieldsWriter = null,
    ) {
        $this->logger           = $logger ?? new SystemLogger();
        $this->agentClient      = $agentClient ?? new AgentClient($this->logger);
        $this->pathResolver     = $pathResolver ?? new DocumentPathResolver($this->logger);
        $this->roundtripService = $roundtripService ?? new RoundtripService($this->logger);
        $this->fieldsWriter     = $fieldsWriter ?? new ExtractedFieldsWriter($this->logger);
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
     * Handle a reprocess POST.  Always emits a JSON response and exits.
     *
     * Caller (reprocess.php) is responsible for booting OpenEMR globals.
     *
     * Expected: POST with JSON body {"extraction_id": <int>}
     *
     * Security controls:
     *  - Session authenticated (userId from session, never from body).
     *  - ACL: aclCheckCore('patients', 'med').
     *  - CSRF token in X-CSRF-Token header.
     *  - Patient-id horizontal-escalation check: extraction.patient_id must
     *    match session pid (prevents one patient's session from reprocessing
     *    another patient's document).
     *  - extraction.is_active = 1 required (only the current active attempt
     *    can be reprocessed; superseded rows are immutable).
     */
    public function handleReprocess(): void
    {
        header('Content-Type: application/json');

        try {
            $this->dispatchReprocess();
        } catch (Throwable $e) {
            $this->logger->error('Clinical Co-Pilot reprocess handler failed', [
                'exception_class' => get_class($e),
                'file'            => basename($e->getFile()),
                'line'            => $e->getLine(),
            ]);
            http_response_code(500);
            echo json_encode([
                'status' => 'error',
                'detail' => 'The agent encountered an internal error. Please retry.',
            ]);
        }
    }

    // -----------------------------------------------------------------------
    // P4 R1 — approve / reject endpoints
    // -----------------------------------------------------------------------

    /**
     * Handle an approve POST.  Always emits a JSON response.
     *
     * Caller (approve_extraction.php) must boot OpenEMR globals first.
     *
     * Expected: POST with JSON body {"extraction_id": <int>}
     *
     * Preconditions:
     *   - status must be ExtractionStatus::PENDING_REVIEW (422 otherwise).
     *   - is_active = 1 (404 if not found or inactive).
     *   - patient_id stored on the row must match the session's sentinel pid.
     *
     * On success:
     *   - Runs RoundtripService::roundtripFromExtractionId (clinical tables written).
     *   - Transitions status to ExtractionStatus::APPROVED.
     *   - Returns {"status": "approved", "extraction_id": <int>}.
     *
     * Error codes:
     *   401 — not authenticated
     *   403 — ACL or CSRF failure, or patient-id mismatch
     *   404 — extraction not found or not active
     *   405 — not a POST
     *   413 — body too large
     *   422 — extraction not in pending_review (wrong state for approve)
     *   500 — internal error
     */
    public function handleApprove(): void
    {
        header('Content-Type: application/json');

        try {
            $this->dispatchApprove();
        } catch (Throwable $e) {
            $this->logger->error('Clinical Co-Pilot approve handler failed', [
                'exception_class' => get_class($e),
                'file'            => basename($e->getFile()),
                'line'            => $e->getLine(),
            ]);
            http_response_code(500);
            echo json_encode([
                'status' => 'error',
                'detail' => 'The server encountered an internal error. Please retry.',
            ]);
        }
    }

    /**
     * Handle a reject POST.  Always emits a JSON response.
     *
     * Caller (reject_extraction.php) must boot OpenEMR globals first.
     *
     * Expected: POST with JSON body {"extraction_id": <int>}
     *
     * Preconditions:
     *   - status must be ExtractionStatus::PENDING_REVIEW (422 otherwise).
     *   - is_active = 1 (404 if not found or inactive).
     *   - patient_id stored on the row must match the session's sentinel pid.
     *
     * On success:
     *   - Transitions status to ExtractionStatus::REJECTED (terminal — no round-trip).
     *   - Returns {"status": "rejected", "extraction_id": <int>}.
     *
     * Error codes: same set as handleApprove.
     */
    public function handleReject(): void
    {
        header('Content-Type: application/json');

        try {
            $this->dispatchReject();
        } catch (Throwable $e) {
            $this->logger->error('Clinical Co-Pilot reject handler failed', [
                'exception_class' => get_class($e),
                'file'            => basename($e->getFile()),
                'line'            => $e->getLine(),
            ]);
            http_response_code(500);
            echo json_encode([
                'status' => 'error',
                'detail' => 'The server encountered an internal error. Please retry.',
            ]);
        }
    }

    private function dispatchApprove(): void
    {
        // 1. Session check.
        $session    = SessionWrapperFactory::getInstance()->getActiveSession();
        $userId     = (int) ($session->get('authUserID') ?? 0);
        $sessionPid = (int) ($session->get('pid') ?? 0);

        if ($userId <= 0) {
            http_response_code(401);
            echo json_encode(['status' => 'error', 'detail' => 'Authentication required.']);
            return;
        }

        // 2. ACL.
        if (!AclMain::aclCheckCore('patients', 'med')) {
            http_response_code(403);
            echo json_encode(['status' => 'error', 'detail' => 'forbidden']);
            return;
        }

        // 3. Method.
        if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST') {
            http_response_code(405);
            echo json_encode(['status' => 'error', 'detail' => 'Method not allowed.']);
            return;
        }

        // 4. CSRF.
        $csrfToken = $_SERVER['HTTP_X_CSRF_TOKEN'] ?? '';
        if (!is_string($csrfToken) || !CsrfUtils::verifyCsrfToken($csrfToken, $session)) {
            http_response_code(403);
            echo json_encode(['status' => 'error', 'detail' => 'Invalid CSRF token.']);
            return;
        }

        // 5. Patient context.
        if ($sessionPid <= 0) {
            http_response_code(400);
            echo json_encode(['status' => 'error', 'detail' => 'No active patient in session.']);
            return;
        }

        // 6. Parse body.
        $rawBody = (string) file_get_contents('php://input', false, null, 0, self::MAX_REQUEST_BYTES + 1);
        if (strlen($rawBody) > self::MAX_REQUEST_BYTES) {
            http_response_code(413);
            echo json_encode(['status' => 'error', 'detail' => 'Request payload too large.']);
            return;
        }

        $decoded = json_decode($rawBody, true);
        if (!is_array($decoded)) {
            http_response_code(400);
            echo json_encode(['status' => 'error', 'detail' => 'Invalid JSON body.']);
            return;
        }

        $extractionIdRaw = $decoded['extraction_id'] ?? null;
        if (!is_int($extractionIdRaw) || $extractionIdRaw <= 0) {
            http_response_code(400);
            echo json_encode(['status' => 'error', 'detail' => 'extraction_id must be a positive integer.']);
            return;
        }

        $extractionId = $extractionIdRaw;

        // 7. Load extraction row (active only).
        $rows = QueryUtils::fetchRecords(
            'SELECT `id`, `patient_id`, `doc_ref_id`, `doc_type`, `status`
               FROM `co_pilot_extractions`
              WHERE `id` = ?
                AND `is_active` = 1
              LIMIT 1',
            [$extractionId],
        );

        if ($rows === []) {
            http_response_code(404);
            echo json_encode(['status' => 'error', 'detail' => 'Extraction not found or not active.']);
            return;
        }

        $extraction = $rows[0];
        $storedPid  = (int) $extraction['patient_id'];

        // 8. Patient-id horizontal-escalation check.
        //    The stored patient_id is the SENTINEL pid; the session pid is real.
        //    Compare via PersonaMap::sentinelId (mirrors handleReprocess pattern).
        $expectedSentinelPid = PersonaMap::sentinelId($sessionPid);
        if ($expectedSentinelPid === null || $storedPid !== $expectedSentinelPid) {
            $this->logger->warning('ClinicalCopilot/approve: patient_id mismatch — possible horizontal escalation', [
                'extraction_id' => $extractionId,
            ]);
            http_response_code(403);
            echo json_encode(['status' => 'error', 'detail' => 'Patient context mismatch.']);
            return;
        }

        // 9. State guard: approve is only valid from ExtractionStatus::PENDING_REVIEW.
        //    Any other status (approved, rejected, error, refused) returns 422.
        $currentStatus = (string) $extraction['status'];
        if (!in_array($currentStatus, ExtractionStatus::approvableStatuses(), true)) {
            http_response_code(422);
            echo json_encode([
                'status'         => 'error',
                'detail'         => 'Extraction is not in pending_review state.',
                'current_status' => $currentStatus,
            ]);
            return;
        }

        // 10. Run round-trip from persisted extraction_json.
        $counts = $this->roundtripService->roundtripFromExtractionId(
            extractionId:  $extractionId,
            realPatientId: $sessionPid,
            fieldsWriter:  $this->fieldsWriter,
        );

        $this->logger->info('ClinicalCopilot: extraction approved', [
            'extraction_id' => $extractionId,
            'observations'  => $counts['observations'],
            'allergies'     => $counts['allergies'],
            'medications'   => $counts['medications'],
            'n_errors'      => count($counts['errors']),
        ]);

        http_response_code(200);
        echo json_encode([
            'status'        => ExtractionStatus::APPROVED,
            'extraction_id' => $extractionId,
            'observations'  => $counts['observations'],
            'allergies'     => $counts['allergies'],
            'medications'   => $counts['medications'],
        ]);
    }

    private function dispatchReject(): void
    {
        // 1. Session check.
        $session    = SessionWrapperFactory::getInstance()->getActiveSession();
        $userId     = (int) ($session->get('authUserID') ?? 0);
        $sessionPid = (int) ($session->get('pid') ?? 0);

        if ($userId <= 0) {
            http_response_code(401);
            echo json_encode(['status' => 'error', 'detail' => 'Authentication required.']);
            return;
        }

        // 2. ACL.
        if (!AclMain::aclCheckCore('patients', 'med')) {
            http_response_code(403);
            echo json_encode(['status' => 'error', 'detail' => 'forbidden']);
            return;
        }

        // 3. Method.
        if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST') {
            http_response_code(405);
            echo json_encode(['status' => 'error', 'detail' => 'Method not allowed.']);
            return;
        }

        // 4. CSRF.
        $csrfToken = $_SERVER['HTTP_X_CSRF_TOKEN'] ?? '';
        if (!is_string($csrfToken) || !CsrfUtils::verifyCsrfToken($csrfToken, $session)) {
            http_response_code(403);
            echo json_encode(['status' => 'error', 'detail' => 'Invalid CSRF token.']);
            return;
        }

        // 5. Patient context.
        if ($sessionPid <= 0) {
            http_response_code(400);
            echo json_encode(['status' => 'error', 'detail' => 'No active patient in session.']);
            return;
        }

        // 6. Parse body.
        $rawBody = (string) file_get_contents('php://input', false, null, 0, self::MAX_REQUEST_BYTES + 1);
        if (strlen($rawBody) > self::MAX_REQUEST_BYTES) {
            http_response_code(413);
            echo json_encode(['status' => 'error', 'detail' => 'Request payload too large.']);
            return;
        }

        $decoded = json_decode($rawBody, true);
        if (!is_array($decoded)) {
            http_response_code(400);
            echo json_encode(['status' => 'error', 'detail' => 'Invalid JSON body.']);
            return;
        }

        $extractionIdRaw = $decoded['extraction_id'] ?? null;
        if (!is_int($extractionIdRaw) || $extractionIdRaw <= 0) {
            http_response_code(400);
            echo json_encode(['status' => 'error', 'detail' => 'extraction_id must be a positive integer.']);
            return;
        }

        $extractionId = $extractionIdRaw;

        // 7. Load extraction row (active only).
        $rows = QueryUtils::fetchRecords(
            'SELECT `id`, `patient_id`, `doc_ref_id`, `doc_type`, `status`
               FROM `co_pilot_extractions`
              WHERE `id` = ?
                AND `is_active` = 1
              LIMIT 1',
            [$extractionId],
        );

        if ($rows === []) {
            http_response_code(404);
            echo json_encode(['status' => 'error', 'detail' => 'Extraction not found or not active.']);
            return;
        }

        $extraction = $rows[0];
        $storedPid  = (int) $extraction['patient_id'];

        // 8. Patient-id horizontal-escalation check.
        //    The stored patient_id is the SENTINEL pid; the session pid is real.
        //    Compare via PersonaMap::sentinelId (mirrors handleReprocess pattern).
        $expectedSentinelPid = PersonaMap::sentinelId($sessionPid);
        if ($expectedSentinelPid === null || $storedPid !== $expectedSentinelPid) {
            $this->logger->warning('ClinicalCopilot/reject: patient_id mismatch — possible horizontal escalation', [
                'extraction_id' => $extractionId,
            ]);
            http_response_code(403);
            echo json_encode(['status' => 'error', 'detail' => 'Patient context mismatch.']);
            return;
        }

        // 9. State guard: reject is only valid from ExtractionStatus::PENDING_REVIEW.
        //    Terminal states (approved, rejected, error, refused) cannot be rejected again.
        $currentStatus = (string) $extraction['status'];
        if (!in_array($currentStatus, ExtractionStatus::rejectableStatuses(), true)) {
            http_response_code(422);
            echo json_encode([
                'status'         => 'error',
                'detail'         => 'Extraction is not in pending_review state.',
                'current_status' => $currentStatus,
            ]);
            return;
        }

        // 10. Transition to rejected (terminal — no round-trip will ever run).
        $this->roundtripService->updateExtractionStatus($extractionId, ExtractionStatus::REJECTED);

        $this->logger->info('ClinicalCopilot: extraction rejected', [
            'extraction_id' => $extractionId,
        ]);

        http_response_code(200);
        echo json_encode([
            'status'        => ExtractionStatus::REJECTED,
            'extraction_id' => $extractionId,
        ]);
    }

    private function dispatchReprocess(): void
    {
        // 1. Session check.
        $session   = SessionWrapperFactory::getInstance()->getActiveSession();
        $userId    = (int) ($session->get('authUserID') ?? 0);
        $sessionPid = (int) ($session->get('pid') ?? 0);

        if ($userId <= 0) {
            http_response_code(401);
            echo json_encode(['status' => 'error', 'detail' => 'Authentication required.']);
            return;
        }

        // 2. ACL.
        if (!AclMain::aclCheckCore('patients', 'med')) {
            http_response_code(403);
            echo json_encode(['status' => 'error', 'detail' => 'forbidden']);
            return;
        }

        // 3. Method.
        if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST') {
            http_response_code(405);
            echo json_encode(['status' => 'error', 'detail' => 'Method not allowed.']);
            return;
        }

        // 4. CSRF.
        $csrfToken = $_SERVER['HTTP_X_CSRF_TOKEN'] ?? '';
        if (!is_string($csrfToken) || !CsrfUtils::verifyCsrfToken($csrfToken, $session)) {
            http_response_code(403);
            echo json_encode(['status' => 'error', 'detail' => 'Invalid CSRF token.']);
            return;
        }

        // 5. Patient context.
        if ($sessionPid <= 0) {
            http_response_code(400);
            echo json_encode(['status' => 'error', 'detail' => 'No active patient in session.']);
            return;
        }

        // 6. Parse body.
        $rawBody = (string) file_get_contents('php://input', false, null, 0, self::MAX_REQUEST_BYTES + 1);
        if (strlen($rawBody) > self::MAX_REQUEST_BYTES) {
            http_response_code(413);
            echo json_encode(['status' => 'error', 'detail' => 'Request payload too large.']);
            return;
        }

        $decoded = json_decode($rawBody, true);
        if (!is_array($decoded)) {
            http_response_code(400);
            echo json_encode(['status' => 'error', 'detail' => 'Invalid JSON body.']);
            return;
        }

        $extractionIdRaw = $decoded['extraction_id'] ?? null;
        if (!is_int($extractionIdRaw) || $extractionIdRaw <= 0) {
            http_response_code(400);
            echo json_encode(['status' => 'error', 'detail' => 'extraction_id must be a positive integer.']);
            return;
        }

        $extractionId = $extractionIdRaw;

        // 7. Load extraction row (active only).
        $rows = QueryUtils::fetchRecords(
            'SELECT `id`, `patient_id`, `doc_ref_id`, `doc_type`
               FROM `co_pilot_extractions`
              WHERE `id` = ?
                AND `is_active` = 1
              LIMIT 1',
            [$extractionId],
        );

        if ($rows === []) {
            http_response_code(404);
            echo json_encode(['status' => 'error', 'detail' => 'Extraction not found or not active.']);
            return;
        }

        $extraction  = $rows[0];
        $storedPid   = (int) $extraction['patient_id'];
        $docRefId    = (string) $extraction['doc_ref_id'];
        $docType     = (string) $extraction['doc_type'];

        // 8. Patient-id horizontal-escalation check.
        //    The stored patient_id is the SENTINEL pid (999101-999104); the
        //    session pid is the REAL OpenEMR pid.  We need to compare sentinel
        //    → real via PersonaMap for the check to be meaningful.
        //    If PersonaMap cannot resolve the session pid, deny access.
        $expectedSentinelPid = PersonaMap::sentinelId($sessionPid);
        if ($expectedSentinelPid === null || $storedPid !== $expectedSentinelPid) {
            $this->logger->warning('ClinicalCopilot/reprocess: patient_id mismatch — possible horizontal escalation', [
                'extraction_id' => $extractionId,
            ]);
            http_response_code(403);
            echo json_encode(['status' => 'error', 'detail' => 'Patient context mismatch.']);
            return;
        }

        // 9. Resolve document path.
        $docIdInt = (int) $docRefId;
        $filePath = $this->pathResolver->resolve($docIdInt);
        if ($filePath === null) {
            http_response_code(422);
            echo json_encode(['status' => 'error', 'detail' => 'Document file could not be resolved.']);
            return;
        }

        // 10. Mark prior attempts inactive before issuing new agent call.
        $this->roundtripService->markPriorAttemptsInactive($docRefId, $docType);

        // 11. Agent call with reprocess headers.
        $response = $this->agentClient->attachAndExtract(
            patientId:          $storedPid,
            docRefId:           $docRefId,
            docType:            $docType,
            filePath:           $filePath,
            userId:             $userId,
            isReprocess:        true,
            parentExtractionId: $extractionId,
        );

        $agentStatus    = is_string($response['status'] ?? null) ? $response['status'] : 'error';
        $requestId      = is_string($response['request_id'] ?? null) ? $response['request_id'] : null;
        $nBlocks        = is_int($response['n_blocks'] ?? null) ? $response['n_blocks'] : null;
        $confidenceAvg  = isset($response['extraction_confidence_avg'])
            ? (float) $response['extraction_confidence_avg']
            : null;

        $templateId     = is_string($response['template_id'] ?? null) ? $response['template_id'] : null;
        $model          = is_string($response['model'] ?? null) ? $response['model'] : null;
        $promptVariant  = is_string($response['prompt_variant'] ?? null) ? $response['prompt_variant'] : 'default';
        $attemptN       = is_int($response['attempt_n'] ?? null) ? $response['attempt_n'] : 1;
        $triggeredBy    = is_string($response['triggered_by'] ?? null) ? $response['triggered_by'] : 'manual_retry';
        $rawCostUsd     = $response['cost_usd'] ?? null;
        $costUsd        = (is_float($rawCostUsd) || is_int($rawCostUsd)) ? (float) $rawCostUsd : null;
        $agentLatencyMs = is_int($response['latency_ms'] ?? null) ? $response['latency_ms'] : null;
        $totalFields    = is_int($response['total_fields'] ?? null) ? $response['total_fields'] : null;
        $strippedFields = is_int($response['stripped_fields'] ?? null) ? $response['stripped_fields'] : null;

        $fieldVerdicts     = is_array($response['field_verdicts'] ?? null) ? $response['field_verdicts'] : [];
        $doclingBlocks     = is_array($response['docling_blocks'] ?? null) ? $response['docling_blocks'] : [];
        $doclingBlocksJson = $doclingBlocks !== []
            ? json_encode($doclingBlocks, JSON_UNESCAPED_UNICODE)
            : null;

        $extractionJson = ($agentStatus === 'ok' && isset($response['extraction']))
            ? json_encode($response['extraction'], JSON_UNESCAPED_UNICODE)
            : null;

        // 12. Persist new extraction row.
        $newExtractionId = (int) QueryUtils::sqlInsert(
            'INSERT INTO `co_pilot_extractions`
                (`patient_id`, `doc_ref_id`, `doc_type`, `status`,
                 `extraction_json`, `n_blocks`, `extraction_confidence_avg`,
                 `agent_request_id`,
                 `template_id`, `model`, `prompt_variant`,
                 `attempt_n`, `triggered_by`,
                 `cost_usd`, `latency_ms`, `total_fields`, `stripped_fields`,
                 `docling_blocks_json`, `parent_extraction_id`)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            [
                $storedPid,
                $docRefId,
                $docType,
                $agentStatus,
                $extractionJson,
                $nBlocks,
                $confidenceAvg,
                $requestId,
                $templateId,
                $model,
                $promptVariant,
                $attemptN,
                $triggeredBy,
                $costUsd,
                $agentLatencyMs,
                $totalFields,
                $strippedFields,
                $doclingBlocksJson,
                $extractionId, // parent_extraction_id
            ],
        );

        // 13. Write extracted field verdicts.
        $verifiedFieldMap = [];
        if ($newExtractionId > 0 && $fieldVerdicts !== []) {
            $verifiedFieldMap = $this->fieldsWriter->writeAll(
                $newExtractionId,
                $fieldVerdicts,
                $doclingBlocks,
            );
        }

        // 14. FHIR round-trip.
        if ($agentStatus === 'ok' && $extractionJson !== null && $newExtractionId > 0) {
            $extractionPayload = json_decode($extractionJson, true);
            if (is_array($extractionPayload)) {
                $this->roundtripService->roundtrip(
                    extractionId:     $newExtractionId,
                    patientId:        $sessionPid,
                    docType:          $docType,
                    extraction:       $extractionPayload,
                    docRefId:         $docRefId,
                    verifiedFieldMap: $verifiedFieldMap,
                    fieldsWriter:     $this->fieldsWriter,
                );
            }
        }

        $this->logger->info('ClinicalCopilot: reprocess completed', [
            'parent_extraction_id' => $extractionId,
            'new_extraction_id'    => $newExtractionId,
            'agent_status'         => $agentStatus,
        ]);

        http_response_code(200);
        echo json_encode([
            'status'           => $agentStatus,
            'new_extraction_id'=> $newExtractionId,
            'total_fields'     => $totalFields,
            'stripped_fields'  => $strippedFields,
        ]);
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
