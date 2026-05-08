import { useEffect, useRef, useState } from 'react'
import { openemrHome } from '../../utils/openemrLinks'

const OPENEMR_BASE =
  import.meta.env.VITE_OPENEMR_BASE_URL ?? 'https://localhost:9300'

const OPENEMR_SITE = import.meta.env.VITE_OPENEMR_SITE ?? 'default'

/**
 * Floating Co-Pilot button + slide-in drawer hosting the legacy
 * Clinical Co-Pilot chat panel via iframe (Path A from the integration
 * plan at ~/.claude/plans/lovely-purring-boot.md).
 *
 * Why iframe (Path A):
 *   - Cheapest viable path. Reuses the existing `chat-panel.php` UI
 *     unchanged (markdown rendering, citation badges, retrieved-record
 *     popover, latency capture, refusal handling, persona wiring).
 *   - The native React reimplementation (Path C) is documented as the
 *     long-term target and is planned out at the path above; it
 *     requires changes in Aria's territory (`agent/main.py` dual-auth)
 *     and is deferred.
 *
 * Auth:
 *   - The iframe carries the user's existing OpenEMR PHP session cookie
 *     (same browser session, same origin via the Vite proxy in dev or
 *     same host in co-located prod). If the PHP session has expired,
 *     OpenEMR will render its login form inside the iframe — the user
 *     can then log in there or use the "Open in OpenEMR" escape hatch
 *     in the drawer header to re-authenticate in a full tab.
 *
 * Responsive shape (per the matrix in PATIENT_DASHBOARD_MIGRATION.md §17):
 *   - Phone (any orientation): full-screen overlay
 *   - Tablet portrait (md): right-edge drawer 400 px wide -> vertical split
 *   - Tablet landscape (lg): right-edge drawer 480 px -> vertical split
 *   - Desktop (xl): right-edge drawer 520 px -> vertical split
 *
 * Closed: only the floating button is visible (bottom-right). Open: the
 * floating button hides, drawer slides in.
 */
interface CoPilotDrawerProps {
  patientId: string
}

/**
 * Tracks a media query and re-renders on change. SSR-safe (returns
 * false when window is unavailable; matchMedia subscribes on mount).
 */
