import { Navigate, Route, Routes } from 'react-router-dom'
import { useAuth } from 'react-oidc-context'
import { LoginPage } from './pages/LoginPage'
import { PatientSelectPage } from './pages/PatientSelectPage'
import { DashboardPage } from './pages/DashboardPage'
import { SectionStubPage } from './pages/SectionStubPage'
import { CallbackPage } from './auth/CallbackPage'
import { ProtectedRoute } from './auth/ProtectedRoute'

/**
 * Top-level router.
 *
 * Routes:
 *   /             → /patients if authed, else /login
 *   /login        → LoginPage (sign-in CTA)
 *   /callback     → CallbackPage (OAuth2 redirect target)
 *   /patients     → PatientSelectPage (Layer 1 of §6 patient-access defense)
 *   /dashboard/:patientId → DashboardPage
 */
export default function App() {
  const auth = useAuth()

  return (
    <Routes>
      <Route
        path="/"
        element={
          auth.isAuthenticated ? (
            <Navigate to="/patients" replace />
          ) : (
            <Navigate to="/login" replace />
          )
        }
      />
      <Route path="/login" element={<LoginPage />} />
      <Route path="/callback" element={<CallbackPage />} />
      <Route
        path="/patients"
        element={
          <ProtectedRoute>
            <PatientSelectPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/dashboard/:patientId"
        element={
          <ProtectedRoute>
            <DashboardPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/dashboard/:patientId/:section"
        element={
          <ProtectedRoute>
            <SectionStubPage />
          </ProtectedRoute>
        }
      />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
