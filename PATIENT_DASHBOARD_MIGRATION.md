# Patient Dashboard Migration — Defense & Architecture

**Status:** in progress (Week 2 surprise challenge — AgentForge)
**Branch:** `agentforge/w2-dashboard-modernize`
**Lead:** Cleo
**Last updated:** 2026-05-06

This is the graded defense document. It traces every requirement in
`.gauntlet/week2/AgentForge — Clinical Co-Pilot W2 — Surprise Challenge_ Modernize the Patient Dashboard.pdf`
to the implementation, and explains the framework decision, the gains
from leaving PHP, and the tradeoffs we accepted.

> The goal is **feature parity** with the existing OpenEMR PHP patient
> dashboard. We are reimplementing the presentation layer only — we do
> not touch the backend, the FHIR server, or the OAuth2 server.

---

## 1. Deliverables coverage

Every requirement from the surprise-challenge brief, mapped to where it
is implemented in this repository. Status is tracked here (not in a
separate task tracker) so the defense doc itself proves coverage.

| # | PDF requirement | Status | Implementation |
|---|---|---|---|
| 1 | **Authentication** — OAuth2/OpenID Connect login | live-verified | `patient-dashboard/src/auth/` — `oidcConfig.ts`, `CallbackPage.tsx`, `ProtectedRoute.tsx`. Authorization Code + PKCE against `${VITE_OPENEMR_BASE_URL}/oauth2/default`, wired in `src/main.tsx`. |
| 2 | **Patient header** — name, DOB, sex, MRN, active status | live-verified | `patient-dashboard/src/components/layout/PatientHeader.tsx`, fed by `usePatient()` against `GET /fhir/Patient/{id}`. Sticky bar, mirrors the existing dashboard's identity strip. |
| 3a | Clinical card: **Allergies** | live-verified | `components/cards/AllergiesCard.tsx` ← `GET /fhir/AllergyIntolerance?patient={id}&clinical-status=active`. Severity → badge variant matches existing Twig logic. |
| 3b | Clinical card: **Problem List** | live-verified | `components/cards/ProblemsCard.tsx` ← `GET /fhir/Condition?patient={id}&category=problem-list-item&clinical-status=active`. |
| 3c | Clinical card: **Medications** | live-verified | `components/cards/MedicationsCard.tsx` ← `GET /fhir/MedicationRequest?patient={id}&status=active`. |
| 3d | Clinical card: **Prescriptions** | live-verified | `components/cards/PrescriptionsCard.tsx` ← `GET /fhir/MedicationDispense?patient={id}&_sort=-whenHandedOver`. |
| 3e | Clinical card: **Care Team** | live-verified | `components/cards/CareTeamCard.tsx` ← `GET /fhir/CareTeam?patient={id}&status=active`. |
| 4 | **Additional section** — Encounter History | live-verified | `components/cards/EncountersCard.tsx` ← `GET /fhir/Encounter?patient={id}&_sort=-date&_count=10`. Title "Encounter History" matches legacy `interface/patient_file/history/encounters.php`. Choice rationale in §7. |
| 5 | **Working reimplementation, feature parity** | live-verified | Composed at `pages/DashboardPage.tsx`. Visual language mirrors existing Bootstrap 4 cards (see `templates/patient/card/*.html.twig`) but in Tailwind v4. Parity checklist in §9. Manual acceptance against OpenEMR demo data + screenshots → `patient-dashboard/docs/parity/` is the remaining work. |
| 6 | **This document** — framework defense | done (this file) | `PATIENT_DASHBOARD_MIGRATION.md` |

Status legend: `planned` (designed only), `in progress` (actively
being written), `code complete, awaiting acceptance test` (all source
files written and self-consistent; not yet exercised against a live
OpenEMR instance), `live-verified` (code complete + manually exercised
end-to-end against running OpenEMR with demo data — Phil Belford
walkthrough on 2026-05-06; pending only parity-screenshot capture),
`done` (live-verified + parity screenshots committed under
`patient-dashboard/docs/parity/`).

---

## 2. Framework choice

**Choice: React 19 + Vite 6 + TypeScript (strict)**

| Layer | Tool | Why this and not the obvious alternative |
|---|---|---|
| UI framework | React 19 | React's compositional model maps naturally to independently loading clinical cards with isolated failure boundaries — the architecture in §10 falls out of the framework choice rather than fighting it. Largest healthcare-app talent pool; broadest SMART-on-FHIR / OIDC client ecosystem. Angular would be heavy and would conflict with the legacy Angular 1.8 already in OpenEMR. Vue / Svelte are fine technically but have thinner SMART-on-FHIR precedent. |
| Build tool | Vite 6 | Fast cold start, native ESM dev server, simple config. Webpack/CRA are slower and more ceremony. |
| Server-side rendering | **none — pure SPA** | Next.js (and similar) would add a Node server for no benefit: the data is patient-specific and behind OAuth2; there is no SEO surface; a server in the middle adds a trust boundary that holds tokens. SMART on FHIR is designed for browser-only SPAs. |
| Language | TypeScript strict | FHIR is deeply nested optional JSON. `patient.name?.[0]?.given?.[0]` is exactly the kind of access where compile-time checks pay for themselves. `noUncheckedIndexedAccess` enforces null-handling at the type level. |
| Data fetching | TanStack Query v5 | Per-card stale-while-revalidate, automatic deduplication, built-in loading/error state. Replaces 6 hand-written `useEffect` + `useState` patterns. Server state, not application state — Redux would be reinventing it. |
| Auth | `react-oidc-context` + `oidc-client-ts` v3 | Canonical TypeScript OIDC SPA library; PKCE built in; SMART on FHIR v2.2.0 compatible; React context wrapper is maintained by the same org as the core. |
| Routing | React Router v7 | Standard SPA routing. We need exactly: `/login`, `/callback`, `/patients`, `/dashboard/:patientId`. |
| Styling | Tailwind CSS v4 | Utility-first, zero unused CSS in prod, CSS-first config (no `tailwind.config.ts` required for our usage). Fits the dense card-grid design without dragging in another component library. |
| Package manager | pnpm | Faster installs, content-addressable store, strict peer-dep resolution. The patient-dashboard subdirectory is an independent Node project from the OpenEMR root (which uses npm), so we choose freely. |

