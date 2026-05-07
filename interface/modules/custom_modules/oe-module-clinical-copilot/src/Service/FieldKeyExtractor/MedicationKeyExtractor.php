<?php

/**
 * Natural-key extractor for intake_form medication rows.
 *
 * Key components:
 *   doc_ref_id + ":medication:" + normalised(name)
 *
 * `name` is the primary identity discriminator for a medication row.
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

final class MedicationKeyExtractor implements FieldKeyExtractorInterface
{
    public function naturalKey(string $docRefId, array $fieldRow): string
    {
        $name = $this->normalize((string) ($fieldRow['name'] ?? ''));
        return $docRefId . ':medication:' . $name;
    }

    private function normalize(string $value): string
    {
        return strtolower(trim(preg_replace('/\s+/', ' ', $value) ?? ''));
    }
}
