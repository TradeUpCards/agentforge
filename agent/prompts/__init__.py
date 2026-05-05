"""Prompt templates for Haiku schema extraction — Stage 2.

Each submodule contains one cacheable schema-spec system prompt block and a
function that builds the variable user block from a DoclingDoc.

The system prompt blocks are kept stable across calls so Anthropic's prompt
caching actually fires (cache_control: ephemeral on the system block).
The variable user block (doc text + block index) is NEVER logged or traced
— only token/cost/latency metadata is surfaced in Langfuse.

W2_ARCHITECTURE.md §2.2 (two-stage pipeline), §8.3 (no-PHI-in-logs).
"""