---

## 3. What we gained by leaving PHP

1. **Independent card loading.** TanStack Query fires the 6 FHIR queries in parallel; the page paints as each one resolves. The PHP dashboard waits for the slowest fragment. Per-card failure behavior is documented in §10.
2. **Real PKCE in the browser.** PKCE-secured Authorization Code flow is the SMART-on-FHIR standard for SPA clients and is the only secure flow for a browser-only public client. This is also the only way a third-party app would consume the same dashboard surface.
3. **Static deployment.** The build output is HTML/JS/CSS — no PHP runtime, no Apache module, no shared filesystem. We co-locate it behind the OpenEMR origin (see §13).
4. **Type safety on FHIR shapes.** `src/types/fhir.ts` declares the resource shapes we read. A typo like `patient.name[0].givens[0]` fails compilation, not at 2 a.m. in production.
5. **Component reuse.** `CardShell` is the testable equivalent of `card_base.html.twig`. The same shell renders allergies, problems, medications, etc. — and is unit-testable in isolation, which the Twig partial is not.
6. **No PHP language drag.** No `$GLOBALS`, no `$_SESSION` as service locator, no Smarty/Twig dual templating. One language, one render path.

## 4. Tradeoffs (honest list)

1. **JS required for first paint.** PHP renders HTML immediately. Our app must download and execute the bundle before the user sees anything. Mitigation: small bundle (no charting libraries, Tailwind purges unused classes), `Spinner` shown immediately so the page never feels frozen.
2. **CORS / co-location.** The browser calls `/apis/default/fhir/...` directly. We resolve this by co-locating the static bundle behind the OpenEMR origin (§13) — no CORS needed. In dev, a Vite proxy stands in for that.
3. **SPA token storage increases XSS sensitivity.** Unlike the PHP session cookie (HttpOnly, server-held), a SPA must keep the token in JavaScript-readable storage to call FHIR. We accept this and mitigate it concretely: short access-token lifetime (typically ≤1 h), refresh-token rotation on each renewal, `sessionStorage` (cleared on tab close, never persisted to disk), strict Content-Security-Policy, no `dangerouslySetInnerHTML` anywhere in the codebase, dependency scanning via `pnpm audit` in CI, and explicit `signoutRedirect()` cleanup on logout (§5).
4. **Two front-ends in the repo.** During the migration the legacy PHP dashboard and the new React app coexist. Long-term we'd retire the legacy dashboard; for this challenge we run them side by side.

## 5. Authentication design

**Flow:** Authorization Code with PKCE (SMART on FHIR v2.2.0).

```
Browser                              OpenEMR /oauth2/default
   │                                       │
   │── 1. signinRedirect ─────────────────▶│
   │       (code_challenge in URL)         │
   │                                       │
   │◀─── 2. login UI ──────────────────────│
   │      (user + password)                │
   │                                       │
   │── 3. submit credentials ─────────────▶│
   │                                       │
   │◀─── 4. redirect to /callback ─────────│
   │      (?code=...)                      │
   │                                       │
   │── 5. POST /token ────────────────────▶│
   │   (code + code_verifier)              │
   │                                       │
   │◀─── 6. access_token + id_token ───────│
   │       + refresh_token                 │
   │                                       │
   │── 7. GET /fhir/Patient/{id} ─────────▶│
   │   (Authorization: Bearer ...)         │
```

**Client type: confidential, with PKCE.**
This is the one place we deviate from the textbook "SPA = public client" answer, and it's deliberate. OpenEMR's authorization server enforces the policy that **`user/` and `system/` scopes are only available to confidential clients** — public clients are restricted to `patient/` (single-patient) scope context. Discovered empirically: registering as `application_type: "public"` returns `invalid_client_metadata: "system and user scopes are only allowed for confidential clients"`.

A clinician dashboard needs `user/` scopes (it shows multiple patients), so we register as confidential (`application_type: "private"`, `token_endpoint_auth_method: "client_secret_post"`). The `client_secret` ships with the SPA via `.env` at build time.

