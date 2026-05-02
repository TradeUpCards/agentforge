# `.github/workflows/` — CI/CD Workflow Inventory

This directory holds GitHub Actions workflow files. Some are inherited from upstream OpenEMR; some are AgentForge-specific. This README is the map.

## AgentForge workflows (added by this fork)

| Workflow | Trigger | Purpose | Status |
|---|---|---|---|
| [`agent-eval.yml`](./agent-eval.yml) | PR to master + push to `master`/`agentforge/**` | Runs verifier unit tests + PHI mask unit tests + eval Golden Set (smoke + full tier) in fixture mode. Mirrors local pre-commit hook behavior. | ✅ Working — no secrets needed |
| [`agent-deploy.yml`](./agent-deploy.yml) | Manual (`workflow_dispatch`) only | SSH deploy to the DigitalOcean droplet with health-check + auto-rollback on failure. Implements the deploy pattern that ARCHITECTURE.md §8.2 sketches. | ⚠️ Stub — fail-fast Preflight step until 4 secrets are configured (see workflow header) |

## Upstream OpenEMR workflows (inherited from openemr/openemr)

These workflows reference `openemr/openemr` CI infrastructure (test runners, Docker images, etc.). They were preserved when this fork was cut and are listed here for awareness — they don't gate AgentForge changes:

- `api-docs.yml` · `build-dev-php-fpm-docker.yml` · `composer.yml` · `composer-require-checker.yml` · `conventional-commits.yml` · `database.yml` · `database-version.yml` · `docker-compose-lint.yml` · `hadolint.yml` · `inferno-test.yml` · plus `js-test.yml`, `phpstan.yml`, `rector.yml`, `shellcheck.yml`, `styling.yml`, `syntax.yml`, `test.yml` if present

## Status against the ARCHITECTURE / system-architecture-review production blockers

The 2026-05-02 system-architecture-review named four production blockers; this directory addresses the first one:

1. ✅ **Wire the planned GitHub Actions CI/CD pipeline.** Done — `agent-eval.yml` covers the eval gate; `agent-deploy.yml` documents the deploy pattern as a manual-trigger stub awaiting SSH-secret wiring (the actual cutover from manual SSH to the workflow is operational work, not architectural).
2. ⚠️ Author a formal SLO doc + wire alerting on existing Langfuse custom metrics. *(Not in this directory — a separate doc.)*
3. ⚠️ Backup/restore automation + restore-drill cadence. *(Not in this directory — operational work.)*
4. ⚠️ Sign Anthropic + Langfuse BAAs. *(Out-of-band procurement, not code.)*

## Operational notes

- **Pre-commit hook still runs locally** via `scripts/git-hooks/pre-commit` (smoke tier only, ~4s). The workflow runs the same tests in CI as a defense-in-depth — if a contributor bypasses pre-commit with `--no-verify`, the PR gate still catches a regression.
- **Eval reports are uploaded as artifacts** on every workflow run (success or failure) — see the `eval-report-<run_id>` artifact in the run summary. Retention: 14 days.
- **Concurrency:** both workflows are `cancel-in-progress: true` (eval) or `cancel-in-progress: false` (deploy — never cancel a deploy mid-flight).
- **No nightly tier in CI yet.** Live-LLM + live-DB cases (17 of 26 in the eval suite) require Anthropic API keys + a Synthea-loaded MariaDB and run only on demand via the local CLI runner. A weekly cron is named in EVAL_SUITE.md §6 #8 as a week-2+ candidate.

## Configuring `agent-deploy.yml` for first use

Before the deploy workflow can do anything, four GitHub Actions secrets must be configured in **Settings > Secrets and variables > Actions**:

| Secret | Example | What it is |
|---|---|---|
| `DROPLET_HOST` | `142.93.242.40` | Droplet IP or hostname |
| `DROPLET_SSH_USER` | `root` | SSH user with permission to `cd $DEPLOY_PATH && docker compose up -d` |
| `DROPLET_SSH_KEY` | (private key contents) | Paste the *contents* of an OpenSSH private key whose public half is in the droplet's `authorized_keys` |
| `DROPLET_DEPLOY_PATH` | `/opt/agentforge` | Repo path on the droplet that already has the agent's `docker-compose.yml` |

Until those land, the workflow's Preflight step exits with `::error::Missing required secrets: ...`. That's intentional — the workflow exists so the deploy pattern is visible and reviewable, but it does not silently no-op or partially-deploy.

Recommended companion: create a `production` GitHub Environment (Settings > Environments > New environment) and require manual approval for deploys to it. The workflow already declares `environment: production`, so once the Environment exists and an approver list is configured, every deploy needs human sign-off in addition to the `confirm: DEPLOY` typed input.
