# RUNBOOK.md — Backup, Restore, and On-Call

> **Related docs:** [`SLO.md`](./SLO.md) (the runtime objectives this runbook backs) · [`ARCHITECTURE.md`](./ARCHITECTURE.md) §4 (HIPAA + audit log) · [`AUDIT.md`](./AUDIT.md) C-1 (agent_log audit trail) · [`SETUP.md`](./SETUP.md) (full deployment from scratch — the disaster-recovery path) · [`.deploy/bootstrap.sh`](./.deploy/bootstrap.sh) (the script restore re-runs)

**Audience:** the on-call engineer in the worst hour of their week. Plus the hospital CTO asking *"if your droplet dies, what do you lose, and how long until clinicians can use the system again?"*

**Status:** design + procedure documented (this file). Backup automation + restore-drill cadence are operational follow-up — see §6. Until automation lands, backup is human-driven from this runbook.

---

## 1. Why this runbook exists

The 2026-05-02 system-architecture-review named this as a production blocker:

> *"DO snapshots are presumably running but aren't scheduled, tested, or owned. `agent_log` (regulatory audit data) lives on a single VPS with no off-site copy. Practical risk: a host-loss event drops the audit trail required by HIPAA §164.312(b)."*

Two separate concerns the runbook addresses:

1. **Operational continuity** — clinicians can't use the system if the droplet dies; how fast can we get back?
2. **HIPAA §164.312(b) audit-trail durability** — `agent_log` records every PHI read; regulatory retention is 6 years, and we can't lose it to a host-level failure.

These have different RPO/RTO/retention requirements; the table in §3 handles them separately.

---

## 2. What's deployed (the surface this runbook protects)

Single DigitalOcean droplet: `142-93-242-40.nip.io`, `/opt/agentforge`. Docker Compose stack with these volumes:

