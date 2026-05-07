import type { FhirBundle, FhirPatient } from '../types/fhir'

/**
 * Pure functions for extracting display data from FHIR resources.
 *
 * These are unit-tested in `utils/fhirParsers.test.ts` (per §11 of
 * PATIENT_DASHBOARD_MIGRATION.md). Keeping them framework-free means
 * tests don't need React or a DOM.
 */

/**
 * Pick the patient's display name. Prefer the official name, fall back to
 * the first available, fall back to a placeholder. Never throws on missing
 * fields.
 */
export function getPatientDisplayName(patient: FhirPatient): string {
  const names = patient.name ?? []
  const official = names.find((n) => n.use === 'official')
  const name = official ?? names[0]
  if (!name) return 'Unknown Patient'
  if (name.text) return name.text
  const given = (name.given ?? []).join(' ')
  return `${given} ${name.family ?? ''}`.trim() || 'Unknown Patient'
}

/**
 * Pull the medical record number. FHIR identifies MRN by the `MR` code
 * within an identifier's type.coding. If no MR-typed identifier exists,
 * fall back to the first identifier with a value, then to a placeholder.
 */
export function getPatientMrn(patient: FhirPatient): string {
  const identifiers = patient.identifier ?? []
  const mr = identifiers.find((id) =>
    (id.type?.coding ?? []).some((c) => c.code === 'MR'),
  )
  if (mr?.value) return mr.value
  const anyWithValue = identifiers.find((id) => id.value)
  return anyWithValue?.value ?? '—'
}

/**
 * `active` defaults to true if the field is omitted (FHIR convention —
 * absence of `active=false` means the patient is active).
 */
export function getPatientActiveStatus(patient: FhirPatient): boolean {
  return patient.active !== false
}

/**
 * Flatten a FHIR Bundle's entries into a typed array, dropping any entry
 * that lacks a resource. The Bundle type is generic so the caller gets
 * the right resource type back.
 */
export function extractBundleResources<T>(bundle: FhirBundle<T>): T[] {
  return (bundle.entry ?? [])
    .map((e) => e.resource)
    .filter((r): r is T => r !== undefined && r !== null)
}
