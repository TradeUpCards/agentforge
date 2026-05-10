import React from 'react'
import ReactDOM from 'react-dom/client'
import { AuthProvider } from 'react-oidc-context'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import './index.css'
import { oidcConfig } from './auth/oidcConfig'

/**
 * App entry point. Provider order matters:
 *   AuthProvider     — token / user state, must be outermost so router
 *                      and query layers can read auth context.
 *   QueryClient      — TanStack Query cache; cleared on logout from
 *                      PatientHeader / PatientSelectPage.
 *   BrowserRouter    — innermost so route changes can react to auth.
 *
 * `basename` is fed from `import.meta.env.BASE_URL`, which Vite injects
 * based on the `base` config (`/` in dev, `/patient-dashboard/` in
 * production builds). Without this, the React routes would not match
 * after the prefix is stripped by Apache. See PATIENT_DASHBOARD_MIGRATION.md
 * §13 for the deploy shape this supports.
 */

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // FHIR resources don't change every second. 1 minute is a reasonable
      // staleness window for an interactive dashboard.
      staleTime: 60_000,
      // Per §10: one automatic retry, then surface the error to the user.
      retry: 1,
      retryDelay: (attemptIndex) => Math.min(1000 * 2 ** attemptIndex, 8000),
    },
  },
})

const rootEl = document.getElementById('root')
if (!rootEl) {
  throw new Error('Root element #root not found in index.html')
}

ReactDOM.createRoot(rootEl).render(
  <React.StrictMode>
    <AuthProvider {...oidcConfig}>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter basename={import.meta.env.BASE_URL}>
          <App />
        </BrowserRouter>
      </QueryClientProvider>
    </AuthProvider>
  </React.StrictMode>,
)
