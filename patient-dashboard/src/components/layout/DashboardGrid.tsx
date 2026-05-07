/**
 * Card grid container. Renders into <main> for screen-reader landmark
 * navigation (§12). Lays out cards in a responsive 1-/2-/3-column grid
 * matching legacy density (no rounded outer container, plain padding).
 */
export function DashboardGrid({ children }: { children: React.ReactNode }) {
  return (
    <main className="px-4 py-3">
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
        {children}
      </div>
    </main>
  )
}
