import { useQuery } from '@tanstack/react-query'
import { useAuth } from 'react-oidc-context'
import { getEncounters } from '../api/resources/encounters'

export function useEncounters(patientId: string) {
  const auth = useAuth()
  const token = auth.user?.access_token ?? ''
  return useQuery({
    queryKey: ['encounters', patientId],
    queryFn: () => getEncounters(patientId, token),
    enabled: !!token && !!patientId,
  })
}
