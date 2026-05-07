import { Spinner } from '../ui/Spinner'
import { ErrorRetry } from '../ui/ErrorRetry'

interface CardShellProps {
  title: string
  isLoading: boolean
  error?: Error | null
  onRetry?: () => void
  children: React.ReactNode
}

/**
 * Shared card shell — visually mirrors the legacy OpenEMR card chrome
 * (`templates/patient/card/card_base.html.twig`):
 *   - Title in OpenEMR's blue link color (~Tailwind blue-700).
 *   - Small drag-handle glyph next to title (decorative — legacy uses
 *     it to signal the card is sortable; we render it for visual
 *     familiarity but do not implement drag).
 *   - Edit-pencil icon at the right of the title bar (decorative for
 *     the same reason — opens the editor in legacy; not implemented).
 *   - No rounded corners, no shadow, white background — matches the
 *     plain bordered look of the legacy dashboard.
 *
 * Owns the loading / error / content state machine described in §10:
 *   - loading → spinner in the header
 *   - error → ErrorRetry inside the body, never collapses to a blank box
 *   - success → children render
 *
 * The card title is the section's h2 — heading hierarchy is h1 (page
 * heading "Medical Record Dashboard") → h2 (card titles), per §12.
 */
export function CardShell({
  title,
  isLoading,
  error,
  onRetry,
  children,
}: CardShellProps) {
  const slug = slugify(title)

  return (
    <section
      className="bg-white border border-gray-200"
      aria-busy={isLoading}
      aria-labelledby={`card-title-${slug}`}
    >
      <div className="px-3 py-2 flex items-center justify-between border-b border-gray-200">
        <h2
          id={`card-title-${slug}`}
          className="text-sm font-semibold text-blue-700 m-0 flex items-center gap-1.5"
        >
          <span>{title}</span>
          <DragHandleIcon />
        </h2>
        <div className="flex items-center gap-2">
          {isLoading && <Spinner label={`Loading ${title}`} />}
          <EditPencilIcon />
        </div>
      </div>
      <div className="px-3 py-2 text-sm">
        {error ? (
          <ErrorRetry error={error} onRetry={onRetry ?? (() => { /* no-op */ })} />
        ) : (
          children
        )}
      </div>
    </section>
  )
}

/** Bootstrap-style drag handle ⊞ used in the legacy OpenEMR card title bar. */
function DragHandleIcon() {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 16 16"
      width="11"
      height="11"
      className="text-blue-700 opacity-60"
      fill="currentColor"
    >
      <path d="M3 3h4v4H3zM9 3h4v4H9zM3 9h4v4H3zM9 9h4v4H9z" />
    </svg>
  )
}

/** Edit-pencil icon at the right of the legacy OpenEMR card title bar. */
function EditPencilIcon() {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 16 16"
      width="13"
      height="13"
      className="text-blue-700 opacity-70"
      fill="currentColor"
    >
      <path d="M12.146 0.146a.5.5 0 0 1 .708 0l3 3a.5.5 0 0 1 0 .708l-10 10a.5.5 0 0 1-.168.11l-5 2a.5.5 0 0 1-.65-.65l2-5a.5.5 0 0 1 .11-.168zM11.207 2.5 13.5 4.793 14.793 3.5 12.5 1.207zm1.586 3L10.5 3.207 4 9.707V10h.5a.5.5 0 0 1 .5.5v.5h.5a.5.5 0 0 1 .5.5v.5h.293zm-9.761 5.175-.106.106-1.528 3.821 3.821-1.528.106-.106A.5.5 0 0 1 5 12.5V12h-.5a.5.5 0 0 1-.5-.5V11h-.5a.5.5 0 0 1-.468-.325" />
    </svg>
  )
}

function slugify(s: string): string {
  return s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')
}
