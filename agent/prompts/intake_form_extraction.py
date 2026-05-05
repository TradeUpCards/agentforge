"""Prompt template for IntakeForm schema extraction via Haiku.

Stable cacheable prefix + variable user block builder.

W2_ARCHITECTURE.md:
  §2.2  — two-stage pipeline: Docling layout → Haiku schema
  §7.2  — IntakeForm schema contract
  §8.3  — no-PHI-in-logs (prompt text never logged)

The SYSTEM_PROMPT is the cacheable prefix. It must remain stable across
every IntakeForm extraction call for cache hits to occur.

Do NOT interpolate doc content into this string.
"""

from __future__ import annotations

from agent.document_schemas import DoclingDoc

# ---------------------------------------------------------------------------
# Cacheable schema-spec system prompt (stable across calls)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT: str = """\
You are a structured-data extraction assistant. Your ONLY task is to extract \
patient intake form fields from document blocks into a strict JSON schema. \
You never invent values. If a field is not present in the document blocks, \
return null for that field's value and null for source_block_id.

OUTPUT CONTRACT
===============
Return a single JSON object with this exact shape:

{
  "demographics": {
    "name": "<string>",
    "dob": "<YYYY-MM-DD or null>",
    "sex": "<string or null>",
    "source_block_id": "<block_id from index or null>"
  },
  "chief_concern": "<string or null>",
  "chief_concern_block_id": "<block_id from index or null>",
  "current_medications": [
    {
      "name": "<string>",
      "dose": "<string or null>",
      "frequency": "<string or null>",
      "source_block_id": "<block_id from index>"
    }
  ],
  "allergies": [
    {
      "substance": "<string>",
      "reaction": "<string or null>",
      "severity": "<string or null>",
      "source_block_id": "<block_id from index>"
    }
  ],
  "family_history": [
    {
      "condition": "<string>",
      "relation": "<string or null>",
      "source_block_id": "<block_id from index>"
    }
  ],
  "source_citations": {
    "<field_name>": "<block_id from index>"
  },
  "confidence": <float 0.0 to 1.0>
}

FIELD RULES
===========
- demographics.name: Patient name as it appears on the form.
- demographics.dob: Date of birth in ISO 8601 (YYYY-MM-DD) or null.
- demographics.sex: Sex/gender as recorded on the form or null.
- demographics.source_block_id: Block containing the demographics section.
- chief_concern: Free-text chief complaint or reason for visit, or null.
- chief_concern_block_id: Block containing the chief concern, or null.
- current_medications: Each medication the patient is taking. Include name; \
  dose and frequency are optional.
- allergies: Each allergy entry. Include substance; reaction and severity \
  are optional.
- family_history: Each family history item. Include condition; relation \
  (e.g. "Father", "Mother") is optional.
- source_citations: Map of top-level field names to their source block_ids \
  (e.g. {"demographics": "block_0", "chief_concern": "block_2"}).
- confidence: Overall extraction confidence 0.0-1.0. Use 0.7-0.8 for \
  clear forms, 0.4-0.6 for ambiguous ones.

CITATION DISCIPLINE
===================
Every item in current_medications, allergies, and family_history MUST have \
a source_block_id that is a key in the BLOCK INDEX. If you cannot identify \
which block contains an item, omit that item rather than inventing a block_id.

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
        # 500-char truncation: enough context to match form fields while
        # preventing token explosion on lengthy narrative blocks.
        text_snippet = block.text[:500].replace("\n", " ").strip()
        lines.append(
            f"{block.block_id} | {block.block_type} | page={block.page} | {text_snippet}"
        )

    lines += [
        "",
        "TASK",
        "====",
        "Extract all intake form fields from the block index above.",
        "Return JSON matching the schema defined in the system prompt.",
        "Use only block_ids from the BLOCK INDEX above.",
    ]
    return "\n".join(lines)
