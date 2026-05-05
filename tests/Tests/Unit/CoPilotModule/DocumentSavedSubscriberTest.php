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
 * DB calls (resolveCategoryName, extractionExists, persistExtractionRow) and
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
use OpenEMR\Modules\ClinicalCopilot\Service\AgentClient;
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
 */
final class TestableDocumentSavedSubscriber extends DocumentSavedSubscriber
{
    /** @var string|null  Category name returned by resolveCategoryName(). */
    public ?string $categoryNameToReturn = null;

    /** @var bool  Whether extractionExists() pretends the row exists. */
    public bool $extractionAlreadyExists = false;

    /** @var string|null  Path returned by resolveFilePath(). */
    public ?string $filePathToReturn = '/fake/path/document.pdf';

    /** @var array<int, array{pid: int, docRefId: string, docType: string, status: string}> */
    public array $persistedRows = [];

    protected function resolveCategoryName(int $categoryId): ?string
    {
        return $this->categoryNameToReturn;
    }

    protected function extractionExists(string $docRefId, string $docType): bool
    {
        return $this->extractionAlreadyExists;
    }

    protected function resolveFilePath(int $docId): ?string
    {
        return $this->filePathToReturn;
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
    ): void {
        $this->persistedRows[] = [
            'pid'       => $pid,
            'docRefId'  => $docRefId,
            'docType'   => $docType,
            'status'    => $status,
        ];
    }
}

final class DocumentSavedSubscriberTest extends TestCase
{
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

        $subscriber = new TestableDocumentSavedSubscriber($agentClient, new NullLogger());
        $subscriber->categoryNameToReturn    = 'Lab Result (auto-extract)';
        $subscriber->extractionAlreadyExists = false;
        $subscriber->filePathToReturn        = '/fake/path/document.pdf';

        $event = new DocumentCreatedEvent(
            documentId: 101,
            patientId:  $pid,
            categoryId: 9000,
            ownerId:    $frontDeskUserId,
        );

        $subscriber->onDocumentCreated($event);

        $this->assertCount(1, $subscriber->persistedRows, 'One extraction row should be persisted.');
        $this->assertSame('ok', $subscriber->persistedRows[0]['status']);
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
        $subscriber->filePathToReturn     = '/fake/path/intake.pdf';

        // pid=50 is not in the PersonaMap (only 1-4 are demo personas).
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
