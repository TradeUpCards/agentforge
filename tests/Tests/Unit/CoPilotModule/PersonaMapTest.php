<?php

/**
 * Unit tests for PersonaMap.
 *
 * Verifies that the four demo personas map to the correct sentinel patient_ids
 * and that pids outside the demo set return null / false as expected.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    AgentForge Team
 * @copyright Copyright (c) 2026 AgentForge
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Tests\Unit\CoPilotModule;

use OpenEMR\Modules\ClinicalCopilot\Service\PersonaMap;
use PHPUnit\Framework\Attributes\DataProvider;
use PHPUnit\Framework\TestCase;

// Ensure the module namespace is autoloadable when the module class-loader
// has not been invoked by the OpenEMR module system.
spl_autoload_register(static function (string $class): void {
    $prefix = 'OpenEMR\\Modules\\ClinicalCopilot\\';
    if (!str_starts_with($class, $prefix)) {
        return;
    }
    $relative = str_replace('\\', DIRECTORY_SEPARATOR, substr($class, strlen($prefix)));
    $path     = __DIR__ . '/../../../../interface/modules/custom_modules/oe-module-clinical-copilot/src/'
        . $relative . '.php';
    if (file_exists($path)) {
        require_once $path;
    }
});

final class PersonaMapTest extends TestCase
{
    // ---------------------------------------------------------------------------
    // sentinelId — demo personas resolve to expected sentinel IDs
    // ---------------------------------------------------------------------------

    /**
     * Each of the four demo personas must map to their designated sentinel id
     * in the 999101-999104 range (W2_ARCHITECTURE.md §8.2).
     *
     * @return array<string, array{int, int}>
     *
     * @codeCoverageIgnore Data providers run before coverage instrumentation starts.
     */
    public static function demoPersonaProvider(): array
    {
        return [
            'Chen (pid=1)'      => [1, 999101],
            'Whitaker (pid=2)'  => [2, 999102],
            'Reyes (pid=3)'     => [3, 999103],
            'Kowalski (pid=4)'  => [4, 999104],
        ];
    }

    #[DataProvider('demoPersonaProvider')]
    public function testDemoPersonasMapsToSentinelId(int $pid, int $expectedSentinel): void
    {
        $this->assertSame(
            $expectedSentinel,
            PersonaMap::sentinelId($pid),
            "pid={$pid} should map to sentinel {$expectedSentinel}."
        );
    }

    #[DataProvider('demoPersonaProvider')]
    public function testIsDemoPersonaReturnsTrueForKnownPids(int $pid, int $_sentinel): void
    {
        $this->assertTrue(
            PersonaMap::isDemoPersona($pid),
            "pid={$pid} should be recognised as a demo persona."
        );
    }

    // ---------------------------------------------------------------------------
    // Non-demo pids return null / false
    // ---------------------------------------------------------------------------

    /**
     * @return array<string, array{int}>
     *
     * @codeCoverageIgnore Data providers run before coverage instrumentation starts.
     */
    public static function nonDemoPersonaProvider(): array
    {
        return [
            'pid=0'      => [0],
            'pid=5'      => [5],
            'pid=100'    => [100],
            'pid=999100' => [999100],
            'pid=999101' => [999101], // sentinel itself is not a real pid
        ];
    }

    #[DataProvider('nonDemoPersonaProvider')]
    public function testNonDemoPersonaReturnsNull(int $pid): void
    {
        $this->assertNull(
            PersonaMap::sentinelId($pid),
            "pid={$pid} should not resolve to a sentinel (not a demo persona)."
        );
    }

    #[DataProvider('nonDemoPersonaProvider')]
    public function testIsDemoPersonaReturnsFalseForUnknownPids(int $pid): void
    {
        $this->assertFalse(
            PersonaMap::isDemoPersona($pid),
            "pid={$pid} should not be a demo persona."
        );
    }
}
