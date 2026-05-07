import { useAllergies } from '../../hooks/useAllergies'
import { extractBundleResources } from '../../utils/fhirParsers'
import type { FhirAllergyIntolerance } from '../../types/fhir'
import { CardShell } from './CardShell'
import { Badge } from '../ui/Badge'
import { EmptyState } from '../ui/EmptyState'

/**
 * PDF requirement #3a — Allergies card.
 *
 * Severity → badge mapping mirrors `templates/patient/card/allergies.html.twig`:
 *   - criticality === 'high'                 → red `danger` badge
 *   - reaction[0].severity === 'severe'      → yellow `warning` badge
 *   - other / unknown                        → muted badge
 *
 * Empty-state copy mirrors the legacy dashboard:
 *   - bundle returned, zero entries          → "No Known Allergies"
 *   - bundle never returned (shouldn't happen post-load) → "Nothing Recorded"
 */
export function AllergiesCard({ patientId }: { patientId: string }) {
  const { data, isLoading, error, refetch } = useAllergies(patientId)
  const allergies = data ? extractBundleResources<FhirAllergyIntolerance>(data) : []

  return (
    <CardShell
      title="Allergies"
      isLoading={isLoading}
      error={error}
      onRetry={() => void refetch()}
    >
      {!isLoading && allergies.length === 0 ? (
        <EmptyState message={data ? 'No Known Allergies' : 'Nothing Recorded'} />
      ) : (
        <ul className="divide-y divide-gray-100">
          {allergies.map((a) => {
            const name =
              a.code?.text ?? a.code?.coding?.[0]?.display ?? 'Unspecified allergy'
            const severity = a.reaction?.[0]?.severity
            return (
              <li
                key={a.id ?? name}
                className="py-1.5 flex items-center justify-between gap-2"
              >
                <span className="text-gray-800">{name}</span>
                <div className="flex gap-1 shrink-0">
                  {a.criticality === 'high' && (
                    <Badge label="High" variant="danger" title="High criticality" />
                  )}
                  {severity && (
                    <Badge
                      label={severity}
                      variant={severity === 'severe' ? 'warning' : 'muted'}
                      title={`Reaction severity: ${severity}`}
                    />
                  )}
                </div>
              </li>
            )
          })}
        </ul>
      )}
    </CardShell>
  )
}
