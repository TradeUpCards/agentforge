"""One-off cache-verification script.

Fires two consecutive identical UC1 requests directly against the
LLM (bypassing the agent loop, so we can read the raw Anthropic
`usage` field). Second call should show non-zero
`cache_read_input_tokens` if explicit prompt caching is working.

Run from repo root:
    USE_FIXTURE_DATA=true USE_FIXTURE_LLM=false \
      agent/venv/Scripts/python.exe -m agent.tests.verify_cache
"""

from __future__ import annotations

import asyncio

import os
os.environ["USE_FIXTURE_DATA"] = "false"

from agent.config import get_settings
from agent.agent import _SYSTEM_PROMPT_STATIC, _format_patient_context
from agent.llm_client import AnthropicLLMClient
from agent.tools import (
    _real_get_problem_list,
    _real_get_active_medications,
    _real_get_recent_labs,
    _real_get_allergies,
    _real_get_recent_encounters,
)


def _build_system_blocks(patient_id: int = 92) -> list[dict]:
    records = (
        _real_get_problem_list(patient_id)
        + _real_get_active_medications(patient_id)
        + _real_get_recent_labs(patient_id)
        + _real_get_allergies(patient_id)
        + _real_get_recent_encounters(patient_id)
    )
    patient_context = _format_patient_context(records)
    return [
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


async def _one_call(label: str, client: AnthropicLLMClient, model: str, system: list[dict]) -> None:
    print(f"\n=== {label} ===")
    response = await client.create(
        model=model,
        system=system,
        messages=[
            {"role": "user", "content": "Generate a pre-visit brief for this patient."}
        ],
        max_tokens=4096,
        temperature=0.0,
    )
    usage = response.usage
    print(f"  input_tokens:                 {usage.input_tokens}")
    print(f"  output_tokens:                {usage.output_tokens}")
    print(f"  cache_creation_input_tokens:  {getattr(usage, 'cache_creation_input_tokens', 0) or 0}")
    print(f"  cache_read_input_tokens:      {getattr(usage, 'cache_read_input_tokens', 0) or 0}")


async def main() -> None:
    settings = get_settings()
    print("Model (workhorse):", settings.model_workhorse)
    print("Base URL:", settings.anthropic_base_url)

    client = AnthropicLLMClient(settings)
    system = _build_system_blocks()

    static_block_chars = len(system[0]["text"])
    context_block_chars = len(system[1]["text"])
    print(f"Static block: {static_block_chars} chars (~{static_block_chars // 4} tokens)")
    print(f"Context block: {context_block_chars} chars (~{context_block_chars // 4} tokens)")

    await _one_call("Call 1 (cache CREATION expected)", client, settings.model_workhorse, system)
    await _one_call("Call 2 (cache READ expected)", client, settings.model_workhorse, system)


if __name__ == "__main__":
    asyncio.run(main())
