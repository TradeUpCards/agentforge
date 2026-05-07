import { useQuery } from '@tanstack/react-query'
import { useAuth } from 'react-oidc-context'
import { getPatientList } from '../api/resources/patientList'

export function usePatientList() {
  const auth = useAuth()
  const token = auth.user?.access_token ?? ''
  return useQuery({
    queryKey: ['patientList'],
    queryFn: () => getPatientList(token),
    enabled: !!token,
  })
}
