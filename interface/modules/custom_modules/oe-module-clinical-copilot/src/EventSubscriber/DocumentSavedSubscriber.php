<?php

/**
 * Reacts to newly-uploaded patient documents and triggers the agent's
 * attach_and_extract pipeline for documents filed under our two auto-extract
 * categories.
 *
 * TWO-ACTOR AUDIT DISCIPLINE (W2_ARCHITECTURE.md §2.1):
 *   This subscriber fires on behalf of the *front-desk* actor (the user who
 *   uploaded the document).  The uploading user's id is the `ownerId` on the
 *   event; it is passed in the HMAC and HTTP header to the agent so the
 *   agent_log row carries the front-desk user_id, NOT the PCP's user_id.
 *   The PCP user_id is only recorded when the PCP later consumes the extracted
 *   data through the chat surface.
 *
 * NO-PHI LOGGING (W2_ARCHITECTURE.md §8.3):
 *   Logs contain only structural identifiers:
 *     - pid_in_demo_set (boolean)
 *     - category_id (int)
 *     - doc_type (string)
 *     - doc_ref_id (string)
 *     - agent_status (ok|refused|error)
 *     - agent_request_id (string|null)
 *     - latency_ms (int)
 *   NEVER: raw filenames with patient names, doc bytes, extracted values.
 *
 * IDEMPOTENCY (W2_ARCHITECTURE.md §2.7):
 *   A duplicate (doc_ref_id, doc_type) row in co_pilot_extractions is
 *   prevented by checking for an existing row before calling the agent.
 *   Same upload re-fired will return the existing extraction without a second
 *   agent call.
 *
 * IS_ACTIVE INVARIANT:
 *   markPriorAttemptsInactive() is called BEFORE resolveFilePath() and the
 *   agent call.  This ensures that even an error-path early return (e.g.
 *   unresolvable file) still deactivates prior attempts before writing the
 *   new error row, maintaining the at-most-one-active invariant.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    AgentForge Team
 * @copyright Copyright (c) 2026 AgentForge
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\EventSubscriber;

use OpenEMR\Common\Database\QueryUtils;
use OpenEMR\Common\Logging\SystemLogger;
use OpenEMR\Modules\ClinicalCopilot\Events\DocumentCreatedEvent;
use OpenEMR\Modules\ClinicalCopilot\Service\AgentClient;
use OpenEMR\Modules\ClinicalCopilot\Service\DocumentPathResolver;
use OpenEMR\Modules\ClinicalCopilot\Service\ExtractedFieldsWriter;
use OpenEMR\Modules\ClinicalCopilot\Service\PersonaMap;
use OpenEMR\Modules\ClinicalCopilot\Service\RoundtripService;
use Psr\Log\LoggerInterface;
use Symfony\Component\EventDispatcher\EventSubscriberInterface;
use Throwable;

class DocumentSavedSubscriber implements EventSubscriberInterface
{
    /**
     * Category-name → doc_type mapping.
     * Must stay in sync with the rows seeded in sql/install.sql.
     *
     * @var array<string, string>
     */
    private const CATEGORY_DOC_TYPE_MAP = [
        'Lab Result (auto-extract)'  => 'lab_pdf',
        'Intake Form (auto-extract)' => 'intake_form',
    ];

    private readonly LoggerInterface $logger;

    private readonly RoundtripService $roundtripService;

    private readonly DocumentPathResolver $pathResolver;

    private readonly ExtractedFieldsWriter $fieldsWriter;

    public function __construct(
        private readonly AgentClient $agentClient,
        ?LoggerInterface $logger = null,
        ?RoundtripService $roundtripService = null,
        ?DocumentPathResolver $pathResolver = null,
        ?ExtractedFieldsWriter $fieldsWriter = null,
    ) {
        $this->logger           = $logger ?? new SystemLogger();
        $this->roundtripService = $roundtripService ?? new RoundtripService($this->logger);
        $this->pathResolver     = $pathResolver ?? new DocumentPathResolver($this->logger);
        $this->fieldsWriter     = $fieldsWriter ?? new ExtractedFieldsWriter($this->logger);
    }

    public static function getSubscribedEvents(): array
    {
        return [
            DocumentCreatedEvent::EVENT_NAME => 'onDocumentCreated',
        ];
    }

    /**
     * Handle a newly-persisted document.
     *
     * The event carries only structural identifiers.  This method:
     *  1. Looks up the category name from the DB.
     *  2. Checks if it is one of our auto-extract categories.
     *  3. Runs the pid through PersonaMap; skips if not a demo persona.
     *  4. Checks idempotency — skips if this (doc_ref_id, doc_type) already
     *     has an active OK extraction row.
     *  5. Marks prior attempts inactive (IS_ACTIVE INVARIANT — before any
     *     file I/O or agent call so even the error path is covered).
     *  6. Resolves the filesystem path via DocumentPathResolver.
     *  7. Calls AgentClient::attachAndExtract.
     *  8. Persists the result row in co_pilot_extractions (incl. docling_blocks_json).
     *  9. Writes co_pilot_extracted_fields rows via ExtractedFieldsWriter.
     * 10. FHIR round-trip for successful extractions.
     */
    public function onDocumentCreated(DocumentCreatedEvent $event): void
    {
        $categoryId = $event->getCategoryId();
        $docId      = $event->getDocumentId();
        $pid        = $event->getPatientId();
        $ownerId    = $event->getOwnerId();

        try {
            // 1. Resolve category name.
            $categoryName = $this->resolveCategoryName($categoryId);
            if ($categoryName === null) {
                return; // category not found; not our concern
            }

            // 2. Is this an auto-extract category?
            $docType = self::CATEGORY_DOC_TYPE_MAP[$categoryName] ?? null;
            if ($docType === null) {
                return; // not one of our categories; nothing to do
            }

            // 3. Persona-map check.
            $inDemoSet    = PersonaMap::isDemoPersona($pid);
            $sentinelPid  = PersonaMap::sentinelId($pid);

            $this->logger->info('ClinicalCopilot: DocumentSavedSubscriber triggered', [
                'category_id'     => $categoryId,
                'doc_type'        => $docType,
                'doc_ref_id'      => (string) $docId,
                'pid_in_demo_set' => $inDemoSet,
            ]);

            if (!$inDemoSet || $sentinelPid === null) {
                $this->logger->info(
                    'ClinicalCopilot: document is not for a demo persona — skipping agent call',
                    ['category_id' => $categoryId, 'doc_type' => $docType]
                );
                return;
            }

            $docRefId = (string) $docId;

            // 4. Idempotency: skip only if an active, successful extraction
            //    already exists.  Refused or error rows do NOT block retry.
            if ($this->activeOkExtractionExists($docRefId, $docType)) {
                $this->logger->info('ClinicalCopilot: active ok extraction already exists — idempotent skip', [
                    'doc_ref_id' => $docRefId,
                    'doc_type'   => $docType,
                ]);
                return;
            }

            // 5. IS_ACTIVE INVARIANT FIX: mark prior attempts inactive BEFORE
            //    any file I/O or agent call.  This ensures the invariant holds
            //    even when resolveFilePath() or the agent call returns early
            //    with an error and we persist an error row below.
            //
            //    Prior code (pre-P3) called markPriorAttemptsInactive() only
            //    just before the successful-path INSERT at step 8, which meant
            //    an error-path early return at step 6 could leave a prior active
            //    row plus a new error row both having is_active=1.
            $this->roundtripService->markPriorAttemptsInactive($docRefId, $docType);

            // 6. Resolve filesystem path via DocumentPathResolver.
            $filePath = $this->pathResolver->resolve($docId);
            if ($filePath === null) {
                $this->logger->error('ClinicalCopilot: could not resolve filesystem path for document', [
                    'doc_ref_id' => $docRefId,
                ]);
                $this->persistExtractionRow(
                    pid:           $sentinelPid,
                    docRefId:      $docRefId,
                    docType:       $docType,
                    status:        'error',
                    extractionJson: null,
                    nBlocks:       null,
                    confidenceAvg: null,
                    requestId:     null,
                );
                return;
            }

            // 7. Call agent.
            $startMs  = (int) round(microtime(true) * 1000);
            $response = $this->agentClient->attachAndExtract(
                patientId: $sentinelPid,
                docRefId:  $docRefId,
                docType:   $docType,
                filePath:  $filePath,
                userId:    $ownerId,
            );
            $latencyMs = (int) round(microtime(true) * 1000) - $startMs;

            $agentStatus  = is_string($response['status'] ?? null) ? $response['status'] : 'error';
            $requestId    = is_string($response['request_id'] ?? null) ? $response['request_id'] : null;
            $nBlocks      = is_int($response['n_blocks'] ?? null) ? $response['n_blocks'] : null;
            $confidenceAvg = isset($response['extraction_confidence_avg'])
                ? (float) $response['extraction_confidence_avg']
                : null;

            // Attempt-chain metadata (P2).  All fields are optional in the
            // agent response; PHP-side defaults ensure backward compatibility
            // with agents that have not yet been updated to emit these keys.
            $templateId    = is_string($response['template_id'] ?? null)    ? $response['template_id']    : null;
            $model         = is_string($response['model'] ?? null)           ? $response['model']          : null;
            $promptVariant = is_string($response['prompt_variant'] ?? null)  ? $response['prompt_variant'] : 'default';
            $attemptN      = is_int($response['attempt_n'] ?? null)          ? $response['attempt_n']      : 1;
            $triggeredBy   = is_string($response['triggered_by'] ?? null)    ? $response['triggered_by']   : 'initial';
            $rawCostUsd    = $response['cost_usd'] ?? null;
            $costUsd       = (is_float($rawCostUsd) || is_int($rawCostUsd))
                ? (float) $rawCostUsd
                : null;
            $agentLatencyMs   = is_int($response['latency_ms'] ?? null)      ? $response['latency_ms']     : null;
            $totalFields   = is_int($response['total_fields'] ?? null)       ? $response['total_fields']   : null;
            $strippedFields = is_int($response['stripped_fields'] ?? null)   ? $response['stripped_fields'] : null;

            // P3: field-level verdicts and Docling block inventory.
            $fieldVerdicts = is_array($response['field_verdicts'] ?? null)
                ? $response['field_verdicts']
                : [];
            $doclingBlocks = is_array($response['docling_blocks'] ?? null)
                ? $response['docling_blocks']
                : [];
            $doclingBlocksJson = $doclingBlocks !== []
                ? json_encode($doclingBlocks, JSON_UNESCAPED_UNICODE)
                : null;

            // extraction_json holds only the validated schema payload — NOT raw doc text.
            $extractionJson = ($agentStatus === 'ok' && isset($response['extraction']))
                ? json_encode($response['extraction'], JSON_UNESCAPED_UNICODE)
                : null;

            $this->logger->info('ClinicalCopilot: agent call completed', [
                'doc_ref_id'    => $docRefId,
                'doc_type'      => $docType,
                'agent_status'  => $agentStatus,
                'request_id'    => $requestId,
                'latency_ms'    => $latencyMs,
                'template_id'   => $templateId,
                'attempt_n'     => $attemptN,
                'triggered_by'  => $triggeredBy,
            ]);

            // 8. Persist result.
            //    Prior attempts were already deactivated at step 5 (IS_ACTIVE
            //    INVARIANT FIX).  No second markPriorAttemptsInactive() call here.
            $extractionId = $this->persistExtractionRow(
                pid:                $sentinelPid,
                docRefId:           $docRefId,
                docType:            $docType,
                status:             $agentStatus,
                extractionJson:     $extractionJson,
                nBlocks:            $nBlocks,
                confidenceAvg:      $confidenceAvg,
                requestId:          $requestId,
                templateId:         $templateId,
                model:              $model,
                promptVariant:      $promptVariant,
                attemptN:           $attemptN,
                triggeredBy:        $triggeredBy,
                costUsd:            $costUsd,
                latencyMs:          $agentLatencyMs,
                totalFields:        $totalFields,
                strippedFields:     $strippedFields,
                doclingBlocksJson:  $doclingBlocksJson,
            );

            // 9. Write per-field verdict rows (P3).
            if ($extractionId > 0 && $fieldVerdicts !== []) {
                $verifiedFieldMap = $this->fieldsWriter->writeAll(
                    $extractionId,
                    $fieldVerdicts,
                    $doclingBlocks,
                );
            } else {
                $verifiedFieldMap = [];
            }

            // 10. FHIR round-trip — only on successful extraction.  Persists
            // derived facts into OpenEMR's clinical tables (procedure_*,
            // lists, prescriptions) so they appear in the chart UI and
            // are queryable via the FHIR API.  Per W2 PRD §1 + §43.
            //
            // NOTE on pid: the round-trip uses the REAL OpenEMR pid (the
            // chart owner), not the sentinel pid the agent service uses.
            // Clinical-table foreign keys must reference the actual
            // patient row.  The sentinel pid namespace exists only for
            // the agent's own audit trail.
            if (
                $agentStatus === 'ok'
                && $extractionJson !== null
                && $extractionId > 0
            ) {
                $extractionPayload = json_decode($extractionJson, true);
                if (is_array($extractionPayload)) {
                    $this->roundtripService->roundtrip(
                        extractionId:    $extractionId,
                        patientId:       $pid,
                        docType:         $docType,
                        extraction:      $extractionPayload,
                        docRefId:        $docRefId,
                        verifiedFieldMap: $verifiedFieldMap,
                        fieldsWriter:    $this->fieldsWriter,
                    );
                }
            }
        } catch (Throwable $e) {
            // Structural-only log: exception class + file:line.  The full
            // exception object (message + stack trace + local vars) is
            // intentionally NOT logged — DB errors can echo column values
            // that include PHI, and the AUDIT.md C-6 no-PHI-in-logs rule
            // is stricter than PSR-3's default exception serialization.
            // Rethrow is suppressed so a failed extraction never breaks
            // the upload response for the user.
            $this->logger->error('ClinicalCopilot: DocumentSavedSubscriber encountered an error', [
                'exception_class' => get_class($e),
                'file'            => basename($e->getFile()),
                'line'            => $e->getLine(),
            ]);
        }
    }

    // -----------------------------------------------------------------------
    // Private helpers
    // -----------------------------------------------------------------------

    /**
     * Look up the category name for the given category id.
     *
     * Returns null if the category does not exist.
     */
    protected function resolveCategoryName(int $categoryId): ?string
    {
        $row = QueryUtils::fetchRecords(
            'SELECT `name` FROM `categories` WHERE `id` = ?',
            [$categoryId]
        );
        $first = $row[0] ?? null;
        return isset($first['name']) ? (string) $first['name'] : null;
    }

    /**
     * Return true only if an active, successfully-completed extraction already
     * exists for this (doc_ref_id, doc_type) pair.
     *
     * Rows with is_active = 0 (superseded attempts) or status != 'ok'
     * (refused / error) are intentionally excluded so that retries can
     * proceed through without being blocked.
     *
     * Prior behaviour (pre-P2): any row — regardless of status — blocked a
     * retry.  That caused refused / error rows to permanently silently skip
     * re-extraction on subsequent uploads or manual reprocess attempts.
     */
    protected function activeOkExtractionExists(string $docRefId, string $docType): bool
    {
        $rows = QueryUtils::fetchRecords(
            'SELECT 1 FROM `co_pilot_extractions`
              WHERE `doc_ref_id` = ?
                AND `doc_type` = ?
                AND `is_active` = 1
                AND `status` = \'ok\'
              LIMIT 1',
            [$docRefId, $docType]
        );
        return !empty($rows);
    }

    /**
     * Write one row to co_pilot_extractions.
     *
     * Uses QueryUtils::sqlInsert so the statement goes through OpenEMR's
     * standard query layer (audit trail, escaping).
     *
     * The P2 attempt-chain columns (template_id through stripped_fields) all
     * default to NULL / their column DEFAULTs when omitted, so existing call
     * sites that pre-date P2 (e.g. the error-path early return above) remain
     * valid without being updated.
     *
     * The P3 column docling_blocks_json defaults to NULL when omitted.
     */
    protected function persistExtractionRow(
        int $pid,
        string $docRefId,
        string $docType,
        string $status,
        ?string $extractionJson,
        ?int $nBlocks,
        ?float $confidenceAvg,
        ?string $requestId,
        ?string $templateId          = null,
        ?string $model               = null,
        string  $promptVariant       = 'default',
        int     $attemptN            = 1,
        string  $triggeredBy         = 'initial',
        ?float  $costUsd             = null,
        ?int    $latencyMs           = null,
        ?int    $totalFields         = null,
        ?int    $strippedFields      = null,
        ?string $doclingBlocksJson   = null,
    ): int {
        return (int) QueryUtils::sqlInsert(
            'INSERT INTO `co_pilot_extractions`
                (`patient_id`, `doc_ref_id`, `doc_type`, `status`,
                 `extraction_json`, `n_blocks`, `extraction_confidence_avg`,
                 `agent_request_id`,
                 `template_id`, `model`, `prompt_variant`,
                 `attempt_n`, `triggered_by`,
                 `cost_usd`, `latency_ms`, `total_fields`, `stripped_fields`,
                 `docling_blocks_json`)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            [
                $pid,
                $docRefId,
                $docType,
                $status,
                $extractionJson,
                $nBlocks,
                $confidenceAvg,
                $requestId,
                $templateId,
                $model,
                $promptVariant,
                $attemptN,
                $triggeredBy,
                $costUsd,
                $latencyMs,
                $totalFields,
                $strippedFields,
                $doclingBlocksJson,
            ]
        );
    }
}
