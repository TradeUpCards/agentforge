# OpenEMR Patient Dashboard — Modern Frontend

React 19 + Vite 6 + TypeScript reimplementation of the OpenEMR patient
dashboard. Consumes OpenEMR's existing FHIR R4 API and OAuth2 server.

The full architecture and defense doc lives at
[`../PATIENT_DASHBOARD_MIGRATION.md`](../PATIENT_DASHBOARD_MIGRATION.md).

## Quick start

1. **Register an OAuth2 client** with OpenEMR (one-time):

   ```bash
   bash scripts/register-oauth-client.sh
   ```

   The response prints both `client_id` and `client_secret`. Confidential
   client registration is required because OpenEMR enforces that `user/`
   scopes are only available to confidential clients — see
   [§5 of the migration doc](../PATIENT_DASHBOARD_MIGRATION.md#5-authentication-design)
   for the tradeoff.

   Approve the client in OpenEMR → Administration → System → API Clients.

2. **Configure environment:**

   ```bash
   cp .env.example .env
   # Edit .env: set VITE_CLIENT_ID and VITE_CLIENT_SECRET from step 1.
   ```

3. **Install + run:**

   ```bash
   pnpm install
   pnpm dev
   ```

   Open http://localhost:5173.

## Scripts

| Script | What it does |
|---|---|
| `pnpm dev` | Vite dev server with HMR; proxies `/oauth2` and `/apis` to OpenEMR. |
| `pnpm build` | Type-check, then emit static bundle to `dist/`. |
| `pnpm preview` | Serve the production build locally. |
| `pnpm typecheck` | TypeScript check, no emit. |

## Where things live

| Concern | Path |
|---|---|
| OAuth2 / OIDC config | `src/auth/oidcConfig.ts` |
| FHIR fetch wrapper | `src/api/fhirClient.ts` |
| One file per FHIR resource | `src/api/resources/` |
| TypeScript shapes | `src/types/fhir.ts` |
| TanStack Query hooks | `src/hooks/` |
| Cards | `src/components/cards/` |
| Layout | `src/components/layout/` |
| Pages | `src/pages/` |

## Deployment

Production target: **co-locate behind the OpenEMR origin** (no CORS).
Build with `pnpm build` and serve `dist/` from a path under the same
host as `/oauth2/` and `/apis/`. See
[`../PATIENT_DASHBOARD_MIGRATION.md` §13](../PATIENT_DASHBOARD_MIGRATION.md#13-deployment)
for details.
