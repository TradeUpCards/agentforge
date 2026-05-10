import { useCareTeam } from '../../hooks/useCareTeam'
import { extractBundleResources } from '../../utils/fhirParsers'
import { formatDate } from '../../utils/formatters'
import { openemrEditLink } from '../../utils/openemrLinks'
import type { FhirCareTeam, FhirCareTeamParticipant } from '../../types/fhir'
import { CardShell } from './CardShell'
import { EmptyState } from '../ui/EmptyState'
import { ExpandableRow, DetailGrid } from '../ui/ExpandableRow'

/**
 * PDF requirement #3e — Care Team card.
 *
 * Backed by FHIR `CareTeam` filtered to active. Each row is a
 * participant (member display + role). Mirrors
 * `templates/patient/card/manage_care_team.html.twig`.
 *
 * Each row expands to show role detail, period (start/end), team
 * status, and the team name if multiple teams are returned.
 */
export function CareTeamCard({ patientId }: { patientId: string }) {
  const { data, isLoading, error, refetch } = useCareTeam(patientId)
  const teams = data ? extractBundleResources<FhirCareTeam>(data) : []

  // Flatten to a list of (participant, parent-team) pairs so the detail
  // panel can show team-level fields (status, name, period) alongside
  // the participant-level ones.
  const rows: { participant: FhirCareTeamParticipant; team: FhirCareTeam }[] = []
  for (const t of teams) {
    for (const p of t.participant ?? []) {
      rows.push({ participant: p, team: t })
    }
  }

  return (
    <CardShell
      title="Care Team"
      isLoading={isLoading}
      error={error}
      onRetry={() => void refetch()}
      editHref={openemrEditLink('care_team')}
    >
      {!isLoading && rows.length === 0 ? (
        <EmptyState message={data ? 'No Care Team Recorded' : 'Nothing Recorded'} />
      ) : (
        <ul className="m-0 p-0 list-none">
          {rows.map((row, idx) => {
            const key =
              row.participant.member?.reference ??
              `${row.team.id ?? 'team'}-participant-${idx}`
            return (
              <ExpandableRow
                key={key}
                summary={<ParticipantSummary participant={row.participant} />}
                details={
                  <ParticipantDetails participant={row.participant} team={row.team} />
                }
              />
            )
          })}
        </ul>
      )}
    </CardShell>
  )
}

function participantName(p: FhirCareTeamParticipant): string {
  return p.member?.display ?? p.member?.reference ?? 'Unnamed member'
}

function participantRole(p: FhirCareTeamParticipant): string | null {
  return (
    p.role?.[0]?.text ??
    p.role?.[0]?.coding?.[0]?.display ??
    null
  )
}

function ParticipantSummary({ participant }: { participant: FhirCareTeamParticipant }) {
  const role = participantRole(participant)
  return (
    <span className="flex items-center justify-between gap-2 min-w-0">
      <span className="text-gray-800 truncate">{participantName(participant)}</span>
      {role && <span className="text-xs text-gray-500 shrink-0">{role}</span>}
    </span>
  )
}

function ParticipantDetails({
  participant,
  team,
}: {
  participant: FhirCareTeamParticipant
  team: FhirCareTeam
}) {
  return (
    <DetailGrid
      rows={[
        { label: 'Role', value: participantRole(participant) },
        { label: 'Member', value: participant.member?.display ?? null },
        {
          label: 'Member ref',
          value: participant.member?.reference ?? null,
        },
        {
          label: 'Period start',
          value: participant.period?.start
            ? formatDate(participant.period.start)
            : null,
        },
        {
          label: 'Period end',
          value: participant.period?.end ? formatDate(participant.period.end) : null,
        },
        { label: 'Team', value: team.name ?? null },
        { label: 'Team status', value: team.status ?? null },
      ]}
    />
  )
}
