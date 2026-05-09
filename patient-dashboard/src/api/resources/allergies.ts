import { fhirGet, fhirPost } from '../fhirClient'
import type { FhirAllergyIntolerance, FhirBundle } from '../../types/fhir'

export const getAllergies = (patientId: string, token: string) =>
  fhirGet<FhirBundle<FhirAllergyIntolerance>>(
    `/AllergyIntolerance?patient=${encodeURIComponent(patientId)}&clinical-status=active&_count=50`,
    token,
  )

/**
 * Create a new AllergyIntolerance for the given patient. Backs the
 * Add-Allergy modal demo. Requires the `user/AllergyIntolerance.cu`
 * scope on the access token; the OAuth2 client registration script
 * includes it (re-run after first install if you registered before
 * 2026-05-08).
 *
 * Status fields default to "active" / "confirmed" — the demo doesn't
 * surface them in the form. Reaction shape uses one manifestation +
 * one severity, mirroring how the existing OpenEMR data is typically
 * authored.
 */
export interface CreateAllergyInput {
  patientId: string
  substance: string
  reaction?: string
  severity?: 'mild' | 'moderate' | 'severe'
}

export const createAllergy = (input: CreateAllergyInput, token: string) => {
  const body: FhirAllergyIntolerance = {
    resourceType: 'AllergyIntolerance',
    clinicalStatus: {
      coding: [
        {
          system:
            'http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical',
          code: 'active',
        },
      ],
    },
    verificationStatus: {
      coding: [
        {
          system:
            'http://terminology.hl7.org/CodeSystem/allergyintolerance-verification',
          code: 'confirmed',
        },
      ],
    },
    code: { text: input.substance },
    ...(input.reaction || input.severity
      ? {
          reaction: [
            {
              ...(input.reaction
                ? { manifestation: [{ text: input.reaction }] }
                : {}),
              ...(input.severity ? { severity: input.severity } : {}),
            },
          ],
        }
      : {}),
  }
  // The FHIR Patient reference is encoded in the resource via a custom
  // field OpenEMR's writer recognizes. The spec puts patient under
  // `patient.reference` but the type narrowing in our local FhirAllergyIntolerance
  // doesn't expose that field; we cast for the wire shape.
  const wire = {
    ...body,
    patient: { reference: `Patient/${input.patientId}` },
  }
  return fhirPost<FhirAllergyIntolerance>('/AllergyIntolerance', wire, token)
}