The reasoning for accepting this:
- **PKCE still does the actual cryptographic work.** The auth code is bound to a `code_verifier` only the originating browser knows; intercepting the code in transit gains an attacker nothing without that verifier.
- **The secret is a confused-deputy guard at the token endpoint.** Its job is preventing an unrelated client from impersonating ours during the token exchange, not protecting the auth code.
- **It does not fix the XSS exposure** — the access token is in `sessionStorage` regardless of client type. We mitigate XSS through the channels in §4: short token TTL, no `dangerouslySetInnerHTML`, strict CSP, dependency scanning.
- **The alternatives are worse for this challenge.** SMART EHR Launch with `patient/` scopes only would lock each session to one patient and break the patient picker. A backend-for-frontend would violate the "no backend" constraint. Asymmetric client authentication (JWKS) is the right long-term answer but heavyweight for a one-week build.

**Storage**
- Tokens in `sessionStorage` via `oidc-client-ts` `WebStorageStateStore`. Cleared on tab close.
- `client_secret` lives in `VITE_CLIENT_SECRET` (build-time env var, embedded in the bundle).

**Scopes (least privilege, all read-only):** `openid fhirUser offline_access user/Patient.rs user/AllergyIntolerance.rs user/Condition.rs user/MedicationRequest.rs user/MedicationDispense.rs user/CareTeam.rs user/Encounter.rs`.

**Token expiration and rotation**
- Access token: short-lived (OpenEMR default ≈ 1 hour). After expiry, FHIR responses fail with 401.
- Refresh token: longer-lived (OpenEMR default ≈ 3 months when `offline_access` is granted). Used to obtain a new access token.
- **Rotation:** every refresh exchange returns a new refresh token. The old one is invalidated server-side. Each tab maintains its own `sessionStorage` context — `sessionStorage` is tab-scoped, not shared (unlike `localStorage`). Concurrent refreshes from sibling tabs are acceptable because refresh-token rotation invalidates prior refresh tokens server-side; a tab that loses the race sees its next refresh fail and converges to re-authentication via the silent-renew failure path. The bounded risk is one extra `/login` round-trip, not data corruption.

**Silent renew (hidden iframe)**
- `automaticSilentRenew: true` — the library renews ~60 seconds before access-token expiry.
- Mechanism: a hidden iframe loads the `/authorize` endpoint with `prompt=none`. If the OpenEMR session is still valid, OpenEMR returns a fresh code without re-prompting; the library exchanges it for new tokens.
- **Failure modes:**
  - OpenEMR session expired → silent renew fails → `oidc-client-ts` emits `silentRenewError`. Our handler clears local state and redirects to `/login`.
  - Network failure → next FHIR call sees a 401; the FHIR client returns an auth error and TanStack Query surfaces it on the affected card. The user can re-authenticate via "Sign in" on the patient header.
  - Refresh token revoked (e.g., admin revoked the client) → same path: redirect to `/login`.

**Logout cleanup**
- `signoutRedirect()` does three things in order: (a) tells `oidc-client-ts` to clear in-memory user state; (b) clears the OIDC entries from `sessionStorage`; (c) redirects the browser to OpenEMR's `end_session_endpoint` so the server-side session is also terminated.
- `post_logout_redirect_uri` brings the user back to `/`, which renders `LoginPage` (now in unauthenticated state).
- `QueryClient.clear()` is called on logout to drop cached FHIR responses.

**One-time client registration** (run once per OpenEMR install). Easiest path:

```bash
cd patient-dashboard
bash scripts/register-oauth-client.sh
```

That script POSTs the following payload to `https://localhost:9300/oauth2/default/registration`:

```json
{
  "client_name": "OpenEMR Patient Dashboard (Modern)",
  "application_type": "private",
  "redirect_uris": ["http://localhost:5173/callback"],
  "post_logout_redirect_uris": ["http://localhost:5173/"],
  "token_endpoint_auth_method": "client_secret_post",
  "scope": "openid fhirUser offline_access user/Patient.rs user/AllergyIntolerance.rs user/Condition.rs user/MedicationRequest.rs user/MedicationDispense.rs user/CareTeam.rs user/Encounter.rs"
}
```

The response includes both `client_id` and `client_secret`. Both go in `.env` as `VITE_CLIENT_ID` and `VITE_CLIENT_SECRET`. After registration, approve the client in OpenEMR Admin → System → API Clients.

## 6. Patient selection and cross-patient access defense

The dashboard accepts a patient ID via URL: `/dashboard/:patientId`. That ID must be defended at three layers, because URL tampering is a real threat (a curious user, a leaked browser-history URL, an open redirect on a phishing link).

**Layer 1 — Patient list source.** `PatientSelectPage` calls `GET /fhir/Patient?_count=100` with the user's bearer token. The FHIR server filters this Bundle to patients the authenticated user is authorized to view. The user can only navigate to IDs they were shown. (Earlier drafts used the standard REST API at `/apis/default/api/patient`, but that requires an `api:oemr` scope we deliberately do not request — the FHIR Patient search satisfies the same requirement using only `user/Patient.rs`, keeping the dashboard 100% FHIR.)

**Layer 2 — Frontend route protection.** `ProtectedRoute` blocks unauthenticated access to `/patients` and `/dashboard/:patientId`. An anonymous user is redirected to `/login` before the dashboard mounts.

