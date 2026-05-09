import { useEffect, useRef, useState } from 'react'

/**
 * Tracks the user's most recent scroll direction and returns whether
 * a "scrolling away" piece of UI (e.g. the auto-hide MainNav) should
 * currently be hidden.
 *
 * Behavior:
 *   - At the very top of the scroll area (within `topThreshold`), always
 *     show. Avoids flicker when the user bounces around the top.
 *   - Scrolling down past `delta` pixels in one step → hide.
 *   - Scrolling up by `delta` pixels in one step → show.
 *   - Tiny jitter below `delta` does nothing — debounces noisy scroll
 *     events without delay.
 *
 * Listens on BOTH the window AND `#root`, because our scroll container
 * varies by viewport: phone-portrait + Co-Pilot open uses `#root` as
 * the scroll container (see `index.css`); everywhere else, the document
 * scrolls. Both listeners feed the same handler; whichever fires per
 * gesture wins.
 *
 * Returns `true` when the auto-hide UI should currently be hidden.
 */
export function useScrollDirection({
  topThreshold = 50,
  delta = 5,
}: { topThreshold?: number; delta?: number } = {}): boolean {
  const [hidden, setHidden] = useState(false)
  const lastYRef = useRef(0)

  useEffect(() => {
    const update = (currentY: number) => {
      const dy = currentY - lastYRef.current
      if (currentY < topThreshold) {
        setHidden(false)
      } else if (dy > delta) {
        setHidden(true)
      } else if (dy < -delta) {
        setHidden(false)
      }
      lastYRef.current = currentY
    }

    const onWindowScroll = () => update(window.scrollY)
    const root = document.getElementById('root')
    const onRootScroll = () => {
      if (root) update(root.scrollTop)
    }

    window.addEventListener('scroll', onWindowScroll, { passive: true })
    root?.addEventListener('scroll', onRootScroll, { passive: true })

    return () => {
      window.removeEventListener('scroll', onWindowScroll)
      root?.removeEventListener('scroll', onRootScroll)
    }
  }, [topThreshold, delta])

  return hidden
}