function useMediaQuery(query: string): boolean {
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

export function CoPilotDrawer({ patientId }: CoPilotDrawerProps) {
  const [open, setOpen] = useState(false)
  const drawerRef = useRef<HTMLDivElement>(null)
  // Phone portrait gets a bottom-sheet layout instead of a side drawer
  // so the patient's chart stays visible in the top half of the
  // viewport. JS-driven so the className can switch wholesale rather
  // than fighting Tailwind variant ordering between right-edge drawer
  // and bottom sheet positioning rules.
  const isPhonePortrait = useMediaQuery(
    '(max-width: 639px) and (orientation: portrait)',
  )
  // Lazy-mount the iframe on first open and keep it mounted thereafter
  // so the legacy chat-panel.php only loads when the user actually
  // uses Co-Pilot. Subsequent opens reuse the same iframe / same chat
  // history without a fresh page load.
  const [hasBeenOpened, setHasBeenOpened] = useState(false)
  useEffect(() => {
    if (open) setHasBeenOpened(true)
  }, [open])

  // Phone-portrait bottom-sheet has two heights — compact (40 vh, the
  // default) and expanded (90 vh, double-tap the header to toggle).
  // Resets to compact when the drawer closes so reopening always starts
  // small.
  const [bottomSheetExpanded, setBottomSheetExpanded] = useState(false)
  useEffect(() => {
    if (!open) setBottomSheetExpanded(false)
  }, [open])

  // Body sets a second attribute when the bottom sheet is expanded,
  // so the CSS in index.css can collapse the chart's max-height
  // accordingly. Only meaningful on phone portrait.
  useEffect(() => {
    if (open && bottomSheetExpanded && isPhonePortrait) {
      document.body.setAttribute('data-copilot-expanded', '')
      return () => document.body.removeAttribute('data-copilot-expanded')
    }
    return undefined
  }, [open, bottomSheetExpanded, isPhonePortrait])

  // Double-tap detection on the header → toggle bottom-sheet height.
  // 350 ms threshold; ignores clicks on real buttons/links inside the
  // header so the X and external-link controls work normally on a
  // single tap.
  const lastTapRef = useRef(0)
  const handleHeaderTap = (e: React.MouseEvent<HTMLElement>) => {
    if (!isPhonePortrait) return
    if ((e.target as HTMLElement).closest('button, a')) return
    const now = Date.now()
    if (now - lastTapRef.current < 350) {
      setBottomSheetExpanded((s) => !s)
      lastTapRef.current = 0
    } else {
      lastTapRef.current = now
    }
  }

  // Push-content mode: when the drawer is open, set a `data-copilot-open`
  // attribute on <body>. CSS rules in `index.css` respond by padding
  // body to make room for the drawer (right padding on tablet/desktop;
  // bottom padding on phone portrait). The chart shrinks to fit; both
  // chart and Co-Pilot remain scrollable / interactive at the same time.
  // No modal, no backdrop, no scroll lock.
  useEffect(() => {
    if (!open) return
    document.body.setAttribute('data-copilot-open', '')
    return () => document.body.removeAttribute('data-copilot-open')
  }, [open])

  // Close on Escape.
  useEffect(() => {
    if (!open) return
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open])

  const iframeSrc = `${OPENEMR_BASE}/interface/modules/custom_modules/oe-module-clinical-copilot/public/chat-panel.php?site=${encodeURIComponent(OPENEMR_SITE)}&pid=${encodeURIComponent(patientId)}`

  return (
    <>
      {/* Floating launch button — primary FAB.
          Press feedback: bg-blue-900 active state + slight scale-down
          (motion-safe so reduce-motion users get the color change but
          not the geometry change). transition-all gives smooth state
          changes between hover/active/focus. */}
      {!open && (
        <button
          type="button"
          onClick={() => setOpen(true)}
          aria-label="Open Co-Pilot"
          className="
            fixed bottom-4 right-4 z-40
            inline-flex items-center gap-2
            px-4 py-3 min-h-11
            rounded-full shadow-lg
            bg-blue-700 hover:bg-blue-800 active:bg-blue-900 text-white
            font-medium text-sm
            transition-colors duration-150
            motion-safe:transition-all motion-safe:active:scale-95
            focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600
          "
        >
          <SparklesIcon />
          <span>Co-Pilot</span>
        </button>
      )}

      {/* No backdrop. Push-content mode (see body padding rules in
          index.css) puts the chart alongside the drawer rather than
          behind it, so there's no underlying surface to "tap to
          dismiss." Close affordances are: the X button in the drawer
          header, the Escape key, and (on phone portrait bottom sheet)
          a swipe-down would be a future addition. */}

      {/* Drawer — always rendered so slide animations fire on open and
          close. Layout switches between phone portrait (bottom sheet,
          50% height, slide up from bottom) and everything else (right-
          edge drawer, slide in from right).
            Phone portrait      : bottom sheet, 50 vh tall
            Phone landscape (sm): right drawer 384 px
            Tablet portrait (md): right drawer 400 px
            Tablet landscape lg : right drawer 480 px
            Desktop (xl)        : right drawer 520 px

          Iframe is lazy-mounted on first open and kept thereafter, so
          the legacy chat-panel.php only loads when the user actually
          uses Co-Pilot at least once. */}
      <aside
        ref={drawerRef}
        role="dialog"
        aria-modal={open}
        aria-hidden={!open}
        aria-label="Clinical Co-Pilot"
        className={`
          fixed z-50 bg-white shadow-2xl flex flex-col overscroll-contain
          motion-safe:transition-transform motion-safe:duration-300 motion-safe:ease-out
          ${
            isPhonePortrait
              ? `inset-x-0 bottom-0 border-t border-gray-200 rounded-t-2xl ${
                  bottomSheetExpanded ? 'h-[90vh]' : 'h-[40vh]'
                }`
              : 'inset-y-0 right-0 w-96 md:w-[400px] lg:w-[480px] xl:w-[520px] border-l border-gray-200'
          }
          ${
            open
              ? 'translate-x-0 translate-y-0'
              : isPhonePortrait
                ? 'translate-y-full pointer-events-none'
                : 'translate-x-full pointer-events-none'
          }
        `}
      >
        <header
          onClick={handleHeaderTap}
          className={`
            relative flex items-center justify-between px-3 border-b border-gray-200 bg-gray-50
            ${isPhonePortrait ? 'pt-4 pb-2 cursor-pointer select-none' : 'py-2'}
          `}
        >
          {/* Drag-handle hint on bottom-sheet variant — pure decoration,
              cues the "swipe down to close" affordance even though
              we don't currently implement gesture-driven dismiss. */}
          {isPhonePortrait && (
            <span
              aria-hidden="true"
              className="absolute left-1/2 -translate-x-1/2 top-1.5 w-10 h-1 rounded-full bg-gray-300"
            />
          )}
          <h2 className="text-sm font-semibold text-gray-800 m-0 flex items-center gap-2">
            <SparklesIcon />
            <span>Co-Pilot</span>
          </h2>
          <div className="flex items-center gap-1">
            <a
              href={openemrHome()}
              target="_blank"
              rel="noopener noreferrer"
              title="Open in OpenEMR (escape hatch if session expired)"
              aria-label="Open in OpenEMR"
              className="
                inline-flex items-center justify-center
                p-2 min-h-11 min-w-11
                text-gray-600 hover:text-blue-700
                rounded
                focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600
              "
            >
              <ExternalIcon />
            </a>
            <button
              type="button"
              onClick={() => setOpen(false)}
              aria-label="Close Co-Pilot"
              className="
                inline-flex items-center justify-center
                p-2 min-h-11 min-w-11
                text-gray-600 hover:text-gray-900
                rounded
                focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600
              "
            >
              <CloseIcon />
            </button>
          </div>
        </header>
        {hasBeenOpened && (
          <iframe
            src={iframeSrc}
            title="Clinical Co-Pilot chat"
            className="flex-1 w-full border-0 overscroll-contain"
            // sandbox: keep same-origin so the chat panel can read its cookie.
            // Note: the chat panel needs scripts to run (chat-panel.js) so we
            // do NOT add a `sandbox` attribute that would strip those.
            // overscroll-contain: when the chat hits its scroll bounds, the
            // touch event stays inside the iframe instead of chaining up to
            // body and scrolling the chart underneath the bottom sheet.
          />
        )}
      </aside>
    </>
  )
}

function SparklesIcon() {
  return (
    <svg viewBox="0 0 16 16" width="16" height="16" fill="currentColor" aria-hidden="true">
      <path d="M7.657 6.247c.11-.33.576-.33.686 0l.645 1.937a2.89 2.89 0 0 0 1.829 1.828l1.936.645c.33.11.33.576 0 .686l-1.937.645a2.89 2.89 0 0 0-1.828 1.829l-.645 1.936a.361.361 0 0 1-.686 0l-.645-1.937a2.89 2.89 0 0 0-1.828-1.828l-1.937-.645a.361.361 0 0 1 0-.686l1.937-.645a2.89 2.89 0 0 0 1.828-1.828zM3.794 1.148a.217.217 0 0 1 .412 0l.387 1.162c.173.518.579.924 1.097 1.097l1.162.387a.217.217 0 0 1 0 .412l-1.162.387A1.734 1.734 0 0 0 4.593 5.69l-.387 1.162a.217.217 0 0 1-.412 0L3.407 5.69A1.734 1.734 0 0 0 2.31 4.593l-1.162-.387a.217.217 0 0 1 0-.412l1.162-.387A1.734 1.734 0 0 0 3.407 2.31zM10.863.099a.145.145 0 0 1 .274 0l.258.774c.115.346.386.617.732.732l.774.258a.145.145 0 0 1 0 .274l-.774.258a1.16 1.16 0 0 0-.732.732l-.258.774a.145.145 0 0 1-.274 0l-.258-.774a1.16 1.16 0 0 0-.732-.732L9.1 2.137a.145.145 0 0 1 0-.274l.774-.258c.346-.115.617-.386.732-.732Z" />
    </svg>
  )
}

function ExternalIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor" aria-hidden="true">
      <path fillRule="evenodd" d="M8.636 3.5a.5.5 0 0 0-.5-.5H1.5A1.5 1.5 0 0 0 0 4.5v10A1.5 1.5 0 0 0 1.5 16h10a1.5 1.5 0 0 0 1.5-1.5V7.864a.5.5 0 0 0-1 0V14.5a.5.5 0 0 1-.5.5h-10a.5.5 0 0 1-.5-.5v-10a.5.5 0 0 1 .5-.5h6.636a.5.5 0 0 0 .5-.5" />
      <path fillRule="evenodd" d="M16 .5a.5.5 0 0 0-.5-.5h-5a.5.5 0 0 0 0 1h3.793L6.146 9.146a.5.5 0 1 0 .708.708L15 1.707V5.5a.5.5 0 0 0 1 0z" />
    </svg>
  )
}

function CloseIcon() {
  return (
    <svg viewBox="0 0 16 16" width="16" height="16" fill="currentColor" aria-hidden="true">
      <path d="M2.146 2.854a.5.5 0 1 1 .708-.708L8 7.293l5.146-5.147a.5.5 0 0 1 .708.708L8.707 8l5.147 5.146a.5.5 0 0 1-.708.708L8 8.707l-5.146 5.147a.5.5 0 0 1-.708-.708L7.293 8z" />
    </svg>
  )
}
