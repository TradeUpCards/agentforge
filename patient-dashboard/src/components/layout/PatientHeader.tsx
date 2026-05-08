import { usePatient } from '../../hooks/usePatient'
import {
  getPatientActiveStatus,
  getPatientDisplayName,
  getPatientMrn,
} from '../../utils/fhirParsers'
import { capitalize, formatDate } from '../../utils/formatters'
import { Spinner } from '../ui/Spinner'
import { EncounterSelector } from './EncounterSelector'

interface PatientHeaderProps {
  patientId: string
  /**
   * Desktop-only — when true, the lg+ variant renders a thin compact bar
   * (just name + Active + MRN) instead of the full inline layout. Toggled
   * by the chevron in OpenTabBar. Mobile variants ignore this prop;
   * they have their own size logic by orientation.
   */
  collapsed?: boolean
  /**
   * Drawer-style toggle (all breakpoints). When true, the entire
   * header content is hidden and only the bottom-center chevron handle
   * remains, sticky at the top. Click the handle to re-show. Default
   * false (header visible).
   */
  hidden?: boolean
  /** Fired when the drawer-toggle chevron handle is clicked. */
  onToggleHidden?: () => void
}

/**
 * Patient identity strip — mirrors the OpenEMR row directly below the
 * open-tabs bar.
 *
 * Three layout variants by viewport (per the matrix in
 * PATIENT_DASHBOARD_MIGRATION.md §17):
 *
 *   ≥lg (tablet landscape / desktop)
 *     Full one-line layout: avatar | name | DOB · Age · Sex · MRN · Active.
 *     Not sticky — wide screens have plenty of vertical room and the
 *     cards scroll under the OpenTabBar / heading bar separately.
 *
 *   <lg portrait (phone portrait, tablet portrait)
 *     Full layout but stacked vertically (name on first row, DOB/Age/Sex/MRN
 *     wrapped, Active pill at end). Sticky to the top of the scroll area
 *     so the clinician always knows which patient they are looking at while
 *     scrolling cards. Clinical safety: prevents "wrong patient" cognitive
 *     errors during a long med list.
 *
 *   <lg landscape (phone landscape — including iPhone 14+ at ~844-932 px)
 *     COMPACT sticky variant: name + Active badge only, in a thin ~40 px
 *     bar. Tap to expand inline (reveals DOB/Age/Sex/MRN below). Defends
 *     the clinical-safety property at half the vertical cost — landscape
 *     phones have ~390 px of usable height before the keyboard, so every
 *     pixel matters.
 *
 * Breakpoint choice (lg, not md): modern phones in landscape exceed the
 * md threshold (768 px) — iPhone 14 lands at 844, Pro Max at 932 — so
 * gating the mobile variants at md would render the desktop layout on
 * those devices. Aligned with MainNav's lg-breakpoint hamburger.
 *
 * The PDF requirement #2 demands name / DOB / sex / MRN / active status —
 * all five remain available in every variant. Compact landscape hides
 * DOB/Sex/MRN behind a tap, but they remain reachable without leaving
 * the page.
 */
