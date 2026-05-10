#!/usr/bin/env bash
# Registers the patient-dashboard SPA as an OAuth2 client with the local
# OpenEMR (https://localhost:9300). Idempotent? No — running it twice
# creates two clients. Run once per OpenEMR install (or after scope changes).
#
# The scope payload is read from oauth-client-registration.json so this
# script and the doc'd payload stay in sync.
#
# Output: full JSON registration response. Two fields you need:
#   - client_id     → VITE_CLIENT_ID     in patient-dashboard/.env
#   - client_secret → VITE_CLIENT_SECRET in patient-dashboard/.env
# After updating .env, approve the client in OpenEMR Admin → System →
# API Clients (Trusted = on, Enabled = on), then sign out and back in
# in the dashboard so the new token carries the new scopes.

set -euo pipefail

OPENEMR_BASE="${OPENEMR_BASE:-https://localhost:9300}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PAYLOAD="${SCRIPT_DIR}/oauth-client-registration.json"

if [ ! -f "${PAYLOAD}" ]; then
  echo "Missing payload file: ${PAYLOAD}" >&2
  exit 1
fi

curl -k -X POST "${OPENEMR_BASE}/oauth2/default/registration" \
  -H 'Content-Type: application/json' \
  --data "@${PAYLOAD}"

echo
