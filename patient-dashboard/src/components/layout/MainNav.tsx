import { useAuth } from 'react-oidc-context'
import { useQueryClient } from '@tanstack/react-query'
import { useState, useRef, useEffect } from 'react'

/**
 * Top OpenEMR application menu — the row that reads
 * "Calendar Finder Flow Recalls Messages Patient Fees Modules
 *  Procedures Admin Reports Miscellaneous Popups" with the
 * "Search by any demographic" input on the right.
 *
 * Visual familiarity for OpenEMR users. Each item opens the legacy
 * PHP application in a new tab — `main_screen.php` is the OpenEMR
 * shell entry that lands the user in their existing app context.
 *
 * Responsive behavior (per the responsive matrix in
 * PATIENT_DASHBOARD_MIGRATION.md §17):
 *   - `≥md` (tablet / desktop)  : full horizontal nav with all 13
 *                                 items + search + user avatar
 *   - `<md` (phone any orient.) : OpenEMR logo + hamburger button +
 *                                 search-icon + avatar; hamburger
 *                                 opens a drawer-like dropdown
 *                                 listing all 13 items at 44 px touch
 *                                 targets
 *
 * The hamburger pattern is the standard responsive answer; legacy
 * OpenEMR was never designed for phone widths, so this is an additive
 * adaptation, not a deviation.
 */

import { openemrHome, openemrMenuLink } from '../../utils/openemrLinks'
import { PatientSearch } from './PatientSearch'

const NAV_ITEMS = [
  'Calendar',
  'Finder',
  'Flow',
  'Recalls',
  'Messages',
  'Patient',
  'Fees',
  'Modules',
  'Procedures',
  'Admin',
  'Reports',
  'Miscellaneous',
  'Popups',
]

/** Logo links to OpenEMR's main shell (no specific destination implied). */
const LOGO_HREF = openemrHome()

