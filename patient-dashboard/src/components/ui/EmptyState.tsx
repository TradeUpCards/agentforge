/**
 * Empty-state copy. Mirrors the legacy Twig allergies card distinction
 * between "list was touched but empty" (e.g. `"No Known Allergies"`) and
 * "never recorded" (`"Nothing Recorded"`). See parity table §9.
 */
export function EmptyState({ message }: { message: string }) {
  return (
    <p className="text-sm text-gray-500 italic py-2 px-1" role="status">
      {message}
    </p>
  )
}
