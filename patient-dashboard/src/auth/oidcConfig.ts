import { WebStorageStateStore } from 'oidc-client-ts'
import type { AuthProviderProps } from 'react-oidc-context'

/**
 * OIDC client configuration for OpenEMR's OAuth2 server.
 *
 * Flow: Authorization Code + PKCE.
 *
 * **Confidential-client choice (defended in §5 of PATIENT_DASHBOARD_MIGRATION.md).**
 * OpenEMR enforces that `user/` and `system/` scopes are only available to
 * confidential clients (those with a client_secret). A clinician-facing
 * dashboard needs `user/` scopes (it shows multiple patients), so we register
 * as confidential. The secret is delivered to the SPA via `.env` at build time.
 * PKCE remains the actual auth-code protection; the secret is a confused-deputy
 * guard at the token endpoint.
 *
 * Token storage: sessionStorage (cleared on tab close) — SMART on FHIR SPA
 * guidance. We avoid localStorage so an XSS exploit can't persist tokens
 * across browser sessions.
 */

const base = import.meta.env.VITE_OPENEMR_BASE_URL ?? 'https://localhost:9300'
const clientId = import.meta.env.VITE_CLIENT_ID ?? ''
const clientSecret = import.meta.env.VITE_CLIENT_SECRET ?? ''
const redirectUri = import.meta.env.VITE_REDIRECT_URI ?? 'http://localhost:5173/callback'
const postLogoutRedirectUri =
  import.meta.env.VITE_POST_LOGOUT_REDIRECT_URI ?? 'http://localhost:5173/'

const SCOPES = [
  'openid',
  'fhirUser',
  'offline_access',
  // `api:oemr` gates access to OpenEMR's legacy REST API at
  // `/apis/default/api/*`. The Add-Allergy modal uses that surface
  // (`POST /api/patient/{puuid}/allergy`) because OpenEMR's FHIR R4
  // server does not implement write for any clinical resource — see
  // PATIENT_DASHBOARD_MIGRATION.md §15. All FHIR reads continue to
  // run through the FHIR endpoints unchanged.
  'api:oemr',
  'user/Patient.rs',
  'user/AllergyIntolerance.rs',
  'user/Condition.rs',
  'user/MedicationRequest.rs',
  'user/MedicationDispense.rs',
  'user/CareTeam.rs',
  'user/Encounter.rs',
  // OpenEMR only registers `user/DocumentReference.rs` in its restricted
  // form (clinical-note category only). The plain `.rs` is silently
  // dropped at registration. We request the restricted form explicitly
  // so the granted scope matches what was asked for.
  'user/DocumentReference.rs?category=http://hl7.org/fhir/us/core/CodeSystem/us-core-documentreference-category|clinical-note',
].join(' ')

export const oidcConfig: AuthProviderProps = {
  authority: `${base}/oauth2/default`,
  client_id: clientId,
  client_secret: clientSecret, // confidential client, see header note
  redirect_uri: redirectUri,
  post_logout_redirect_uri: postLogoutRedirectUri,
  scope: SCOPES,
  response_type: 'code',
  automaticSilentRenew: true,
  loadUserInfo: false, // OpenEMR's userinfo endpoint is optional; we read fhirUser claim from id_token
  userStore: new WebStorageStateStore({ store: window.sessionStorage }),
  // Strip ?code & ?state from the URL after callback so refresh doesn't replay it.
  onSigninCallback: () => {
    window.history.replaceState({}, document.title, window.location.pathname)
  },
}
