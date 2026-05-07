import { useEffect } from 'react'
import { useAuth } from 'react-oidc-context'
import { useNavigate } from 'react-router-dom'
import { Spinner } from '../components/ui/Spinner'

/**
 * OAuth2 redirect target.
 *
 * `react-oidc-context` automatically detects `?code=` + `?state=` in the URL
 * and exchanges them for tokens via `oidc-client-ts`. This component is just
 * the visible UI during that exchange — once `auth.isAuthenticated` flips to
 * true, we navigate to the patient picker.
 *
 * On error (e.g. invalid state, expired code, network failure during the
 * token exchange) we send the user back to /login. The error itself is
 * already surfaced on `auth.error`.
 */
export function CallbackPage() {
  const auth = useAuth()
  const navigate = useNavigate()

  useEffect(() => {
    if (auth.isLoading) return
    if (auth.error) {
      navigate('/login', { replace: true })
      return
    }
    if (auth.isAuthenticated) {
      navigate('/patients', { replace: true })
    }
  }, [auth.isLoading, auth.isAuthenticated, auth.error, navigate])

  return (
    <div
      className="min-h-screen flex items-center justify-center bg-gray-50"
      role="status"
      aria-live="polite"
    >
      <Spinner />
      <span className="ml-3 text-sm text-gray-600">Completing sign-in…</span>
    </div>
  )
}
