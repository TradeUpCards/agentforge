import { useQuery } from '@tanstack/react-query'
import { useAuth } from 'react-oidc-context'
import { getAllergies } from '../api/resources/allergies'

export function useAllergies(patientId: string) {
  const auth = useAuth()
  const token = auth.user?.access_token ?? ''
  return useQuery({
    queryKey: ['allergies', patientId],
    queryFn: () => getAllergies(patientId, token),
    enabled: !!token && !!patientId,
  })
}
