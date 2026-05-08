import { usePrescriptions } from '../../hooks/usePrescriptions'
import { extractBundleResources } from '../../utils/fhirParsers'
import { formatDate, formatQuantity } from '../../utils/formatters'
import { openemrEditLink } from '../../utils/openemrLinks'
import type { FhirMedicationDispense } from '../../types/fhir'
import { CardShell } from './CardShell'
import { EmptyState } from '../ui/EmptyState'
import { ExpandableRow, DetailGrid } from '../ui/ExpandableRow'

/**
 * PDF requirement #3d — Prescriptions card.
 *
 * Backed by FHIR `MedicationDispense`, sorted newest-first.
 * Mirrors `templates/patient/card/rx.html.twig`.
 *
 * Each row expands to show quantity, days supply, when handed over,
 * when prepared, status, and the RxNorm/NDC code+system if present.
 */
export function PrescriptionsCard({ patientId }: { patientId: string }) {
  const { data, isLoading, error, refetch } = usePrescriptions(patientId)
  const dispenses = data ? extractBundleResources<FhirMedicationDispense>(data) : []

  return (
    <CardShell
      title="Prescriptions"
      isLoading={isLoading}
      error={error}
      onRetry={() => void refetch()}
      editHref={openemrEditLink('prescription')}
    >
      {!isLoading && dispenses.length === 0 ? (
        <EmptyState message={data ? 'No Prescriptions Recorded' : 'Nothing Recorded'} />
      ) : (
        <ul className="m-0 p-0 list-none">
          {dispenses.map((d) => (
            <ExpandableRow
              key={d.id ?? `${rxName(d)}-${d.whenHandedOver ?? ''}`}
              summary={<PrescriptionSummary dispense={d} />}
              details={<PrescriptionDetails dispense={d} />}
            />
          ))}
        </ul>
      )}
    </CardShell>
  )
}

function rxName(d: FhirMedicationDispense): string {
  return (
    d.medicationCodeableConcept?.text ??
    d.medicationCodeableConcept?.coding?.[0]?.display ??
    d.medicationReference?.display ??
    'Unspecified prescription'
  )
}

function PrescriptionSummary({ dispense }: { dispense: FhirMedicationDispense }) {
  const qty = formatQuantity(dispense.quantity?.value, dispense.quantity?.unit)
  const handed = dispense.whenHandedOver ?? dispense.whenPrepared
  return (
    <span className="flex items-start justify-between gap-2 min-w-0">
      <span className="flex-1 min-w-0">
        <span className="text-gray-800 block truncate">{rxName(dispense)}</span>
        {qty && qty !== '—' && (
          <span className="text-xs text-gray-500 block mt-0.5">Qty: {qty}</span>
        )}
      </span>
      {handed && (
        <span className="text-xs text-gray-500 shrink-0" title="Handed over">
          {formatDate(handed)}
        </span>
      )}
    </span>
  )
}

function PrescriptionDetails({ dispense }: { dispense: FhirMedicationDispense }) {
  const code = dispense.medicationCodeableConcept?.coding?.[0]
  return (
    <DetailGrid
      rows={[
        {
          label: 'Quantity',
          value: formatQuantity(dispense.quantity?.value, dispense.quantity?.unit),
        },
        {
          label: 'Days supply',
          value: formatQuantity(
            dispense.daysSupply?.value,
            dispense.daysSupply?.unit ?? 'days',
          ),
        },
        {
          label: 'Handed over',
          value: dispense.whenHandedOver ? formatDate(dispense.whenHandedOver) : null,
        },
        {
          label: 'Prepared',
          value: dispense.whenPrepared ? formatDate(dispense.whenPrepared) : null,
        },
        { label: 'Status', value: dispense.status ?? null },
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
