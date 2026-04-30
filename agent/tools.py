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
  for eval-suite determinism and for development without a populated DB.

Toggled via `USE_FIXTURE_DATA` in `agent/.env` (default: true). Production
flips it false. SQL conventions cribbed from OpenEMR's own service classes
(see `src/Services/ConditionService.php`, `PrescriptionService.php`,
`ObservationLabService.php`, `AllergyIntoleranceService.php`,
`EncounterService.php`).
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from typing import Any

import pymysql
import pymysql.cursors
from langfuse import get_client, observe

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


@observe(name="tool.execute")
async def execute_tool(tool_name: str, tool_input: dict[str, Any]) -> list[RetrievedRecord]:
    """Dispatch a tool call to the right handler.

    Returns a list of RetrievedRecord. An empty list is a valid "no records
    found" response per ARCHITECTURE.md §2.6 (explicit absence).
    """
    settings = get_settings()
    lf = get_client()
    lf.update_current_span(
        input={"tool_name": tool_name, "params": tool_input},
        metadata={"data_mode": "fixture" if settings.use_fixture_data else "live"},
    )
    # Tool data mode is independent of LLM mode. Today's typical config:
    # live LLM + fixture data (LLM observes the synthetic Maria Hernandez
    # records and cites their stable IDs accurately). Once real DB queries
    # land in _real_dispatch(), flip USE_FIXTURE_DATA=false.
    if settings.use_fixture_data:
        records = _fixture_dispatch(tool_name, tool_input)
    else:
        records = await _real_dispatch(tool_name, tool_input)
    lf.update_current_span(output={"record_count": len(records)})
    return records


# ---------------------------------------------------------------------------
# Fixture mode — deterministic records with stable IDs
# ---------------------------------------------------------------------------


_EMPTY_PATIENT_SENTINEL = 999999
"""Patient ID that the fixture layer returns empty for, regardless of tool.
Used by eval cases to exercise the "patient has no records on file" path
through the agent and verifier. See agent/tests/eval/cases/06_*.yaml."""


def _fixture_dispatch(tool_name: str, tool_input: dict[str, Any]) -> list[RetrievedRecord]:
    # Sentinel: agent-side eval path for "patient with no records on file".
    # All fixture tools return empty for this patient_id so we can exercise
    # the absence-claim verification path (verifier.py / ARCHITECTURE.md §3.7).
    if int(tool_input.get("patient_id", 0)) == _EMPTY_PATIENT_SENTINEL:
        return []

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
    """Run real DB queries against OpenEMR's MariaDB. PyMySQL is sync, so we
    drop into a threadpool — keeps FastAPI's event loop responsive."""
    patient_id = int(tool_input.get("patient_id", 0))
    if patient_id <= 0:
        return []

    handlers = {
        "get_problem_list": _real_get_problem_list,
        "get_active_medications": _real_get_active_medications,
        "get_recent_labs": _real_get_recent_labs,
        "get_allergies": _real_get_allergies,
        "get_recent_encounters": _real_get_recent_encounters,
    }
    handler = handlers.get(tool_name)
    if handler is None:
        raise ValueError(f"Unknown tool: {tool_name}")

    return await asyncio.get_event_loop().run_in_executor(None, handler, patient_id)


# ---------------------------------------------------------------------------
# Real-DB query implementations
#
# SQL queries cribbed from OpenEMR's own service classes (see file-level
# docstring) and trimmed to just the columns the agent needs. Narrow SELECT
# lists per AUDIT.md C-3 — never `patient_data.ss` or `drivers_license`.
# ---------------------------------------------------------------------------


@contextmanager
def _db_cursor():
    """Open a PyMySQL connection with a dict cursor. Read-only is enforced
    at the DB-user level (`agent_ro` has SELECT-only privileges); we don't
    rely on application-level enforcement."""
    settings = get_settings()
    conn = pymysql.connect(
        host=settings.db_host,
        port=settings.db_port,
        user=settings.db_user,
        password=settings.db_password,
        database=settings.db_name,
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=5,
        read_timeout=10,
        autocommit=True,
    )
    try:
        with conn.cursor() as cur:
            yield cur
    finally:
        conn.close()


def _real_get_problem_list(patient_id: int) -> list[RetrievedRecord]:
    """Active diagnoses / chronic conditions. Excludes ended problems."""
    sql = """
        SELECT id, title, diagnosis, `date` AS date_added, activity
        FROM lists
        WHERE type = 'medical_problem'
          AND pid = %s
          AND (enddate IS NULL OR enddate = '0000-00-00')
        ORDER BY `date` DESC
        LIMIT 30
    """
    out: list[RetrievedRecord] = []
    with _db_cursor() as cur:
        cur.execute(sql, (patient_id,))
        for row in cur.fetchall():
            # Citation strength: ICD-10 / SNOMED in `diagnosis` → code-backed;
            # free-text title only → structured (typed column, no code).
            diagnosis = row.get("diagnosis") or ""
            strength = (
                CitationStrength.CODE_BACKED
                if any(prefix in str(diagnosis) for prefix in ("ICD10:", "SNOMED:", "ICD9:"))
                else CitationStrength.STRUCTURED
            )
            out.append(RetrievedRecord(
                table="lists",
                record_id=str(row["id"]),
                citation_strength=strength,
                fields={
                    "title": row.get("title"),
                    "diagnosis": str(diagnosis) if diagnosis else None,
                    "date_added": str(row.get("date_added")) if row.get("date_added") else None,
                    "type": "medical_problem",
                },
            ))
    return out


