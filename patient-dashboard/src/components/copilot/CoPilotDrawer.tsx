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

export function CoPilotDrawer({ patientId }: CoPilotDrawerProps) {
  const [open, setOpen] = useState(false)
  const drawerRef = useRef<HTMLDivElement>(null)

  // Lock body scroll when drawer is open on phone (full-screen overlay).
  useEffect(() => {
    if (!open) return
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = previous
    }
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
      {/* Floating launch button — sized as a primary FAB on phone (56 px
          tall, generous padding, lifted further from the corner to clear
          iOS home-indicator + give thumb-reach room). On tablet+/desktop
          where the cursor is precise, the button shrinks back to a 44 px
          pill so it doesn't dominate. */}
      {!open && (
        <button
          type="button"
          onClick={() => setOpen(true)}
          aria-label="Open Co-Pilot"
          className="
            fixed z-40
            right-5 bottom-6 md:right-4 md:bottom-4
            inline-flex items-center gap-2
            px-5 py-4 md:px-4 md:py-3
            min-h-14 md:min-h-11
            rounded-full shadow-lg
            bg-blue-700 hover:bg-blue-800 active:bg-blue-900 text-white
            font-medium text-base md:text-sm
            focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600
          "
        >
          <SparklesIcon />
          <span>Co-Pilot</span>
        </button>
      )}

      {/* Backdrop on phone (helps the eye see the overlay layer) */}
      {open && (
        <div
          className="fixed inset-0 z-40 bg-black/30 lg:hidden"
          onClick={() => setOpen(false)}
          aria-hidden="true"
        />
      )}

      {/* Drawer */}
      {open && (
        <aside
          ref={drawerRef}
          role="dialog"
          aria-modal="true"
          aria-label="Clinical Co-Pilot"
          className="
            fixed inset-y-0 right-0 z-50
            w-full md:w-[400px] lg:w-[480px] xl:w-[520px]
            bg-white border-l border-gray-200 shadow-2xl
            flex flex-col
          "
        >
          <header className="flex items-center justify-between px-3 py-2 border-b border-gray-200 bg-gray-50">
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
          <iframe
            src={iframeSrc}
            title="Clinical Co-Pilot chat"
            className="flex-1 w-full border-0"
            // sandbox: keep same-origin so the chat panel can read its cookie.
            // Note: the chat panel needs scripts to run (chat-panel.js) so we
            // do NOT add a `sandbox` attribute that would strip those.
          />
        </aside>
      )}
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
