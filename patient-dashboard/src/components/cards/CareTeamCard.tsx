import { useCareTeam } from '../../hooks/useCareTeam'
import { extractBundleResources } from '../../utils/fhirParsers'
import type { FhirCareTeam } from '../../types/fhir'
import { CardShell } from './CardShell'
import { EmptyState } from '../ui/EmptyState'

/**
 * PDF requirement #3e — Care Team card.
 *
 * Backed by FHIR `CareTeam` filtered to active. Each row is a participant
 * (member display + role). Mirrors `templates/patient/card/manage_care_team.html.twig`.
 */
export function CareTeamCard({ patientId }: { patientId: string }) {
  const { data, isLoading, error, refetch } = useCareTeam(patientId)
  const teams = data ? extractBundleResources<FhirCareTeam>(data) : []
  const participants = teams.flatMap((t) => t.participant ?? [])

  return (
    <CardShell
      title="Care Team"
      isLoading={isLoading}
      error={error}
      onRetry={() => void refetch()}
    >
      {!isLoading && participants.length === 0 ? (
        <EmptyState message={data ? 'No Care Team Recorded' : 'Nothing Recorded'} />
      ) : (
        <ul className="divide-y divide-gray-100">
          {participants.map((p, idx) => {
            const name = p.member?.display ?? p.member?.reference ?? 'Unnamed member'
            const role =
              p.role?.[0]?.text ?? p.role?.[0]?.coding?.[0]?.display ?? null
            // Stable key: prefer member.reference, fall back to index. Reference
            // is unique within a CareTeam.
            const key = p.member?.reference ?? `participant-${idx}`
            return (
              <li
                key={key}
                className="py-1.5 flex items-center justify-between gap-2"
              >
                <span className="text-gray-800">{name}</span>
                {role && <span className="text-xs text-gray-500 shrink-0">{role}</span>}
              </li>
            )
          })}
        </ul>
      )}
    </CardShell>
  )
}
