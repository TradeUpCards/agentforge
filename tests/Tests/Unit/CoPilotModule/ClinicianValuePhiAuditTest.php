<?php

/**
 * No-PHI audit for clinician_value — Bucket I.
 *
 * The clinician_value column holds clinician-asserted PHI by design.
 * The WRITE is expected (edit endpoint, splice path, preserve-edits-on-reprocess).
 * But READS of clinician_value must NEVER leak into:
 *   - Logger context arrays (logs may be shipped to observability platforms)
 *   - 4xx/5xx error response bodies (which can be logged by upstream proxies)
 *   - The agent-side Python code (agent never sees co_pilot_extracted_fields)
 *
 * Tests in this file are source-analysis grepping across the PHP module.
 * They require ZERO matches for disallowed patterns.
 *
 * METHODOLOGY:
 *   We scan all PHP files under the module root (excluding sql/ and tests/).
 *   Any logger call that directly passes $clinicianValue (or the full DB row)
 *   as a context value is a violation.  Only structural metadata
 *   (clinician_value_length, field_path) may appear in logs.
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

final class ClinicianValuePhiAuditTest extends TestCase
{
    private const MODULE_ROOT = __DIR__
        . '/../../../../interface/modules/custom_modules'
        . '/oe-module-clinical-copilot/';

    // -----------------------------------------------------------------------
    // I.1 — No logger call passes clinician_value directly
    // -----------------------------------------------------------------------

    /**
     * Scan all PHP files in the module.  No logger call may include
     * $clinicianValue (or $clinician_value) as a context array value.
     *
     * Permitted: 'clinician_value_length' => strlen($clinicianValue)
     * Forbidden: 'clinician_value' => $clinicianValue  OR  $clinicianValue in any context
     */
    public function testNoLoggerCallExposesClinicianlValue(): void
    {
        $violations = [];

        foreach ($this->iteratePhpFiles() as $filePath => $content) {
            $lines = explode("\n", $content);
            foreach ($lines as $lineNo => $line) {
                // Detect logger calls that directly pass the clinician_value raw variable.
                if (
                    $this->isLoggerCall($line)
                    && (
                        str_contains($line, '$clinicianValue')
                        && !str_contains($line, 'strlen(')
                        && !str_contains($line, 'clinician_value_length')
                    )
                ) {
                    $violations[] = "{$filePath}:" . ($lineNo + 1) . ": {$line}";
                }
            }
        }

        $this->assertEmpty(
            $violations,
            'PHI LEAK: The following lines expose $clinicianValue in a logger call. '
            . 'Only clinician_value_length (an integer) may appear in log context. '
            . "\n" . implode("\n", $violations),
        );
    }

    // -----------------------------------------------------------------------
    // I.2 — No logger call passes the full DB row containing clinician_value
    // -----------------------------------------------------------------------

    /**
     * Scan for any logger call that passes an entire $fieldRow or $priorRow
     * array in context.  These arrays contain clinician_value.
     */
    public function testNoLoggerCallPassesFullFieldRow(): void
    {
        $violations = [];

        foreach ($this->iteratePhpFiles() as $filePath => $content) {
            $lines = explode("\n", $content);
            foreach ($lines as $lineNo => $line) {
                if (
                    $this->isLoggerCall($line)
                    && (
                        str_contains($line, '$fieldRow,')
                        || str_contains($line, '$priorRow,')
                        || str_contains($line, '$priorManualRows,')
                        || str_contains($line, "'row' => \$fieldRow")
                        || str_contains($line, "'row' => \$priorRow")
                    )
                ) {
                    $violations[] = "{$filePath}:" . ($lineNo + 1) . ": " . trim($line);
                }
            }
        }

        $this->assertEmpty(
            $violations,
            'PHI LEAK: The following lines log a full field row that contains clinician_value. '
            . 'Only structural metadata (field_path, extraction_id) may be logged. '
            . "\n" . implode("\n", $violations),
        );
    }

    // -----------------------------------------------------------------------
    // I.3 — No error_log() call passes clinician_value
    // -----------------------------------------------------------------------

    public function testNoErrorLogCallExposesClinician​Value(): void
    {
        $violations = [];

        foreach ($this->iteratePhpFiles() as $filePath => $content) {
            $lines = explode("\n", $content);
            foreach ($lines as $lineNo => $line) {
                if (
                    str_contains($line, 'error_log(')
                    && str_contains($line, 'clinician_value')
                ) {
                    $violations[] = "{$filePath}:" . ($lineNo + 1) . ": " . trim($line);
                }
            }
        }

        $this->assertEmpty(
            $violations,
            'PHI LEAK: error_log() references clinician_value. '
            . "\n" . implode("\n", $violations),
        );
    }

    // -----------------------------------------------------------------------
    // I.4 — extraction_for_doc.php GET response INCLUDES clinician_value
    //        (intentional: authed GET, modal needs it to pre-fill the edit form)
    // -----------------------------------------------------------------------

    /**
     * This is an INTENTIONAL inclusion: the GET response for extraction_for_doc.php
     * must include clinician_value in the per-field row payload so the edit form
     * can pre-populate with the existing override value.
     *
     * This test documents and asserts the intentionality.
     */
    public function testExtractionForDocIncludesClinicianValueIntentionally(): void
    {
        $path = self::MODULE_ROOT . 'public/extraction_for_doc.php';
        if (!file_exists($path)) {
            $this->markTestSkipped('extraction_for_doc.php does not exist.');
        }

        $source = file_get_contents($path);
        self::assertIsString($source);

        // The GET endpoint MUST SELECT and return clinician_value.
        // This is intentional: the authed clinician needs to see their prior override.
        $this->assertStringContainsString(
            'clinician_value',
            $source,
            'extraction_for_doc.php must return clinician_value in the field-row payload. '
            . 'This is intentional and authorised — the clinician needs to see their prior '
            . 'override value when editing a field.',
        );
    }

    // -----------------------------------------------------------------------
    // I.5 — clinician_value does NOT appear in 4xx/5xx response bodies
    //        (which can be logged by upstream proxies)
    // -----------------------------------------------------------------------

    public function testNoErrorResponseEchoesClinicianValue(): void
    {
        $violations = [];

        foreach ($this->iteratePhpFiles() as $filePath => $content) {
            // Look for error response patterns followed by clinician_value.
            // Simple heuristic: find http_response_code(4xx/5xx) blocks that
            // also contain echo ... clinician_value nearby.
            if (
                str_contains($content, 'clinician_value')
                && preg_match_all(
                    '/http_response_code\((4\d\d|5\d\d)\).*?echo[^;]*clinician_value/s',
                    $content,
                    $matches,
                )
            ) {
                foreach ($matches[0] as $match) {
                    $violations[] = "{$filePath}: " . substr($match, 0, 200);
                }
            }
        }

        $this->assertEmpty(
            $violations,
            'PHI LEAK: Error response bodies echo clinician_value. '
            . 'Error bodies may be logged by upstream proxies. '
            . "\n" . implode("\n", $violations),
        );
    }

    // -----------------------------------------------------------------------
    // I.6 — Agent code (Python) never reads co_pilot_extracted_fields
    // -----------------------------------------------------------------------

    /**
     * The agent (/attach_and_extract endpoint in agent/main.py) must never
     * read from co_pilot_extracted_fields — it is a PHP/DB-only table.
     *
     * Source-analysis: grep agent/main.py for any reference to the table
     * or to clinician_value.
     */
    public function testAgentMainPyNeverReadsClinicianlValue(): void
    {
        $mainPyPath = __DIR__ . '/../../../../agent/main.py';

        if (!file_exists($mainPyPath)) {
            $this->markTestSkipped('agent/main.py does not exist at expected path.');
        }

        $source = file_get_contents($mainPyPath);
        self::assertIsString($source);

        $this->assertStringNotContainsString(
            'clinician_value',
            $source,
            'agent/main.py must not reference clinician_value. '
            . 'This column is PHP/DB-side only — the agent never holds it.',
        );

        $this->assertStringNotContainsString(
            'co_pilot_extracted_fields',
            $source,
            'agent/main.py must not reference co_pilot_extracted_fields. '
            . 'The agent is stateless w.r.t. the HITL review table.',
        );
    }

    // -----------------------------------------------------------------------
    // I.7 — No clinician_value in non-authenticated GET endpoints
    // -----------------------------------------------------------------------

    /**
     * Endpoints that return data without checking authentication must not
     * include clinician_value in their outputs.  This is belt-and-suspenders
     * to the D.4 test in EditExtractedFieldEndpointTest.
     */
    public function testUnauthenticatedEndpointsDoNotExposeClinicianValue(): void
    {
        // Scan all public/*.php files for any that DON'T check authUserID
        // but DO echo clinician_value.
        $publicDir = self::MODULE_ROOT . 'public/';

        if (!is_dir($publicDir)) {
            $this->markTestSkipped('Module public/ directory does not exist.');
        }

        $violations = [];

        foreach (glob($publicDir . '*.php') ?: [] as $phpFile) {
            $content = file_get_contents($phpFile);
            if ($content === false) {
                continue;
            }

            // If the file references clinician_value in an echo/json_encode...
            if (!str_contains($content, 'clinician_value')) {
                continue;
            }

            // ...AND it does not check authentication (authUserID or globals.php
            // which boots the session + auth).
            if (
                !str_contains($content, 'authUserID')
                && !str_contains($content, 'globals.php')
                && !str_contains($content, 'require_once')
            ) {
                $violations[] = $phpFile . ': references clinician_value without apparent auth check.';
            }
        }

        $this->assertEmpty(
            $violations,
            'PHI LEAK: The following endpoints reference clinician_value without '
            . 'apparent auth check: '
            . "\n" . implode("\n", $violations),
        );
    }

    // -----------------------------------------------------------------------
    // Private helpers
    // -----------------------------------------------------------------------

    /**
     * Yield all PHP files under the module root (excluding sql/ and tests/).
     * Returns filePath => content.
     *
     * @return \Generator<string, string>
     */
    private function iteratePhpFiles(): \Generator
    {
        $moduleRoot = realpath(self::MODULE_ROOT);
        if ($moduleRoot === false) {
            return;
        }

        $iterator = new \RecursiveIteratorIterator(
            new \RecursiveDirectoryIterator($moduleRoot, \RecursiveDirectoryIterator::SKIP_DOTS),
        );

        foreach ($iterator as $file) {
            /** @var \SplFileInfo $file */
            if ($file->getExtension() !== 'php') {
                continue;
            }

            $realPath = str_replace('\\', '/', (string) $file->getRealPath());

            // Skip sql/ and tests/ subdirectories.
            if (str_contains($realPath, '/sql/') || str_contains($realPath, '/tests/')) {
                continue;
            }

            $content = file_get_contents($realPath);
            if ($content === false) {
                continue;
            }

            yield $realPath => $content;
        }
    }

    /**
     * Heuristic: is this line a PSR-3 logger call?
     */
    private function isLoggerCall(string $line): bool
    {
        return (bool) preg_match('/->(?:error|warning|info|debug|notice|critical|alert|emergency)\(/', $line);
    }
}
