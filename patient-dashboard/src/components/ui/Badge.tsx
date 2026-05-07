type BadgeVariant = 'default' | 'warning' | 'danger' | 'muted' | 'success'

interface BadgeProps {
  label: string
  variant?: BadgeVariant
  /**
   * Title text for hover/screen-reader context. The badge itself includes
   * the visible label, so this is supplementary.
   */
  title?: string
}

/**
 * Status badge. We never rely on color alone — every variant carries a
 * text label, per §12 (Accessibility).
 *
 * Color contrast: the chosen Tailwind class pairs all meet WCAG AA
 * (verified against Tailwind's documented contrast tables).
 */
export function Badge({ label, variant = 'default', title }: BadgeProps) {
  const variants: Record<BadgeVariant, string> = {
    default: 'bg-blue-100 text-blue-800',
    warning: 'bg-yellow-100 text-yellow-800 font-semibold',
    danger: 'bg-red-100 text-red-800 font-bold',
    muted: 'bg-gray-100 text-gray-700',
    success: 'bg-green-100 text-green-800',
  }
  return (
    <span
      title={title}
      className={`inline-flex items-center px-2 py-0.5 rounded text-xs ${variants[variant]}`}
    >
      {label}
    </span>
  )
}
