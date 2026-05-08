import { useQuery } from '@tanstack/react-query'
import { useAuth } from 'react-oidc-context'
import { getDocuments } from '../api/resources/documents'

/**
 * Shared hook for the Documents card and the /documents page. The card
 * passes `count=5`; the page passes `count=50`. Distinct query keys
 * keep their caches separate so a card render doesn't preempt a page
 * fetch and vice versa.
 */
export function useDocuments(patientId: string, count = 50) {
  const auth = useAuth()
  const token = auth.user?.access_token ?? ''
  return useQuery({
    queryKey: ['documents', patientId, count],
    queryFn: () => getDocuments(patientId, token, count),
    enabled: !!token && !!patientId,
  })
}
