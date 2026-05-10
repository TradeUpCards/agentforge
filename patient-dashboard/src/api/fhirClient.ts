/**
 * Thin wrapper around `fetch` for the OpenEMR FHIR API.
 *
 * Every call:
 *   - injects the Bearer token from the OIDC user
 *   - sets `Accept: application/fhir+json`
 *   - throws a typed error on non-2xx, with the HTTP status preserved
 *
 * We deliberately do NOT log the response body on error — FHIR responses
 * can contain partial PHI in malformed payloads, and our log-PHI rule is
 * hard.
 *
 * 401 handling note: this layer surfaces the error. Auth recovery (silent
 * renew, redirect to /login) happens in the auth layer, not here. Keeping
 * concerns separated means the FHIR client is independently testable with
 * a mocked fetch.
 */

// Relative paths — handled by:
//   - Dev: Vite proxy (`server.proxy['/apis']` in vite.config.ts) forwards
//          to VITE_OPENEMR_BASE_URL.
//   - Prod: same-origin co-location (§13 of PATIENT_DASHBOARD_MIGRATION.md)
//          places the bundle behind the OpenEMR host.
// Either way the browser sees same-origin requests — no CORS preflight needed.
// (OpenEMR's FHIR endpoint does not send CORS headers by default; only the
// /oauth2 endpoints do, which is why OAuth2 sign-in works against absolute
// URLs but FHIR fetches do not.)
const FHIR_BASE = '/apis/default/fhir'
const STANDARD_API_BASE = '/apis/default/api'

export class FhirError extends Error {
  constructor(
    public readonly status: number,
    public readonly statusText: string,
    public readonly path: string,
  ) {
    super(`FHIR ${status} ${statusText} — ${path}`)
    this.name = 'FhirError'
  }
}

async function request<T>(url: string, token: string, accept: string): Promise<T> {
  const res = await fetch(url, {
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: accept,
    },
  })
  if (!res.ok) {
    // Drain the response so the connection can be reused, but do not include the
    // body in the error — see PHI safety note above.
    void res.text().catch(() => {
      /* ignore */
    })
    throw new FhirError(res.status, res.statusText, url)
  }
  return res.json() as Promise<T>
}

export function fhirGet<T>(path: string, token: string): Promise<T> {
  const url = path.startsWith('http') ? path : `${FHIR_BASE}${path}`
  return request<T>(url, token, 'application/fhir+json')
}

export function standardApiGet<T>(path: string, token: string): Promise<T> {
  const url = path.startsWith('http') ? path : `${STANDARD_API_BASE}${path}`
  return request<T>(url, token, 'application/json')
}

/**
 * POST a FHIR resource. Same auth + error-handling contract as `fhirGet`;
 * throws FhirError on non-2xx.
 *
 * Note: OpenEMR's FHIR R4 server does not currently implement POST on any
 * clinical resource (AllergyIntolerance, Condition, MedicationRequest, etc.
 * — see PATIENT_DASHBOARD_MIGRATION.md §15 for the source-level evidence).
 * The Add-Allergy modal therefore uses `standardApiPost` against OpenEMR's
 * legacy REST API instead. `fhirPost` is kept here for future write
 * surfaces against Patient/Practitioner/Organization (the three resources
 * OpenEMR's FHIR does write).
 */
export async function fhirPost<T>(
  path: string,
  body: unknown,
  token: string,
): Promise<T> {
  const url = path.startsWith('http') ? path : `${FHIR_BASE}${path}`
  const res = await fetch(url, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/fhir+json',
      Accept: 'application/fhir+json',
    },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    void res.text().catch(() => {
      /* ignore */
    })
    throw new FhirError(res.status, res.statusText, url)
  }
  return res.json() as Promise<T>
}

/**
 * POST against OpenEMR's legacy REST API (`/apis/default/api/*`). Used by
 * the Add-Allergy demo. Same auth + PHI-safe error handling as `fhirPost`;
 * throws FhirError on non-2xx so the UI can branch on status code.
 *
 * The legacy REST API gates access on the `api:oemr` OAuth scope (see
 * `BearerTokenAuthorizationStrategy.php` line 373) plus a per-route ACL
 * check at the user level. The OAuth scope is requested in `oidcConfig.ts`;
 * the user-ACL check uses the clinician's existing OpenEMR role
 * permissions.
 */
export async function standardApiPost<T>(
  path: string,
  body: unknown,
  token: string,
): Promise<T> {
  const url = path.startsWith('http') ? path : `${STANDARD_API_BASE}${path}`
  const res = await fetch(url, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json',
      Accept: 'application/json',
    },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    void res.text().catch(() => {
      /* ignore */
    })
    throw new FhirError(res.status, res.statusText, url)
  }
  return res.json() as Promise<T>
}
