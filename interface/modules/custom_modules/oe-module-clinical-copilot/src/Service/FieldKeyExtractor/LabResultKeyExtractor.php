<?php

/**
 * Natural-key extractor for lab_pdf result rows.
 *
 * Key components:
 *   doc_ref_id + ":lab:" + normalised(test_name)
 *
 * `test_name` is the primary identity discriminator within a lab panel.
 * Two results from different lab PDFs will have different doc_ref_id prefixes
 * and therefore different keys even when the test_name matches.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    AgentForge Team
 * @copyright Copyright (c) 2026 AgentForge
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\Service\FieldKeyExtractor;

use OpenEMR\Modules\ClinicalCopilot\Service\FieldKeyExtractorInterface;

final class LabResultKeyExtractor implements FieldKeyExtractorInterface
{
    public function naturalKey(string $docRefId, array $fieldRow): string
    {
        $testName = $this->normalize((string) ($fieldRow['test_name'] ?? ''));
        return $docRefId . ':lab:' . $testName;
    }

    private function normalize(string $value): string
    {
        return strtolower(trim(preg_replace('/\s+/', ' ', $value) ?? ''));
    }
}