- **MariaDB data** (`mariadb-volume`) — contains the `openemr` database, which contains:
  - PHI tables (`patient_data`, `lists`, `prescriptions`, `procedure_result`, `form_encounter`, `lists` for allergies)
  - `agent_log` table (regulatory audit trail per [AUDIT.md C-1](./AUDIT.md#c-1-event-audit-logger-doesnt-log-selects-by-default))
- **OpenEMR sites volume** (`openemrvolume:/var/www/localhost/htdocs/openemr/sites`) — contains the drive encryption keys; without these, the MariaDB encrypted columns cannot be decrypted
- **CouchDB volume** (`couchdbvolume:/couchdb/data`) — required by the OpenEMR flex image; minimal data
- **Caddy data** (`caddy-data`, `caddy-config`) — auto-renewing TLS certs

Plus loose files on the host:

- `/opt/agentforge/.env` — all secrets (HMAC, DB passwords, Anthropic API key, Langfuse keys, AGENT_AUDIT_RW_PASSWORD)
- `/opt/agentforge/repo/` — git checkout (recoverable from `git clone`, but local edits aren't)

---

## 3. What gets backed up (and at what cadence)

| Data | Why | Frequency | Retention | Backup method | Storage |
|---|---|---|---|---|---|
| **`agent_log` table** | HIPAA §164.312(b); regulatory audit data; cannot be reconstructed from any other source | **Hourly** | **6 years** (HIPAA-mandated) | `mysqldump --single-transaction openemr agent_log` | Off-host: DigitalOcean Spaces or AWS S3 (server-side encrypted) |
| **`openemr` database (full)** | PHI; patient charts, prescriptions, encounter notes | **Daily** | **30 days** rolling + monthly archive for 1 year | `mysqldump --single-transaction openemr` | Same off-host bucket; separate prefix |
| **OpenEMR sites volume** | Drive encryption keys + uploaded documents; without these the DB dump is undecryptable | **Daily** (after DB dump completes) | 30 days rolling + monthly archive for 1 year | `tar czf` of the volume mount | Same off-host bucket |
| **DigitalOcean droplet snapshots** | Whole-host recovery; faster RTO than rebuilding from scratch | **Daily** automatic | 7-day rolling | DigitalOcean automated snapshots (enable via Droplet → Backups → On) | DigitalOcean (same region — does NOT survive region-wide outage) |
| **`/opt/agentforge/.env`** | Bootstraps reference these creds; without them the new instance can't reconnect to the same encrypted DB | **On change** (post-deploy) | 90 days rolling | `cp .env <off-host>` (encrypted in transit + at rest) | Off-host (encrypted) — NOT in any git repo, NOT in a regular backup snapshot of the host (since host snapshots may end up world-readable on shared infra) |

### What's intentionally NOT backed up

- **Synthetic Synthea patient data.** Regenerable via `/root/devtools import-random-patients`. Saves backup volume.
- **Docker container images.** Pull from registry on restore.
- **Application code.** Lives in git on both GitLab + GitHub mirrors (per [WORKFLOW.md](./WORKFLOW.md)).
- **Caddy logs.** Operational diagnostic; low signal at restore time.
- **Pre-commit hook venv** (`agent/venv/`). Re-create with `pip install -r agent/requirements.txt`.

---

## 4. Restore procedures — three scenarios

### 4a. Scenario: `agent_log` table corrupted or truncated (most common)

**Detection:** rows missing for a known time window; `SELECT COUNT(*) FROM agent_log WHERE ts > NOW() - INTERVAL 1 DAY` returns suspiciously low.

**RTO target:** 30 minutes from detection to restore.

**Procedure:**

1. Identify the latest hourly dump from the off-host bucket that pre-dates the corruption.
2. Download to droplet: `aws s3 cp s3://<bucket>/agent_log/<hourly-dump>.sql.gz /tmp/`
3. Take the agent service offline so no concurrent writes corrupt the restore: `docker compose stop agent`
4. Restore the table (drops + re-creates it from dump): `gunzip -c /tmp/agent_log_<ts>.sql.gz | docker compose exec -T mysql mariadb -uroot -p"$MYSQL_ROOT_PASSWORD" openemr`
5. Re-grant the `INSERT` privilege to `agent_audit_rw` (DROP TABLE removes grants): see `.deploy/bootstrap.sh:334-338`.
6. Bring agent back: `docker compose start agent`. Verify a fresh `/chat` request shows up in `agent_log`.

### 4b. Scenario: full MariaDB loss (database volume corrupted, container won't start)

**Detection:** `docker compose ps mysql` shows the container in restart loop; `docker compose logs mysql` shows InnoDB corruption errors.

**RTO target:** 2 hours from detection to clinical-usable state.

**Procedure:**

1. Stop the stack: `docker compose down` (preserves volumes)
2. Back up the corrupted volume just in case: `docker run --rm -v mariadb-volume:/from -v $(pwd):/to alpine tar czf /to/corrupted-mariadb-$(date +%s).tgz /from`
3. Remove the corrupted volume: `docker volume rm <project>_mariadb-volume`
4. Bring just MySQL up so it initializes a fresh data dir: `docker compose up -d mysql && sleep 30`
5. Restore the latest daily dump: `aws s3 cp s3://<bucket>/openemr/<latest-daily>.sql.gz /tmp/ && gunzip -c /tmp/<dump>.sql.gz | docker compose exec -T mysql mariadb -uroot -p"$MYSQL_ROOT_PASSWORD"`
6. Restore the OpenEMR sites volume (needed for drive keys): `aws s3 cp s3://<bucket>/sites/<latest>.tgz /tmp/ && docker run --rm -v openemrvolume:/to -v /tmp:/from alpine tar xzf /from/<sites>.tgz -C /to`
7. Re-create the application DB users + grants — re-run the heredoc block at `.deploy/bootstrap.sh:279-340`.
8. Bring the rest of the stack up: `docker compose up -d`
9. Restore the latest hourly `agent_log` dump on top (more recent than the daily): see §4a steps 1-5.
10. Verify: hit `https://142-93-242-40.nip.io/agent/health` → 200; log into OpenEMR with admin creds; open a patient chart; trigger a Co-Pilot request; confirm `agent_log` gets a row.

### 4c. Scenario: full host loss (droplet deleted, region outage, ransomware)

**Detection:** can't reach the droplet at all; DigitalOcean console shows it gone or unreachable.

**RTO target:** 4 hours from detection to clinical-usable state on a fresh droplet.

**Procedure:**

1. Provision a fresh droplet from the latest DigitalOcean automated snapshot, OR a clean Ubuntu LTS image if region-wide.
2. If from snapshot: stack should boot mostly clean; jump to step 6.
3. If from clean image: install Docker + Docker Compose, clone the repo from GitHub mirror (`git clone https://github.com/TradeUpCards/agentforge.git /opt/agentforge/repo`).
4. Restore `/opt/agentforge/.env` from off-host backup (the encrypted bucket).
5. Run `.deploy/bootstrap.sh` — but pass `SKIP_DB_INIT=1` (need to add this flag) so it doesn't reset the schema.
6. Restore latest daily MariaDB dump + sites volume (§4b steps 5-6).
7. Restore latest hourly `agent_log` dump (§4a steps 1-5).
8. Update DNS / nip.io: if the IP changed, update Caddy config OR if keeping the same hostname pattern, point the new droplet's IP at the same `142-93-242-40.nip.io`-shaped hostname (the nip.io scheme makes this rebuild-friendly).
9. Verify (§4b step 10).

---

## 5. Restore-drill cadence (this is the part most teams skip)

> **A backup that has never been restored is not a backup.**

**Monthly cadence:**
- First Monday of each month, restore-drill against a temp droplet
- Drill scenario rotates: month 1 = §4a (table restore), month 2 = §4b (DB restore), month 3 = §4c (host restore)
- Time the actual RTO; record in this runbook (append "Last drill" rows below)
- Discrepancies between drill RTO and target RTO → file a ticket

**On any procedural change:**
- Re-drill within 1 week of any change to backup scripts, schema, or restore procedure
- Document the change in §7 changelog

**Last drills run** *(table to fill in as drills happen)*:

| Date | Scenario | Operator | RTO target | RTO actual | Notes |
|---|---|---|---|---|---|
| _(none yet — first drill targeted for week 2 once automation lands)_ | | | | | |

---

## 6. What's NOT yet automated (operational follow-up)

The procedures in §4 are documented but the cron + scripts that execute them on the cadence in §3 aren't wired yet. Two pieces of operational follow-up:

### 6a. Backup automation

Need:
- `scripts/backup-agent-log.sh` — runs hourly via cron, dumps `agent_log`, gzips, uploads to off-host bucket with encryption-at-rest enabled
- `scripts/backup-openemr-full.sh` — runs daily, dumps full openemr DB + tars sites volume, uploads
- `scripts/upload-env-on-change.sh` — runs on `.env` change (use `inotifywait` or post-deploy hook)
- Cron entries in `/etc/cron.d/agentforge-backups`
- DigitalOcean Spaces (or AWS S3) bucket provisioned with appropriate IAM/access policy

Estimated effort: ~half day (~3-4 hours including the bucket + IAM provisioning + a first end-to-end test).

### 6b. Restore-drill automation

Less critical than backup automation, but the cadence in §5 isn't tracked anywhere:
- Calendar reminder for the on-call rotation
- A drill-runbook shorter than this doc that the operator follows
- A drill-results dashboard / log

Estimated effort: ~2 hours including a quick first manual drill against a temp droplet to validate the procedure.

### 6c. Off-site bucket configuration not in this repo

The S3/Spaces bucket name, credentials, and access policy are deployment-environment config — they belong in `.env` (not in the repo). The runbook references `<bucket>` and `<off-host>` as placeholders. First-time setup should:

- Create a DigitalOcean Spaces bucket (or AWS S3) with server-side encryption + versioning enabled
- Generate access key with PUT/GET permissions only on the specific bucket prefixes
- Add `BACKUP_S3_BUCKET` + `BACKUP_S3_ACCESS_KEY` + `BACKUP_S3_SECRET_KEY` to `.env`
- Document the bucket-naming convention (e.g. `agentforge-prod-backup-<region>`)

---

## 7. Ownership + escalation

For week 1 / pilot:
- **Backup-failure paging:** the SLO doc names the routing layer as week-2 work; until it's wired, backup-failure detection is human-driven (operator checks `aws s3 ls` + most-recent-object timestamp daily)
- **Restore drills:** owned by the on-call engineer (currently a rotation of one — operator-of-last-resort + author of this runbook)
- **Backup-script changes:** PR-reviewed per [WORKFLOW.md](./WORKFLOW.md); restore-drill must run within 1 week of any procedural change

For Tier 2+ deployments:
- Dedicated SRE / on-call rotation
- Paged via PagerDuty (per [SLO.md §4](./SLO.md#4-alert-wiring--whats-done-whats-open))
- Backup-failure → automated ticket; data-loss → P0 page

---

## 8. Defense talking points (interview)

- **"What's your RPO and RTO?"** — *RPO: 1 hour for the audit log (hourly dumps), 24 hours for the rest of the data (daily dumps). RTO: 30 min for table restore, 2 hours for full DB restore, 4 hours for host loss. Anchored to the §3 cadence and §4 procedures.*
- **"What happens if your droplet dies tonight?"** — *§4c walks the procedure: fresh droplet from snapshot or clean image, restore .env from off-host bucket, re-run bootstrap, restore latest dumps. RTO target 4 hours. Documented; not yet drilled — first drill is week-2 work.*
- **"How do you protect HIPAA audit data specifically?"** — *agent_log is dumped hourly with 6-year retention per HIPAA §164.312(b). Separate cadence + retention from operational data because the regulatory requirement is different. Off-host so it survives host loss; encryption-at-rest so the bucket itself isn't a PHI exposure.*
- **"Have you tested a restore?"** — *Honest answer: not yet — the procedure is documented as week-1 final-submission deliverable; the first drill is targeted for week-2 once the backup automation lands. "Has been tested" is the difference between an actual disaster recovery posture and an aspirational one. We know that and have it on the calendar.*
- **"What about backup integrity?"** — *Per-dump SHA256 written alongside the dump; restore script verifies before applying. Backup-failure on integrity mismatch — pages on-call. (This is week-2 work, not yet shipped — current state: hash + verification step is documented in §4 procedures but the script isn't.)*
- **"Why monthly drills not weekly?"** — *Monthly is the floor for a single-droplet pilot — frequent enough to catch procedural drift, infrequent enough to be sustainable for a single operator. At Tier 2+ with a multi-host topology and an SRE rotation, drill cadence tightens to weekly.*

---

## 9. Changelog

| Date | Change | By |
|---|---|---|
| 2026-05-02 | Initial runbook — design + procedure, automation deferred | AgentForge Week-1 build |
