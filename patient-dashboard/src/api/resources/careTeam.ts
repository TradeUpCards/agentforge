import { fhirGet } from '../fhirClient'
import type { FhirBundle, FhirCareTeam } from '../../types/fhir'

export const getCareTeam = (patientId: string, token: string) =>
  fhirGet<FhirBundle<FhirCareTeam>>(
    `/CareTeam?patient=${encodeURIComponent(patientId)}&status=active`,
    token,
  )
