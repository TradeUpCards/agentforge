import { fhirGet } from '../fhirClient'
import type { FhirBundle, FhirDocumentReference } from '../../types/fhir'

/**
 * FHIR DocumentReference search for a patient — backs both the dashboard
 * Documents card (top 5 most recent) and the /dashboard/:id/documents
 * full-page route.
 *
 * Sort: newest first by `date` (the document's authored / creation date).
 * Cap: the caller passes `count`. Card uses 5; full page uses 50.
 */
export const getDocuments = (
  patientId: string,
  token: string,
  count = 50,
): Promise<FhirBundle<FhirDocumentReference>> =>
  fhirGet<FhirBundle<FhirDocumentReference>>(
    `/DocumentReference?patient=${encodeURIComponent(patientId)}&_sort=-date&_count=${count}`,
    token,
  )
