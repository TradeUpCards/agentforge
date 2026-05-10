#!/usr/bin/env bash
# Build the patient-dashboard SPA with production env + rsync the
# resulting `dist/` to the DigitalOcean droplet's OpenEMR docroot under
# `/dashboard-app/`.
#
# Usage:
#   bash patient-dashboard/scripts/deploy-to-droplet.sh
#
# Prerequisites — set these in `patient-dashboard/scripts/.env.deploy`
# (gitignored, see `.env.deploy.example`):
#   DROPLET_SSH_TARGET   = e.g. "deploy@agentforge.example.com"
#   DROPLET_DOCROOT      = e.g. "/var/www/localhost/htdocs/openemr"
#
# And in `patient-dashboard/.env.production` (gitignored, see
# `.env.production.example`):
#   VITE_OPENEMR_BASE_URL=                                   # empty → relative same-origin
#   VITE_OPENEMR_SITE=default
#   VITE_CLIENT_ID=<from prod OAuth registration>
#   VITE_CLIENT_SECRET=<from prod OAuth registration>
#   VITE_REDIRECT_URI=https://<droplet-host>/dashboard-app/callback
#   VITE_POST_LOGOUT_REDIRECT_URI=https://<droplet-host>/dashboard-app/
#   VITE_BASE_PATH=/dashboard-app/
#
# What this script does, in order:
#   1. Source `.env.deploy` (the SSH target + docroot path)
#   2. Build the SPA (`pnpm build`) — Vite picks up `.env.production`
#      automatically, so the prod env values get baked into the bundle
#   3. Verify `dist/index.html` and `dist/.htaccess` are present
#   4. rsync `dist/` to `<droplet>:<docroot>/dashboard-app/` (the
#      `--delete` flag removes stale files from previous deploys)
#   5. Smoke-test the deployed page by curling `/dashboard-app/`
#
# Safety:
#   - Will refuse to run if `.env.deploy` or `.env.production` is missing
#   - Will refuse to run if the build fails or `dist/` is empty
#   - rsync is run on the host; SSH credentials come from the operator's
#     existing ssh-agent / `~/.ssh/config` setup (no embedded secrets)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DASHBOARD_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_DEPLOY="${SCRIPT_DIR}/.env.deploy"
ENV_PROD="${DASHBOARD_DIR}/.env.production"

# ---- 1. Sanity-check env files ----------------------------------------------

if [ ! -f "${ENV_DEPLOY}" ]; then
  echo "ERROR: missing ${ENV_DEPLOY}" >&2
  echo "       copy .env.deploy.example to .env.deploy and fill in the SSH target." >&2
  exit 1
fi

if [ ! -f "${ENV_PROD}" ]; then
  echo "ERROR: missing ${ENV_PROD}" >&2
  echo "       copy .env.production.example to .env.production and fill in OAuth values." >&2
  exit 1
fi

# Source the deploy env (DROPLET_SSH_TARGET, DROPLET_DOCROOT).
# shellcheck disable=SC1090
. "${ENV_DEPLOY}"

: "${DROPLET_SSH_TARGET:?DROPLET_SSH_TARGET not set in .env.deploy}"
: "${DROPLET_DOCROOT:?DROPLET_DOCROOT not set in .env.deploy}"

# ---- 2. Build ----------------------------------------------------------------

echo "[1/4] Building dashboard with .env.production…"
cd "${DASHBOARD_DIR}"
pnpm build

if [ ! -f "${DASHBOARD_DIR}/dist/index.html" ]; then
  echo "ERROR: build produced no dist/index.html" >&2
  exit 1
fi
if [ ! -f "${DASHBOARD_DIR}/dist/.htaccess" ]; then
  echo "ERROR: dist/.htaccess missing — Apache won't fall back for SPA routes." >&2
  echo "       (Vite copies anything under public/ — check public/.htaccess exists.)" >&2
  exit 1
fi

# ---- 3. Deploy ---------------------------------------------------------------

REMOTE_PATH="${DROPLET_DOCROOT%/}/dashboard-app/"
echo "[2/4] Deploying dist/ → ${DROPLET_SSH_TARGET}:${REMOTE_PATH}"

# Prefer rsync when available (delta transfer + --delete in one shot).
# Fall back to tar-over-ssh when rsync is missing — typical on Git Bash
# for Windows. The fallback creates a remote temp dir, untars into it,
# atomically swaps it in, and removes the previous version. That mimics
# rsync --delete semantics without needing rsync.
if command -v rsync >/dev/null 2>&1; then
  rsync -avz --delete \
    "${DASHBOARD_DIR}/dist/" \
    "${DROPLET_SSH_TARGET}:${REMOTE_PATH}"
else
  echo "        (rsync not found — using tar+ssh portable fallback)"
  TIMESTAMP="$(date +%s)"
  REMOTE_TMP="${REMOTE_PATH%/}.${TIMESTAMP}.new"
  REMOTE_OLD="${REMOTE_PATH%/}.${TIMESTAMP}.old"

  ssh "${DROPLET_SSH_TARGET}" "mkdir -p '${REMOTE_TMP}'"
  tar -C "${DASHBOARD_DIR}/dist" -czf - . | \
    ssh "${DROPLET_SSH_TARGET}" "tar -C '${REMOTE_TMP}' -xzf -"
  ssh "${DROPLET_SSH_TARGET}" "
    if [ -d '${REMOTE_PATH%/}' ]; then mv '${REMOTE_PATH%/}' '${REMOTE_OLD}'; fi
    mv '${REMOTE_TMP}' '${REMOTE_PATH%/}'
    rm -rf '${REMOTE_OLD}'
  "
fi

# ---- 4. Smoke test -----------------------------------------------------------

# Pull host out of the SSH target for the smoke curl. Format is
# "user@host" — strip the "user@" prefix.
DROPLET_HOST="${DROPLET_SSH_TARGET#*@}"

echo "[3/4] Smoke-testing https://${DROPLET_HOST}/dashboard-app/"
HTTP_CODE="$(curl -sk -o /dev/null -w '%{http_code}' "https://${DROPLET_HOST}/dashboard-app/" || echo "000")"
if [ "${HTTP_CODE}" = "200" ]; then
  echo "        ✅ HTTP 200 — bundle is reachable"
else
  echo "        ⚠️  HTTP ${HTTP_CODE} — investigate"
  echo "        Common causes: AllowOverride not set, .htaccess not read,"
  echo "        Apache needs a config reload, TLS cert not yet issued."
fi

echo "[4/4] Done."
echo
echo "Next:"
echo "  - Sign in: https://${DROPLET_HOST}/dashboard-app/"
echo "  - Click a patient → verify FHIR cards load"
echo "  - From legacy chart: open patient → click the new grid icon → land in modern dashboard"
