<?php

/**
 * Event fired after a patient document is successfully persisted to the
 * OpenEMR `documents` table and associated with a category.
 *
 * This event is dispatched by `Document::createDocument()` immediately after
 * `$this->persist()` and the `categories_to_documents` insert complete.
 * Subscribers receive the document id, the owning patient pid, and the
 * category id so they can inspect the category without an extra DB query.
 *
 * The event class lives here (module namespace) rather than in the OpenEMR
 * core `Events\PatientDocuments` namespace because:
 *   (a) no equivalent event exists natively in OpenEMR,
 *   (b) the module is the only current consumer.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    AgentForge Team
 * @copyright Copyright (c) 2026 AgentForge
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\Events;

use Symfony\Contracts\EventDispatcher\Event;

final class DocumentCreatedEvent extends Event
{
    /**
     * Symfony event name used as the second argument to
     * `EventDispatcher::dispatch()`.
     */
    public const EVENT_NAME = 'copilot.document.created';

    /**
     * @param int    $documentId  The newly-assigned `documents.id`.
     * @param int    $patientId   The owning patient pid (`documents.foreign_id`).
     * @param int    $categoryId  The category the document was filed under.
     * @param int    $ownerId     The `authUserID` of the uploading user (front-desk).
     */
    public function __construct(
        private readonly int $documentId,
        private readonly int $patientId,
        private readonly int $categoryId,
        private readonly int $ownerId,
    ) {
    }

    public function getDocumentId(): int
    {
        return $this->documentId;
    }

    public function getPatientId(): int
    {
        return $this->patientId;
    }

    public function getCategoryId(): int
    {
        return $this->categoryId;
    }

    public function getOwnerId(): int
    {
        return $this->ownerId;
    }
}
