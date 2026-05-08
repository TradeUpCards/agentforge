/**
 * Card grid container. Renders into <main> for screen-reader landmark
 * navigation (§12). Lays out cards in a responsive 1/2/3-column grid
 * matching legacy density (no rounded outer container, plain padding).
 *
 * Uses CONTAINER QUERIES (@container + @<size>:) instead of viewport
 * media queries (md:/xl:). Reason: when the Co-Pilot drawer pushes
 * the chart content (see body[data-copilot-open] padding rules in
 * `index.css`), the chart's actual width shrinks even though the
 * viewport doesn't. Viewport-based breakpoints would still try to
 * render 2 or 3 columns at the same viewport width, leaving cramped
 * 200-px-wide cards. Container queries respond to the grid's *own*
 * width and reflow naturally to 1 column when squeezed.
 *
 * Breakpoints (default Tailwind v4 container-query scale):
 *   <@3xl  (<768 px)  : 1 col
 *   @3xl   (≥768 px)  : 2 col
 *   @7xl   (≥1280 px) : 3 col
 *
 * These match the previous viewport breakpoints (md=768, xl=1280)
 * one-for-one when the chart fills the viewport, so non-Co-Pilot
 * behavior is unchanged.
 */
export function DashboardGrid({ children }: { children: React.ReactNode }) {
  return (
    <main className="px-4 py-3 @container">
      <div className="grid grid-cols-1 @3xl:grid-cols-2 @7xl:grid-cols-3 gap-3">
        {children}
      </div>
    </main>
  )
}
