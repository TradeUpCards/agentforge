/**
 * Loading indicator. Respects `prefers-reduced-motion` per §12 of
 * PATIENT_DASHBOARD_MIGRATION.md — reduces to a static "Loading…" label.
 */
export function Spinner({ label = 'Loading' }: { label?: string }) {
  return (
    <>
      <span className="sr-only">{label}…</span>
      <span
        aria-hidden="true"
        className="inline-block w-5 h-5 border-2 border-blue-600 border-t-transparent rounded-full animate-spin motion-reduce:hidden"
      />
      <span
        aria-hidden="true"
        className="hidden motion-reduce:inline text-xs text-gray-500"
      >
        {label}…
      </span>
    </>
  )
}
