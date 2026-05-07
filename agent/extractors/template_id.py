"""Template ID resolver for eval metrics (P1 eval metrics — PRD §8.3).

Maps uploaded filenames to canonical template identifiers for the
haiku_schema_extraction Langfuse span. Unknown filenames resolve to
"unknown" rather than raising, so a novel fixture file never blocks a run.

PHI discipline: filenames are structural metadata, not PHI. Template IDs
are string labels used only for observability grouping — safe to log and
trace. The raw filename is never emitted to any span or log; only the
resolved template_id string is used.
"""

from __future__ import annotations

# Canonical mapping of synthetic fixture filenames to template labels.
# Each template_id encodes lab vendor + panel + schema version.
# Add new entries here as new fixture PDFs are introduced.
# Do NOT invent template IDs for unknown filenames at runtime.
TEMPLATE_BY_FILENAME: dict[str, str] = {
    "p01-chen-lipid-panel.pdf":  "labcorp_lipid_v1",
    "p02-whitaker-cbc.pdf":      "labcorp_cbc_v1",
    "p03-reyes-hba1c.png":       "quest_hba1c_v1",
    "p04-kowalski-cmp.pdf":      "labcorp_cmp_v1",
    "p01-chen-intake-typed.pdf": "intake_form_v1",
    "p02-whitaker-intake.pdf":   "intake_form_v1",
    "p03-reyes-intake.png":      "intake_form_v1",
    "p04-kowalski-intake.png":   "intake_form_v1",
}

_UNKNOWN = "unknown"


def resolve_template_id(filename: str | None) -> str:
    """Return the canonical template ID for a given filename.

    Matches on the final path component only (basename), so callers may
    pass a full absolute path or just the bare filename.

    Returns "unknown" for None, empty string, or unrecognised filenames.
    The raw filename is never logged or included in span attributes —
    only the return value of this function is safe to emit.

    PHI-safe: filename is structural metadata, not patient data. However,
    clinical filenames sometimes embed patient names or MRNs. The resolver
    only outputs canonical template IDs from a closed vocabulary, so no
    patient-derived string can escape via this function's return value.
    """
    if not filename:
        return _UNKNOWN
    # Strip directory components — the dict keys are basenames only.
    basename = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    return TEMPLATE_BY_FILENAME.get(basename, _UNKNOWN)
