import { useConditions } from '../../hooks/useConditions'
import { extractBundleResources } from '../../utils/fhirParsers'
import { formatDate } from '../../utils/formatters'
import type { FhirCondition } from '../../types/fhir'
import { CardShell } from './CardShell'
import { EmptyState } from '../ui/EmptyState'

/**
 * PDF requirement #3b — Problem List card.
 *
 * Backed by FHIR `Condition` filtered to active problem-list-item entries.
 * Mirrors `templates/patient/card/medical_problems.html.twig`.
 *
 * Filtering is client-side because OpenEMR's FHIR server returns 500 on
 * the combined `category=problem-list-item&clinical-status=active`
 * query. See `conditions.ts` header note. We accept any condition that
 * either (a) carries `problem-list-item` in its category coding, or
 * (b) has no category at all (some OpenEMR installs don't set it).
 * Inactive / resolved conditions are dropped.
 */
function isProblemListItem(c: FhirCondition): boolean {
  const categories = c.category ?? []
  if (categories.length === 0) return true // no category set → assume problem-list
  return categories.some((cat) =>
    (cat.coding ?? []).some((code) => code.code === 'problem-list-item'),
  )
}

function isActive(c: FhirCondition): boolean {
  const status = c.clinicalStatus?.coding?.[0]?.code
  // FHIR R4 active statuses: active, recurrence, relapse. Treat missing as active.
  if (!status) return true
  return ['active', 'recurrence', 'relapse'].includes(status)
}

export function ProblemsCard({ patientId }: { patientId: string }) {
  const { data, isLoading, error, refetch } = useConditions(patientId)
  const all = data ? extractBundleResources<FhirCondition>(data) : []
  const conditions = all.filter((c) => isProblemListItem(c) && isActive(c))

  return (
    <CardShell
      title="Medical Problems"
      isLoading={isLoading}
      error={error}
      onRetry={() => void refetch()}
    >
      {!isLoading && conditions.length === 0 ? (
        <EmptyState message={data ? 'No Active Problems' : 'Nothing Recorded'} />
      ) : (
        <ul className="divide-y divide-gray-100">
          {conditions.map((c) => {
            const name =
              c.code?.text ?? c.code?.coding?.[0]?.display ?? 'Unspecified condition'
            const onset = c.onsetDateTime ?? c.recordedDate
            return (
              <li
                key={c.id ?? name}
                className="py-1.5 flex items-center justify-between gap-2"
              >
                <span className="text-gray-800">{name}</span>
                {onset && (
                  <span className="text-xs text-gray-500 shrink-0" title="Onset">
                    {formatDate(onset)}
                  </span>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </CardShell>
  )
}
