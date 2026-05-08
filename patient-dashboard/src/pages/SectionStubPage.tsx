import { useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { MainNav } from '../components/layout/MainNav'
import { OpenTabBar } from '../components/layout/OpenTabBar'
import { PatientHeader } from '../components/layout/PatientHeader'
import { MedicalRecordHeading } from '../components/layout/MedicalRecordHeading'
import { SubNav } from '../components/layout/SubNav'
import { CoPilotDrawer } from '../components/copilot/CoPilotDrawer'
import { openemrHome } from '../utils/openemrLinks'

/**
 * Placeholder page for the non-Dashboard sub-nav items
 * (History / Assessments / Report / Documents / Transactions / Issues /
 * Ledger / External Data).
 *
 * These sections exist in the legacy OpenEMR PHP dashboard but are out
 * of scope for the W2 surprise-challenge port. Rather than break the
 * sub-nav links or hide the items, we render a stub that:
 *   1. Names the section.
 *   2. Acknowledges it is not ported.
 *   3. Offers a "View in OpenEMR" out-link to the legacy app at the
 *      same OpenEMR origin (the user is already authenticated there
 *      via the same browser session).
 *
 * This is the honest answer the parity discipline (§9) calls for:
 * mirror the legacy sub-nav visually, without claiming functionality
 * we do not implement.
 */
const LABELS: Record<string, string> = {
  history: 'History',
  assessments: 'Assessments',
  report: 'Report',
  documents: 'Documents',
  transactions: 'Transactions',
  issues: 'Issues',
  ledger: 'Ledger',
  'external-data': 'External Data',
}

export function SectionStubPage() {
  const { patientId, section } = useParams<{ patientId: string; section: string }>()
  const label = (section && LABELS[section]) ?? 'Section'
  const [headerCollapsed, setHeaderCollapsed] = useState(false)
  const [headerHidden, setHeaderHidden] = useState(false)

  if (!patientId) {
    return (
      <p className="p-4 text-red-700" role="alert">
        No patient selected.
      </p>
    )
  }

  return (
    <div className="min-h-screen bg-white">
      <MainNav />
      <PatientHeader
        patientId={patientId}
        collapsed={headerCollapsed}
        hidden={headerHidden}
        onToggleHidden={() => setHeaderHidden((s) => !s)}
      />
      <OpenTabBar
        active="Dashboard"
        headerCollapsed={headerCollapsed}
        onToggleHeader={() => setHeaderCollapsed((s) => !s)}
      />
      <MedicalRecordHeading patientId={patientId} />
      <SubNav patientId={patientId} />
      <main className="px-4 py-8 max-w-3xl mx-auto">
        <h2 className="text-lg font-semibold text-gray-800 m-0">
          {label}
        </h2>
        <p className="text-sm text-gray-700 mt-3">
          This section exists in the legacy OpenEMR dashboard and is not yet
          part of the modernized React port. The patient dashboard
          surface (Allergies, Medical Problems, Medications, Prescriptions,
          Care Team, Encounter History) is the W2 deliverable.
        </p>
        <div className="mt-5 flex items-center gap-3">
          <a
            href={openemrHome()}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center px-3 py-1.5 text-sm font-medium rounded border border-blue-700 bg-white text-blue-700 hover:bg-blue-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
          >
            Open in OpenEMR ↗
          </a>
          <Link
            to={`/dashboard/${patientId}`}
            className="text-sm text-blue-700 hover:underline"
          >
            ← Back to dashboard
          </Link>
        </div>
      </main>
      <CoPilotDrawer patientId={patientId} />
    </div>
  )
}
