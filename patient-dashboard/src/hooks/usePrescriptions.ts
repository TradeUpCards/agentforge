import { useQuery } from '@tanstack/react-query'
import { useAuth } from 'react-oidc-context'
import { getPrescriptions } from '../api/resources/prescriptions'

export function usePrescriptions(patientId: string) {
  const auth = useAuth()
  const token = auth.user?.access_token ?? ''
  return useQuery({
    queryKey: ['prescriptions', patientId],
    queryFn: () => getPrescriptions(patientId, token),
    enabled: !!token && !!patientId,
  })
}
