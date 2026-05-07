<?php

/**
 * Natural-key extractor for intake_form allergy rows.
 *
 * Key components:
 *   doc_ref_id + ":allergy:" + normalised(substance)
 *
 * `substance` is the primary identity discriminator for an allergy row.
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

final class AllergyKeyExtractor implements FieldKeyExtractorInterface
{
    public function naturalKey(string $docRefId, array $fieldRow): string
    {
        $substance = $this->normalize((string) ($fieldRow['substance'] ?? ''));
        return $docRefId . ':allergy:' . $substance;
    }

    private function normalize(string $value): string
    {
        return strtolower(trim(preg_replace('/\s+/', ' ', $value) ?? ''));
    }
}
