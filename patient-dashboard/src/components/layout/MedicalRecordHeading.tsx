import { usePatient } from '../../hooks/usePatient'
import { getPatientDisplayName } from '../../utils/fhirParsers'

/**
 * Page heading band that mirrors the legacy
 * "Medical Record Dashboard - {Name}" line directly below the
 * application tabs, with the legacy chat / minimize / help icon trio
 * floated to the right.
 *
 * Visual familiarity only: the icons are decorative. Implementing
 * chat, minimize, and contextual help would require deeper integration
 * with OpenEMR's UI shell, which is out of scope.
 */
export function MedicalRecordHeading({ patientId }: { patientId: string }) {
  const { data: patient } = usePatient(patientId)
  const name = patient ? getPatientDisplayName(patient) : '…'

  return (
    <div className="bg-white px-4 py-2 border-b border-gray-200 flex items-center justify-between gap-3">
      <p className="text-base font-semibold text-gray-800 m-0 truncate flex-1 min-w-0">
        Medical Record Dashboard - {name}
      </p>
      <div className="flex items-center gap-3 text-gray-500" aria-hidden="true">
        {/* Chat icon hidden on phone portrait specifically — that's the
            viewport where it's hardest to tap reliably and where the
            floating FAB at bottom-right is the obvious primary launcher.
            On wider/landscape viewports, the chat icon stays for visual
            familiarity with the legacy heading-bar trio. */}
        <span className="max-md:portrait:hidden inline-flex">
          <ChatIcon />
        </span>
        {/* The legacy minimize icon (fa-window-minimize, class
            "expand_contract") is a global "collapse all cards" button.
            We render per-row inline accordions on every card instead of
            a global expand/collapse, so the icon would be a false
            affordance — removed. The HelpIcon stays for visual
            familiarity with OpenEMR's standard page heading. */}
        <HelpIcon />
      </div>
    </div>
  )
}

function ChatIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor">
      <path d="M2.678 11.894a1 1 0 0 1 .287.801 11 11 0 0 1-.398 2c1.395-.323 2.247-.697 2.634-.893a1 1 0 0 1 .71-.074A8 8 0 0 0 8 14c3.996 0 7-2.807 7-6s-3.004-6-7-6-7 2.808-7 6c0 1.468.617 2.83 1.678 3.894M8 1c4.418 0 8 3.134 8 7s-3.582 7-8 7a9 9 0 0 1-2.347-.306c-.584.296-1.925.864-4.181 1.234-.2.032-.352-.176-.273-.362.354-.836.674-1.95.77-2.966C.744 11.37 0 9.76 0 8c0-3.866 3.582-7 8-7" />
    </svg>
  )
}

function HelpIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor">
      <path d="M8 15A7 7 0 1 1 8 1a7 7 0 0 1 0 14m0 1A8 8 0 1 0 8 0a8 8 0 0 0 0 16" />
      <path d="M5.255 5.786a.237.237 0 0 0 .241.247h.825c.138 0 .248-.113.266-.25.09-.656.54-1.134 1.342-1.134.686 0 1.314.343 1.314 1.168 0 .635-.374.927-.965 1.371-.673.489-1.206 1.06-1.168 1.987l.003.217a.25.25 0 0 0 .25.246h.811a.25.25 0 0 0 .25-.25v-.105c0-.718.273-.927 1.01-1.486.609-.463 1.244-.977 1.244-2.056 0-1.511-1.276-2.241-2.673-2.241-1.267 0-2.655.59-2.75 2.286m1.557 5.763c0 .533.425.927 1.01.927.609 0 1.028-.394 1.028-.927 0-.552-.42-.94-1.029-.94-.584 0-1.009.388-1.009.94" />
    </svg>
  )
}
