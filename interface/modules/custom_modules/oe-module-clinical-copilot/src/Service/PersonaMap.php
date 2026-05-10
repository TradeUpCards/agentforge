<?php

/**
 * Persona map: real OpenEMR pid ↔ sentinel patient_id.
 *
 * Auto-derives a sentinel id from the real OpenEMR pid using a fixed
 * offset, so all live patients in the demo install (currently 200, pid
 * 1-200) flow through the agent without per-pid map maintenance.
 *
 * The sentinel range matches the agent service's pinned validator
 * (see agent/document_schemas.py::_SENTINEL_MIN/_MAX): real pid p maps
 * to sentinel `999_000 + p` for p in [1, 999], yielding sentinels in
 * [999_001, 999_999].  Real pids outside [1, 999] return null (caller
 * treats null as "skip — agent extraction not enabled for this pid").
 *
 * PHI-safety story:
 *   - The offset still ensures no real pid leaks into agent logs /
 *     Langfuse traces / structured observability — those see only
 *     sentinels.
 *   - Sentinel space is bounded to [999_000, 999_999] (1000 slots),
 *     reserved for AgentForge use; can't collide with Synthea or any
 *     other live data range.
 *   - Reverse lookup is `real_pid = sentinel - 999_000` — used by
 *     RoundtripService and backfill_roundtrip.php to write to the
 *     correct OpenEMR clinical-table row.
 *
 * History:
 *   - W2 demo originally hardcoded 4 personas (Chen, Whitaker, Reyes,
 *     Kowalski → pid 1-4 → sentinels 999_101-999_104) so testing was
 *     scoped to known fixtures.
 *   - Switched to auto-derive 2026-05-09 (right before W2 final
 *     submission) so the agent works against any pid in the demo
 *     install without map-edit overhead.  Old four-persona pids are
 *     still in the new range (sentinels 999_001-999_004 instead of
 *     999_101-999_104) — old extraction rows referencing 999_101
 *     etc. remain readable since the validator's expanded range
 *     covers them too.
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
 * Maps OpenEMR real pids to AgentForge sentinel patient_ids.
 *
 * Auto-derive: sentinel = 999_000 + real_pid, for real_pid ∈ [1, 999].
 * Returns null for any pid outside that range; callers must treat
 * null as "skip — not eligible for agent extraction" (structural log,
 * no agent call).
 */
final class PersonaMap
{
    /**
     * Sentinel range — must match agent/document_schemas.py constants.
     * Real pid p in [1, 999] maps to sentinel 999_000 + p.
     */
    public const SENTINEL_OFFSET = 999_000;
    public const REAL_PID_MIN    = 1;
    public const REAL_PID_MAX    = 999;
    public const SENTINEL_MIN    = self::SENTINEL_OFFSET + self::REAL_PID_MIN;
    public const SENTINEL_MAX    = self::SENTINEL_OFFSET + self::REAL_PID_MAX;

    /**
     * Resolve a real OpenEMR pid to the corresponding sentinel patient_id.
     *
     * @param int $pid Real OpenEMR patient identifier from the document record.
     * @return int|null Sentinel patient_id, or null if the pid is outside
     *                  the demo-eligible range [1, 999].
     */
    public static function sentinelId(int $pid): ?int
    {
        if ($pid < self::REAL_PID_MIN || $pid > self::REAL_PID_MAX) {
            return null;
        }
        return self::SENTINEL_OFFSET + $pid;
    }

    /**
     * Reverse lookup: sentinel patient_id → real OpenEMR pid.
     *
     * Used by RoundtripService and backfill scripts when reading an
     * extraction row (which stores the sentinel) and writing back to
     * OpenEMR clinical tables (which use real pids as foreign keys).
     *
     * @param int $sentinelPid Sentinel patient_id from co_pilot_extractions.
     * @return int|null Real OpenEMR pid, or null if the sentinel is
     *                  outside the reserved range.
     */
    public static function realPid(int $sentinelPid): ?int
    {
        if ($sentinelPid < self::SENTINEL_MIN || $sentinelPid > self::SENTINEL_MAX) {
            return null;
        }
        return $sentinelPid - self::SENTINEL_OFFSET;
    }

    /**
     * Returns true when the given real pid is in the demo-eligible range.
     *
     * Eligibility means the agent can be invoked for documents filed under
     * this pid; it does NOT mean the patient exists in the OpenEMR DB.
     */
    public static function isDemoPersona(int $pid): bool
    {
        return $pid >= self::REAL_PID_MIN && $pid <= self::REAL_PID_MAX;
    }
}
