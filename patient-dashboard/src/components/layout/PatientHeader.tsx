import { usePatient } from '../../hooks/usePatient'
import {
  getPatientActiveStatus,
  getPatientDisplayName,
  getPatientMrn,
} from '../../utils/fhirParsers'
import { capitalize, formatDate } from '../../utils/formatters'
import { Spinner } from '../ui/Spinner'

interface PatientHeaderProps {
  patientId: string
}

/**
 * Patient identity strip — mirrors the OpenEMR row directly below the
 * open-tabs bar:
 *   - Avatar circle + patient name in OpenEMR blue + "(pid)"-style ID
 *     badge + close glyph.
 *   - Below the name: DOB / Age (calculated client-side, since FHIR
 *     does not return age directly), Sex, MRN, and Active pill.
 *   - Right side: minimal "Open Encounter: None" widget for visual
 *     familiarity — the encounter feature is out of scope for this
 *     port, so it is informational only.
 *
 * The PDF requirement #2 demands name / DOB / sex / MRN / active
 * status. All five appear here.
 */
export function PatientHeader({ patientId }: PatientHeaderProps) {
  const { data: patient, isLoading, error } = usePatient(patientId)
  const age = patient?.birthDate ? calculateAge(patient.birthDate) : undefined

  return (
    <header className="bg-white border-b border-gray-200" aria-busy={isLoading}>
      <div className="px-4 py-3 flex items-start justify-between gap-4 flex-wrap">
        <div className="flex items-start gap-3">
          <div
            className="w-12 h-12 rounded-full bg-gray-200 border border-gray-300 flex items-center justify-center text-gray-500"
            aria-hidden="true"
          >
            <svg viewBox="0 0 24 24" width="22" height="22" fill="currentColor">
              <path d="M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8m0 2c-3.5 0-8 1.75-8 5v3h16v-3c0-3.25-4.5-5-8-5" />
            </svg>
          </div>

          {isLoading ? (
            <div className="flex items-center gap-2 mt-1">
              <Spinner label="Loading patient" />
            </div>
          ) : error ? (
            <p className="text-sm text-red-700" role="alert">
              Failed to load patient.
            </p>
          ) : patient ? (
            <div>
              <h1 className="text-xl font-bold text-blue-700 m-0 leading-tight flex items-baseline gap-1.5">
                <span>{getPatientDisplayName(patient)}</span>
                <span className="text-gray-500 text-sm font-normal">
                  ({patient.id?.slice(0, 8) ?? '—'})
                </span>
                <span className="text-gray-400 text-sm font-normal">×</span>
              </h1>
              <p className="text-sm text-gray-700 mt-1">
                <span className="font-semibold">DOB:</span>{' '}
                {formatDate(patient.birthDate)}
                {age !== undefined && (
                  <>
                    {' '}
                    <span className="font-semibold">Age:</span> {age}
                  </>
                )}
                {' · '}
                <span className="font-semibold">Sex:</span>{' '}
                {capitalize(patient.gender)}
                {' · '}
                <span className="font-semibold">MRN:</span>{' '}
                <span className="font-mono">{getPatientMrn(patient)}</span>
                {' · '}
                <span
                  className={`inline-block px-1.5 py-0.5 rounded text-xs font-medium align-middle ${
                    getPatientActiveStatus(patient)
                      ? 'bg-green-100 text-green-800'
                      : 'bg-gray-200 text-gray-700'
                  }`}
                >
                  {getPatientActiveStatus(patient) ? 'Active' : 'Inactive'}
                </span>
              </p>
            </div>
          ) : null}
        </div>

        {/* Right-side encounter widget — visual placeholder per legacy */}
        <div className="text-sm text-gray-700 mt-1">
          <span className="font-semibold">Open Encounter:</span>{' '}
          <span className="text-gray-500">None</span>
        </div>
      </div>
    </header>
  )
}

function calculateAge(isoDate: string): number | undefined {
  const dob = new Date(isoDate)
  if (Number.isNaN(dob.getTime())) return undefined
  const now = new Date()
  let age = now.getFullYear() - dob.getFullYear()
  const m = now.getMonth() - dob.getMonth()
  if (m < 0 || (m === 0 && now.getDate() < dob.getDate())) age--
  return age
}
