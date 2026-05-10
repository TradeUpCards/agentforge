/// <reference types="vitest/config" />
import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Vite config — proxies OAuth2 + FHIR API calls to the OpenEMR backend during dev
// so the browser never sees a CORS preflight against an HTTPS-only EHR.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const target = env.VITE_OPENEMR_BASE_URL ?? 'https://localhost:9300'

  // Public base path for the built bundle. In production the dashboard
  // is co-located under OpenEMR's docroot at `/patient-dashboard/`
  // (see PATIENT_DASHBOARD_MIGRATION.md §13), so asset URLs must be
  // prefixed `/patient-dashboard/...`. Set via VITE_BASE_PATH in
  // .env.production. In dev (no env var set) the default is `/` so
  // `pnpm dev` continues to serve at `http://localhost:5173/`.
  const basePath = env.VITE_BASE_PATH ?? '/'

  return {
    base: basePath,
    plugins: [react(), tailwindcss()],
    server: {
      port: 5173,
      proxy: {
        '/oauth2': {
          target,
          secure: false,
          changeOrigin: true,
        },
        '/apis': {
          target,
          secure: false,
          changeOrigin: true,
        },
      },
    },
    build: {
      sourcemap: true,
      target: 'es2020',
    },
    test: {
      environment: 'happy-dom',
      globals: true,
      setupFiles: ['./src/test/setup.ts'],
      css: false,
    },
  }
})
