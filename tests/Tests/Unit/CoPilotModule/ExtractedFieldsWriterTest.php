<?php

/**
 * Unit tests for ExtractedFieldsWriter.
 *
 * ExtractedFieldsWriter uses QueryUtils::sqlStatementThrowException and
 * QueryUtils::sqlInsert directly (not through overridable protected methods),
 * so isolation tests use source-analysis strategy.
 *
 * Covers PRD §10 requirement 6 (no failed values in database):
 *   Assert no column in co_pilot_extracted_fields ever stores the LLM-produced
 *   value of a stripped field.  writeStripped() stores only status, field_path,
 *   source_block_id, bbox_json, and verifier_reason — never the extracted value.
 *
 * Also covers:
 *   - writeAll() dispatches verified → writeVerified(), stripped → writeStripped()
 *   - writeAll() returns verified-field map with correct field_path → row_id entries
 *   - updateClinicalPointer() updates clinical_table and clinical_row_id columns
 *   - No-PHI logging: log context never includes field values or paths
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

final class ExtractedFieldsWriterTest extends TestCase
{
    private string $source;

    protected function setUp(): void
    {
        $path = __DIR__
            . '/../../../../interface/modules/custom_modules'
            . '/oe-module-clinical-copilot/src/Service/ExtractedFieldsWriter.php';

        self::assertFileExists($path, 'ExtractedFieldsWriter.php must exist.');
        $content = file_get_contents($path);
        self::assertIsString($content);
        $this->source = $content;
    }

    // -----------------------------------------------------------------------
    // PRD §10 req 6: writeStripped() must NOT store the field value
    //
    // The INSERT for a stripped field should carry:
    //   extraction_id, field_path, status='stripped', source_block_id,
    //   bbox_json, verifier_reason
    //
    // It must NOT carry a 'value' column — that would store LLM output for
    // a field that failed verification, violating PRD §10 req 6.
    // -----------------------------------------------------------------------

    public function testWriteStrippedInsertDoesNotIncludeValueColumn(): void
    {
        // Locate the writeStripped INSERT block (first INSERT in the file).
        $insertPos = strpos($this->source, "INSERT INTO `co_pilot_extracted_fields`");
        self::assertNotFalse($insertPos, 'writeStripped INSERT must exist in source.');

        // The next INSERT (writeVerified) should come after the first.
        $secondInsertPos = strpos($this->source, "INSERT INTO `co_pilot_extracted_fields`", $insertPos + 1);
        self::assertNotFalse($secondInsertPos, 'writeVerified INSERT must also exist in source.');

        // Extract the first INSERT block (writeStripped).
        $firstInsertBlock = substr($this->source, $insertPos, $secondInsertPos - $insertPos);

        // The column list for writeStripped must NOT include a bare `value` column.
        $this->assertStringNotContainsString(
            '`value`',
            $firstInsertBlock,
            'writeStripped INSERT must not include a `value` column — '
            . 'storing LLM-extracted values for stripped fields violates PRD §10 req 6.',
        );
    }

    /**
     * writeStripped INSERT must include verifier_reason column.
     */
    public function testWriteStrippedInsertIncludesVerifierReason(): void
    {
        $insertPos = strpos($this->source, "INSERT INTO `co_pilot_extracted_fields`");
        self::assertNotFalse($insertPos);

        $secondInsertPos = strpos($this->source, "INSERT INTO `co_pilot_extracted_fields`", $insertPos + 1);
        self::assertNotFalse($secondInsertPos);

        $firstInsertBlock = substr($this->source, $insertPos, $secondInsertPos - $insertPos);

        $this->assertStringContainsString(
            '`verifier_reason`',
            $firstInsertBlock,
            'writeStripped INSERT must include the verifier_reason column.',
        );
    }

    /**
     * writeStripped INSERT must store status = 'stripped'.
     *
     * The source file literally contains the characters \'stripped\' because
     * the SQL is held in a single-quoted PHP string literal, so the inner
     * quotes are backslash-escaped on disk.  The assertion looks for the
     * escaped form to match the on-disk bytes.  An alternative would be to
     * switch the SQL holder to a double-quoted string in the source, but the
     * single-quoted form is conventional in this codebase.
     */
    public function testWriteStrippedInsertUsesStrippedStatus(): void
    {
        $insertPos = strpos($this->source, "INSERT INTO `co_pilot_extracted_fields`");
        self::assertNotFalse($insertPos);

        $secondInsertPos = strpos($this->source, "INSERT INTO `co_pilot_extracted_fields`", $insertPos + 1);
        self::assertNotFalse($secondInsertPos);

        $firstInsertBlock = substr($this->source, $insertPos, $secondInsertPos - $insertPos);

        $this->assertStringContainsString(
            "\\'stripped\\'",
            $firstInsertBlock,
            "writeStripped INSERT must hard-code status = 'stripped'.",
        );
    }

    /**
     * writeVerified INSERT must store status = 'verified'.
     *
     * Same single-quoted-string escaping caveat as above.
     */
    public function testWriteVerifiedInsertUsesVerifiedStatus(): void
    {
        $secondInsertPos = false;
        $firstInsertPos  = strpos($this->source, "INSERT INTO `co_pilot_extracted_fields`");
        if ($firstInsertPos !== false) {
            $secondInsertPos = strpos($this->source, "INSERT INTO `co_pilot_extracted_fields`", $firstInsertPos + 1);
        }
        self::assertNotFalse($secondInsertPos, 'writeVerified INSERT must exist.');

        $verifiedBlock = substr($this->source, (int) $secondInsertPos, 400);

        $this->assertStringContainsString(
            "\\'verified\\'",
            $verifiedBlock,
            "writeVerified INSERT must hard-code status = 'verified'.",
        );
    }

    // -----------------------------------------------------------------------
    // writeAll() dispatch logic
    // -----------------------------------------------------------------------

    public function testWriteAllDispatchesVerifiedToWriteVerified(): void
    {
        $this->assertStringContainsString(
            "if (\$status === 'verified')",
            $this->source,
            "writeAll must dispatch to writeVerified() when status === 'verified'.",
        );
    }

    public function testWriteAllDispatchesOtherToWriteStripped(): void
    {
        $this->assertStringContainsString(
            '$this->writeStripped(',
            $this->source,
            'writeAll must dispatch to writeStripped() for non-verified status.',
        );
    }

    public function testWriteAllBuildsBlockBboxMapFromDoclingBlocks(): void
    {
        $this->assertStringContainsString(
            '$blockBboxMap',
            $this->source,
            'writeAll must build a block_id → bbox map from docling_blocks for joining to verdicts.',
        );
    }

    /**
     * The verified map must only be populated for 'verified' entries — not stripped.
     */
    public function testWriteAllPopulatesVerifiedMapOnlyForVerifiedEntries(): void
    {
        $this->assertStringContainsString(
            '$verifiedMap[$fieldPath] = $rowId',
            $this->source,
            'writeAll must populate verifiedMap with field_path => rowId for verified fields only.',
        );
    }

    /**
     * The else branch (stripped) must NOT populate verifiedMap.
     */
    public function testWriteAllElseBranchDoesNotPopulateVerifiedMap(): void
    {
        $elsePos   = strpos($this->source, '} else {');
        $returnPos = strpos($this->source, 'return $verifiedMap');
        self::assertNotFalse($elsePos,   'else block must exist in writeAll.');
        self::assertNotFalse($returnPos, 'return $verifiedMap must exist in writeAll.');

        $elseBranch = substr($this->source, (int) $elsePos, $returnPos - (int) $elsePos);

        $this->assertStringNotContainsString(
            '$verifiedMap[',
            $elseBranch,
            'The stripped (else) branch of writeAll must not populate verifiedMap.',
        );
    }

    /**
     * writeAll must skip verdicts with empty field_path.
     */
    public function testWriteAllSkipsEmptyFieldPath(): void
    {
        $this->assertStringContainsString(
            '$fieldPath === \'\'',
            $this->source,
            "writeAll must skip verdicts with empty field_path (continue; guard).",
        );
    }

    // -----------------------------------------------------------------------
    // updateClinicalPointer() structural verification
    // -----------------------------------------------------------------------

    public function testUpdateClinicalPointerUpdatesCorrectTable(): void
    {
        $this->assertStringContainsString(
            "UPDATE `co_pilot_extracted_fields`",
            $this->source,
            'updateClinicalPointer must UPDATE co_pilot_extracted_fields.',
        );
    }

    public function testUpdateClinicalPointerSetsClinicalTable(): void
    {
        $this->assertStringContainsString(
            '`clinical_table` = ?',
            $this->source,
            'updateClinicalPointer must set clinical_table column.',
        );
    }

    public function testUpdateClinicalPointerSetsClinicalRowId(): void
    {
        $this->assertStringContainsString(
            '`clinical_row_id` = ?',
            $this->source,
            'updateClinicalPointer must set clinical_row_id column.',
        );
    }

    // -----------------------------------------------------------------------
    // No-PHI logging invariant (source-level audit)
    //
    // Log context must contain only extraction_id and row_id — never field
    // values, field paths, bbox coordinates, or verifier reasons.
    // -----------------------------------------------------------------------

    public function testLoggerContextNeverContainsFieldPath(): void
    {
        preg_match_all('/\$this->logger->[a-z]+\([^;]+\);/s', $this->source, $logMatches);
        $logCalls = $logMatches[0] ?? [];

        $this->assertNotEmpty($logCalls, 'ExtractedFieldsWriter must have at least one logger call.');

        foreach ($logCalls as $logCall) {
            $this->assertStringNotContainsString(
                '$fieldPath',
                $logCall,
                "Logger call must not include \$fieldPath in context: {$logCall}",
            );
        }
    }

    public function testLoggerContextNeverContainsVerifierReason(): void
    {
        preg_match_all('/\$this->logger->[a-z]+\([^;]+\);/s', $this->source, $logMatches);
        $logCalls = $logMatches[0] ?? [];

        foreach ($logCalls as $logCall) {
            $this->assertStringNotContainsString(
                '$verifierReason',
                $logCall,
                "Logger call must not include \$verifierReason in context: {$logCall}",
            );
        }
    }

    public function testLoggerContextNeverContainsBboxJson(): void
    {
        preg_match_all('/\$this->logger->[a-z]+\([^;]+\);/s', $this->source, $logMatches);
        $logCalls = $logMatches[0] ?? [];

        foreach ($logCalls as $logCall) {
            $this->assertStringNotContainsString(
                '$bboxJson',
                $logCall,
                "Logger call must not include \$bboxJson in context: {$logCall}",
            );
        }
    }

    /**
     * The class docblock must assert the NO-PHI logging contract.
     */
    public function testDocblockDocumentsNophiContract(): void
    {
        $this->assertStringContainsString(
            'NO-PHI',
            $this->source,
            'ExtractedFieldsWriter docblock must state the NO-PHI logging contract.',
        );
    }
}
