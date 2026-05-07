import { useQuery } from '@tanstack/react-query'
import { useAuth } from 'react-oidc-context'
import { getPatient } from '../api/resources/patient'

export function usePatient(patientId: string) {
  const auth = useAuth()
  const token = auth.user?.access_token ?? ''
  return useQuery({
    queryKey: ['patient', patientId],
    queryFn: () => getPatient(patientId, token),
    enabled: !!token && !!patientId,
  })
}
