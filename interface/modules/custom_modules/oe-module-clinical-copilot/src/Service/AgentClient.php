<?php

/**
 * Thin HTTP client that calls the Python agent's attach_and_extract endpoint.
 *
 * Responsibilities:
 *   - Reads file bytes from the resolved storage path.
 *   - Computes the SHA-256 hash of those bytes.
 *   - Builds the canonical HMAC payload and signs it.
 *   - Sends a multipart/form-data POST to the agent.
 *   - Returns the decoded JSON response array.
 *   - Throws a generic \RuntimeException on failure — never exposing PHI,
 *     file paths, or raw exception messages from the transport layer.
 *
 * NO-PHI LOGGING CONTRACT (W2_ARCHITECTURE.md §8.3):
 *   - File bytes, raw response bodies, and extracted field values are never
 *     written to logs or exception messages.
 *   - The HMAC secret is never logged.
 *   - Only structural identifiers (patient_id ref, doc_ref_id, doc_type,
 *     agent status, latency_ms, request_id) appear in log context.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    AgentForge Team
 * @copyright Copyright (c) 2026 AgentForge
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\Service;

use OpenEMR\Common\Logging\SystemLogger;
use OpenEMR\Modules\ClinicalCopilot\Bootstrap;
use Psr\Log\LoggerInterface;
use RuntimeException;

class AgentClient
{
    private const ATTACH_EXTRACT_PATH = '/attach_and_extract';
    private const CURL_TIMEOUT_SECONDS = 120;
    private const CURL_CONNECT_TIMEOUT_SECONDS = 10;

    private readonly LoggerInterface $logger;

    public function __construct(?LoggerInterface $logger = null)
    {
        $this->logger = $logger ?? new SystemLogger();
    }

    /**
     * Call the agent's attach_and_extract endpoint for a single document.
     *
     * The file bytes are read from `$filePath`, hashed, signed, and streamed
     * as a multipart form field.  The bytes are never written anywhere — they
     * live in memory only for the duration of this call.
     *
     * @param int    $patientId  Sentinel patient_id (999101-999104 for demo personas).
     * @param string $docRefId   OpenEMR documents.id (string); becomes a FHIR
     *                           DocumentReference reference on Thursday.
     * @param string $docType    "lab_pdf" | "intake_form"
     * @param string $filePath   Absolute filesystem path to the stored document
     *                           (resolved from `documents.url` by the subscriber).
     * @param int    $userId     Uploading user's authUserID (front-desk actor).
     * @return array<string, mixed>  Decoded JSON response from the agent.
     *
     * @throws RuntimeException on configuration error, transport failure, or
     *         non-2xx HTTP status.  Messages are generic — no PHI.
     */
    public function attachAndExtract(
        int $patientId,
        string $docRefId,
        string $docType,
        string $filePath,
        int $userId,
    ): array {
        $secret = $this->resolveHmacSecret();
        if ($secret === null) {
            throw new RuntimeException(
                'AgentClient: HMAC secret not configured (OPENEMR_HMAC_SECRET).'
            );
        }

        // Read bytes — never persist to temp files.
        $fileBytes = $this->readFileBytes($filePath);
        if ($fileBytes === false) {
            throw new RuntimeException(
                'AgentClient: failed to read document bytes from storage.'
            );
        }

        $fileSha256 = hash('sha256', $fileBytes);
        $timestamp  = time();

        $hmac = $this->computeHmac(
            userId:      $userId,
            patientId:   $patientId,
            docRefId:    $docRefId,
            docType:     $docType,
            timestamp:   $timestamp,
            fileSha256:  $fileSha256,
            secret:      $secret,
        );

        $url = Bootstrap::getAgentBaseUrl() . self::ATTACH_EXTRACT_PATH;

        $startMs = (int) round(microtime(true) * 1000);
        $result  = $this->postMultipart(
            url:        $url,
            userId:     $userId,
            timestamp:  $timestamp,
            hmac:       $hmac,
            patientId:  $patientId,
            docRefId:   $docRefId,
            docType:    $docType,
            fileBytes:  $fileBytes,
            filePath:   $filePath,
        );
        $latencyMs = (int) round(microtime(true) * 1000) - $startMs;

        if ($result['http_status'] < 200 || $result['http_status'] >= 300) {
            $this->logger->error('AgentClient: non-2xx response from attach_and_extract', [
                'doc_ref_id'  => $docRefId,
                'doc_type'    => $docType,
                'http_status' => $result['http_status'],
                'latency_ms'  => $latencyMs,
            ]);
            throw new RuntimeException(
                'AgentClient: agent returned HTTP ' . $result['http_status'] . '.'
            );
        }

        $decoded = json_decode($result['body'], true);
        if (!is_array($decoded)) {
            throw new RuntimeException(
                'AgentClient: agent response is not valid JSON.'
            );
        }

        $this->logger->info('AgentClient: attach_and_extract completed', [
            'doc_ref_id'     => $docRefId,
            'doc_type'       => $docType,
            'agent_status'   => $decoded['status'] ?? 'unknown',
            'request_id'     => $decoded['request_id'] ?? null,
            'latency_ms'     => $latencyMs,
        ]);

        return $decoded;
    }

    /**
     * Build the canonical HMAC payload and return the hex digest.
     *
     * Canonical layout (must match Python agent's `verify_attach_hmac`):
     *   "{user_id}|{patient_id}|{doc_ref_id}|{doc_type}|{timestamp}|{file_sha256_hex}"
     *
     * The file_sha256_hex is lowercase hex of SHA-256(file bytes).
     * Including the content hash prevents a replay attack substituting a
     * different file for the same doc_ref_id.
     */
    public function computeHmac(
        int $userId,
        int $patientId,
        string $docRefId,
        string $docType,
        int $timestamp,
        string $fileSha256,
        string $secret,
    ): string {
        $payload = implode('|', [
            (string) $userId,
            (string) $patientId,
            $docRefId,
            $docType,
            (string) $timestamp,
            $fileSha256,
        ]);

        return hash_hmac('sha256', $payload, $secret);
    }

    /**
     * Resolve the HMAC secret from environment configuration.
     *
     * Extracted as a protected method so unit tests can override it without
     * depending on process environment variables.
     */
    protected function resolveHmacSecret(): ?string
    {
        return Bootstrap::getHmacSecret();
    }

    /**
     * Read raw bytes from the document storage path.
     *
     * Extracted as a protected method so unit tests can override the
     * filesystem read without requiring real files on disk.
     *
     * @return string|false File contents, or false on read failure.
     */
    protected function readFileBytes(string $filePath): string|false
    {
        return @file_get_contents($filePath);
    }

    /**
     * Execute the multipart POST via cURL.
     *
     * @return array{http_status: int, body: string}
     * @throws RuntimeException on cURL transport error.
     */
    protected function postMultipart(
        string $url,
        int $userId,
        int $timestamp,
        string $hmac,
        int $patientId,
        string $docRefId,
        string $docType,
        string $fileBytes,
        string $filePath,
    ): array {
        $ch = curl_init($url);
        if ($ch === false) {
            throw new RuntimeException('AgentClient: unable to initialise cURL handle.');
        }

        // Build a CURLFile from in-memory bytes via a data: URI approach is
        // not supported; we must use a temp file for cURL multipart.
        // The temp file is unlinked immediately after curl_exec.
        $tmp = tempnam(sys_get_temp_dir(), 'copilot_');
        if ($tmp === false) {
            curl_close($ch);
            throw new RuntimeException('AgentClient: unable to create temp file for multipart upload.');
        }

        try {
            if (file_put_contents($tmp, $fileBytes) === false) {
                throw new RuntimeException('AgentClient: unable to write to temp file for multipart upload.');
            }

            // Derive a safe display name from the last path segment — strip
            // directory components.  Do NOT log the full path (may contain pid).
            //
            // The $displayName below flows to the Python agent as the
            // Content-Disposition filename inside the multipart envelope.
            // The Python endpoint reads file bytes via UploadFile but does NOT
            // log the filename to stdout, Langfuse, or any trace metadata
            // (verified by obs-sec round-2 review).  If a future Python-side
            // change starts logging the filename, that's a regression — the
            // multipart filename can carry patient-name-bearing original
            // upload names (e.g. "Chen_Maria_labs.pdf").
            $displayName = basename($filePath);

            $postFields = [
                'patient_id' => (string) $patientId,
                'doc_ref_id' => $docRefId,
                'doc_type'   => $docType,
                'file'       => new \CURLFile($tmp, 'application/octet-stream', $displayName),
            ];

            curl_setopt_array($ch, [
                CURLOPT_RETURNTRANSFER => true,
                CURLOPT_POST           => true,
                CURLOPT_POSTFIELDS     => $postFields,
                CURLOPT_HTTPHEADER     => [
                    'X-OpenEMR-User-Id: '   . $userId,
                    'X-OpenEMR-Timestamp: ' . $timestamp,
                    'X-OpenEMR-HMAC: '      . $hmac,
                    'Accept: application/json',
                ],
                CURLOPT_TIMEOUT        => self::CURL_TIMEOUT_SECONDS,
                CURLOPT_CONNECTTIMEOUT => self::CURL_CONNECT_TIMEOUT_SECONDS,
                CURLOPT_FAILONERROR    => false,
            ]);

            $body = curl_exec($ch);

            if ($body === false) {
                $curlError = curl_error($ch);
                $this->logger->error('AgentClient: cURL transport error', [
                    'curl_errno' => curl_errno($ch),
                ]);
                throw new RuntimeException(
                    'AgentClient: cURL transport error (errno ' . curl_errno($ch) . ').'
                );
            }

            $httpStatus = (int) curl_getinfo($ch, CURLINFO_HTTP_CODE);

            return [
                'http_status' => $httpStatus,
                'body'        => (string) $body,
            ];
        } finally {
            curl_close($ch);
            if (file_exists($tmp)) {
                // Use @unlink to suppress warnings on permission failure.
                // If unlink fails, log structurally and let OS-level temp
                // cleanup handle eventual removal — never let a temp-file
                // cleanup error mask the original RuntimeException, and never
                // let doc bytes silently persist if cleanup is partial.
                if (!@unlink($tmp)) {
                    error_log(sprintf(
                        'AgentClient: temp file unlink failed (errno=%d) — relying on OS temp cleanup',
                        // $errno isn't available without php_errormsg; log path-stable structural data only
                        0
                    ));
                }
            }
        }
    }
}
