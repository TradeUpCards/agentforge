import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Vite config — proxies OAuth2 + FHIR API calls to the OpenEMR backend during dev
// so the browser never sees a CORS preflight against an HTTPS-only EHR.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const target = env.VITE_OPENEMR_BASE_URL ?? 'https://localhost:9300'

  return {
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
  }
})
