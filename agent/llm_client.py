"""LLM client abstraction.

Two implementations share the same interface so the agent loop is identical
whether we're talking to a real Anthropic API or local fixtures:

- `AnthropicLLMClient` — wraps the official Anthropic SDK
- `FixtureLLMClient` — returns canned Anthropic-shaped responses from
  `agent/fixtures/llm/`. Used when no API key is present (path-ii dev mode).

The agent loop calls `client.create(...)` and gets back an Anthropic
`Message` object either way. Tomorrow's swap from fixtures to real LLM is a
config change, not a code change.

Architectural reference: prd.md §5 (LLM provider config), ARCHITECTURE.md §2.1.
"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from anthropic import AsyncAnthropic
from anthropic.types import Message
from langfuse import get_client, observe

from .config import Settings, get_settings


class LLMClient(ABC):
    """Minimal interface the agent loop uses. Returns Anthropic SDK `Message`."""

    @abstractmethod
    async def create(
        self,
        *,
        model: str,
        system: str | list[dict[str, Any]],
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 2048,
        temperature: float = 0.0,
    ) -> Message: ...


class AnthropicLLMClient(LLMClient):
    """Real Anthropic SDK. Used when a non-empty API key is configured."""

    def __init__(self, settings: Settings) -> None:
        self._client = AsyncAnthropic(
            api_key=settings.anthropic_api_key,
            base_url=settings.anthropic_base_url,
        )

    @observe(
        as_type="generation",
        name="anthropic.messages.create",
        # PHI guard: by default Langfuse @observe captures function args at
        # entry (system + messages — full prompt, PHI when patient context
        # is referenced) and the return Message at exit (full LLM response,
        # PHI in claim text).  Disable auto-capture; the body below writes
        # only structural metadata (model, token counts, cost attribution,
        # cache stats, stop_reason) via update_current_generation().  We
        # lose the ability to debug LLM hallucinations from the trace UI,
        # but token tracking + cost accounting + cache utilization stay
        # intact — those are the demo-graded observability signals.
        capture_input=False,
        capture_output=False,
    )
    async def create(
        self,
        *,
        model: str,
        system: str | list[dict[str, Any]],
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 2048,
        temperature: float = 0.0,
    ) -> Message:
        kwargs: dict[str, Any] = {
            "model": model,
            "system": system,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            kwargs["tools"] = tools

        # Pre-call: stamp model + structural input shape (no PHI).
        # Langfuse generation spans require nominal input/output to record
        # as "generation" type (vs plain span); we provide structural-only
        # summaries — character counts and message counts, no content.
        # The actual content is sent to Anthropic but never to Langfuse.
        lf = get_client()
        system_chars = len(system) if isinstance(system, str) else sum(
            len(str(s.get("text", ""))) for s in system if isinstance(s, dict)
        )
        lf.update_current_generation(
            input={
                "system_prompt_chars": system_chars,
                "n_messages": len(messages),
                "has_tools": bool(tools),
                "n_tools": len(tools) if tools else 0,
            },
            model=model,
            model_parameters={
                "max_tokens": max_tokens,
                "temperature": temperature,
            },
            metadata={"has_tools": bool(tools)},
        )

        response = await self._client.messages.create(**kwargs)

        # Post-call: structural-only output (length, finish reason).  Token
        # counts + cache stats stay — those are observability primitives
        # that don't carry PHI.
        output_text = "".join(
            getattr(b, "text", "") for b in response.content
            if getattr(b, "type", None) == "text"
        )
        usage = response.usage
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_create = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cache_hit_rate = (
            cache_read / max(usage.input_tokens, 1)
            if usage.input_tokens
            else 0.0
        )
        lf.update_current_generation(
            output={
                "output_chars": len(output_text),
                "n_content_blocks": len(response.content) if response.content else 0,
            },
            usage_details={
                "input": usage.input_tokens,
                "output": usage.output_tokens,
                "cache_read_input_tokens": cache_read,
                "cache_creation_input_tokens": cache_create,
            },
            metadata={
                "stop_reason": response.stop_reason,
                "prompt_cache_hit_rate": round(cache_hit_rate, 4),
            },
        )
        return response


class FixtureLLMClient(LLMClient):
    """Path-ii dev mode. Returns a canned Anthropic-shaped response.

    Today, returns a single generic pre-visit-brief-shaped response regardless
    of input. As we develop the eval suite, we'll route to specific fixture
    files based on a request-shape hash — but week-1 reality is this stub
    proves the contract works end-to-end without burning tokens.
    """

    def __init__(self, fixtures_dir: Path | None = None) -> None:
        self._fixtures_dir = fixtures_dir or Path(__file__).parent / "fixtures" / "llm"

    async def create(
        self,
        *,
        model: str,
        system: str | list[dict[str, Any]],
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 2048,
        temperature: float = 0.0,
    ) -> Message:
        # For now, always return the default fixture. Eval suite will key on
        # specific fixtures later.
        fixture_path = self._fixtures_dir / "default_pre_visit_brief.json"
        if not fixture_path.exists():
            raise FileNotFoundError(
                f"Fixture not found: {fixture_path}. "
                "Either create it or set ANTHROPIC_API_KEY to use a real LLM."
            )
        payload = json.loads(fixture_path.read_text(encoding="utf-8"))
        return Message.model_validate(payload)


def build_llm_client(settings: Settings | None = None) -> LLMClient:
    """Factory: returns the right client per config."""
    settings = settings or get_settings()
    if settings.use_fixture_llm:
        return FixtureLLMClient()
    if not settings.has_real_llm_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is empty but USE_FIXTURE_LLM is false. "
            "Set USE_FIXTURE_LLM=auto or true, or provide a real key."
        )
    return AnthropicLLMClient(settings)
