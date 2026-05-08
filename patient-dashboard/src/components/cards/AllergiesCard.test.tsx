import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { AllergiesCard } from './AllergiesCard'
import type { FhirAllergyIntolerance, FhirBundle } from '../../types/fhir'

/**
 * Component test for AllergiesCard. Verifies the four states described
 * in §10 of PATIENT_DASHBOARD_MIGRATION.md: loading, populated, empty,
 * and error.
 *
 * Hermetic — `useAuth` and `getAllergies` are mocked. No fetch, no
 * OpenEMR, no real network.
 */

// Mock react-oidc-context's useAuth so the hook is happy in tests.
vi.mock('react-oidc-context', () => ({
  useAuth: () => ({
    user: { access_token: 'fake-test-token' },
    isAuthenticated: true,
    isLoading: false,
    error: undefined,
  }),
}))

// Mock the API resource. Each test case stubs the implementation it needs.
vi.mock('../../api/resources/allergies', () => ({
  getAllergies: vi.fn(),
}))

import { getAllergies } from '../../api/resources/allergies'
const mockGetAllergies = vi.mocked(getAllergies)

/**
 * One QueryClient per test, with retries off and stale time 0 so the
 * mocked fetch resolves immediately and predictably. The default
 * QueryClient (used by the live app) is configured with retry: 1, which
 * would slow tests with no benefit.
 */
function renderWithQueryClient(node: ReactNode) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: 0, gcTime: 0 },
    },
  })
  return render(
    <QueryClientProvider client={queryClient}>{node}</QueryClientProvider>,
  )
}

beforeEach(() => {
  mockGetAllergies.mockReset()
})

describe('AllergiesCard', () => {
  it('renders the loading state immediately on mount', () => {
    // A promise that never resolves — keeps the query in `isLoading`.
    mockGetAllergies.mockImplementation(() => new Promise(() => {}))
    renderWithQueryClient(<AllergiesCard patientId="phil-belford-uuid" />)

    expect(screen.getByText('Allergies')).toBeInTheDocument()
    // Spinner renders the label twice — once `sr-only` for assistive tech,
    // once visible-on-prefers-reduced-motion. Both are correct; assert
    // at least one is present.
    expect(screen.getAllByText(/Loading Allergies/i).length).toBeGreaterThan(0)
  })

  it('renders allergy names + criticality and severity badges when data resolves', async () => {
    const bundle: FhirBundle<FhirAllergyIntolerance> = {
      resourceType: 'Bundle',
      entry: [
        {
          resource: {
            resourceType: 'AllergyIntolerance',
            id: 'a1',
            code: { text: 'Penicillin' },
            criticality: 'high',
            reaction: [{ severity: 'severe' }],
          },
        },
        {
          resource: {
            resourceType: 'AllergyIntolerance',
            id: 'a2',
            code: { text: 'Sulfa drugs' },
            reaction: [{ severity: 'mild' }],
          },
        },
      ],
    }
    mockGetAllergies.mockResolvedValue(bundle)
    renderWithQueryClient(<AllergiesCard patientId="phil-belford-uuid" />)

    expect(await screen.findByText('Penicillin')).toBeInTheDocument()
    expect(screen.getByText('Sulfa drugs')).toBeInTheDocument()
    // Two badges on the Penicillin row (high criticality + severe reaction).
    expect(screen.getByText('High')).toBeInTheDocument()
    expect(screen.getByText('severe')).toBeInTheDocument()
    // The mild allergy gets a muted badge.
    expect(screen.getByText('mild')).toBeInTheDocument()
  })

  it('expands a row to show reaction details when the user clicks it', async () => {
    const bundle: FhirBundle<FhirAllergyIntolerance> = {
      resourceType: 'Bundle',
      entry: [
        {
          resource: {
            resourceType: 'AllergyIntolerance',
            id: 'a1',
            code: { text: 'Penicillin' },
            criticality: 'high',
            recordedDate: '2024-03-01',
            reaction: [
              { severity: 'severe', manifestation: [{ text: 'Anaphylaxis' }] },
            ],
          },
        },
      ],
    }
    mockGetAllergies.mockResolvedValue(bundle)
    const { default: userEvent } = await import('@testing-library/user-event')
    const user = userEvent.setup()
    renderWithQueryClient(<AllergiesCard patientId="phil-belford-uuid" />)

    // The row trigger is the only button rendered (apart from CardShell's
    // internals). Find by accessible name = the row's text.
    const trigger = await screen.findByRole('button', { name: /Penicillin/i })
    expect(trigger).toHaveAttribute('aria-expanded', 'false')

    await user.click(trigger)

    expect(trigger).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText(/Anaphylaxis/)).toBeInTheDocument()
    expect(screen.getByText(/Recorded/)).toBeInTheDocument()
  })

  it('renders the "No Known Allergies" empty state for a zero-entry bundle', async () => {
    mockGetAllergies.mockResolvedValue({
      resourceType: 'Bundle',
      entry: [],
    })
    renderWithQueryClient(<AllergiesCard patientId="phil-belford-uuid" />)

    expect(await screen.findByText('No Known Allergies')).toBeInTheDocument()
  })

  it('renders the error retry control when the FHIR call rejects', async () => {
    mockGetAllergies.mockRejectedValue(new Error('boom — network down'))
    renderWithQueryClient(<AllergiesCard patientId="phil-belford-uuid" />)

    // ErrorRetry uses role="alert" for screen readers.
    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeInTheDocument()
    })
    expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument()
  })
})
