# SETUP.md — Local Development Setup

> **Related docs:** [`README.md`](./README.md) (project overview) · [`ARCHITECTURE.md`](./ARCHITECTURE.md) (system architecture) · [`OPENEMR_README.md`](./OPENEMR_README.md) (upstream OpenEMR docs preserved for reference)

**For:** AgentForge Clinical Co-Pilot — a fork of OpenEMR with an embedded AI agent.
**Audience:** anyone setting this fork up for development, including future-you.
**Scope:** end-to-end local development — OpenEMR + Synthea-populated demo data + the Python agent service + the chat drawer reachable from any patient chart page.

This document captures **what was actually required** to get the OpenEMR fork running, including the gotchas discovered along the way. Where this differs from upstream `README.md` / `DOCKER_README.md`, this file is the source of truth for AgentForge.

---

## Prerequisites

- **Docker Desktop** (29.x or later) — the Docker daemon must be running
- **~10 GB free disk** for images and persistent volumes (MariaDB grows with use)
- **At least 4 GB allocated to Docker** in Docker Desktop's resource settings (the OpenEMR `flex` image is heavyweight)
- **Bash-compatible shell** (Git Bash, WSL, Linux, or macOS Terminal). PowerShell works for most commands but path syntax differs.
- **Ports 8300, 8310, 8320, 9300, 8025, 1025, 5984, 6984, 4444, 7900 free** on the host

No host-side PHP, Node, MySQL, or Composer installation is required for development. Everything runs in containers via the dev stack at `docker/development-easy/`.

---

## First-time setup

```bash
git clone <fork-url> openemr
cd openemr/docker/development-easy
docker compose up --detach --wait
```

### What to expect on first boot

**The first run takes 10–15 minutes.** Three things happen serially:

1. **Image pulls** (~2–4 min on a fast connection): `openemr/openemr:flex`, `mariadb:11.8.6`, plus phpMyAdmin, Selenium, CouchDB, OpenLDAP, Mailpit.
2. **MariaDB initialization** (~1–2 min): MariaDB has a healthcheck with `start_period: 1m`. OpenEMR depends on `condition: service_healthy` (`docker-compose.yml:95-97`) and waits for it.
3. **OpenEMR install + asset compilation** (~5–10 min): the `flex` image is dev-focused and runs `composer update` and asset compilation on first boot. This is normal — watch progress with `docker compose logs -f openemr`.

### The `--wait` flag often appears to fail

The OpenEMR healthcheck has `start_period: 3m` and a Curl probe to `https://localhost/meta/health/readyz`. On a slow first boot the install routine can exceed this window, causing `docker compose up --wait` to exit non-zero **even though the install is still progressing successfully**. If `--wait` returns an error:

```bash
# Verify directly
docker inspect -f '{{.State.Health.Status}}' development-easy-openemr-1
docker compose logs --tail=50 openemr
```

Wait until `Health.Status` is `healthy`. This may take another 5–10 minutes after `--wait` returns.

---

## Access points

| Service | URL | Credentials |
|---|---|---|
| OpenEMR (HTTP) | http://localhost:8300/ | `admin` / `pass` |
| OpenEMR (HTTPS) | https://localhost:9300/ | `admin` / `pass` |
| phpMyAdmin | http://localhost:8310/ | `root` / `root` |
| Mailpit (SMTP test viewer) | http://localhost:8025/ | — |
| Selenium VNC (test browser) | http://localhost:7900/ | password `openemr123` |
| MariaDB (direct) | `localhost:8320` | `root` / `root` |

The default OpenEMR site is `default`. The agent's hypothetical FHIR API base URL is `https://localhost:9300/apis/default/fhir/`.

---

## Loading demo patient data

**The `flex` image's auto-install does NOT seed patient data.** A fresh install has no patients, problems, meds, labs, or encounters.

### Step 1 — Reset and reinstall with demo data

```bash
cd docker/development-easy
docker compose exec openemr /root/devtools dev-reset-install-demodata
```

This is **destructive** — it wipes the DB and reinstalls OpenEMR fresh, then loads the bundled demo dataset. Safe on a fresh stack. Takes ~1–2 minutes.

