import { useMedications } from '../../hooks/useMedications'
import { extractBundleResources } from '../../utils/fhirParsers'
import { formatDate } from '../../utils/formatters'
import { openemrEditLink } from '../../utils/openemrLinks'
import type { FhirMedicationRequest } from '../../types/fhir'
import { CardShell } from './CardShell'
import { EmptyState } from '../ui/EmptyState'
import { ExpandableRow, DetailGrid } from '../ui/ExpandableRow'

/**
 * PDF requirement #3c — Medications card.
 *
 * Backed by FHIR `MedicationRequest` filtered to active orders.
 * Mirrors `templates/patient/card/medication.html.twig`.
 *
 * Each row is a click-to-expand `ExpandableRow` revealing dosage
 * instruction, route, status, intent, authored date, and the
 * RxNorm/NDC code+system if present.
 */
export function MedicationsCard({ patientId }: { patientId: string }) {
  const { data, isLoading, error, refetch } = useMedications(patientId)
  const meds = data ? extractBundleResources<FhirMedicationRequest>(data) : []

  return (
    <CardShell
      title="Medications"
      isLoading={isLoading}
      error={error}
      onRetry={() => void refetch()}
      editHref={openemrEditLink('medication')}
    >
      {!isLoading && meds.length === 0 ? (
        <EmptyState message={data ? 'No Active Medications' : 'Nothing Recorded'} />
      ) : (
        <ul className="m-0 p-0 list-none">
          {meds.map((m) => (
            <ExpandableRow
              key={m.id ?? `${medName(m)}-${m.authoredOn ?? ''}`}
              summary={<MedicationSummary med={m} />}
              details={<MedicationDetails med={m} />}
            />
          ))}
        </ul>
      )}
    </CardShell>
  )
}

function medName(m: FhirMedicationRequest): string {
  return (
    m.medicationCodeableConcept?.text ??
    m.medicationCodeableConcept?.coding?.[0]?.display ??
    m.medicationReference?.display ??
    'Unspecified medication'
  )
}

function MedicationSummary({ med }: { med: FhirMedicationRequest }) {
  const dosage = med.dosageInstruction?.[0]?.text
  return (
    <span className="block min-w-0">
      <span className="text-gray-800 block truncate">{medName(med)}</span>
      {dosage && (
        <span className="text-xs text-gray-500 block truncate mt-0.5">{dosage}</span>
      )}
    </span>
  )
}

function MedicationDetails({ med }: { med: FhirMedicationRequest }) {
  const code = med.medicationCodeableConcept?.coding?.[0]
  const dosage = med.dosageInstruction?.[0]
  return (
    <DetailGrid
      rows={[
        { label: 'Dosage', value: dosage?.text ?? null },
        {
          label: 'Route',
          value:
            dosage?.route?.text ?? dosage?.route?.coding?.[0]?.display ?? null,
        },
        { label: 'Status', value: med.status ?? null },
        { label: 'Intent', value: med.intent ?? null },
        {
          label: 'Authored',
          value: med.authoredOn ? formatDate(med.authoredOn) : null,
        },
        {
          label: 'Requester',
          value: med.requester?.display ?? null,
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
