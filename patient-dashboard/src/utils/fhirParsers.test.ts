import { describe, it, expect } from 'vitest'
import {
  getPatientDisplayName,
  getPatientMrn,
  getPatientActiveStatus,
  extractBundleResources,
} from './fhirParsers'
import type { FhirBundle, FhirPatient } from '../types/fhir'

describe('getPatientDisplayName', () => {
  it('prefers the official name when multiple are present', () => {
    const patient: FhirPatient = {
      resourceType: 'Patient',
      name: [
        { use: 'usual', given: ['Phil', 'Q'], family: 'Public' },
        { use: 'official', given: ['Phil'], family: 'Belford' },
      ],
    }
    expect(getPatientDisplayName(patient)).toBe('Phil Belford')
  })

  it('falls back to the first available name when no official exists', () => {
    const patient: FhirPatient = {
      resourceType: 'Patient',
      name: [{ given: ['Riley'], family: 'Test' }],
    }
    expect(getPatientDisplayName(patient)).toBe('Riley Test')
  })

  it('uses name.text when given/family are absent but text is provided', () => {
    const patient: FhirPatient = {
      resourceType: 'Patient',
      name: [{ text: 'Ms. Phil Belford, MD' }],
    }
    expect(getPatientDisplayName(patient)).toBe('Ms. Phil Belford, MD')
  })

  it('joins multiple given names with a space', () => {
    const patient: FhirPatient = {
      resourceType: 'Patient',
      name: [{ given: ['Phil', 'Q'], family: 'Belford' }],
    }
    expect(getPatientDisplayName(patient)).toBe('Phil Q Belford')
  })

  it('returns "Unknown Patient" when name array is missing', () => {
    const patient: FhirPatient = { resourceType: 'Patient' }
    expect(getPatientDisplayName(patient)).toBe('Unknown Patient')
  })

  it('returns "Unknown Patient" when name array is empty', () => {
    const patient: FhirPatient = { resourceType: 'Patient', name: [] }
    expect(getPatientDisplayName(patient)).toBe('Unknown Patient')
  })

  it('returns "Unknown Patient" when given/family/text are all absent', () => {
    const patient: FhirPatient = {
      resourceType: 'Patient',
      name: [{ use: 'official' }],
    }
    expect(getPatientDisplayName(patient)).toBe('Unknown Patient')
  })
})

describe('getPatientMrn', () => {
  it('finds the identifier whose type.coding includes code "MR"', () => {
    const patient: FhirPatient = {
      resourceType: 'Patient',
      identifier: [
        { use: 'usual', value: 'SSN-1234' },
        {
          type: { coding: [{ code: 'MR', system: 'http://hl7.org/fhir/v2/0203' }] },
          value: '333222333',
        },
      ],
    }
    expect(getPatientMrn(patient)).toBe('333222333')
  })

  it('falls back to the first identifier with a value when no MR-coded one exists', () => {
    const patient: FhirPatient = {
      resourceType: 'Patient',
      identifier: [{ use: 'usual', value: 'X-123' }],
    }
    expect(getPatientMrn(patient)).toBe('X-123')
  })

  it('returns the em-dash placeholder when no identifiers are present', () => {
    const patient: FhirPatient = { resourceType: 'Patient' }
    expect(getPatientMrn(patient)).toBe('—')
  })

  it('returns the em-dash when identifiers exist but none have a value', () => {
    const patient: FhirPatient = {
      resourceType: 'Patient',
      identifier: [{ use: 'usual' }],
    }
    expect(getPatientMrn(patient)).toBe('—')
  })
})

describe('getPatientActiveStatus', () => {
  it('returns true when active is explicitly true', () => {
    expect(getPatientActiveStatus({ resourceType: 'Patient', active: true })).toBe(true)
  })

  it('returns false when active is explicitly false', () => {
    expect(getPatientActiveStatus({ resourceType: 'Patient', active: false })).toBe(false)
  })

  it('treats missing active field as active (FHIR convention)', () => {
    expect(getPatientActiveStatus({ resourceType: 'Patient' })).toBe(true)
  })
})

describe('extractBundleResources', () => {
  it('flattens entry.resource values from a Bundle', () => {
    const bundle: FhirBundle<FhirPatient> = {
      resourceType: 'Bundle',
      entry: [
        { resource: { resourceType: 'Patient', id: 'a' } },
        { resource: { resourceType: 'Patient', id: 'b' } },
      ],
    }
    const result = extractBundleResources<FhirPatient>(bundle)
    expect(result).toHaveLength(2)
    expect(result[0]?.id).toBe('a')
    expect(result[1]?.id).toBe('b')
  })

  it('skips entries that lack a resource', () => {
    const bundle: FhirBundle<FhirPatient> = {
      resourceType: 'Bundle',
      entry: [
        { resource: { resourceType: 'Patient', id: 'a' } },
        { fullUrl: 'urn:uuid:nope' }, // no resource
        { resource: { resourceType: 'Patient', id: 'b' } },
      ],
    }
    const result = extractBundleResources<FhirPatient>(bundle)
    expect(result).toHaveLength(2)
    expect(result.map((r) => r.id)).toEqual(['a', 'b'])
  })

  it('returns an empty array when entry is missing', () => {
    const bundle: FhirBundle<FhirPatient> = { resourceType: 'Bundle' }
    expect(extractBundleResources(bundle)).toEqual([])
  })

  it('returns an empty array when entry is an empty array', () => {
    const bundle: FhirBundle<FhirPatient> = { resourceType: 'Bundle', entry: [] }
    expect(extractBundleResources(bundle)).toEqual([])
  })
})
