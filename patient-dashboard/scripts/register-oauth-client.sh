#!/usr/bin/env bash
# Registers the patient-dashboard SPA as an OAuth2 client with the local
# OpenEMR (https://localhost:9300). Idempotent? No — running it twice
# creates two clients. Run once per OpenEMR install.
#
# Output: full JSON registration response. The field you need is `client_id`.
# Put that value into patient-dashboard/.env as VITE_CLIENT_ID, then approve
# the client in OpenEMR Admin → System → API Clients.

set -euo pipefail

OPENEMR_BASE="${OPENEMR_BASE:-https://localhost:9300}"

curl -k -X POST "${OPENEMR_BASE}/oauth2/default/registration" \
  -H 'Content-Type: application/json' \
  -d '{"client_name":"OpenEMR Patient Dashboard (Modern)","application_type":"private","redirect_uris":["http://localhost:5173/callback"],"post_logout_redirect_uris":["http://localhost:5173/"],"token_endpoint_auth_method":"client_secret_post","scope":"openid fhirUser offline_access user/Patient.rs user/AllergyIntolerance.cu user/AllergyIntolerance.rs user/Condition.rs user/MedicationRequest.rs user/MedicationDispense.rs user/CareTeam.rs user/Encounter.rs user/DocumentReference.rs"}'

echo
