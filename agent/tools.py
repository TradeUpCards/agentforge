"""Data tools the agent invokes via Anthropic tool-use.

Each tool is a plain Python function with structured I/O — see ARCHITECTURE.md
§2.6 (tool return contract).

Two execution modes share the same external interface:

- **Real mode:** queries OpenEMR's MariaDB directly via PyMySQL using a
  dedicated read-only DB user (per prd.md §5 v1 data path). Tools NEVER
  request `patient_data.ss` / `drivers_license` per AUDIT.md C-3 — narrow
  SELECT lists.

- **Fixture mode:** returns deterministic fixture records with stable
  record_ids the LLM fixture (in `agent/fixtures/llm/`) references. Used
  when no API key is present; lets the agent loop be exercised end-to-end
  without an LLM and without depending on local DB schema.

Real-DB queries land at the start of Phase 5 once the local stack is verified
running and the schema is double-checked against `src/Services/*.php`. Until
then the real-mode functions raise NotImplementedError — agent runs in
fixture mode regardless.
"""

from __future__ import annotations

from typing import Any

from .config import get_settings
from .schemas import CitationStrength, RetrievedRecord


# ---------------------------------------------------------------------------
# Tool registry — exposed to the LLM as Anthropic tool-use schemas
# ---------------------------------------------------------------------------


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "get_problem_list",
        "description": (
            "Return the patient's active problem list (chronic conditions and "
            "ongoing diagnoses) with onset dates. Cite results by record_id "
            "in the form 'lists:<id>'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"patient_id": {"type": "integer"}},
            "required": ["patient_id"],
        },
    },
    {
        "name": "get_active_medications",
        "description": (
            "Return the patient's active prescriptions with drug name, dose, "
            "frequency, start date. Cite as 'prescriptions:<id>'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"patient_id": {"type": "integer"}},
            "required": ["patient_id"],
        },
    },
    {
        "name": "get_recent_labs",
        "description": (
            "Return lab results for the patient, optionally filtered by date "
            "or LOINC codes. Cite as 'procedure_result:<id>'. Each lab "
            "includes value, units, reference range, and abnormal flag if set."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "patient_id": {"type": "integer"},
                "since_date": {
                    "type": "string",
                    "description": "ISO date; only results on/after this date.",
                },
                "loinc_codes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional LOINC codes to filter by.",
                },
            },
            "required": ["patient_id"],
        },
    },
    {
        "name": "get_allergies",
        "description": "Return the patient's allergies. Cite as 'lists:<id>'.",
        "input_schema": {
            "type": "object",
            "properties": {"patient_id": {"type": "integer"}},
            "required": ["patient_id"],
        },
    },
    {
        "name": "get_recent_encounters",
        "description": (
            "Return the patient's recent encounters with date, reason, and "
            "the assessment-and-plan section of any associated note. "
            "Cite as 'form_encounter:<id>'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "patient_id": {"type": "integer"},
                "limit": {"type": "integer", "default": 5},
            },
            "required": ["patient_id"],
        },
    },
]


# ---------------------------------------------------------------------------
# Public dispatch
# ---------------------------------------------------------------------------


async def execute_tool(tool_name: str, tool_input: dict[str, Any]) -> list[RetrievedRecord]:
    """Dispatch a tool call to the right handler.

    Returns a list of RetrievedRecord. An empty list is a valid "no records
    found" response per ARCHITECTURE.md §2.6 (explicit absence).
    """
    settings = get_settings()
    if settings.use_fixture_llm:
        return _fixture_dispatch(tool_name, tool_input)
    return await _real_dispatch(tool_name, tool_input)


# ---------------------------------------------------------------------------
# Fixture mode — deterministic records with stable IDs
# ---------------------------------------------------------------------------


