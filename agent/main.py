"""FastAPI entry point for the AgentForge Clinical Co-Pilot agent service.

Endpoints:
- POST /chat   — multi-turn conversational endpoint (UC1/UC2/UC3 all flow through here)
- GET  /health — liveness probe (used by docker compose healthcheck later)

Service is internal-network-only on the deployed stack (per prd.md §5).
The OpenEMR integration module signs requests with HMAC; this service
verifies the signature before any tool runs.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .agent import record_score, run_chat, verify_score_hmac
from .config import get_settings
from .schemas import (
    AgentResponse,
    ChatRequest,
    RefusalResponse,
    ScoreRequest,
    ScoreResponse,
)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("agent")


# ---------------------------------------------------------------------------
# Langfuse (optional — disabled if keys missing)
# ---------------------------------------------------------------------------


def _maybe_init_langfuse() -> None:
    """Initialize Langfuse SDK if keys are configured.

    Langfuse picks env vars up automatically; we just validate they're
    present and log status. The SDK is initialized via the @observe decorator
    pattern in the agent loop where applicable.
    """
    settings = get_settings()
    if settings.langfuse_public_key and settings.langfuse_secret_key:
        logger.info(
            "Langfuse configured (host=%s); traces enabled.",
            settings.langfuse_host,
        )
    else:
        logger.info("Langfuse keys not configured; observability disabled.")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    _maybe_init_langfuse()
    mode = "FIXTURE" if settings.use_fixture_llm else "LIVE"
    logger.info(
        "AgentForge agent starting on %s:%s (LLM mode=%s, model=%s)",
        settings.host,
        settings.port,
        mode,
        settings.model_reasoning,
    )
    yield
    # No shutdown work for week 1.


app = FastAPI(
    title="AgentForge Clinical Co-Pilot",
    version="0.1.0",
    description=(
        "Multi-turn conversational AI agent for OpenEMR. "
        "All requests must be signed with HMAC by the OpenEMR integration module."
    ),
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict[str, str]:
    settings = get_settings()
    return {
        "status": "ok",
        "llm_mode": "fixture" if settings.use_fixture_llm else "live",
    }


@app.post("/chat", response_model=None)
async def chat(request: ChatRequest) -> AgentResponse | RefusalResponse:
    """Single agent turn — returns the next assistant message."""
    try:
        result = await run_chat(request)
    except Exception as exc:
        logger.exception("Unhandled error in /chat")
        # Per ARCHITECTURE.md §7: never expose raw exception messages to user.
        raise HTTPException(
            status_code=500,
            detail="The agent encountered an internal error. Please retry.",
        ) from exc
    return result


@app.post("/score", response_model=ScoreResponse)
async def score(request: ScoreRequest) -> ScoreResponse:
    """Attach a client-side metric to an existing Langfuse trace.

    Used by the OpenEMR chat panel to record visual-render latency
    (browser click → DOM update) onto the same trace that captured
    the agent-side latency, so end-to-end timing lives in one place.
    """
    settings = get_settings()
    if not verify_score_hmac(
        request.user_id,
        request.patient_id,
        request.trace_id,
        request.name,
        request.value,
        request.hmac,
        settings.openemr_hmac_secret,
    ):
        raise HTTPException(status_code=403, detail="Invalid signature.")
    await record_score(
        trace_id=request.trace_id,
        name=request.name,
        value=request.value,
        comment=request.comment,
    )
    return ScoreResponse()


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"status": "error", "detail": exc.detail},
    )