export function PatientHeader({
  patientId,
  collapsed = false,
  hidden = false,
  onToggleHidden,
}: PatientHeaderProps) {
  const { data: patient, isLoading, error } = usePatient(patientId)
  const age = patient?.birthDate ? calculateAge(patient.birthDate) : undefined

  if (isLoading) {
    return (
      <header
        className="bg-white border-b border-gray-200 px-4 py-3 flex items-center gap-2 max-lg:sticky max-lg:top-0 z-20"
        aria-busy="true"
      >
        <Spinner label="Loading patient" />
        <span className="text-sm text-gray-600">Loading patient…</span>
      </header>
    )
  }

  if (error || !patient) {
    return (
      <header
        className="bg-white border-b border-gray-200 px-4 py-3 max-lg:sticky max-lg:top-0 z-20"
        role="alert"
      >
        <p className="text-sm text-red-700">Failed to load patient.</p>
      </header>
    )
  }

  const name = getPatientDisplayName(patient)
  const mrn = getPatientMrn(patient)
  const active = getPatientActiveStatus(patient)
  const idShort = patient.id?.slice(0, 8) ?? '—'

  return (
    <div className="sticky top-0 z-20 bg-white">
      <div className="relative">
      {!hidden && (
        <>
          {/* ≥lg, collapsed: thin bar (toggled by OpenTabBar's chevron) */}
          {collapsed && (
            <header className="hidden lg:block bg-white border-b border-gray-200 px-4 py-1.5">
              <div className="flex items-center gap-3">
                <Avatar small />
                <h1 className="text-base font-bold text-blue-700 m-0 truncate">{name}</h1>
                <ActiveBadge active={active} />
                <span className="text-xs text-gray-600 ml-2">
                  <span className="font-semibold">MRN:</span>{' '}
                  <span className="font-mono">{mrn}</span>
                </span>
                <span className="ml-auto">
                  <EncounterSelector patientId={patient.id ?? ''} />
                </span>
              </div>
            </header>
          )}

          {/* ≥lg, expanded: full inline layout (default) */}
          {!collapsed && (
            <header className="hidden lg:block bg-white border-b border-gray-200 px-4 py-3">
              <div className="flex items-start justify-between gap-4 flex-wrap">
                <div className="flex items-start gap-3">
                  <Avatar />
                  <div>
                    <h1 className="text-xl font-bold text-blue-700 m-0 leading-tight flex items-baseline gap-1.5">
                      <span>{name}</span>
                      <span className="text-gray-500 text-sm font-normal">({idShort})</span>
                      <span className="text-gray-400 text-sm font-normal">×</span>
                    </h1>
                    <p className="text-sm text-gray-700 mt-1">
                      <span className="font-semibold">DOB:</span> {formatDate(patient.birthDate)}
                      {age !== undefined && (
                        <>
                          {' '}
                          <span className="font-semibold">Age:</span> {age}
                        </>
                      )}
                      {' · '}
                      <span className="font-semibold">Sex:</span> {capitalize(patient.gender)}
                      {' · '}
                      <span className="font-semibold">MRN:</span>{' '}
                      <span className="font-mono">{mrn}</span>
                      {' · '}
                      <ActiveBadge active={active} />
                    </p>
                  </div>
                </div>
                <div className="mt-1">
                  <EncounterSelector patientId={patient.id ?? ''} />
                </div>
              </div>
            </header>
          )}

          {/* <lg portrait: dense 2-line header (phone portrait, tablet
              portrait). Static — vertical room is plentiful in portrait,
              so no per-variant collapse. Drawer toggle below covers the
              "I want all my screen" case.
                Line 1: avatar + name + Active badge
                Line 2: DOB · Age · Sex · MRN */}
          <header className="hidden max-lg:portrait:block bg-white border-b border-gray-200 px-4 py-2">
            <div className="flex items-center gap-3">
              <Avatar small />
              <h1 className="text-base font-bold text-blue-700 m-0 leading-tight truncate flex-1 min-w-0">
                {name}
              </h1>
              <ActiveBadge active={active} />
            </div>
            <p className="text-xs text-gray-700 mt-1 leading-snug m-0 flex flex-wrap gap-x-2">
              <span>
                <span className="font-semibold">DOB:</span> {formatDate(patient.birthDate)}
              </span>
              {age !== undefined && (
                <span>
                  <span className="font-semibold">Age:</span> {age}
                </span>
              )}
              <span>
                <span className="font-semibold">Sex:</span> {capitalize(patient.gender)}
              </span>
              <span>
                <span className="font-semibold">MRN:</span>{' '}
                <span className="font-mono">{mrn}</span>
              </span>
            </p>
          </header>

          {/* <lg landscape: single-line dense bar.
              Vertical room is precious (~390 px before keyboard); the
              whole identity packs onto one line. Phone landscape
              including iPhone 14+ at 844-932 px. */}
          <header className="hidden max-lg:landscape:block bg-white border-b border-gray-200 px-4 py-1.5">
            <div className="flex items-center gap-2 text-xs">
              <span className="font-bold text-blue-700 truncate min-w-0 flex-shrink">
                {name}
              </span>
              <ActiveBadge active={active} />
              <span className="text-gray-700 ml-1 whitespace-nowrap">
                <span className="font-semibold">DOB:</span> {formatDate(patient.birthDate)}
                {age !== undefined && (
                  <>
                    {' '}
                    · <span className="font-semibold">Age:</span> {age}
              </>
            )}
            {' · '}
            <span className="font-semibold">Sex:</span> {capitalize(patient.gender)}
            {' · '}
            <span className="font-semibold">MRN:</span>{' '}
            <span className="font-mono">{mrn}</span>
          </span>
        </div>
      </header>
        </>
      )}

      {/* Drawer-toggle handle — absolutely positioned chevron tab,
          anchored top-center, half-overlapping the bottom edge of the
          MainNav row above. Takes NO layout space.
          Position is identical in both states; only the chevron
          orientation flips:
            Expanded → chevron up    (click to collapse upward)
            Hidden   → chevron down  (click to expand downward) */}
      {onToggleHidden && (
        <button
          type="button"
          onClick={onToggleHidden}
          aria-expanded={!hidden}
          aria-label={hidden ? 'Show patient header' : 'Hide patient header'}
          className="
            absolute left-1/2 -translate-x-1/2 -top-2.5 z-10
            inline-flex items-center justify-center
            px-3 h-5
            bg-white border border-gray-200
            rounded-md shadow-sm
            text-blue-700 hover:bg-gray-50
            focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600
          "
        >
          <DrawerChevron showing={!hidden} />
        </button>
      )}
      </div>
    </div>
  )
}

