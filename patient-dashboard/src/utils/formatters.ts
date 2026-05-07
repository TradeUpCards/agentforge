/**
 * Display formatters. Pure; unit-tested in formatters.test.ts.
 */

const EM_DASH = '—'

export function formatDate(isoDate: string | undefined): string {
  if (!isoDate) return EM_DASH
  const d = new Date(isoDate)
  if (Number.isNaN(d.getTime())) return EM_DASH
  return d.toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  })
}

export function capitalize(s: string | undefined): string {
  if (!s) return EM_DASH
  return s.charAt(0).toUpperCase() + s.slice(1)
}

/**
 * Render a FHIR Quantity ("30 tablet", "5 mg/day"). Returns em-dash for
 * missing values rather than empty string so the UI never shows a blank
 * cell.
 */
export function formatQuantity(value: number | undefined, unit: string | undefined): string {
  if (value === undefined && !unit) return EM_DASH
  const v = value ?? ''
  const u = unit ?? ''
  return `${v} ${u}`.trim() || EM_DASH
}
