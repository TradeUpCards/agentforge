"""Prompt template for LabReport schema extraction via Haiku.

Stable cacheable prefix + variable user block builder.

W2_ARCHITECTURE.md:
  §2.2  — two-stage pipeline: Docling layout → Haiku schema
  §7.1  — LabReport schema contract
  §8.3  — no-PHI-in-logs (prompt text never logged)

The SYSTEM_PROMPT is the cacheable prefix that should be stable
across every LabReport extraction call. Anthropic will keep it in
the cache tier once it has been written (cache_creation_input_tokens)
and subsequent calls see cache_read_input_tokens instead.

Do NOT interpolate doc content into this string. Doc blocks go into
the user message only.
"""

from __future__ import annotations

from agent.document_schemas import DoclingDoc

# ---------------------------------------------------------------------------
# Cacheable schema-spec system prompt (stable across calls)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT: str = """\
You are a structured-data extraction assistant. Your ONLY task is to extract \
lab report fields from document blocks into a strict JSON schema. You never \
invent values. If a field is not present in the document blocks, return null \
for that field's value and null for source_block_id.

OUTPUT CONTRACT
===============
Return a single JSON object with this exact shape:

{
  "results": [
    {
      "test_name": "<string>",
      "value": <number>,
      "unit": "<string>",
      "reference_range": "<string or null>",
      "abnormal": <true|false|null>,
      "collection_date": "<YYYY-MM-DD or null>",
      "source_block_id": "<block_id string from the provided index>",
      "confidence": <float 0.0 to 1.0>
    }
  ]
}

FIELD RULES
===========
- test_name: The exact test name as it appears in the document.
- value: Numeric result. Must be a JSON number (not a string). If non-numeric, \
  use the closest numeric interpretation or omit the result entirely.
- unit: Unit string exactly as it appears (e.g. "%" or "mg/dL").
- reference_range: Reference range string as it appears (e.g. "<5.7" or \
  "70-99"), or null if absent.
- abnormal: true if flagged as high/low/abnormal, false if explicitly normal, \
  null if not stated.
- collection_date: ISO 8601 date (YYYY-MM-DD) if present, else null.
- source_block_id: The block_id from the BLOCK INDEX that contains this result. \
  Must be an exact key from the provided index. Never invent a block_id.
- confidence: Your confidence that this field value is correctly extracted, \
  0.0 (no confidence) to 1.0 (certain). Be conservative: use 0.7-0.8 for \
  typical clear extractions, 0.4-0.6 for ambiguous ones.

CITATION DISCIPLINE
===================
Every result row MUST have a source_block_id that is a key in the BLOCK INDEX. \
If you cannot identify which block contains a result, omit that result entirely \
rather than inventing a block_id.

Return ONLY the JSON object. No prose before or after. No markdown fences.\
"""

# ---------------------------------------------------------------------------
# Variable user message builder
# ---------------------------------------------------------------------------


def build_user_message(doc: DoclingDoc) -> str:
    """Build the variable user message from a DoclingDoc.

    The returned string is the document block index + extraction instruction.
    It is NOT cached (it varies per document).

    No PHI discipline: this string is NEVER logged or surfaced in traces.
    Only the token count of this message is observable in Langfuse.
    """
    lines: list[str] = [
        "BLOCK INDEX",
        "===========",
        "Each entry: block_id | block_type | page | text",
        "",
    ]
    for block in doc.blocks:
        # Truncate very long block text to avoid token explosion while keeping
        # enough context for the LLM to match field values. 500 chars is well
        # above any single lab row but prevents runaway cost on multi-page
        # clinical PDFs with lengthy interpretation paragraphs.
        text_snippet = block.text[:500].replace("\n", " ").strip()
        lines.append(
            f"{block.block_id} | {block.block_type} | page={block.page} | {text_snippet}"
        )

    lines += [
        "",
        "TASK",
        "====",
        "Extract all individual lab test results from the block index above.",
        "Return JSON matching the schema defined in the system prompt.",
        "Use only block_ids from the BLOCK INDEX above.",
    ]
    return "\n".join(lines)
