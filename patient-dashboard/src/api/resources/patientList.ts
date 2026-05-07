import { fhirGet } from '../fhirClient'
import type { FhirBundle, FhirPatient } from '../../types/fhir'
import {
  getPatientDisplayName,
  getPatientMrn,
  extractBundleResources,
} from '../../utils/fhirParsers'

/**
 * Patient picker source — uses the FHIR `Patient` search rather than
 * OpenEMR's standard REST API (`/apis/default/api/patient`).
 *
 * Why FHIR: the standard REST API requires the `api:oemr` scope, which
 * we did not request (and would expand the token's authority beyond what
 * the dashboard needs). FHIR's `GET /Patient` accepts the `user/Patient.rs`
 * scope we already have, and the resulting Bundle is server-filtered to
 * the patients this user is authorized to see. This is Layer 1 of the
 * patient-access defense (§6 of PATIENT_DASHBOARD_MIGRATION.md).
 */

export interface ApiPatientSummary {
  id: string
  displayName: string
  dob: string
  sex: string
  mrn: string
}

export async function getPatientList(token: string): Promise<ApiPatientSummary[]> {
  const bundle = await fhirGet<FhirBundle<FhirPatient>>('/Patient?_count=100', token)
  const patients = extractBundleResources<FhirPatient>(bundle)
  return patients.map((p) => ({
    id: p.id ?? '',
    displayName: getPatientDisplayName(p),
    dob: p.birthDate ?? '',
    sex: p.gender ?? '',
    mrn: getPatientMrn(p),
  }))
}
