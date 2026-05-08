"""Synthesis helper — extracted from agent.py:run_chat (W2 graph phase).

synthesize_with_verifier() contains the LLM call + verifier logic that was
previously inlined in run_chat (steps 3–6, lines ~962–1154 before graph phase).

The outbound PHI gate (step 6a) is NOT run inside this helper — it lives in the
caller. For run_chat, the PHI gate is still inlined (unchanged). For the responder
node, the PHI gate runs inside responder_node after the synthesis result is returned.
This keeps the PHI gate at the defense-in-depth layer (decision #11) and lets the
responder's test mock intercept synthesis without also mocking the PHI gate.

Interface (designed for testability — quality-lead tests mock this):
  synthesize_with_verifier(
      *,
      messages: list[Message],
      retrieved_records: list[RetrievedRecord],
      citations: list[Citation],
      model: str,
      request_id: str,
      patient_id: int,
  ) -> tuple[AgentResponse | RefusalResponse, VerifierResult | None]

Both call sites:
  - agent/agent.py:run_chat (existing /chat endpoint) uses _run_chat_synthesis()
    which calls the full pipeline inline (unchanged from W1 behavior).
  - agent/graph/workers/responder.py:responder_node calls synthesize_with_verifier()
    directly, then applies the PHI gate separately.

CRITICAL: run_chat behavioral invariant
  run_chat uses _run_chat_synthesis() which preserves all existing behavior,
  including the outbound PHI gate and _close_audit contract. The helper this
  module exports is used only by responder_node.

W2_ARCHITECTURE.md §3 (responder node), §3.6 (verifier), §8.3 (PHI gate).
Decision #12 (extraction to _synthesis.py).
Decision #11 (outbound PHI gate defense-in-depth in responder — called BY responder).
Decision #4 (Sonnet escalation when verifier returns REFUSED on attempt 1).
Decision #15 (escalated_to_sonnet field on AgentResponse).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from agent.schemas import (
    AgentResponse,
    Citation,
    Claim,
    Message,
    RefusalResponse,
    RetrievedRecord,
    Role,
    ToolCallSummary,
    VerifierResult,
    VerifierVerdict,
)
from agent.verifier import verify_claims

logger = logging.getLogger(__name__)

# TODO: centralize pricing constants
# Haiku 4.5: $1.00/MTok input, $5.00/MTok output
# Sonnet 4.6: $3.00/MTok input, $15.00/MTok output
_HAIKU_COST_INPUT_PER_MTOK = 1.00
_HAIKU_COST_OUTPUT_PER_MTOK = 5.00
_SONNET_COST_INPUT_PER_MTOK = 3.00
_SONNET_COST_OUTPUT_PER_MTOK = 15.00


async def synthesize_with_verifier(
    *,
    messages: list[Message],
    retrieved_records: list[RetrievedRecord],
    citations: list[Citation],
    model: str,
    request_id: str,
    patient_id: int,
) -> tuple[AgentResponse | RefusalResponse, VerifierResult | None]:
    """Run LLM synthesis + verifier. Returns (response, verifier_result).

    Used by the responder node. Does NOT apply the outbound PHI gate — that
    is the caller's responsibility (decision #11: PHI gate lives in
    responder_node for defense-in-depth testability).

    Does NOT call _close_audit. Does NOT check HMAC or rate limits.

    Parameters
    ----------
    messages : list[Message]
        Conversation messages (query as last user message).
    retrieved_records : list[RetrievedRecord]
        Records fetched by tools. Used to build patient context and verify
        claim citations.
    citations : list[Citation]
        Accumulated graph citations (used for context; verification uses
        retrieved_records for the claims contract).
    model : str
        The model to use for synthesis (Haiku or Sonnet).
    request_id : str
        Unique ID for this request.
    patient_id : int
        Patient ID for the current session.

    Returns
    -------
    (AgentResponse | RefusalResponse, VerifierResult | None)
        VerifierResult is None on parse failure (before verification ran).
        Caller applies the PHI gate to the AgentResponse before returning it.

    Notes on escalation (decision #4b for responder):
        The responder node calls this twice on RefusalResponse: first with
        model=model_workhorse (Haiku), then with model=model_reasoning (Sonnet).
        This helper itself does NOT escalate — that logic lives in responder_node.
        This keeps the helper stateless and re-entrant.
    """
    # Import here to avoid circular import at module load time.
    from agent.agent import (
        _extract_text,
        _format_patient_context,
        _parse_structured_output,
        _SYSTEM_PROMPT_STATIC,
    )
    from agent.llm_client import build_llm_client
    from agent.config import get_settings

    settings = get_settings()
    llm = build_llm_client(settings)

    # Build system prompt with context.
    patient_context = _format_patient_context(retrieved_records)
    system_blocks = [
        {
            "type": "text",
            "text": _SYSTEM_PROMPT_STATIC,
        },
        {
            "type": "text",
            "text": (
                "PATIENT CONTEXT (records retrieved for this patient):\n\n"
                + patient_context
            ),
            "cache_control": {"type": "ephemeral"},
        },
    ]

    # Call LLM.
    anthropic_messages = [
        {"role": m.role.value, "content": m.content} for m in messages
    ]
    # max_tokens=8192 (was 4096): the 4k ceiling was hitting truncation
    # mid-JSON on absence-claim cases (case 06 empty_records, case 27
    # patient_switch_resists_stale_history) — Haiku stop_reason=max_tokens
    # → json.JSONDecodeError → RefusalResponse("could not be parsed").
    # See .gauntlet/week2/audit/2026-05-08-eval-failures.md (Cluster C).
    response = await llm.create(
        model=model,
        system=system_blocks,
        messages=anthropic_messages,
        max_tokens=8192,
        temperature=0.0,
    )

    # Parse structured output.
    raw_text = _extract_text(response)
    try:
        prose, claims = _parse_structured_output(raw_text)
    except ValueError as parse_exc:
        stop_reason = getattr(response, "stop_reason", "unknown")
        logger.warning(
            "_synthesis parse_failure stop_reason=%s len=%d exc_type=%s",
            stop_reason,
            len(raw_text),
            type(parse_exc).__name__,
        )
        return RefusalResponse(
            reason="The agent's response could not be parsed.",
            searched=[],
            request_id=request_id,
            trace_id=None,
        ), None

    # Verify claims against retrieved_records.
    verdict = verify_claims(claims, retrieved_records)

    if verdict.retry_needed and verdict.failure_rate > 0.30:
        logger.warning(
            "_synthesis verifier_30pct: passed=%d failed=%d rate=%.2f",
            len(verdict.claims_passed),
            len(verdict.claims_failed),
            verdict.failure_rate,
        )
        return RefusalResponse(
            reason=(
                "More than 30% of the agent's claims could not be verified "
                "against the retrieved records. The agent declines to answer "
                "rather than risk an unsupported claim."
            ),
            searched=[],
            request_id=request_id,
            trace_id=None,
        ), verdict

    # Build verified response.
    _derived_citations: list[Citation] = []
    _seen_source_ids: set[str] = set()
    for _claim in verdict.claims_passed:
        for _raw_id in (_claim.source_record_ids or []):
            if _raw_id in _seen_source_ids:
                continue
            _seen_source_ids.add(_raw_id)
            if _raw_id.startswith("guideline_corpus:"):
                _chunk_id = _raw_id[len("guideline_corpus:"):]
                _derived_citations.append(Citation(
                    source_type="guideline",
                    source_id=_chunk_id,
                    field_or_chunk_id=_chunk_id,
                    quote_or_value="",
                ))
            else:
                _derived_citations.append(Citation(
                    source_type="patient_record",
                    source_id=_raw_id,
                    field_or_chunk_id=_raw_id,
                    quote_or_value="",
                ))

    return AgentResponse(
        message=Message(role=Role.ASSISTANT, content=prose),
        claims=verdict.claims_passed,
        retrieved_records=retrieved_records,
        tools_called=[],
        request_id=request_id,
        trace_id=None,
        claims_stripped=len(verdict.claims_failed),
        citations=_derived_citations,
        escalated_to_sonnet=False,
    ), verdict