function DrawerChevron({ showing }: { showing: boolean }) {
  // Points UP when the drawer is showing (clicking will hide it upward),
  // points DOWN when the drawer is hidden (clicking will reveal it).
  return (
    <svg
      viewBox="0 0 16 16"
      width="14"
      height="14"
      fill="currentColor"
      aria-hidden="true"
      className={`transition-transform ${showing ? 'rotate-180' : ''}`}
    >
      <path d="M1.646 4.646a.5.5 0 0 1 .708 0L8 10.293l5.646-5.647a.5.5 0 0 1 .708.708l-6 6a.5.5 0 0 1-.708 0l-6-6a.5.5 0 0 1 0-.708" />
    </svg>
  )
}

function Avatar({ small = false }: { small?: boolean }) {
  const size = small ? 'w-9 h-9' : 'w-12 h-12'
  const iconSize = small ? 18 : 22
  return (
    <div
      className={`${size} rounded-full bg-gray-200 border border-gray-300 flex items-center justify-center text-gray-500 shrink-0`}
      aria-hidden="true"
    >
      <svg viewBox="0 0 24 24" width={iconSize} height={iconSize} fill="currentColor">
        <path d="M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8m0 2c-3.5 0-8 1.75-8 5v3h16v-3c0-3.25-4.5-5-8-5" />
      </svg>
    </div>
  )
}

function ActiveBadge({ active }: { active: boolean }) {
  return (
    <span
      className={`inline-block px-1.5 py-0.5 rounded text-xs font-medium align-middle ${
        active ? 'bg-green-100 text-green-800' : 'bg-gray-200 text-gray-700'
      }`}
    >
      {active ? 'Active' : 'Inactive'}
    </span>
  )
}

function calculateAge(isoDate: string): number | undefined {
  const dob = new Date(isoDate)
  if (Number.isNaN(dob.getTime())) return undefined
  const now = new Date()
  let age = now.getFullYear() - dob.getFullYear()
  const m = now.getMonth() - dob.getMonth()
  if (m < 0 || (m === 0 && now.getDate() < dob.getDate())) age--
  return age
}
