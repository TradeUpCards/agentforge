<?php

/**
 * Demo persona map: real OpenEMR pid → sentinel patient_id.
 *
 * Used only during the graded demo; the four test patients (Chen, Whitaker,
 * Reyes, Kowalski) are mapped to the 999101-999104 sentinel range defined in
 * W2_ARCHITECTURE.md §8.2 so that no real patient identifiers flow into the
 * agent or extraction tables.
 *
 * TODO Thursday: replace the persona-map lookup with a direct pass-through of
 * the session-derived patient_id once real patients exist in the demo stack.
 * In production, the subscriber should use the document's foreign_id (pid)
 * directly — never a hardcoded mapping.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    AgentForge Team
 * @copyright Copyright (c) 2026 AgentForge
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\Service;

/**
 * Maps the four demo patient pids to their sentinel patient_ids.
 *
 * Returns null for any pid not in the demo set; callers must treat
 * null as "skip — not a demo persona" (structural log, no agent call).
 */
final class PersonaMap
{
    /**
     * Real OpenEMR pid → sentinel patient_id for the four graded personas.
     *
     * Keys are the actual OpenEMR patient pid values loaded in the demo
     * fixture data.  Values are the sentinel ids (999101-999104) that the
     * agent service uses as its patient namespace.
     *
     * TODO Thursday: remove this map and use session pid directly once the
     * demo fixture data is wired to use the sentinel range natively.
     *
     * @var array<int, int>
     */
    private const PERSONA_MAP = [
        1 => 999101, // Chen
        2 => 999102, // Whitaker
        3 => 999103, // Reyes
        4 => 999104, // Kowalski
    ];

    /**
     * Resolve a real OpenEMR pid to the corresponding sentinel patient_id.
     *
     * @param int $pid Real OpenEMR patient identifier from the document record.
     * @return int|null Sentinel patient_id, or null if the pid is not a demo persona.
     */
    public static function sentinelId(int $pid): ?int
    {
        return self::PERSONA_MAP[$pid] ?? null;
    }

    /**
     * Returns true when the given pid belongs to one of the four demo personas.
     */
    public static function isDemoPersona(int $pid): bool
    {
        return array_key_exists($pid, self::PERSONA_MAP);
    }
}
