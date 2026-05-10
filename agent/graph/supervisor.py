"""Supervisor node (W2 Stage-3b, graph phase).

The supervisor is the LangGraph entry node. It:
  1. Reads the current state (query + accumulated results so far).
  2. Calls Anthropic (Haiku 4.5 by default — decision #4) with a
     small system prompt asking for a JSON routing decision.
  3. Validates the route is in the allowed set.
  4. Increments hops_taken.
  5. Returns a state patch.

If hops_taken would exceed 4, it sets terminal_reason = "supervisor_max_hops" and emits
the SUPERVISOR_MAX_HOPS_REACHED sentinel log line.

Model split (decision #4):
  - Haiku 4.5 (model_workhorse) is the default. With max_tokens=128 and a
    routing-only system prompt, query echo risk is acceptable and cost is 3×
    lower than Sonnet.
  - Sonnet 4.6 (model_reasoning) is used on retry when Haiku returns invalid
    JSON or an unrecognised route (single escalation attempt per hop).

W2_ARCHITECTURE.md §3.2, §3.3, §3.5.

No-PHI discipline (§8.3):
  - Langfuse span tags: route (structural category), hops_taken, latency_ms.
  - The LLM rationale string is NEVER logged or traced — it may echo the query.
  - query is passed to the LLM but never written to logs, spans, or state.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from agent._phi_scrubber import mask_observability_patterns
from agent.config import get_settings
from agent.graph.state import SupervisorState
from agent.llm_client import build_llm_client

logger = logging.getLogger(__name__)

# Hard cap on worker hops per §3.2.
_MAX_HOPS = 4

# Allowed routes the supervisor may choose.
_ALLOWED_ROUTES = frozenset({"intake_extractor", "evidence_retriever", "none"})

_SYSTEM_PROMPT = (
    "You are a clinical co-pilot supervisor. Based on the user query and the "
    "accumulated evidence so far, decide the next action.\n\n"
    "Available routes:\n"
    "  intake_extractor   — Use when an uploaded document (lab PDF / intake form) "
    "needs to be parsed and structured facts extracted.\n"
    "  evidence_retriever — Use when the query needs guideline evidence or "
    "patient-record data retrieved from the clinical corpus or chart.\n"
    "  none               — Use when sufficient evidence has been accumulated to "
    "answer the query, or when neither worker can help.\n\n"
    "Reply with ONLY valid JSON in this exact shape:\n"
    '{"route": "<one of: intake_extractor | evidence_retriever | none>", '
    '"rationale": "<one sentence>"}\n\n'
    "Do not include any other text outside the JSON object."
)

# TODO: centralize pricing constants
# Haiku 4.5: $1.00/MTok input, $5.00/MTok output
# Sonnet 4.6: $3.00/MTok input, $15.00/MTok output
_HAIKU_COST_INPUT_PER_MTOK = 1.00
_HAIKU_COST_OUTPUT_PER_MTOK = 5.00
_SONNET_COST_INPUT_PER_MTOK = 3.00
_SONNET_COST_OUTPUT_PER_MTOK = 15.00


def supervisor_node(state: SupervisorState) -> dict[str, Any]:
    """LangGraph node: supervisor routing decision.

    Returns a state patch (partial dict). LangGraph merges it automatically.

    Raises:
        RuntimeError: if the Anthropic SDK call fails (scrubbed message).
    """
    try:
        from langfuse import get_client
        lf = get_client()
        _langfuse_available = True
    except Exception:
        lf = None
        _langfuse_available = False

    hops_taken = state.get("hops_taken", 0)

    # ----- Deterministic short-circuit for graph-routed extraction -----
    # When the graph is invoked via the /extract_via_graph endpoint, the
    # initial state carries:
    #   - a doc_ref_id in tool_calls_accumulated[0] (signals "this run is
    #     about a specific document"); and
    #   - extraction_payload starts as None.
    # Two deterministic decisions follow, no LLM call required:
    #   (a) extraction_payload is still None → route to intake_extractor.
    #       The router (next dispatch decision) is fixed by the calling
    #       context, not by interpreting a query.
    #   (b) extraction_payload is now populated → terminate with reason
    #       "extraction_complete".  The /extract_via_graph endpoint reads
    #       the payload from final_state and builds its HTTP response.
    #
    # Skipping the routing LLM on this path is a pure optimization: it
    # saves ~1 supervisor LLM call (~$0.0002 + 200-800ms) and removes the
    # only failure mode where the LLM might mis-route an extraction
    # request.  The chat path (no doc_ctx in initial state) takes the
    # normal LLM-routed flow below, unchanged.
    has_doc_ctx = any(
        isinstance(entry, dict) and "doc_ref_id" in entry
        for entry in state.get("tool_calls_accumulated", [])
    )
    extraction_payload = state.get("extraction_payload")
    if has_doc_ctx and extraction_payload is None:
        # Open a Langfuse span so the demo trace shows the deterministic
        # routing decision (parity with the LLM-routed flow below where
        # every supervisor visit produces a span).  No tokens / no cost
        # since there's no LLM call — span metadata reflects that.
        logger.info("supervisor_node: deterministic short-circuit → intake_extractor")
        with _langfuse_span(lf, _langfuse_available, hops_taken) as _span:
            if _langfuse_available and lf is not None:
                try:
                    lf.update_current_span(
                        metadata={
                            "route": "intake_extractor",
                            "hops_taken": hops_taken,
                            "decision_kind": "deterministic_short_circuit",
                            "tokens_input": 0,
                            "tokens_output": 0,
                            "cost_estimate_usd": 0.0,
                            "escalated_to_sonnet": False,
                            "trigger": "doc_ref_id_in_state",
                        }
                    )
                except Exception:
                    pass
        return {
            "hops_taken": hops_taken + 1,
            "_pending_route": "intake_extractor",
        }
    if has_doc_ctx and extraction_payload is not None:
        # Second supervisor visit — extraction has run, payload is on
        # state, terminate cleanly (router → END, skipping responder).
        logger.info("supervisor_node: extraction complete — terminating")
        with _langfuse_span(lf, _langfuse_available, hops_taken) as _span:
            if _langfuse_available and lf is not None:
                try:
                    lf.update_current_span(
                        metadata={
                            "route": "terminal",
                            "hops_taken": hops_taken,
                            "decision_kind": "extraction_complete",
                            "tokens_input": 0,
                            "tokens_output": 0,
                            "cost_estimate_usd": 0.0,
                        }
                    )
                except Exception:
                    pass
        return {
            "terminal_reason": "extraction_complete",
            "_pending_route": None,
        }

    # Hard stop: 4-hop cap (§3.2 — named refusal supervisor_max_hops).
    if hops_taken >= _MAX_HOPS:
        logger.warning("SUPERVISOR_MAX_HOPS_REACHED hops_taken=%d", hops_taken)
        if _langfuse_available and lf is not None:
            try:
                lf.update_current_span(
                    metadata={
                        "supervisor_max_hops_reached": True,
                        "hops_taken": hops_taken,
                    }
                )
            except Exception:
                pass
        return {"terminal_reason": "supervisor_max_hops", "_pending_route": None}

    t0 = time.monotonic()
    route = "none"
    tokens_input = 0
    tokens_output = 0
    escalated = False
    try:
        with _langfuse_span(lf, _langfuse_available, hops_taken) as _span:
            route, tokens_input, tokens_output, escalated = _call_supervisor_llm(state)
            latency_ms = int((time.monotonic() - t0) * 1000)

            # Compute cost estimate for observability.
            if escalated:
                cost_usd = (
                    (tokens_input / 1_000_000) * _SONNET_COST_INPUT_PER_MTOK
                    + (tokens_output / 1_000_000) * _SONNET_COST_OUTPUT_PER_MTOK
                )
            else:
                cost_usd = (
                    (tokens_input / 1_000_000) * _HAIKU_COST_INPUT_PER_MTOK
                    + (tokens_output / 1_000_000) * _HAIKU_COST_OUTPUT_PER_MTOK
                )

            # Tag span with structural-only metadata: route category + latency.
            # The rationale string is intentionally not logged (may echo query).
            if _langfuse_available and lf is not None:
                try:
                    lf.update_current_span(
                        metadata={
                            "route": route,
                            "hops_taken": hops_taken,
                            "latency_ms": latency_ms,
                            "tokens_input": tokens_input,
                            "tokens_output": tokens_output,
                            "cost_estimate_usd": round(cost_usd, 8),
                            "escalated_to_sonnet": escalated,
                        }
                    )
                except Exception:
                    pass
    except Exception as exc:
        scrubbed = mask_observability_patterns(str(exc))
        logger.error("supervisor_node LLM call failed: %s", scrubbed)
        # On LLM failure, route to none (terminal).
        return {"terminal_reason": "no_route", "_pending_route": None}

    if route not in _ALLOWED_ROUTES:
        # Fix #3 (obs-sec): truncate + scrub before logging — the raw LLM
        # value may contain prompt-injected text or echoed query content.
        safe_route = mask_observability_patterns(route[:64])
        logger.warning(
            "supervisor_node received unrecognised route (truncated) — terminating: %s",
            safe_route,
        )
        return {"terminal_reason": "no_route", "_pending_route": None}

    if route == "none":
        return {"terminal_reason": "answered", "hops_taken": hops_taken, "_pending_route": None}

    # Valid worker route: increment hop counter and signal the router.
    # Append node observability (decision #14).
    obs_entry = {
        "node_name": "supervisor",
        "latency_ms": int((time.monotonic() - t0) * 1000),
        "tokens_input": tokens_input,
        "tokens_output": tokens_output,
        "cost_estimate_usd": round(
            ((tokens_input / 1_000_000) * (_SONNET_COST_INPUT_PER_MTOK if escalated else _HAIKU_COST_INPUT_PER_MTOK))
            + ((tokens_output / 1_000_000) * (_SONNET_COST_OUTPUT_PER_MTOK if escalated else _HAIKU_COST_OUTPUT_PER_MTOK)),
            8,
        ),
        "retrieval_hits": 0,
        "extraction_confidence": None,
    }
    existing_obs = list(state.get("node_observability", []))
    existing_obs.append(obs_entry)

    return {
        "hops_taken": hops_taken + 1,
        "_pending_route": route,
        "node_observability": existing_obs,
    }


def _call_supervisor_llm(
    state: SupervisorState,
) -> tuple[str, int, int, bool]:
    """Call the Anthropic LLM and return (route, tokens_in, tokens_out, escalated).

    Structural: returns route only; rationale is parsed but discarded
    (it may echo the query — PHI-adjacent per §8.3).

    Decision #4 model split:
      - Haiku 4.5 (model_workhorse) is the default.
      - Sonnet 4.6 (model_reasoning) is used on retry when Haiku returns
        invalid JSON or an unrecognised route (single escalation per hop).

    Decision #1: supervisor sees the full query text (not just length).
    With max_tokens=128 and a routing-only system prompt, the query echo
    risk is acceptable per the locked design.
    """
    settings = get_settings()
    query = state.get("query", "")
    worker_results = state.get("worker_results", [])

    # Build a minimal context message (no raw tool output, no chunk text).
    context_lines = []
    for wr in worker_results:
        # worker_results entries align to ToolCallSummary shape (decision #10).
        worker_name = wr.get("tool_name", wr.get("worker_name", "unknown"))
        context_lines.append(
            f"- Worker '{worker_name}' ran: "
            f"records={wr.get('record_count', 0)}, "
            f"citations_added={wr.get('citations_added', wr.get('record_count', 0))}"
        )
    context_summary = "\n".join(context_lines) if context_lines else "No workers called yet."

    # Indicate if a document context is present (structural flag, no PHI).
    has_doc_context = bool(state.get("tool_calls_accumulated"))
    doc_flag = "Document context: available." if has_doc_context else "Document context: none."

    # Decision #1: full query text (not just length).
    user_message = (
        f"Query: {query}\n\n"
        f"Hops so far: {state.get('hops_taken', 0)}. "
        f"{doc_flag}\n\n"
        f"Workers called so far:\n{context_summary}"
    )

    llm_client = build_llm_client()

    def _run_coro(coro: Any) -> Any:
        """Run a coroutine synchronously, handling running event loops."""
        try:
            asyncio.get_running_loop()
            _loop_running = True
        except RuntimeError:
            _loop_running = False

        if _loop_running:
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, coro)
                return future.result(timeout=30)
        else:
            return asyncio.run(coro)

    def _attempt(model: str) -> tuple[str, int, int]:
        """Make one LLM call and return (raw_response_text, tokens_in, tokens_out)."""
        coro = llm_client.create(
            model=model,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
            max_tokens=128,
            temperature=0.0,
        )
        try:
            response = _run_coro(coro)
        except Exception as sdk_exc:
            scrubbed = mask_observability_patterns(str(sdk_exc))
            raise RuntimeError(f"Supervisor LLM call failed: {scrubbed}") from None

        response_text = "".join(
            getattr(b, "text", "") for b in response.content
            if getattr(b, "type", None) == "text"
        ).strip()

        # Strip markdown fences if present.
        if response_text.startswith("```"):
            lines = response_text.splitlines()
            inner = lines[1:-1] if lines and lines[-1].strip() == "```" else lines[1:]
            response_text = "\n".join(inner).strip()

        # Extract token counts for observability (decision #14).
        usage = getattr(response, "usage", None)
        tokens_in = getattr(usage, "input_tokens", 0) if usage else 0
        tokens_out = getattr(usage, "output_tokens", 0) if usage else 0

        return response_text, tokens_in, tokens_out

    # First attempt: Haiku 4.5 (model_workhorse).
    haiku_model = settings.model_workhorse
    response_text, tok_in, tok_out = _attempt(haiku_model)

    # Try to parse.
    try:
        parsed = json.loads(response_text)
        route = str(parsed.get("route", "none")).strip().lower()
        if route in _ALLOWED_ROUTES:
            return route, tok_in, tok_out, False
        # Unrecognised route — fall through to Sonnet escalation.
        logger.info(
            "supervisor_node: Haiku returned unrecognised route '%s' — escalating to Sonnet",
            mask_observability_patterns(route[:32]),
        )
    except json.JSONDecodeError:
        logger.info("supervisor_node: Haiku returned non-JSON — escalating to Sonnet")

    # Decision #4b: retry once with Sonnet 4.6 on bad JSON or unrecognised route.
    sonnet_model = settings.model_reasoning
    response_text2, tok_in2, tok_out2 = _attempt(sonnet_model)

    try:
        parsed2 = json.loads(response_text2)
        route2 = str(parsed2.get("route", "none")).strip().lower()
    except json.JSONDecodeError:
        logger.warning("supervisor_node: Sonnet also returned non-JSON — defaulting to 'none'")
        route2 = "none"

    # Return escalated=True; token counts from Sonnet call.
    return route2, tok_in2, tok_out2, True


class _langfuse_span:
    """Context manager that opens a Langfuse span named 'supervisor.decide'
    when Langfuse is available, and is a no-op otherwise.

    W2_ARCHITECTURE.md §3.3: every supervisor routing decision is a span.
    Span metadata includes rationale text only at DEBUG level — the rationale
    may echo the user query and is PHI-adjacent (§8.3).
    """

    def __init__(self, lf: Any, available: bool, hops_taken: int) -> None:
        self._lf = lf
        self._available = available
        self._hops_taken = hops_taken
        self._span = None

    def __enter__(self) -> "_langfuse_span":
        if self._available and self._lf is not None:
            try:
                # Use start_observation (non-context-manager form) — matches installed
                # Langfuse version; start_span is not available in this version.
                self._span = self._lf.start_observation(
                    name="supervisor.decide",
                    metadata={"hops_taken": self._hops_taken},
                )
            except Exception:
                self._span = None
        return self

    def __exit__(self, *_: object) -> None:
        if self._span is not None:
            try:
                self._span.end()
            except Exception:
                pass