export function MainNav() {
  const auth = useAuth()
  const queryClient = useQueryClient()
  const [userMenuOpen, setUserMenuOpen] = useState(false)
  const [hamburgerOpen, setHamburgerOpen] = useState(false)
  const userMenuRef = useRef<HTMLDivElement>(null)
  const hamburgerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (userMenuRef.current && !userMenuRef.current.contains(e.target as Node)) {
        setUserMenuOpen(false)
      }
      if (hamburgerRef.current && !hamburgerRef.current.contains(e.target as Node)) {
        setHamburgerOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  // Close the hamburger drawer when the viewport grows past `lg`. Without this,
  // a user who opens the drawer on phone-portrait and rotates to a wider layout
  // is left with an orphan dropdown.
  useEffect(() => {
    const mql = window.matchMedia('(min-width: 1024px)')
    const onChange = () => mql.matches && setHamburgerOpen(false)
    mql.addEventListener('change', onChange)
    return () => mql.removeEventListener('change', onChange)
  }, [])

  const handleSignOut = async () => {
    queryClient.clear()
    await auth.signoutRedirect()
  }

  return (
    <nav
      className="bg-white border-b border-gray-200 px-4 py-2"
      aria-label="OpenEMR application menu"
    >
      <div className="flex items-center justify-between gap-3">
        {/* Left: logo + (≥md) horizontal nav items */}
        <div className="flex items-center gap-4 min-w-0">
          <a
            href={LOGO_HREF}
            target="_blank"
            rel="noopener noreferrer"
            className="shrink-0 inline-block focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 rounded-full"
            aria-label="OpenEMR home"
          >
            <OpenEmrLogo />
          </a>

          <ul className="hidden lg:flex items-center gap-4 text-sm m-0 flex-wrap">
            {NAV_ITEMS.map((item) => (
              <li key={item}>
                <a
                  href={openemrMenuLink(item)}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="font-semibold text-gray-800 hover:text-blue-700 hover:underline"
                >
                  {item}
                </a>
              </li>
            ))}
          </ul>
        </div>

        {/* Right: search (≥md inline, <md icon-only with overlay), user avatar, hamburger */}
        <div className="flex items-center gap-1 sm:gap-2 shrink-0">
          {/* Patient search — real FHIR search at ≥lg widths */}
          <div className="hidden lg:block">
            <PatientSearch />
          </div>
          <button
            type="button"
            aria-label="Search"
            className="p-2 min-h-11 min-w-11 lg:hidden text-gray-600 hover:text-blue-700 flex items-center justify-center"
          >
            <SearchIcon />
          </button>

          {/* User menu — OpenEMR's avatar slot */}
          <div ref={userMenuRef} className="relative">
            <button
              type="button"
              onClick={() => setUserMenuOpen((s) => !s)}
              aria-label="User menu"
              aria-expanded={userMenuOpen}
              className="p-2 min-h-11 min-w-11 lg:min-h-0 lg:min-w-0 lg:p-1 text-gray-600 hover:text-blue-700 flex items-center justify-center"
            >
              <UserIcon />
            </button>
            {userMenuOpen && (
              <div
                role="menu"
                className="absolute right-0 mt-1 w-44 bg-white border border-gray-200 shadow-md py-1 z-30"
              >
                <button
                  type="button"
                  role="menuitem"
                  onClick={handleSignOut}
                  className="w-full text-left px-3 py-3 lg:py-1.5 text-sm text-gray-700 hover:bg-gray-50 focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-blue-600"
                >
                  Sign out
                </button>
              </div>
            )}
          </div>

          {/* Hamburger — visible on <lg only (so phone landscape + tablet portrait + tablet landscape all use it) */}
          <div ref={hamburgerRef} className="relative lg:hidden">
            <button
              type="button"
              onClick={() => setHamburgerOpen((s) => !s)}
              aria-label={hamburgerOpen ? 'Close menu' : 'Open menu'}
              aria-expanded={hamburgerOpen}
              className="p-2 min-h-11 min-w-11 text-gray-700 hover:text-blue-700 flex items-center justify-center"
            >
              {hamburgerOpen ? <CloseIcon /> : <HamburgerIcon />}
            </button>
            {hamburgerOpen && (
              <div
                role="menu"
                aria-label="Application menu"
                className="absolute right-0 mt-1 w-56 bg-white border border-gray-200 shadow-lg py-1 z-30"
              >
                <ul className="m-0">
                  {NAV_ITEMS.map((item) => (
                    <li key={item}>
                      <a
                        role="menuitem"
                        href={openemrMenuLink(item)}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="block px-4 py-3 min-h-11 text-sm font-semibold text-gray-800 hover:bg-gray-50 hover:text-blue-700 focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-blue-600"
                        onClick={() => setHamburgerOpen(false)}
                      >
                        {item}
                      </a>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </div>
      </div>
    </nav>
  )
}

/**
 * OpenEMR brand mark — sourced verbatim from
 * `public/images/logos/core/menu/primary/logo.svg` in this repo so the
 * dashboard uses the canonical artwork. We inline it (rather than
 * loading via <img>) to inherit the app's blue via `currentColor`,
 * matching how legacy OpenEMR styles the menu logo.
 */
function OpenEmrLogo() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 287 287"
      width="26"
      height="26"
      className="text-blue-700"
      fill="currentColor"
      aria-hidden="true"
    >
      <g transform="matrix(1.8257135,0,0,1.7522053,-3.3357514e-4,-3.0245411e-7)">
        <path d="m 67.702,135.668 c 19.27,8.211 42.485,4.396 58.198,-11.317 20.693,-20.694 20.693,-54.254 0,-74.947 a 55.4,55.4 0 0 0 -7.048,-5.949 65.785,65.785 0 0 1 10.281,8.406 c 25.608,25.608 25.608,67.123 0,92.73 -19.205,19.205 -47.334,23.99 -71.001,14.42 a 13.906,13.906 0 0 1 -4.268,-2.78 c -4.915,-4.916 -4.915,-12.933 0,-17.849 a 12.613,12.613 0 0 1 13.838,-2.714" />
        <path d="m 28.127,50.697 c -8.213,19.27 -4.397,42.485 11.317,58.2 20.692,20.691 54.253,20.691 74.945,0 a 55.336,55.336 0 0 0 5.95,-7.05 65.747,65.747 0 0 1 -8.406,10.282 c -25.608,25.607 -67.122,25.607 -92.73,0 C -0.003,92.924 -4.787,64.795 4.783,41.127 a 13.87,13.87 0 0 1 2.78,-4.268 c 4.915,-4.914 12.934,-4.914 17.848,0 a 12.612,12.612 0 0 1 2.716,13.838" />
        <path d="m 132.82,49.792 c -7.826,-19.464 -26.9,-33.173 -49.146,-33.173 -29.229,0 -52.96,23.733 -52.96,52.96 0,3.104 0.258,6.208 0.776,9.183 A 68.08,68.08 0 0 1 30.196,65.571 C 30.196,29.359 59.554,0 95.766,0 c 27.16,0 50.439,16.49 60.398,40.028 0.647,1.551 1.035,3.232 1.035,4.98 0,6.983 -5.627,12.61 -12.61,12.61 -5.367,0.063 -9.894,-3.234 -11.77,-7.826" />
      </g>
    </svg>
  )
}

function SearchIcon() {
  return (
    <svg viewBox="0 0 16 16" width="16" height="16" fill="currentColor" aria-hidden="true">
      <path d="M11.742 10.344a6.5 6.5 0 1 0-1.397 1.398h-.001q.044.06.098.115l3.85 3.85a1 1 0 0 0 1.415-1.414l-3.85-3.85a1 1 0 0 0-.115-.1zM12 6.5a5.5 5.5 0 1 1-11 0 5.5 5.5 0 0 1 11 0" />
    </svg>
  )
}

function UserIcon() {
  return (
    <svg viewBox="0 0 16 16" width="20" height="20" fill="currentColor" aria-hidden="true">
      <path d="M8 8a3 3 0 1 0 0-6 3 3 0 0 0 0 6m2-3a2 2 0 1 1-4 0 2 2 0 0 1 4 0m4 8c0 1-1 1-1 1H3s-1 0-1-1 1-4 6-4 6 3 6 4m-1-.004c-.001-.246-.154-.986-.832-1.664C11.516 10.68 10.289 10 8 10s-3.516.68-4.168 1.332c-.678.678-.83 1.418-.832 1.664z" />
    </svg>
  )
}

function HamburgerIcon() {
  return (
    <svg viewBox="0 0 16 16" width="20" height="20" fill="currentColor" aria-hidden="true">
      <path d="M2.5 12a.5.5 0 0 1 .5-.5h10a.5.5 0 0 1 0 1H3a.5.5 0 0 1-.5-.5m0-4a.5.5 0 0 1 .5-.5h10a.5.5 0 0 1 0 1H3a.5.5 0 0 1-.5-.5m0-4a.5.5 0 0 1 .5-.5h10a.5.5 0 0 1 0 1H3a.5.5 0 0 1-.5-.5" />
    </svg>
  )
}

function CloseIcon() {
  return (
    <svg viewBox="0 0 16 16" width="18" height="18" fill="currentColor" aria-hidden="true">
      <path d="M2.146 2.854a.5.5 0 1 1 .708-.708L8 7.293l5.146-5.147a.5.5 0 0 1 .708.708L8.707 8l5.147 5.146a.5.5 0 0 1-.708.708L8 8.707l-5.146 5.147a.5.5 0 0 1-.708-.708L7.293 8z" />
    </svg>
  )
}
