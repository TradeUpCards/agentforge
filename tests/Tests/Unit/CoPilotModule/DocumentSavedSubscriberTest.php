<?php

/**
 * Unit tests for DocumentSavedSubscriber.
 *
 * Covers the three critical behavioural paths:
 *   1. testIgnoresNonAutoExtractCategory   — fires for a non-auto-extract
 *      category → agent is NOT called.
 *   2. testCallsAgentForLabResultCategory  — fires for
 *      "Lab Result (auto-extract)" + a demo persona → agent IS called with
 *      doc_type="lab_pdf".
 *   3. testNonDemoPersonaIsSkipped         — fires for a demo category but a
 *      pid not in PersonaMap → agent is NOT called (structural log only).
 *
 * DB calls (resolveCategoryName, activeOkExtractionExists, persistExtractionRow) and
 * the filesystem read (resolveFilePath) are overridden in a test subclass so
 * the tests run without a live database or real documents.
 *
 * TWO-ACTOR AUDIT NOTE (W2_ARCHITECTURE.md §2.1):
 *   The subscriber fires on behalf of the front-desk actor.  The AgentClient
 *   mock asserts that the front-desk user id (ownerId on the event) is forwarded
 *   to the agent call — NOT a PCP user id.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    AgentForge Team
 * @copyright Copyright (c) 2026 AgentForge
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Tests\Unit\CoPilotModule;

use OpenEMR\Modules\ClinicalCopilot\EventSubscriber\DocumentSavedSubscriber;
use OpenEMR\Modules\ClinicalCopilot\Events\DocumentCreatedEvent;
use OpenEMR\Modules\ClinicalCopilot\ExtractionStatus;
use OpenEMR\Modules\ClinicalCopilot\Service\AgentClient;
use OpenEMR\Modules\ClinicalCopilot\Service\DocumentPathResolver;
use OpenEMR\Modules\ClinicalCopilot\Service\RoundtripService;
use PHPUnit\Framework\MockObject\MockObject;
use PHPUnit\Framework\TestCase;
use Psr\Log\NullLogger;

// Ensure the module namespace is autoloadable when the module class-loader
// has not been invoked by the OpenEMR module system.
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
 * Testable subclass that overrides DB + filesystem helpers so tests
 * do not need a live database or real documents.
 *
 * P3 NOTE: DocumentPathResolver is now an injected service (no longer a
 * protected resolveFilePath() method).  We inject a mock DocumentPathResolver
 * via the constructor instead.  The subclass overrides the DB helpers that
 * ARE still protected methods: resolveCategoryName(), activeOkExtractionExists(),
 * and persistExtractionRow().
 *
 * P3 signature update: persistExtractionRow() now includes doclingBlocksJson
 * as the final optional parameter, matching the P3 source implementation.
 */
final class TestableDocumentSavedSubscriber extends DocumentSavedSubscriber
{
    /** @var string|null  Category name returned by resolveCategoryName(). */
    public ?string $categoryNameToReturn = null;

    /** @var bool  Whether activeOkExtractionExists() pretends the row exists. */
    public bool $extractionAlreadyExists = false;

    /** @var array<int, array{pid: int, docRefId: string, docType: string, status: string}> */
    public array $persistedRows = [];

    /**
     * @param AgentClient                $agentClient
     * @param \Psr\Log\LoggerInterface|null $logger
     * @param \OpenEMR\Modules\ClinicalCopilot\Service\RoundtripService|null $roundtripService
     * @param \OpenEMR\Modules\ClinicalCopilot\Service\DocumentPathResolver|null $pathResolver
     * @param \OpenEMR\Modules\ClinicalCopilot\Service\ExtractedFieldsWriter|null $fieldsWriter
     */
    public function __construct(
        AgentClient $agentClient,
        ?\Psr\Log\LoggerInterface $logger = null,
        ?\OpenEMR\Modules\ClinicalCopilot\Service\RoundtripService $roundtripService = null,
        ?\OpenEMR\Modules\ClinicalCopilot\Service\DocumentPathResolver $pathResolver = null,
        ?\OpenEMR\Modules\ClinicalCopilot\Service\ExtractedFieldsWriter $fieldsWriter = null,
    ) {
        parent::__construct($agentClient, $logger, $roundtripService, $pathResolver, $fieldsWriter);
    }

