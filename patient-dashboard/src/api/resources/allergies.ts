import { fhirGet } from '../fhirClient'
import type { FhirAllergyIntolerance, FhirBundle } from '../../types/fhir'

export const getAllergies = (patientId: string, token: string) =>
  fhirGet<FhirBundle<FhirAllergyIntolerance>>(
    `/AllergyIntolerance?patient=${encodeURIComponent(patientId)}&clinical-status=active&_count=50`,
    token,
  )
