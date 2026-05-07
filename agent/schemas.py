"""Pydantic models for the AgentForge Clinical Co-Pilot agent service.

All wire-format types live here so the FastAPI layer, the agent loop, the
verifier, and the eval suite all agree on shape.

Architectural references:
- ARCHITECTURE.md §2.6 (tool return contract)
- ARCHITECTURE.md §3.2 (LLM emission contract — structured-first)
- ARCHITECTURE.md §3.6 (verifier placement, atomic strip, 30% rule)
- prd.md §5 (chat-shaped contract decisions)
- W2_ARCHITECTURE.md §7.3 (Citation extension — bbox / page / visual-overlay)
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from agent.document_schemas import BBox


# ---------------------------------------------------------------------------
# Conversation primitives
# ---------------------------------------------------------------------------


class Role(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"


class Message(BaseModel):
    """A single turn in the chat conversation. Sent to and from the agent."""

    role: Role
    content: str


# ---------------------------------------------------------------------------
# Citation primitives
# ---------------------------------------------------------------------------


class Citation(BaseModel):
    """Machine-readable citation attached to every clinical claim.

    W2_ARCHITECTURE.md §7.3 — extends Week 1's source_record_ids pattern
    to a richer object that can represent patient-record facts, guideline
    evidence, and extracted-document facts distinctly. The discriminator
    (source_type) prevents the answer model from blurring the three flavors.

    bbox is populated only for extracted_document citations, where Docling
    supplied real page-coordinate bounding boxes. It is never filled by an
    LLM — coordinates always come from the layout engine.

    Guideline evidence cites:
        source_id          → guideline chunk_id (e.g. "ada-2024-s2-3-chunk-7")
        page_or_section    → guideline section (e.g. "S2.3")
        field_or_chunk_id  → stable chunk ID for RAG roundtrip
        quote_or_value     → the specific text quoted from the guideline

    Patient record facts cite:
        source_id          → OpenEMR table:id (e.g. "prescriptions:573")
        field_or_chunk_id  → field name within the record
        quote_or_value     → the value cited

    Extracted document facts cite:
        source_id          → FHIR DocumentReference ID
        page_or_section    → page number (e.g. "2")
        field_or_chunk_id  → Docling block_id
        quote_or_value     → the extracted text from that block
        bbox               → real BBox from Docling layout engine
    """

    source_type: Literal["patient_record", "guideline", "extracted_document"]
    source_id: str = Field(
        description=(
            "OpenEMR table:id for patient_record; "
            "guideline corpus chunk_id for guideline; "
            "FHIR DocumentReference ID for extracted_document."
        )
    )
    page_or_section: str | None = Field(
        default=None,
        description="Page number or section identifier (guidelines / multi-page docs).",
    )
    field_or_chunk_id: str = Field(
        description=(
            "Field name for patient_record; "
            "stable chunk_id for guideline; "
            "Docling block_id for extracted_document."
        )
    )
    quote_or_value: str = Field(
        description="The specific text or value from the source that grounds this claim."
    )
    bbox: BBox | None = Field(
        default=None,
        description=(
            "Page-coordinate bounding box from Docling. Populated only for "
            "extracted_document citations. Never filled by an LLM."
        ),
    )


class CitationStrength(str, Enum):
    """Per ARCHITECTURE.md §3.4 / AUDIT.md D-1/D-2/D-3."""

    CODE_BACKED = "code_backed"   # LOINC/SNOMED/ICD-10/NDC etc.
    STRUCTURED = "structured"     # typed columns, no code
    FREE_TEXT = "free_text"       # narrative fields


class ClaimType(str, Enum):
    FACT = "fact"                 # "patient is on metformin 500mg BID"
    HISTORY = "history"           # "history of T2DM since 2018"
    LAB_VALUE = "lab_value"       # "A1c 7.2 on 2026-03-15"
    MEDICATION_CHANGE = "medication_change"
    DIAGNOSIS_CHANGE = "diagnosis_change"
    ABSENCE = "absence"           # "no recorded LDL"
    RULE_FLAG = "rule_flag"       # "warfarin + new NSAID — bleeding risk per X"


# LLM frequently produces shorter or pluralized aliases; map them onto
# the canonical enum values rather than failing the whole response. The
# verifier still treats them by their target type; we just don't
# fabricate strictness about a label the LLM consistently misnames.
_CLAIM_TYPE_ALIASES: dict[str, str] = {
    "diagnosis": "history",
    "diagnoses": "history",
    "problem": "history",
    "condition": "history",
    "medication": "fact",
    "medications": "fact",
    "med": "fact",
    "lab": "lab_value",
    "labs": "lab_value",
    "result": "lab_value",
    "allergy": "fact",
    "allergies": "fact",
    "encounter": "fact",
    "absent": "absence",
    "no_data": "absence",
    "rule": "rule_flag",
    "flag": "rule_flag",
}


class Claim(BaseModel):
    """A factual statement the LLM emits about THIS patient.

    Generated as structured JSON; the prose presented to the user is rendered
    FROM the claim list (per ARCHITECTURE.md §3.2). Each claim must point at
    one or more retrieved record IDs the verifier will match against.
    """

    text: str
    claim_type: ClaimType
    source_record_ids: list[str] = Field(default_factory=list)
    """Stable record IDs in `<table>:<id>` form, e.g. 'prescriptions:1234'."""

    # Filled in by the verifier post-generation:
    verified: bool | None = None
    verifier_note: str | None = None

    @field_validator("claim_type", mode="before")
    @classmethod
    def _normalize_claim_type(cls, v: object) -> object:
        # Accept canonical enum values as-is. Map common LLM shorthand
        # ("diagnosis" → "history", etc.) to the enum value. Anything we
        # don't recognize falls through to FACT — safer than refusing
        # the whole response on a label mismatch.
        if isinstance(v, ClaimType):
            return v
        if isinstance(v, str):
            normalized = v.strip().lower().replace("-", "_").replace(" ", "_")
            valid_values = {ct.value for ct in ClaimType}
            if normalized in valid_values:
                return normalized
            if normalized in _CLAIM_TYPE_ALIASES:
                return _CLAIM_TYPE_ALIASES[normalized]
            return ClaimType.FACT.value
        return v


# ---------------------------------------------------------------------------
# Retrieved data records (returned by tools, fed to the verifier)
# ---------------------------------------------------------------------------


class RetrievedRecord(BaseModel):
    """A row a tool returned. Citable by `f"{table}:{record_id}"`."""

    table: str                    # e.g. 'prescriptions', 'procedure_result', 'lists'
    record_id: str                # primary key as string
    citation_strength: CitationStrength
    fields: dict[str, Any] = Field(default_factory=dict)
    """The tool's structured payload for this record."""