    protected function resolveCategoryName(int $categoryId): ?string
    {
        return $this->categoryNameToReturn;
    }

    protected function activeOkExtractionExists(string $docRefId, string $docType): bool
    {
        return $this->extractionAlreadyExists;
    }

    protected function persistExtractionRow(
        int $pid,
        string $docRefId,
        string $docType,
        string $status,
        ?string $extractionJson,
        ?int $nBlocks,
        ?float $confidenceAvg,
        ?string $requestId,
        ?string $templateId        = null,
        ?string $model             = null,
        string  $promptVariant     = 'default',
        int     $attemptN          = 1,
        string  $triggeredBy       = 'initial',
        ?float  $costUsd           = null,
        ?int    $latencyMs         = null,
        ?int    $totalFields       = null,
        ?int    $strippedFields    = null,
        ?string $doclingBlocksJson = null,   // P3 addition
    ): int {
        $this->persistedRows[] = [
            'pid'       => $pid,
            'docRefId'  => $docRefId,
            'docType'   => $docType,
            'status'    => $status,
        ];
        // Return a synthetic id so the round-trip dispatch path in the
        // production class can run without throwing on `int $extractionId`.
        // Tests that assert agent invocation never reach the round-trip
        // call (the test stub's RoundtripService is a no-op fake).
        return count($this->persistedRows);
    }
}

final class DocumentSavedSubscriberTest extends TestCase
{
    // ---------------------------------------------------------------------------
    // P3: IS_ACTIVE invariant — source-level structural tests
    // ---------------------------------------------------------------------------

    /**
     * The IS_ACTIVE INVARIANT FIX requires markPriorAttemptsInactive() to be
     * called BEFORE resolveFilePath() and the agent call, not after.
     *
     * This test reads the subscriber source and asserts that:
     *   (a) markPriorAttemptsInactive() is called before the agent call.
     *   (b) The agent call (attachAndExtract) comes after markPriorAttemptsInactive.
     *
     * If the order is reversed, an error-path early return (e.g. file not found)
     * could leave a prior active row + a new error row both with is_active=1.
     */
    public function testIsActiveInvariantMarkBeforeAgentCall(): void
    {
        $sourcePath = __DIR__
            . '/../../../../interface/modules/custom_modules'
            . '/oe-module-clinical-copilot/src/EventSubscriber/DocumentSavedSubscriber.php';

        self::assertFileExists($sourcePath);
        $source = file_get_contents($sourcePath);
        self::assertIsString($source);

        $markPos  = strpos($source, 'markPriorAttemptsInactive(');
        $agentPos = strpos($source, 'attachAndExtract(');

        self::assertNotFalse($markPos,  'markPriorAttemptsInactive must exist in subscriber.');
        self::assertNotFalse($agentPos, 'attachAndExtract must exist in subscriber.');

        $this->assertLessThan(
            $agentPos,
            $markPos,
            'IS_ACTIVE INVARIANT: markPriorAttemptsInactive() must appear BEFORE attachAndExtract() '
            . 'so the invariant holds even on error-path early returns.',
        );
    }

    /**
     * The second markPriorAttemptsInactive call that pre-P3 appeared just before
     * the successful-path INSERT must be ABSENT (replaced by the early call at step 5).
     *
     * Pre-P3 code had TWO calls: one before success-path INSERT (wrong).
     * P3 fix: one call before file I/O, none afterwards.
     */
    public function testIsActiveInvariantOnlyOneMarkCall(): void
    {
        $sourcePath = __DIR__
            . '/../../../../interface/modules/custom_modules'
            . '/oe-module-clinical-copilot/src/EventSubscriber/DocumentSavedSubscriber.php';

        self::assertFileExists($sourcePath);
        $source = file_get_contents($sourcePath);
        self::assertIsString($source);

        // Count call-site occurrences only (qualified by the service prefix).
        // Plain substr_count('markPriorAttemptsInactive(') would count
        // docblock and inline-comment mentions too; the qualified form
        // matches only the actual method invocation in code.
        $count = substr_count($source, '$this->roundtripService->markPriorAttemptsInactive(');

        $this->assertSame(
            1,
            $count,
            'IS_ACTIVE INVARIANT: markPriorAttemptsInactive() must be called exactly once '
            . '(before file I/O) — a second call after the agent call means the error-path '
            . 'is still not covered.',
        );
    }

