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
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    AgentForge Team
 * @copyright Copyright (c) 2026 AgentForge
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\EventSubscriber;

use Document as OpenEMRDocument;
use OpenEMR\Common\Database\QueryUtils;
use OpenEMR\Common\Logging\SystemLogger;
use OpenEMR\Modules\ClinicalCopilot\Events\DocumentCreatedEvent;
use OpenEMR\Modules\ClinicalCopilot\Service\AgentClient;
use OpenEMR\Modules\ClinicalCopilot\Service\PersonaMap;
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

    public function __construct(
        private readonly AgentClient $agentClient,
        ?LoggerInterface $logger = null,
    ) {
        $this->logger = $logger ?? new SystemLogger();
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
     *     has an extraction row.
     *  5. Resolves the filesystem path via a Document instance.
     *  6. Calls AgentClient::attachAndExtract.
     *  7. Persists the result row in co_pilot_extractions.
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

            // 4. Idempotency: skip if already extracted.
            if ($this->extractionExists($docRefId, $docType)) {
                $this->logger->info('ClinicalCopilot: extraction already exists — idempotent skip', [
                    'doc_ref_id' => $docRefId,
                    'doc_type'   => $docType,
                ]);
                return;
            }

            // 5. Resolve filesystem path via Document instance.
            //    Document::get_filesystem_filepath() handles path depth + site dir.
            //    We call get_data() to also handle encryption transparently.
            $filePath = $this->resolveFilePath($docId);
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

            // 6. Call agent.
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
            ]);

            // 7. Persist result.
            $this->persistExtractionRow(
                pid:           $sentinelPid,
                docRefId:      $docRefId,
                docType:       $docType,
                status:        $agentStatus,
                extractionJson: $extractionJson,
                nBlocks:       $nBlocks,
                confidenceAvg: $confidenceAvg,
                requestId:     $requestId,
            );
        } catch (Throwable $e) {
            // Log structure-only; rethrow is intentionally suppressed so a
            // failed extraction never breaks the upload response for the user.
            $this->logger->error('ClinicalCopilot: DocumentSavedSubscriber encountered an error', [
                'exception' => $e,
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
     * Check whether a co_pilot_extractions row already exists for this
     * (doc_ref_id, doc_type) pair.
     */
    protected function extractionExists(string $docRefId, string $docType): bool
    {
        $rows = QueryUtils::fetchRecords(
            'SELECT `id` FROM `co_pilot_extractions` WHERE `doc_ref_id` = ? AND `doc_type` = ? LIMIT 1',
            [$docRefId, $docType]
        );
        return !empty($rows);
    }

    /**
     * Resolve the absolute filesystem path for a document.
     *
     * Uses `Document::get_filesystem_filepath()` so path-depth adjustments
     * and OE_SITE_DIR resolution happen in the same way as the existing
     * document download flow.
     *
     * Returns null when the document uses CouchDB storage or the path is
     * otherwise unresolvable.
     *
     * NOTE: We do NOT call `Document::get_data()` here because we want the
     * AgentClient to stream the bytes independently (handles large files
     * without double-buffering in PHP).  The subscriber only resolves the
     * path; AgentClient reads and hashes the bytes.
     */
    protected function resolveFilePath(int $docId): ?string
    {
        $doc = new OpenEMRDocument($docId);
        if ($doc->is_deleted()) {
            return null;
        }

        $path = $doc->get_filesystem_filepath();
        if (empty($path) || !file_exists($path)) {
            return null;
        }

        return $path;
    }

    /**
     * Write one row to co_pilot_extractions.
     *
     * Uses QueryUtils::sqlInsert so the statement goes through OpenEMR's
     * standard query layer (audit trail, escaping).
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
    ): void {
        QueryUtils::sqlInsert(
            'INSERT INTO `co_pilot_extractions`
                (`patient_id`, `doc_ref_id`, `doc_type`, `status`,
                 `extraction_json`, `n_blocks`, `extraction_confidence_avg`,
                 `agent_request_id`)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            [
                $pid,
                $docRefId,
                $docType,
                $status,
                $extractionJson,
                $nBlocks,
                $confidenceAvg,
                $requestId,
            ]
        );
    }
}