# ---------------------------------------------------------------------------
# Tool calls
# ---------------------------------------------------------------------------


class ToolCallSummary(BaseModel):
    """Shape returned in audit logs / refusal payloads. Lightweight — does
    NOT include full record payloads (those live on RetrievedRecord)."""

    tool_name: str
    params: dict[str, Any]
    latency_ms: int
    success: bool
    record_count: int = 0
    error: str | None = None


# ---------------------------------------------------------------------------
# Wire format — request / response
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    """POST /chat body. Per prd.md §5: client-side conversation state, full
    history sent every turn, agent service is stateless."""

    user_id: int
    """Authenticated OpenEMR user (clinician) ID. Forwarded by the integration
    module after AclMain::aclCheckCore('patients', 'med')."""

    patient_id: int
    """Session-derived patient ID. Per AUDIT.md S-2 / §4.1: NEVER from request
    body downstream of the OpenEMR module — the module must derive from
    $_SESSION['pid'] before calling this service."""

    timestamp: int
    """Unix epoch seconds at the moment the OpenEMR module signed this
    request. Included in the HMAC payload so a captured request body
    cannot be replayed later — the agent rejects requests whose timestamp
    is more than 30 seconds off the agent's clock (in either direction;
    future-dated requests are also rejected as a clock-skew sanity check).

    Per ai-security-review production blocker #3 (2026-05-02 audit pass).
    The 30-second window is generous for clock-drift between PHP and
    Python over an internal Docker network; it could be tightened later."""

    hmac: str
    """HMAC-SHA256 of (user_id|patient_id|timestamp|<message-bodies-joined>)
    using OPENEMR_HMAC_SECRET. Defense-in-depth: agent verifies before any
    tool runs. The timestamp inside the signed payload is what makes this
    replay-resistant — see `timestamp` field above.

    Note: session_id is intentionally NOT in the HMAC payload. It's
    observability metadata only — tampering with it would corrupt Langfuse
    trace grouping but cannot bypass auth, leak PHI, or alter agent behavior."""

    messages: list[Message]
    """Full conversation history including the latest user turn."""

    session_id: str | None = None
    """Browser-generated UUID stable across all turns of one chat-panel
    session. Used as the Langfuse trace's `session_id` so multi-turn UC3
    conversations group together. Optional — when absent, traces stand alone."""


