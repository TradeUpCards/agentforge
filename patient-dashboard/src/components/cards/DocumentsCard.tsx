import { Link } from 'react-router-dom'
import { useDocuments } from '../../hooks/useDocuments'
import { extractBundleResources } from '../../utils/fhirParsers'
import { formatDate } from '../../utils/formatters'
import { openemrEditLink } from '../../utils/openemrLinks'
import type { FhirDocumentReference } from '../../types/fhir'
import { CardShell } from './CardShell'
import { EmptyState } from '../ui/EmptyState'
import { ExpandableRow, DetailGrid } from '../ui/ExpandableRow'

/**
 * Documents card — shows the 5 most recent FHIR DocumentReference
 * resources for the patient. Bonus card beyond the PDF brief; included
 * because Aria's HITL extraction workstream uploads documents into
 * OpenEMR and this card surfaces them.
 *
 * Each row expands to show document type, status, doc-status (preliminary
 * vs final), category, attachment content-type, attachment size,
 * description, and authors.
 *
 * The card title is a link to the full /dashboard/:id/documents page so
 * a clinician can see more than the five most recent.
 */
export function DocumentsCard({ patientId }: { patientId: string }) {
  const { data, isLoading, error, refetch } = useDocuments(patientId, 5)
  const docs = data ? extractBundleResources<FhirDocumentReference>(data) : []

  return (
    <CardShell
      title="Documents"
      isLoading={isLoading}
      error={error}
      onRetry={() => void refetch()}
      editHref={openemrEditLink('document')}
    >
      {!isLoading && docs.length === 0 ? (
        <EmptyState message={data ? 'No Documents' : 'Nothing Recorded'} />
      ) : (
        <>
          <ul className="m-0 p-0 list-none">
            {docs.map((d) => (
              <ExpandableRow
                key={d.id ?? `${docTitle(d)}-${d.date ?? ''}`}
                summary={<DocumentSummary doc={d} />}
                details={<DocumentDetails doc={d} />}
              />
            ))}
          </ul>
          <Link
            to={`/dashboard/${patientId}/documents`}
            className="block mt-2 text-xs text-blue-700 hover:underline"
          >
            View all documents →
          </Link>
        </>
      )}
    </CardShell>
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

function DocumentSummary({ doc }: { doc: FhirDocumentReference }) {
  return (
    <span className="block min-w-0">
      <span className="flex items-center justify-between gap-2">
        <span className="text-gray-800 truncate">{docTitle(doc)}</span>
        {doc.date && (
          <span className="text-xs text-gray-500 shrink-0">{formatDate(doc.date)}</span>
        )}
      </span>
      {doc.description && doc.description !== docTitle(doc) && (
        <span className="text-xs text-gray-500 block mt-0.5 truncate">
          {doc.description}
        </span>
      )}
    </span>
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
      ]}
    />
  )
}
