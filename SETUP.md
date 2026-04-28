# SETUP.md — Local Development Setup

**For:** AgentForge Clinical Co-Pilot — a fork of OpenEMR with an embedded AI agent (planned).
**Audience:** anyone setting this fork up for development, including future-you.
**Scope:** Stage 1 of the Week 1 brief — getting OpenEMR running locally with sample data.

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

Should return `3`. The bundled demo is intentionally small — **3 patients with thin clinical histories** (3 problems, 1 medication, 1 allergy, 0 labs, 3 encounters). It is enough to verify the install works and click around the UI; it is **not** enough to develop the agent against. See Step 2.

### Step 2 — Realistic clinical data (deferred — see ARCHITECTURE.md §5.2)

For the agent to be evaluable, patients need richer histories — multiple encounters, lab trends, medication changes. The bundled demo doesn't provide this. The plan (per `ARCHITECTURE.md` §5.2 and `AUDIT.md` finding D-4) is:

- **MVP (week 1):** ~5–8 hand-crafted edge-case patients via a Python seed script — engineered to exercise specific verifier behaviors (warfarin + NSAID, A1c +1.5 trend, PCN allergy, no recent labs, etc.)
- **Week 2:** layer in Synthea-generated synthetic patients via OpenEMR's `import-random-patients` devtool for broader eval coverage:
  ```bash
  docker compose exec openemr /root/devtools import-random-patients 20
  ```

Neither is part of Stage 1 setup.

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

5. **Patient data is sparse by default.** This is documented in the audit (`AUDIT.md` finding D-4) and shaped the eval plan.

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
| `SETUP.md` | This file |
| `.gauntlet/` | Local working notes (gitignored) — not part of deliverables |

These are AgentForge-specific. All upstream OpenEMR documentation (`README.md`, `DOCKER_README.md`, `CLAUDE.md`, `CONTRIBUTING.md`, `API_README.md`, `FHIR_README.md`) remains authoritative for OpenEMR-the-product.