def _real_get_active_medications(patient_id: int) -> list[RetrievedRecord]:
    """Active prescriptions. RxNorm-coded drugs are code-backed citation
    strength; free-text drug names without a code are structured."""
    sql = """
        SELECT id, drug, dosage, `interval`, route, quantity,
               start_date, date_added, active, rxnorm_drugcode
        FROM prescriptions
        WHERE patient_id = %s
          AND active = 1
        ORDER BY date_added DESC
        LIMIT 30
    """
    out: list[RetrievedRecord] = []
    with _db_cursor() as cur:
        cur.execute(sql, (patient_id,))
        for row in cur.fetchall():
            rxnorm = row.get("rxnorm_drugcode")
            strength = (
                CitationStrength.CODE_BACKED
                if rxnorm
                else CitationStrength.STRUCTURED
            )
            out.append(RetrievedRecord(
                table="prescriptions",
                record_id=str(row["id"]),
                citation_strength=strength,
                fields={
                    "drug": row.get("drug"),
                    "dosage": row.get("dosage"),
                    "frequency": row.get("interval"),
                    "route": row.get("route"),
                    "quantity": row.get("quantity"),
                    "start_date": str(row["start_date"]) if row.get("start_date") else None,
                    "date_added": str(row["date_added"]) if row.get("date_added") else None,
                    "rxnorm_drugcode": rxnorm,
                    "active": bool(row.get("active")),
                },
            ))
    return out


def _real_get_recent_labs(patient_id: int) -> list[RetrievedRecord]:
    """Recent lab results. LOINC-coded results are code-backed citation
    strength; raw values without codes drop to structured."""
    sql = """
        SELECT presult.procedure_result_id AS rid,
               presult.result, presult.units, presult.`range`,
               presult.abnormal, presult.result_code, presult.result_text,
               pordercode.procedure_name, pordercode.procedure_code,
               preport.date_report
        FROM procedure_result AS presult
        LEFT JOIN procedure_report AS preport
               ON preport.procedure_report_id = presult.procedure_report_id
        LEFT JOIN procedure_order AS porder
               ON porder.procedure_order_id = preport.procedure_order_id
        LEFT JOIN procedure_order_code AS pordercode
               ON pordercode.procedure_order_id = porder.procedure_order_id
        WHERE porder.patient_id = %s
        ORDER BY preport.date_report DESC
        LIMIT 50
    """
    out: list[RetrievedRecord] = []
    with _db_cursor() as cur:
        cur.execute(sql, (patient_id,))
        for row in cur.fetchall():
            loinc = row.get("result_code") or row.get("procedure_code")
            strength = (
                CitationStrength.CODE_BACKED
                if loinc
                else CitationStrength.STRUCTURED
            )
            out.append(RetrievedRecord(
                table="procedure_result",
                record_id=str(row["rid"]),
                citation_strength=strength,
                fields={
                    "loinc": row.get("result_code") or row.get("procedure_code"),
                    "name": row.get("result_text") or row.get("procedure_name"),
                    "value": row.get("result"),
                    "units": row.get("units"),
                    "reference_range": row.get("range"),
                    "abnormal_flag": row.get("abnormal"),
                    "date": str(row["date_report"]) if row.get("date_report") else None,
                },
            ))
    return out


def _real_get_allergies(patient_id: int) -> list[RetrievedRecord]:
    sql = """
        SELECT id, title, reaction, severity_al, `date` AS date_added
        FROM lists
        WHERE type = 'allergy'
          AND pid = %s
          AND (enddate IS NULL OR enddate = '0000-00-00')
        ORDER BY `date` DESC
        LIMIT 20
    """
    out: list[RetrievedRecord] = []
    with _db_cursor() as cur:
        cur.execute(sql, (patient_id,))
        for row in cur.fetchall():
            out.append(RetrievedRecord(
                table="lists",
                record_id=str(row["id"]),
                citation_strength=CitationStrength.STRUCTURED,
                fields={
                    "title": row.get("title"),
                    "type": "allergy",
                    "severity": row.get("severity_al"),
                    "reaction": row.get("reaction"),
                    "date_added": str(row["date_added"]) if row.get("date_added") else None,
                },
            ))
    return out


def _real_get_recent_encounters(patient_id: int) -> list[RetrievedRecord]:
    """Most recent encounters with SOAP-form assessment + plan when
    available. SOAP forms are optional — older or migrated encounters may
    not have them. Free-text SOAP fields are free-text citation strength."""
    sql = """
        SELECT fe.encounter, fe.`date`, fe.reason,
               fs.subjective, fs.objective, fs.assessment, fs.plan
        FROM form_encounter AS fe
        LEFT JOIN forms AS fo
               ON fo.encounter = fe.encounter
              AND fo.formdir = 'soap'
              AND fo.deleted = 0
        LEFT JOIN form_soap AS fs
               ON fs.id = fo.form_id AND fs.pid = fe.pid
        WHERE fe.pid = %s
        ORDER BY fe.`date` DESC
        LIMIT 5
    """
    out: list[RetrievedRecord] = []
    with _db_cursor() as cur:
        cur.execute(sql, (patient_id,))
        for row in cur.fetchall():
            # SOAP-derived data is free-text → weakest citation strength.
            has_soap = any(row.get(k) for k in ("subjective", "objective", "assessment", "plan"))
            strength = (
                CitationStrength.FREE_TEXT
                if has_soap
                else CitationStrength.STRUCTURED
            )
            assessment_plan = "\n".join(filter(None, [
                f"Assessment: {row['assessment']}" if row.get("assessment") else None,
                f"Plan: {row['plan']}" if row.get("plan") else None,
            ]))
            out.append(RetrievedRecord(
                table="form_encounter",
                record_id=str(row["encounter"]),
                citation_strength=strength,
                fields={
                    "date": str(row["date"]) if row.get("date") else None,
                    "reason": row.get("reason"),
                    "assessment_plan": assessment_plan or None,
                },
            ))
    return out
