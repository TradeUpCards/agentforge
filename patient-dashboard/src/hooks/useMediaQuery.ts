import { useEffect, useState } from 'react'

/**
 * Tracks a CSS media query and re-renders on match changes.
 *
 * SSR-safe: returns `false` during initial render when `window` is
 * unavailable; `matchMedia` subscribes on mount and re-syncs the state.
 *
 * Usage:
 *   const isPhonePortrait = useMediaQuery('(max-width: 639px) and (orientation: portrait)')
 *   const isDesktop = useMediaQuery('(min-width: 1024px)')
 */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => {
    if (typeof window === 'undefined') return false
    return window.matchMedia(query).matches
  })
  useEffect(() => {
    const mql = window.matchMedia(query)
    const onChange = () => setMatches(mql.matches)
    setMatches(mql.matches)
    mql.addEventListener('change', onChange)
    return () => mql.removeEventListener('change', onChange)
  }, [query])
  return matches
}
