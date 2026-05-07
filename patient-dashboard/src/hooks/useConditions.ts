import { useQuery } from '@tanstack/react-query'
import { useAuth } from 'react-oidc-context'
import { getConditions } from '../api/resources/conditions'

export function useConditions(patientId: string) {
  const auth = useAuth()
  const token = auth.user?.access_token ?? ''
  return useQuery({
    queryKey: ['conditions', patientId],
    queryFn: () => getConditions(patientId, token),
    enabled: !!token && !!patientId,
  })
}
