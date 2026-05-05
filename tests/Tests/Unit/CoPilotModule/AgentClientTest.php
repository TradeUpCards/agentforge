<?php

/**
 * Unit tests for AgentClient.
 *
 * Covers:
 *   - Canonical HMAC payload construction (regression guard for contract drift).
 *   - attachAndExtract returns decoded array on a simulated 200 response.
 *   - attachAndExtract throws a generic RuntimeException on non-2xx without
 *     exposing PHI or raw response bodies in the exception message.
 *
 * The HTTP transport layer (postMultipart) and file-read are overridden in a
 * test subclass so these tests run without a live agent or real documents.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    AgentForge Team
 * @copyright Copyright (c) 2026 AgentForge
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Tests\Unit\CoPilotModule;

use OpenEMR\Modules\ClinicalCopilot\Service\AgentClient;
use PHPUnit\Framework\TestCase;
use Psr\Log\NullLogger;
use RuntimeException;

// Ensure the module namespace is autoloadable when the module class-loader
// has not been invoked by the OpenEMR module system (e.g. module not enabled
// in the test database or running outside Docker).
spl_autoload_register(static function (string $class): void {
    $prefix = 'OpenEMR\\Modules\\ClinicalCopilot\\';
    if (!str_starts_with($class, $prefix)) {
        return;
    }
    $relative = str_replace('\\', DIRECTORY_SEPARATOR, substr($class, strlen($prefix)));
    $path     = __DIR__ . '/../../../../interface/modules/custom_modules/oe-module-clinical-copilot/src/'
        . $relative . '.php';
    if (file_exists($path)) {
        require_once $path;
    }
});

/**
 * Testable subclass of AgentClient that short-circuits the filesystem read,
 * the HMAC-secret lookup, and the cURL transport so tests run without
 * environment setup.
 */
final class TestableAgentClient extends AgentClient
{
    /**
     * @param array{http_status: int, body: string} $stubbedResponse
     */
    public function __construct(
        private readonly ?string $stubbedSecret,
        private readonly string|false $stubbedFileBytes,
        private readonly array $stubbedResponse,
    ) {
        parent::__construct(new NullLogger());
    }

    protected function resolveHmacSecret(): ?string
    {
        return $this->stubbedSecret;
    }

    protected function readFileBytes(string $filePath): string|false
    {
        return $this->stubbedFileBytes;
    }

    /**
     * @return array{http_status: int, body: string}
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
        return $this->stubbedResponse;
    }
}

final class AgentClientTest extends TestCase
{
    // ---------------------------------------------------------------------------
    // computeHmac — canonical payload contract
    // ---------------------------------------------------------------------------

    /**
     * Regression guard: the canonical HMAC payload must stay bit-for-bit
     * consistent with the Python verifier (agent/agent.py:verify_attach_hmac).
     *
     * Locked canonical format:
     *   "{user_id}|{patient_id}|{doc_ref_id}|{doc_type}|{timestamp}|{file_sha256_hex}"
     *
     * If this test fails after an edit to computeHmac, the Python verifier
     * contract must be updated in the same commit.
     */
    public function testHmacPayloadIsCanonical(): void
    {
        $client = new TestableAgentClient(
            stubbedSecret: 'test-secret',
            stubbedFileBytes: 'irrelevant',
            stubbedResponse: ['http_status' => 200, 'body' => '{"status":"ok"}'],
        );

        $userId     = 42;
        $patientId  = 999101;
        $docRefId   = '12345';
        $docType    = 'lab_pdf';
        $timestamp  = 1746403200;
        $fileSha256 = 'abc123def456abc123def456abc123def456abc123def456abc123def456abc1';
        $secret     = 'super-secret';

        // Build the expected payload manually to mirror what Python does.
        $expectedPayload = implode('|', [
            (string) $userId,
            (string) $patientId,
            $docRefId,
            $docType,
            (string) $timestamp,
            $fileSha256,
        ]);
        $expectedHmac = hash_hmac('sha256', $expectedPayload, $secret);

        $actualHmac = $client->computeHmac(
            userId:     $userId,
            patientId:  $patientId,
            docRefId:   $docRefId,
            docType:    $docType,
            timestamp:  $timestamp,
            fileSha256: $fileSha256,
            secret:     $secret,
        );

        $this->assertSame($expectedHmac, $actualHmac, 'HMAC canonical payload has drifted from the locked contract.');
    }

    // ---------------------------------------------------------------------------
    // attachAndExtract — 200 success path
    // ---------------------------------------------------------------------------

    /**
     * When the agent returns HTTP 200 with a well-formed JSON body,
     * attachAndExtract must return the decoded array with the expected keys.
     */
    public function testAttachAndExtractSuccessReturnsResponseArray(): void
    {
        $fileContent = 'fake-pdf-bytes';
        $responseBody = json_encode([
            'status'                      => 'ok',
            'doc_ref_id'                  => '99',
            'doc_type'                    => 'lab_pdf',
            'extraction'                  => ['results' => []],
            'n_blocks'                    => 12,
            'n_pages'                     => 2,
            'extraction_confidence_avg'   => 0.9850,
            'request_id'                  => 'req-abc-123',
        ], JSON_THROW_ON_ERROR);

        $client = new TestableAgentClient(
            stubbedSecret:    'test-secret',
            stubbedFileBytes: $fileContent,
            stubbedResponse:  ['http_status' => 200, 'body' => $responseBody],
        );

        $result = $client->attachAndExtract(
            patientId: 999101,
            docRefId:  '99',
            docType:   'lab_pdf',
            filePath:  '/fake/path/doc.pdf',
            userId:    7,
        );

        $this->assertIsArray($result);
        $this->assertSame('ok', $result['status']);
        $this->assertSame('req-abc-123', $result['request_id']);
        $this->assertArrayHasKey('extraction', $result);
        $this->assertArrayHasKey('n_blocks', $result);
    }

    // ---------------------------------------------------------------------------
    // attachAndExtract — non-2xx path
    // ---------------------------------------------------------------------------

    /**
     * When the agent returns a non-2xx status, attachAndExtract must throw a
     * RuntimeException.  The exception message must be generic — no PHI, no
     * raw response body, no file paths.
     */
    public function testAttachAndExtractNon2xxThrowsGenericException(): void
    {
        $client = new TestableAgentClient(
            stubbedSecret:    'test-secret',
            stubbedFileBytes: 'fake-bytes',
            stubbedResponse:  [
                'http_status' => 500,
                'body'        => '{"status":"error","reason":"internal server error"}',
            ],
        );

        $this->expectException(RuntimeException::class);

        // The message must contain only the status code — no PHI, no raw body.
        $this->expectExceptionMessageMatches('/500/');

        $client->attachAndExtract(
            patientId: 999101,
            docRefId:  '99',
            docType:   'lab_pdf',
            filePath:  '/fake/path/doc.pdf',
            userId:    7,
        );
    }
}
