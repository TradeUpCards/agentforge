<?php

/**
 * Resolves the absolute filesystem path for a stored OpenEMR document.
 *
 * Extracted from DocumentSavedSubscriber so the same path-resolution logic
 * can be used by both the event subscriber and the reprocess endpoint without
 * duplication.
 *
 * Only filesystem-stored documents (storagemethod = 0) are supported.
 * CouchDB-backed documents return null; the caller must treat null as an
 * unresolvable path and record an error extraction row.
 *
 * ENCRYPTION NOTE:
 *   OpenEMR encrypts uploaded documents at rest via CryptoGen (aes-256-gcm).
 *   The bytes on disk are NOT raw PDF/PNG — they are ciphertext.  This class
 *   decrypts via Document::decrypt_content() and writes the plaintext to a
 *   temporary file so AgentClient can stream it without double-buffering.
 *   The temporary file is the caller's responsibility to unlink.
 *
 * NO-PHI LOGGING:
 *   Only the structural document id is logged.  Raw file paths, file contents,
 *   and patient-name segments in filenames are never written to logs.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    AgentForge Team
 * @copyright Copyright (c) 2026 AgentForge
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\Service;

use Document as OpenEMRDocument;
use OpenEMR\Common\Database\QueryUtils;
use OpenEMR\Common\Logging\SystemLogger;
use Psr\Log\LoggerInterface;

final class DocumentPathResolver
{
    private readonly LoggerInterface $logger;

    public function __construct(?LoggerInterface $logger = null)
    {
        $this->logger = $logger ?? new SystemLogger();
    }

    /**
     * Resolve the absolute filesystem path for a document, decrypting if needed.
     *
     * Returns null when:
     *  - The document row does not exist or is deleted.
     *  - The document uses CouchDB storage (storagemethod != 0).
     *  - The file cannot be read from disk.
     *  - A temp file for the decrypted plaintext cannot be created.
     *
     * The returned path points to a temporary file containing the plaintext
     * bytes; the caller is responsible for unlinking it after use.
     *
     * @param int $docId  OpenEMR documents.id
     * @return string|null  Absolute path to a temp file with decrypted bytes, or null.
     */
    public function resolve(int $docId): ?string
    {
        $row = QueryUtils::fetchRecords(
            'SELECT `url`, `storagemethod` FROM `documents` WHERE `id` = ? AND `deleted` = 0 LIMIT 1',
            [$docId],
        );

        if (empty($row) || (int) ($row[0]['storagemethod'] ?? -1) !== 0) {
            $this->logger->info('ClinicalCopilot/DocumentPathResolver: non-filesystem storage or missing doc', [
                'doc_id' => $docId,
            ]);
            return null;
        }

        $url  = (string) ($row[0]['url'] ?? '');
        $path = str_starts_with($url, 'file://') ? substr($url, 7) : $url;

        if ($path === '' || !file_exists($path)) {
            $this->logger->error('ClinicalCopilot/DocumentPathResolver: file not found on disk', [
                'doc_id' => $docId,
            ]);
            return null;
        }

        $rawBytes = @file_get_contents($path);
        if ($rawBytes === false) {
            $this->logger->error('ClinicalCopilot/DocumentPathResolver: file_get_contents failed', [
                'doc_id' => $docId,
            ]);
            return null;
        }

        // Decrypt at rest — Document::decrypt_content() handles aes-256-gcm.
        // If the file is not actually encrypted on this OpenEMR install, the
        // method will throw; we fall back to raw bytes.
        $document = new OpenEMRDocument($docId);
        try {
            $plainBytes = $document->decrypt_content($rawBytes);
        } catch (\Throwable) {
            $plainBytes = $rawBytes;
        }

        $tmpPath = tempnam(sys_get_temp_dir(), 'copilot_plain_');
        if ($tmpPath === false || @file_put_contents($tmpPath, $plainBytes) === false) {
            $this->logger->error('ClinicalCopilot/DocumentPathResolver: temp file creation failed', [
                'doc_id' => $docId,
            ]);
            return null;
        }

        return $tmpPath;
    }
}
