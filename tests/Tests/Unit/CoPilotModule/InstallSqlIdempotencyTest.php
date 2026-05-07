<?php

/**
 * Schema migration idempotency test — PRD §10 requirement 1.
 *
 * Validates that install.sql directives are structurally well-formed and
 * that the #IfMissingColumn / #IfNotIndex / #IfNotTable guard patterns
 * are present for every ALTER and CREATE added after the initial P1 schema.
 *
 * This test does NOT need a live database.  It parses the SQL file content
 * directly, asserting the idempotency guard is in place for each migration
 * block.  The CREATE TABLE IF NOT EXISTS baseline is verified by confirming
 * the IF NOT EXISTS clause is present.
 *
 * WHY TEXT-PARSE OVER LIVE-DB:
 *   Running the SQL against a real DB requires Docker and the OpenEMR schema
 *   to be installed.  The guard-directive pattern used by OpenEMR's
 *   SQLUpgradeService is sufficient to guarantee idempotency at runtime; we
 *   verify the directives are present.
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

final class InstallSqlIdempotencyTest extends TestCase
{
    private string $sql;

    protected function setUp(): void
    {
        $path = __DIR__
            . '/../../../../interface/modules/custom_modules'
            . '/oe-module-clinical-copilot/sql/install.sql';

        self::assertFileExists($path, 'install.sql must exist at the expected path.');
        $content = file_get_contents($path);
        self::assertIsString($content, 'install.sql must be readable.');
        $this->sql = $content;
    }

    // -----------------------------------------------------------------------
    // Baseline CREATE TABLE guards
    // -----------------------------------------------------------------------

    /**
     * co_pilot_extractions uses IF NOT EXISTS so a re-run does not fail.
     */
    public function testCoPilotExtractionsTableUsesIfNotExists(): void
    {
        $this->assertStringContainsString(
            'CREATE TABLE IF NOT EXISTS `co_pilot_extractions`',
            $this->sql,
            'co_pilot_extractions CREATE TABLE must use IF NOT EXISTS for idempotency.',
        );
    }

    /**
     * co_pilot_fhir_links uses IF NOT EXISTS so a re-run does not fail.
     */
    public function testCoPilotFhirLinksTableUsesIfNotExists(): void
    {
        $this->assertStringContainsString(
            'CREATE TABLE IF NOT EXISTS `co_pilot_fhir_links`',
            $this->sql,
            'co_pilot_fhir_links CREATE TABLE must use IF NOT EXISTS for idempotency.',
        );
    }

    /**
     * co_pilot_extracted_fields uses the #IfNotTable guard (P3 table).
     */
    public function testCoPilotExtractedFieldsUsesIfNotTableGuard(): void
    {
        $this->assertStringContainsString(
            '#IfNotTable co_pilot_extracted_fields',
            $this->sql,
            'co_pilot_extracted_fields must be guarded by #IfNotTable for idempotency.',
        );
    }

    // -----------------------------------------------------------------------
    // P2 attempt-chain column guards (ALTER ... ADD COLUMN)
    // -----------------------------------------------------------------------

    /**
     * @return array<string, array{string}>
     *
     * @codeCoverageIgnore Data providers run before coverage instrumentation starts.
     */
    public static function p2ColumnProvider(): array
    {
        return [
            'is_active'             => ['is_active'],
            'template_id'           => ['template_id'],
            'model'                 => ['model'],
            'prompt_variant'        => ['prompt_variant'],
            'attempt_n'             => ['attempt_n'],
            'triggered_by'          => ['triggered_by'],
            'parent_extraction_id'  => ['parent_extraction_id'],
            'cost_usd'              => ['cost_usd'],
            'latency_ms'            => ['latency_ms'],
            'total_fields'          => ['total_fields'],
            'stripped_fields'       => ['stripped_fields'],
        ];
    }

    /**
     * Every P2 ALTER ADD COLUMN is guarded by #IfMissingColumn.
     *
     * @dataProvider p2ColumnProvider
     */
    public function testP2ColumnIsGuardedByIfMissingColumn(string $column): void
    {
        $this->assertStringContainsString(
            '#IfMissingColumn co_pilot_extractions ' . $column,
            $this->sql,
            "P2 column '{$column}' must be guarded by #IfMissingColumn co_pilot_extractions {$column}.",
        );
    }

    // -----------------------------------------------------------------------
    // P3 column guard
    // -----------------------------------------------------------------------

    /**
     * The P3 docling_blocks_json column is guarded by #IfMissingColumn.
     */
    public function testP3DoclingBlocksJsonColumnIsGuarded(): void
    {
        $this->assertStringContainsString(
            '#IfMissingColumn co_pilot_extractions docling_blocks_json',
            $this->sql,
            'P3 docling_blocks_json column must be guarded by #IfMissingColumn.',
        );
    }

    // -----------------------------------------------------------------------
    // Index guards
    // -----------------------------------------------------------------------

    /**
     * @return array<string, array{string, string}>
     *
     * @codeCoverageIgnore Data providers run before coverage instrumentation starts.
     */
    public static function indexGuardProvider(): array
    {
        return [
            'idx_active_attempt'    => ['co_pilot_extractions', 'idx_active_attempt'],
            'idx_parent_extraction' => ['co_pilot_extractions', 'idx_parent_extraction'],
        ];
    }

    /**
     * P2 indexes are guarded by #IfNotIndex.
     *
     * @dataProvider indexGuardProvider
     */
    public function testIndexIsGuardedByIfNotIndex(string $table, string $indexName): void
    {
        $this->assertStringContainsString(
            '#IfNotIndex ' . $table . ' ' . $indexName,
            $this->sql,
            "Index '{$indexName}' on '{$table}' must be guarded by #IfNotIndex.",
        );
    }

    // -----------------------------------------------------------------------
    // Schema completeness — P3 table columns
    // -----------------------------------------------------------------------

    /**
     * co_pilot_extracted_fields must include all seven required columns.
     *
     * @return array<string, array{string}>
     *
     * @codeCoverageIgnore Data providers run before coverage instrumentation starts.
     */
    public static function extractedFieldsColumnProvider(): array
    {
        return [
            'id'               => ['`id`'],
            'extraction_id'    => ['`extraction_id`'],
            'field_path'       => ['`field_path`'],
            'status'           => ['`status`'],
            'source_block_id'  => ['`source_block_id`'],
            'bbox_json'        => ['`bbox_json`'],
            'verifier_reason'  => ['`verifier_reason`'],
            'clinical_table'   => ['`clinical_table`'],
            'clinical_row_id'  => ['`clinical_row_id`'],
        ];
    }

    /**
     * Each co_pilot_extracted_fields column appears in the CREATE TABLE block.
     *
     * @dataProvider extractedFieldsColumnProvider
     */
    public function testExtractedFieldsTableContainsRequiredColumn(string $column): void
    {
        // The column definition must appear after the #IfNotTable guard.
        $guardPos  = strpos($this->sql, '#IfNotTable co_pilot_extracted_fields');
        $endIfPos  = strpos($this->sql, '#EndIf', (int) $guardPos);

        self::assertNotFalse($guardPos, '#IfNotTable guard must exist.');
        self::assertNotFalse($endIfPos, '#EndIf after #IfNotTable guard must exist.');

        $tableBlock = substr($this->sql, (int) $guardPos, $endIfPos - (int) $guardPos);
        $this->assertStringContainsString(
            $column,
            $tableBlock,
            "Column {$column} must appear inside co_pilot_extracted_fields CREATE TABLE block.",
        );
    }

    // -----------------------------------------------------------------------
    // FK guard: co_pilot_extracted_fields references co_pilot_extractions
    // -----------------------------------------------------------------------

    public function testExtractedFieldsHasForeignKeyToExtractions(): void
    {
        $this->assertStringContainsString(
            'REFERENCES `co_pilot_extractions`',
            $this->sql,
            'co_pilot_extracted_fields must have a FK referencing co_pilot_extractions.',
        );
    }

    // -----------------------------------------------------------------------
    // INSERT IGNORE guard on categories seed rows
    // -----------------------------------------------------------------------

    public function testCategoriesSeedUsesInsertIgnore(): void
    {
        $this->assertStringContainsString(
            'INSERT IGNORE INTO `categories`',
            $this->sql,
            'Category seed rows must use INSERT IGNORE for idempotency.',
        );
    }
}
