import { NavLink } from 'react-router-dom'

interface SubNavItem {
  label: string
  /** Slug used in the in-app route `/patient/{id}/{slug}`. */
  slug: string
}

/**
 * Section sub-nav strip — mirrors the OpenEMR dashboard's
 * "Dashboard / History / Assessments / Report / Documents /
 * Transactions / Issues / Ledger / External Data" row.
 *
 * Routing model:
 *   - Dashboard (slug "") → the implemented React dashboard.
 *   - Other items → in-app stub pages (`SectionStubPage`) that
 *     acknowledge the section is not ported and provide a link to
 *     open the legacy OpenEMR view in a new tab.
 *
 * This keeps the visual familiarity of the legacy sub-nav without
 * misleading anyone about scope: every link goes somewhere; only
 * Dashboard renders patient data.
 */
const ITEMS: SubNavItem[] = [
  { label: 'Dashboard', slug: '' },
  { label: 'History', slug: 'history' },
  { label: 'Assessments', slug: 'assessments' },
  { label: 'Report', slug: 'report' },
  { label: 'Documents', slug: 'documents' },
  { label: 'Transactions', slug: 'transactions' },
  { label: 'Issues', slug: 'issues' },
  { label: 'Ledger', slug: 'ledger' },
  { label: 'External Data', slug: 'external-data' },
]

export function SubNav({ patientId }: { patientId: string }) {
  return (
    <nav
      className="bg-white border-b border-gray-200 px-4"
      aria-label="Patient record sections"
    >
      {/* On <md: horizontal scroll strip with no wrap, 44 px touch
          targets, momentum scrolling. On >=md: original wrap layout. */}
      <ul
        className="
          flex md:flex-wrap gap-x-5 gap-y-1 text-sm py-2 m-0
          max-md:overflow-x-auto max-md:flex-nowrap max-md:whitespace-nowrap
          max-md:-mx-4 max-md:px-4
        "
      >
        {ITEMS.map((item) => {
          const to = item.slug
            ? `/dashboard/${patientId}/${item.slug}`
            : `/dashboard/${patientId}`
          return (
            <li key={item.slug || 'dashboard'} className="shrink-0">
              <NavLink
                to={to}
                end={item.slug === ''}
                className={({ isActive }) =>
                  `inline-flex items-center min-h-11 md:min-h-0 ${
                    isActive
                      ? 'text-gray-900 font-semibold border-b-2 border-blue-700 -mb-px pb-px'
                      : 'text-blue-700 hover:underline border-b-2 border-transparent -mb-px pb-px'
                  }`
                }
              >
                {item.label}
              </NavLink>
            </li>
          )
        })}
      </ul>
    </nav>
  )
}
