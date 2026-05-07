import { usePrescriptions } from '../../hooks/usePrescriptions'
import { extractBundleResources } from '../../utils/fhirParsers'
import { formatDate, formatQuantity } from '../../utils/formatters'
import type { FhirMedicationDispense } from '../../types/fhir'
import { CardShell } from './CardShell'
import { EmptyState } from '../ui/EmptyState'

/**
 * PDF requirement #3d — Prescriptions card.
 *
 * Backed by FHIR `MedicationDispense`, sorted newest-first.
 * Mirrors `templates/patient/card/rx.html.twig`.
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
    >
      {!isLoading && dispenses.length === 0 ? (
        <EmptyState message={data ? 'No Prescriptions Recorded' : 'Nothing Recorded'} />
      ) : (
        <ul className="divide-y divide-gray-100">
          {dispenses.map((d) => {
            const name =
              d.medicationCodeableConcept?.text ??
              d.medicationCodeableConcept?.coding?.[0]?.display ??
              d.medicationReference?.display ??
              'Unspecified prescription'
            const qty = formatQuantity(d.quantity?.value, d.quantity?.unit)
            const handed = d.whenHandedOver ?? d.whenPrepared
            return (
              <li
                key={d.id ?? name}
                className="py-1.5 flex items-center justify-between gap-2"
              >
                <div className="flex-1">
                  <p className="text-gray-800">{name}</p>
                  {qty && qty !== '—' && (
                    <p className="text-xs text-gray-500 mt-0.5">Qty: {qty}</p>
                  )}
                </div>
                {handed && (
                  <span className="text-xs text-gray-500 shrink-0" title="Handed over">
                    {formatDate(handed)}
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
