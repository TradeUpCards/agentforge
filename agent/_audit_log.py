"""PHI access audit-log writer for the AgentForge agent service.

Per AUDIT.md C-1 [CRITICAL]: OpenEMR's EventAuditLogger does NOT log
SELECT queries by default. For HIPAA §164.312(b) compliance against a
read-only AI agent, this module writes a row per /chat request to the
`agent_log` table (schema lives inline in .deploy/bootstrap.sh; design
rationale in ARCHITECTURE.md §4.2).

Design properties:

- **Fail-safe.** Audit-write failure NEVER breaks the request path. We
  catch+log and return; the response still ships to the user. This is
  the same posture observability code takes in agent/agent.py.
- **Least-privilege.** Writes use a dedicated `agent_audit_rw` DB user
  with INSERT-only on `openemr.agent_log`. Read access stays bound to
  `agent_ro` (which has no INSERT/UPDATE/DELETE anywhere). See
  ARCHITECTURE.md §4.1 + AUDIT.md C-3.
- **No audit-row mutation.** The agent service is INSERT-only by DB
  privilege. UPDATE/DELETE on agent_log is a DB-layer error, not a
  policy convention — closes ARCHITECTURE.md §4.3 application-enforced
  integrity gap as much as a single host can.
- **Graceful absence.** If audit credentials are not configured (e.g.,
  local dev before grants.sql is applied), writes become no-ops with a
  one-time stderr warning. Production deploys via .deploy/bootstrap.sh
  always provision the user, so missing credentials = misconfiguration
  worth noticing but not crashing on.

The audit row is a snapshot of the request lifecycle: request_id,
user/patient/use_case, prompt, tools_called, llm_calls, verifier
verdict + claim counts, final response, total latency, outcome,
refusal_reason. See `AuditRow` below for the typed structure.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Any

import pymysql

from .config import get_settings


log = logging.getLogger("agent.audit")

# Set on the first failed-credentials write so we don't spam stderr for
# every request when the audit user is intentionally unconfigured for
# local dev.
_warned_missing_credentials = False


@dataclass
class AuditRow:
    """Typed snapshot of a /chat request. Maps 1:1 to agent_log columns.

    All fields are optional / have sensible defaults so partial-flow
    refusals (HMAC fail before tools fire, etc.) can still write a
    meaningful row. The `outcome` field is the only string we promise
    will always be set: success / refused / error.
    """
    request_id: str
    user_id: int
    patient_id: int
    outcome: str  # 'success' | 'refused' | 'error'
    use_case: str | None = None
    prompt: str | None = None
    tools_called: list[dict[str, Any]] = field(default_factory=list)
    llm_calls: list[dict[str, Any]] = field(default_factory=list)
    verifier_verdict: str | None = None
    claims_passed: int = 0
    claims_failed: int = 0
    final_response: str | None = None
    total_latency_ms: int = 0
    refusal_reason: str | None = None


_INSERT_SQL = """
    INSERT INTO agent_log (
        request_id, user_id, patient_id, use_case, prompt,
        tools_called, llm_calls,
        verifier_verdict, claims_passed, claims_failed,
        final_response, total_latency_ms, outcome, refusal_reason
    )
    VALUES (
        %(request_id)s, %(user_id)s, %(patient_id)s, %(use_case)s, %(prompt)s,
        %(tools_called)s, %(llm_calls)s,
        %(verifier_verdict)s, %(claims_passed)s, %(claims_failed)s,
        %(final_response)s, %(total_latency_ms)s, %(outcome)s, %(refusal_reason)s
    )
"""


# Truncation guards mirror the schema's VARCHAR sizes. Defensive — the
# schema itself enforces these, but truncating here yields a cleaner
# error surface (no MariaDB warnings on overflow).
_PROMPT_MAX = 4000
_RESPONSE_MAX = 8000
_REASON_MAX = 1000


def _truncate(s: str | None, limit: int) -> str | None:
    if s is None:
        return None
    if len(s) <= limit:
        return s
    # Reserve 32 chars for a "(truncated, full in Langfuse)" marker so
    # operators reading the SQL row know to chase the trace.
    suffix = "...(truncated; see Langfuse trace)"
    return s[: limit - len(suffix)] + suffix


def write_audit_row(row: AuditRow) -> None:
    """Insert one row into agent_log. Fail-safe — never raises."""
    settings = get_settings()
    audit_user = (settings.db_audit_user or "").strip()
    audit_pass = (settings.db_audit_password or "").strip()
    if not audit_user or not audit_pass:
        global _warned_missing_credentials
        if not _warned_missing_credentials:
            log.warning(
                "agent_log audit writes are no-ops: AGENT_DB_AUDIT_USER / "
                "AGENT_DB_AUDIT_PASS not set. Apply the schema + grants "
                "from .deploy/bootstrap.sh (search 'agent_log') and "
                "configure agent/.env for HIPAA-compliant audit logging. "
                "See AUDIT.md C-1."
            )
            _warned_missing_credentials = True
        return

    payload = {
        "request_id": row.request_id,
        "user_id": row.user_id,
        "patient_id": row.patient_id,
        "use_case": row.use_case,
        "prompt": _truncate(row.prompt, _PROMPT_MAX),
        "tools_called": json.dumps(row.tools_called) if row.tools_called else None,
        "llm_calls": json.dumps(row.llm_calls) if row.llm_calls else None,
        "verifier_verdict": row.verifier_verdict,
        "claims_passed": row.claims_passed,
        "claims_failed": row.claims_failed,
        "final_response": _truncate(row.final_response, _RESPONSE_MAX),
        "total_latency_ms": row.total_latency_ms,
        "outcome": row.outcome,
        "refusal_reason": _truncate(row.refusal_reason, _REASON_MAX),
    }

    try:
        conn = pymysql.connect(
            host=settings.db_host,
            port=settings.db_port,
            user=audit_user,
            password=audit_pass,
            database=settings.db_name,
            connect_timeout=3,
            read_timeout=3,
            autocommit=True,
        )
    except Exception as exc:
        log.error(
            "agent_log audit-write connection failed",
            extra={"error_type": type(exc).__name__, "request_id": row.request_id},
        )
        return

    try:
        with conn.cursor() as cur:
            cur.execute(_INSERT_SQL, payload)
    except Exception as exc:
        log.error(
            "agent_log audit-write INSERT failed",
            extra={
                "error_type": type(exc).__name__,
                "request_id": row.request_id,
                "outcome": row.outcome,
            },
        )
    finally:
        try:
            conn.close()
        except Exception:
            pass


# Re-export the dataclass-as-dict helper so callers don't need to import
# `dataclasses` themselves.
def asdict_audit(row: AuditRow) -> dict[str, Any]:
    return asdict(row)
