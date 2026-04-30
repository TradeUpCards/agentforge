# AgentForge — Python agent service

The Python AI agent that powers the Clinical Co-Pilot inside OpenEMR. Runs as
its own FastAPI service, internal-network-only on the deployed stack.

> Companion documents: [`prd.md`](../.gauntlet/week1/prd.md), [`tasks.md`](../.gauntlet/week1/tasks.md), [`ARCHITECTURE.md`](../ARCHITECTURE.md).

## Layout

```
agent/
├── main.py            FastAPI app — POST /chat, GET /health
├── agent.py           Composite-tool fetch + LLM synthesis + verifier handoff
├── verifier.py        Post-generation deterministic verifier (the differentiator)
├── tools.py           Data tools (fixture mode now; real DB queries land Phase 5)
├── llm_client.py      LLM client abstraction (Anthropic real / fixture)
├── schemas.py         Pydantic DTOs — wire format for /chat
├── config.py          .env loading + typed Settings
├── fixtures/llm/      Canned LLM responses for path-ii dev mode
└── tests/             Pytest unit + integration tests
```

## Running locally

```bash
cd agent
python -m venv venv
source venv/Scripts/activate     # Windows / Git Bash
# OR: source venv/bin/activate    # macOS / Linux
python -m pip install -r requirements.txt

# Copy and edit secrets
cp .env.example .env
# Paste your LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, ANTHROPIC_API_KEY (when available)

# Run the agent (from repo root, NOT from agent/)
cd ..
uvicorn agent.main:app --reload --host 0.0.0.0 --port 8000
```

Visit http://localhost:8000/health — should return `{"status":"ok","llm_mode":"fixture"}`.

## Local OpenEMR integration

The agent runs as a separate process from OpenEMR locally. The OpenEMR module
needs to know how to reach the agent and what HMAC secret to use. Both are
wired via env vars in `docker/development-easy/docker-compose.override.yml`
(gitignored — copy and edit as needed):

```yaml
services:
  openemr:
    environment:
      AGENT_BASE_URL: "http://host.docker.internal:8000"
      OPENEMR_HMAC_SECRET: "<same value as agent/.env>"
    extra_hosts:
      - "host.docker.internal:host-gateway"
```

**Why `host.docker.internal`** — the dev-easy stack runs OpenEMR in Docker,
the agent runs as a uvicorn process on the host. Docker Desktop maps
`host.docker.internal` to the host machine so containers can reach back to
host-bound ports. On Linux Docker Engine (no Desktop), the `extra_hosts`
mapping above adds the same alias.

When the agent ships to the droplet, both services run inside the same
Docker network and the URL in `/opt/agentforge/.env` becomes
`http://agent:8000` (the default). No `host.docker.internal` needed.

After editing the override, restart the openemr container:
```bash
cd /c/Dev/GauntletAI/AgentForge/docker/development-easy
docker compose up -d openemr
```

## Path-ii dev mode (no LLM key today)

When `ANTHROPIC_API_KEY` is empty, the agent runs in **fixture mode**: the
LLM call returns a canned response from `fixtures/llm/default_pre_visit_brief.json`,
and the data tools return canned `RetrievedRecord` objects. The full agent
loop is exercised end-to-end — HMAC verification, tool dispatch, LLM call,
verifier — without burning tokens or needing a DB.

When the real Anthropic key is pasted into `.env`, the agent automatically
flips to live mode (`USE_FIXTURE_LLM=auto`).

## Testing

From repo root:

```bash
source agent/venv/Scripts/activate
python -m pytest agent/tests/ -v
```

Three test groups:
- `tests/unit/test_verifier.py` — verifier matching logic, no LLM
- `tests/test_chat_endpoint.py` — full `/chat` round-trip via FastAPI TestClient
- `tests/eval/` — eval suite (Phase 6, lands at early submission)

## HMAC convention

Every `/chat` request must carry an HMAC-SHA256 signature. The OpenEMR
integration module computes:

```
payload = f"{user_id}|{patient_id}|" + "|".join(message_contents)
hmac = HMAC_SHA256(OPENEMR_HMAC_SECRET, payload).hexdigest()
```

The agent verifies this before any tool runs. Agent has no public network exposure,
but HMAC adds a defense-in-depth check that the request actually came from the
OpenEMR module rather than another container on the docker network.

## Environment variables

See [.env.example](./.env.example) for the full list. Highlights:

- `ANTHROPIC_API_KEY` — leave blank for fixture mode; paste real key for live
- `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST` — observability
- `AGENT_DB_*` — local dev-easy MariaDB defaults (port 8320 on host)
- `OPENEMR_HMAC_SECRET` — pre-shared with the OpenEMR module
- `USE_FIXTURE_LLM` — `auto` (default), `true` (force fixtures), `false` (require live)
