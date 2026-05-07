import { fhirGet } from '../fhirClient'
import type { FhirBundle, FhirEncounter } from '../../types/fhir'

export const getEncounters = (patientId: string, token: string) =>
  fhirGet<FhirBundle<FhirEncounter>>(
    `/Encounter?patient=${encodeURIComponent(patientId)}&_sort=-date&_count=10`,
    token,
  )
