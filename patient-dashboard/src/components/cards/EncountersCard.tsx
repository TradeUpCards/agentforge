import { useEffect, useState } from 'react'
import { useEncounters } from '../../hooks/useEncounters'
import { extractBundleResources } from '../../utils/fhirParsers'
import { formatDate } from '../../utils/formatters'
import { openemrEditLink } from '../../utils/openemrLinks'
import type { FhirEncounter } from '../../types/fhir'
import { CardShell } from './CardShell'
import { EmptyState } from '../ui/EmptyState'
import { ExpandableRow, DetailGrid } from '../ui/ExpandableRow'

/**
 * PDF requirement #4 — additional section. We chose Encounters (defended
 * in §7 of PATIENT_DASHBOARD_MIGRATION.md). Sorted newest-first, capped
 * at 10 per the FHIR query.
 *
 * Mirrors `interface/patient_file/history/encounters.php`.
 *
 * Each row carries `id="encounter-<fhirId>"` so the patient-header
 * encounter selector can hash-anchor straight to the matching row. When
 * the URL hash points at a row, that row gets a brief yellow flash to
 * draw the eye, then settles back to default.
 */
export function EncountersCard({ patientId }: { patientId: string }) {
  const { data, isLoading, error, refetch } = useEncounters(patientId)
  const encounters = data ? extractBundleResources<FhirEncounter>(data) : []
  const [highlightedId, setHighlightedId] = useState<string | null>(null)

  // Listen for hash changes — when the patient-header encounter selector
  // navigates to `#encounter-<id>`, highlight that row briefly. We do
  // this in addition to the browser's native scroll-to-anchor behavior.
  useEffect(() => {
    function handle() {
      const m = /^#encounter-(.+)$/.exec(window.location.hash)
      if (m && m[1]) {
        setHighlightedId(m[1])
        // Clear the highlight after the flash so re-clicking the same
        // selector item re-triggers the animation.
        const t = window.setTimeout(() => setHighlightedId(null), 1500)
        return () => window.clearTimeout(t)
      }
      return undefined
    }
    handle() // run once for initial hash
    window.addEventListener('hashchange', handle)
    return () => window.removeEventListener('hashchange', handle)
  }, [])

  return (
    <CardShell
      title="Encounter History"
      isLoading={isLoading}
      error={error}
      onRetry={() => void refetch()}
      editHref={openemrEditLink('encounter')}
    >
      {!isLoading && encounters.length === 0 ? (
        <EmptyState message={data ? 'No Encounters Recorded' : 'Nothing Recorded'} />
      ) : (
        <ul className="m-0 p-0 list-none">
          {encounters.map((e) => {
            const isHighlighted = e.id === highlightedId
            return (
              <div
                key={e.id ?? `${encounterTypeLabel(e)}-${e.period?.start ?? ''}`}
                id={e.id ? `encounter-${e.id}` : undefined}
                className={
                  isHighlighted
                    ? 'transition-colors duration-300 bg-yellow-100'
                    : 'transition-colors duration-1000'
                }
              >
                <ExpandableRow
                  summary={<EncounterSummary encounter={e} />}
                  details={<EncounterDetails encounter={e} />}
                />
              </div>
            )
          })}
        </ul>
      )}
    </CardShell>
  )
}

function encounterTypeLabel(e: FhirEncounter): string {
  return e.type?.[0]?.text ?? e.type?.[0]?.coding?.[0]?.display ?? 'Encounter'
}

function EncounterSummary({ encounter }: { encounter: FhirEncounter }) {
  const date = encounter.period?.start
  const provider = encounter.participant?.[0]?.individual?.display
  const reason = encounter.reasonCode?.[0]?.text
  return (
    <span className="block min-w-0">
      <span className="flex items-center justify-between gap-2">
        <span className="text-gray-800 truncate">{encounterTypeLabel(encounter)}</span>
        {date && (
          <span className="text-xs text-gray-500 shrink-0">{formatDate(date)}</span>
        )}
      </span>
      {(provider ?? reason) && (
        <span className="text-xs text-gray-500 block mt-0.5 truncate">
          {provider}
          {provider && reason ? ' — ' : ''}
          {reason}
        </span>
      )}
    </span>
  )
}

function EncounterDetails({ encounter }: { encounter: FhirEncounter }) {
  const cls = encounter.class?.display ?? encounter.class?.code
  return (
    <DetailGrid
      rows={[
        {
          label: 'Type',
          value: encounter.type?.[0]?.text ?? null,
        },
        { label: 'Class', value: cls ?? null },
        {
          label: 'Service type',
          value:
            encounter.serviceType?.text ??
            encounter.serviceType?.coding?.[0]?.display ??
            null,
        },
        { label: 'Status', value: encounter.status ?? null },
        {
          label: 'Period start',
          value: encounter.period?.start ? formatDate(encounter.period.start) : null,
        },
        {
          label: 'Period end',
          value: encounter.period?.end ? formatDate(encounter.period.end) : null,
        },
        {
          label: 'Provider',
          value: encounter.participant?.[0]?.individual?.display ?? null,
        },
        {
          label: 'Reason',
          value: encounter.reasonCode?.[0]?.text ?? null,
        },
      ]}
    />
  )
}