    // ---------------------------------------------------------------------------
    // P3: extracted_fields writes — structural checks
    // ---------------------------------------------------------------------------

    /**
     * The subscriber must call ExtractedFieldsWriter::writeAll() when field_verdicts
     * are present in the agent response.
     */
    public function testSubscriberCallsExtractedFieldsWriterWriteAll(): void
    {
        $sourcePath = __DIR__
            . '/../../../../interface/modules/custom_modules'
            . '/oe-module-clinical-copilot/src/EventSubscriber/DocumentSavedSubscriber.php';

        self::assertFileExists($sourcePath);
        $source = file_get_contents($sourcePath);
        self::assertIsString($source);

        $this->assertStringContainsString(
            '$this->fieldsWriter->writeAll(',
            $source,
            'DocumentSavedSubscriber must call ExtractedFieldsWriter::writeAll() '
            . 'to persist per-field verdict rows (P3 requirement).',
        );
    }

    /**
     * The subscriber must persist docling_blocks_json in the extraction row.
     */
    public function testSubscriberPersistsDoclingBlocksJson(): void
    {
        $sourcePath = __DIR__
            . '/../../../../interface/modules/custom_modules'
            . '/oe-module-clinical-copilot/src/EventSubscriber/DocumentSavedSubscriber.php';

        self::assertFileExists($sourcePath);
        $source = file_get_contents($sourcePath);
        self::assertIsString($source);

        $this->assertStringContainsString(
            'doclingBlocksJson',
            $source,
            'DocumentSavedSubscriber must pass doclingBlocksJson to persistExtractionRow() (P3).',
        );
    }

    /**
     * The subscriber must guard writeAll with "extractionId > 0 && fieldVerdicts !== []"
     * to avoid writing verdict rows for error-path extractions that returned id=0.
     */
    public function testSubscriberGuardsWriteAllOnPositiveExtractionId(): void
    {
        $sourcePath = __DIR__
            . '/../../../../interface/modules/custom_modules'
            . '/oe-module-clinical-copilot/src/EventSubscriber/DocumentSavedSubscriber.php';

        self::assertFileExists($sourcePath);
        $source = file_get_contents($sourcePath);
        self::assertIsString($source);

        $this->assertStringContainsString(
            '$extractionId > 0',
            $source,
            'writeAll must be guarded on $extractionId > 0 to skip error-path extraction rows.',
        );
    }

    // ---------------------------------------------------------------------------
    // testIgnoresNonAutoExtractCategory
    // ---------------------------------------------------------------------------

    /**
     * When a document is saved under a category that is NOT one of the two
     * auto-extract categories, the subscriber must return early without
     * calling the agent.
     */
    public function testIgnoresNonAutoExtractCategory(): void
    {
        /** @var AgentClient&MockObject $agentClient */
        $agentClient = $this->createMock(AgentClient::class);
        $agentClient->expects($this->never())->method('attachAndExtract');

        $subscriber = new TestableDocumentSavedSubscriber($agentClient, new NullLogger());
        $subscriber->categoryNameToReturn = 'Medical Records'; // not an auto-extract category

        $event = new DocumentCreatedEvent(
            documentId: 101,
            patientId:  1,   // Chen — a demo persona, but category should filter first
            categoryId: 5,
            ownerId:    20,
        );

        $subscriber->onDocumentCreated($event);

        $this->assertEmpty($subscriber->persistedRows, 'No extraction row should be persisted for a non-auto-extract category.');
    }

    // ---------------------------------------------------------------------------
    // testCallsAgentForLabResultCategory
    // ---------------------------------------------------------------------------

