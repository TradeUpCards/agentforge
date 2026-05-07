import { fhirGet } from '../fhirClient'
import type { FhirBundle, FhirMedicationRequest } from '../../types/fhir'

export const getMedications = (patientId: string, token: string) =>
  fhirGet<FhirBundle<FhirMedicationRequest>>(
    `/MedicationRequest?patient=${encodeURIComponent(patientId)}&status=active&_count=50`,
    token,
  )
