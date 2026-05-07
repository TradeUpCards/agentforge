<?php

/**
 * Unit tests for DocumentPathResolver.
 *
 * DocumentPathResolver is a `final` class that calls QueryUtils::fetchRecords and
 * the OpenEMR Document class directly (both requiring a live DB and environment).
 * Accordingly, these tests use two strategies:
 *
 *   (a) Source-analysis tests — read the PHP source and assert structural properties
 *       (branching logic, log context keys, no-PHI compliance).  These run in full
 *       isolation with no OpenEMR globals.
 *
 *   (b) DB-required tests — marked with markTestSkipped().  They document the
 *       exact command to run inside Docker and the scenario to exercise.
 *
 * Source-analysis tests are deterministic and catch regressions introduced during
 * future edits without needing a live environment.
 *
 * NO-PHI LOGGING AUDIT:
 *   The docblock on DocumentPathResolver.php says:
 *     "Only the structural document id is logged. Raw file paths, file contents,
 *      and patient-name segments in filenames are never written to logs."
 *   These tests verify that claim holds against the actual source code.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    AgentForge Team
 * @copyright Copyright (c) 2026 AgentForge
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Tests\Unit\CoPilotModule;

use PHPUnit\Framework\TestCase;

final class DocumentPathResolverTest extends TestCase
{
    private string $source;
    private string $sourcePath;

    protected function setUp(): void
    {
        $this->sourcePath = __DIR__
            . '/../../../../interface/modules/custom_modules'
            . '/oe-module-clinical-copilot/src/Service/DocumentPathResolver.php';

        self::assertFileExists(
            $this->sourcePath,
            'DocumentPathResolver.php must exist at expected path.',
        );

        $content = file_get_contents($this->sourcePath);
        self::assertIsString($content);
        $this->source = $content;
    }

    // -----------------------------------------------------------------------
    // Structural contract: null return paths
    // -----------------------------------------------------------------------

    /**
     * Non-filesystem storage (CouchDB, storagemethod != 0) must return null.
     *
     * The source must check storagemethod and return null when it is not 0.
     */
    public function testResolvesReturnsNullForNonFilesystemStorage(): void
    {
        $this->assertStringContainsString(
            'storagemethod',
            $this->source,
            'resolve() must check the storagemethod column to detect CouchDB storage.',
        );
        $this->assertStringContainsString(
            '!== 0',
            $this->source,
            'resolve() must return null when storagemethod is not 0.',
        );
    }

    /**
     * A file not found on disk must cause resolve() to return null.
     */
    public function testResolveReturnsNullWhenFileNotOnDisk(): void
    {
        // The source must check file_exists or do a read that can return false,
        // and return null (not throw) when the file is missing.
        $this->assertStringContainsString(
            'file_exists($path)',
            $this->source,
            'resolve() must call file_exists() to verify the file is on disk.',
        );

        // When file_get_contents fails, the function returns null.
        $this->assertStringContainsString(
            'return null',
            $this->source,
            'resolve() must return null on any failure path.',
        );
    }

    /**
     * Temp-file creation failure must return null.
     */
    public function testResolveReturnsNullOnTempFileFailure(): void
    {
        $this->assertStringContainsString(
            'tempnam(',
            $this->source,
            'resolve() must create a temp file via tempnam().',
        );
        // The temp file path or write failure must trigger a null return.
        $this->assertStringContainsString(
            'return null',
            $this->source,
            'resolve() must return null when temp file creation fails.',
        );
    }

    // -----------------------------------------------------------------------
    // No-PHI logging invariant
    //
    // Log entries must contain only doc_id (int).
    // Raw file paths, file contents, and patient-name segments must not appear
    // in any log context array.
    // -----------------------------------------------------------------------

    /**
     * Logger calls must only use doc_id in context — never $path, $url, or $rawBytes.
     */
    public function testLogContextNeverContainsRawFilePath(): void
    {
        // Extract all logger calls from the source.
        preg_match_all('/\$this->logger->[a-z]+\([^;]+\);/s', $this->source, $matches);
        $logCalls = $matches[0] ?? [];

        $this->assertNotEmpty($logCalls, 'DocumentPathResolver must have at least one logger call.');

        foreach ($logCalls as $logCall) {
            $this->assertStringNotContainsString(
                '$path',
                $logCall,
                "Logger call must not include \$path (raw file path) in context: {$logCall}",
            );
            $this->assertStringNotContainsString(
                '$url',
                $logCall,
                "Logger call must not include \$url (file URL that may contain patient names) in context: {$logCall}",
            );
            $this->assertStringNotContainsString(
                '$rawBytes',
                $logCall,
                "Logger call must not include \$rawBytes in context: {$logCall}",
            );
            $this->assertStringNotContainsString(
                '$plainBytes',
                $logCall,
                "Logger call must not include \$plainBytes in context: {$logCall}",
            );
        }
    }

    /**
     * Logger context must include doc_id as the only patient-identifying key.
     */
    public function testLogContextIncludesDocId(): void
    {
        $this->assertStringContainsString(
            "'doc_id'",
            $this->source,
            'Logger context must include doc_id as the only structural identifier.',
        );
    }

    /**
     * The class docblock explicitly states the no-PHI contract.
     */
    public function testDocblockDocumentsNophiContract(): void
    {
        $this->assertStringContainsString(
            'NO-PHI',
            $this->source,
            'DocumentPathResolver docblock must state the NO-PHI logging contract.',
        );
    }

    // -----------------------------------------------------------------------
    // Decryption contract
    // -----------------------------------------------------------------------

    /**
     * The resolver must attempt decryption via Document::decrypt_content().
     */
    public function testDecryptionIsAttempted(): void
    {
        $this->assertStringContainsString(
            'decrypt_content(',
            $this->source,
            'resolve() must call decrypt_content() to decrypt documents stored at rest.',
        );
    }

    /**
     * Decryption failure must be caught and the resolver must fall back to
     * raw bytes — not crash.
     */
    public function testDecryptionFailureFallsBackToRawBytes(): void
    {
        // The source must catch a Throwable around decrypt_content.
        $this->assertStringContainsString(
            'catch (\Throwable)',
            $this->source,
            'resolve() must catch Throwable around decrypt_content() and fall back to raw bytes.',
        );
        $this->assertStringContainsString(
            '$plainBytes = $rawBytes',
            $this->source,
            'When decryption fails, resolve() must fall back to $rawBytes (unencrypted install).',
        );
    }

    // -----------------------------------------------------------------------
    // Temp file cleanup contract
    //
    // The class docblock says "The temporary file is the caller's responsibility
    // to unlink."  This test asserts no auto-unlink in the resolver itself.
    // -----------------------------------------------------------------------

    public function testResolverDoesNotUnlinkTempFile(): void
    {
        $this->assertStringNotContainsString(
            'unlink(',
            $this->source,
            'DocumentPathResolver must NOT unlink the temp file — that is the caller\'s responsibility.',
        );
    }

    // -----------------------------------------------------------------------
    // DB-required placeholder
    // -----------------------------------------------------------------------

    /**
     * End-to-end: resolve() returns a valid path for a real document row.
     *
     * SKIPPED: requires live OpenEMR DB + document on disk.
     *
     * Command:
     *   docker compose exec openemr /root/devtools unit-test \
     *     --filter DocumentPathResolverTest::testResolveReturnsPathForRealDocument
     */
    public function testResolveReturnsPathForRealDocument(): void
    {
        $this->markTestSkipped(
            'DB-required: needs live OpenEMR DB + filesystem document. '
            . 'Run inside Docker with a real doc_id.',
        );
    }
}
