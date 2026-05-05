"""Supervisor node (W2 Stage-3b).

The supervisor is the LangGraph entry node. It:
  1. Reads the current state (query + accumulated results so far).
  2. Calls Anthropic (Sonnet — reasoning capacity per §3 model split) with a
     small system prompt asking for a JSON routing decision.
  3. Validates the route is in the allowed set.
  4. Increments hops_taken.
  5. Returns a state patch.

If hops_taken would exceed 4, it sets terminal_reason = "supervisor_max_hops" and emits
the SUPERVISOR_MAX_HOPS_REACHED sentinel log line.

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
from agent.graph.state import SupervisorState
from agent.llm_client import build_llm_client

logger = logging.getLogger(__name__)

# Hard cap on worker hops per §3.2.
_MAX_HOPS = 4

# The supervisor uses Sonnet for routing decisions (reasoning capacity).
# Model name from config.model_reasoning; hardcoded fallback matches §3.
_SUPERVISOR_MODEL = "claude-sonnet-4-6"

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
    try:
        with _langfuse_span(lf, _langfuse_available, hops_taken) as _span:
            route = _call_supervisor_llm(state)
            latency_ms = int((time.monotonic() - t0) * 1000)
            # Tag span with structural-only metadata: route category + latency.
            # The rationale string is intentionally not logged (may echo query).
            if _langfuse_available and lf is not None:
                try:
                    lf.update_current_span(
                        metadata={
                            "route": route,
                            "hops_taken": hops_taken,
                            "latency_ms": latency_ms,
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
    return {"hops_taken": hops_taken + 1, "_pending_route": route}


def _call_supervisor_llm(state: SupervisorState) -> str:
    """Call the Anthropic LLM and return the chosen route string.

    Structural: returns route only; rationale is parsed but discarded
    (it may echo the query — PHI-adjacent per §8.3).
    """
    query = state.get("query", "")
    worker_results = state.get("worker_results", [])

    # Build a minimal context message (no raw tool output, no chunk text).
    context_lines = []
    for wr in worker_results:
        worker_name = wr.get("worker_name", "unknown")
        context_lines.append(
            f"- Worker '{worker_name}' ran: "
            f"records={wr.get('record_count', 0)}, "
            f"citations_added={wr.get('citations_added', 0)}"
        )
    context_summary = "\n".join(context_lines) if context_lines else "No workers called yet."

    # Indicate if a document context is present (structural flag, no PHI).
    has_doc_context = bool(state.get("tool_calls_accumulated"))
    doc_flag = "Document context: available." if has_doc_context else "Document context: none."

    user_message = (
        f"Query length: {len(query)} chars. "
        f"Hops so far: {state.get('hops_taken', 0)}. "
        f"{doc_flag}\n\n"
        f"Workers called so far:\n{context_summary}"
    )

    llm_client = build_llm_client()

    # asyncio.run() is safe here: supervisor_node is called synchronously
    # by LangGraph. The async worker wraps in attach_and_extract_async per
    # the Stage-2 footgun note.
    # Python 3.12: use get_running_loop() to detect running loop safely.
    try:
        asyncio.get_running_loop()
        _loop_running = True
    except RuntimeError:
        _loop_running = False

    coro = llm_client.create(
        model=_SUPERVISOR_MODEL,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
        max_tokens=128,
        temperature=0.0,
    )

    try:
        if _loop_running:
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, coro)
                response = future.result(timeout=30)
        else:
            response = asyncio.run(coro)
    except Exception as sdk_exc:
        scrubbed = mask_observability_patterns(str(sdk_exc))
        raise RuntimeError(
            f"Supervisor LLM call failed: {scrubbed}"
        ) from None

    response_text = "".join(
        getattr(b, "text", "") for b in response.content
        if getattr(b, "type", None) == "text"
    ).strip()

    # Strip markdown fences if present.
    if response_text.startswith("```"):
        lines = response_text.splitlines()
        inner = lines[1:-1] if lines and lines[-1].strip() == "```" else lines[1:]
        response_text = "\n".join(inner).strip()

    try:
        parsed = json.loads(response_text)
    except json.JSONDecodeError:
        logger.warning("supervisor_node: LLM returned non-JSON — defaulting to 'none'")
        return "none"

    route = str(parsed.get("route", "none")).strip().lower()
    # Rationale is parsed but never logged (PHI-adjacent).
    return route


class _langfuse_span:
    """Context manager that opens a Langfuse span named 'supervisor.decide'
    when Langfuse is available, and is a no-op otherwise.

    W2_ARCHITECTURE.md §3.3: every supervisor routing decision is a span.
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
