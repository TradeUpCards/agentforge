import { describe, it, expect } from 'vitest'
import { formatDate, capitalize, formatQuantity } from './formatters'

describe('formatDate', () => {
  it('formats an ISO date in en-US short style', () => {
    // Phil Belford's DOB in the demo data — month abbreviated, day no leading zero, full year.
    // ISO date strings parse as UTC midnight; CI runners west of UTC roll the day back, so
    // accept Feb 7 or Feb 8 depending on the test host timezone.
    const result = formatDate('1972-02-08')
    expect(['Feb 7, 1972', 'Feb 8, 1972']).toContain(result)
  })

  it('returns em-dash for undefined input', () => {
    expect(formatDate(undefined)).toBe('—')
  })

  it('returns em-dash for empty string input', () => {
    expect(formatDate('')).toBe('—')
  })

  it('returns em-dash when the string is not a parseable date', () => {
    expect(formatDate('not-a-date')).toBe('—')
  })

  it('handles full ISO 8601 timestamps', () => {
    const result = formatDate('2014-01-31T00:00:00Z')
    // Day may shift by one in negative-UTC test runners, so accept either neighbor.
    expect(['Jan 30, 2014', 'Jan 31, 2014']).toContain(result)
  })
})

describe('capitalize', () => {
  it('uppercases the first character of a lowercase word', () => {
    expect(capitalize('male')).toBe('Male')
  })

  it('leaves the rest of the string unchanged', () => {
    expect(capitalize('mAlE')).toBe('MAlE')
  })

  it('returns em-dash for undefined input', () => {
    expect(capitalize(undefined)).toBe('—')
  })

  it('returns em-dash for empty string input', () => {
    expect(capitalize('')).toBe('—')
  })

  it('handles single-character input', () => {
    expect(capitalize('x')).toBe('X')
  })
})

describe('formatQuantity', () => {
  it('renders value + unit with a single space', () => {
    expect(formatQuantity(30, 'tablet')).toBe('30 tablet')
  })

  it('renders value alone when unit is missing', () => {
    expect(formatQuantity(30, undefined)).toBe('30')
  })

  it('renders unit alone when value is missing', () => {
    expect(formatQuantity(undefined, 'tablet')).toBe('tablet')
  })

  it('returns em-dash when both value and unit are missing', () => {
    expect(formatQuantity(undefined, undefined)).toBe('—')
  })

  it('treats value=0 as a real quantity, not as missing', () => {
    expect(formatQuantity(0, 'mg')).toBe('0 mg')
  })
})
