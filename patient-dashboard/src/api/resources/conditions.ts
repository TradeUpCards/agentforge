import { fhirGet } from '../fhirClient'
import type { FhirBundle, FhirCondition } from '../../types/fhir'

/**
 * NOTE: we deliberately do NOT pass `category=problem-list-item` or
 * `clinical-status=active` as query parameters — OpenEMR's FHIR server
 * returns HTTP 500 when both are combined (verified empirically against
 * docker `development-easy`). We fetch all conditions for the patient
 * and filter client-side in `ProblemsCard.tsx`.
 *
 * Tradeoff: slightly more bytes over the wire. We cap with `_count=50`
 * which is the same upper bound we'd have applied server-side.
 */
export const getConditions = (patientId: string, token: string) =>
  fhirGet<FhirBundle<FhirCondition>>(
    `/Condition?patient=${encodeURIComponent(patientId)}&_count=50`,
    token,
  )
