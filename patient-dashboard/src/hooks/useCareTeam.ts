import { useQuery } from '@tanstack/react-query'
import { useAuth } from 'react-oidc-context'
import { getCareTeam } from '../api/resources/careTeam'

export function useCareTeam(patientId: string) {
  const auth = useAuth()
  const token = auth.user?.access_token ?? ''
  return useQuery({
    queryKey: ['careTeam', patientId],
    queryFn: () => getCareTeam(patientId, token),
    enabled: !!token && !!patientId,
  })
}
