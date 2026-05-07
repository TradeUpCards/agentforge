/**
 * OpenEMR's open-tabs strip — the row that reads
 * "Calendar / Message Center / Patient Finder / Dashboard / Visit History"
 * with each tab carrying refresh / lock / close glyphs in the legacy.
 *
 * In OpenEMR's PHP shell this strip represents the user's currently
 * open patient/feature tabs (multi-tab session). Our React app does
 * not implement multi-tab; the strip is here for visual familiarity.
 *
 * Wiring:
 *   - "Dashboard" tab is rendered as the active tab, no link (we are
 *     already there).
 *   - The other tabs link to OpenEMR's main_screen.php in a new tab —
 *     the legacy app handles tab restoration from there.
 *   - The per-tab refresh / lock / close icons are decorative.
 */

const OPENEMR_BASE =
  import.meta.env.VITE_OPENEMR_BASE_URL ?? 'https://localhost:9300'

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
}

export function OpenTabBar({ active = 'Dashboard' }: OpenTabBarProps) {
  return (
    <div
      className="bg-white border-b border-gray-200 px-4 py-1 flex items-end gap-3"
      aria-label="Open tabs"
    >
      <span className="text-blue-700 text-base leading-none mr-1" aria-hidden="true">
        ▲
      </span>
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
                  href={`${OPENEMR_BASE}/interface/main/main_screen.php`}
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
