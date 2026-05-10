import { fhirGet, standardApiPost } from '../fhirClient'
import type { FhirAllergyIntolerance, FhirBundle } from '../../types/fhir'

export const getAllergies = (patientId: string, token: string) =>
  fhirGet<FhirBundle<FhirAllergyIntolerance>>(
    `/AllergyIntolerance?patient=${encodeURIComponent(patientId)}&clinical-status=active&_count=50`,
    token,
  )

/**
 * Create a new allergy for the given patient via OpenEMR's legacy REST
 * API at `POST /apis/default/api/patient/{puuid}/allergy`.
 *
 * Why the legacy API and not FHIR: OpenEMR's FHIR R4 server does not
 * implement create/update for any clinical resource — only Patient,
 * Practitioner, and Organization. See PATIENT_DASHBOARD_MIGRATION.md §15
 * for the source-level evidence and the design tradeoff.
 *
 * Security envelope:
 *   - OAuth2 scope `api:oemr` (requested in `oidcConfig.ts`) is required.
 *     Without it OpenEMR returns 403 at the dispatch layer (see
 *     `BearerTokenAuthorizationStrategy.php` line 373).
 *   - The clinician's user-level ACL must include `patients/med` (see
 *     route handler in `apis/routes/_rest_routes_standard.inc.php` line 279).
 *   - The endpoint converts `puuid` to internal `pid` server-side using
 *     `UuidRegistry::uuidToBytes()`, so the dashboard does not need to
 *     resolve UUID → integer pid in the browser.
 *   - Insert flows through `AllergyIntoleranceService::insert()` which
 *     calls `sqlInsert()` — this writes to the audit log automatically
 *     (see `library/sql.inc.php` and `QueryUtils::sqlInsert`).
 *
 * Body shape: the controller's WHITELISTED_FIELDS constant accepts
 *   { title, begdate, enddate, diagnosis, comments }
 * — see `src/RestControllers/AllergyIntoleranceRestController.php` line 38.
 *
 * Field mapping from the modal:
 *   substance → title         (free text — the allergen name)
 *   reaction  → comments      (free text — observed reaction)
 *   severity  → comments      (appended; the legacy whitelist excludes
 *                              the `severity_al` lists-table column, so
 *                              structured severity isn't writable through
 *                              this endpoint. The modal still collects it
 *                              so a future FHIR write path can use it.)
 *   begdate   → today's ISO date (auto-populated; the modal does not
 *                              expose a date picker — the typical demo
 *                              workflow is "I just discovered this".)
 */
export interface CreateAllergyInput {
  patientId: string
  substance: string
  reaction?: string
  severity?: 'mild' | 'moderate' | 'severe'
}

interface LegacyAllergyResponse {
  validationErrors?: unknown[]
  internalErrors?: unknown[]
  data?: { id?: number; uuid?: string }
}

function buildComments(input: CreateAllergyInput): string | undefined {
  const parts: string[] = []
  if (input.reaction) parts.push(`Reaction: ${input.reaction}`)
  if (input.severity) parts.push(`Severity: ${input.severity}`)
  return parts.length > 0 ? parts.join(' | ') : undefined
}

function todayIso(): string {
  return new Date().toISOString().slice(0, 10) // YYYY-MM-DD
}

export const createAllergy = (input: CreateAllergyInput, token: string) => {
  const body: Record<string, string> = {
    title: input.substance,
    begdate: todayIso(),
  }
  const comments = buildComments(input)
  if (comments) body.comments = comments

  return standardApiPost<LegacyAllergyResponse>(
    `/patient/${encodeURIComponent(input.patientId)}/allergy`,
    body,
    token,
  )
}
