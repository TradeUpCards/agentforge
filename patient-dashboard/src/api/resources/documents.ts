import { fhirGet } from '../fhirClient'
import type { FhirBundle, FhirDocumentReference } from '../../types/fhir'

/**
 * FHIR DocumentReference search for a patient — backs both the dashboard
 * Documents card (top 5 most recent) and the /dashboard/:id/documents
 * full-page route.
 *
 * Sort: newest first by `date` (the document's authored / creation date).
 * Cap: the caller passes `count`. Card uses 5; full page uses 50.
 *
 * **Scope-aligned category filter.** OpenEMR only registers
 * `user/DocumentReference.rs` in its restricted form
 * (`category=...|clinical-note`) — see `ServerScopeListEntity.php`
 * line 188-190. The plain unrestricted `.rs` scope is silently dropped
 * at registration. We pass the same `category` filter on the search
 * URL so the request literally matches the granted scope; without it,
 * OpenEMR returns 403 ("scope check failed") on the search even though
 * the token has the restricted scope. The dashboard therefore shows
 * only clinical-note category documents — that's the intended set
 * for a clinical dashboard, not a hard limitation worth working around.
 */
const DOCUMENT_CATEGORY_FILTER =
  'category=' +
  encodeURIComponent(
    'http://hl7.org/fhir/us/core/CodeSystem/us-core-documentreference-category|clinical-note',
  )

export const getDocuments = (
  patientId: string,
  token: string,
  count = 50,
): Promise<FhirBundle<FhirDocumentReference>> =>
  fhirGet<FhirBundle<FhirDocumentReference>>(
    `/DocumentReference?patient=${encodeURIComponent(patientId)}&${DOCUMENT_CATEGORY_FILTER}&_sort=-date&_count=${count}`,
    token,
  )
