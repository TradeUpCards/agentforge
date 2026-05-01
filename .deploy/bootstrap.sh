#!/bin/bash
# Bootstrap AgentForge demo deployment on a fresh DigitalOcean Docker droplet.
# Idempotent: safe to re-run.
#
# Layout on the droplet:
#   /opt/agentforge/         <- deploy state (.env, compose file, Caddyfile)
#   /opt/agentforge/repo/    <- the AgentForge git checkout
#
# Run from inside the repo on the droplet:
#   cd /opt/agentforge/repo
#   sudo bash .deploy/bootstrap.sh
#
# Required secrets (set in /opt/agentforge/.env BEFORE running, or the
# script will create placeholders that you must fill in):
#   ANTHROPIC_API_KEY     - LLM access (Anthropic or OpenRouter token)
#   ANTHROPIC_BASE_URL    - https://api.anthropic.com or https://openrouter.ai/api
#   OPENEMR_HMAC_SECRET   - shared secret between PHP module and Python agent
#   LANGFUSE_PUBLIC_KEY   - observability (optional; agent runs without)
#   LANGFUSE_SECRET_KEY   - observability (optional)
#   LANGFUSE_HOST         - default https://cloud.langfuse.com
set -euo pipefail

PUBLIC_IP="142.93.242.40"
PUBLIC_HOSTNAME="142-93-242-40.nip.io"
DEPLOY_DIR="/opt/agentforge"
REPO_DIR="$DEPLOY_DIR/repo"

echo "==> Setting up at $DEPLOY_DIR for $PUBLIC_HOSTNAME"

mkdir -p "$DEPLOY_DIR"
cd "$DEPLOY_DIR"

# Generate strong secrets only on first run; preserve across re-runs.
if [[ ! -f .env ]]; then
  MYSQL_ROOT_PASSWORD=$(openssl rand -hex 16)
  MYSQL_USER_PASSWORD=$(openssl rand -hex 16)
  AGENT_RO_PASSWORD=$(openssl rand -hex 16)
  OE_ADMIN_PASSWORD=$(openssl rand -hex 12)
  HMAC_SECRET=$(openssl rand -hex 32)

  cat > .env <<EOF
# Public + DB
PUBLIC_HOSTNAME=$PUBLIC_HOSTNAME
MYSQL_ROOT_PASSWORD=$MYSQL_ROOT_PASSWORD
MYSQL_USER_PASSWORD=$MYSQL_USER_PASSWORD
AGENT_RO_PASSWORD=$AGENT_RO_PASSWORD
OE_ADMIN_PASSWORD=$OE_ADMIN_PASSWORD

# Agent integrity (shared secret between PHP module and Python agent)
OPENEMR_HMAC_SECRET=$HMAC_SECRET

# LLM provider — fill these in before the agent service comes up healthy.
# Direct Anthropic:   ANTHROPIC_BASE_URL=https://api.anthropic.com
# OpenRouter:         ANTHROPIC_BASE_URL=https://openrouter.ai/api
ANTHROPIC_API_KEY=
ANTHROPIC_BASE_URL=https://api.anthropic.com
ANTHROPIC_MODEL_REASONING=claude-sonnet-4-5
ANTHROPIC_MODEL_WORKHORSE=claude-haiku-4-5

# Observability (optional). Get keys from https://cloud.langfuse.com.
# Without these, the agent runs but doesn't emit traces.
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
LANGFUSE_HOST=https://cloud.langfuse.com

# Data mode in production = real DB.
USE_FIXTURE_DATA=false
USE_FIXTURE_LLM=false
EOF
  chmod 600 .env

  cat > CREDENTIALS.txt <<EOF
=============================================
AgentForge Demo - Login Credentials
=============================================
Public URL:        https://$PUBLIC_HOSTNAME
OpenEMR username:  admin
OpenEMR password:  $OE_ADMIN_PASSWORD
=============================================
EOF
  chmod 600 CREDENTIALS.txt

  echo ""
  echo "==> WARNING: created .env with placeholders for ANTHROPIC_API_KEY"
  echo "==> and (optional) LANGFUSE_*. Fill these in BEFORE the next"
  echo "==> 'docker compose up' or the agent will run in degraded mode."
  echo ""
fi

# Ensure repo is checked out at $REPO_DIR. Used for bind-mounting the
# OpenEMR custom module + as the build context for the agent image.
if [[ ! -d "$REPO_DIR/.git" ]]; then
  echo "==> Repo not found at $REPO_DIR. Clone it first:"
  echo "       git clone <repo-url> $REPO_DIR"
  exit 1
fi

