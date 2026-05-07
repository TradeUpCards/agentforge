import { fhirGet } from '../fhirClient'
import type { FhirPatient } from '../../types/fhir'

export const getPatient = (patientId: string, token: string) =>
  fhirGet<FhirPatient>(`/Patient/${encodeURIComponent(patientId)}`, token)
