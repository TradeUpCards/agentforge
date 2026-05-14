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
  AGENT_AUDIT_RW_PASSWORD=$(openssl rand -hex 16)
  OE_ADMIN_PASSWORD=$(openssl rand -hex 12)
  HMAC_SECRET=$(openssl rand -hex 32)

  cat > .env <<EOF
# Public + DB
PUBLIC_HOSTNAME=$PUBLIC_HOSTNAME
MYSQL_ROOT_PASSWORD=$MYSQL_ROOT_PASSWORD
MYSQL_USER_PASSWORD=$MYSQL_USER_PASSWORD
AGENT_RO_PASSWORD=$AGENT_RO_PASSWORD
AGENT_AUDIT_RW_PASSWORD=$AGENT_AUDIT_RW_PASSWORD
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

# Reranker (optional). With key set, hybrid RAG uses Cohere Rerank API.
# Without it, the agent falls back to a local BAAI cross-encoder
# (sentence-transformers, downloaded into hf_cache volume on first run).
# CI runs in BAAI fallback mode, so leaving this empty is supported.
COHERE_API_KEY=

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
    # `flex` image (development variant) bind-mounts the source from
    # the host. Required for running our forked OpenEMR code on the
    # droplet — the upstream `latest` image bakes in stock OpenEMR and
    # is missing class methods (e.g. OEGlobalsBag::getWebRoot) that our
    # custom module + restored database expect. First boot runs
    # composer install + asset compilation (~10-15 min); subsequent
    # boots are fast.
    image: openemr/openemr:flex
    expose:
      - "80"
      - "443"
    volumes:
      - $REPO_DIR:/openemr:ro
      - $REPO_DIR:/var/www/localhost/htdocs/openemr:rw
      # Named volumes for build artifacts so first-boot composer/npm/asset
      # output persists across restarts and doesn't pollute the host repo.
      - assetvolume:/var/www/localhost/htdocs/openemr/public/assets:rw
      - themevolume:/var/www/localhost/htdocs/openemr/public/themes:rw
      - sitevolume:/var/www/localhost/htdocs/openemr/sites:rw
      - nodemodules:/var/www/localhost/htdocs/openemr/node_modules:rw
      - vendordir:/var/www/localhost/htdocs/openemr/vendor:rw
      - logvolume:/var/log
      # The flex image's startup runs `rsync /couchdb/data ...` and
      # exits 23 if the source dir doesn't exist. Mount an empty
      # volume there so the rsync no-ops cleanly. We don't actually
      # use CouchDB; this is just to keep the startup script happy.
      - couchdbvolume:/couchdb/data:rw
    environment:
      MYSQL_HOST: mysql
      MYSQL_ROOT_PASS: \${MYSQL_ROOT_PASSWORD}
      MYSQL_USER: openemr
      MYSQL_PASS: \${MYSQL_USER_PASSWORD}
      OE_USER: admin
      OE_PASS: \${OE_ADMIN_PASSWORD}
      # Triggers the flex image's first-boot composer + asset build.
      EASY_DEV_MODE: "yes"
      EASY_DEV_MODE_NEW: "yes"
      # Wire the PHP module to the Python agent on the internal network.
      AGENT_BASE_URL: "http://agent:8000"
      OPENEMR_HMAC_SECRET: \${OPENEMR_HMAC_SECRET}
    depends_on:
      mysql:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "/usr/bin/curl", "--fail", "--insecure", "--location", "--show-error", "--silent", "https://localhost/meta/health/readyz"]
      start_period: 15m
      start_interval: 15s
      interval: 1m
      timeout: 10s
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

      # Audit-log writer user — INSERT-only on agent_log per AUDIT.md C-1
      # / ARCHITECTURE.md §4.2. Provisioned alongside agent_ro below.
      AGENT_DB_AUDIT_USER: "agent_audit_rw"
      AGENT_DB_AUDIT_PASS: \${AGENT_AUDIT_RW_PASSWORD}

      # Integrity (must match openemr container's value)
      OPENEMR_HMAC_SECRET: \${OPENEMR_HMAC_SECRET}

      USE_FIXTURE_DATA: \${USE_FIXTURE_DATA}
      USE_FIXTURE_LLM: \${USE_FIXTURE_LLM}

      # Per-user abuse controls (post-HMAC). Empty values fall through
      # to config.py defaults: 60 req/min, 200K tokens/hour. Tighten in
      # .env once real clinician usage shape is known.
      AGENT_RATE_LIMIT_RPM: \${AGENT_RATE_LIMIT_RPM}
      AGENT_TOKEN_BUDGET_PER_HOUR: \${AGENT_TOKEN_BUDGET_PER_HOUR}

      # W2 Stage-3a: hybrid RAG vector path. Qdrant runs as a sibling
      # service on the internal network (not publicly exposed).
      # Reranker uses Cohere if COHERE_API_KEY is set; else falls back to
      # BAAI cross-encoder (downloaded into HF_HOME volume on first run).
      QDRANT_HOST: qdrant
      QDRANT_PORT: 6333
      COHERE_API_KEY: \${COHERE_API_KEY}
    volumes:
      # HuggingFace / sentence-transformers / BAAI reranker model cache.
      # Persisted so the ~80 MB model download survives agent restarts.
      - hf_cache:/app/.hf_cache
    depends_on:
      mysql:
        condition: service_healthy
      qdrant:
        condition: service_started

  # W2 Stage-3a: Qdrant vector DB for hybrid (BM25 + dense + RRF) RAG.
  # Internal-network only — agent reaches it at http://qdrant:6333.
  # No healthcheck: qdrant ships a distroless image (no shell/wget) and
  # boots in ~3-5s. The agent connects lazily on first query, and the
  # post-bootstrap corpus ingestion step retries until qdrant is ready.
  qdrant:
    restart: always
    image: qdrant/qdrant:v1.10.0
    expose:
      - "6333"
      - "6334"
    volumes:
      - qdrantvolume:/qdrant/storage

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
  # flex-image build artifacts (first-boot composer + asset compilation).
  assetvolume: {}
  themevolume: {}
  nodemodules: {}
  vendordir: {}
  couchdbvolume: {}
  # W2: Qdrant vector store + agent HF/BAAI model cache.
  qdrantvolume: {}
  hf_cache: {}
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
# Pass current commit SHA into the agent image so /health surfaces a
# deterministic version fingerprint (consumed by W3 Clinical Red Team
# Platform daemon for fingerprint-change detection). Falls back to
# "unknown" if not in a git checkout (e.g. tarball deploy).
VERSION_SHA="$(git rev-parse HEAD 2>/dev/null || echo unknown)"
docker compose build --build-arg VERSION_SHA="$VERSION_SHA" agent

