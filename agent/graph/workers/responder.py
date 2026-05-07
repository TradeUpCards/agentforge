"""Responder node (W2 graph phase — decision #2).

The responder is the terminal synthesis node in the supervisor graph.
It is called by the graph after the supervisor decides the accumulated
evidence is sufficient (terminal_reason = "answered" or "supervisor_max_hops").

Graph position:
  supervisor → {worker(s)} → supervisor → responder → END
  supervisor (no_route)    → END  [bypasses responder — RefusalResponse
                                   returned directly from /graph_chat handler]

Design choice: use synthesize_with_verifier() from agent/_synthesis.py.
  The responder calls synthesize_with_verifier() with the test-compatible
  interface (messages, retrieved_records, citations, model, request_id,
  patient_id) → (AgentResponse | RefusalResponse, VerifierResult | None).
  This makes the synthesis step mockable by the quality-lead test suite.

  After synthesis, the responder applies the outbound PHI gate inline
  (decision #11: defense-in-depth, separate from the verifier step so tests
  can mock synthesis independently from PHI gate logic).

Sonnet escalation (decision #4b):
  - First attempt: Haiku 4.5 (settings.model_workhorse).
  - If synthesize_with_verifier() returns RefusalResponse (verifier refused):
    retry once with Sonnet 4.6 (settings.model_reasoning) and set
    escalated_to_sonnet=True on the AgentResponse.

W2_ARCHITECTURE.md §3 (responder node), §3.6 (verifier), §8.3 (PHI gate).
Decisions: #2, #4, #11, #12, #14, #15.

No-PHI discipline (§8.3):
  - Langfuse span tags: latency_ms, terminal_reason, escalated_to_sonnet.
    NEVER raw query text or synthesis output.
  - Exceptions are scrubbed before logging.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

from agent._phi_scrubber import mask_observability_patterns, find_outbound_violations
from agent._synthesis import synthesize_with_verifier
from agent.config import get_settings
from agent.graph.state import SupervisorState
from agent.schemas import (
    AgentResponse,
    Citation,
    Message,
    RefusalResponse,
    RetrievedRecord,
    Role,
    ToolCallSummary,
)

logger = logging.getLogger(__name__)

_NODE_NAME = "responder"

# TODO: centralize pricing constants
# Haiku 4.5: $1.00/MTok input, $5.00/MTok output
# Sonnet 4.6: $3.00/MTok input, $15.00/MTok output
_HAIKU_COST_INPUT_PER_MTOK = 1.00
_HAIKU_COST_OUTPUT_PER_MTOK = 5.00
_SONNET_COST_INPUT_PER_MTOK = 3.00
_SONNET_COST_OUTPUT_PER_MTOK = 15.00


def responder_node(state: SupervisorState) -> dict[str, Any]:
    """LangGraph terminal node: synthesis + verification + PHI gate.

    Reads accumulated citations and worker results from state.
    Calls synthesize_with_verifier() with Haiku 4.5 first; if refused,
    retries once with Sonnet 4.6 (decision #4b).
    Applies outbound PHI gate after synthesis (decision #11).

    Writes:
      - state["final_response"]: AgentResponse | RefusalResponse
      - state["terminal_reason"]: "responded"
      - state["node_observability"]: updated with responder entry

    Returns a state patch. LangGraph merges it automatically.
    """
    try:
        from langfuse import get_client
        lf = get_client()
        _langfuse_available = True
    except Exception:
        lf = None
        _langfuse_available = False

    t0 = time.monotonic()
    settings = get_settings()
    request_id = str(uuid.uuid4())

    escalated_to_sonnet = False
    tokens_input = 0
    tokens_output = 0
    final_response: AgentResponse | RefusalResponse | None = None

    with _langfuse_span(lf, _langfuse_available) as _span:
        try:
            query = state.get("query", "")
            patient_id = state.get("patient_id") or 0
            citations = list(state.get("citations", []))
            worker_results = list(state.get("worker_results", []))

            # Build inputs for synthesize_with_verifier.
            messages = [Message(role=Role.USER, content=query)]
            retrieved_records = _citations_to_retrieved_records(citations)

            # Attempt 1: Haiku 4.5 (model_workhorse).
            result, _verdict = _run_synthesis_sync(
                messages=messages,
                retrieved_records=retrieved_records,
                citations=citations,
                model=settings.model_workhorse,
                request_id=request_id,
                patient_id=patient_id,
            )

            # Decision #4b: retry with Sonnet 4.6 if Haiku was refused.
            if isinstance(result, RefusalResponse):
                logger.info(
                    "responder_node: Haiku synthesis refused — escalating to Sonnet"
                )
                result2, _verdict2 = _run_synthesis_sync(
                    messages=messages,
                    retrieved_records=retrieved_records,
                    citations=citations,
                    model=settings.model_reasoning,
                    request_id=request_id,
                    patient_id=patient_id,
                )
                if isinstance(result2, AgentResponse):
                    result2.escalated_to_sonnet = True
                    escalated_to_sonnet = True
                result = result2

            # Decision #11: outbound PHI gate (defense-in-depth).
            # Applied in the responder AFTER synthesis so tests can mock
            # synthesize_with_verifier() independently.
            if isinstance(result, AgentResponse):
                prose = result.message.content
                claim_texts = [c.text for c in result.claims]
                _phi_targets = [prose] + claim_texts
                _phi_violations: list[str] = []
                for _t in _phi_targets:
                    _phi_violations.extend(
                        find_outbound_violations(_t, patient_id)
                    )
                if _phi_violations:
                    logger.warning(
                        "responder_node: outbound PHI gate triggered "
                        "(%d violations) — refusing",
                        len(_phi_violations),
                    )
                    result = RefusalResponse(
                        reason=(
                            "The Co-Pilot detected potential cross-patient "
                            "information in the response and declined to send it. "
                            "Please retry; if this persists, the patient's chart "
                            "should be reviewed manually."
                        ),
                        searched=[],
                        request_id=request_id,
                        trace_id=None,
                    )

            final_response = result

        except Exception as exc:
            scrubbed = mask_observability_patterns(str(exc))
            logger.error(
                "responder_node failed: %s — returning refusal",
                type(exc).__name__,
            )
            logger.debug("responder_node scrubbed error: %s", scrubbed)
            final_response = RefusalResponse(
                reason="The responder encountered an internal error.",
                searched=[],
                request_id=request_id,
                trace_id=None,
            )

        latency_ms = int((time.monotonic() - t0) * 1000)
        terminal_reason = state.get("terminal_reason", "answered")
        if _langfuse_available and lf is not None:
            try:
                lf.update_current_span(
                    metadata={
                        "node_name": _NODE_NAME,
                        "terminal_reason": terminal_reason,
                        "escalated_to_sonnet": escalated_to_sonnet,
                        "latency_ms": latency_ms,
                        "tokens_input": tokens_input,
                        "tokens_output": tokens_output,
                    }
                )
            except Exception:
                pass

    # Decision #14: per-node observability entry.
    _is_sonnet = escalated_to_sonnet
    cost_usd = (
        (tokens_input / 1_000_000) * (
            _SONNET_COST_INPUT_PER_MTOK if _is_sonnet else _HAIKU_COST_INPUT_PER_MTOK
        )
        + (tokens_output / 1_000_000) * (
            _SONNET_COST_OUTPUT_PER_MTOK if _is_sonnet else _HAIKU_COST_OUTPUT_PER_MTOK
        )
    )
    obs_entry = {
        "node_name": _NODE_NAME,
        "latency_ms": latency_ms,
        "tokens_input": tokens_input,
        "tokens_output": tokens_output,
        "cost_estimate_usd": round(cost_usd, 8),
        "retrieval_hits": 0,
        "extraction_confidence": None,
    }
    existing_obs = list(state.get("node_observability", []))
    existing_obs.append(obs_entry)

    return {
        "final_response": final_response,
        "terminal_reason": "responded",
        "node_observability": existing_obs,
    }


# ---------------------------------------------------------------------------
# Adapter helpers
# ---------------------------------------------------------------------------


def _citations_to_retrieved_records(
    citations: list,
) -> list[RetrievedRecord]:
    """Convert Citation objects accumulated by workers to RetrievedRecord shape.

    synthesize_with_verifier() uses retrieved_records to verify claim
    source_record_ids. We construct RetrievedRecord objects whose
    f"{table}:{record_id}" matches the Citation.source_id pattern.
    """
    from agent.schemas import CitationStrength

    records: list[RetrievedRecord] = []
    seen: set[str] = set()
    for cit in citations:
        if isinstance(cit, Citation):
            source_id = cit.source_id
            source_type = cit.source_type
        elif isinstance(cit, dict):
            source_id = cit.get("source_id", "")
            source_type = cit.get("source_type", "patient_record")
        else:
            continue
        if not source_id or source_id in seen:
            continue
        seen.add(source_id)

        if ":" in source_id:
            parts = source_id.split(":", 1)
            table, record_id = parts[0], parts[1]
        else:
            table = "unknown"
            record_id = source_id

        strength = (
            CitationStrength.STRUCTURED
            if source_type == "patient_record"
            else CitationStrength.FREE_TEXT
        )
        records.append(
            RetrievedRecord(
                table=table,
                record_id=record_id,
                citation_strength=strength,
                fields={},
            )
        )

    return records


def _run_synthesis_sync(
    *,
    messages: list[Message],
    retrieved_records: list[RetrievedRecord],
    citations: list[Citation],
    model: str,
    request_id: str,
    patient_id: int,
) -> tuple[AgentResponse | RefusalResponse, Any]:
    """Run synthesize_with_verifier() synchronously from a LangGraph node.

    synthesize_with_verifier() is async; LangGraph nodes are sync.
    Handles both running and non-running event loops (same pattern as
    supervisor.py and evidence_retriever.py).
    """
    coro = synthesize_with_verifier(
        messages=messages,
        retrieved_records=retrieved_records,
        citations=citations,
        model=model,
        request_id=request_id,
        patient_id=patient_id,
    )

    try:
        asyncio.get_running_loop()
        _loop_running = True
    except RuntimeError:
        _loop_running = False

    if _loop_running:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result(timeout=60)
    else:
        return asyncio.run(coro)


class _langfuse_span:
    """Context manager: opens a Langfuse span named 'worker.responder'
    when Langfuse is available; no-op otherwise."""

    def __init__(self, lf: Any, available: bool) -> None:
        self._lf = lf
        self._available = available
        self._span = None

    def __enter__(self) -> "_langfuse_span":
        if self._available and self._lf is not None:
            try:
                self._span = self._lf.start_observation(name="worker.responder")
            except Exception:
                self._span = None
        return self

    def __exit__(self, *_: object) -> None:
        if self._span is not None:
            try:
                self._span.end()
            except Exception:
                pass
