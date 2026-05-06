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
You never invent values. If a field is genuinely absent from the document, \
return null. But: real intake forms ALWAYS contain medications, allergies, \
and family history sections — if the arrays come back empty, you are \
probably skipping data that's in tables. Read every block carefully.

WHERE THE DATA LIVES (real intake forms)
=========================================
- Demographics: section headed PERSONAL DETAILS, PATIENT INFORMATION, or \
  similar. Fields: legal name (often "Last, First M."), date of birth \
  (DOB / Date of Birth), sex / gender.
- Chief concern: section headed REASON FOR VISIT, CHIEF CONCERN, or \
  CHIEF COMPLAINT. The value is usually free-text describing symptoms.
- Current medications: section headed CURRENT MEDICATIONS, MEDICATIONS, \
  or MEDICATION LIST. Layout is usually a TABLE with columns like \
  Medication / Dose / Route / Frequency / Started / Reason. Each ROW is \
  one medication. Extract every row.
- Allergies: section headed ALLERGIES, ALLERGIES & INTOLERANCES, or \
  DRUG ALLERGIES. Usually a TABLE with columns like Substance / Reaction \
  / Severity. Each row is one allergy. Extract every row, even uncertain \
  ones (e.g. "shellfish? maybe iodine") — record what's there.
- Family history: section headed FAMILY HEALTH HISTORY, FAMILY HISTORY, \
  or FAMILY MEDICAL HISTORY. Usually a TABLE with columns like Relation \
  / Condition / Age at onset. Each row is one family member's condition.

TABLES IN BLOCK TEXT
=====================
Docling renders multi-row tables into a single block whose text contains \
ALL rows concatenated (e.g. "MEDICATION DOSE ... Lisinopril 10 mg PO ... \
Metformin 500 mg PO ..."). Treat one such block as the source for ALL \
items extracted from it — every medication, allergy, or family history \
row from the same table block uses the same source_block_id.

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
- demographics.name: Patient name as it appears on the form. If the form \
  shows "Last, First M.", reproduce that punctuation faithfully.
- demographics.dob: Date of birth in ISO 8601 (YYYY-MM-DD) or null.
- demographics.sex: Sex/gender as recorded on the form or null.
- demographics.source_block_id: Block containing the demographics section.
- chief_concern: Quote the chief concern free-text verbatim from the source \
  block. Do not paraphrase or shorten.
- chief_concern_block_id: Block containing the chief concern, or null.
- current_medications: Each medication. Use the medication name as the \
  drug name only (e.g. "Lisinopril", not "Lisinopril 10 mg"). Put the \
  dose ("10 mg") in `dose` and the frequency/route ("PO daily (AM)") in \
  `frequency`. Extract every row from the medications table.
- allergies: Each allergy entry. Substance is the drug or food. \
  Reaction is what happens (e.g. "Hives", "Rash"). Severity is the \
  intensity ("Mild", "Moderate", "Severe", or null if unsure).
- family_history: Each family member's condition. Condition is the \
  diagnosis. Relation is the family role ("Father", "Mother", "Brother", \
  etc.).
- source_citations: Map of top-level field names to source block_ids.
- confidence: 0.7-0.9 for forms with clear sections; 0.4-0.6 for unclear.

EXAMPLE (typed intake form, table sections)
============================================
If the BLOCK INDEX contains a block_3 with text like:
  "MEDICATION DOSE ROUTE/FREQUENCY STARTED REASON Lisinopril 10 mg PO \
  daily (AM) 2018 High blood pressure Metformin 500 mg PO twice daily \
  2020 Type 2 diabetes"

Then current_medications must contain TWO entries:
  [
    {"name": "Lisinopril", "dose": "10 mg", "frequency": "PO daily (AM)", \
     "source_block_id": "block_3"},
    {"name": "Metformin", "dose": "500 mg", "frequency": "PO twice daily", \
     "source_block_id": "block_3"}
  ]

Both entries cite the same block_3 because they came from the same \
table block.

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
        # 2000-char truncation: tables on intake forms (medications, allergies,
        # family history) can render as a single block with many concatenated
        # rows.  500 chars was the original cap but truncated mid-table for
        # forms with 5+ medications.  2000 fits ~10 medication rows comfortably.
        text_snippet = block.text[:2000].replace("\n", " ").strip()
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