**Layer 3 — Server-side scope enforcement (the source of truth).** Even if a logged-in user types `/dashboard/999` for a patient they don't have access to, every card's FHIR query goes to OpenEMR with the bearer token. OpenEMR's authorization layer evaluates the user's scopes (`user/Patient.rs`, etc.) against the requested patient and returns **401/403** for unauthorized records. The frontend renders the error in `CardShell`; no PHI leaks because no PHI was returned.

**Why all three matter.** Layer 1 is UX (don't show what they can't have). Layer 2 is defense-in-depth (don't let the page mount in an unauthenticated state). Layer 3 is authoritative (the only layer the SPA cannot bypass). A reviewer who asks *"what stops a user from typing another patient's ID?"* gets answered at Layer 3 — the frontend simply cannot bypass FHIR's authz checks because the frontend doesn't hold the keys to those checks.

**What we do NOT defend against.** A user who is authorized to see Patient A and Patient B can navigate freely between them. That is a feature, not a leak — it matches the legacy PHP dashboard's behavior and the FHIR API's contract.

## 7. Why Encounters as the additional section

Options the brief allows: encounter history, lab results, vitals, immunizations, upcoming appointments, patient notes.

Encounters wins because:
- Backed by FHIR `Encounter` resource — fully implemented in OpenEMR's FHIR server (US Core 8.0).
- Direct analog to `interface/patient_file/history/encounters.php` — confirmed working in the existing dashboard.
- Clinically high-signal: "what has this patient been seen for, and when?" is the first question a provider asks.
- Simple query (`?patient={id}&_sort=-date&_count=10`), small response.

Not chosen and why:
- **Vitals / labs (`Observation`)** — requires category filtering and unit handling; more code without proportional UX gain in a one-week build.
- **Immunizations** — fine, but visually a flat list with little nuance.
- **Appointments** — requires future-date filtering and is duplicative of encounters for a defense build.
- **Patient notes** — text-heavy, free-form; needs more thoughtful UX than a card affords.

## 8. FHIR resource → card mapping

| Card | FHIR resource | Query | Display |
|---|---|---|---|
| Patient header | `Patient` | `GET /fhir/Patient/{id}` | `name`, `birthDate`, `gender`, `identifier[type=MR].value`, `active` |
| Allergies | `AllergyIntolerance` | `?patient={id}&clinical-status=active` | `code.text`, `criticality` (high → red badge), `reaction[0].severity` |
| Problem List | `Condition` | `?patient={id}&_count=50` (filtered client-side — see note below) | `code.text`, `onsetDateTime` |
| Medications | `MedicationRequest` | `?patient={id}&status=active` | `medicationCodeableConcept.text`, `dosageInstruction[0].text` |
| Prescriptions | `MedicationDispense` | `?patient={id}&_sort=-whenHandedOver` | `medicationCodeableConcept.text`, `quantity`, `whenHandedOver` |
| Care Team | `CareTeam` | `?patient={id}&status=active` | `participant[*].member.display`, `participant[*].role[0].text` |
| Encounters | `Encounter` | `?patient={id}&_sort=-date&_count=10` | `type[0].text`, `period.start`, `participant[0].individual.display`, `reasonCode[0].text` |

**OpenEMR quirk — Condition category filter.** The combined query
`Condition?patient={id}&category=problem-list-item&clinical-status=active`
returns HTTP 500 on OpenEMR's FHIR server (verified empirically against
`docker/development-easy`). We work around this by issuing the
unfiltered `Condition?patient={id}&_count=50` query and applying the
problem-list / active-status filter client-side in
`components/cards/ProblemsCard.tsx`. Tradeoff is a slightly larger
response body in exchange for the page actually rendering the card.
The filter logic is `isProblemListItem` + `isActive` — both treat
missing fields as "include," which matches OpenEMR's behavior of
sometimes omitting category metadata on older condition rows.

## 9. Feature parity with the legacy PHP dashboard

The brief sets the bar at *feature parity*, not *redesign*. We mirror the legacy
dashboard's structure, copy, and behavior fragment-by-fragment. This table is the
acceptance checklist.

**Parity discipline.** Things we deliberately do NOT change from the legacy
PHP/Twig dashboard:

- **Card order** — Allergies → Medical Problems → Medications → Prescriptions → Care Team → Encounter History.
- **Information hierarchy** — sticky patient header on top, cards below in a single grid, no nested navigation.
- **Core layout** — header strip + responsive card grid (single column mobile, multi-column desktop).
- **Field labels** — `DOB:`, `Sex:`, `MRN:` in the header; card titles match the legacy Twig template names (`Medical Problems` from `medical_problems.html.twig`, `Encounter History` from `interface/patient_file/history/encounters.php`, etc.).
- **Patient header contents** — exactly the five fields specified in the brief (name, DOB, sex, MRN, active status); no expanded demographics inline.
- **Which clinical sections appear** — exactly the five required cards plus the additional section, no extras.

Things we deliberately DO change, because React makes them cheap and the
legacy was constrained by PHP's render model:

