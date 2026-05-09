import { useState, useEffect, useId } from 'react'
import { useAuth } from 'react-oidc-context'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { createAllergy } from '../../api/resources/allergies'
import { FhirError } from '../../api/fhirClient'

interface AddAllergyModalProps {
  patientId: string
  open: boolean
  onClose: () => void
}

/**
 * Add-Allergy demo modal. The only WRITE surface in the dashboard;
 * exists to demonstrate that:
 *
 *   1. OAuth2 scope expansion works (`user/AllergyIntolerance.cu`)
 *      — re-running scripts/register-oauth-client.sh registers a
 *      client with that scope; the dashboard can then POST.
 *   2. The modal is the right pattern for a focused write task
 *      (the rest of the dashboard uses inline accordions; the contrast
 *      illustrates the case for each).
 *   3. After a successful POST we invalidate the allergies query so
 *      the list refreshes immediately — read-after-write consistency
 *      via TanStack Query, not a manual refetch.
 *
 * Form fields: substance (required), reaction (optional), severity
 * (optional). Status defaults are baked into createAllergy.
 *
 * Layout:
 *   - Phone (any orientation, <md): full-screen overlay. The modal
 *     fills the viewport so the keyboard doesn't fight for space.
 *   - Tablet+ (≥md): centered card, ~520 px wide, backdrop click
 *     and Escape both close.
 */
