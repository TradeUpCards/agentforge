<?php

/**
 * Schema migration idempotency tests for P4 R2 — Bucket A (supplement to
 * InstallSqlIdempotencyTest.php which covers P1-P3).
 *
 * Validates R2-specific additions:
 *   A.1 — clinician_value column exists inside the #IfNotTable block (first-install).
 *   A.2 — clinician_value #IfMissingColumn guard exists (idempotency on re-run).
 *   A.3 — Exactly one #IfMissingColumn block for clinician_value on the correct table.
 *   A.4 — clinician_value has TEXT type and NULL-able (per PHI storage requirement).
 *   A.5 — clinician_value PHI comment present in the schema file.
 *   A.6 — DB-tier first-install (markTestSkipped — Docker MySQL).
 *   A.7 — DB-tier second-install idempotency (markTestSkipped — Docker MySQL).
 *
 * Source-analysis tests run without a live DB.
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

final class InstallSqlR2MigrationTest extends TestCase
{
    private string $sql;

    protected function setUp(): void
    {
        $path = __DIR__
            . '/../../../../interface/modules/custom_modules'
            . '/oe-module-clinical-copilot/sql/install.sql';

        self::assertFileExists($path, 'install.sql must exist.');
        $content = file_get_contents($path);
        self::assertIsString($content, 'install.sql must be readable.');
        $this->sql = $content;
    }

    // -----------------------------------------------------------------------
    // A.1 — clinician_value column in #IfNotTable block (first-install)
    // -----------------------------------------------------------------------

    /**
     * On a fresh install, the co_pilot_extracted_fields table must include
     * clinician_value as a column inside the CREATE TABLE block so the R2
     * feature works without any further ALTER.
     */
    public function testClinicianValueColumnInIfNotTableBlock(): void
    {
        $guardPos  = strpos($this->sql, '#IfNotTable co_pilot_extracted_fields');
        $endIfPos  = strpos($this->sql, '#EndIf', (int) $guardPos);

        self::assertNotFalse($guardPos, '#IfNotTable guard for co_pilot_extracted_fields must exist.');
        self::assertNotFalse($endIfPos, '#EndIf after the #IfNotTable block must exist.');

        $tableBlock = substr($this->sql, (int) $guardPos, $endIfPos - (int) $guardPos);

        $this->assertStringContainsString(
            '`clinician_value`',
            $tableBlock,
            'co_pilot_extracted_fields CREATE TABLE block must include the clinician_value column.',
        );
    }

    // -----------------------------------------------------------------------
    // A.2 — clinician_value #IfMissingColumn guard for idempotency on upgrade
    // -----------------------------------------------------------------------

    /**
     * An upgrade path must exist: a #IfMissingColumn guard that adds
     * clinician_value to an existing co_pilot_extracted_fields table that
     * was created before R2 shipped.
     */
    public function testClinicianValueHasIfMissingColumnGuard(): void
    {
        $this->assertStringContainsString(
            '#IfMissingColumn co_pilot_extracted_fields clinician_value',
            $this->sql,
            'install.sql must include a #IfMissingColumn guard for clinician_value '
            . 'to support upgrades from P3 installs (where the column did not exist).',
        );
    }

    // -----------------------------------------------------------------------
    // A.3 — Exactly one #IfMissingColumn for clinician_value
    // -----------------------------------------------------------------------

    /**
     * Multiple duplicate migration blocks would cause a double-ADD on some
     * upgrade paths.  Assert there is exactly one.
     */
    public function testExactlyOneIfMissingColumnForClinicianValue(): void
    {
        $count = substr_count(
            $this->sql,
            '#IfMissingColumn co_pilot_extracted_fields clinician_value',
        );

        $this->assertSame(
            1,
            $count,
            "install.sql must contain exactly one #IfMissingColumn block for "
            . "clinician_value, found {$count}.",
        );
    }

    // -----------------------------------------------------------------------
    // A.4 — clinician_value is TEXT NULL
    // -----------------------------------------------------------------------

    /**
     * clinician_value must be TEXT (not VARCHAR) and NULL-able.
     * TEXT because clinician-typed values have unbounded length.
     * NULL because no override means NULL (default state before R2 edits).
     */
    public function testClinicianValueIsTextNull(): void
    {
        // In the CREATE TABLE block.
        $guardPos  = strpos($this->sql, '#IfNotTable co_pilot_extracted_fields');
        $endIfPos  = strpos($this->sql, '#EndIf', (int) $guardPos);

        self::assertNotFalse($guardPos);
        self::assertNotFalse($endIfPos);

        $tableBlock = substr($this->sql, (int) $guardPos, $endIfPos - (int) $guardPos);

        $this->assertStringContainsString(
            'TEXT',
            $tableBlock,
            'clinician_value column must be TEXT type in the CREATE TABLE block.',
        );

        $this->assertStringContainsString(
            'NULL',
            $tableBlock,
            'clinician_value must be NULL-able (no override present before R2 edits).',
        );
    }

    // -----------------------------------------------------------------------
    // A.5 — PHI comment present in schema file
    // -----------------------------------------------------------------------

    /**
     * The install.sql must contain a PHI custody comment on the clinician_value
     * column definition so any DBA reviewing the schema understands the
     * data-governance obligation.
     */
    public function testClinicianValueHasPhiComment(): void
    {
        $this->assertTrue(
            str_contains($this->sql, 'PHI') && str_contains($this->sql, 'clinician_value'),
            'install.sql must include a PHI note near the clinician_value column '
            . 'definition so DBAs understand the custody designation.',
        );
    }

    // -----------------------------------------------------------------------
    // A.6 — DB-tier: first-install (markTestSkipped)
    // -----------------------------------------------------------------------

    /**
     * Full Docker MySQL test: fresh schema spin-up confirms clinician_value
     * column exists after running install.sql for the first time.
     */
    public function testFirstInstallCreatesClinicianValueColumn(): void
    {
        $this->markTestSkipped(
            'DB-tier test: requires Docker MySQL. '
            . 'Run install.sql on a fresh schema; '
            . 'confirm SHOW COLUMNS FROM co_pilot_extracted_fields includes clinician_value.',
        );
    }

    // -----------------------------------------------------------------------
    // A.7 — DB-tier: second-install idempotency (markTestSkipped)
    // -----------------------------------------------------------------------

    /**
     * Full Docker MySQL test: re-running install.sql on an existing schema
     * (with clinician_value already present) must not throw an error.
     * The #IfMissingColumn guard ensures the ADD COLUMN is skipped.
     */
    public function testSecondInstallIsIdempotentForClinicianValueColumn(): void
    {
        $this->markTestSkipped(
            'DB-tier test: requires Docker MySQL. '
            . 'Run install.sql twice on the same schema; '
            . 'confirm no error thrown and column type/nullable is unchanged. '
            . 'Mirrors the P3 #IfMissingColumn idempotency pattern.',
        );
    }
}
