import { useEffect } from 'react'
import { useAuth } from 'react-oidc-context'
import { useNavigate } from 'react-router-dom'
import { Spinner } from '../components/ui/Spinner'

/**
 * Unauthenticated landing page. The "Sign in with OpenEMR" button kicks
 * off the Authorization Code + PKCE flow.
 *
 * `signinRedirect()` sends the browser to OpenEMR's /authorize endpoint
 * with a generated code_challenge. After the user logs in there, OpenEMR
 * redirects to /callback (handled by CallbackPage).
 */
export function LoginPage() {
  const auth = useAuth()
  const navigate = useNavigate()

  useEffect(() => {
    if (auth.isAuthenticated) navigate('/patients', { replace: true })
  }, [auth.isAuthenticated, navigate])

  return (
    <div className="min-h-screen bg-gray-50 flex flex-col items-center justify-center p-4">
      <main className="bg-white rounded-xl shadow-sm border border-gray-200 p-10 max-w-sm w-full text-center space-y-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 m-0">OpenEMR</h1>
          <p className="text-sm text-gray-500 mt-1">Patient Dashboard</p>
        </div>
        {auth.isLoading ? (
          <div className="flex justify-center" role="status" aria-live="polite">
            <Spinner label="Checking session" />
          </div>
        ) : auth.error ? (
          <p className="text-sm text-red-700" role="alert">
            Sign-in failed. Please try again.
          </p>
        ) : (
          <button
            type="button"
            onClick={() => auth.signinRedirect()}
            className="w-full bg-blue-600 hover:bg-blue-700 text-white font-medium py-2.5 px-4 rounded-lg transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
          >
            Sign in with OpenEMR
          </button>
        )}
      </main>
    </div>
  )
}
