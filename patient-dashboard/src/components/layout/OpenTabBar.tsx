/**
 * OpenEMR's open-tabs strip — the row that reads
 * "Calendar / Message Center / Patient Finder / Dashboard / Visit History"
 * with each tab carrying refresh / lock / close glyphs in the legacy.
 *
 * In OpenEMR's PHP shell this strip represents the user's currently
 * open patient/feature tabs (multi-tab session). Our React app does
 * not implement multi-tab; the strip is here for visual familiarity.
 *
 * Responsive behavior (per the matrix in PATIENT_DASHBOARD_MIGRATION.md
 * §17): hidden on `<md` (any phone orientation). Justification:
 *   - The strip represents OpenEMR's multi-tab session shell which has
 *     no functional analog on phone widths — clicking a tab opens the
 *     legacy app in a new browser tab regardless.
 *   - Visual familiarity isn't load-bearing here; hiding reclaims ~40
 *     px of scarce vertical space on small screens.
 *   - Power-users who want OpenEMR's multi-tab shell can launch via
 *     the MainNav hamburger -> OpenEMR home, same as the chevron arrow.
 *
 * Wiring (≥md only):
 *   - "Dashboard" tab is rendered as the active tab, no link (we are
 *     already there).
 *   - The other tabs link to OpenEMR's main_screen.php in a new tab —
 *     the legacy app handles tab restoration from there.
 *   - The per-tab refresh / lock / close icons are decorative.
 */

import { openemrMenuLink } from '../../utils/openemrLinks'

const TABS = [
  'Calendar',
  'Message Center',
  'Patient Finder',
  'Dashboard',
  'Visit History',
] as const

type TabName = (typeof TABS)[number]

interface OpenTabBarProps {
  active?: TabName
  /**
   * Whether the patient header is currently collapsed. Used to set the
   * leading chevron's rotation state and the button's aria-expanded.
   */
  headerCollapsed?: boolean
  /**
   * Callback fired when the leading chevron is clicked. The legacy
   * OpenEMR widget uses this slot to toggle the patient header's
   * collapse state — we implement the same behavior at lg+ widths
   * (where the desktop PatientHeader renders).
   */
  onToggleHeader?: () => void
}

export function OpenTabBar({
  active = 'Dashboard',
  headerCollapsed = false,
  onToggleHeader,
}: OpenTabBarProps) {
  return (
    <div
      className="hidden md:flex bg-white border-b border-gray-200 px-4 py-1 items-end gap-3"
      aria-label="Open tabs"
    >
      {/*
       * Leading chevron — collapses/expands the desktop patient header.
       * Hidden on md-to-lg widths because the mobile PatientHeader
       * variants render there and have their own toggle. Becomes a real
       * button only when an onToggleHeader callback is provided; otherwise
       * decorative-only for visual familiarity with the legacy widget.
       */}
      {onToggleHeader ? (
        <button
          type="button"
          onClick={onToggleHeader}
          aria-expanded={!headerCollapsed}
          aria-label={headerCollapsed ? 'Expand patient header' : 'Collapse patient header'}
          className="
            hidden lg:inline-flex items-center justify-center
            text-blue-700 hover:text-blue-900
            p-1 rounded
            focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600
          "
        >
          <ToggleChevron collapsed={headerCollapsed} />
        </button>
      ) : (
        <span className="hidden lg:inline-flex text-blue-700 text-base leading-none mr-1" aria-hidden="true">
          ▲
        </span>
      )}
      <ul className="flex items-end gap-3 m-0 flex-wrap">
        {TABS.map((name) => {
          const isActive = name === active
          const labelClass = isActive
            ? 'text-gray-900 font-semibold border-b-2 border-blue-700 pb-1'
            : 'text-blue-700 hover:underline pb-1'
          return (
            <li key={name} className="flex items-center gap-1.5 text-sm">
              {isActive ? (
                <span className={labelClass}>{name}</span>
              ) : (
                <a
                  href={openemrMenuLink(name)}
                  target="_blank"
                  rel="noopener noreferrer"
                  className={labelClass}
                >
                  {name}
                </a>
              )}
              <TabIcons />
            </li>
          )
        })}
      </ul>
    </div>
  )
}

/**
 * Modern chevron used by the patient-header collapse toggle. Points up
 * when the header is expanded (default) and rotates 180° down when the
 * header is collapsed. Animated transition for smooth state change.
 */
function ToggleChevron({ collapsed }: { collapsed: boolean }) {
  return (
    <svg
      viewBox="0 0 16 16"
      width="14"
      height="14"
      fill="currentColor"
      aria-hidden="true"
      className={`transition-transform ${collapsed ? '' : 'rotate-180'}`}
    >
      <path d="M1.646 4.646a.5.5 0 0 1 .708 0L8 10.293l5.646-5.647a.5.5 0 0 1 .708.708l-6 6a.5.5 0 0 1-.708 0l-6-6a.5.5 0 0 1 0-.708" />
    </svg>
  )
}

/** Trio of refresh / lock / close glyphs that legacy OpenEMR puts on each tab. Decorative. */
function TabIcons() {
  return (
    <span className="flex items-center gap-1 text-gray-400" aria-hidden="true">
      <svg viewBox="0 0 16 16" width="11" height="11" fill="currentColor">
        <path d="M8 3a5 5 0 1 1-4.546 2.914.5.5 0 0 0-.908-.417A6 6 0 1 0 8 2z" />
        <path d="M8 4.466V.534a.25.25 0 0 0-.41-.192L5.23 2.308a.25.25 0 0 0 0 .384l2.36 1.966A.25.25 0 0 0 8 4.466" />
      </svg>
      <svg viewBox="0 0 16 16" width="11" height="11" fill="currentColor">
        <path d="M8 1a2 2 0 0 1 2 2v4H6V3a2 2 0 0 1 2-2m3 6V3a3 3 0 0 0-6 0v4a2 2 0 0 0-2 2v5a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2" />
      </svg>
      <svg viewBox="0 0 16 16" width="11" height="11" fill="currentColor">
        <path d="M2.146 2.854a.5.5 0 1 1 .708-.708L8 7.293l5.146-5.147a.5.5 0 0 1 .708.708L8.707 8l5.147 5.146a.5.5 0 0 1-.708.708L8 8.707l-5.146 5.147a.5.5 0 0 1-.708-.708L7.293 8z" />
      </svg>
    </span>
  )
}
