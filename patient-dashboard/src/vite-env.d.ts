/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_OPENEMR_BASE_URL: string
  readonly VITE_OPENEMR_SITE: string
  readonly VITE_CLIENT_ID: string
  readonly VITE_CLIENT_SECRET: string
  readonly VITE_REDIRECT_URI: string
  readonly VITE_POST_LOGOUT_REDIRECT_URI: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
