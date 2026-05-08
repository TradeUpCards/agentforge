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
 * Three layout variants picked by CONTAINER WIDTH (not viewport width).
 * This matters when the Co-Pilot drawer pushes the chart and shrinks
 * the patient-header's actual width even though the viewport hasn't
 * changed — viewport queries would still render the wide variant in a
 * narrow space, truncating the name with an ellipsis. Container queries
 * pick the right variant for the actual width:
 *
 *   < @xl   (< 576 px)        : stacked 2-line variant
 *     Avatar + name + Active badge on row 1; DOB · Age · Sex · MRN
 *     wrapped on row 2. Used for phone portrait, phone landscape with
 *     Co-Pilot open, and tablet portrait with Co-Pilot open.
 *
 *   @xl    (576 - 1023 px)    : dense single-line variant
 *     Whole identity packs onto one line. Used for phone landscape at
 *     full width and tablet landscape with Co-Pilot open.
 *
 *   @5xl+  (≥ 1024 px)        : desktop full inline variant
 *     name + (idShort) + × + DOB · Age · Sex · MRN · Active +
 *     EncounterSelector on the right. Used for tablet landscape and
 *     desktop at full width.
 *
 * The PDF requirement #2 demands name / DOB / sex / MRN / active status
 * — all five appear in every variant.
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
    <div className="sticky top-0 z-20 bg-white @container">
      <div className="relative">
      {!hidden && (
        <>
          {/* @5xl+, collapsed: thin bar (toggled by OpenTabBar's chevron) */}
          {collapsed && (
            <header className="hidden @5xl:block bg-white border-b border-gray-200 px-4 py-1.5">
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

          {/* @5xl+, expanded: full inline layout (default) */}
          {!collapsed && (
            <header className="hidden @5xl:block bg-white border-b border-gray-200 px-4 py-3">
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

          {/* < @xl: dense 2-line stacked variant.
              Used for phone portrait, AND for any larger viewport when
              the Co-Pilot drawer squeezes the chart container below
              576 px (e.g., phone landscape with Co-Pilot open).
                Line 1: avatar + name + Active badge
                Line 2: DOB · Age · Sex · MRN (flex-wraps if very narrow) */}
          <header className="@xl:hidden bg-white border-b border-gray-200 px-4 py-2">
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

          {/* @xl - @5xl: single-line dense bar.
              Used for phone landscape at full width AND tablet landscape
              with Co-Pilot squeezing the chart between 576-1024 px. */}
          <header className="hidden @xl:block @5xl:hidden bg-white border-b border-gray-200 px-4 py-1.5">
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
