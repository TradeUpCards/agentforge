import { useId, useState, type ReactNode } from 'react'

interface ExpandableRowProps {
  /** What's always visible — the "row" content on the trigger button. */
  summary: ReactNode
  /** What appears below when expanded. */
  details: ReactNode
  /**
   * Whether the row should be expanded by default. Cards leave this
   * false; consumers can override (e.g. expand the first row in a list).
   */
  defaultExpanded?: boolean
}

/**
 * Reusable click-to-expand accordion row, used inside cards (Allergies,
 * Medical Problems, Medications, Prescriptions, Care Team, Encounter
 * History) to surface the full FHIR resource shape without a modal or
 * navigation hop.
 *
 * Behavior:
 *   - The whole row is a `<button>` with `aria-expanded`. Tapping or
 *     pressing Space/Enter toggles. Native focus + keyboard support; no
 *     custom keyboard handlers needed.
 *   - Detail panel appears inline below; surrounding cards reflow.
 *   - Chevron rotates 180° on expand.
 *   - Touch target is ≥44 px on `<md` (Apple HIG / WCAG 2.2 SC 2.5.5).
 *
 * Why inline accordion (vs modal / drawer / new page):
 *   - Keeps surrounding chart context visible — clinically useful when
 *     comparing rows (e.g. two interacting medications).
 *   - Works at every breakpoint without switching to a different UI
 *     pattern. Modals are jarring on phone; drawers compete with the
 *     Co-Pilot drawer; new pages add a navigation hop.
 *   - Matches Epic Hyperdrive and Cerner Camp chart-row conventions.
 */
export function ExpandableRow({
  summary,
  details,
  defaultExpanded = false,
}: ExpandableRowProps) {
  const [expanded, setExpanded] = useState(defaultExpanded)
  const detailsId = useId()

  return (
    <li className="border-b border-gray-100 last:border-b-0">
      <button
        type="button"
        onClick={() => setExpanded((s) => !s)}
        aria-expanded={expanded}
        aria-controls={detailsId}
        className="
          w-full text-left py-2 md:py-1.5 px-1 min-h-11 md:min-h-0
          flex items-center justify-between gap-2
          hover:bg-gray-50
          focus-visible:outline-2 focus-visible:outline-offset-[-2px]
          focus-visible:outline-blue-600
        "
      >
        <span className="flex-1 min-w-0">{summary}</span>
        <Chevron expanded={expanded} />
      </button>
      {expanded && (
        <div id={detailsId} className="px-1 pb-3 pt-1 text-xs text-gray-700 bg-gray-50">
          {details}
        </div>
      )}
    </li>
  )
}

function Chevron({ expanded }: { expanded: boolean }) {
  return (
    <svg
      viewBox="0 0 16 16"
      width="14"
      height="14"
      fill="currentColor"
      aria-hidden="true"
      className={`text-gray-400 shrink-0 transition-transform ${
        expanded ? 'rotate-180' : ''
      }`}
    >
      <path d="M1.646 4.646a.5.5 0 0 1 .708 0L8 10.293l5.646-5.647a.5.5 0 0 1 .708.708l-6 6a.5.5 0 0 1-.708 0l-6-6a.5.5 0 0 1 0-.708" />
    </svg>
  )
}

/**
 * Helper component for the detail panel — renders a key/value list in
 * a consistent two-column layout. Each card's expanded detail uses
 * this for consistency.
 */
export function DetailGrid({ rows }: { rows: { label: string; value: ReactNode }[] }) {
  return (
    <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1 m-0">
      {rows.map((r, i) => (
        <div key={i} className="contents">
          <dt className="font-semibold text-gray-600">{r.label}</dt>
          <dd className="m-0 text-gray-800 break-words">{r.value ?? '—'}</dd>
        </div>
      ))}
    </dl>
  )
}
