import { useConditions } from '../../hooks/useConditions'
import { extractBundleResources } from '../../utils/fhirParsers'
import { formatDate } from '../../utils/formatters'
import { openemrEditLink } from '../../utils/openemrLinks'
import type { FhirCondition } from '../../types/fhir'
import { CardShell } from './CardShell'
import { EmptyState } from '../ui/EmptyState'
import { ExpandableRow, DetailGrid } from '../ui/ExpandableRow'

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
 *
 * Each row is an inline-accordion `ExpandableRow` revealing onset
 * date, recorded date, clinical status, verification status, and
 * SNOMED/ICD code+system.
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
      editHref={openemrEditLink('medical_problem')}
    >
      {!isLoading && conditions.length === 0 ? (
        <EmptyState message={data ? 'No Active Problems' : 'Nothing Recorded'} />
      ) : (
        <ul className="m-0 p-0 list-none">
          {conditions.map((c) => (
            <ExpandableRow
              key={c.id ?? `${c.code?.text ?? 'condition'}-${c.recordedDate ?? ''}`}
              summary={<ConditionSummary condition={c} />}
              details={<ConditionDetails condition={c} />}
            />
          ))}
        </ul>
      )}
    </CardShell>
  )
}

function ConditionSummary({ condition }: { condition: FhirCondition }) {
  const name =
    condition.code?.text ??
    condition.code?.coding?.[0]?.display ??
    'Unspecified condition'
  const onset = condition.onsetDateTime ?? condition.recordedDate
  return (
    <span className="flex items-center justify-between gap-2 min-w-0">
      <span className="text-gray-800 truncate">{name}</span>
      {onset && (
        <span className="text-xs text-gray-500 shrink-0" title="Onset">
          {formatDate(onset)}
        </span>
      )}
    </span>
  )
}

function ConditionDetails({ condition }: { condition: FhirCondition }) {
  const code = condition.code?.coding?.[0]
  return (
    <DetailGrid
      rows={[
        {
          label: 'Onset',
          value: condition.onsetDateTime ? formatDate(condition.onsetDateTime) : null,
        },
        {
          label: 'Recorded',
          value: condition.recordedDate ? formatDate(condition.recordedDate) : null,
        },
        {
          label: 'Clinical status',
          value: condition.clinicalStatus?.coding?.[0]?.code ?? null,
        },
        {
          label: 'Verification',
          value: condition.verificationStatus?.coding?.[0]?.code ?? null,
        },
        {
          label: 'Code',
          value:
            code?.code && code.system
              ? `${code.code} (${code.system})`
              : code?.code ?? null,
        },
      ]}
    />
  )
}
