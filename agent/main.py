"""FastAPI entry point for the AgentForge Clinical Co-Pilot agent service.

Endpoints:
- POST /chat   — multi-turn conversational endpoint (UC1/UC2/UC3 all flow through here)
- GET  /health — liveness probe (used by docker compose healthcheck later)

Service is internal-network-only on the deployed stack (per prd.md §5).
The OpenEMR integration module signs requests with HMAC; this service
verifies the signature before any tool runs.
"""

from __future__ import annotations

import hashlib
import logging
import sys
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

from .agent import record_score, run_chat, verify_attach_hmac, verify_hmac, verify_score_hmac
from ._phi_scrubber import mask_observability_patterns
from .config import get_settings
from .document_schemas import IntakeForm, LabReport
from .extractors import attach_and_extract_async, attach_and_extract_with_metadata_async
from .graph.builder import build_supervisor_graph
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

    We instantiate the client explicitly (via agent.agent._langfuse) at
    startup so the PHI mask is wired into the SDK singleton BEFORE any
    @observe-decorated function fires. Without this, the SDK would
    self-init on first @observe call without the mask.
    """
    from agent.agent import _langfuse

    settings = get_settings()
    if settings.langfuse_public_key and settings.langfuse_secret_key:
        client = _langfuse()
        logger.info(
            "Langfuse configured (host=%s); traces enabled. PHI mask: %s",
            settings.langfuse_host,
            "active" if client is not None else "disabled (init failed)",
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


# ---------------------------------------------------------------------------
# Supervisor graph — compiled once at module load so /graph_chat calls are
# cheap (no recompile per request).
# ---------------------------------------------------------------------------

_compiled_graph = build_supervisor_graph()


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


# ---------------------------------------------------------------------------
# /attach_and_extract — Stage-4a multipart ingestion endpoint
#
# Replay-window constant: ±300s (5 minutes), wider than the /chat endpoint's
# 30s window to accommodate slower front-desk upload workflows where PHP and
# Python may be separated by more network hops than a same-container chat call.
# ---------------------------------------------------------------------------

_ATTACH_HMAC_MAX_AGE_SECONDS = 300
"""Reject /attach_and_extract requests whose signed timestamp is more than
this many seconds off the agent's clock. 300s (5 min) is generous for
front-desk upload flows; tighten if the PHP implementer confirms tighter
clock sync is achievable on the deployed stack."""

_SUPPORTED_DOC_TYPES = frozenset({"lab_pdf", "intake_form"})

# Sentinel range re-exported for the endpoint (mirrors extractors/__init__.py).
# Expanded 2026-05-09 from [999_100, 999_199] (100 slots, 4 hardcoded
# personas) to [999_001, 999_999] (999 slots) so the agent can be invoked
# for any pid in the demo install (real pid p auto-maps to sentinel
# 999_000+p via PersonaMap::sentinelId on the PHP side).
_SENTINEL_MIN = 999_001
_SENTINEL_MAX = 999_999


async def _dispatch_extraction_via_supervisor_graph(
    patient_id: int,
    doc_ref_id: str,
    doc_type: str,
    pdf_path: "Path",
    triggered_by_value: str,
    parent_extraction_id: int | None,
) -> dict:
    """Invoke the LangGraph supervisor for a document-extraction request.

    Used by /extract_via_graph — the demonstration endpoint that exercises
    PRD §3's runtime-graph promise (supervisor + intake_extractor +
    evidence_retriever).  /attach_and_extract takes a direct call path
    for production demo latency; this helper is the graph-routed
    equivalent.

    State setup
    -----------
      - The supervisor recognises this as an extraction request because
        tool_calls_accumulated[0] carries a 'doc_ref_id'.  The
        deterministic short-circuit in supervisor_node() routes to
        intake_extractor without an LLM call (saves a Haiku invocation,
        ~200-800ms + cost).
      - intake_extractor reads the same entry to find the doc context
        (_find_doc_context), runs attach_and_extract_with_metadata_async
        through its thread-pool wrapper, writes the full payload into
        state.extraction_payload.
      - Supervisor sees extraction_payload != None on its second visit
        and sets terminal_reason='extraction_complete'.  Builder routes
        to END (responder skipped — extractions don't need synthesis).

    Returns the extraction_payload dict (same shape that
    attach_and_extract_with_metadata_async returns plus n_blocks /
    n_pages / extraction_confidence_avg) so the calling endpoint can
    build its HTTP response identically to /attach_and_extract.

    Raises RuntimeError when the graph terminates without writing a
    payload — unexpected, indicates a routing or worker bug.

    PHI: extraction_payload carries patient values; held in graph
    memory only.  NEVER logged or traced.
    """
    initial_state: dict[str, Any] = {
        "query": "",  # not used on extraction path
        "patient_id": patient_id,
        "tool_calls_accumulated": [{
            "name": "document_upload",
            "doc_ref_id": doc_ref_id,
            "patient_id": patient_id,
            "doc_type": doc_type,
            "pdf_path": str(pdf_path),
            "triggered_by": triggered_by_value,
            "parent_extraction_id": parent_extraction_id,
        }],
        "citations": [],
        "hops_taken": 0,
        "terminal_reason": None,
        "worker_results": [],
        "final_response": None,
        "node_observability": [],
        "retrieved_records": [],
        "extraction_payload": None,
        "_pending_route": None,
    }

    final_state = _compiled_graph.invoke(initial_state)

    payload = final_state.get("extraction_payload")
    if not isinstance(payload, dict):
        raise RuntimeError(
            "supervisor graph terminated without an extraction_payload — "
            "routing or worker bug"
        )
    return payload


@app.post("/attach_and_extract", response_model=None)
async def attach_and_extract_endpoint(
    request: Request,
    patient_id: int = Form(...),
    doc_ref_id: str = Form(...),
    doc_type: str = Form(...),
    file: UploadFile = Form(...),
) -> JSONResponse:
    """Accept a scanned document (PDF/PNG), verify HMAC, run the two-stage
    extraction pipeline (Docling layout → Haiku schema), and return the
    structured extraction.

    Security controls (mirrors /chat and /score patterns):
    - X-OpenEMR-HMAC header verification (sha256, constant-time compare)
    - Replay-window gate: ±300s on X-OpenEMR-Timestamp
    - Sentinel patient_id range: 999001–999999 only
    - doc_type allowlist: "lab_pdf" | "intake_form"
    - Exception scrubbing via mask_observability_patterns + raise from None
      (Stage-2 fix #1 pattern) — no raw doc text or PHI in error responses

    No FHIR persistence (Thursday). No drawer rendering. No bbox overlay.
    Extraction is dispatched directly to attach_and_extract_async() — the
    supervisor graph is not involved in this path.
    """
    request_id = str(uuid.uuid4())
    t0 = time.perf_counter()

    # --- Read and verify headers before touching the multipart body. --------
    user_id_raw = request.headers.get("X-OpenEMR-User-Id", "")
    timestamp_raw = request.headers.get("X-OpenEMR-Timestamp", "")
    hmac_header = request.headers.get("X-OpenEMR-HMAC", "")

    # P3 reprocess-flow headers (PRD §7.3).  Both optional; absent on initial
    # uploads, present when the PHP reprocess endpoint POSTs to this route.
    # Workflow metadata only — NOT part of the HMAC payload formula
    # (verify_attach_hmac at agent.py:435 hashes only the file + identifiers).
    is_reprocess = request.headers.get("X-OpenEMR-Reprocess", "").strip().lower() == "true" \
        or request.headers.get("X-OpenEMR-Reprocess", "").strip() == "1"
    parent_extraction_id_raw = request.headers.get("X-OpenEMR-Parent-Extraction-Id", "")
    parent_extraction_id: int | None = None
    if is_reprocess:
        try:
            parent_extraction_id = int(parent_extraction_id_raw)
            if parent_extraction_id <= 0:
                raise ValueError("non-positive")
        except (ValueError, TypeError):
            return JSONResponse(
                status_code=400,
                content={
                    "status": "refused",
                    "reason": "reprocess_missing_parent_id",
                    "request_id": request_id,
                },
            )
    triggered_by_value = "manual_retry" if is_reprocess else "initial"

    # Parse user_id: must be a valid integer.
    try:
        user_id = int(user_id_raw)
    except (ValueError, TypeError):
        return JSONResponse(
            status_code=400,
            content={
                "status": "refused",
                "reason": "missing_or_invalid_user_id",
                "request_id": request_id,
            },
        )

    # Parse timestamp.
    try:
        timestamp = int(timestamp_raw)
    except (ValueError, TypeError):
        return JSONResponse(
            status_code=400,
            content={
                "status": "refused",
                "reason": "missing_or_invalid_timestamp",
                "request_id": request_id,
            },
        )

    # --- Replay-window check (before reading file bytes). -------------------
    now = time.time()
    age = now - timestamp
    if abs(age) > _ATTACH_HMAC_MAX_AGE_SECONDS:
        logger.info(
            "/attach_and_extract: stale timestamp rejected",
            extra={
                "request_id": request_id,
                "age_seconds": int(age),
                "doc_type": doc_type,
            },
        )
        return JSONResponse(
            status_code=401,
            content={
                "status": "refused",
                "reason": "stale_timestamp",
                "request_id": request_id,
            },
        )

    # --- Sentinel patient_id range check. -----------------------------------
    if not (_SENTINEL_MIN <= patient_id <= _SENTINEL_MAX):
        logger.info(
            "/attach_and_extract: patient_id outside sentinel range",
            extra={
                "request_id": request_id,
                "patient_id_in_sentinel_range": False,
                "doc_type": doc_type,
            },
        )
        return JSONResponse(
            status_code=400,
            content={
                "status": "refused",
                "reason": "patient_id_out_of_sentinel_range",
                # Intentionally no echo of the offending pid (Stage-2 fix #2 pattern).
                "request_id": request_id,
            },
        )

    # --- doc_type allowlist check. ------------------------------------------
    if doc_type not in _SUPPORTED_DOC_TYPES:
        return JSONResponse(
            status_code=400,
            content={
                "status": "refused",
                "reason": "unsupported_doc_type",
                "request_id": request_id,
            },
        )

    # --- Read file bytes (deferred until after cheap checks pass). ----------
    file_bytes = await file.read()

    # --- Compute file hash for HMAC. ----------------------------------------
    file_sha256_hex = hashlib.sha256(file_bytes).hexdigest()

    # --- HMAC verification. -------------------------------------------------
    settings = get_settings()
    hmac_ok = verify_attach_hmac(
        user_id=user_id,
        patient_id=patient_id,
        doc_ref_id=doc_ref_id,
        doc_type=doc_type,
        timestamp=timestamp,
        file_sha256_hex=file_sha256_hex,
        hmac_str=hmac_header,
        secret=settings.openemr_hmac_secret,
    )
    if not hmac_ok:
        logger.info(
            "/attach_and_extract: HMAC verification failed",
            extra={
                "request_id": request_id,
                "doc_type": doc_type,
                "file_sha256_hex": file_sha256_hex,
            },
        )
        return JSONResponse(
            status_code=401,
            content={
                "status": "refused",
                "reason": "invalid_signature",
                "request_id": request_id,
            },
        )

    # --- Dispatch extraction. -----------------------------------------------
    # Write bytes to a temp file so attach_and_extract_async can use a Path.
    import tempfile
    import os as _os
    from pathlib import Path

    suffix = ".pdf" if file_bytes[:4] == b"%PDF" else ".png"
    tmp_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(file_bytes)
            tmp_path = Path(tmp.name)

        logger.info(
            "/attach_and_extract: dispatching extraction",
            extra={
                "request_id": request_id,
                "doc_type": doc_type,
                "file_sha256_hex": file_sha256_hex,
                "patient_id_in_sentinel_range": True,
            },
        )

        # P3: switch to the metadata-returning variant so the response can
        # include docling_blocks (full block inventory for the "Possibly
        # Missed" review pane) and field_verdicts (per-field verifier verdicts
        # for co_pilot_extracted_fields persistence).  The pre-P3
        # attach_and_extract_async() is preserved for LangGraph callers.
        extraction_payload = await attach_and_extract_with_metadata_async(
            patient_id=patient_id,
            doc_ref_id=doc_ref_id,
            doc_type=doc_type,
            pdf_path=tmp_path,
            triggered_by=triggered_by_value,
            parent_extraction_id=parent_extraction_id,
        )
        result = extraction_payload["result"]
        docling_blocks_payload = extraction_payload["docling_blocks"]
        field_verdicts_payload = extraction_payload["field_verdicts"]
        # P4 R2: flat {field_path: original_value} map for verified fields.
        # Assembly-only from the extraction result — no LLM rerun.
        original_values_payload = extraction_payload.get("original_values", {})

    except Exception as exc:
        # Stage-2 fix #1 pattern: scrub exception message and break __cause__
        # chain so no raw doc text or PHI reaches the caller.
        scrubbed = mask_observability_patterns(str(exc))
        logger.warning(
            "/attach_and_extract: extraction failed",
            extra={
                "request_id": request_id,
                "doc_type": doc_type,
                "error_type": type(exc).__name__,
                "error_scrubbed": scrubbed,
            },
        )
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "reason": "extraction_failed",
                "request_id": request_id,
            },
        )
    finally:
        # Clean up temp file regardless of outcome.
        if tmp_path is not None:
            try:
                _os.unlink(tmp_path)
            except Exception:
                pass

    # --- Build response. ----------------------------------------------------
    extraction_dict = result.model_dump(mode="json")

    # Compute derived response fields: n_blocks, n_pages, avg confidence.
    if isinstance(result, LabReport):
        n_blocks = len(result.results)
        n_pages = result.extraction_metadata.page_count
        confidences = [r.confidence for r in result.results]
    elif isinstance(result, IntakeForm):
        # IntakeForm doesn't have a flat results list; use source_citations count
        # as proxy for n_blocks, and extraction_metadata for page count.
        n_blocks = (
            len(result.current_medications)
            + len(result.allergies)
            + len(result.family_history)
        )
        n_pages = result.extraction_metadata.page_count
        confidences = []
    else:
        # DoclingDoc — shouldn't reach here (doc_type is validated above)
        # but handle gracefully.
        n_blocks = len(getattr(result, "blocks", []))
        n_pages = getattr(result, "page_count", 1)
        confidences = []

    extraction_confidence_avg = (
        sum(confidences) / len(confidences) if confidences else 0.0
    )

    latency_ms = int((time.perf_counter() - t0) * 1000)
    logger.info(
        "/attach_and_extract: extraction complete",
        extra={
            "request_id": request_id,
            "doc_type": doc_type,
            "n_blocks": n_blocks,
            "n_pages": n_pages,
            "extraction_confidence_avg": round(extraction_confidence_avg, 4),
            "latency_ms": latency_ms,
            "http_status": 200,
            "patient_id_in_sentinel_range": True,
        },
    )

    return JSONResponse(
        status_code=200,
        content={
            "status": "ok",
            "doc_ref_id": doc_ref_id,
            "doc_type": doc_type,
            "extraction": extraction_dict,
            "n_blocks": n_blocks,
            "n_pages": n_pages,
            "extraction_confidence_avg": extraction_confidence_avg,
            "request_id": request_id,
            # P4 R2: flat map of verified field values. Assembly-only; no LLM
            # rerun. No new PHI surface (same response already carries the
            # full extraction model_dump). Must NOT appear in logs or spans.
            "original_values": original_values_payload,
            # P3 (PRD §7): full Docling block inventory for the "Possibly
            # Missed" pane + per-field verifier verdicts so the OpenEMR
            # subscriber can persist co_pilot_extracted_fields rows and the
            # review modal can render bbox overlays.  parent_extraction_id /
            # triggered_by are echoed so the PHP layer can chain attempts
            # without a second DB query.
            "parent_extraction_id": parent_extraction_id,
            "triggered_by": triggered_by_value,
            "docling_blocks": docling_blocks_payload,
            "field_verdicts": field_verdicts_payload,
        },
    )


@app.post("/extract_via_graph", response_model=None)
async def extract_via_graph_endpoint(
    request: Request,
    patient_id: int = Form(...),
    doc_ref_id: str = Form(...),
    doc_type: str = Form(...),
    file: UploadFile = Form(...),
) -> JSONResponse:
    """Graph-routed equivalent of /attach_and_extract.

    Demonstrates PRD §3's runtime-graph promise — extraction flows
    through the LangGraph supervisor → intake_extractor pipeline, with
    state plumbed end-to-end through SupervisorState.  /attach_and_extract
    remains the production demo path (direct call, lower latency); this
    endpoint exists alongside it as the architectural-completeness path.

    Same HTTP contract as /attach_and_extract (multipart form fields,
    HMAC headers, response shape).  PHP DocumentSavedSubscriber can
    point at either endpoint; the response is byte-identical.

    Internal differences vs /attach_and_extract:
      - Dispatch goes through _dispatch_extraction_via_supervisor_graph()
        which builds a SupervisorState seeded with a doc_ref_id in
        tool_calls_accumulated[0].  The supervisor's deterministic
        short-circuit recognises this signal and routes to
        intake_extractor without an LLM call.
      - intake_extractor writes the full extraction payload into
        state.extraction_payload; supervisor sees it on its second
        visit and terminates with terminal_reason='extraction_complete';
        builder routes to END (responder skipped).
      - The endpoint reads extraction_payload from final_state and
        builds the same response shape.

    Security controls (identical to /attach_and_extract):
    - X-OpenEMR-HMAC header verification (sha256, constant-time compare)
    - Replay-window gate: ±300s on X-OpenEMR-Timestamp
    - Sentinel patient_id range: 999001–999999 only
    - doc_type allowlist: "lab_pdf" | "intake_form"
    - Exception scrubbing — no raw doc text or PHI in error responses

    NOTE on duplication: this endpoint duplicates the
    /attach_and_extract validation flow rather than refactoring shared
    logic into a helper.  Reason: /attach_and_extract is the
    production-tier path with verified end-to-end behavior across 18+
    successful extractions on the demo install; we don't want to
    refactor it under deadline pressure when the duplication risk is
    near-zero (one of the two endpoints can be removed cleanly when
    the architecture finalizes in W3).
    """
    request_id = str(uuid.uuid4())
    t0 = time.perf_counter()

    # --- Read and verify headers before touching the multipart body. --------
    user_id_raw = request.headers.get("X-OpenEMR-User-Id", "")
    timestamp_raw = request.headers.get("X-OpenEMR-Timestamp", "")
    hmac_header = request.headers.get("X-OpenEMR-HMAC", "")

    is_reprocess = request.headers.get("X-OpenEMR-Reprocess", "").strip().lower() == "true" \
        or request.headers.get("X-OpenEMR-Reprocess", "").strip() == "1"
    parent_extraction_id_raw = request.headers.get("X-OpenEMR-Parent-Extraction-Id", "")
    parent_extraction_id: int | None = None
    if is_reprocess:
        try:
            parent_extraction_id = int(parent_extraction_id_raw)
            if parent_extraction_id <= 0:
                raise ValueError("non-positive")
        except (ValueError, TypeError):
            return JSONResponse(
                status_code=400,
                content={
                    "status": "refused",
                    "reason": "reprocess_missing_parent_id",
                    "request_id": request_id,
                },
            )
    triggered_by_value = "manual_retry" if is_reprocess else "initial"

    try:
        user_id = int(user_id_raw)
    except (ValueError, TypeError):
        return JSONResponse(
            status_code=400,
            content={
                "status": "refused",
                "reason": "missing_or_invalid_user_id",
                "request_id": request_id,
            },
        )

    try:
        timestamp = int(timestamp_raw)
    except (ValueError, TypeError):
        return JSONResponse(
            status_code=400,
            content={
                "status": "refused",
                "reason": "missing_or_invalid_timestamp",
                "request_id": request_id,
            },
        )

    now = time.time()
    age = now - timestamp
    if abs(age) > _ATTACH_HMAC_MAX_AGE_SECONDS:
        logger.info(
            "/extract_via_graph: stale timestamp rejected",
            extra={
                "request_id": request_id,
                "age_seconds": int(age),
                "doc_type": doc_type,
            },
        )
        return JSONResponse(
            status_code=401,
            content={
                "status": "refused",
                "reason": "stale_timestamp",
                "request_id": request_id,
            },
        )

    if not (_SENTINEL_MIN <= patient_id <= _SENTINEL_MAX):
        logger.info(
            "/extract_via_graph: patient_id outside sentinel range",
            extra={
                "request_id": request_id,
                "patient_id_in_sentinel_range": False,
                "doc_type": doc_type,
            },
        )
        return JSONResponse(
            status_code=400,
            content={
                "status": "refused",
                "reason": "patient_id_out_of_sentinel_range",
                "request_id": request_id,
            },
        )

    if doc_type not in _SUPPORTED_DOC_TYPES:
        return JSONResponse(
            status_code=400,
            content={
                "status": "refused",
                "reason": "unsupported_doc_type",
                "request_id": request_id,
            },
        )

    file_bytes = await file.read()
    file_sha256_hex = hashlib.sha256(file_bytes).hexdigest()

    settings = get_settings()
    hmac_ok = verify_attach_hmac(
        user_id=user_id,
        patient_id=patient_id,
        doc_ref_id=doc_ref_id,
        doc_type=doc_type,
        timestamp=timestamp,
        file_sha256_hex=file_sha256_hex,
        hmac_str=hmac_header,
        secret=settings.openemr_hmac_secret,
    )
    if not hmac_ok:
        logger.info(
            "/extract_via_graph: HMAC verification failed",
            extra={
                "request_id": request_id,
                "doc_type": doc_type,
                "file_sha256_hex": file_sha256_hex,
            },
        )
        return JSONResponse(
            status_code=401,
            content={
                "status": "refused",
                "reason": "invalid_signature",
                "request_id": request_id,
            },
        )

    # --- Dispatch extraction via the supervisor graph -----------------------
    import tempfile
    import os as _os
    from pathlib import Path

    suffix = ".pdf" if file_bytes[:4] == b"%PDF" else ".png"
    tmp_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(file_bytes)
            tmp_path = Path(tmp.name)

        logger.info(
            "/extract_via_graph: dispatching extraction via supervisor graph",
            extra={
                "request_id": request_id,
                "doc_type": doc_type,
                "file_sha256_hex": file_sha256_hex,
                "patient_id_in_sentinel_range": True,
                "graph_routed": True,
            },
        )

        # The graph helper runs supervisor → intake_extractor → supervisor
        # → END.  The intake_extractor calls attach_and_extract_with_metadata_async
        # internally (via its existing thread-pool wrapper) so we get the
        # same payload shape as the direct path.
        graph_payload = await _dispatch_extraction_via_supervisor_graph(
            patient_id=patient_id,
            doc_ref_id=doc_ref_id,
            doc_type=doc_type,
            pdf_path=tmp_path,
            triggered_by_value=triggered_by_value,
            parent_extraction_id=parent_extraction_id,
        )
        # graph_payload was built by intake_extractor_node and includes
        # the response-meta the endpoint needs (n_blocks, n_pages, avg).
        result_dict = graph_payload["result"]
        docling_blocks_payload = graph_payload.get("docling_blocks") or []
        field_verdicts_payload = graph_payload.get("field_verdicts") or []
        original_values_payload = graph_payload.get("original_values") or {}
        n_blocks = int(graph_payload.get("n_blocks") or 0)
        n_pages = int(graph_payload.get("n_pages") or 1)
        extraction_confidence_avg = float(
            graph_payload.get("extraction_confidence_avg") or 0.0
        )

    except Exception as exc:
        scrubbed = mask_observability_patterns(str(exc))
        logger.warning(
            "/extract_via_graph: extraction failed",
            extra={
                "request_id": request_id,
                "doc_type": doc_type,
                "error_type": type(exc).__name__,
                "error_scrubbed": scrubbed,
            },
        )
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "reason": "extraction_failed",
                "request_id": request_id,
            },
        )
    finally:
        if tmp_path is not None:
            try:
                _os.unlink(tmp_path)
            except Exception:
                pass

    latency_ms = int((time.perf_counter() - t0) * 1000)
    logger.info(
        "/extract_via_graph: extraction complete",
        extra={
            "request_id": request_id,
            "doc_type": doc_type,
            "n_blocks": n_blocks,
            "n_pages": n_pages,
            "extraction_confidence_avg": round(extraction_confidence_avg, 4),
            "latency_ms": latency_ms,
            "http_status": 200,
            "patient_id_in_sentinel_range": True,
            "graph_routed": True,
        },
    )

    return JSONResponse(
        status_code=200,
        content={
            "status": "ok",
            "doc_ref_id": doc_ref_id,
            "doc_type": doc_type,
            "extraction": result_dict,
            "n_blocks": n_blocks,
            "n_pages": n_pages,
            "extraction_confidence_avg": extraction_confidence_avg,
            "request_id": request_id,
            "original_values": original_values_payload,
            "parent_extraction_id": parent_extraction_id,
            "triggered_by": triggered_by_value,
            "docling_blocks": docling_blocks_payload,
            "field_verdicts": field_verdicts_payload,
            # Marker so the caller can confirm which path served the
            # request — useful in tests + interview defense (no PHI).
            "served_via": "supervisor_graph",
        },
    )


@app.post("/graph_chat", response_model=None)
async def graph_chat(request: ChatRequest) -> AgentResponse | RefusalResponse:
    """Multi-agent graph endpoint — supervisor → worker(s) → responder → END.

    Accepts the same ChatRequest body as /chat. Verifies HMAC, checks rate
    limits and token budget, then invokes the compiled LangGraph supervisor
    graph. Returns AgentResponse on success or RefusalResponse on any failure.

    Security controls mirror /chat:
      - HMAC-SHA256 verification (same verify_hmac function)
      - Replay-window gate (30s, enforced by verify_hmac)
      - Per-user request rate + token budget checks
      - Outbound PHI gate runs inside the responder node (defense-in-depth)

    Graph routing (W2_ARCHITECTURE.md §3 / decision #2):
      supervisor → {intake_extractor | evidence_retriever} → supervisor (loop)
      supervisor (answered | supervisor_max_hops) → responder → END
      supervisor (no_route) → END  [returns RefusalResponse directly]

    Langfuse flush once after graph.invoke() per decision #8.
    """
    import uuid as _uuid
    from agent._rate_limit import check_request_rate, check_token_budget

    request_id = str(_uuid.uuid4())
    settings = get_settings()

    # --- HMAC verification (fail closed) -------------------------------------
    hmac_failure = verify_hmac(request, settings.openemr_hmac_secret)
    if hmac_failure is not None:
        logger.info(
            "/graph_chat: HMAC verification failed",
            extra={"request_id": request_id, "reason": hmac_failure},
        )
        return RefusalResponse(
            reason="Request integrity check failed.",
            searched=[],
            request_id=request_id,
            trace_id=None,
        )

    # --- Per-user request rate check (post-HMAC) -----------------------------
    if not check_request_rate(
        request.user_id,
        settings.rate_limit_requests_per_minute,
        60,
    ):
        return RefusalResponse(
            reason=(
                "Request rate limit exceeded for this user. "
                "Please wait a minute before retrying."
            ),
            searched=[],
            request_id=request_id,
            trace_id=None,
        )

    # --- Per-user token budget check -----------------------------------------
    budget_ok, current_tokens = check_token_budget(
        request.user_id,
        settings.token_budget_per_hour,
        3600,
    )
    if not budget_ok:
        return RefusalResponse(
            reason=(
                "This user has reached the hourly token budget. "
                "Please retry in an hour."
            ),
            searched=[],
            request_id=request_id,
            trace_id=None,
        )

    # --- Extract last user message as the supervisor query -------------------
    last_user_message = ""
    for m in reversed(request.messages):
        if m.role.value == "user":
            last_user_message = m.content
            break

    # --- Build initial supervisor state (decision #7: patient_id top-level) --
    initial_state = {
        "query": last_user_message,
        "patient_id": request.patient_id,
        "tool_calls_accumulated": [],
        "citations": [],
        "hops_taken": 0,
        "terminal_reason": None,
        "worker_results": [],
        "final_response": None,
        "node_observability": [],
        "retrieved_records": [],
        "_pending_route": None,
    }

    # --- Invoke graph --------------------------------------------------------
    try:
        final_state = _compiled_graph.invoke(initial_state)
    except Exception as exc:
        logger.exception("/graph_chat: graph.invoke() raised unexpectedly")
        raise HTTPException(
            status_code=500,
            detail="The agent encountered an internal error. Please retry.",
        ) from exc

    # --- Decision #8: Langfuse flush after graph.invoke() --------------------
    try:
        from langfuse import get_client as _get_lf_client
        _get_lf_client().flush()
    except Exception:
        pass

    # --- Inspect terminal_reason and return ----------------------------------
    terminal_reason = final_state.get("terminal_reason")

    # "no_route" → bypass responder, return RefusalResponse directly.
    if terminal_reason == "no_route":
        return RefusalResponse(
            reason="no_route",
            searched=[],
            request_id=request_id,
            trace_id=None,
        )

    # Responder wrote final_response into state.
    final_response = final_state.get("final_response")
    if final_response is None:
        # Defensive: graph terminated without a final_response (shouldn't happen
        # in normal operation — supervisor_max_hops → responder → END).
        logger.warning(
            "/graph_chat: final_response is None after graph completion "
            "(terminal_reason=%s, request_id=%s)",
            terminal_reason,
            request_id,
        )
        return RefusalResponse(
            reason="The agent graph completed without producing a response.",
            searched=[],
            request_id=request_id,
            trace_id=None,
        )

    return final_response


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"status": "error", "detail": exc.detail},
    )
