import { useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { MainNav } from '../components/layout/MainNav'
import { OpenTabBar } from '../components/layout/OpenTabBar'
import { PatientHeader } from '../components/layout/PatientHeader'
import { MedicalRecordHeading } from '../components/layout/MedicalRecordHeading'
import { SubNav } from '../components/layout/SubNav'
import { useDocuments } from '../hooks/useDocuments'
import { extractBundleResources } from '../utils/fhirParsers'
import { formatDate } from '../utils/formatters'
import type { FhirDocumentReference } from '../types/fhir'
import { Spinner } from '../components/ui/Spinner'
import { ErrorRetry } from '../components/ui/ErrorRetry'
import { ExpandableRow, DetailGrid } from '../components/ui/ExpandableRow'
import { CoPilotDrawer } from '../components/copilot/CoPilotDrawer'

/**
 * Documents page — full listing of FHIR DocumentReference resources for
 * the patient, served at `/dashboard/:patientId/documents`. Replaces
 * the SectionStubPage default for `documents` (the SubNav link routes
 * here directly via App.tsx route ordering).
 *
 * Pulls up to 50 documents (more than the 5 the dashboard card shows).
 * Each row is an inline-accordion ExpandableRow with the same field set
 * as DocumentsCard's expanded panel.
 *
 * For viewing/downloading the document binary itself: each expanded row
 * surfaces an "Open in OpenEMR" out-link. The binary fetch via FHIR
 * `Binary/{id}` is technically possible but requires a `Binary.read`
 * scope expansion and a content-disposition handler we deliberately
 * defer.
 */
export function DocumentsPage() {
  const { patientId } = useParams<{ patientId: string }>()
  const [headerCollapsed, setHeaderCollapsed] = useState(false)
  const { data, isLoading, error, refetch } = useDocuments(patientId ?? '', 50)
  const docs = data ? extractBundleResources<FhirDocumentReference>(data) : []

  if (!patientId) {
    return (
      <p className="p-4 text-red-700" role="alert">
        No patient selected.
      </p>
    )
  }

  return (
    <div className="min-h-screen bg-white">
      <MainNav />
      <PatientHeader patientId={patientId} collapsed={headerCollapsed} />
      <OpenTabBar
        active="Dashboard"
        headerCollapsed={headerCollapsed}
        onToggleHeader={() => setHeaderCollapsed((s) => !s)}
      />
      <MedicalRecordHeading patientId={patientId} />
      <SubNav patientId={patientId} />
      <main className="px-4 py-6 max-w-4xl mx-auto">
        <div className="flex items-baseline justify-between gap-2 mb-3">
          <h2 className="text-base font-semibold text-gray-800 m-0">
            Documents ({isLoading ? '…' : docs.length})
          </h2>
          <Link
            to={`/dashboard/${patientId}`}
            className="text-sm text-blue-700 hover:underline"
          >
            ← Back to dashboard
          </Link>
        </div>

        {isLoading ? (
          <div className="flex items-center gap-2" role="status" aria-live="polite">
            <Spinner label="Loading documents" />
            <span className="text-sm text-gray-600">Loading documents…</span>
          </div>
        ) : error ? (
          <ErrorRetry error={error} onRetry={() => void refetch()} />
        ) : docs.length === 0 ? (
          <p className="text-sm text-gray-500 italic">
            No documents recorded for this patient.
          </p>
        ) : (
          <ul className="m-0 p-0 list-none bg-white border border-gray-200">
            {docs.map((d) => (
              <ExpandableRow
                key={d.id ?? `${docTitle(d)}-${d.date ?? ''}`}
                summary={
                  <span className="block min-w-0">
                    <span className="flex items-center justify-between gap-2">
                      <span className="text-gray-800 truncate">{docTitle(d)}</span>
                      {d.date && (
                        <span className="text-xs text-gray-500 shrink-0">
                          {formatDate(d.date)}
                        </span>
                      )}
                    </span>
                    {d.description && d.description !== docTitle(d) && (
                      <span className="text-xs text-gray-500 block mt-0.5 truncate">
                        {d.description}
                      </span>
                    )}
                  </span>
                }
                details={<DocumentDetails doc={d} />}
              />
            ))}
          </ul>
        )}
      </main>
      <CoPilotDrawer patientId={patientId} />
    </div>
  )
}

function docTitle(d: FhirDocumentReference): string {
  return (
    d.type?.text ??
    d.type?.coding?.[0]?.display ??
    d.content?.[0]?.attachment?.title ??
    d.description ??
    'Document'
  )
}

function DocumentDetails({ doc }: { doc: FhirDocumentReference }) {
  const attachment = doc.content?.[0]?.attachment
  const sizeKb = attachment?.size ? Math.round(attachment.size / 1024) : null
  const authors = (doc.author ?? [])
    .map((a) => a.display ?? a.reference)
    .filter(Boolean)
    .join(', ')
  return (
    <DetailGrid
      rows={[
        { label: 'Type', value: doc.type?.text ?? doc.type?.coding?.[0]?.display ?? null },
        { label: 'Status', value: doc.status ?? null },
        { label: 'Doc status', value: doc.docStatus ?? null },
        {
          label: 'Category',
          value:
            doc.category?.[0]?.text ??
            doc.category?.[0]?.coding?.[0]?.display ??
            null,
        },
        { label: 'Date', value: doc.date ? formatDate(doc.date) : null },
        { label: 'Authors', value: authors || null },
        { label: 'Description', value: doc.description ?? null },
        { label: 'Content type', value: attachment?.contentType ?? null },
        { label: 'Size', value: sizeKb !== null ? `${sizeKb} KB` : null },
        { label: 'Title', value: attachment?.title ?? null },
      ]}
    />
  )
}
