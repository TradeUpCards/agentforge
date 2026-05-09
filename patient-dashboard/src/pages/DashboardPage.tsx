import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { MainNav } from '../components/layout/MainNav'
import { OpenTabBar } from '../components/layout/OpenTabBar'
import { PatientHeader } from '../components/layout/PatientHeader'
import { MedicalRecordHeading } from '../components/layout/MedicalRecordHeading'
import { SubNav } from '../components/layout/SubNav'
import { DashboardGrid } from '../components/layout/DashboardGrid'
import { AllergiesCard } from '../components/cards/AllergiesCard'
import { ProblemsCard } from '../components/cards/ProblemsCard'
import { MedicationsCard } from '../components/cards/MedicationsCard'
import { PrescriptionsCard } from '../components/cards/PrescriptionsCard'
import { CareTeamCard } from '../components/cards/CareTeamCard'
import { EncountersCard } from '../components/cards/EncountersCard'
import { DocumentsCard } from '../components/cards/DocumentsCard'
import { CoPilotDrawer } from '../components/copilot/CoPilotDrawer'

/**
 * Composes the full patient dashboard with the OpenEMR-familiar layout:
 *   1. Identity strip (avatar, name in blue, DOB/Age/Sex/MRN/Active)
 *   2. "Medical Record Dashboard - {Name}" heading band
 *   3. Sub-nav (Dashboard / History / Assessments / … / External Data)
 *   4. Card grid
 *
 * Card order matches the legacy PHP dashboard's layout (§9 parity table):
 *   Allergies → Medical Problems → Medications → Prescriptions → Care Team
 * Encounter History appended as the additional section (§7).
 *
 * Each card mounts independently; failure of one does not block the
 * others (§10).
 */
export function DashboardPage() {
  const { patientId } = useParams<{ patientId: string }>()
  // Desktop-only thin-bar collapse, toggled by OpenTabBar's chevron.
  const [headerCollapsed, setHeaderCollapsed] = useState(false)
  // Drawer-style hide-entirely toggle (all breakpoints), via the
  // chevron handle bottom-center of the patient header.
  const [headerHidden, setHeaderHidden] = useState(false)

  if (!patientId) {
    return (
      <div className="p-6">
        <p className="text-red-700" role="alert">
          No patient selected.
        </p>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-white">
      {/* Sticky chrome — MainNav + PatientHeader move together so the
          menu stays accessible when the user scrolls cards. */}
      <div className="sticky top-0 z-30 bg-white">
        <MainNav />
        <PatientHeader
          patientId={patientId}
          collapsed={headerCollapsed}
          hidden={headerHidden}
          onToggleHidden={() => setHeaderHidden((s) => !s)}
        />
      </div>
      <OpenTabBar
        active="Dashboard"
        headerCollapsed={headerCollapsed}
        onToggleHeader={() => setHeaderCollapsed((s) => !s)}
      />
      <MedicalRecordHeading patientId={patientId} />
      <SubNav patientId={patientId} />
      <DashboardGrid>
        <AllergiesCard patientId={patientId} />
        <ProblemsCard patientId={patientId} />
        <MedicationsCard patientId={patientId} />
        <PrescriptionsCard patientId={patientId} />
        <CareTeamCard patientId={patientId} />
        <EncountersCard patientId={patientId} />
        <DocumentsCard patientId={patientId} />
      </DashboardGrid>
      <CoPilotDrawer patientId={patientId} />
    </div>
  )
}