class AgentResponse(BaseModel):
    """Successful response — at least one assistant turn produced and verified."""

    status: Literal["ok"] = "ok"
    message: Message
    """The new assistant turn appended to the conversation."""

    claims: list[Claim] = Field(default_factory=list)
    """The structured claim list backing the assistant message. Already
    verified — failed claims have been atomically stripped (per §3.6)."""

    retrieved_records: list[RetrievedRecord] = Field(default_factory=list)
    """Records the tools returned during this turn. The next request can
    short-circuit some tool calls if context is preserved client-side."""

    tools_called: list[ToolCallSummary] = Field(default_factory=list)
    """For audit log + observability; not strictly needed by the UI."""

    request_id: str
    """Joins agent_log row with Langfuse trace."""

    trace_id: str | None = None
    """Langfuse trace id for this request. Round-trips to the browser so
    the UI can attach client-side metrics (e.g. visual-render latency)
    back onto the same trace via /score."""

    claims_stripped: int = 0
    """Count of claims the verifier removed via atomic strip before
    returning. Lets the UI surface "9 verified · 2 stripped" so the
    clinician can see the verifier is doing its job even on responses
    that pass overall (i.e. the strip didn't cross the 30% refusal
    threshold)."""

    citations: list[Citation] = Field(
        default_factory=list,
        description=(
            "Machine-readable citations derived from verified claims. "
            "source_type distinguishes patient_record vs guideline evidence. "
            "Guideline citations carry source_type='guideline' with chunk_id in source_id. "
            "Patient record citations carry source_type='patient_record' with table:id in source_id. "
            "W2_ARCHITECTURE.md §7.3."
        ),
    )

    escalated_to_sonnet: bool = Field(
        default=False,
        description=(
            "True when the responder node retried with Sonnet 4.6 after the "
            "Haiku first attempt was refused by the verifier. False for all "
            "/chat responses and /graph_chat responses that passed on the "
            "first Haiku attempt. W2 graph phase decision #15."
        ),
    )


class RefusalResponse(BaseModel):
    """Returned when the verifier rejects ≥30% of claims twice (per §3.6)."""

    status: Literal["refused"] = "refused"
    reason: str
    """Short, user-facing. NEVER includes raw exception messages."""

    searched: list[ToolCallSummary] = Field(default_factory=list)
    """What the agent attempted before refusing. Tells the PCP where to look
    in the chart manually."""

    request_id: str
    trace_id: str | None = None


# ---------------------------------------------------------------------------
# Score endpoint — browser-side metrics attach to existing trace
# ---------------------------------------------------------------------------


class ScoreRequest(BaseModel):
    """Browser → PHP → agent. Records a client-side metric (e.g. visual
    latency) onto an existing Langfuse trace.

    HMAC payload convention (must match the PHP module):
      f"{user_id}|{patient_id}|{trace_id}|{name}|{value}"
    """

    user_id: int
    patient_id: int
    hmac: str
    trace_id: str
    name: str
    """Metric name. Convention: snake_case (e.g. `visual_latency_ms`)."""
    value: float
    comment: str | None = None


class ScoreResponse(BaseModel):
    status: Literal["ok"] = "ok"


# ---------------------------------------------------------------------------
# Verifier verdict
# ---------------------------------------------------------------------------


class VerifierVerdict(str, Enum):
    PASS = "pass"
    PARTIAL_STRIP = "partial_strip"
    REFUSED = "refused"


class VerifierResult(BaseModel):
    """Internal — not on the wire. Output of `verifier.verify(...)`."""

    verdict: VerifierVerdict
    claims_passed: list[Claim]
    claims_failed: list[Claim]
    failure_rate: float
    retry_needed: bool
    """True iff failure_rate > 0.30 AND this was the first pass."""