def _fixture_dispatch(tool_name: str, tool_input: dict[str, Any]) -> list[RetrievedRecord]:
    if tool_name == "get_problem_list":
        return _fixture_problems()
    if tool_name == "get_active_medications":
        return _fixture_medications()
    if tool_name == "get_recent_labs":
        return _fixture_labs()
    if tool_name == "get_allergies":
        return _fixture_allergies()
    if tool_name == "get_recent_encounters":
        return _fixture_encounters()
    raise ValueError(f"Unknown tool: {tool_name}")


def _fixture_problems() -> list[RetrievedRecord]:
    return [
        RetrievedRecord(
            table="lists",
            record_id="1408",
            citation_strength=CitationStrength.STRUCTURED,
            fields={
                "title": "Type 2 diabetes mellitus",
                "diagnosis": "ICD-10:E11.9",
                "date_added": "2024-08-12",
                "type": "medical_problem",
            },
        ),
        RetrievedRecord(
            table="lists",
            record_id="1411",
            citation_strength=CitationStrength.STRUCTURED,
            fields={
                "title": "Essential hypertension",
                "diagnosis": "ICD-10:I10",
                "date_added": "2023-02-04",
                "type": "medical_problem",
            },
        ),
    ]


def _fixture_medications() -> list[RetrievedRecord]:
    return [
        RetrievedRecord(
            table="prescriptions",
            record_id="2231",
            citation_strength=CitationStrength.STRUCTURED,
            fields={
                "drug": "Metformin",
                "dosage": "1000mg",
                "frequency": "BID",
                "start_date": "2025-10-04",
                "active": True,
            },
        ),
        RetrievedRecord(
            table="prescriptions",
            record_id="2289",
            citation_strength=CitationStrength.STRUCTURED,
            fields={
                "drug": "Lisinopril",
                "dosage": "10mg",
                "frequency": "QD",
                "start_date": "2023-02-15",
                "active": True,
            },
        ),
    ]


def _fixture_labs() -> list[RetrievedRecord]:
    return [
        RetrievedRecord(
            table="procedure_result",
            record_id="5421",
            citation_strength=CitationStrength.CODE_BACKED,
            fields={
                "loinc": "4548-4",
                "name": "HbA1c",
                "value": "7.8",
                "units": "%",
                "reference_range": "<5.7",
                "abnormal_flag": "H",
                "date": "2026-03-15",
            },
        ),
        RetrievedRecord(
            table="procedure_result",
            record_id="4892",
            citation_strength=CitationStrength.CODE_BACKED,
            fields={
                "loinc": "4548-4",
                "name": "HbA1c",
                "value": "6.8",
                "units": "%",
                "reference_range": "<5.7",
                "abnormal_flag": "H",
                "date": "2025-12-10",
            },
        ),
    ]


def _fixture_allergies() -> list[RetrievedRecord]:
    return [
        RetrievedRecord(
            table="lists",
            record_id="1399",
            citation_strength=CitationStrength.STRUCTURED,
            fields={
                "title": "Penicillin",
                "type": "allergy",
                "severity": "moderate",
                "reaction": "rash",
                "date_added": "2018-05-22",
            },
        ),
    ]


def _fixture_encounters() -> list[RetrievedRecord]:
    return [
        RetrievedRecord(
            table="form_encounter",
            record_id="9012",
            citation_strength=CitationStrength.STRUCTURED,
            fields={
                "date": "2026-01-22",
                "reason": "Diabetes follow-up",
                "assessment_plan": (
                    "T2DM, suboptimal control. Continue metformin, repeat A1c "
                    "in 3 months. Reinforced dietary counseling."
                ),
            },
        ),
    ]


# ---------------------------------------------------------------------------
# Real mode — placeholder; lands at start of Phase 5
# ---------------------------------------------------------------------------


async def _real_dispatch(tool_name: str, tool_input: dict[str, Any]) -> list[RetrievedRecord]:
    raise NotImplementedError(
        "Real-DB queries are wired in Phase 5 (after local OpenEMR schema is verified). "
        "For now, set USE_FIXTURE_LLM=auto in agent/.env to run in fixture mode."
    )
