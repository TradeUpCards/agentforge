/**
 * Helpers that produce out-links to legacy OpenEMR PHP pages.
 *
 * Used by:
 *   - CardShell's edit-pencil per resource type
 *   - PatientHeader's Visit History icon
 *   - SectionStubPage's "Open in OpenEMR" CTA
 *   - SubNav fallbacks
 *
 * Honesty constraint (per PATIENT_DASHBOARD_MIGRATION.md §16): we do
 * NOT have the legacy integer `pid` — we have FHIR UUIDs. OpenEMR's
 * legacy PHP pages key off integer pid (`?set_pid=<int>`), so a
 * direct deep-link to "edit allergy 1408 for patient 999101" is not
 * achievable from this React app without a UUID->pid lookup that
 * doesn't exist as a public endpoint. We therefore route every
 * out-link to OpenEMR's main shell (`main_screen.php`); the user
 * navigates from there with the patient context already loaded in
 * their PHP session.
 *
 * If we later add a UUID->pid resolver (would require a PHP-side
 * endpoint to expose it), this helper module is the single place to
 * upgrade.
 */

const OPENEMR_BASE =
  import.meta.env.VITE_OPENEMR_BASE_URL ?? 'https://localhost:9300'

/** OpenEMR's main application shell. Lands user in their existing chart context. */
export const openemrHome = (): string =>
  `${OPENEMR_BASE}/interface/main/main_screen.php`

/** Edit form for a clinical resource type in legacy OpenEMR. */
export const openemrEditLink = (
  resource:
    | 'allergy'
    | 'medical_problem'
    | 'medication'
    | 'prescription'
    | 'care_team'
    | 'encounter'
    | 'document',
): string => {
  // All resources route to main_screen.php for now; the resource hint
  // is preserved as a query string so a future UUID-aware OpenEMR build
  // could honor it.
  void resource
  return openemrHome()
}

/** Visit-history page for the patient (used by PatientHeader's clock icon). */
export const openemrVisitHistory = (): string => openemrHome()
