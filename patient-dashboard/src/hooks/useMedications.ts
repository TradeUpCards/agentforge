import { useQuery } from '@tanstack/react-query'
import { useAuth } from 'react-oidc-context'
import { getMedications } from '../api/resources/medications'

export function useMedications(patientId: string) {
  const auth = useAuth()
  const token = auth.user?.access_token ?? ''
  return useQuery({
    queryKey: ['medications', patientId],
    queryFn: () => getMedications(patientId, token),
    enabled: !!token && !!patientId,
  })
}
