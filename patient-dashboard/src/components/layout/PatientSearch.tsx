import { useState, useEffect, useRef, useId } from 'react'
import { useAuth } from 'react-oidc-context'
import { useNavigate } from 'react-router-dom'
import { fhirGet } from '../../api/fhirClient'
import {
  extractBundleResources,
  getPatientDisplayName,
  getPatientMrn,
} from '../../utils/fhirParsers'
import { formatDate } from '../../utils/formatters'
import type { FhirBundle, FhirPatient } from '../../types/fhir'
import { Spinner } from '../ui/Spinner'

/**
 * Inline patient search wired to the FHIR Patient resource.
 *
 * Replaces the legacy "Search by any demographic" decorative input.
 * Behavior:
 *   - 300 ms debounce: keystrokes within the window are coalesced into
 *     a single FHIR call.
 *   - Trims and ignores queries shorter than 2 characters (FHIR servers
 *     return very wide bundles for one-letter prefixes; not worth the
 *     bandwidth).
 *   - Searches `Patient?name=<q>` (FHIR's `name` parameter matches across
 *     family, given, prefix, suffix, and `text`). Uses `_count=8` to cap
 *     the results popover at a comfortable scan-length.
 *   - Click a result -> navigate to `/dashboard/<patient.id>`.
 *   - Closes on outside click, Escape, or after a result is chosen.
 *   - Read-only — uses the existing `user/Patient.rs` scope; no new
 *     OAuth2 scope required.
 *
 * Visible only at `lg+` widths (the same breakpoint MainNav uses for
 * the inline search input).
 */
export function PatientSearch() {
  const auth = useAuth()
  const navigate = useNavigate()
  const [query, setQuery] = useState('')
  const [debouncedQuery, setDebouncedQuery] = useState('')
  const [open, setOpen] = useState(false)
  const [results, setResults] = useState<FhirPatient[]>([])
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const inputId = useId()
  const listboxId = useId()

  // Debounce — 300 ms after the last keystroke.
  useEffect(() => {
    const trimmed = query.trim()
    if (trimmed.length < 2) {
      setDebouncedQuery('')
      setResults([])
      return
    }
    const t = window.setTimeout(() => setDebouncedQuery(trimmed), 300)
    return () => window.clearTimeout(t)
  }, [query])

  // Fetch on debounced query change.
  useEffect(() => {
    if (!debouncedQuery) return
    const token = auth.user?.access_token
    if (!token) return

    let cancelled = false
    setIsLoading(true)
    setError(null)
    fhirGet<FhirBundle<FhirPatient>>(
      `/Patient?name=${encodeURIComponent(debouncedQuery)}&_count=8`,
      token,
    )
      .then((bundle) => {
        if (cancelled) return
        setResults(extractBundleResources<FhirPatient>(bundle))
      })
      .catch((e) => {
        if (cancelled) return
        setError(e instanceof Error ? e.message : 'Search failed')
        setResults([])
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [debouncedQuery, auth.user?.access_token])

  // Close on outside click.
  useEffect(() => {
    function onMouseDown(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', onMouseDown)
    return () => document.removeEventListener('mousedown', onMouseDown)
  }, [])

  // Close on Escape.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [])

  const handleSelect = (patient: FhirPatient) => {
    setOpen(false)
    setQuery('')
    setResults([])
    if (patient.id) navigate(`/dashboard/${patient.id}`)
  }

  return (
    <div ref={containerRef} className="relative">
      <label className="sr-only" htmlFor={inputId}>
        Search patients
      </label>
      <input
        id={inputId}
        type="search"
        value={query}
        onChange={(e) => {
          setQuery(e.target.value)
          setOpen(true)
        }}
        onFocus={() => query.trim().length >= 2 && setOpen(true)}
        placeholder="Search by any demographic"
        autoComplete="off"
        role="combobox"
        aria-expanded={open}
        aria-controls={listboxId}
        aria-autocomplete="list"
        className="px-2 py-1 text-sm border border-gray-300 rounded focus:outline-2 focus:outline-blue-600 focus:outline-offset-1 w-56"
      />
      {open && query.trim().length >= 2 && (
        <div
          id={listboxId}
          role="listbox"
          className="absolute right-0 mt-1 w-80 max-h-96 overflow-y-auto bg-white border border-gray-200 shadow-lg z-30"
        >
          {isLoading ? (
            <div className="px-3 py-3 flex items-center gap-2">
              <Spinner label="Searching" />
              <span className="text-xs text-gray-600">Searching…</span>
            </div>
          ) : error ? (
            <p className="px-3 py-3 text-xs text-red-700 italic m-0" role="alert">
              Search failed. Try again.
            </p>
          ) : results.length === 0 ? (
            <p className="px-3 py-3 text-xs text-gray-500 italic m-0">
              No patients matched.
            </p>
          ) : (
            <ul className="m-0 p-0 list-none">
              {results.map((p) => (
                <li key={p.id ?? `${p.birthDate}-${getPatientDisplayName(p)}`}>
                  <button
                    type="button"
                    role="option"
                    aria-selected="false"
                    onClick={() => handleSelect(p)}
                    className="
                      w-full text-left px-3 py-2 min-h-11
                      hover:bg-gray-50
                      focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-blue-600
                    "
                  >
                    <p className="text-sm font-medium text-gray-900 m-0 truncate">
                      {getPatientDisplayName(p)}
                    </p>
                    <p className="text-xs text-gray-500 m-0 mt-0.5 truncate">
                      DOB: {formatDate(p.birthDate)} · MRN:{' '}
                      <span className="font-mono">{getPatientMrn(p)}</span>
                    </p>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}
