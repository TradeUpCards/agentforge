<?php

/**
 * Unit tests for DocumentSavedSubscriber P4 R1 approve-gate behaviour.
 *
 * Contract being pinned:
 *
 *   1. RoundtripService::roundtrip is NEVER called from onDocumentCreated (gate
 *      removed in P4 R1 — round-trip now deferred until clinician approves via
 *      CoPilotController::handleApprove).
 *   2. A successful agent extraction (status='ok') lands in co_pilot_extractions
 *      with status='pending_review', NOT 'ok'.
 *   3. Refused / refused-by-agent extractions still land in 'refused'.
 *   4. Error-path extractions still land in 'error'.
 *   5. The P2 IS_ACTIVE invariant (markPriorAttemptsInactive before agent call)
 *      is not broken by the gate removal.
 *   6. No PHI appears in any logger call: no patient_id value, no field values,
 *      no block text in any context key.
 *
 * All behaviour tests use the TestableDocumentSavedSubscriber subclass
 * (defined in DocumentSavedSubscriberTest.php in the same namespace) which
 * stubs out DB + filesystem helpers.
 *
 * Tests that require Docker MySQL fixture seeding are explicitly
 * markTestSkipped, mirroring the P3 pattern.
 *
 * NO-PHI FIXTURES:
 *   All patient IDs use sentinel range 999100-999199 per W2_ARCHITECTURE.md §8.2.
 *   No real names, no field values, no block text in any fixture.
 *
 * CONSTANTS:
 *   Tests reference \OpenEMR\Modules\ClinicalCopilot\ExtractionStatus constants.
 *   No raw string literals for status values.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    AgentForge Team
 * @copyright Copyright (c) 2026 AgentForge
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Tests\Unit\CoPilotModule;

use OpenEMR\Modules\ClinicalCopilot\Events\DocumentCreatedEvent;
use OpenEMR\Modules\ClinicalCopilot\ExtractionStatus;
use OpenEMR\Modules\ClinicalCopilot\Service\AgentClient;
use OpenEMR\Modules\ClinicalCopilot\Service\DocumentPathResolver;
use OpenEMR\Modules\ClinicalCopilot\Service\ExtractedFieldsWriter;
use OpenEMR\Modules\ClinicalCopilot\Service\RoundtripService;
use PHPUnit\Framework\MockObject\MockObject;
use PHPUnit\Framework\TestCase;
use Psr\Log\LoggerInterface;
use Psr\Log\NullLogger;

// ---------------------------------------------------------------------------
// Module autoloader (mirrors DocumentSavedSubscriberTest.php pattern exactly)
// ---------------------------------------------------------------------------
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

final class DocumentSavedSubscriberApproveGateTest extends TestCase
{
    // ---------------------------------------------------------------------------
    // Sentinel patient IDs (per W2_ARCHITECTURE.md §8.2 — never use real PIDs)
    // ---------------------------------------------------------------------------

    /**
     * Real OpenEMR pid for persona Chen (maps to sentinel 999101 via PersonaMap).
     * We use pid=1 here because that is what PersonaMap maps; the sentinel value
     * 999101 (≥ 999100) is what gets persisted and asserted.
     */
    private const PID_CHEN_REAL = 1;

    private const PID_CHEN_SENTINEL = 999101;

    // ---------------------------------------------------------------------------
    // Helpers
    // ---------------------------------------------------------------------------

    /**
     * Build a minimal successful agent response.  The 'status' key is 'ok'
     * because that is what the agent returns; the subscriber is expected to
     * translate it to ExtractionStatus::PENDING_REVIEW before persisting.
     *
     * @return array<string, mixed>
     */
    private function makeSuccessAgentResponse(): array
    {
        return [
            'status'                    => ExtractionStatus::OK,
            'doc_ref_id'                => '501',
            'doc_type'                  => 'lab_pdf',
            'extraction'                => ['results' => []],
            'n_blocks'                  => 4,
            'n_pages'                   => 1,
            'extraction_confidence_avg' => 0.91,
            'request_id'                => 'req-approve-gate-test',
        ];
    }

    /**
     * Build a mock AgentClient whose attachAndExtract returns $response.
     *
     * @param array<string, mixed> $response
     * @return AgentClient&MockObject
     */
    private function makeAgentClientMock(array $response): AgentClient&MockObject
    {
        /** @var AgentClient&MockObject $mock */
        $mock = $this->createMock(AgentClient::class);
        $mock->method('attachAndExtract')->willReturn($response);
        return $mock;
    }

    /**
     * Build a mock DocumentPathResolver that returns a fake path so the
     * subscriber never hits the real filesystem.
     *
     * @return DocumentPathResolver&MockObject
     */
    private function makePathResolverMock(): DocumentPathResolver&MockObject
    {
        /** @var DocumentPathResolver&MockObject $mock */
        $mock = $this->createMock(DocumentPathResolver::class);
        $mock->method('resolve')->willReturn('/sentinel/path/document.pdf');
        return $mock;
    }

    /**
     * Build a mock RoundtripService with no expectations set (caller must add them).
     *
     * @return RoundtripService&MockObject
     */
    private function makeRoundtripServiceMock(): RoundtripService&MockObject
    {
        /** @var RoundtripService&MockObject $mock */
        return $this->createMock(RoundtripService::class);
    }

    /**
     * Build a DocumentCreatedEvent for persona Chen (pid=1, maps to sentinel 999101).
     */
    private function makeEvent(int $documentId = 501, int $ownerId = 20): DocumentCreatedEvent
    {
        return new DocumentCreatedEvent(
            documentId: $documentId,
            patientId:  self::PID_CHEN_REAL,
            categoryId: 9000,
            ownerId:    $ownerId,
        );
    }

    /**
     * Build a fully-wired TestableDocumentSavedSubscriber with 'Lab Result (auto-extract)'
     * category and no pre-existing extraction.
     *
     * @param AgentClient          $agentClient
     * @param RoundtripService     $roundtripService
     * @param DocumentPathResolver $pathResolver
     * @param LoggerInterface|null $logger
     */
    private function makeSubscriber(
        AgentClient $agentClient,
        RoundtripService $roundtripService,
        DocumentPathResolver $pathResolver,
        ?LoggerInterface $logger = null,
    ): TestableDocumentSavedSubscriber {
        $subscriber = new TestableDocumentSavedSubscriber(
            agentClient:      $agentClient,
            logger:           $logger ?? new NullLogger(),
            roundtripService: $roundtripService,
            pathResolver:     $pathResolver,
        );
        $subscriber->categoryNameToReturn    = 'Lab Result (auto-extract)';
        $subscriber->extractionAlreadyExists = false;
        return $subscriber;
    }

    // ---------------------------------------------------------------------------
    // Test 1: roundtrip() is NEVER called from onDocumentCreated
    // ---------------------------------------------------------------------------

    /**
     * P4 R1 gate assertion: RoundtripService::roundtrip must NEVER be called
     * from within onDocumentCreated, even when the agent returns a successful
     * extraction.
     *
     * If this test fails, the synchronous round-trip is still wired and
     * unreviewed LLM output is entering clinical tables without a human gate.
     */
    public function testGateRemovesSynchronousRoundtripCall(): void
    {
        $roundtripService = $this->makeRoundtripServiceMock();

        // Hard assertion: roundtrip() must NEVER be called.
        $roundtripService->expects($this->never())
            ->method('roundtrip');

        $subscriber = $this->makeSubscriber(
            $this->makeAgentClientMock($this->makeSuccessAgentResponse()),
            $roundtripService,
            $this->makePathResolverMock(),
        );

        $subscriber->onDocumentCreated($this->makeEvent());

        // If we get here without the mock throwing, the gate is in place.
        $this->assertTrue(true, 'Gate is in place: roundtrip() was never called.');
    }

    // ---------------------------------------------------------------------------
    // Test 2: Successful extraction lands in pending_review
    // ---------------------------------------------------------------------------

    /**
     * After onDocumentCreated with a successful agent response, the persisted
     * row must have status = ExtractionStatus::PENDING_REVIEW ('pending_review'),
     * NOT ExtractionStatus::OK ('ok').
     *
     * This is the core P4 R1 behaviour: the status written to the DB is
     * 'pending_review', not the raw agent status 'ok'.  The round-trip is
     * deferred until the clinician approves.
     */
    public function testSuccessfulExtractionLandsInPendingReview(): void
    {
        $roundtripService = $this->makeRoundtripServiceMock();

        $subscriber = $this->makeSubscriber(
            $this->makeAgentClientMock($this->makeSuccessAgentResponse()),
            $roundtripService,
            $this->makePathResolverMock(),
        );

        $subscriber->onDocumentCreated($this->makeEvent());

        $this->assertCount(1, $subscriber->persistedRows, 'Exactly one extraction row should be persisted.');

        $row = $subscriber->persistedRows[0];

        $this->assertSame(
            ExtractionStatus::PENDING_REVIEW,
            $row['status'],
            'A successful extraction must land in status="pending_review" (P4 R1 gate), '
            . 'not in the raw agent status "ok". If this fails, the subscriber is still '
            . 'writing "ok" which bypasses the clinician review gate.',
        );

        // Confirm the persisted sentinel pid is in the safe range.
        $this->assertGreaterThanOrEqual(
            999100,
            $row['pid'],
            'Persisted patient_id must be in sentinel range ≥ 999100 (no real PID in extraction table).',
        );
    }

    // ---------------------------------------------------------------------------
    // Test 3: Refused extraction still lands in refused
    // ---------------------------------------------------------------------------

    /**
     * An agent refusal (status='refused') must be persisted as-is.  The gate
     * must NOT mistakenly rewrite 'refused' to 'pending_review'.
     */
    public function testRefusedExtractionStillLandsInRefused(): void
    {
        $refusedResponse = [
            'status'     => ExtractionStatus::REFUSED,
            'doc_ref_id' => '502',
            'doc_type'   => 'lab_pdf',
            'request_id' => 'req-refused-test',
        ];

        $roundtripService = $this->makeRoundtripServiceMock();
        $roundtripService->expects($this->never())->method('roundtrip');

        $subscriber = $this->makeSubscriber(
            $this->makeAgentClientMock($refusedResponse),
            $roundtripService,
            $this->makePathResolverMock(),
        );

        $subscriber->onDocumentCreated($this->makeEvent(documentId: 502));

        $this->assertCount(1, $subscriber->persistedRows, 'One row should be persisted for a refused extraction.');

        $this->assertSame(
            ExtractionStatus::REFUSED,
            $subscriber->persistedRows[0]['status'],
            'A refused extraction must land in status="refused". '
            . 'The gate must not rewrite refusal to pending_review.',
        );
    }

    // ---------------------------------------------------------------------------
    // Test 4: Error extraction still lands in error
    // ---------------------------------------------------------------------------

    /**
     * An agent error (status='error') must be persisted as 'error'.  The gate
     * must NOT rewrite 'error' to 'pending_review'.
     */
    public function testErroredExtractionStillLandsInError(): void
    {
        $errorResponse = [
            'status'     => ExtractionStatus::ERROR,
            'doc_ref_id' => '503',
            'doc_type'   => 'lab_pdf',
            'request_id' => 'req-error-test',
        ];

        $roundtripService = $this->makeRoundtripServiceMock();
        $roundtripService->expects($this->never())->method('roundtrip');

        $subscriber = $this->makeSubscriber(
            $this->makeAgentClientMock($errorResponse),
            $roundtripService,
            $this->makePathResolverMock(),
        );

        $subscriber->onDocumentCreated($this->makeEvent(documentId: 503));

        $this->assertCount(1, $subscriber->persistedRows);

        $this->assertSame(
            ExtractionStatus::ERROR,
            $subscriber->persistedRows[0]['status'],
            'An errored extraction must land in status="error". '
            . 'The gate must not rewrite error to pending_review.',
        );
    }

    // ---------------------------------------------------------------------------
    // Test 5: markPriorAttemptsInactive is still called (IS_ACTIVE invariant)
    // ---------------------------------------------------------------------------

    /**
     * P2 IS_ACTIVE invariant must survive the P4 R1 gate removal.
     *
     * markPriorAttemptsInactive() must be called exactly once on the
     * RoundtripService even though roundtrip() itself is no longer called.
     *
     * This guards against the regression where removing the roundtrip call
     * accidentally also removes the is_active guard.
     */
    public function testMarkPriorAttemptsInactiveStillCalled(): void
    {
        $roundtripService = $this->makeRoundtripServiceMock();

        $roundtripService->expects($this->once())
            ->method('markPriorAttemptsInactive')
            ->with(
                $this->equalTo('501'),  // doc_ref_id (string)
                $this->equalTo('lab_pdf'),
            );

        // roundtrip must NOT be called (gate).
        $roundtripService->expects($this->never())->method('roundtrip');

        $subscriber = $this->makeSubscriber(
            $this->makeAgentClientMock($this->makeSuccessAgentResponse()),
            $roundtripService,
            $this->makePathResolverMock(),
        );

        $subscriber->onDocumentCreated($this->makeEvent(documentId: 501));
    }

    // ---------------------------------------------------------------------------
    // Test 6: No PHI in logger context
    // ---------------------------------------------------------------------------

    /**
     * Logger calls from onDocumentCreated must contain only structural keys.
     * No patient_id value, no field values, no block text, no raw doc path
     * containing patient names.
     *
     * The spy logger records every log call.  After onDocumentCreated we assert
     * that no context array contains 'patient_id', 'pid', or any key whose
     * value looks like a real patient ID (i.e. a value NOT in the sentinel range
     * 999100-999199).
     */
    public function testNoLeakOfPHIInLoggerContext(): void
    {
        /** @var array<int, array{level: string, message: string, context: array<string, mixed>}> $logEntries */
        $logEntries = [];

        // Spy logger captures all PSR-3 calls.
        $spyLogger = new class ($logEntries) extends \Psr\Log\AbstractLogger {
            /** @param array<int, array{level: string, message: string, context: array<string, mixed>}> $entries */
            public function __construct(private array &$entries) {}

            /** @param array<string, mixed> $context */
            public function log(mixed $level, \Stringable|string $message, array $context = []): void
            {
                $this->entries[] = [
                    'level'   => (string) $level,
                    'message' => (string) $message,
                    'context' => $context,
                ];
            }
        };

        $subscriber = $this->makeSubscriber(
            $this->makeAgentClientMock($this->makeSuccessAgentResponse()),
            $this->makeRoundtripServiceMock(),
            $this->makePathResolverMock(),
            $spyLogger,
        );

        $subscriber->onDocumentCreated($this->makeEvent());

        // There should be at least one log entry (the "triggered" info + the
        // "extraction persisted" info).
        $this->assertNotEmpty($logEntries, 'Subscriber must emit at least one log entry.');

        // Assert no context key is 'patient_id' or 'pid', and no context value
        // is an integer that looks like a real (non-sentinel) patient PID.
        foreach ($logEntries as $entry) {
            $context = $entry['context'];

            $this->assertArrayNotHasKey(
                'patient_id',
                $context,
                'Logger context must not expose raw patient_id key. Entry: ' . $entry['message'],
            );

            $this->assertArrayNotHasKey(
                'pid',
                $context,
                'Logger context must not expose raw pid key. Entry: ' . $entry['message'],
            );

            // Verify that if any integer value appears in the context, it is
            // either a structural counter (extraction_id, latency_ms, n_blocks,
            // attempt_n) OR it is in the sentinel range (≥ 999100).
            // A real OpenEMR pid (e.g. 1, 2, 3, 4) appearing as a bare int
            // context value would be a PHI leak.
            foreach ($context as $key => $value) {
                if (is_int($value) && $value > 0 && $value < 999100) {
                    // Only permitted structural-counter keys may carry small ints.
                    $allowedSmallIntKeys = [
                        'category_id', 'attempt_n', 'latency_ms', 'n_blocks',
                        'total_fields', 'stripped_fields', 'line',
                    ];
                    $this->assertContains(
                        $key,
                        $allowedSmallIntKeys,
                        "Logger context key '{$key}' carries a small int value ({$value}) "
                        . "that could be a real patient PID. Either use a sentinel ID "
                        . "(≥ 999100) or restrict to known structural-counter keys.",
                    );
                }
            }
        }
    }

    // ---------------------------------------------------------------------------
    // Test 7: Source-level gate verification
    // ---------------------------------------------------------------------------

    /**
     * Source-analysis gate: DocumentSavedSubscriber must NOT call
     * $this->roundtripService->roundtrip( inside onDocumentCreated's
     * success path.
     *
     * This is a belt-and-suspenders structural check that complements the
     * mock-based testGateRemovesSynchronousRoundtripCall.  It will catch any
     * regression where the roundtrip call is re-added as dead code that happens
     * not to execute in the mock scenario.
     *
     * If backend-openemr-teammate's implementation still contains the roundtrip
     * call in onDocumentCreated, this test will block the PR.
     */
    public function testSourceConfirmsRoundtripNotCalledInOnDocumentCreated(): void
    {
        $sourcePath = __DIR__
            . '/../../../../interface/modules/custom_modules'
            . '/oe-module-clinical-copilot/src/EventSubscriber/DocumentSavedSubscriber.php';

        self::assertFileExists($sourcePath, 'DocumentSavedSubscriber.php must exist.');
        $source = file_get_contents($sourcePath);
        self::assertIsString($source);

        // Isolate the onDocumentCreated method body.
        $methodStart = strpos($source, 'public function onDocumentCreated(');
        self::assertNotFalse($methodStart, 'onDocumentCreated method must exist.');

        // Find the next public/protected/private function declaration to bound the body.
        $nextMethod = strpos($source, "\n    protected function ", (int) $methodStart + 1);
        if ($nextMethod === false) {
            $nextMethod = strpos($source, "\n    private function ", (int) $methodStart + 1);
        }
        if ($nextMethod === false) {
            $nextMethod = strpos($source, "\n    public function ", (int) $methodStart + 1);
        }

        $methodBody = $nextMethod !== false
            ? substr($source, (int) $methodStart, $nextMethod - (int) $methodStart)
            : substr($source, (int) $methodStart);

        $this->assertStringNotContainsString(
            '->roundtrip(',
            $methodBody,
            'onDocumentCreated must NOT contain a ->roundtrip() call (P4 R1 gate). '
            . 'The synchronous round-trip has been removed. If this fails, the gate '
            . 'regression has been introduced and the PR must be blocked.',
        );
    }

    // ---------------------------------------------------------------------------
    // Test 8: Source confirms status translation exists
    // ---------------------------------------------------------------------------

    /**
     * Source-analysis: the subscriber must translate agent status 'ok' to
     * ExtractionStatus::PENDING_REVIEW before calling persistExtractionRow.
     *
     * Checks that the source contains the translation expression rather than
     * passing the raw $agentStatus directly.
     */
    public function testSourceConfirmsStatusTranslationToPendingReview(): void
    {
        $sourcePath = __DIR__
            . '/../../../../interface/modules/custom_modules'
            . '/oe-module-clinical-copilot/src/EventSubscriber/DocumentSavedSubscriber.php';

        self::assertFileExists($sourcePath);
        $source = file_get_contents($sourcePath);
        self::assertIsString($source);

        // Must reference the ExtractionStatus constant for PENDING_REVIEW.
        $this->assertStringContainsString(
            'ExtractionStatus::PENDING_REVIEW',
            $source,
            'DocumentSavedSubscriber must reference ExtractionStatus::PENDING_REVIEW '
            . '(P4 R1: successful extractions land in pending_review, not ok).',
        );

        // The translation must be conditional on the agent returning 'ok'
        // (or ExtractionStatus::OK) — refused/error must pass through verbatim.
        $this->assertStringContainsString(
            'ExtractionStatus::OK',
            $source,
            'DocumentSavedSubscriber must check ExtractionStatus::OK before '
            . 'deciding to rewrite the status to pending_review.',
        );
    }
}
