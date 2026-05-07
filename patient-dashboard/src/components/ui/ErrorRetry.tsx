/**
 * Error display with a Retry button. Used by CardShell when a TanStack
 * Query fails (per §10 of PATIENT_DASHBOARD_MIGRATION.md).
 *
 * PHI safety: we render only the error class/status, never the response
 * body. FhirError preserves status + statusText; that's what we show.
 */

import { FhirError } from '../../api/fhirClient'

interface ErrorRetryProps {
  error: Error
  onRetry: () => void
}

export function ErrorRetry({ error, onRetry }: ErrorRetryProps) {
  const message =
    error instanceof FhirError
      ? `Failed to load (HTTP ${error.status} ${error.statusText})`
      : 'Failed to load'

  return (
    <div role="alert" className="text-sm">
      <p className="text-red-700 mb-2">{message}</p>
      <button
        type="button"
        onClick={onRetry}
        className="inline-flex items-center px-2.5 py-1 text-xs font-medium rounded border border-gray-300 bg-white text-gray-700 hover:bg-gray-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
      >
        Retry
      </button>
    </div>
  )
}
