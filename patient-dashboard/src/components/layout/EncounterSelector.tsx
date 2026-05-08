import { useState, useRef, useEffect } from 'react'
import { useEncounters } from '../../hooks/useEncounters'
import { extractBundleResources } from '../../utils/fhirParsers'
import { formatDate } from '../../utils/formatters'
import { openemrVisitHistory } from '../../utils/openemrLinks'
import type { FhirEncounter } from '../../types/fhir'
import { Spinner } from '../ui/Spinner'

/**
 * Three-button group that mirrors the legacy OpenEMR widget at
 * `interface/main/tabs/templates/patient_data_template.php` lines
 * 121–166:
 *
 *   [ 🕒 Visit History ]  [ Select Encounter (N) ▾ ]
 *
 * We omit the legacy "+ New Encounter" button — our dashboard is
 * read-only, so creating an encounter requires the legacy app
 * (covered by the Visit History out-link instead).
 *
 * Behavior of the dropdown:
 *   - Pulls live FHIR encounters via `useEncounters` (already cached
 *     by TanStack Query — same query key as `EncountersCard`, so the
 *     network call is shared).
 *   - Each item shows date · class · reason; click navigates to
 *     `#encounter-<id>` so the browser scrolls the EncountersCard
 *     entry into view, and EncountersCard's hashchange listener
 *     gives the matched row a brief yellow flash.
 *
 * Visit History button:
 *   - Opens the legacy `/interface/patient_file/history/encounters.php`
 *     (currently routed via main_screen.php — see `openemrLinks.ts`).
 */
export function EncounterSelector({ patientId }: { patientId: string }) {
  const { data, isLoading } = useEncounters(patientId)
  const encounters = data ? extractBundleResources<FhirEncounter>(data) : []
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  return (
    <div className="inline-flex items-center gap-0.5 text-sm">
      <a
        href={openemrVisitHistory()}
        target="_blank"
        rel="noopener noreferrer"
        title="Visit History (opens in OpenEMR)"
        aria-label="Visit History (opens in OpenEMR)"
        className="
          inline-flex items-center justify-center
          px-2 py-1 min-h-11 min-w-11 md:min-h-0 md:min-w-0 md:px-2.5 md:py-1
          border border-gray-300 bg-white text-gray-700 hover:bg-gray-50
          focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600
        "
      >
        <ClockIcon />
      </a>
      <div ref={ref} className="relative">
        <button
          type="button"
          onClick={() => setOpen((s) => !s)}
          aria-expanded={open}
          aria-haspopup="menu"
          className="
            inline-flex items-center gap-1
            px-2 py-1 min-h-11 md:min-h-0 md:px-2.5 md:py-1
            border border-l-0 border-gray-300 bg-white text-gray-700 hover:bg-gray-50
            focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600
          "
        >
          <span>Select Encounter</span>
          <span className="text-gray-500" aria-hidden="true">
            ({isLoading ? '…' : encounters.length})
          </span>
          <ChevronIcon expanded={open} />
        </button>
        {open && (
          <div
            role="menu"
            aria-label="Past encounters"
            className="absolute right-0 mt-1 w-72 max-h-80 overflow-y-auto bg-white border border-gray-200 shadow-lg z-30"
          >
            {isLoading ? (
              <div className="px-3 py-3 flex items-center gap-2">
                <Spinner label="Loading encounters" />
                <span className="text-xs text-gray-600">Loading encounters…</span>
              </div>
            ) : encounters.length === 0 ? (
              <p className="px-3 py-3 text-xs text-gray-500 italic m-0">
                No encounters recorded.
              </p>
            ) : (
              <ul className="m-0 p-0 list-none">
                {encounters.map((e) => (
                  <li key={e.id ?? `${e.period?.start}-${e.status}`}>
                    <a
                      role="menuitem"
                      href={e.id ? `#encounter-${e.id}` : '#'}
                      onClick={() => setOpen(false)}
                      className="
                        flex items-center justify-between gap-2 px-3 py-2 min-h-11
                        text-xs text-gray-800
                        hover:bg-gray-50
                        focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-blue-600
                      "
                    >
                      <span className="flex-1 min-w-0 truncate">
                        <span className="font-mono text-gray-600 mr-2">
                          {e.period?.start ? formatDate(e.period.start) : '—'}
                        </span>
                        <span className="text-gray-700">
                          {e.type?.[0]?.text ??
                            e.type?.[0]?.coding?.[0]?.display ??
                            e.class?.display ??
                            'Encounter'}
                        </span>
                      </span>
                    </a>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

function ClockIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor" aria-hidden="true">
      <path d="M8 3.5a.5.5 0 0 0-1 0V9a.5.5 0 0 0 .252.434l3.5 2a.5.5 0 0 0 .496-.868L8 8.71z" />
      <path d="M8 16A8 8 0 1 0 8 0a8 8 0 0 0 0 16m7-8A7 7 0 1 1 1 8a7 7 0 0 1 14 0" />
    </svg>
  )
}

function ChevronIcon({ expanded }: { expanded: boolean }) {
  return (
    <svg
      viewBox="0 0 16 16"
      width="10"
      height="10"
      fill="currentColor"
      aria-hidden="true"
      className={`text-gray-500 transition-transform ${expanded ? 'rotate-180' : ''}`}
    >
      <path d="M1.646 4.646a.5.5 0 0 1 .708 0L8 10.293l5.646-5.647a.5.5 0 0 1 .708.708l-6 6a.5.5 0 0 1-.708 0l-6-6a.5.5 0 0 1 0-.708" />
    </svg>
  )
}