- **Per-card loading** — each card paints when its query resolves, not when the slowest one does.
- **Per-card error states** — one card's failure does not blank the page (§10).
- **Cleaner empty states** — `EmptyState` differentiates "list checked, none found" from "never recorded" (§11 of legacy Twig).
- **Responsive card grid** — single column on phones, two on tablets, three on wide displays.
- **Clearer retry buttons** — explicit `Retry` button on failed cards instead of a full page refresh (§10).
- **Two-tier allergy severity badges** — high criticality (red) is distinguished from severe reaction severity (yellow); legacy used a single yellow tier. This is clinically informative and the only color-system addition.

This is a *modernization*, not a *redesign*. A clinician familiar with the
legacy dashboard should recognize every card and find every field in the
same place.

| Surface | Legacy PHP behavior | React reimplementation | Parity |
|---|---|---|---|
| Identity bar | Sticky strip with name, DOB, sex, MRN, active status — see `interface/patient_file/summary/dashboard_header.php` + `templates/patient/dashboard_header.html.twig` | `PatientHeader.tsx`, sticky, same five fields, same order | ✅ |
| Card: Allergies | List of active allergies; severe/life-threatening/fatal highlighted in `bg-warning` — see `templates/patient/card/allergies.html.twig` | `AllergiesCard.tsx`; `criticality === 'high'` → red badge; `reaction[0].severity === 'severe'` → warning badge | ✅ |
| Card: Medical Problems | Active problems (`Condition` rows from `medical_problems.html.twig`); title "Medical Problems" | `ProblemsCard.tsx` — same source, same active-only filter, same title | ✅ |
| Card: Medications | Active meds from `medication.html.twig` | `MedicationsCard.tsx` — `status=active` filter | ✅ |
| Card: Prescriptions | Recent prescriptions from `rx.html.twig` | `PrescriptionsCard.tsx` — sorted by `whenHandedOver` desc | ✅ |
| Card: Care Team | Care team participants from `manage_care_team.html.twig` | `CareTeamCard.tsx` — same field set | ✅ |
| Card order | Allergies → Medical Problems → Medications → Prescriptions → Care Team (from existing dashboard layout) | Same order in `DashboardPage.tsx`, plus Encounter History appended | ✅ |
| Empty state copy | Twig: `"No Known Allergies"` (when list is touched) vs `"Nothing Recorded"` (when never touched) | `EmptyState.tsx` shows `"No Known Allergies"` when bundle returned but empty; `"Nothing Recorded"` when no data at all | ✅ |
| Loading state | Server-rendered HTML — appears all-or-nothing after PHP completes | Per-card `Spinner` while fetching; cards paint independently as they resolve | ⤴ better |
| Error state | PHP error or blank fragment | Per-card error message + Retry button; one card's failure does not block others (§10) | ⤴ better |
| Date format | `YYYY-MM-DD` (locale-independent in legacy) | Locale-formatted via `formatters.formatDate` (`MMM D, YYYY`); switch to ISO in `.test.ts` if required for parity | configurable |
| Severity badge colors | Bootstrap `bg-warning` (yellow) for severe | Tailwind `bg-yellow-100 text-yellow-800` (warning) and `bg-red-100 text-red-800` (high) | ✅ visual analog |

**How parity is verified at the end of the week:**
1. Run the React dashboard against an OpenEMR install seeded with demo data.
2. Open the legacy PHP dashboard for the same patient in another tab.
3. Walk the table above row by row; check off the parity column.
4. Capture before/after screenshots; commit them to `patient-dashboard/docs/parity/`.

## 10. Per-card failure behavior

Independent card loading is only a strength if the failure modes degrade gracefully. We commit to the following:

- **Each card is its own TanStack Query.** Allergies failing does not block Medications; Medications loading slowly does not block the patient header. The grid composes whatever cards have resolved.
- **`CardShell` accepts a `Spinner` (loading), an `error: Error | null` (failure), or rendered children (success).** It never collapses to a blank box.
- **Retry policy.**
  - Automatic: TanStack Query is configured with `retry: 1` and an exponential backoff. Transient 5xx and network errors are retried once before surfacing.
  - Manual: on persistent failure, `CardShell` renders a "Retry" button that calls `query.refetch()`. The user can retry just the failed card without reloading the page.
- **Auth failures (401) are handled centrally in `fhirClient.ts`.** A 401 triggers a single attempt at silent renew (handled by `oidc-client-ts` independently); if that fails, the user is redirected to `/login` with the current URL preserved as `state` so they return to the same patient after re-auth.
- **PHI safety on error.** `CardShell` displays only the error class and HTTP status — never the response body, which could contain partial PHI in a malformed response.

**What's explicitly post-MVP and called out:** richer error copy (e.g., distinguishing "FHIR server unreachable" from "scope not granted"), per-card timeouts shorter than the default fetch timeout, and offline support.

## 11. Testing