    /**
     * When a document is saved under "Lab Result (auto-extract)" for a demo
     * persona, the subscriber must call attachAndExtract with doc_type="lab_pdf"
     * and forward the front-desk ownerId as the userId.
     *
     * P3 update: DocumentPathResolver is now injected rather than a protected method.
     * We pass a mock DocumentPathResolver that returns a fake path.
     */
    public function testCallsAgentForLabResultCategory(): void
    {
        $frontDeskUserId = 20;
        $pid             = 1; // Chen → sentinel 999101

        $agentResponse = [
            'status'                    => 'ok',
            'doc_ref_id'                => '101',
            'doc_type'                  => 'lab_pdf',
            'extraction'                => ['results' => []],
            'n_blocks'                  => 8,
            'n_pages'                   => 1,
            'extraction_confidence_avg' => 0.97,
            'request_id'                => 'req-test-lab',
        ];

        /** @var AgentClient&MockObject $agentClient */
        $agentClient = $this->createMock(AgentClient::class);
        $agentClient->expects($this->once())
            ->method('attachAndExtract')
            ->with(
                $this->equalTo(999101),   // sentinel for Chen
                $this->equalTo('101'),    // doc_ref_id as string
                $this->equalTo('lab_pdf'),
                $this->equalTo('/fake/path/document.pdf'),
                $this->equalTo($frontDeskUserId),  // front-desk actor, NOT PCP
            )
            ->willReturn($agentResponse);

        /** @var DocumentPathResolver&MockObject $pathResolver */
        $pathResolver = $this->createMock(DocumentPathResolver::class);
        $pathResolver->method('resolve')->willReturn('/fake/path/document.pdf');

        // P3 IS_ACTIVE INVARIANT FIX moved markPriorAttemptsInactive() to
        // step 5 (before file I/O) — it's invoked BEFORE persistExtractionRow.
        // The default RoundtripService constructed by the parent class would
        // hit QueryUtils on a real DB; in unit tests we inject a mock whose
        // markPriorAttemptsInactive() and roundtrip() methods are no-ops.
        /** @var RoundtripService&MockObject $roundtripService */
        $roundtripService = $this->createMock(RoundtripService::class);

        $subscriber = new TestableDocumentSavedSubscriber(
            $agentClient,
            new NullLogger(),
            roundtripService: $roundtripService,
            pathResolver: $pathResolver,
        );
        $subscriber->categoryNameToReturn    = 'Lab Result (auto-extract)';
        $subscriber->extractionAlreadyExists = false;

        $event = new DocumentCreatedEvent(
            documentId: 101,
            patientId:  $pid,
            categoryId: 9000,
            ownerId:    $frontDeskUserId,
        );

        $subscriber->onDocumentCreated($event);

        $this->assertCount(1, $subscriber->persistedRows, 'One extraction row should be persisted.');
        // P4 R1: successful agent extractions land in pending_review (gated)
        // instead of writing directly to clinical tables. The agent's "ok" status
        // is translated to ExtractionStatus::PENDING_REVIEW by the subscriber.
        $this->assertSame(ExtractionStatus::PENDING_REVIEW, $subscriber->persistedRows[0]['status']);
        $this->assertSame('lab_pdf', $subscriber->persistedRows[0]['docType']);
        $this->assertSame(999101, $subscriber->persistedRows[0]['pid']);
    }

    // ---------------------------------------------------------------------------
    // testNonDemoPersonaIsSkipped
    // ---------------------------------------------------------------------------

    /**
     * When a document is saved under an auto-extract category but the patient
     * pid is NOT in the PersonaMap (not a demo persona), the subscriber must
     * skip the agent call and log structurally without any PHI.
     */
    public function testNonDemoPersonaIsSkipped(): void
    {
        /** @var AgentClient&MockObject $agentClient */
        $agentClient = $this->createMock(AgentClient::class);
        $agentClient->expects($this->never())->method('attachAndExtract');

        $subscriber = new TestableDocumentSavedSubscriber($agentClient, new NullLogger());
        $subscriber->categoryNameToReturn = 'Intake Form (auto-extract)';
        // pid=50 fails PersonaMap before pathResolver is ever called — no need to mock it.
        $event = new DocumentCreatedEvent(
            documentId: 202,
            patientId:  50,
            categoryId: 9001,
            ownerId:    25,
        );

        $subscriber->onDocumentCreated($event);

        $this->assertEmpty(
            $subscriber->persistedRows,
            'No extraction row should be persisted for a non-demo persona.'
        );
    }
}
