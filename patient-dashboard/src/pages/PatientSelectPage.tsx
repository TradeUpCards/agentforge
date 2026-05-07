import { useAuth } from 'react-oidc-context'
import { useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { usePatientList } from '../hooks/usePatientList'
import { Spinner } from '../components/ui/Spinner'
import { ErrorRetry } from '../components/ui/ErrorRetry'
import { formatDate, capitalize } from '../utils/formatters'

/**
 * Patient picker. Layer 1 of the patient-access defense (§6 of
 * PATIENT_DASHBOARD_MIGRATION.md): the FHIR Patient search is
 * server-filtered to patients the authenticated user is authorized to
 * view, so the user can only navigate to IDs they were shown.
 *
 * Source: `GET /fhir/Patient` (Bundle of FhirPatient resources). The
 * Patient resource's `id` is the value `/fhir/Patient/{id}` expects.
 */
export function PatientSelectPage() {
  const auth = useAuth()
  const queryClient = useQueryClient()
  const { data: patients, isLoading, error, refetch } = usePatientList()

  const handleSignOut = async () => {
    queryClient.clear()
    await auth.signoutRedirect()
  }

  return (
    <div className="min-h-screen bg-gray-50">
      <header className="bg-white border-b border-gray-200 shadow-sm">
        <div className="max-w-4xl mx-auto px-4 py-3 flex items-center justify-between">
          <h1 className="text-base font-semibold text-gray-800 m-0">Select Patient</h1>
          <button
            type="button"
            onClick={handleSignOut}
            className="text-sm text-gray-600 hover:text-gray-900 hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 rounded"
          >
            Sign out
          </button>
        </div>
      </header>

      <main className="max-w-4xl mx-auto px-4 py-6">
        {isLoading ? (
          <div className="flex items-center gap-2" role="status" aria-live="polite">
            <Spinner label="Loading patients" />
            <span className="text-sm text-gray-600">Loading patients…</span>
          </div>
        ) : error ? (
          <ErrorRetry error={error} onRetry={() => void refetch()} />
        ) : !patients || patients.length === 0 ? (
          <p className="text-sm text-gray-500 italic">
            No patients available. You may not have access to any patients yet.
          </p>
        ) : (
          <ul className="bg-white rounded-lg border border-gray-200 divide-y divide-gray-100 shadow-sm">
            {patients.map((p) => (
              <li key={p.id}>
                <Link
                  to={`/dashboard/${p.id}`}
                  className="flex items-center justify-between gap-4 px-4 py-3 hover:bg-gray-50 focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-blue-600 transition-colors"
                >
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-gray-900 truncate">
                      {p.displayName}
                    </p>
                    <p className="text-xs text-gray-500">
                      DOB: {formatDate(p.dob)} · Sex: {capitalize(p.sex)}
                    </p>
                  </div>
                  <span className="text-xs font-mono text-gray-500 shrink-0">
                    MRN: {p.mrn || '—'}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </main>
    </div>
  )
}