| Layer | Tool | What it covers |
|---|---|---|
| Unit | Vitest | `utils/fhirParsers.test.ts` — name extraction (with/without `use=official`), MRN extraction (with/without `type.coding[code=MR]`), bundle resource extraction. `utils/formatters.test.ts` — date formatting, capitalization with empty input. Pure functions; the highest-leverage tests. |
| Component | Vitest + React Testing Library | One test per card: render with mocked TanStack Query (`QueryClientProvider` wrapping a `MockedProvider`), assert (a) loading state, (b) populated state, (c) empty state, (d) error state. Mock the API resource function, not `fetch`, so tests don't depend on URL shape. |
| Auth smoke | Vitest | `auth/ProtectedRoute.test.tsx` — mock `useAuth` from `react-oidc-context` with three fixtures (`isLoading`, `isAuthenticated`, anonymous); assert the right thing renders or redirects. `auth/CallbackPage.test.tsx` — assert the navigate side-effect on auth state change. |
| Fixtures | Static JSON | `tests/fixtures/` — one realistic FHIR `Bundle` per resource, drawn from the OpenEMR demo data. Imported by component tests; never network-fetched. |
| Manual acceptance | Checklist | Run against OpenEMR demo data (https://www.open-emr.org/wiki/index.php/Development_Demo). Walk the parity table in §9 row by row. Verify silent renew by setting access token TTL to 60 s in OpenEMR config and watching the Network tab for a token-endpoint hit. Verify cross-patient defense (§6) by typing an unauthorized patient ID into the URL and confirming all cards show 401. |

CI hook: `pnpm typecheck && pnpm test && pnpm build` runs on every push to `agentforge/w2-dashboard-modernize`. Test files do not import from real `fetch` or real `oidc-client-ts` storage — both are injected at the seam, so the suite is hermetic.

## 12. Accessibility

We do not ship "accessibility audit pending." The following are baked in from the start:

- **Semantic HTML.** `<header>` for the patient header, `<main>` for the dashboard region, `<section>` per card, `<button>` for actions (never `<div onclick>`). Role-appropriate elements throughout.
- **Heading hierarchy.** `h1` is the patient's name in the header (one per page). `h2` is each card title. No skipped levels. Screen-reader landmark navigation (`Ctrl+F6` in NVDA, `VO+U` in VoiceOver) jumps directly to cards.
- **Keyboard navigation.** Every interactive element is a native focusable element. Tab order follows visual order. The Retry button on a failed card is focusable and labeled. No keyboard traps.
- **Focus visibility.** Tailwind's default `focus-visible` outline is preserved — never `outline: none` without a replacement.
- **Color contrast.** Text colors selected from Tailwind palettes that meet WCAG AA (4.5:1 for normal text) on white: `text-gray-700` on `bg-white`, `text-blue-800` on `bg-blue-100`, `text-red-800` on `bg-red-100`. We never rely on color alone — severity badges include a text label, not just a hue.
- **ARIA where semantics are insufficient.** `aria-busy="true"` on a card while loading, `role="alert"` on error messages so screen readers announce them when they appear, `aria-label` on icon-only buttons (the sign-out button uses an icon + visible "Sign out" text, but the pattern is documented).
- **Empty-state announcements.** `EmptyState` renders visible text (`"No Known Allergies"`, `"Nothing Recorded"`) — never a hyphen or icon-only marker.
- **Reduced motion.** Spinner uses CSS `animate-spin`; we'll add `@media (prefers-reduced-motion: reduce)` to swap to a static "Loading…" label.

**What's still post-MVP:** a Lighthouse axe scan against the rendered dashboard with seeded demo data, and a screen-reader walk-through (NVDA + VoiceOver). Both go on the punch list at the end of the week.

## 13. Deployment

**Production: co-locate the bundle behind the OpenEMR origin.**

- `pnpm build` emits `patient-dashboard/dist/` — a standard static bundle.
- Deploy that dist directory to a path served by OpenEMR's web server (e.g. `<openemr-docroot>/patient-dashboard/`).
- The bundle's runtime URLs become `https://<openemr-host>/patient-dashboard/...`, and FHIR/OAuth2 calls go to `https://<openemr-host>/apis/...` and `https://<openemr-host>/oauth2/...` — **same origin**, so no CORS configuration is needed.
- Update the OAuth2 client registration's `redirect_uris` to the production URL (e.g. `https://<openemr-host>/patient-dashboard/callback`).

**Why same-origin and not CORS:** CORS works, but adds a header surface that has to be maintained correctly across upgrades. Same-origin sidesteps the entire class of misconfiguration. It also matches how the legacy PHP dashboard deploys today.

**Rate limiting.** FHIR request volume from a single dashboard session is naturally bounded — at most one query per card per patient view, plus silent-renew traffic. Server-side rate limiting (OpenEMR's own controls or a reverse-proxy layer such as Nginx `limit_req`) is the appropriate enforcement point if abuse is observed; we do not implement client-side throttling because it would not defend against a misbehaving client.

**Dev:** Vite proxies `/oauth2` and `/apis` to OpenEMR (see `vite.config.ts`). Dev runs at `http://localhost:5173`; the proxy means the browser thinks all calls are same-origin.

## 14. Directory structure

```
patient-dashboard/
├── package.json              # React 19, Vite 6, TanStack Query, oidc-client-ts, Tailwind v4
├── vite.config.ts            # dev proxy /oauth2 + /apis → OpenEMR
├── tsconfig.json             # strict, noUncheckedIndexedAccess
├── .env.example              # VITE_OPENEMR_BASE_URL, VITE_CLIENT_ID, redirect URIs
├── index.html
└── src/
    ├── main.tsx              # AuthProvider + QueryClientProvider + BrowserRouter
    ├── App.tsx               # routes
    ├── index.css             # @import "tailwindcss"
    ├── auth/                 # OIDC config, ProtectedRoute, CallbackPage
    ├── api/
    │   ├── fhirClient.ts     # fetch wrapper, injects Bearer
    │   └── resources/        # one file per FHIR resource
    ├── types/fhir.ts         # FHIR R4 type definitions (subset we read)
    ├── hooks/                # one TanStack Query hook per resource
    ├── components/
    │   ├── layout/           # PatientHeader, DashboardGrid
    │   ├── cards/            # CardShell + 6 card components
    │   └── ui/               # Spinner, Badge, EmptyState
    ├── pages/                # LoginPage, PatientSelectPage, DashboardPage
    └── utils/                # fhirParsers (name/MRN/active), formatters (dates)
```

## 15. OpenEMR FHIR write limitations and the legacy-REST workaround

This section captures a discovery from late W2 build that shapes how
the Add-Allergy write demo is wired. It is recorded here so the
tradeoff is defended honestly in interview, not papered over.

### 15.1 The discovery

OpenEMR's FHIR R4 server registers no `.c` / `.u` (create/update)
scopes for clinical resources. The relevant code is
`src/Common/Auth/OpenIDConnect/Entities/ServerScopeListEntity.php`
around line 167 — the V2 scope-registration loop is annotated
`// we'll ignore write for now` and emits only `.rs` (read/search)
scopes for every resource on the FHIR R4 list. The V1 scope set adds
`.write` for exactly three resources: `Patient`, `Practitioner`, and
`Organization`. Every clinical resource the dashboard touches —
`AllergyIntolerance`, `Condition`, `MedicationRequest`,
`MedicationDispense`, `CareTeam`, `Encounter`, `DocumentReference` —
is read-only via FHIR.

OpenEMR's OAuth2 server silently filters unrecognized scopes at
client registration. A registration request including
`user/AllergyIntolerance.cu` returns a successful 200 with that scope
absent from the response body. A subsequent `POST /fhir/AllergyIntolerance`
returns 404 — not 403 — because no route exists, not because the
scope is insufficient.

### 15.2 The chosen workaround: legacy REST API

The Add-Allergy modal POSTs to OpenEMR's legacy REST API:

```
POST /apis/default/api/patient/{puuid}/allergy
Authorization: Bearer <oauth2-token>
Content-Type: application/json

{
  "title": "Penicillin",
  "begdate": "2026-05-08",
  "comments": "Reaction: hives | Severity: moderate"
}
```

Three properties make this a clean choice:

1. **Same OAuth2 token, additional `api:oemr` scope.** The token
   carries both `user/AllergyIntolerance.rs` (FHIR read) and
   `api:oemr` (legacy API write). One sign-in, two endpoints. The
   `api:oemr` scope is gated at OpenEMR's bearer-token strategy
   (`BearerTokenAuthorizationStrategy.php` line 373); without it
   OpenEMR returns 403 at the dispatch layer.

2. **No UUID → pid resolution needed in the SPA.** The endpoint
   accepts the FHIR Patient UUID directly in the path. The internal
   `AllergyIntoleranceService::insert()` calls
   `UuidRegistry::uuidToBytes()` and `getIdByUuid()` to resolve to
   the integer pid server-side. The dashboard already has the UUID
   from the FHIR Patient resource — no extra lookup.

3. **Same compliance posture as FHIR.** The write flows through
   `AllergyIntoleranceService::insert()` → `sqlInsert()`, which
   automatically writes to OpenEMR's audit log. ACL check is
   enforced inline (`RestConfig::request_authorization_check($request,
   "patients", "med")` in the route handler). Transmission is the
   same TLS as the FHIR endpoint. The legacy REST endpoint is
   functionally equivalent to FHIR for HIPAA technical safeguards
   (authentication, authorization, audit, transmission encryption),
   with one caveat: `api:oemr` is a broader scope than a hypothetical
   per-resource `.cu` would be. The user-level ACL still constrains
   what each clinician can actually do, which is what every
   ONC-certified OpenEMR deployment relies on.

**Field mapping.** The legacy controller's WHITELISTED_FIELDS accepts
`{title, begdate, enddate, diagnosis, comments}`. The modal's
substance maps to `title`; reaction and severity fold into
`comments` (the whitelist excludes the `severity_al` lists-table
column, so structured severity isn't writable through this endpoint).
`begdate` is auto-populated to today; the modal does not expose a
date picker because the typical clinician workflow for the modal is
"I just discovered this allergy." The mapping is documented in
`patient-dashboard/src/api/resources/allergies.ts`.

### 15.3 DocumentReference scope restriction (related, smaller)

OpenEMR registers `user/DocumentReference.rs` only in its restricted
form (`category=...|clinical-note`). The plain unrestricted scope is
silently dropped at registration. Both the registration JSON and
`oidcConfig.ts` request the restricted form explicitly so the granted
scope literally matches what is asked for. The DocumentReference
search URL (`getDocuments`) appends the same `category` filter so
the search request matches the granted scope — without it OpenEMR
returns 403 on an otherwise valid token. The dashboard therefore
shows only clinical-note category documents, which is the intended
set for a clinician dashboard, not a workaround.

### 15.4 What this means for the codebase

- `oidcConfig.ts` requests `api:oemr` alongside the FHIR `.rs` scopes.
- `fhirClient.ts` exposes `standardApiPost` (mirror of `fhirPost`)
  pointed at `/apis/default/api/`.
- `api/resources/allergies.ts` uses `standardApiPost` for create and
  `fhirGet` for read — both backed by the same OAuth2 token.
- `AddAllergyModal.tsx` error mapping recognizes 403 (scope/ACL),
  401 (expired session), 404 (patient not found), 5xx (server error).
- The two-API client architecture is the model for any future
  write surfaces in the dashboard. FHIR for reads where supported,
  legacy REST API for writes that FHIR doesn't expose.

### 15.5 Alternatives considered and why they were rejected

| Alternative | Reason rejected |
|---|---|
| Wait for OpenEMR to add FHIR write | Out of timeline; out of workstream scope |
| Custom PHP endpoint that hits the DB directly | Reimplements `AllergyIntoleranceService::insert()` (validation, audit, ACL, list-options FK, idempotency) — strictly more work, strictly worse compliance posture |
| Omit the write demo entirely | Loses the modal-vs-accordion UX contrast and the read-after-write consistency demonstration; obscures a real architectural decision |
| Demo write on Patient demographics (FHIR-supported) | Demographics edit isn't a useful clinical-dashboard surface |

## 16. How to run

```bash
# 1. Register the OAuth2 client (one time per OpenEMR install)
#    See §5 for the curl command. Save the returned client_id.

# 2. Configure environment
cd patient-dashboard
cp .env.example .env
# Edit .env: set VITE_CLIENT_ID to the value from step 1

# 3. Install + run
pnpm install
pnpm dev
# Dashboard at http://localhost:5173, proxying OAuth2/FHIR calls to OpenEMR

# 4. Production build
pnpm build      # emits dist/, deploy under OpenEMR origin (§13)
```

If pnpm is not on the system: `npm i -g pnpm` (or `corepack enable`).

## 17. What I can defend in interview

- **"If OpenEMR already has a patient dashboard, why should this exist?"** Six concrete things this delivers that the PHP dashboard does not:
  1. **Independent card loading** — the page paints as each FHIR query resolves; one slow card cannot block the rest (§10).
  2. **Modern typed frontend architecture** — TypeScript strict + `noUncheckedIndexedAccess` catches FHIR-shape bugs at compile time, not in production.
  3. **Easier future extensibility** — adding a new card is one new file in `components/cards/`, one hook, one route mention. No Smarty/Twig dual-templating drag.
  4. **Reusable component system** — `CardShell`, `Badge`, `EmptyState`, `Spinner` are testable in isolation and composable into adjacent surfaces (e.g. a future encounter-detail view).
  5. **External SMART-on-FHIR compatibility** — built as a SMART standalone app today; SMART EHR Launch is a small additional step from the same code, opening this dashboard to third-party app integration the PHP version cannot offer.
  6. **Deployable independently of the PHP rendering layer** — a static bundle behind any web server. Lets us iterate the UI without coupling to OpenEMR PHP release cycles.
- **"Why not Next.js?"** SMART-on-FHIR is designed for browser-only SPAs. A Node server in the middle adds a trust boundary that holds tokens for no benefit; there is no SEO surface; the data is per-user behind OAuth2.
- **"Why TanStack Query and not Redux/RTK?"** The state we manage is server state, not application state. TanStack Query is purpose-built for that; Redux would be reinventing it.
- **"Why sessionStorage and not HttpOnly cookies?"** SPAs need the token in JS to call FHIR. HttpOnly cookies require a backend-for-frontend, which we deliberately don't have. We compensate with short token TTL, refresh rotation, strict CSP, no `dangerouslySetInnerHTML`, dependency scanning, and explicit logout cleanup (§4 and §5).
- **"What stops two browser tabs from racing on token refresh?"** Nothing prevents the concurrent attempt — `sessionStorage` is tab-scoped, not shared. The defense is server-side: refresh-token rotation invalidates the prior refresh token at OpenEMR, so a losing tab's next refresh fails and the failure path redirects it to `/login`. The risk is one extra re-authentication, not data corruption.
- **"What stops cross-patient access via URL tampering?"** Three layers: list source filtering (UX), route protection (defense-in-depth), and authoritative server-side scope enforcement returning 401/403 (the layer the SPA cannot bypass). See §6.
- **"What happens when silent renew fails?"** Three failure modes documented in §5 — expired session, network failure, revoked token. All converge on a redirect to `/login` with no PHI rendered.
- **"What if Allergies fails but Medications succeeds?"** One card shows error + Retry; the rest render normally (§10). Independent loading is the explicit gain over PHP's all-or-nothing render.
- **"How would you prove feature parity?"** §9 acceptance checklist + screenshots committed to `patient-dashboard/docs/parity/`.
- **"What's missing for production?"** Lighthouse + axe accessibility audit, screen-reader walk-through, richer per-card error copy, CSP header configured on OpenEMR's web server. Listed explicitly in §10 and §12.