After it finishes, additional demo logins (various ACL roles plus a patient portal account) are created — see the [OpenEMR demo credentials wiki](https://www.open-emr.org/wiki/index.php/Development_Demo#Demo_Credentials). The default `admin` / `pass` login still works.

#### Windows / Git Bash gotcha

On Windows under Git Bash, MSYS path translation rewrites `/root/devtools` to a Windows path before it reaches Docker:

```
exec failed: ... stat C:/Program Files/Git/root/devtools: no such file
```

Prefix the command with `MSYS_NO_PATHCONV=1`:

```bash
MSYS_NO_PATHCONV=1 docker compose exec openemr /root/devtools dev-reset-install-demodata
```

PowerShell and Linux/macOS shells are unaffected.

#### Verify

```bash
docker compose exec mysql mariadb -uopenemr -popenemr openemr -e \
  "SELECT COUNT(*) FROM patient_data;"
```

Should return `3`. The bundled demo is intentionally small — **3 patients with thin clinical histories** (3 problems, 1 medication, 1 allergy, 0 labs, 3 encounters). Enough to verify the install works; **not** enough to develop the agent against. See Step 2.

### Step 2 — Layer in Synthea patients with realistic histories

The agent's verifier story (strict-on-numerics, lenient-on-qualifiers, multi-encounter delta narratives) is only defensible when shown across multiple patients with real lab trends and medication histories. We pulled this forward into week 1 — see DECISIONS.md appendix `2026-04-30 — Pull forward real-DB tools + Synthea-populated demo data` for the rationale.

```bash
# From docker/development-easy/
MSYS_NO_PATHCONV=1 docker compose exec openemr /root/devtools import-random-patients 200
```

This generates ~200 Synthea patients with multi-year clinical histories: encounters, lab trends (LOINC-coded), medication changes, allergies. Takes ~10–20 minutes depending on disk speed.

Verify:

```bash
docker compose exec mysql mariadb -uopenemr -popenemr openemr -e \
  "SELECT COUNT(*) FROM patient_data;
   SELECT COUNT(*) FROM lists WHERE type='medical_problem';
   SELECT COUNT(*) FROM procedure_result;"
```

Should be in the ballpark of 200 / 6,000+ / 50,000+.

### Step 3 — Seed today's appointments

The Co-Pilot demo narrative is "PCP looks at today's schedule, picks the next patient, summons Co-Pilot, walks in." That story needs appointments on the *current day* for a handful of imported patients. The `agent/seed_appointments.sql` script does this — pick ~5 patients you'd like to see on the schedule and run:

```bash
MSYS_NO_PATHCONV=1 docker compose exec -T mysql \
  mariadb -uopenemr -popenemr openemr < ../../agent/seed_appointments.sql
```

Edit the script first to use real patient IDs from your import — the file has placeholders to find/replace. After running, the OpenEMR calendar (Calendar → today) will show the seeded appointments.

### Step 4 — Create the read-only DB user the agent connects with

The Python agent never connects as `openemr` (which has full DDL/DML rights). It connects as `agent_ro` with `SELECT`-only privileges, per ARCHITECTURE.md §4.1 / DECISIONS.md §3:

```bash
MSYS_NO_PATHCONV=1 docker compose exec mysql mariadb -uroot -proot -e "
  CREATE USER IF NOT EXISTS 'agent_ro'@'%' IDENTIFIED BY 'agent_ro_dev_password';
  GRANT SELECT ON openemr.* TO 'agent_ro'@'%';
  FLUSH PRIVILEGES;
"
```

The agent's `agent/.env` should reference this user (see `agent/.env.example`).

### Step 5 — Configure agent secrets

The agent needs four categories of credentials, all wired through `agent/.env` (gitignored — copy from `agent/.env.example`):

| Category | Required? | Notes |
|---|---|---|
| **LLM provider** (`ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`) | Optional for first boot | Without a key, the agent runs in **fixture mode** — canned LLM responses, full loop still exercised. With a real key it flips to live. Direct Anthropic uses `https://api.anthropic.com`; OpenRouter uses `https://openrouter.ai/api` (no trailing `/v1` — the SDK appends it). On OpenRouter, models are `anthropic/<model>` prefixed. |
| **Observability** (`LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`) | Optional | Without keys, traces are simply not emitted. With keys, every `/chat` request appears in Langfuse with per-step latency, token usage, verifier verdict, and stripped-claim detail. Get keys from https://cloud.langfuse.com → project settings. Free tier is sufficient for development (50k observations/mo). |
| **OpenEMR ↔ agent integrity** (`OPENEMR_HMAC_SECRET`) | **Required** | Pre-shared secret used by the OpenEMR PHP module to sign requests and the agent to verify them. Generate with `openssl rand -hex 32`. The **same** value must also be set in `docker/development-easy/docker-compose.override.yml` for the openemr container — see `agent/README.md` "Local OpenEMR integration". HMAC payload includes a unix timestamp for replay protection — agent rejects requests >30s off its clock. |
| **Audit-log writer** (`AGENT_DB_AUDIT_USER`, `AGENT_DB_AUDIT_PASS`) | Optional in dev (no-op gracefully if unset) | Dedicated MariaDB user with `INSERT`-only privileges on the `agent_log` table (the HIPAA §164.312(b) audit trail per [`AUDIT.md C-1`](./AUDIT.md)). Defaults to `agent_audit_rw` per `.deploy/bootstrap.sh`. **In production, REQUIRED** — `agent/_audit_log.py` no-ops when these are empty, which is fine for local dev iteration but unacceptable for any deployment that touches real PHI. |

**Optional rate-limit tunables** (have sensible defaults, override only if needed):

| Variable | Default | Purpose |
|---|---|---|
| `AGENT_RATE_LIMIT_RPM` | `60` | Per-user requests-per-minute limit. Refuses with `refusal_reason: "rate_limit"` past threshold. |
| `AGENT_TOKEN_BUDGET_PER_HOUR` | `200000` | Per-user hourly token budget across all LLM calls. Refuses with `refusal_reason: "token_budget"` past threshold. |

The `agent_ro` DB password from Step 4 also goes in `agent/.env` as `AGENT_DB_PASS`. Full variable list and trade-offs in [`agent/.env.example`](./agent/.env.example) and [`agent/README.md`](./agent/README.md).

---

## Stopping and restarting

```bash
# Stop without losing data
docker compose stop

# Restart later (much faster — images and volumes are cached)
docker compose up --detach

# Tear down completely (DESTROYS DATA)
docker compose down --volumes
```

---

## System dependencies discovered during setup

These are the things that aren't obvious from the upstream docs but matter for development on this fork:

1. **MariaDB healthcheck-gated startup.** OpenEMR has `depends_on: mysql: condition: service_healthy`. MariaDB's healthcheck has `start_period: 1m`. Ordering matters; tools or platforms that don't honor `condition: service_healthy` will not deploy this stack cleanly (this is one of several reasons VPS + Docker Compose was chosen over Railway for production deployment — see `ARCHITECTURE.md` §8.1).

2. **Eleven persistent volumes for the OpenEMR service alone** (`assetvolume`, `themevolume`, `sitesvolume`, `nodemodules`, `vendordir`, `ccdanodemodules`, `ccdanodemodules2`, `logvolume`, `couchdbvolume`, plus DB and mail). They survive `compose stop` but are wiped by `compose down --volumes`.

3. **The `flex` image bind-mounts source code** (`docker-compose.yml:34-35`: `${OPENEMR_DIR:-../..}:/openemr:ro`). Local file edits propagate into the container. Useful for development; complicates production deployment (the agent service Dockerfile bakes source instead of bind-mounting).

4. **HTTPS uses a self-signed cert** at `localhost:9300`. Browsers will warn; accept the risk for local dev.

5. **Patient data is sparse by default.** The bundled `dev-reset-install-demodata` only seeds 3 thin patients (`AUDIT.md` finding D-4). Step 2 above (Synthea import) is what makes the install evaluable; without it, the agent has nothing meaningful to summarize.

---

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `--wait` exits non-zero | Normal on first boot; wait for `docker inspect` healthy status |
| Port already in use | Another service holding 8300/8310/8320/etc. — stop it or change `WT_*_PORT` env vars |
| OpenEMR shows install screen | The auto-install didn't complete; usually a DB connectivity issue. `docker compose logs mysql` |
| 502 Bad Gateway after restart | OpenEMR container restarted before MariaDB; wait 30s and retry |
| Patient list empty | Demo data not loaded — run `dev-reset-install-demodata` (see "Loading demo patient data") |
| Composer / asset rebuild on every restart | Confirm `EASY_DEV_MODE_NEW: "yes"` is set in compose env |

---

## Project-specific files in this fork

| File | Purpose |
|---|---|
| `AUDIT.md` | OpenEMR audit findings with agent-integration implications |
| `USERS.md` | Target user (PCP), workflow, use cases |
| `ARCHITECTURE.md` | Agent integration plan + tradeoffs |
| `DECISIONS.md` | CTO-defense ledger — every architectural choice with its rationale and tradeoff |
| `SETUP.md` | This file |
| `agent/` | Python agent service (FastAPI). See [`agent/README.md`](./agent/README.md) for setup. |
| `interface/modules/custom_modules/oe-module-clinical-copilot/` | OpenEMR PHP integration module |
| `.gauntlet/` | Local working notes (gitignored) — not part of deliverables |

These are AgentForge-specific. All upstream OpenEMR documentation (`README.md`, `DOCKER_README.md`, `CLAUDE.md`, `CONTRIBUTING.md`, `API_README.md`, `FHIR_README.md`) remains authoritative for OpenEMR-the-product.

---

## Bringing up the full stack (OpenEMR + agent)

The OpenEMR stack instructions above are sufficient to run OpenEMR on its
own. To run the full agent integration locally, you need three things up
at the same time:

1. **OpenEMR dev-easy stack** — see "First-time setup" above, plus Step 2 (Synthea data) and Step 4 (`agent_ro` DB user).
2. **Python agent service** — see [`agent/README.md`](./agent/README.md). Runs as a uvicorn process on the host, port 8000. Uses `agent_ro` to query OpenEMR's MariaDB.
3. **Docker-compose override** at `docker/development-easy/docker-compose.override.yml` — adds `AGENT_BASE_URL=http://host.docker.internal:8000` and `OPENEMR_HMAC_SECRET` to the openemr container so the PHP module can reach the host-bound agent service. Gitignored; create from the example in `agent/README.md`.

### Reaching the chat drawer from a chart

Once the stack is up, open any patient's chart in OpenEMR. The drawer is reachable from three affordances, all wired through the custom module under `interface/modules/custom_modules/oe-module-clinical-copilot/`:

- **Page-heading icon** — top-right of the chart's heading row, left of the expand/contract icon. Outline when closed, filled blue when open. Renders only on patient-context pages.
- **Floating "Co-Pilot" button** — bottom-right corner of the viewport, persistent across tabs.
- **Right-edge chevron handle** — vertically centered against the active tab's iframe; click to toggle. Stays visible whether the drawer is on- or off-screen.

All three call the same top-window `OE_COPILOT.toggle()`. The drawer iframes `chat-panel.php`, which enforces session + CSRF + ACL before forwarding the request to the Python agent (HMAC-signed).

End-to-end click-through walkthrough lives in [`.gauntlet/week1/local-openemr-runbook.md`](./.gauntlet/week1/local-openemr-runbook.md) (gitignored — local notes only).

---

## Pre-commit hook (eval-on-commit)

Every commit runs the agent's verifier unit tests + eval Golden Set in
fixture mode (~5–10s, no LLM cost). The hook lives in
`scripts/git-hooks/pre-commit`. **Enable it once per clone:**

```bash
git config core.hooksPath scripts/git-hooks
```

Verify it's wired:

```bash
git config core.hooksPath
# → scripts/git-hooks
```

Live-LLM and live-DB eval cases are intentionally skipped in the hook
(they cost tokens / require the docker stack running). Run them
manually before tagging a release:

```bash
USE_FIXTURE_DATA=false USE_FIXTURE_LLM=false \
  agent/venv/Scripts/python.exe -m agent.tests.eval.runner
```

Reports land in `agent/tests/eval/results/<timestamp>.md`. Preview the
latest in your browser:

```bash
agent/venv/Scripts/python.exe -m agent.tests.eval.preview_latest
```

Skip the hook in an emergency: `git commit --no-verify` (sparingly —
defeats the safety net).

---

## Verifying everything works

Five checks, in order. Each one isolates a layer so a failure points at one specific thing.

**1. OpenEMR is up and demo data loaded.**
```bash
curl -ksI https://localhost:9300/ | head -1     # HTTP/2 200 (or HTTP/1.1 200)
docker compose exec mysql mariadb -uopenemr -popenemr openemr -e \
  "SELECT COUNT(*) FROM patient_data;"           # >> 3 if Step 2 (Synthea) ran
```

**2. Agent service responds.** With `agent/venv` active and uvicorn running on port 8000:
```bash
curl -s http://localhost:8000/health
# {"status":"ok","llm_mode":"fixture"}   # or "live" if ANTHROPIC_API_KEY set
```

**3. Agent unit tests pass.**
```bash
agent/venv/Scripts/pytest agent/tests/unit/ -v
# 16 passed
```

**4. End-to-end click-through.** Sign in to OpenEMR, open any patient's chart, then summon the drawer (header icon, floating button, or right-edge chevron — see "Reaching the chat drawer from a chart" above). Click "Pre-visit brief" or type a question. Within ~6 seconds you should see a streamed response with citations to record IDs.

If you see *"Something went wrong. Please retry."*, check the browser console first (likely 401 from a session-bag mismatch or a malformed URL) before suspecting the agent. The agent's uvicorn log will show the inbound request and the verifier verdict — those are the next two diagnostic surfaces.

**5. Langfuse trace lands** (only if Langfuse keys are configured). Open https://cloud.langfuse.com → your project → Traces. Each `/chat` request appears as a top-level span with per-tool nested spans, generation tokens/cost, and a `verifier_verdict` metadata field.

---

## Week 2 — additional services + endpoint verification

Week 2 added two services that come up automatically with `docker compose up`:

| Service | Container | Purpose | Verification |
|---|---|---|---|
| **Qdrant** (vector store) | `development-easy-qdrant-1` | Stores the 26-chunk clinical-guideline corpus for hybrid RAG | `docker compose exec agent python -c "import httpx; print(httpx.get('http://qdrant:6333/healthz').text)"` → `healthz check passed` |
| **Docling** (Python lib in agent container) | inside `development-easy-agent-1` | Document layout extractor (PDF / PNG / JPG) for the upload pipeline | Implicit — tested by the W2 endpoint verification below |

### Optional W2 env vars

| Var | Notes |
|---|---|
| `COHERE_API_KEY` | Optional. With the key, Cohere Rerank is used; without it, the local BAAI cross-encoder fallback fires (~16s cold-start, ~2s warm). Either path satisfies the hybrid-RAG rerank requirement; Cohere is a cost-lever, not a dependency. |

### Week 2 endpoint smoke checks

Run these after the W1 verification above passes. They confirm the W2 multimodal capabilities work end-to-end.

**6. `/graph_chat` endpoint (LangGraph supervisor + 2 workers + responder).**
```bash
# From inside OpenEMR's container (so HMAC + internal-network rules hold)
docker compose exec openemr curl -s -X POST http://agent:8000/graph_chat \
  -H "Content-Type: application/json" \
  -d '{"patient_id": 1, "session_id": "smoke-test", "messages": [{"role":"user","content":"What problems does this patient have?"}]}'
# 200 OK with {"final_response": ..., "citations": [...]}
```

**7. `/attach_and_extract` endpoint (Docling layout + Haiku extraction).** Easiest path: upload a lab PDF via the OpenEMR Documents UI under category 9000 (Lab Result auto-extract) or 9001 (Intake Form auto-extract). The DocumentSavedSubscriber fires automatically; check that an extraction landed:
```bash
docker compose exec mysql mariadb -uopenemr -p"$MYSQL_USER_PASSWORD" openemr -e \
  "SELECT id, doc_type, status, attempt_n, model FROM co_pilot_extractions ORDER BY id DESC LIMIT 3;"
# Most-recent row shows status='ok' with a non-null model (e.g. claude-haiku-4-5)
```

**8. PR-blocking eval gate (W2 §6 hard gate).**
```bash
# Run the smoke tier (8 cases, fixture mode, ~5s, $0)
USE_FIXTURE_LLM=true USE_FIXTURE_DATA=true USE_FIXTURE_EXTRACTION=true \
OPENEMR_HMAC_SECRET=ci-fixture-mode-not-a-real-secret \
agent/venv/Scripts/python -m pytest agent/tests/eval/ -q -m smoke
```

**9. React patient dashboard (W2 surprise challenge).** Run in a second terminal alongside the docker stack:
```bash
cd patient-dashboard
pnpm install && pnpm dev
# Open http://localhost:5173 — sign in via OAuth2 against local OpenEMR
```
Six clinical cards (Allergies, Problems, Medications, Prescriptions, Care Team, Encounters) load over OpenEMR FHIR API; full parity with the legacy patient summary screen.
