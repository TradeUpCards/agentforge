<?php

/**
 * One-shot backfill: round-trip every co_pilot_extractions row with
 * status='ok' that does not yet have any co_pilot_fhir_links entries.
 *
 * Usage (from inside the openemr container):
 *   php /var/www/localhost/htdocs/openemr/interface/modules/custom_modules/oe-module-clinical-copilot/bin/backfill_roundtrip.php
 *
 * Idempotent — RoundtripService's UNIQUE-constraint logic skips
 * already-linked rows even if this script is run multiple times.
 *
 * NOT a long-lived utility — exists to bridge the period between the
 * subscriber wiring landing and re-uploading test docs.  Safe to delete
 * once the wiring proves out on fresh uploads.
 *
 * @package   OpenEMR
 * @copyright Copyright (c) 2026 AgentForge
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

// Fake CLI server vars so interface/globals.php doesn't bail on
// undefined HTTP_HOST / REQUEST_URI.  Default site is "default".
$_SERVER['HTTP_HOST']    = $_SERVER['HTTP_HOST']    ?? 'localhost';
$_SERVER['REQUEST_URI']  = $_SERVER['REQUEST_URI']  ?? '/cli/backfill';
$_SERVER['SCRIPT_NAME']  = $_SERVER['SCRIPT_NAME']  ?? '/cli/backfill.php';
$_GET['site']            = $_GET['site']            ?? 'default';

// Bootstrap OpenEMR enough to get DB + autoloader.
$ignoreAuth = true;
require_once __DIR__ . '/../../../../../interface/globals.php';

use OpenEMR\Common\Database\QueryUtils;
use OpenEMR\Modules\ClinicalCopilot\Service\PersonaMap;
use OpenEMR\Modules\ClinicalCopilot\Service\RoundtripService;

// Register the module namespace if not already loaded.
spl_autoload_register(static function (string $class): void {
    $prefix = 'OpenEMR\\Modules\\ClinicalCopilot\\';
    if (!str_starts_with($class, $prefix)) {
        return;
    }
    $relative = str_replace('\\', DIRECTORY_SEPARATOR, substr($class, strlen($prefix)));
    $path     = __DIR__ . '/../src/' . $relative . '.php';
    if (file_exists($path)) {
        require_once $path;
    }
});

$service = new RoundtripService();

$rows = QueryUtils::fetchRecords(
    "SELECT id, patient_id, doc_ref_id, doc_type, extraction_json
       FROM co_pilot_extractions
      WHERE status = 'ok'
        AND extraction_json IS NOT NULL
      ORDER BY id ASC",
    [],
);

echo sprintf("Found %d candidate extraction rows.\n", count($rows));

foreach ($rows as $row) {
    $extractionId = (int) $row['id'];
    $sentinelPid  = (int) $row['patient_id'];
    $docRefId     = (string) $row['doc_ref_id'];
    $docType      = (string) $row['doc_type'];
    $payload      = json_decode((string) $row['extraction_json'], true);

    if (!is_array($payload)) {
        echo "  ext_id={$extractionId} doc_ref={$docRefId} ─ extraction_json invalid, skipping\n";
        continue;
    }

    // Sentinel → real pid: invert PersonaMap.  Real pid p maps to
    // sentinel 999_000 + p (PersonaMap::sentinelId), so the reverse is
    // real_pid = sentinel - 999_000 for sentinels in [999_001, 999_999].
    // Older extraction rows in the legacy [999_101, 999_199] range still
    // resolve correctly under the same arithmetic.
    $realPid = PersonaMap::realPid($sentinelPid) ?? $sentinelPid;

    echo sprintf(
        "  ext_id=%d doc_ref=%s doc_type=%s sentinel_pid=%d real_pid=%d → ",
        $extractionId, $docRefId, $docType, $sentinelPid, $realPid,
    );

    $result = $service->roundtrip(
        extractionId: $extractionId,
        patientId:    $realPid,
        docType:      $docType,
        extraction:   $payload,
    );

    echo sprintf(
        "obs=%d allergies=%d meds=%d errors=%d\n",
        $result['observations'],
        $result['allergies'],
        $result['medications'],
        count($result['errors']),
    );
}

echo "Done.\n";
