<?php

/**
 * Factory that returns the correct FieldKeyExtractorInterface implementation
 * for a given (doc_type, writer family) pair.
 *
 * Supported keys:
 *   'lab_pdf:result'        → LabResultKeyExtractor
 *   'intake_form:allergy'   → AllergyKeyExtractor
 *   'intake_form:medication'→ MedicationKeyExtractor
 *
 * Throws \InvalidArgumentException on unknown key so callers know
 * immediately when they pass an unrecognised combination rather than
 * silently getting a wrong key.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    AgentForge Team
 * @copyright Copyright (c) 2026 AgentForge
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\Service;

use OpenEMR\Modules\ClinicalCopilot\Service\FieldKeyExtractor\AllergyKeyExtractor;
use OpenEMR\Modules\ClinicalCopilot\Service\FieldKeyExtractor\LabResultKeyExtractor;
use OpenEMR\Modules\ClinicalCopilot\Service\FieldKeyExtractor\MedicationKeyExtractor;

final class FieldKeyExtractorFactory
{
    /** @var array<string, FieldKeyExtractorInterface> */
    private array $map;

    public function __construct()
    {
        $this->map = [
            'lab_pdf:result'         => new LabResultKeyExtractor(),
            'intake_form:allergy'    => new AllergyKeyExtractor(),
            'intake_form:medication' => new MedicationKeyExtractor(),
        ];
    }

    /**
     * Return the extractor for the given composite key.
     *
     * @param string $compositeKey  "{doc_type}:{family}" — e.g. "lab_pdf:result"
     * @throws \InvalidArgumentException when no extractor is registered for the key
     */
    public function get(string $compositeKey): FieldKeyExtractorInterface
    {
        if (!isset($this->map[$compositeKey])) {
            throw new \InvalidArgumentException(
                'FieldKeyExtractorFactory: no extractor registered for key "' . $compositeKey . '".'
            );
        }

        return $this->map[$compositeKey];
    }
}
