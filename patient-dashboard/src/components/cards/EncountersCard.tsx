import { useEncounters } from '../../hooks/useEncounters'
import { extractBundleResources } from '../../utils/fhirParsers'
import { formatDate } from '../../utils/formatters'
import type { FhirEncounter } from '../../types/fhir'
import { CardShell } from './CardShell'
import { EmptyState } from '../ui/EmptyState'

/**
 * PDF requirement #4 — additional section. We chose Encounters (defended
 * in §7 of PATIENT_DASHBOARD_MIGRATION.md). Sorted newest-first, capped
 * at 10 per the FHIR query.
 *
 * Mirrors `interface/patient_file/history/encounters.php`.
 */
export function EncountersCard({ patientId }: { patientId: string }) {
  const { data, isLoading, error, refetch } = useEncounters(patientId)
  const encounters = data ? extractBundleResources<FhirEncounter>(data) : []

  return (
    <CardShell
      title="Encounter History"
      isLoading={isLoading}
      error={error}
      onRetry={() => void refetch()}
    >
      {!isLoading && encounters.length === 0 ? (
        <EmptyState message={data ? 'No Encounters Recorded' : 'Nothing Recorded'} />
      ) : (
        <ul className="divide-y divide-gray-100">
          {encounters.map((e) => {
            const type =
              e.type?.[0]?.text ?? e.type?.[0]?.coding?.[0]?.display ?? 'Encounter'
            const date = e.period?.start
            const provider = e.participant?.[0]?.individual?.display
            const reason = e.reasonCode?.[0]?.text
            return (
              <li key={e.id ?? `${type}-${date}`} className="py-1.5">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-gray-800">{type}</span>
                  {date && (
                    <span className="text-xs text-gray-500 shrink-0">
                      {formatDate(date)}
                    </span>
                  )}
                </div>
                {(provider ?? reason) && (
                  <p className="text-xs text-gray-500 mt-0.5">
                    {provider}
                    {provider && reason ? ' — ' : ''}
                    {reason}
                  </p>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </CardShell>
  )
}