echo "==> Starting containers"
docker compose up -d

# Create the read-only DB user the agent connects with. Idempotent.
# Has to wait until MariaDB is healthy AND OpenEMR has run its install
# to create the openemr schema. ~5 min on first boot.
echo "==> Waiting for openemr container health (up to 18 min — flex image runs"
echo "    composer install + asset compilation on first boot)..."
for i in $(seq 1 108); do
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

echo "==> Applying agent_log schema (audit-trail table per AUDIT.md C-1)..."
# Schema lives inline rather than as a tracked .sql file. The repo's
# *.sql gitignore is a defensive policy against accidentally committing
# database dumps with real PHI (see commit e87ed7b). Keeping the schema
# in this script avoids an exception to that policy and keeps the deploy
# self-contained. Idempotent via CREATE TABLE IF NOT EXISTS.
# Field-by-field rationale: ARCHITECTURE.md §4.2.
docker compose exec -T mysql mariadb -uroot -p"$MYSQL_ROOT_PASSWORD" openemr <<'AGENTLOG_SCHEMA' 2>&1 | grep -v "Using a password" || true
CREATE TABLE IF NOT EXISTS agent_log (
    id                 BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    request_id         CHAR(36) NOT NULL,
    created_at         DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    user_id            INT UNSIGNED NOT NULL,
    patient_id         INT UNSIGNED NOT NULL,
    use_case           VARCHAR(8) NULL,
    prompt             VARCHAR(4000) NULL,
    tools_called       JSON NULL,
    llm_calls          JSON NULL,
    verifier_verdict   VARCHAR(16) NULL,
    claims_passed      INT UNSIGNED NOT NULL DEFAULT 0,
    claims_failed      INT UNSIGNED NOT NULL DEFAULT 0,
    final_response     VARCHAR(8000) NULL,
    total_latency_ms   INT UNSIGNED NOT NULL DEFAULT 0,
    outcome            VARCHAR(16) NOT NULL,
    refusal_reason     VARCHAR(1000) NULL,
    PRIMARY KEY (id),
    INDEX idx_user_patient_date (user_id, patient_id, created_at),
    INDEX idx_request_id (request_id),
    INDEX idx_outcome_date (outcome, created_at)
)
ENGINE=InnoDB
DEFAULT CHARSET=utf8mb4
COLLATE=utf8mb4_unicode_ci
COMMENT='AgentForge PHI access audit trail. See ARCHITECTURE.md §4.2.';
AGENTLOG_SCHEMA

echo "==> Creating agent_audit_rw DB user (INSERT-only on agent_log, idempotent)..."
docker compose exec -T mysql mariadb -uroot -p"$MYSQL_ROOT_PASSWORD" -e "
  CREATE USER IF NOT EXISTS 'agent_audit_rw'@'%' IDENTIFIED BY '$AGENT_AUDIT_RW_PASSWORD';
  GRANT INSERT ON openemr.agent_log TO 'agent_audit_rw'@'%';
  FLUSH PRIVILEGES;
" 2>&1 | grep -v "Using a password" || true

# Restart the agent so it picks up the now-existing DB users + table.
docker compose restart agent

# Wait for the agent to come back healthy before running corpus ingestion.
# Without this, `docker compose exec agent` can race the restart.
echo "==> Waiting for agent service to be healthy after restart..."
for i in $(seq 1 30); do
  if docker compose exec -T agent python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=2)" 2>/dev/null; then
    break
  fi
  sleep 5
done

# W2: ingest the guideline corpus into Qdrant. Idempotent — the ingest
# script drops + recreates the collection, so re-running on each bootstrap
# guarantees the index matches the current corpus on disk.
# First run downloads the sentence-transformers embedding model
# (all-MiniLM-L6-v2, ~80 MB) and BAAI reranker into the hf_cache volume,
# so this step is slow on first boot (3-5 min) and fast thereafter.
echo "==> Ingesting guideline corpus into Qdrant (first run downloads model, ~3-5 min)..."
docker compose exec -T agent python -m agent.corpus.ingest || {
  echo "==> WARNING: corpus ingestion failed. Run manually after stack is up:"
  echo "      cd $DEPLOY_DIR && docker compose exec agent python -m agent.corpus.ingest"
}

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
