# Droplet deployment

Deploys the full AgentForge stack (OpenEMR + MariaDB + Python agent +
Caddy reverse proxy) on a single Docker droplet. Public URL goes through
Caddy with auto-TLS via `nip.io`. The agent service is internal-network
only — no public exposure.

## One-time setup on a fresh droplet

```bash
# As root on the droplet (DigitalOcean's Docker image has docker preinstalled)
mkdir -p /opt/agentforge
cd /opt/agentforge
git clone https://labs.gauntletai.com/coryvandenberg/agentforge.git repo
cd repo
bash .deploy/bootstrap.sh
```

The bootstrap is **idempotent** — safe to re-run. On first run it:
1. Generates a fresh `.env` at `/opt/agentforge/.env` with strong random
   secrets for MariaDB, OpenEMR admin, the `agent_ro` DB user, and the
   shared HMAC secret.
2. Writes `docker-compose.yml` + `Caddyfile` to `/opt/agentforge/`.
3. Configures UFW to allow only ports 22, 80, 443.
4. Pulls images, builds the agent, starts the stack.
5. Waits for OpenEMR to finish auto-install, then creates the `agent_ro`
   DB user and restarts the agent.

## Required secrets

The bootstrap leaves placeholders for these — **fill them in BEFORE
running** (or fill in after first run, then re-run bootstrap):

```bash
sudo nano /opt/agentforge/.env
```

| Var | Notes |
|---|---|
| `ANTHROPIC_API_KEY` | Anthropic key OR OpenRouter token. Without this, agent loop fails on the LLM call. |
| `ANTHROPIC_BASE_URL` | `https://api.anthropic.com` for direct, `https://openrouter.ai/api` for OpenRouter (no trailing `/v1`). |
| `LANGFUSE_PUBLIC_KEY` | Optional. Without it, traces don't emit but the agent runs. |
| `LANGFUSE_SECRET_KEY` | Optional, paired with public key. |
| `LANGFUSE_HOST` | Default `https://cloud.langfuse.com`. |

Generated automatically and you don't need to touch:

| Var | Notes |
|---|---|
| `OPENEMR_HMAC_SECRET` | Shared secret between PHP module and agent. |
| `MYSQL_ROOT_PASSWORD` / `MYSQL_USER_PASSWORD` / `AGENT_RO_PASSWORD` | DB. |
| `OE_ADMIN_PASSWORD` | OpenEMR `admin` user. Logged to `CREDENTIALS.txt`. |

## Update from local development

After pushing changes to the remote, on the droplet:

```bash
cd /opt/agentforge/repo
git pull
sudo bash .deploy/bootstrap.sh   # rewrites compose file + Caddyfile, restarts stack
docker compose -f /opt/agentforge/docker-compose.yml up -d --build agent  # rebuild agent if its code changed
```

The OpenEMR module is bind-mounted from the repo so PHP/JS/CSS edits
land without rebuild — just reload the chart page.

## Smoke tests

```bash
# Stack health
cd /opt/agentforge
docker compose ps
docker inspect -f '{{.State.Health.Status}}' $(docker compose ps -q openemr)

# Agent reachable on the internal network from openemr
docker compose exec openemr curl -s http://agent:8000/health
# → {"status":"ok","llm_mode":"live"}

# Agent NOT reachable from the public internet
curl -s http://142.93.242.40:8000/health   # should hang or timeout

# OpenEMR public URL serves the chat panel
curl -ksI https://142-93-242-40.nip.io/ | head -1
```

## Common issues

**`agent_ro` doesn't exist yet.** Bootstrap creates it after MariaDB +
OpenEMR install finishes. If the agent comes up before then, it'll log
DB connection errors. Fix: `docker compose restart agent`.

**Agent returns 401 on every chat request.** HMAC mismatch. Both
containers must read the same `OPENEMR_HMAC_SECRET`. The bootstrap
sets it from `.env` — if you edited `.env`, re-run the bootstrap to
update both containers.

**Custom module not visible in OpenEMR.** The bind-mount uses the
repo path. If the repo isn't at `/opt/agentforge/repo`, edit the
`docker-compose.yml` volumes line OR move the repo. After fixing,
`docker compose up -d openemr`.

**Healthcheck timeouts on first boot.** OpenEMR's `flex` install runs
`composer update` and asset compilation on first boot — can take
8-10 min on slow droplets. The bootstrap waits up to 8 min; if it
times out, the stack will still come up healthy a few minutes later.
Monitor with `docker compose logs -f openemr`.
