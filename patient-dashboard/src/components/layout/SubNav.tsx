import { useEffect, useRef, useState } from 'react'
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
 *   - Documents (slug "documents") → real DocumentsPage.
 *   - Other items → in-app stub pages (`SectionStubPage`) that
 *     acknowledge the section is not ported and provide a link to
 *     open the legacy OpenEMR view in a new tab.
 *
 * Layout:
 *   - `≥md`: items wrap to multiple lines if needed; standard.
 *   - `<md`: horizontal-scroll strip with edge fade gradients to signal
 *     overflowed content (recommended Material/iOS pattern). The
 *     gradients are scroll-aware: left fade appears only when scrolled
 *     past the start; right fade only while more content is to the
 *     right. Nothing for the user to "click" — this is a discoverability
 *     cue, not a control. Swipe / drag remains the only navigation
 *     gesture.
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

const FADE_THRESHOLD_PX = 4

export function SubNav({ patientId }: { patientId: string }) {
  const scrollRef = useRef<HTMLUListElement>(null)
  const [showLeftFade, setShowLeftFade] = useState(false)
  const [showRightFade, setShowRightFade] = useState(false)

  useEffect(() => {
    const el = scrollRef.current
    if (!el) return

    const update = () => {
      const { scrollLeft, scrollWidth, clientWidth } = el
      // Show left fade once the user has scrolled past the start (with a
      // small dead-zone so the fade doesn't flicker at the edge).
      setShowLeftFade(scrollLeft > FADE_THRESHOLD_PX)
      // Show right fade while there's still content to scroll into view.
      setShowRightFade(scrollLeft < scrollWidth - clientWidth - FADE_THRESHOLD_PX)
    }

    update()
    el.addEventListener('scroll', update, { passive: true })
    // Re-check on resize / orientation change because the same scroll
    // position can flip from "more right" to "no more right" when the
    // viewport widens.
    const ro = new ResizeObserver(update)
    ro.observe(el)
    return () => {
      el.removeEventListener('scroll', update)
      ro.disconnect()
    }
  }, [])

  return (
    <nav
      className="bg-white border-b border-gray-200"
      aria-label="Patient record sections"
    >
      <div className="relative">
        <ul
          ref={scrollRef}
          className="
            flex md:flex-wrap gap-x-5 gap-y-1 text-sm py-2 m-0 px-4
            max-md:overflow-x-auto max-md:flex-nowrap max-md:whitespace-nowrap
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

        {/* Edge fade gradients — only on `<md` (where the scroll strip
            is). Pure visual cue; never intercept clicks. */}
        <div
          aria-hidden="true"
          className={`
            md:hidden pointer-events-none
            absolute left-0 top-0 bottom-0 w-8
            bg-gradient-to-r from-white to-transparent
            transition-opacity duration-150
            ${showLeftFade ? 'opacity-100' : 'opacity-0'}
          `}
        />
        <div
          aria-hidden="true"
          className={`
            md:hidden pointer-events-none
            absolute right-0 top-0 bottom-0 w-8
            bg-gradient-to-l from-white to-transparent
            transition-opacity duration-150
            ${showRightFade ? 'opacity-100' : 'opacity-0'}
          `}
        />
      </div>
    </nav>
  )
}
