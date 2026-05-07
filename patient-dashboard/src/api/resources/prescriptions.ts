import { fhirGet } from '../fhirClient'
import type { FhirBundle, FhirMedicationDispense } from '../../types/fhir'

export const getPrescriptions = (patientId: string, token: string) =>
  fhirGet<FhirBundle<FhirMedicationDispense>>(
    `/MedicationDispense?patient=${encodeURIComponent(patientId)}&_sort=-whenHandedOver&_count=20`,
    token,
  )
