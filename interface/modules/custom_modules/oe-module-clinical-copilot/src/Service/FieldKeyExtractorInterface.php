<?php

/**
 * Contract for computing a stable natural key from an extracted field row.
 *
 * Implementations normalize whitespace and case so that two rows that
 * represent the same clinical fact (e.g. two extractions of the same
 * "Cholesterol, Total" result) produce identical keys regardless of minor
 * formatting differences in the source document.
 *
 * The key is used by RoundtripService to decide whether a clinical-table
 * row already exists across extraction attempts (cross-attempt deduplication
 * via a JOIN through co_pilot_extractions.doc_ref_id).
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    AgentForge Team
 * @copyright Copyright (c) 2026 AgentForge
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\Service;

interface FieldKeyExtractorInterface
{
    /**
     * Compute a stable natural key for one extracted field row.
     *
     * The key must be:
     *  - Deterministic: same logical field always produces the same key.
     *  - Stable across extraction attempts: minor whitespace/case differences
     *    in the source document do not produce different keys.
     *  - Unique enough within a (doc_ref_id, doc_type) scope to identify
     *    the specific clinical fact without false duplicates.
     *
     * Normalization contract: implementations MUST apply
     *   strtolower(trim(preg_replace('/\s+/', ' ', $value)))
     * to every string component before concatenating.
     *
     * @param string               $docRefId  OpenEMR documents.id (string scope key)
     * @param array<string, mixed> $fieldRow  One entry from the extraction payload
     *                                        (e.g. a results[i] or allergies[i] element)
     * @return string  Opaque stable key, safe for direct equality comparison
     */
    public function naturalKey(string $docRefId, array $fieldRow): string;
}
