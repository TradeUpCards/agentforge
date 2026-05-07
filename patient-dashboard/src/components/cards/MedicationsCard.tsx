import { useMedications } from '../../hooks/useMedications'
import { extractBundleResources } from '../../utils/fhirParsers'
import type { FhirMedicationRequest } from '../../types/fhir'
import { CardShell } from './CardShell'
import { EmptyState } from '../ui/EmptyState'

/**
 * PDF requirement #3c — Medications card.
 *
 * Backed by FHIR `MedicationRequest` filtered to active orders.
 * Mirrors `templates/patient/card/medication.html.twig`.
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
    >
      {!isLoading && meds.length === 0 ? (
        <EmptyState message={data ? 'No Active Medications' : 'Nothing Recorded'} />
      ) : (
        <ul className="divide-y divide-gray-100">
          {meds.map((m) => {
            const name =
              m.medicationCodeableConcept?.text ??
              m.medicationCodeableConcept?.coding?.[0]?.display ??
              m.medicationReference?.display ??
              'Unspecified medication'
            const dosage = m.dosageInstruction?.[0]?.text
            return (
              <li key={m.id ?? name} className="py-1.5">
                <p className="text-gray-800">{name}</p>
                {dosage && <p className="text-xs text-gray-500 mt-0.5">{dosage}</p>}
              </li>
            )
          })}
        </ul>
      )}
    </CardShell>
  )
}
