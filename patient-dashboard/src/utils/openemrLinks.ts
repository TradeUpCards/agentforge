/**
 * Helpers that produce out-links to legacy OpenEMR PHP pages.
 *
 * Used by MainNav, OpenTabBar, EncounterSelector, SectionStubPage, and
 * each card's edit-pencil. Centralized here so the routing decisions are
 * one-stop-shop for review.
 *
 * ----------------------------------------------------------------------
 * Why some links go to specific pages and others go to main_screen.php
 * ----------------------------------------------------------------------
 *
 * Two real constraints drive this:
 *
 *  1) **Multi-tenant `site` parameter is required.** OpenEMR's PHP pages
 *     bounce to a "Site ID missing" error when `?site=<name>` is absent
 *     and there is no active PHP session. Every URL produced here MUST
 *     include `?site=default` (override via VITE_OPENEMR_SITE).
 *
 *  2) **We have FHIR UUIDs, not the legacy integer `pid`.** OpenEMR's
 *     patient-scoped PHP pages (e.g. patient history, edit-allergy
 *     forms, document viewer) take an integer pid as `?set_pid=<int>`.
 *     The FHIR Patient resource exposes a UUID; OpenEMR does not
 *     publicly expose a UUID->pid resolver endpoint. Without that
 *     resolver, we cannot deep-link directly into a specific patient's
 *     edit form. The user must land at OpenEMR's main shell and select
 *     the patient there (whose context is then carried by the PHP
 *     session for subsequent clicks).
 *
 * Implications:
 *
 *  - **Patient-agnostic destinations** (Calendar, Patient Finder,
 *    Messages, Reports) → DEEP-LINK directly to their PHP page.
 *    These don't need pid, so the user lands exactly where they expected.
 *
 *  - **Patient-scoped destinations** (card edit pencils, History,
 *    Documents, etc.) → ROUTE TO main_screen.php. The user re-selects
 *    the patient in OpenEMR. Honest scope statement; documented in the
 *    interface (the SectionStubPage explicitly says "Open in OpenEMR").
 *
 *  - **Visit History** is patient-scoped but uses OpenEMR's session pid:
 *    if the user has selected a patient in their PHP session at any
 *    point in this browser, that session pid resolves and they land on
 *    the right encounters page. We deep-link `encounters.php` and
 *    accept the session-resolution semantics.
 *
 *  - **Dropdown-style menu items** (Flow, Recalls, Fees, Modules,
 *    Procedures, Admin, Miscellaneous, Popups) are sub-menus in legacy
 *    OpenEMR with no single destination URL — clicking the label opens
 *    a dropdown of further choices. Routing them to `main_screen.php`
 *    re-exposes the same menu in OpenEMR's UI, so the user picks the
 *    same sub-item there. No deep-link is meaningfully better.
 *
 * Future work: a tiny PHP shim endpoint that accepts a UUID and
 * 302-redirects to the resolved-pid URL would unblock pid-aware
 * deep-linking. That's a change to the legacy module, out of scope
 * for the React dashboard.
 */

const OPENEMR_BASE =
  import.meta.env.VITE_OPENEMR_BASE_URL ?? 'https://localhost:9300'

const OPENEMR_SITE = import.meta.env.VITE_OPENEMR_SITE ?? 'default'

/** Append `?site=<name>` (or `&site=<name>` if the path already has a query). */
function withSite(path: string): string {
  const sep = path.includes('?') ? '&' : '?'
  return `${OPENEMR_BASE}${path}${sep}site=${encodeURIComponent(OPENEMR_SITE)}`
}

// --- Patient-agnostic destinations: deep-link directly ---------------------

/** OpenEMR application home / fallback for patient-scoped or sub-menu items. */
export const openemrHome = (): string =>
  withSite('/interface/main/main_screen.php')

/** Calendar / appointments. */
export const openemrCalendar = (): string =>
  withSite('/interface/main/calendar/index.php?module=PostCalendar')

/** Patient finder / picker (legacy "Finder" + "Patient" menu items, plus tab). */
export const openemrPatientFinder = (): string =>
  withSite('/interface/main/finder/dynamic_finder.php')

/** Internal messages center. */
export const openemrMessages = (): string =>
  withSite('/interface/main/messages/messages.php')

/** Reports landing. */
export const openemrReports = (): string =>
  withSite('/interface/reports/index.php')

// --- Patient-scoped destinations: rely on session pid -----------------------

/** Visit history page; uses session pid (set elsewhere in OpenEMR). */
export const openemrVisitHistory = (): string =>
  withSite('/interface/patient_file/history/encounters.php')

// --- Edit / SubNav fallback: routes to main shell --------------------------

/**
 * Edit form for a clinical resource type. Currently routes to the main
 * shell (see header note about UUID->pid). The resource hint is
 * preserved so a future UUID-aware OpenEMR could honor it.
 */
export const openemrEditLink = (
  resource:
    | 'allergy'
    | 'medical_problem'
    | 'medication'
    | 'prescription'
    | 'care_team'
    | 'encounter'
    | 'document',
): string => {
  void resource
  return openemrHome()
}

/**
 * Top-app menu deep-links by label. Used by MainNav and OpenTabBar.
 * Items not enumerated here fall back to the main shell.
 */
export const openemrMenuLink = (label: string): string => {
  switch (label) {
    case 'Calendar':
      return openemrCalendar()
    case 'Finder':
    case 'Patient':
    case 'Patient Finder':
      return openemrPatientFinder()
    case 'Messages':
    case 'Message Center':
      return openemrMessages()
    case 'Reports':
      return openemrReports()
    case 'Visit History':
      return openemrVisitHistory()
    default:
      // Flow, Recalls, Fees, Modules, Procedures, Admin, Miscellaneous,
      // Popups — all dropdown-style menus in legacy with no canonical
      // single-destination URL. main_screen.php re-exposes the same
      // dropdown so the user can pick the sub-item there.
      return openemrHome()
  }
}