export function AddAllergyModal({ patientId, open, onClose }: AddAllergyModalProps) {
  const auth = useAuth()
  const queryClient = useQueryClient()
  const titleId = useId()
  const substanceId = useId()
  const reactionId = useId()
  const severityId = useId()

  const [substance, setSubstance] = useState('')
  const [reaction, setReaction] = useState('')
  const [severity, setSeverity] = useState<'' | 'mild' | 'moderate' | 'severe'>('')

  const mutation = useMutation({
    mutationFn: () => {
      const token = auth.user?.access_token ?? ''
      return createAllergy(
        {
          patientId,
          substance: substance.trim(),
          ...(reaction.trim() ? { reaction: reaction.trim() } : {}),
          ...(severity ? { severity } : {}),
        },
        token,
      )
    },
    onSuccess: () => {
      // Invalidate the allergies query so the card refetches and shows
      // the new entry. Same query key as `useAllergies(patientId)`.
      queryClient.invalidateQueries({ queryKey: ['allergies', patientId] })
      onClose()
      // Reset form for next open.
      setSubstance('')
      setReaction('')
      setSeverity('')
    },
  })

  // Close on Escape.
  useEffect(() => {
    if (!open) return
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null

  const errorMessage = (() => {
    if (!mutation.isError) return null
    const err = mutation.error
    if (err instanceof FhirError) {
      if (err.status === 401) return 'Your session has expired. Please sign out and back in.'
      if (err.status === 403) {
        return 'OAuth2 scope missing. Re-run scripts/register-oauth-client.sh and update .env to enable Allergy writes.'
      }
      return `Could not save (HTTP ${err.status}).`
    }
    return 'Could not save. Please try again.'
  })()

  return (
    <>
      {/* Backdrop */}
      <div
        aria-hidden="true"
        onClick={onClose}
        className="fixed inset-0 z-50 bg-black/40 motion-safe:transition-opacity motion-safe:duration-150"
      />
      {/* Modal container — bottom-sheet on phone (slides up from
          bottom, capped at 90 vh), centered card on tablet+. Flex
          column inside the dialog so the header and footer stay fixed
          while the form fields scroll if the content overflows
          (especially with the soft keyboard up). */}
      <div className="fixed inset-0 z-50 flex items-end md:items-center justify-center p-0 md:p-4 pointer-events-none">
        <form
          role="dialog"
          aria-modal="true"
          aria-labelledby={titleId}
          onSubmit={(e) => {
            e.preventDefault()
            if (!substance.trim()) return
            mutation.mutate()
          }}
          className="
            pointer-events-auto bg-white shadow-2xl
            w-full md:w-[520px]
            max-h-[90vh] md:max-h-[calc(100vh-2rem)]
            rounded-t-2xl md:rounded-lg
            flex flex-col
          "
        >
          <header className="flex items-center justify-between px-4 py-3 border-b border-gray-200 shrink-0">
            <h2 id={titleId} className="text-base font-semibold text-gray-800 m-0">
              Add Allergy
            </h2>
            <button
              type="button"
              onClick={onClose}
              aria-label="Close"
              className="
                inline-flex items-center justify-center
                p-2 min-h-11 min-w-11 -mr-2
                text-gray-600 hover:text-gray-900
                rounded
                focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600
              "
            >
              <svg viewBox="0 0 16 16" width="16" height="16" fill="currentColor" aria-hidden="true">
                <path d="M2.146 2.854a.5.5 0 1 1 .708-.708L8 7.293l5.146-5.147a.5.5 0 0 1 .708.708L8.707 8l5.147 5.146a.5.5 0 0 1-.708.708L8 8.707l-5.146 5.147a.5.5 0 0 1-.708-.708L7.293 8z" />
              </svg>
            </button>
          </header>

          {/* Scrollable fields — flex-1 + overflow inside the form's
              column. Footer (Save/Cancel) stays pinned via shrink-0
              below this region. */}
          <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
            <div>
              <label
                htmlFor={substanceId}
                className="block text-sm font-medium text-gray-700 mb-1"
              >
                Substance <span className="text-red-600" aria-hidden="true">*</span>
              </label>
              <input
                id={substanceId}
                type="text"
                value={substance}
                onChange={(e) => setSubstance(e.target.value)}
                required
                autoFocus
                placeholder="e.g. Penicillin"
                className="
                  w-full px-3 py-2 min-h-11
                  border border-gray-300 rounded
                  text-sm
                  focus:outline-2 focus:outline-blue-600 focus:outline-offset-1
                "
              />
            </div>

            <div>
              <label
                htmlFor={reactionId}
                className="block text-sm font-medium text-gray-700 mb-1"
              >
                Reaction <span className="text-gray-500 font-normal">(optional)</span>
              </label>
              <input
                id={reactionId}
                type="text"
                value={reaction}
                onChange={(e) => setReaction(e.target.value)}
                placeholder="e.g. Hives, anaphylaxis"
                className="
                  w-full px-3 py-2 min-h-11
                  border border-gray-300 rounded
                  text-sm
                  focus:outline-2 focus:outline-blue-600 focus:outline-offset-1
                "
              />
            </div>

            <div>
              <span
                id={severityId}
                className="block text-sm font-medium text-gray-700 mb-1"
              >
                Severity <span className="text-gray-500 font-normal">(optional)</span>
              </span>
              {/*
                Button-group radio pattern instead of a native <select>.
                Native dropdowns overflow the viewport when the field
                is near the bottom of a modal (verified on phone
                portrait — `Severe` option clipped off-screen). Button
                group keeps every option visible at once, no overlay
                positioning to fight, and the larger tap targets are
                better for touch anyway.
              */}
              <div
                role="radiogroup"
                aria-labelledby={severityId}
                className="grid grid-cols-2 gap-2 sm:grid-cols-4"
              >
                {(
                  [
                    { value: '', label: 'Not specified' },
                    { value: 'mild', label: 'Mild' },
                    { value: 'moderate', label: 'Moderate' },
                    { value: 'severe', label: 'Severe' },
                  ] as const
                ).map((o) => {
                  const selected = severity === o.value
                  return (
                    <button
                      key={o.value || 'unspecified'}
                      type="button"
                      role="radio"
                      aria-checked={selected}
                      onClick={() => setSeverity(o.value)}
                      className={`
                        min-h-11 px-3 py-2 text-sm rounded border
                        focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600
                        motion-safe:transition-colors
                        ${
                          selected
                            ? 'border-blue-700 bg-blue-50 text-blue-700 font-semibold'
                            : 'border-gray-300 bg-white text-gray-700 hover:bg-gray-50'
                        }
                      `}
                    >
                      {o.label}
                    </button>
                  )
                })}
              </div>
            </div>

            {errorMessage && (
              <p
                role="alert"
                className="text-sm text-red-700 bg-red-50 border border-red-200 rounded px-3 py-2"
              >
                {errorMessage}
              </p>
            )}
          </div>

          {/* Pinned footer — stays visible at the bottom of the modal
              even when the field area scrolls. */}
          <footer className="flex items-center justify-end gap-2 px-4 py-3 border-t border-gray-200 shrink-0">
            <button
              type="button"
              onClick={onClose}
              disabled={mutation.isPending}
              className="
                inline-flex items-center justify-center
                px-4 py-2 min-h-11
                text-sm font-medium text-gray-700
                border border-gray-300 rounded bg-white hover:bg-gray-50
                disabled:opacity-50
                focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600
              "
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={!substance.trim() || mutation.isPending}
              className="
                inline-flex items-center justify-center
                px-4 py-2 min-h-11
                text-sm font-medium text-white
                bg-blue-700 hover:bg-blue-800 active:bg-blue-900 rounded
                disabled:opacity-50 disabled:cursor-not-allowed
                focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600
                motion-safe:transition-colors
              "
            >
              {mutation.isPending ? 'Saving…' : 'Save Allergy'}
            </button>
          </footer>
        </form>
      </div>
    </>
  )
}
