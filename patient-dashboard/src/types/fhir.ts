/**
 * FHIR R4 resource shapes — a focused subset.
 *
 * We only model the fields the dashboard actually reads. The full FHIR R4 spec
 * has hundreds of optional fields; declaring them all would create noise without
 * adding type safety to our display logic.
 *
 * All fields are optional (FHIR servers may omit anything not populated), and
 * parsers in `utils/fhirParsers.ts` handle the missing-data cases.
 */

export interface FhirCoding {
  system?: string
  code?: string
  display?: string
}

export interface FhirCodeableConcept {
  coding?: FhirCoding[]
  text?: string
}

export interface FhirHumanName {
  use?: string
  family?: string
  given?: string[]
  text?: string
}

export interface FhirIdentifier {
  use?: string
  type?: FhirCodeableConcept
  system?: string
  value?: string
}

export interface FhirReference {
  reference?: string
  display?: string
}

export interface FhirPeriod {
  start?: string
  end?: string
}

export interface FhirQuantity {
  value?: number
  unit?: string
  system?: string
  code?: string
}

// --- Patient ---------------------------------------------------------------

export interface FhirPatient {
  resourceType: 'Patient'
  id?: string
  active?: boolean
  name?: FhirHumanName[]
  birthDate?: string
  gender?: 'male' | 'female' | 'other' | 'unknown'
  identifier?: FhirIdentifier[]
}

// --- AllergyIntolerance ----------------------------------------------------

export interface FhirAllergyReaction {
  manifestation?: FhirCodeableConcept[]
  severity?: 'mild' | 'moderate' | 'severe'
  description?: string
}

export interface FhirAllergyIntolerance {
  resourceType: 'AllergyIntolerance'
  id?: string
  clinicalStatus?: FhirCodeableConcept
  verificationStatus?: FhirCodeableConcept
  criticality?: 'low' | 'high' | 'unable-to-assess'
  code?: FhirCodeableConcept
  reaction?: FhirAllergyReaction[]
  recordedDate?: string
}

// --- Condition (Problem List) ---------------------------------------------

export interface FhirCondition {
  resourceType: 'Condition'
  id?: string
  clinicalStatus?: FhirCodeableConcept
  verificationStatus?: FhirCodeableConcept
  category?: FhirCodeableConcept[]
  code?: FhirCodeableConcept
  onsetDateTime?: string
  recordedDate?: string
}

// --- MedicationRequest ----------------------------------------------------

export interface FhirDosageInstruction {
  text?: string
  timing?: { code?: FhirCodeableConcept }
  route?: FhirCodeableConcept
}

export interface FhirMedicationRequest {
  resourceType: 'MedicationRequest'
  id?: string
  status?: string
  intent?: string
  medicationCodeableConcept?: FhirCodeableConcept
  medicationReference?: FhirReference
  authoredOn?: string
  dosageInstruction?: FhirDosageInstruction[]
  requester?: FhirReference
}

// --- MedicationDispense ---------------------------------------------------

export interface FhirMedicationDispense {
  resourceType: 'MedicationDispense'
  id?: string
  status?: string
  medicationCodeableConcept?: FhirCodeableConcept
  medicationReference?: FhirReference
  quantity?: FhirQuantity
  daysSupply?: FhirQuantity
  whenHandedOver?: string
  whenPrepared?: string
}

// --- CareTeam -------------------------------------------------------------

export interface FhirCareTeamParticipant {
  role?: FhirCodeableConcept[]
  member?: FhirReference
  period?: FhirPeriod
}

export interface FhirCareTeam {
  resourceType: 'CareTeam'
  id?: string
  status?: string
  name?: string
  participant?: FhirCareTeamParticipant[]
  period?: FhirPeriod
}

// --- Encounter ------------------------------------------------------------

export interface FhirEncounterParticipant {
  type?: FhirCodeableConcept[]
  individual?: FhirReference
}

export interface FhirEncounter {
  resourceType: 'Encounter'
  id?: string
  status?: string
  class?: FhirCoding
  type?: FhirCodeableConcept[]
  serviceType?: FhirCodeableConcept
  period?: FhirPeriod
  participant?: FhirEncounterParticipant[]
  reasonCode?: FhirCodeableConcept[]
}

// --- DocumentReference -----------------------------------------------------

export interface FhirAttachment {
  contentType?: string
  url?: string
  size?: number
  title?: string
  creation?: string
}

export interface FhirDocumentContent {
  attachment?: FhirAttachment
  format?: FhirCoding
}

export interface FhirDocumentContext {
  encounter?: FhirReference[]
  period?: FhirPeriod
}

export interface FhirDocumentReference {
  resourceType: 'DocumentReference'
  id?: string
  status?: string
  docStatus?: string
  type?: FhirCodeableConcept
  category?: FhirCodeableConcept[]
  subject?: FhirReference
  date?: string
  author?: FhirReference[]
  description?: string
  content?: FhirDocumentContent[]
  context?: FhirDocumentContext
}

// --- Bundle ---------------------------------------------------------------

export interface FhirBundleEntry<T = unknown> {
  fullUrl?: string
  resource?: T
}

export interface FhirBundle<T = unknown> {
  resourceType: 'Bundle'
  type?: string
  total?: number
  entry?: FhirBundleEntry<T>[]
}
