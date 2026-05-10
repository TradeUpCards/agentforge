import { fhirGet } from '../fhirClient'
import type { FhirBundle, FhirPatient } from '../../types/fhir'
import {
  getPatientDisplayName,
  getPatientMrn,
  extractBundleResources,
} from '../../utils/fhirParsers'

/**
 * Patient picker source — uses the FHIR `Patient` search.
 *
 * Why FHIR (and not the legacy REST `/api/patient` even though we now
 * carry `api:oemr`): FHIR's `GET /Patient` returns a server-filtered
 * Bundle of patients this user is authorized to see. That filter is
 * Layer 1 of the patient-access defense (§6 of
 * PATIENT_DASHBOARD_MIGRATION.md). We use the legacy REST API only for
 * the Add-Allergy write surface, where FHIR doesn't expose a write
 * route (§15).
 *
 * Pagination: we fetch up to 200 patients in one call. The inline
 * filter on `PatientSelectPage` then narrows that set client-side and
 * paginates 25 per page in the UI. For deployments with >200 active
 * patients we would need to follow the Bundle `link.next` pointers for
 * server-side pagination — that is recorded as future-work.
 */

export interface ApiPatientSummary {
  id: string
  displayName: string
  dob: string
  sex: string
  mrn: string
}

export async function getPatientList(token: string): Promise<ApiPatientSummary[]> {
  const bundle = await fhirGet<FhirBundle<FhirPatient>>('/Patient?_count=200', token)
  const patients = extractBundleResources<FhirPatient>(bundle)
  return patients.map((p) => ({
    id: p.id ?? '',
    displayName: getPatientDisplayName(p),
    dob: p.birthDate ?? '',
    sex: p.gender ?? '',
    mrn: getPatientMrn(p),
  }))
}
