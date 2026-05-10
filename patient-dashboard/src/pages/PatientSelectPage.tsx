import { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { usePatientList } from '../hooks/usePatientList'
import { useMediaQuery } from '../hooks/useMediaQuery'
import { ErrorRetry } from '../components/ui/ErrorRetry'
import { MainNav } from '../components/layout/MainNav'
import { formatDate, capitalize } from '../utils/formatters'
import type { ApiPatientSummary } from '../api/resources/patientList'

/**
 * Scroll dead-zone before showing/hiding fade gradients — same threshold
 * SubNav uses for the horizontal fades, kept consistent so the visual
 * cue feels identical to the user.
 */
const FADE_THRESHOLD_PX = 4

/**
 * "All" sentinel for the page-size dropdown — matches whatever the
 * filtered list size happens to be. Encoded as 0 internally so the
 * value type stays `number` and only the UI maps the special case.
 */
const SHOW_ALL = 0

const PAGE_SIZE_OPTIONS: Array<{ value: number; label: string }> = [
  { value: 10, label: '10' },
  { value: 25, label: '25' },
  { value: 50, label: '50' },
  { value: 100, label: '100' },
  { value: SHOW_ALL, label: 'All' },
]

/**
 * Default page size based on viewport. Two signals:
 *   - width bucket (phone / tablet / desktop) for the obvious case
 *   - viewport HEIGHT for short layouts like phone landscape, where
 *     the width-only check would put a 390-tall device into the
 *     "tablet, 25 per page" bucket and bury the pagination controls
 *     below 5+ scrolls of patients.
 *
 * A "short viewport" forces the phone default regardless of width.
 */
function defaultPageSizeFor(
  isPhone: boolean,
  isTablet: boolean,
  isShort: boolean,
): number {
  if (isPhone || isShort) return 10
  if (isTablet) return 25
  return 50
}

/**
 * Patient picker. Layer 1 of the patient-access defense (§6 of
 * PATIENT_DASHBOARD_MIGRATION.md): the FHIR Patient search is
 * server-filtered to patients the authenticated user is authorized to
 * view, so the user can only navigate to IDs they were shown.
 *
 * Source: `GET /fhir/Patient?_count=200` (Bundle of FhirPatient
 * resources). 200 is generous for the demo data set; production
 * deployments with >200 patients will want server-side pagination via
 * the Bundle `link` (next/prev) navigation. That is recorded as
 * future-work in §10 of PATIENT_DASHBOARD_MIGRATION.md.
 *
 * UX:
 *   - `MainNav` at the top so the page lives in the same chrome as
 *     `/dashboard/{id}` — including the global PatientSearch in the nav
 *     for the "I know who I want" case.
 *   - Inline filter input filters the loaded list client-side, scoped
 *     to name + MRN + DOB. Empty filter shows everything.
 *   - Page size adapts to viewport on first load (10 phone / 25 tablet
 *     / 50 desktop) and the user can override via a "Show:" dropdown
 *     (10 / 25 / 50 / 100 / All). Once the user picks a value the
 *     viewport-driven default no longer overrides — their choice
 *     sticks until they change it.
 *   - Pagination at the chosen page size with prev/next buttons + a
 *     "Showing X-Y of Z" indicator.
 */
export function PatientSelectPage() {
  const { data: patients, isLoading, error, refetch } = usePatientList()

  const isPhone = useMediaQuery('(max-width: 767px)')
  const isTablet = useMediaQuery('(min-width: 768px) and (max-width: 1023px)')
  // Short viewport ≈ phone landscape (iPhone 14 landscape ≈ 390 tall).
  // Tablet landscape and up are well above this.
  const isShort = useMediaQuery('(max-height: 500px)')

  const [filter, setFilter] = useState('')
  const [page, setPage] = useState(0) // zero-indexed

  // Page size: starts as `null` (= "follow viewport default"), becomes a
  // sticky number once the user picks from the dropdown. Persisting the
  // viewport-default behavior until first interaction means a desktop
  // user lands on 50/page automatically and a phone user lands on
  // 10/page automatically — no per-device manual setting needed.
  const [chosenPageSize, setChosenPageSize] = useState<number | null>(null)
  const viewportDefault = defaultPageSizeFor(isPhone, isTablet, isShort)
  const effectivePageSize =
    chosenPageSize === null ? viewportDefault : chosenPageSize

  const filtered = useMemo<ApiPatientSummary[]>(() => {
    if (!patients) return []
    const q = filter.trim().toLowerCase()
    if (!q) return patients
    return patients.filter((p) => {
      const name = (p.displayName ?? '').toLowerCase()
      const mrn = (p.mrn ?? '').toLowerCase()
      const dob = (p.dob ?? '').toLowerCase()
      return name.includes(q) || mrn.includes(q) || dob.includes(q)
    })
  }, [patients, filter])

  // "All" = no slicing, render the entire filtered set.
  const isShowAll = effectivePageSize === SHOW_ALL
  const totalPages = isShowAll
    ? 1
    : Math.max(1, Math.ceil(filtered.length / effectivePageSize))
  // Clamp the current page when the filter shrinks the result set or the
  // user picks a larger page size.
  const safePage = Math.min(page, totalPages - 1)
  const start = isShowAll ? 0 : safePage * effectivePageSize
  const end = isShowAll
    ? filtered.length
    : Math.min(start + effectivePageSize, filtered.length)
  const pageRows = filtered.slice(start, end)

  // Scroll-aware fade gradients on the patient list — same UX pattern as
  // SubNav's horizontal scroll cue, rotated 90°. Top fade appears once the
  // user has scrolled past the start of the list; bottom fade while there
  // is still content to scroll into view. Pure visual cue (pointer-events
  // none, aria-hidden); no clickability.
  const listRef = useRef<HTMLUListElement>(null)
  const [showTopFade, setShowTopFade] = useState(false)
  const [showBottomFade, setShowBottomFade] = useState(false)

  useEffect(() => {
    const el = listRef.current
    if (!el) return

    const update = () => {
      const { scrollTop, scrollHeight, clientHeight } = el
      setShowTopFade(scrollTop > FADE_THRESHOLD_PX)
      setShowBottomFade(
        scrollTop < scrollHeight - clientHeight - FADE_THRESHOLD_PX,
      )
    }

    update()
    el.addEventListener('scroll', update, { passive: true })
    // Re-evaluate on resize (the same scroll position can flip from
    // "more below" to "no more below" when the viewport grows or shrinks)
    // and when the page contents change (paging / filtering).
    const ro = new ResizeObserver(update)
    ro.observe(el)
    return () => {
      el.removeEventListener('scroll', update)
      ro.disconnect()
    }
  }, [pageRows.length, isShort])

  // Reset to page 0 whenever the page size changes — staying on page 5
  // of a 25-per-page view doesn't translate cleanly to a 100-per-page
  // view, so resetting is the least-surprising behavior.
  useEffect(() => {
    setPage(0)
  }, [effectivePageSize])

  // The count text in the header row varies by state so the chrome
  // remains visible regardless of whether data has loaded. Computing it
  // once here keeps the JSX below tidy.
  const countText = (() => {
    if (isLoading) return 'Loading…'
    if (error) return 'Failed to load'
    if (!patients || patients.length === 0) return 'No patients'
    if (filtered.length === 0) return 'No matches'
    return `Showing ${start + 1}-${end} of ${filtered.length}`
  })()

  // Skeleton row count for the loading state — match the page-size that
  // will be used once data arrives so the layout doesn't jump.
  const skeletonCount = isShowAll ? 10 : effectivePageSize

  return (
    // `h-dvh` (dynamic viewport height) on the root + `flex flex-col`
    // gives the inner `<main>` a real bounded height to fill via flex-1.
    // `min-h-screen` would not, because it is a minimum: the body could
    // grow taller than viewport and the list would never overflow → no
    // scroll, no fade. `dvh` over `vh` so mobile browsers' collapsing URL
    // bar doesn't leave dead space at the bottom.
    <div className="h-dvh bg-gray-50 flex flex-col overflow-hidden">
      <MainNav />

      <main
        className={`
          max-w-4xl w-full mx-auto px-4 flex-1
          flex flex-col min-h-0
          ${isShort ? 'py-1' : 'py-3 sm:py-4 md:py-6'}
        `}
      >
        {/* Header row — h1 + filter + page-size dropdown + count, all on
            one row when there is room (mobile landscape and up). On
            phone portrait the row wraps via `flex-wrap`: h1 stays on
            top by itself, then filter, then page-size + count. The
            filter has `min-w-[12rem]` so wrap happens at a usable
            width rather than collapsing to a sliver.

            The chrome is rendered in every state — including loading
            and error — so the page skeleton is stable. The controls
            disable themselves while the underlying list is unavailable
            instead of disappearing. */}
        <div
          className={`
            flex items-center gap-3 flex-wrap
            ${isShort ? 'mb-1' : 'mb-2 sm:mb-4'}
          `}
        >
          <h1 className="text-base font-semibold text-gray-800 m-0 whitespace-nowrap">
            Select Patient
          </h1>

          <label htmlFor="patient-filter" className="sr-only">
            Filter patients
          </label>
          <input
            id="patient-filter"
            type="search"
            value={filter}
            onChange={(e) => {
              setFilter(e.target.value)
              setPage(0) // reset to first page on filter change
            }}
            disabled={isLoading || !!error}
            placeholder="Filter by name, MRN, or DOB"
            autoComplete="off"
            className="
              flex-1 min-w-[12rem] max-w-md
              px-3 py-2 min-h-11
              text-sm border border-gray-300 rounded
              focus:outline-2 focus:outline-blue-600 focus:outline-offset-1
              disabled:bg-gray-50 disabled:text-gray-400
            "
          />

          <label
            htmlFor="patient-page-size"
            className="text-xs text-gray-600 m-0 whitespace-nowrap"
          >
            Show:
          </label>
          <select
            id="patient-page-size"
            value={chosenPageSize ?? viewportDefault}
            onChange={(e) => setChosenPageSize(Number(e.target.value))}
            disabled={isLoading || !!error}
            className="
              px-2 py-2 min-h-11
              text-sm border border-gray-300 rounded bg-white
              focus:outline-2 focus:outline-blue-600 focus:outline-offset-1
              disabled:bg-gray-50 disabled:text-gray-400
            "
          >
            {PAGE_SIZE_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>

          <p
            className="text-xs text-gray-600 m-0 whitespace-nowrap"
            aria-live="polite"
          >
            {countText}
          </p>
        </div>

        {/* List region — always rendered as a bounded scroll container
            so the layout stays stable across loading / error / empty /
            populated states. The contents vary by state; the chrome
            (rounded white card with subtle border) does not. */}
        <div className="relative flex-1 min-h-0">
          {error ? (
            <div className="h-full bg-white rounded-lg border border-gray-200 shadow-sm p-4 overflow-y-auto">
              <ErrorRetry error={error} onRetry={() => void refetch()} />
            </div>
          ) : (
            <ul
              ref={listRef}
              className="
                h-full overflow-y-auto
                bg-white rounded-lg border border-gray-200
                divide-y divide-gray-100 shadow-sm
              "
              aria-busy={isLoading}
            >
              {isLoading ? (
                // Skeleton rows — same vertical rhythm as the real rows
                // so when data arrives the layout doesn't jump.
                Array.from({ length: skeletonCount }, (_, i) => (
                  <SkeletonRow key={i} short={isShort} />
                ))
              ) : !patients || patients.length === 0 ? (
                <li className="px-4 py-6 text-sm text-gray-500 italic">
                  No patients available. You may not have access to any
                  patients yet.
                </li>
              ) : filtered.length === 0 ? (
                <li className="px-4 py-6 text-sm text-gray-500 italic">
                  No patients match “{filter}”.
                </li>
              ) : (
                pageRows.map((p) => (
                  <li key={p.id}>
                    <Link
                      to={`/dashboard/${p.id}`}
                      className={`
                        flex items-center justify-between gap-4 px-4
                        hover:bg-gray-50
                        focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-blue-600
                        transition-colors
                        ${isShort ? 'py-1.5' : 'py-2 sm:py-2.5 md:py-3'}
                      `}
                    >
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium text-gray-900 truncate">
                          {p.displayName}
                        </p>
                        <p className="text-xs text-gray-500">
                          DOB: {formatDate(p.dob)} · Sex: {capitalize(p.sex)}
                        </p>
                      </div>
                      <span className="text-xs font-mono text-gray-500 shrink-0">
                        MRN: {p.mrn || '—'}
                      </span>
                    </Link>
                  </li>
                ))
              )}
            </ul>
          )}

          {/* Top fade — visible once the user has scrolled past the
              list start. Mirrors the bottom-fade pattern in SubNav. */}
          <div
            aria-hidden="true"
            className={`
              pointer-events-none absolute left-0 right-0 top-0 h-6
              bg-gradient-to-b from-white to-transparent
              rounded-t-lg
              motion-safe:transition-opacity duration-150
              ${showTopFade ? 'opacity-100' : 'opacity-0'}
            `}
          />
          {/* Bottom fade — visible while there is more list to scroll
              into view. */}
          <div
            aria-hidden="true"
            className={`
              pointer-events-none absolute left-0 right-0 bottom-0 h-6
              bg-gradient-to-t from-white to-transparent
              rounded-b-lg
              motion-safe:transition-opacity duration-150
              ${showBottomFade ? 'opacity-100' : 'opacity-0'}
            `}
          />
        </div>

        {/* Pagination controls — only show when there is more than
            one page AND data has actually loaded. Buttons are 44px
            tall for touch. */}
        {!isLoading && !error && totalPages > 1 && (
          <nav
            className="flex items-center justify-between gap-3 mt-3"
            aria-label="Patient list pagination"
          >
            <button
              type="button"
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={safePage === 0}
              className="
                inline-flex items-center justify-center
                px-4 py-2 min-h-11
                text-sm font-medium text-gray-700
                border border-gray-300 rounded bg-white hover:bg-gray-50
                disabled:opacity-50 disabled:cursor-not-allowed
                focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600
              "
            >
              ← Previous
            </button>

            <p className="text-xs text-gray-600 m-0" aria-live="polite">
              Page {safePage + 1} of {totalPages}
            </p>

            <button
              type="button"
              onClick={() =>
                setPage((p) => Math.min(totalPages - 1, p + 1))
              }
              disabled={safePage >= totalPages - 1}
              className="
                inline-flex items-center justify-center
                px-4 py-2 min-h-11
                text-sm font-medium text-gray-700
                border border-gray-300 rounded bg-white hover:bg-gray-50
                disabled:opacity-50 disabled:cursor-not-allowed
                focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600
              "
            >
              Next →
            </button>
          </nav>
        )}
      </main>
    </div>
  )
}

/**
 * Skeleton row used during the loading state. Matches the real row's
 * height envelope so the layout doesn't jump when patients arrive.
 * Pure visual placeholder — `animate-pulse` provides the standard
 * "loading shimmer" cue, gated by `motion-safe` so users with reduced-
 * motion preferences see a static skeleton.
 */
function SkeletonRow({ short }: { short: boolean }) {
  return (
    <li aria-hidden="true">
      <div
        className={`
          flex items-center justify-between gap-4 px-4
          motion-safe:animate-pulse
          ${short ? 'py-1.5' : 'py-2 sm:py-2.5 md:py-3'}
        `}
      >
        <div className="flex-1 min-w-0 space-y-1.5">
          <div className="h-3 bg-gray-200 rounded w-1/2" />
          <div className="h-2.5 bg-gray-100 rounded w-1/3" />
        </div>
        <div className="h-2.5 bg-gray-100 rounded w-20 shrink-0" />
      </div>
    </li>
  )
}
