import { useAllergies } from '../../hooks/useAllergies'
import { extractBundleResources } from '../../utils/fhirParsers'
import { formatDate } from '../../utils/formatters'
import type { FhirAllergyIntolerance } from '../../types/fhir'
import { CardShell } from './CardShell'
import { Badge } from '../ui/Badge'
import { EmptyState } from '../ui/EmptyState'
import { ExpandableRow, DetailGrid } from '../ui/ExpandableRow'

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
 *
 * Each row is a click-to-expand `ExpandableRow` (inline accordion) that
 * reveals the full FHIR shape: clinical status, verification status,
 * recorded date, and every reaction's manifestation + description.
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
        <ul className="m-0 p-0 list-none">
          {allergies.map((a) => (
            <ExpandableRow
              key={a.id ?? `${a.code?.text ?? 'allergy'}-${a.recordedDate ?? ''}`}
              summary={<AllergySummary allergy={a} />}
              details={<AllergyDetails allergy={a} />}
            />
          ))}
        </ul>
      )}
    </CardShell>
  )
}

function AllergySummary({ allergy }: { allergy: FhirAllergyIntolerance }) {
  const name =
    allergy.code?.text ?? allergy.code?.coding?.[0]?.display ?? 'Unspecified allergy'
  const severity = allergy.reaction?.[0]?.severity
  return (
    <span className="flex items-center justify-between gap-2 min-w-0">
      <span className="text-gray-800 truncate">{name}</span>
      <span className="flex gap-1 shrink-0">
        {allergy.criticality === 'high' && (
          <Badge label="High" variant="danger" title="High criticality" />
        )}
        {severity && (
          <Badge
            label={severity}
            variant={severity === 'severe' ? 'warning' : 'muted'}
            title={`Reaction severity: ${severity}`}
          />
        )}
      </span>
    </span>
  )
}

function AllergyDetails({ allergy }: { allergy: FhirAllergyIntolerance }) {
  const codeSystem = allergy.code?.coding?.[0]?.system
  const codeValue = allergy.code?.coding?.[0]?.code
  const clinicalStatus = allergy.clinicalStatus?.coding?.[0]?.code
  const verificationStatus = allergy.verificationStatus?.coding?.[0]?.code

  const reactions = (allergy.reaction ?? []).map((r, i) => {
    const manifestations = (r.manifestation ?? [])
      .map((m) => m.text ?? m.coding?.[0]?.display)
      .filter(Boolean)
      .join(', ')
    return (
      <div key={i} className="mt-1">
        <span className="font-semibold text-gray-700">Reaction {i + 1}:</span>{' '}
        {manifestations || '—'}
        {r.severity && <span className="ml-2 text-gray-600">({r.severity})</span>}
        {r.description && (
          <p className="m-0 mt-0.5 text-gray-700 italic">{r.description}</p>
        )}
      </div>
    )
  })

  return (
    <DetailGrid
      rows={[
        {
          label: 'Recorded',
          value: allergy.recordedDate ? formatDate(allergy.recordedDate) : null,
        },
        {
          label: 'Clinical status',
          value: clinicalStatus ?? null,
        },
        {
          label: 'Verification',
          value: verificationStatus ?? null,
        },
        {
          label: 'Criticality',
          value: allergy.criticality ?? null,
        },
        {
          label: 'Code',
          value:
            codeValue && codeSystem
              ? `${codeValue} (${codeSystem})`
              : codeValue ?? null,
        },
        {
          label: 'Reactions',
          value: reactions.length > 0 ? <div>{reactions}</div> : null,
        },
      ]}
    />
  )
}
