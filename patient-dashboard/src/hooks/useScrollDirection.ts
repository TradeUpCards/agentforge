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
 *   - **Cooldown**: after each visibility change, ignore scroll events
 *     for `cooldownMs`. Without this, the layout shift from MainNav's
 *     collapse animation causes the browser to adjust scrollY, the
 *     hook reads that as new "user scroll," and the hidden state
 *     oscillates rapidly until inertia decays. The cooldown matches
 *     the animation duration so the hook resumes once the layout has
 *     settled. Same fix protects against layout shifts from sibling
 *     components (PatientHeader's drawer toggle).
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
  cooldownMs = 250,
}: {
  topThreshold?: number
  delta?: number
  cooldownMs?: number
} = {}): boolean {
  const [hidden, setHidden] = useState(false)
  // Single ref keeps cooldown + lastY + last committed state in sync with
  // the listener closure. State setters only fire when the value actually
  // changes, and only outside the cooldown window.
  const stateRef = useRef({
    lastY: 0,
    hidden: false,
    ignoreUntil: 0,
  })

  useEffect(() => {
    const update = (currentY: number) => {
      const now = Date.now()

      // During cooldown, refresh the lastY reference (so we don't have a
      // huge delta when cooldown ends) but make no decision changes.
      if (now < stateRef.current.ignoreUntil) {
        stateRef.current.lastY = currentY
        return
      }

      const dy = currentY - stateRef.current.lastY
      let nextHidden = stateRef.current.hidden

      if (currentY < topThreshold) {
        nextHidden = false
      } else if (dy > delta) {
        nextHidden = true
      } else if (dy < -delta) {
        nextHidden = false
      }

      if (nextHidden !== stateRef.current.hidden) {
        stateRef.current.hidden = nextHidden
        stateRef.current.ignoreUntil = now + cooldownMs
        setHidden(nextHidden)
      }
      stateRef.current.lastY = currentY
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
  }, [topThreshold, delta, cooldownMs])

  return hidden
}