cat > docker-compose.yml <<COMPOSE
services:
  mysql:
    restart: always
    image: mariadb:11.8.6
    command: ['mariadbd', '--character-set-server=utf8mb4']
    volumes:
      - databasevolume:/var/lib/mysql
    environment:
      MYSQL_ROOT_PASSWORD: \${MYSQL_ROOT_PASSWORD}
    healthcheck:
      test: ["CMD", "/usr/local/bin/healthcheck.sh", "--su-mysql", "--connect", "--innodb_initialized"]
      start_period: 1m
      start_interval: 10s
      interval: 1m
      timeout: 5s
      retries: 3

  openemr:
    restart: always
    image: openemr/openemr:latest
    expose:
      - "80"
      - "443"
    volumes:
      - logvolume:/var/log
      - sitevolume:/var/www/localhost/htdocs/openemr/sites
      # Bind-mount our custom module from the repo so the chat panel,
      # event subscribers, and chart-bootstrap.js are available inside
      # the openemr container without baking a custom image.
      - $REPO_DIR/interface/modules/custom_modules/oe-module-clinical-copilot:/var/www/localhost/htdocs/openemr/interface/modules/custom_modules/oe-module-clinical-copilot:ro
    environment:
      MYSQL_HOST: mysql
      MYSQL_ROOT_PASS: \${MYSQL_ROOT_PASSWORD}
      MYSQL_USER: openemr
      MYSQL_PASS: \${MYSQL_USER_PASSWORD}
      OE_USER: admin
      OE_PASS: \${OE_ADMIN_PASSWORD}
      # Wire the PHP module to the Python agent on the internal network.
      AGENT_BASE_URL: "http://agent:8000"
      OPENEMR_HMAC_SECRET: \${OPENEMR_HMAC_SECRET}
    depends_on:
      mysql:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "/usr/bin/curl", "--fail", "--insecure", "--location", "--show-error", "--silent", "https://localhost/meta/health/readyz"]
      start_period: 5m
      start_interval: 10s
      interval: 1m
      timeout: 5s
      retries: 3

  agent:
    restart: always
    build:
      context: $REPO_DIR/agent
      dockerfile: Dockerfile
    expose:
      - "8000"
    # Internal-network only by design (no host port mapping). The
    # OpenEMR container reaches it at http://agent:8000.
    environment:
      AGENT_HOST: "0.0.0.0"
      AGENT_PORT: "8000"
      AGENT_LOG_LEVEL: "info"

      # LLM provider
      ANTHROPIC_API_KEY: \${ANTHROPIC_API_KEY}
      ANTHROPIC_BASE_URL: \${ANTHROPIC_BASE_URL}
      ANTHROPIC_MODEL_REASONING: \${ANTHROPIC_MODEL_REASONING}
      ANTHROPIC_MODEL_WORKHORSE: \${ANTHROPIC_MODEL_WORKHORSE}

      # Observability
      LANGFUSE_PUBLIC_KEY: \${LANGFUSE_PUBLIC_KEY}
      LANGFUSE_SECRET_KEY: \${LANGFUSE_SECRET_KEY}
      LANGFUSE_HOST: \${LANGFUSE_HOST}

      # DB (read-only user; created post-install by the SQL init below)
      AGENT_DB_HOST: "mysql"
      AGENT_DB_PORT: "3306"
      AGENT_DB_USER: "agent_ro"
      AGENT_DB_PASS: \${AGENT_RO_PASSWORD}
      AGENT_DB_NAME: "openemr"

      # Integrity (must match openemr container's value)
      OPENEMR_HMAC_SECRET: \${OPENEMR_HMAC_SECRET}

      USE_FIXTURE_DATA: \${USE_FIXTURE_DATA}
      USE_FIXTURE_LLM: \${USE_FIXTURE_LLM}
    depends_on:
      mysql:
        condition: service_healthy

  caddy:
    image: caddy:2-alpine
    restart: always
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data
      - caddy_config:/config
    environment:
      PUBLIC_HOSTNAME: \${PUBLIC_HOSTNAME}
    depends_on:
      - openemr

volumes:
  logvolume: {}
  sitevolume: {}
  databasevolume: {}
  caddy_data: {}
  caddy_config: {}
COMPOSE

cat > Caddyfile <<'CADDY'
{$PUBLIC_HOSTNAME} {
    reverse_proxy https://openemr:443 {
        transport http {
            tls
            tls_insecure_skip_verify
        }
    }
}
CADDY

echo "==> Configuring UFW (allow 22, 80, 443)"
ufw --force reset >/dev/null
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw allow 22/tcp >/dev/null
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null

echo "==> Pulling images + building agent (~3-5 min)"
docker compose pull --quiet --ignore-buildable
docker compose build agent

echo "==> Starting containers"
docker compose up -d

# Create the read-only DB user the agent connects with. Idempotent.
# Has to wait until MariaDB is healthy AND OpenEMR has run its install
# to create the openemr schema. ~5 min on first boot.
echo "==> Waiting for openemr container health (up to 8 min)..."
for i in $(seq 1 48); do
  status=$(docker inspect -f '{{.State.Health.Status}}' "$(docker compose ps -q openemr)" 2>/dev/null || echo "starting")
  if [[ "$status" == "healthy" ]]; then break; fi
  sleep 10
done

echo "==> Creating agent_ro DB user (idempotent)..."
. ./.env
docker compose exec -T mysql mariadb -uroot -p"$MYSQL_ROOT_PASSWORD" -e "
  CREATE USER IF NOT EXISTS 'agent_ro'@'%' IDENTIFIED BY '$AGENT_RO_PASSWORD';
  GRANT SELECT ON openemr.* TO 'agent_ro'@'%';
  FLUSH PRIVILEGES;
" 2>&1 | grep -v "Using a password" || true

# Restart the agent so it picks up the now-existing DB user.
docker compose restart agent

echo ""
echo "==> Stack is up. First boot takes 5-8 min while OpenEMR auto-installs."
echo ""
cat CREDENTIALS.txt
echo ""
echo "Monitor:    cd $DEPLOY_DIR && docker compose ps"
echo "OE logs:    cd $DEPLOY_DIR && docker compose logs -f openemr"
echo "Agent logs: cd $DEPLOY_DIR && docker compose logs -f agent"
echo "Caddy log:  cd $DEPLOY_DIR && docker compose logs -f caddy"
echo "Health:     docker inspect -f '{{.State.Health.Status}}' \$(docker compose ps -q openemr)"
echo ""
echo "Smoke test: curl -s http://localhost:8000 from inside the openemr"
echo "container would reach the agent (internal network only, not"
echo "publicly exposed). Use 'docker compose exec openemr curl http://agent:8000/health'."
