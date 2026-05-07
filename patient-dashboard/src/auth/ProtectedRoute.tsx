import { useAuth } from 'react-oidc-context'
import { Navigate, useLocation } from 'react-router-dom'
import { Spinner } from '../components/ui/Spinner'

/**
 * Frontend route guard. Layer 2 of the patient-access defense (see §6 of
 * PATIENT_DASHBOARD_MIGRATION.md):
 *   - Layer 1: only show patients the user is authorized for (PatientSelectPage)
 *   - Layer 2: this — block unauthenticated rendering of patient routes
 *   - Layer 3: server-side scope enforcement on every FHIR request (authoritative)
 *
 * If silent renew fails (refresh token expired/revoked/network), the auth
 * context flips to unauthenticated and we redirect to /login, preserving
 * the attempted URL in router state so the user lands back on the same
 * patient after re-auth.
 */
export function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const auth = useAuth()
  const location = useLocation()

  if (auth.isLoading) {
    return (
      <div
        className="min-h-screen flex items-center justify-center bg-gray-50"
        role="status"
        aria-live="polite"
      >
        <Spinner />
        <span className="ml-3 text-sm text-gray-600">Checking authentication…</span>
      </div>
    )
  }

  if (!auth.isAuthenticated) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />
  }

  return <>{children}</>
}
