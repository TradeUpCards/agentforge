# DECISIONS.md — Architectural Choices and Their Defenses

> **Related docs:** [`ARCHITECTURE.md`](./ARCHITECTURE.md) (the choices documented here are made concrete there) · [`AUDIT.md`](./AUDIT.md) (audit findings that drove several decisions) · [`USERS.md`](./USERS.md) (the use cases the choices serve) · [`COST_ANALYSIS.md`](./COST_ANALYSIS.md) (cost decisions, §6) · [`PERFORMANCE.md`](./PERFORMANCE.md) (perf-driven decisions) · [`RULE_CORPUS.md`](./RULE_CORPUS.md) (rule-selection decisions, §2)

**Audience:** the hospital CTO deciding whether to put this in front of their physicians, plus the project's grading reviewers and developers picking the work up cold.

**Purpose:** every meaningful architectural choice in AgentForge Clinical Co-Pilot, organized by the concern a CTO would raise — with our decision, the rationale, the tradeoff we accepted, and the source-of-truth document to drill into.

**How to read this:** each section opens with the question a reader would ask. The decision follows. References point to authoritative artifacts (`ARCHITECTURE.md` §X, `AUDIT.md` finding Y) — this document is the *summary*; the full reasoning lives in those.

**This is a living document.** Updated as decisions evolve. See the revision log at the bottom.

---

## 1. The defining constraint

> *"Why should I trust this thing in front of my physicians?"*

**Decision.** Every architectural choice is graded against one question: does it make the agent's claims more or less verifiable?

- This is the design lens, not a feature. The verifier, the auth model, the citation tiers, the bounded-retry behavior, the rule corpus, the cost model — all trace back to this single constraint.
- A confidently stated hallucination in a clinical setting can directly harm a patient. The gap between "a prototype that demos well" and "an agent a hospital can deploy" is the entire scope of the project.

**Tradeoff.** Some questions a less-constrained chatbot would answer, this agent will refuse — because the answer can't be verified. Refuse-vs-fabricate is an explicit choice. We accept user-experience cost (some sessions end with "I don't know") to eliminate safety risk.

**Source:** [ARCHITECTURE.md Executive Summary](./ARCHITECTURE.md), [Week 1 brief — "Why this matters"](./.gauntlet/week1/) (north star).

---

## 2. Patient safety — how we prevent hallucinations from harming patients

> *"What stops your agent from telling my doctor the patient is on a drug they're not on?"*

**Decision.** Verification runs as a separate, deterministic post-generation pass. Not as part of the LLM prompt. Not as another LLM critiquing the first.

- **Structured-first LLM emission.** The LLM emits a JSON list of `{claim, source_record_id, claim_type}` objects. The prose presented to the user is generated *from* that list. Citation-by-prompt was rejected because the LLM can fabricate citations that look real — strictly worse than no citations. ([ARCHITECTURE.md §3.2](./ARCHITECTURE.md))
- **Deterministic verifier.** A pure function `verify(claims, retrieved_records) -> verdict` that matches each claim's `source_record_id` against the actual records the tools returned. Strict on numerical values and dates; lenient on qualifiers. ([ARCHITECTURE.md §3.1, §3.4](./ARCHITECTURE.md))
- **Atomic strip.** Failed claims are removed from the response; passing claims are kept. One bad claim does not poison the rest. ([ARCHITECTURE.md §3.6](./ARCHITECTURE.md))
- **30% rule + bounded retry.** If more than 30% of claims fail, the agent regenerates *once* with a stricter prompt. If still failing, it refuses honestly. Predictable latency (under 6s) beats nondeterministic success in a 90-second clinical window. ([ARCHITECTURE.md §3.6](./ARCHITECTURE.md))
- **Citation strength tiers** — code-backed (LOINC/SNOMED/ICD-10) > structured (typed columns) > free-text (narrative `pnotes`). Free-text-sourced claims are weakest and flagged accordingly. The agent does not make standalone claims from free text alone. ([ARCHITECTURE.md §3.4](./ARCHITECTURE.md), [AUDIT.md D-1, D-2, D-3](./AUDIT.md))
- **Rule corpus boundary.** Clinical-significance claims (e.g. "this lab change is concerning") only fire when a cited rule matches. The agent will not invent clinical interpretation outside the rule corpus. Expanding the agent's clinical-claim surface requires expanding cited rules — content problem, not architecture. **Selection criteria + initial corpus of 7 rules documented in [`RULE_CORPUS.md`](./RULE_CORPUS.md)** (added 2026-05-01 in response to MVP grader feedback). ([ARCHITECTURE.md §3.5](./ARCHITECTURE.md))

**Limits we acknowledge.**

- **Omissions.** The verifier does NOT catch *omissions* — if the agent fails to mention the active diabetes diagnosis, no rule fires. Eval suite has partial mitigation via "did you surface X?" cases, but true omission detection is a hard problem deferred. ([ARCHITECTURE.md §3.9](./ARCHITECTURE.md))
- **Temporal coherence.** Observed during live testing on a Synthea patient (2026-04-30): the LLM produced *"Creatinine improved 2.65 mg/dL (08/19) → 0.92 mg/dL (08/16)"* — both values exist in cited records, both dates match cited record fields, **but the arrow is backwards in time** (08/19 came AFTER 08/16, so the values went up not down). The verifier passed it because each individual claim is verifiable in isolation; it doesn't check that delta narratives are temporally consistent. Strict v1 fix would require teaching the verifier to recognize delta-language ("improved", "rose to", "→") and validate the date direction. Week-2 candidate; flagged as a known verifier gap rather than papered over.
- **~~Token-level matching, not semantic pairing.~~ Closed 2026-04-30 (afternoon).** *Original gap (now fixed):* the verifier extracted individual digit groups from claim text (e.g. *"77.3 mL/min on 08/19/2025"* → tokens `77.3`, `08`, `19`, `2025`) and checked each one was present somewhere in the cited record's field blob. It did NOT check that value and date came from the **same** record, and it did NOT parse non-ISO date formats (`08/19/2025` was matched as three separate digit groups, not a date). *Fix:* `verifier.py` now (a) normalizes ISO / `MM/DD/YYYY` / `MM-DD-YYYY` to a canonical ISO form before matching, (b) extracts `(value, date)` pairs by proximity (within ~60 chars in the same sentence) from claim text and requires the pair to co-locate in a *single* cited record's fields. Cross-record splicing now fails verification with a `not co-located` note. Covered by 4 new unit tests in `test_verifier.py`. See appendix entry 2026-04-30 (afternoon).

**Tradeoff accepted.** Refuse beats fabricate. Some legitimate questions get refused. Our eval suite specifically tests this boundary so we know how often it fires.

---

## 3. Authorization — who can query which patient's data

> *"Can a nurse query a doctor's patient? Can someone bypass auth via a forged URL?"*

**Decision.** Authorization is explicit on every request and patient identity comes from the authenticated session, never from the request body.

- **`AclMain::aclCheckCore('patients', 'med')` is called explicitly before any tool runs.** Route-level authorization is not trusted. ([AUDIT.md S-1](./AUDIT.md), [ARCHITECTURE.md §4.1](./ARCHITECTURE.md))
- **Patient ID is session-derived.** It flows from `$_SESSION['pid']` on the OpenEMR side, is forwarded to the Python agent in the request body, and the Python agent uses *that* value — never any `patient_id` an attacker might inject elsewhere. Closes the IDOR class that recent OpenEMR advisories patched. ([AUDIT.md S-2](./AUDIT.md))
- **HMAC defense in depth.** The OpenEMR module computes `hash_hmac('sha256', ...)` and the Python agent verifies it before any tool runs. Defends against requests that bypass the OpenEMR module (e.g., another container on the Docker network). The agent service has no public network exposure either way. (`agent/agent.py:verify_hmac()`)
- **No `skip_acl_check` escape hatches.** OpenEMR's legacy code has them in places (`library/documents.php:84`); our code does not. ([AUDIT.md S-3](./AUDIT.md))
- **DB privileges are scoped.** The agent connects via a dedicated read-only MariaDB user (`agent_ro`) with SELECT-only privileges on the OpenEMR schema. There is no agent-superuser account.

**Tradeoff accepted.** The agent inherits OpenEMR's ACL correctness. If OpenEMR's ACL check returns the wrong answer for some edge case, the agent will too. Fixing OpenEMR's ACL implementation is out of scope; the system is in production at thousands of clinics, so we treat it as a trusted layer.

---

## 4. HIPAA, audit logging, and compliance

> *"Will I be in violation of HIPAA if I deploy this?"*

**Decision.** HIPAA-defensible for development with a clear, scoped path to HIPAA-deployable.

- **Per-request agent-side audit log.** A new `agent_log` table mirrors OpenEMR's `log` schema and records every agent request: `request_id`, `user_id`, `patient_id`, `use_case`, `prompt`, `tools_called` (JSON), `llm_calls` (JSON), `verifier_verdict`, `claims_passed/failed`, `final_response`, `total_latency_ms`, `outcome`. ([ARCHITECTURE.md §4.2](./ARCHITECTURE.md))
- **Why we built our own log.** OpenEMR's `EventAuditLogger` does not log SELECT queries by default (`audit_events_query` is opt-in). For a read-only AI agent that's a HIPAA §164.312(b) gap. ([AUDIT.md C-1](./AUDIT.md))
- **BAA framing.** Per the brief footnote, we treat both Anthropic (LLM) and Langfuse (observability) as covered services under a hypothetical signed BAA. Real production requires actual signed BAAs — flagged as a pre-production gate. ([ARCHITECTURE.md §4.4](./ARCHITECTURE.md))
- **What we explicitly deferred to week 3 and why:**
  - **External audit-log forwarding (ATNA syslog)** — log integrity is application-enforced today, not schema-enforced. Same is true for OpenEMR's existing log. Documented honestly rather than papered over. ([AUDIT.md C-2](./AUDIT.md))
  - **PHI redaction in observability traces** — Langfuse traces include patient context in dev mode for the demo; production hardens this with redaction at log-write time. **Detailed plan in [§4a](#4a-phi-redaction-implementation-plan) below.** ([ARCHITECTURE.md §7](./ARCHITECTURE.md))
  - **Anomalous-access alerting** — the structured `agent_log` makes this trivial to add later, but it's not in MVP. ([ARCHITECTURE.md §10 #4](./ARCHITECTURE.md))
  - **Langfuse HIPAA-compliant region** (`us-hipaa.cloud.langfuse.com`) under signed BAA on the Enterprise tier — current setup uses the standard `us.cloud.langfuse.com` free tier, which is NOT HIPAA-compliant. Per the brief's footnote (page 3) we operate under the *assumption* of a signed BAA across all third-party services for the bootcamp; real production migration is a config change (one URL + Enterprise account provisioning), not architectural. The same applies to Anthropic — production needs their enterprise BAA with PHI commitments.

**Three pre-production gates, in concrete terms.** A CTO walking up to deploy this should expect:

1. **Move Langfuse to the HIPAA region** under a signed BAA. URL changes from `https://us.cloud.langfuse.com` to `https://us-hipaa.cloud.langfuse.com` in `agent/.env`. Enterprise plan required.
2. **Sign a BAA with Anthropic** (their enterprise tier offers PHI commitments). API base URL likely unchanged; the contract is the change.
3. **Wire PHI redaction at log-write time** in the agent — strip patient identifiers from prompts/responses sent to Langfuse before they leave the trust boundary, even with the BAA. Defense in depth; minimizes blast radius if anything in the observability pipeline is later breached. **Detailed plan in [§4a](#4a-phi-redaction-implementation-plan) below.**

**Tradeoff accepted.** This is a v0.5 with a defined v1 path. A CTO would treat it as deployable for pilot/sandbox use today; full production deployment requires the week-3 hardening list to land.

---

### 4a. PHI redaction implementation plan

> *"Specifically — what gets redacted, where in the call path, and how do you prove it works?"*

This sub-section addresses MVP-feedback gap #2 (the original §4 flagged PHI redaction as week-3 work but didn't say *what* gets redacted, *where in the call path*, or *how* the redaction is verified). Expanded so the gate #3 above is concrete enough that an engineer could implement it from this doc alone.

> **Updated 2026-05-02.** First-cut implementation shipped using Langfuse's built-in `mask=` callback — runs at the SDK serialization boundary, only on the copy of inputs/outputs sent to Langfuse Cloud. Does NOT affect what the LLM, verifier, or end user sees. The mask currently performs **year-month bucketing on day-precision dates** in three formats (ISO `YYYY-MM-DD`, US slash `MM/DD/YYYY`, US dash `MM-DD-YYYY`), recursively walking dicts, lists, and Pydantic models. Code lives in `agent/agent.py:_mask_phi`; unit tests at `agent/tests/unit/test_mask.py` (12 cases). What's NOT yet redacted (still deferred to full week-3 implementation): names, DOBs, MRNs, phone numbers, addresses, and free-text date detection (e.g. "March 15, 2026"). Singleton-init wrinkle worth flagging: Langfuse v4's `LangfuseResourceManager` is per-public-key — if the SDK is constructed without `mask=` first, later constructions with the mask are silently ignored. The agent forces mask attachment on the singleton in `_langfuse()` to defend against init-order races.

> **Updated 2026-05-02 (Tier-2 outbound expansion).** Closes the high-confidence half of [`AUDIT.md C-6`](./AUDIT.md#c-6-high--outbound-phi-redaction-needed-for-response-narratives) — the cross-patient identifier leakage class surfaced by eval case 26. Implementation in `agent/_phi_scrubber.py` (~24 unit tests at `agent/tests/unit/test_phi_scrubber.py`):
>
> - **`find_outbound_violations(text, allowed_patient_id)`** — runs at the response-gate (just before `AgentResponse` construction in `agent.agent.run_chat`); refuses with `refusal_reason: "outbound_phi_leak"` if any of these high-confidence patterns appear:
>   - `patient_id=N` / `pid:N` / `pid=N` mentions where N ≠ request's `patient_id` (cross-patient ID leakage)
>   - SSN (`XXX-XX-XXXX`, valid forms only)
>   - US phone (dash, dot, paren-area-code variants)
>   - Email addresses (excluding `@example.com` / `@test.*` placeholders)
>   - MRN-prefixed identifiers (`MRN: 12345678`)
> - **`mask_observability_patterns(text)`** — extends `_mask_phi` so the same patterns are scrubbed from input/output payloads before Langfuse Cloud export (replaces with `<REDACTED-SSN>` / `<REDACTED-PHONE>` / `<REDACTED-EMAIL>` / `<REDACTED-MRN>` / normalizes `patient_id=N` to `pid:N`).
>
> **Why "refuse" not "redact" at the response boundary:** cross-patient leakage is a HIPAA breach class; partially-redacted responses can still ship implied content. Refusing with a clear reason is preferable. Clinician retries; if persistent, manual chart review.
>
> **Why name detection is deferred** (the eval case 26 lure also injected the name "Maria Hernandez", and the response repeated it — Tier-2 doesn't catch this):
>
> 1. **Plumbing:** detecting "is this name a different patient" requires an allowlist = the request patient's name. The agent doesn't currently know the request patient's name (only `patient_id`); fixing requires either a name-lookup tool call at run_chat start (~10 min) or schema change to pass it from the OpenEMR module (~30 min, larger blast radius). 
> 2. **False-positive risk:** capitalized First+Last regex matches doctor names in encounter notes ("seen by Dr. Smith"), some drug brand names, common medical terms. Allowlisting requires either retrieved-record provider extraction OR a curated medical-term allowlist — both are project-scope work.
> 3. **Refuse-on-name-match has high false-positive cost:** clinician fatigue. A scrubber that refuses 1 in 5 valid responses is worse than one that misses 1 in 50.
>
> **Realistic estimate** for full name + DOB scrubbing: ~3 hours including the patient-name lookup, allowlist population from retrieved records, FP suppression for honorific-prefixed names, and unit + eval tests. Not undertaken at week-1 final-submission time. Documented as week-3 hardening alongside the broader Safe Harbor sweep below.

#### The 18 HIPAA Safe Harbor identifiers (§164.514(b)(2))

Each tagged for our agent's actual I/O surface. The bar for "in-scope" is whether the identifier could plausibly appear in our prompts to the LLM, our responses back to the user, or our trace metadata sent to Langfuse.

| # | Identifier | Status | Notes |
|---|---|---|---|
| 1 | **Names** (patient, family, employer) | **IN-SCOPE** | `patient_data.fname/lname/mname` — present in chart context if the prompt includes patient context blocks. |
| 2 | **Geographic subdivisions** smaller than state (street, city, county, ZIP, precinct) | **IN-SCOPE** | Address fields in `patient_data` — currently NOT in our tool SELECTs, but could leak via free-text notes. |
| 3 | **Dates** related to individual (birth, admission, discharge, death; ages > 89) | **IN-SCOPE — partial redaction** | Clinical reasoning needs date precision (lab trends, med start dates, encounter sequence). Year-month-day stripping breaks the agent's utility. **Strategy: redact DOB; preserve clinical event dates.** Documented honest tension below. |
| 4 | **Telephone numbers** | IN-SCOPE | Patient phone fields. NOT in our current tool SELECTs but easy to leak via narrative SOAP notes. |
| 5 | **Vehicle identifiers** (license plates, VINs) | NOT-RELEVANT | Not in our schema; not in tool returns. |
| 6 | **Fax numbers** | NOT-RELEVANT | Same as #5. |
| 7 | **Device identifiers and serial numbers** | NOT-RELEVANT | Same as #5. |
| 8 | **Email addresses** | IN-SCOPE | Patient email field. NOT in our SELECTs; could leak via free-text. |
| 9 | **Web URLs** | NOT-RELEVANT | Not in our queried fields. |
| 10 | **Social Security numbers** | NOT-RELEVANT | Explicitly NOT in our tool SELECTs (per [AUDIT.md C-3](./AUDIT.md)) — narrowed away at the tool layer. |
| 11 | **IP addresses** | NOT-RELEVANT | Not in chart data. |
| 12 | **Medical record numbers** | **IN-SCOPE** | `patient_data.pid` and `pubpid` are foundational to our citation system (records are addressed by `<table>:<id>`). **Cannot be stripped without breaking the verifier.** Strategy: hash for stable trace-correlation, retain in audit log. |
| 13 | **Biometric identifiers** (fingerprints, voice prints) | NOT-RELEVANT | Not in our schema. |
| 14 | **Health plan beneficiary numbers** | NOT-RELEVANT | Billing-side; not in tool returns. |
| 15 | **Full-face photographs and comparable images** | NOT-RELEVANT | Binary blobs; not in our queried fields. |
| 16 | **Account numbers** | NOT-RELEVANT | Billing-side. |
| 17 | **Any other unique identifying number, characteristic, or code** | **IN-SCOPE — case-by-case** | Catch-all. Encounter IDs (`form_encounter.encounter`) are in this category — also load-bearing for citations; same hash-for-correlation approach as #12. |
| 18 | **Certificate / license numbers** | NOT-RELEVANT | Driver's license — explicitly excluded from our SELECTs. |

#### Per-category redaction strategy

| Category | Strategy | Why |
|---|---|---|
| **Names** | Substitute (`[NAME]`) | Stable substitution preserves prose readability; no need to correlate names across traces. |
| **DOB** | Year only (substitute `[DOB-YYYY]`) | Age-relevant clinical reasoning preserved (e.g., "65-year-old patient"); exact day stripped. |
| **Clinical event dates** | **PRESERVE** | Lab trends, med-start sequencing, encounter ordering require day precision. The agent's value collapses without this. **This is the honest HIPAA tension below.** |
| **Phone / email / address** | Strip (replace with `[REDACTED-PHONE]` / `[REDACTED-EMAIL]` / `[REDACTED-ADDR]`) | Never clinically necessary; cheap to strip. |
| **Medical record numbers** (pid, pubpid, encounter IDs) | Hash with a per-environment salt (`hashed:abc123…`) | Citations need stable IDs across a single trace; hashing preserves correlation without exposing the raw value. Salt rotates per deploy environment so dev hashes don't correlate to prod hashes. |
| **Catch-all unique identifiers** | Strip unless in a known-good list | Default closed: anything that looks like a UUID, GUID, or unrecognized numeric ID gets stripped. Allowlist: cited record IDs (handled by the hashing rule above). |

#### The honest tension on dates

> "Clinical event dates are PHI. We're preserving them. Why is that defensible?"

HIPAA Safe Harbor de-identification under §164.514(b)(2) requires stripping dates more granular than year. **For a research dataset that's the right call.** For an operational clinical tool sending traces to a BAA-covered observability provider, the calculus is different:

- Dates are essential to clinical reasoning (lab trend direction, med-start sequencing).
- The receiver (Langfuse under signed BAA on `us-hipaa.cloud.langfuse.com`) is a covered entity for our purposes — they're not a public dataset.
- The minimum-necessary rule (§164.502(b)) says "limit PHI to the minimum necessary to accomplish the intended purpose." For us, the intended purpose is debugging agent behavior on real-shape clinical data — that requires dates.

**Defensible position:** under signed BAA + HIPAA-region observability, preserving clinical event dates is permissible because (a) the recipient is not the public, (b) the use is the agent's own observability not external research, (c) stripping dates would break the operational purpose. Document this explicitly in the BAA as a covered use.

**This is a real call we're making, not a paper-over.** A reviewer who disagrees should know we considered it; here's the reasoning. If clinic policy is more conservative, the redaction layer below has a flag to fall back to year-only on event dates too — the architecture supports it.

#### Code seam — where redaction lands

**File:** `agent/agent.py`. **Function:** `redact_phi(payload: str | dict, policy: RedactionPolicy) -> str | dict`. **Call sites:** every `langfuse.get_client().update_current_span(input=...)` and `update_current_generation(input=..., output=...)` invocation.

Today the relevant calls are at `agent.py` lines 338, 351, 372, 389, 433, 459, 486 (per current code; line numbers may drift). The redaction layer wraps each one:

```python
# Before (current):
get_client().update_current_span(input={"prompt": prompt, "patient_context": ctx})

# After:
get_client().update_current_span(
    input=redact_phi({"prompt": prompt, "patient_context": ctx}, _redaction_policy)
)
```

`_format_patient_context` (line 131) is the upstream point where raw record fields enter the prompt — that's the right place to add a parallel `_format_patient_context_redacted` for the trace path, leaving the LLM-input path unchanged. **The LLM still receives unredacted context** (it needs to reason on real data); only the *trace copy* sent to Langfuse is redacted.

This split matters: redaction is a observability concern, not an agent-behavior concern. The verifier still operates on raw data; the audit log still has the real values; only the third-party-trace destination gets the scrubbed version.

#### Test plan

Two test layers:

**Unit (`agent/tests/unit/test_phi_redaction.py`):**

```python
@pytest.mark.parametrize("identifier_type, raw_value, expected_pattern", [
    ("name", "John Smith", r"\[NAME\]"),
    ("dob", "1962-04-15", r"\[DOB-1962\]"),
    ("phone", "(555) 123-4567", r"\[REDACTED-PHONE\]"),
    ("email", "patient@example.com", r"\[REDACTED-EMAIL\]"),
    ("ssn", "123-45-6789", r"\[REDACTED-ID\]"),
    ("mrn", "12345", r"hashed:[a-f0-9]{8,}"),
    # ...18 cases total, one per identifier from the table above
])
def test_redact_phi_strips_identifier(identifier_type, raw_value, expected_pattern):
    out = redact_phi(raw_value, _default_policy())
    assert re.search(expected_pattern, out)
    assert raw_value not in out  # the literal MUST NOT survive
```

Plus a structural test that `redact_phi` is idempotent (running twice = same as once) and a test that asserts NO value passes through unchanged for IN-SCOPE categories.

**Integration (`agent/tests/integration/test_phi_redaction_in_traces.py`):**

```python
def test_langfuse_trace_does_not_contain_seed_phi(langfuse_test_client):
    # Seed the request with marker PHI a real prompt wouldn't contain
    request = make_chat_request(
        patient_id=999998,  # the prompt-injection sentinel; we add seeds via patient_data
        seeded_phi={
            "name": "PHI_TEST_NAME_xY9z",
            "phone": "PHI_TEST_PHONE_aB3c",
        },
    )
    run_chat(request)

    captured = langfuse_test_client.flush_and_capture()
    assert "PHI_TEST_NAME_xY9z" not in str(captured)
    assert "PHI_TEST_PHONE_aB3c" not in str(captured)
```

Marker strings are intentionally improbable; if any survive into the trace, the assertion fires. Run both test suites in CI as part of the pre-commit hook.

#### What we're NOT solving here

- **Free-text leakage in narrative SOAP notes.** A clinician writing "patient John Smith called from 555-1234" in an `assessment` field will leak via the agent's tool returns regardless of our redaction layer. **That's a chart-content problem, not an agent problem** — it pre-existed the agent and won't be fixed by us. Production deployments should pair this redaction with chart-side de-id tooling (e.g., regex sweep + spaCy NER pass on free-text fields at write time).
- **The OpenEMR-side audit log (`agent_log` table).** We chose to retain raw values there per §4 (it's the legal-defensibility audit surface; reducing detail weakens HIPAA §164.312(b) defense). Redaction applies only to the *third-party observability* destination.
- **Anthropic prompt content.** We rely on the Anthropic BAA + their own data-handling commitments. If a clinic's policy doesn't allow LLM-side PHI exposure even under BAA, the answer is self-hosting (Bedrock with private VPC, or a self-hosted open model) — not our redaction layer.

#### Status

**As of 2026-05-01: planned, not implemented.** Implementation is week-3 hardening per the [pre-production gates list](#3-pre-production-gates) above. This sub-section makes the plan concrete enough that an engineer could implement and ship it without further design work. Scoped to ~3-4 hours for the redaction function + test layers; scoped to ~1 hour for wiring it into the existing `update_current_span` call sites.

**Implementation will land alongside the BAA-region Langfuse migration** (gate #1) — both are pre-production gates and both touch the trace pipeline. Do them as one commit to keep the trust-boundary change atomic.

---

## 5. Failure modes — what happens when things break

> *"It's 11pm and a tool call fails. What does the doctor see?"*

**Decision.** Useful failure over silent success. Bounded behavior under load. Never partial silent data.

| Failure | Behavior |
|---|---|
| Tool DB error | Tool returns `{error, retryable}`; agent surfaces failure rather than fabricating |
| Empty data | First-class result, not an error → enables honest absence claims ("no recorded LDL") |
| Verifier rejects ≥30% of claims | One bounded retry; if still ≥30%, refuse with a `searched` list of what was attempted |
| Anthropic rate limit / outage | Exponential backoff (max 3 attempts, 30s ceiling); on exhaustion, service-degraded response |
| Anthropic timeout (>15s) | Cancel; return labeled timeout |
| Auth failure | Tool returns `{error: "access denied"}`; agent stops; no partial info leaked |
| Prompt injection in note text | Records wrapped in `<patient_record>` tags with explicit "data not instructions" framing; verifier is the safety net (injected instructions can't produce citation-matched output); adversarial eval cases included |
| Malformed LLM output | Treated as 100% failure → triggers retry path; two consecutive → refuse |
| Ambiguous user query | Agent asks one clarifying question rather than guessing |

**Source:** [ARCHITECTURE.md §6](./ARCHITECTURE.md).

**Refusals carry information.** A refusal payload includes a `searched` list — *"we looked at problem list, current meds, and recent labs but couldn't verify any clinical-significance claims"* — so the PCP knows where to look manually rather than thinking the agent is silently broken.

**Tradeoff accepted.** Some sessions end with "I don't know." Predictable bounded refusal at 6 seconds beats a nondeterministic answer at 18 seconds in a 90-second window.

---

## 6. Cost economics — dev burn and the path to scale

> *"What's the per-physician monthly cost at 1K, 10K, 100K users?"*

**Decision.** Cost is an architectural input, not a postscript. Several decisions in the codebase exist primarily because the alternatives produced unacceptable cost economics.

- **Multi-model tiering.** Sonnet 4.6 for reasoning (~30% of calls); Haiku 4.5 for routing, claim extraction, and free-text summarization (~70% of calls, ~3× cheaper, adequate quality for those tasks). Blended cost is roughly half what Sonnet-only would be. ([ARCHITECTURE.md §2.3](./ARCHITECTURE.md))
- **Prompt caching.** Anthropic's explicit cache breakpoints save ~90% on cached input. Our system prompt + per-patient context (constant across UC3 turns) is heavily cache-friendly. Effective input cost drops ~80% on multi-turn conversations. ([ARCHITECTURE.md §2.4](./ARCHITECTURE.md))
- **Anthropic over OpenAI** specifically for caching: Anthropic's explicit breakpoints save ~90%; OpenAI's automatic prefix caching saves ~50%. For our workload that's a meaningful net cost difference. ([ARCHITECTURE.md §2.3](./ARCHITECTURE.md))
- **Sonnet 4.6 over Opus 4.7.** Opus is ~5× the cost; reasoning quality gap doesn't justify the multiplier for any use case here. ([ARCHITECTURE.md §2.5](./ARCHITECTURE.md))
- **Single bounded retry, not N retries.** Retry has two failure causes — bad phrasing (one retry covers it) and unsupportable claims (NOT fixable). After two failures the second cause dominates; retrying further wastes tokens on cases that can't succeed. ([ARCHITECTURE.md §3.6](./ARCHITECTURE.md))

> **Updated 2026-05-01.** Two refinements after explicit caching shipped and was measured:
>
> - The "~80% drop on multi-turn conversations" claim holds *within a single UC3 conversation on Sonnet only*. Blended across the full call mix (UC1 + UC2 first-call + UC3 follow-ups), the realized savings vs no-cache is ~3-4% — UC1/UC2 single-turn dominates and pays the 25% cache CREATION premium. The Anthropic-vs-OpenAI 90%/50% comparison is per-cached-portion, not blended; Anthropic still wins on net (Citations API + multi-UC composition story) but the per-cached-portion delta isn't the dominant lever.
> - The strategic value of the cache architecture isn't within-encounter savings — it's that adding new UCs (UC4 suggested orders, UC5 differential dx, etc.) on the same patient runs ~50% cheaper than the first UC because the prefix is already cached. See [COST_ANALYSIS.md §3.2](./COST_ANALYSIS.md#32-multi-uc-composition-the-cached-prefix-multiplier) for the marginal-cost table and [§11](./COST_ANALYSIS.md#11-roadmap-implications--sub-linear-cost-growth-as-ucs-expand) for the roadmap implications.

**Concrete projections** (week 1 dev burn, in dollars):
- With multi-model + caching: **$15–40**
- Sonnet-only, no caching: ~$60–150 — roughly 4× higher

> **Updated 2026-05-01.** Actual measured agent runtime burn was **$1.64** of LLM spend (OpenRouter, ~36 hours of dev). The $15–40 estimate was an order-of-magnitude high — actual dev requests were Haiku-heavy with shallow Maria-fixture contexts. Caching shipped end-of-week-1, so most dev burn was no-cache; the multi-model tiering is what saved ~$2 vs Sonnet-only. See [COST_ANALYSIS.md §1](./COST_ANALYSIS.md#1-actual-week-1-dev-burn) for the measured breakdown.

**Scaling phases** ([ARCHITECTURE.md §8.4](./ARCHITECTURE.md)):
| Phase | Concurrent users | Architectural changes |
|---|---|---|
| 1 — Single VPS | 5–50 | Vertical scaling |
| 2 — Compute / data split | 50–150 | Managed DB, agent + OpenEMR on separate hosts behind LB, Redis for shared state |
| 3 — Horizontal at scale | 300+ | Multiple OpenEMR instances behind LB, agent horizontally scaled (ECS / Cloud Run / GKE), read replicas, patient-context cache, self-hosted Langfuse |

**Honest framing for the CTO.** At 300 concurrent users, the bottleneck is the LLM provider, not the host count. Anthropic API rate limits, token economics, and prompt-cache hit rate are larger levers than any infrastructure decision. Multi-model tiering and caching are already in place — they get *more* valuable at scale.

A full cost-analysis document at 100 / 1K / 10K / 100K users with per-tier architectural transitions is a separate deliverable (`COST_ANALYSIS.md` — final-submission scope per the brief).

> **Updated 2026-05-01.** [COST_ANALYSIS.md](./COST_ANALYSIS.md) is now in repo. Per-PCP/mo lands $9.74–$10.83 across all four tiers (100→100K PCPs); LLM is ~85-91% of total cost. Three sections worth knowing: [§3.1 measurement-driven kill switch](./COST_ANALYSIS.md#31-measurement-driven-kill-switch-pilot-decision-rule) (when to disable explicit caching based on pilot cache-hit-ratio telemetry), [§3.2 multi-UC composition](./COST_ANALYSIS.md#32-multi-uc-composition-the-cached-prefix-multiplier) (sub-linear cost growth as UCs are added), and [§11 roadmap implications](./COST_ANALYSIS.md#11-roadmap-implications--sub-linear-cost-growth-as-ucs-expand) (back-of-envelope cost rules for sizing future UC pitches).

**Tradeoff accepted.** We are bound to Anthropic's pricing curve. Mitigation in §7 below.

---

## 7. Vendor and sustainability — lock-in concerns

> *"What if Anthropic changes terms or shuts down?"*

**Decision.** Direct Anthropic SDK with native tool use, no LangChain — but every load-bearing module is provider-agnostic enough to swap.

- **Why direct SDK over LangChain.** As a first-time agent build, the orchestration layer needs to be small enough to fully understand and debug. The brief grades verification, observability, eval, audit — not framework choice. Anthropic's SDK supports tool use natively in ~50 lines for a basic agent loop. ([ARCHITECTURE.md §2.1](./ARCHITECTURE.md))
- **Migration insurance built in:**
  - Tools are plain Python functions with structured I/O, callable from unit tests without an LLM.
  - Verifier is a pure function `(claims, retrieved_records) → verdict` — no provider knowledge.
  - Conversation state is a plain dataclass, no SDK-specific types in our schemas.
  - LLM client is behind an interface (`LLMClient` protocol; today: `AnthropicLLMClient` + `FixtureLLMClient` share the same shape; tomorrow you could add `OpenAILLMClient` or `BedrockLLMClient` with one new file).
- **If Anthropic disappears or changes terms tomorrow:** swapping providers is one module of work (`agent/llm_client.py`), not a rewrite. Tools, verifier, audit log, observability wiring all keep working unchanged.
- **Why we picked Anthropic specifically:**
  - Citations API (native pointer-to-source) maps onto our verification architecture.
  - Prompt caching savings are larger than OpenAI's (90% vs ~50%).
  - Existing developer ecosystem fit (Claude Max + Cursor) — one billing relationship.
  - ([ARCHITECTURE.md §2.3](./ARCHITECTURE.md))

**Tradeoff acknowledged.** We are betting on a single provider for week 1. Multi-provider redundancy was considered and rejected (two SDKs, two billing relationships, more failure modes). It is *available as an extension* via the `LLMClient` interface — not free, but cheap to add.

---

## 8. Operational visibility — how we'll know it's working in production

> *"How will I know if the agent is working — or quietly degrading — once it's live?"*

**Decision.** Observability is wired in from the first request, not added later. Custom metrics make verifier behavior measurable, not just present.

- **Langfuse cloud free tier (50K observations/month; we use ~5–10K).** Wired through the Anthropic SDK integration. ([ARCHITECTURE.md §5.1](./ARCHITECTURE.md))
- **Required metrics (per the brief):**
  - What the agent did on a request, in order
  - Per-step latency
  - Tool failures and their reasons
  - Token consumption and cost
- **Custom metrics that operationalize the verifier — not just present-but-unmeasured:**
  - **Verifier verdict per response** (pass / partial-strip / refused) — direct signal of how often the safety net fires
  - **Citation match rate** (claims passed / total claims) — alarm if trends below 90% (verifier becoming too aggressive, or LLM emitting unverifiable content more often)
  - **Prompt-cache hit rate** — confirms the cost model holds in production; if it drops, prompts are drifting and per-request cost is rising
- **Eval suite** (5–8 cases at early submission; ~12 at final) covers happy paths plus failure cases a happy-path demo would not reveal: auth boundary, verifier strip, refusal, missing data, prompt injection in note text, IDOR attempt in chat free-text, ambiguous query handling. Each new commit runs the suite; PRs blocked if pass-rate drops >5%. ([ARCHITECTURE.md §5.2](./ARCHITECTURE.md))
- **Alerting roadmap.** Anomalous-access alerting is week-3 work. Langfuse can chart custom-metric trends today; cresting thresholds become alerts when production hardening lands.

**Tradeoff accepted.** Today's Langfuse traces include PHI in dev mode for the demo. PHI redaction at log-write time is week-3 hardening. ([ARCHITECTURE.md §7](./ARCHITECTURE.md))

---

## 9. Integration risk — did we make OpenEMR worse

> *"Will installing your module break my existing OpenEMR install?"*

**Decision.** Custom-module pattern with strict separation: modern hooks only, no legacy patches, additive schema, internal-network agent.

- **Module location.** `interface/modules/custom_modules/oe-module-clinical-copilot/` — fully self-contained. Same install posture as any third-party OpenEMR module. Installs and uninstalls via Module Manager UI.
- **Modern hooks only.** Subscribes to `PatientMenuEvent::MENU_UPDATE` (Symfony EventDispatcher); calls `AclMain::aclCheckCore` (modern PSR-4 service); reads via FHIR R4 (planned for week 2; v1 uses direct DB on a read-only user). Never patches `library/` or legacy `interface/` code where 8.0.0.2/8.0.0.3 patched recent CVEs. ([AUDIT.md S-1, S-2, S-3 + Executive Summary](./AUDIT.md))
- **Additive schema.** The new `agent_log` table is in its own DDL; does not modify any existing OpenEMR table. A clean uninstall path is `DROP TABLE agent_log` (week 2 work; week 1 documents this).
- **Network isolation.** The Python agent service runs on the internal Docker network only — not exposed publicly via Caddy. The OpenEMR container reaches it at `agent:8000`; nothing else can. ([prd.md §5](./.gauntlet/week1/prd.md))
- **REST endpoint scoping.** Lives under the module's `public/` directory, not under `/apis/` (which is OpenEMR's OAuth-protected dispatcher reserved for third-party API clients). Keeps the existing API contract uncluttered. CSRF token verification on every state-changing request, matching OpenEMR's existing pattern.
- **Agent never auto-acts.** Read-only by design. No order entry, no prescription writing, no chart edits. Architectural rule, not preference. ([USERS.md "What This Persona Does NOT Need"](./USERS.md))

**Tradeoff accepted.** Docker Compose deployment is not Kubernetes-native; managed-services migration is week 3+. ([ARCHITECTURE.md §8.4](./ARCHITECTURE.md))

---

## 10. What's NOT in scope and why

> *"What aren't you doing that I might want — and have you thought about it carefully?"*

A defined "no" is part of a defensible product. Each of these was explicitly considered and excluded.

- **UC3 voice / ambient capture** — different category of product; not an agent in the brief's sense.
- **Note drafting / order entry / prescription writing** — agent never auto-acts. Architectural rule. Adds large HIPAA / liability surface for week 1.
- **Care-gap sweeps across the panel** ("which of my patients are overdue for X?") — different mode of interaction (list, not conversation). Possible week-3 add. ([USERS.md "What This Persona Does NOT Need"](./USERS.md))
- **Inbox triage / message management** — workflow tool, not agent. The brief grades agent-shape capabilities.
- **25-rule corpus** — 5–8 cited rules enough for v1; expansion is a content problem, not architectural. Week 2.
- **PHI redaction in observability** — week 3 hardening per [ARCHITECTURE.md §7](./ARCHITECTURE.md).
- **External audit-log forwarding (ATNA syslog)** — week 3 per [AUDIT.md C-2](./AUDIT.md).
- **Anomalous-access alerting** — week 3.
- **Real BAAs with Anthropic and Langfuse** — pre-production gate. Architecture treats them as covered services per the brief footnote. [ARCHITECTURE.md §4.4](./ARCHITECTURE.md).
- **OAuth2 / SMART-on-FHIR auth between OpenEMR and the Python agent** — week 2. Week 1 uses a session-token-forwarding shim plus internal-network isolation.
- **Multi-provider LLM redundancy** — viable migration path via the `LLMClient` interface; not v1.
- **LLM-as-judge eval** — manual + deterministic asserts for v1; LLM-as-judge is week 2 stretch.
- **Custom OpenEMR-image bake** — for week 1, mount the module dir from the host. Bake into a custom image week 2+.
- **Calendar-driven Co-Pilot triggering.** A "Brief" button next to each appointment on the schedule view, so the PCP can summon a pre-visit brief in one click directly from their day's schedule (no chart-load roundtrip). The current trigger is the patient menu / floating launcher inside an already-opened chart. Calendar integration is the more workflow-native entry point — week-2 candidate; see appendix entry 2026-04-30 for the workflow insight.
- **Pre-visit brief pre-warming.** Anticipating that the next N appointments on today's schedule will all need briefs, the agent could pre-fetch + cache them in the background, so when the PCP clicks, the brief is already there. Reduces perceived latency to zero; verifier still runs at click time on the cached output. Observability would surface pre-warm hit rate. Real architectural value at scale; not in scope for week 1.

**Why this matters.** The surface area we are defending is bounded. A CTO walks away knowing the line we drew, what crosses it, when it would cross it, and that we considered each "no" rather than overlooking it.

---

## Appendix — Decisions made *during* the build (revision log)

Decisions that emerged from execution and are NOT in `ARCHITECTURE.md` (because they post-date the case-study defense). Documented here so they don't get lost.

### 2026-04-29 — Chat-first interface, not three separate menu items

**Original plan (PRD v1):** UC1 and UC2 as separate menu items each loading their own panel.

**Revised plan (PRD v2 after re-reading the brief):** one chat panel, two starter buttons inject canned first-user-messages ("Generate a pre-visit brief…" / "Show what's changed…"), free-text input handles UC3. All flow through a single `POST /chat` endpoint.

**Why.** The brief's Agent Requirements §1 mandates a *multi-turn AI agent that can receive follow-up questions*. UC1 and UC2 alone are button-click structured outputs — neither is multi-turn. Without UC3 in scope, the brief's central requirement is unmet. Chat-first reorganization satisfies the requirement with less code (one panel, one endpoint) than the original three-surface approach.

### 2026-04-29 — REST endpoint moved off `/apis/`

**During Phase 4.** Sub-agent building the OpenEMR PHP module flagged that `/apis/` is OpenEMR's OAuth/JWT-protected dispatcher (`apis/dispatch.php`), not session-aware. Wiring our session-authenticated handler through that dispatcher would either bypass OAuth (security regression) or require a JWT flow on the panel side (out of scope).

**Decision.** Endpoint lives at `{webroot}/interface/modules/custom_modules/oe-module-clinical-copilot/public/chat.php` instead. The chat panel reads the URL from an injected config object so changing it later is one line.

**Why this is the right call.** Keeps the existing OAuth API contract uncluttered; aligns the endpoint's auth surface with how it's actually called (session); avoids fighting the dispatcher's design.

### 2026-04-29 — Direct DB access for week 1; FHIR auth deferred to week 2

**Original architecture (§2.6):** FHIR is the primary read path; direct DB is a fallback for fields FHIR doesn't expose.

**Revised v1 scope:** all reads go through direct DB (read-only `agent_ro` user) for week 1. FHIR OAuth setup (registering a SMART-on-FHIR app, configuring redirect URIs, token exchange) is more complex than the time budget allows.

**Why this is acceptable.** Architecture §2.6 already permits direct DB as a fallback; we extend that to all reads for week 1. Re-do via FHIR in week 2 — explicit deviation, called out in the demo video. Auth is still enforced upstream by the OpenEMR module (`AclMain::aclCheckCore` runs before the agent does anything); the read-only DB user only has SELECT, so the blast radius if compromised is data exfil, not data corruption.

### 2026-04-29 — Verifier bug found and fixed in unit test

The verifier's regex was matching `01` from inside `2026-01-01` as a numeric token before reaching the date check, causing the verifier to fail with the wrong reason (still correctly stripping the claim, but reporting "numeric token mismatch" instead of "date mismatch"). Found by `test_date_mismatch_strips_claim`; fixed by checking dates first and stripping matched dates from the text before numeric scanning.

**Why this matters for the CTO defense.** This is exactly the failure mode the brief asks about: *"What does your eval suite test that a happy-path demo would not reveal?"* The test caught a real issue. The fix made the verifier more correct. Eval-driven development is doing what it's supposed to do.

### 2026-04-29 (evening) — JS endpoint URL: `js_escape`, not `js_url`

**Symptom.** Browser POST to `chat.php` returned 404 with a malformed URL containing percent-encoded slashes (`/` → `%2F`), producing a duplicated relative path.

**Cause.** OpenEMR's `js_url()` percent-encodes URL-component characters. Used on a full path, it encodes the separators, producing a broken value when consumed by `fetch()`.

**Decision.** Use `js_escape()` for embedding a full URL into a JS string literal. `js_url()` is for URL components (query-param values), not full paths.

**Why this matters for the CTO defense.** This was a sub-agent's first-pass bug. PHP unit tests don't exist on this surface; Python integration tests don't see the JS layer. The bug surfaced on first browser smoke test. **Integration tests at the smoke-test layer catch a class of bug that unit tests cannot.** Eval-suite expansion in week 2 will include browser-driven adversarial cases as a result.

### 2026-04-29 (evening) — `chat.php` bootstrap matches `library/ajax/*` pattern

**Symptom.** `400 "Site ID is missing from session data!"` (HTML, not JSON, so the JS rendered "Something went wrong"). An initial workaround forcing `$_GET['site'] = 'default'` got past the site check but produced a different failure (`401 Authentication required`).

**Cause.** `$sessionAllowWrite = true` + `$_GET['site']` override triggered a different session-init path in `interface/globals.php` that did not restore `authUserID`. Working in-app AJAX endpoints (e.g. `library/ajax/dated_reminders_counter.php`) just `require_once globals.php` with no special toggles.

**Decision.** Drop both `$sessionAllowWrite` (the controller doesn't write session state) and the `$_GET['site']` override (the existing session already knows its site). Match the working AJAX pattern.

**Why this matters.** Defensive coding can mask root-cause issues rather than solving them. Removing the workaround surfaced the actual root cause (next entry).

### 2026-04-30 — Pull forward real-DB tools + Synthea-populated demo data into week 1

**Original scope.** PRD §5 v1 data-path decision was *"direct DB access from the Python agent via a dedicated read-only MariaDB user. ARCHITECTURE.md §2.6 permits direct DB as a fallback; we extend that to all reads for week 1."* Implementation deferred — `agent/tools.py:_real_dispatch()` raises `NotImplementedError`, and the agent runs in `USE_FIXTURE_DATA=true` mode against a single hand-crafted Maria Hernandez fixture.

**Revised scope.** Pulling the real-DB-tool implementation forward into week 1, plus seeding the demo with realistic clinical data. Two reasons:

1. **The single-patient fixture demo doesn't exercise the architecture's claims.** The verifier's strict-on-numerics, lenient-on-qualifiers behavior is most defensible when shown across multiple patients with real lab trends. The "what's changed since last visit" delta only meaningfully exists with multi-encounter histories. Fixtures dodge both.
2. **A populated calendar reframes the demo around the real workflow.** The CTO defense narrative is *"PCP between rooms looks at schedule, picks next patient, summons Co-Pilot, walks in."* That story needs (a) appointments on today's schedule and (b) patients with real chart depth so the brief has something to summarize.

**What changes:**

- **`agent/tools.py:_real_dispatch()`** gets real PyMySQL queries for the 5 baseline tools (`get_problem_list`, `get_active_medications`, `get_recent_labs`, `get_allergies`, `get_recent_encounters`) against OpenEMR's actual schema (`lists`, `prescriptions`, `procedure_result`, `form_encounter`).
- **A dedicated read-only DB user** (`agent_ro`) is created on the local MariaDB with SELECT-only privileges. Per ARCHITECTURE.md §4.1: *"the agent runs queries with the authenticated user's effective DB privileges — there is no agent-superuser account."* `agent_ro` is the agent-side equivalent.
- **Synthea-generated patients** imported via OpenEMR's `import-random-patients` devtool. ~20 patients with realistic multi-year clinical histories — multiple encounters, lab trends, medication changes, real allergies.
- **Today's appointments** seeded for ~5 of the imported patients via SQL (`openemr_postcalendar_events`) so the calendar view tells the right "PCP's day" story.
- **Eval suite** runs in `USE_FIXTURE_DATA=true` for determinism (existing cases reference fixture record IDs); production runs in `USE_FIXTURE_DATA=false`. Mode toggle preserved as the test-vs-prod boundary.

**Trade-off accepted.** ~3–4 hours of additional work today against an already-tight Thu 22:59 early-submission target. Worth it because the demo video story changes meaningfully — instead of *"here's the fixture patient I crafted"*, it's *"here's a Synthea-generated patient with a 5-year history, summarized in 6 seconds with verifiable citations to actual record IDs."*

**Why this matters for the CTO defense.** This is the difference between a prototype and something a hospital CTO could imagine putting in front of physicians. The architecture's verifier story holds up against real clinical data variability (free-text problem entries, LOINC-coded labs vs uncoded labs, missing fields, multi-encounter trends) — not just against a single curated test case. It also lets us defend cost economics at a more honest scale (the prompt size grows with real chart depth; the cost projections in `COST_ANALYSIS.md` will reflect that).

**Provenance note.** This decision came directly from a workflow observation made during testing — the schedule-as-entry-point insight (entry above) led to *"we need a populated schedule"* led to *"and that means real patients with real depth."* Both entries should be read together; one prompted the other.

### 2026-04-30 — Workflow insight: the schedule, not the search box, is the natural entry point for a PCP

**Observation.** During local testing, the user noted: opening a patient chart in OpenEMR requires either a name search or a click-through navigation. Search-as-primary doesn't match a PCP's actual day — patients are pre-booked on a schedule; search is the edge case (cancellation, walk-in, urgent recall), not the common case.

**Implication for the architecture.** The 90-second clinical window referenced in [USERS.md](./USERS.md) doesn't start at *"I clicked a patient name from search"*; it starts at *"I closed the previous chart and looked at my schedule for who's next."* The agent's value compounds when its trigger is co-located with the schedule.

**Decision (week 1).** Stay with the patient-menu + floating launcher trigger. The architecture's `PatientMenuEvent::MENU_UPDATE` integration point is already in place; the sidecar drawer summons from any chart view; this is sufficient for the case-study demo.

**Roadmap (week 2+).** Two concrete extensions worth flagging:

1. **Calendar-integrated Co-Pilot trigger.** Subscribe to OpenEMR's calendar-render event (or render hook); add a "Brief" button next to each appointment block. Click → drawer opens pre-targeted at that patient. One step from "see schedule" to "have brief in hand."
2. **Pre-visit brief pre-warming.** For the next N appointments on the day's schedule, pre-fetch the brief on a background thread and cache it. PCP clicks → brief is already there (zero perceived latency). Verifier still runs at click time; observability surfaces pre-warm hit rate. Real architectural value at scale.

**Why this matters for the CTO defense.** A CTO doesn't grade architectural completeness in isolation — they grade *whether the architecture maps onto how the work actually gets done*. Acknowledging that a search-driven flow is wrong for clinical practice (and naming what we'd build instead) is exactly the kind of insight that distinguishes "implemented the spec" from "designed for the user."

### 2026-04-30 — Absence claims pass verification when no records were retrieved (§3.7 implementation gap closed)

**Symptom.** During the live-LLM swap, manual click-through testing showed: when all baseline tools returned zero records, the LLM correctly emitted *"No patient records are available in the system for this patient."* with `claim_type: "absence"` and empty `source_record_ids`. The verifier stripped that claim because it had no source IDs, hit 100% failure rate, and returned the generic *"More than 30% of the agent's claims could not be verified..."* refusal. The user saw a misleading error instead of the correct "no data found" message.

**Cause.** `ARCHITECTURE.md §3.7` already prescribed the right behavior: *"Claims about absence ('no recorded LDL') are verifiable if the corresponding tool was actually called and returned an empty result. The verifier matches against the **tool-call shape**, not just records."* The verifier implementation hadn't caught up — every claim was required to carry at least one `source_record_id`.

**Decision.** In `verifier.py:_verify_one_claim`, allow `claim_type == ABSENCE` to pass without `source_record_ids` IFF the retrieved-records index is empty. The empty tool-call shape grounds the absence claim. Strict v1 form: only blanket-empty records get the exemption; partial-data absence ("no recent labs but here are problems") still requires source IDs against specific empty tools — week-2 work.

**Companion change in the agent loop.** Added a separate refusal path for "tools failed transiently" (e.g., DB error) vs "tools cleanly returned empty." If any tool failed AND no records came back, the agent refuses with an explicit *"clinical data sources could not be reached"* message rather than letting the LLM make absence claims about data we couldn't reach. Different conditions, different messages.

**New tests:**
- Unit: `test_absence_claim_passes_when_no_records_retrieved`, `test_non_absence_claim_still_fails_when_no_records`.
- Eval: `06_empty_records_absence_claim.yaml` — uses sentinel `patient_id=999999` (fixture layer returns empty for this) to exercise the absence-verification path through the chat endpoint. Live-LLM-only (canned fixture response references real IDs that wouldn't match in this case).

**Why this matters for the CTO defense — be honest about provenance.** This was caught by **manual click-through testing during the live-LLM swap**, not by the eval suite. The eval cases as designed didn't simulate "tools returned empty" — they used `bad_hmac` for refusal triggering and assumed populated fixtures otherwise. The lesson: **eval suites need to grow with new failure modes; manual exploratory testing is still load-bearing.** Eval-driven development isn't fire-and-forget. The new eval case (06) closes this specific gap so future regressions are caught.

This is also a reminder that the architecture-doc-as-source-of-truth discipline matters: §3.7 had the answer all along; the implementation just hadn't reached it yet. Defense in front of a CTO benefits from acknowledging the gap rather than hiding it.

### 2026-04-30 (afternoon) — Verifier closes the token-level / date-normalization gap

**Symptom.** While reviewing successful traces in Langfuse, the user spotted: a `claim_type=lab_value` claim *"GFR was 77.3 mL/min on 08/19/2025"* passed verification, but the verifier was actually matching the digit groups `77.3`, `08`, `19`, `2025` independently against the cited record's blob — not the date `2025-08-19` as a single normalized token, and not `(77.3, 2025-08-19)` as a tuple. Two related gaps from §2:
1. Non-ISO dates (`MM/DD/YYYY`, `MM-DD-YYYY`) decomposed into bare digits, so a deliberate format swap could trick the verifier.
2. `(value, date)` pairs not validated as a tuple from a single record — value-from-record-A paired with date-from-record-B would slip through.

**Decision.** Both fixed in `verifier.py`:
- New `_DATE_REGEX` with named groups for ISO / `MM/DD/YYYY` / `MM-DD-YYYY`. `_normalize_date()` rewrites all three to canonical `YYYY-MM-DD` before any comparison.
- Per-record date sets (`record_dates_normalized`) maintained alongside per-record text blobs, so date-presence is checked against the specific record(s) the claim cites — not a flat union.
- Sentence-level proximity pairing: for each value + date in the same sentence within ~60 chars (`_PAIR_DISTANCE_THRESHOLD`), require both to co-locate in a *single* cited record's fields. Cross-record pairs fail with note `"(value, date) not co-located in any single cited record"`.
- Standalone date and numeric checks remain as fallbacks for tokens not part of any pair.

**Sentence splitter bugfix found via test failure.** `_split_sentences` originally split on `[.!?\n;]+`, which broke decimals — `"7.8"` became `"7"` + `"8"`. The cross-record-pair test caught this on the first run (claim text `"7.8 on 2025-12-10"` was split into `"7"` and `"8 on 2025-12-10"`, so the pair check ran on `("8", "2025-12-10")` instead of `("7.8", "2025-12-10")` — and `"8"` is a substring of `"6.8"` in record B, which falsely matched). Fixed by treating `.` as a sentence boundary only when not flanked by digits on both sides: `r"(?:(?<!\d)\.|\.(?!\d)|[!?\n;])+"`.

**New tests** (4, all passing alongside the existing 12):
- `test_us_slash_date_normalizes_to_match_iso_record_date`
- `test_us_dash_date_normalizes_to_match_iso_record_date`
- `test_value_date_pair_must_come_from_same_record` (cross-record fail)
- `test_value_date_pair_passes_when_co_located_in_same_record`

**Why this matters for the CTO defense.** Two of the three named verifier limits in §2 are now closed in week 1. The third (temporal-coherence — backwards-in-time delta narratives) remains explicitly week-2 work because it requires teaching the verifier to recognize delta language (`improved`, `→`, `up from`) and validate direction. Honest accounting: live testing surfaced the gap, the gap had a named fix path in DECISIONS.md, and the fix landed within hours. This is the verifier-iteration discipline the architecture promises.

**Provenance.** Caught by **manual review of a successful Langfuse trace** — not by the eval suite, not by a customer report. Reviewing successful traces (not just refusals) is what surfaced the structural mismatch. Same lesson as the absence-claim gap: eval suites need to grow with new failure modes; reviewing what *passed* is as important as reviewing what failed.

### 2026-04-29 (evening) — Session reads via `SessionWrapperFactory`, not the raw `$_SESSION` superglobal

**Symptom.** `401 Authentication required` with a valid session cookie, even after the bootstrap was simplified. Server-side debug dump showed:
```
session_keys: ["OpenEMR", "_sf2_meta", "_symfony_flashes"]
authUserID: (missing)
pid: (missing)
```

**Cause.** OpenEMR uses a Symfony session-bag pattern: authentication and patient-context data live inside `$_SESSION['OpenEMR']`, not at the raw `$_SESSION` top level. Sub-agent's first-pass controller used `$_SESSION['authUserID']` directly — always empty regardless of whether the user was authenticated.

**Decision.** Read session via `SessionWrapperFactory::getInstance()->getActiveSession()->get('authUserID')`. Matches what `chat-panel.php` and other in-app pages do.

**Why this matters for the CTO defense.** Strongest interview talking-point from the build session. *"What does your eval suite test that a happy-path demo wouldn't reveal?"* — **namespace mismatches between session abstraction and raw superglobal access.** The deterministic verifier on the agent side wouldn't have caught this; the Python eval suite's auth-boundary case (`05_auth_boundary_bad_hmac.yaml`) only tests HMAC validation; it took an integration smoke test plus a strategic debug dump to find. Three-line fix; high-leverage finding. Both the lesson (use the abstraction, not the superglobal) and the diagnostic technique (dump session keys, not just session ID) are reusable for week 2.

---

## Revision log

| Date | Revision | Author |
|---|---|---|
| 2026-04-29 (afternoon) | Initial document. Sections 1–10 + appendix entries for Wed afternoon work. | AgentForge build |
| 2026-04-29 (evening) | Three appendix entries for the local-integration smoke-test bugs (`js_url` URL-encoding, `$sessionAllowWrite` bootstrap, session-bag namespace). | AgentForge build |
| 2026-04-30 (morning) | Closed §3.7 implementation gap (absence claims pass when no records retrieved); added eval case 06; honest provenance note (caught by manual testing, not eval). | AgentForge build |
| 2026-04-30 (morning) | Workflow insight: schedule (not search) is the natural entry point. Calendar-integrated trigger + pre-visit-brief pre-warming added to §10 non-goals as week-2+ candidates. | AgentForge build |
| 2026-04-30 (mid-morning) | Pulled real-DB tools + Synthea data import + today's appointments forward into week 1 (was week-2 work). Trade-off: ~3–4 hours more today; demo story shifts from single-curated-fixture to realistic multi-patient workflow. | AgentForge build |
| 2026-04-30 (late-morning) | §4 (HIPAA) expanded with explicit `us-hipaa.cloud.langfuse.com` migration path + 3 named pre-production gates (HIPAA region, Anthropic BAA, PHI redaction at log-write). | AgentForge build |
| 2026-04-30 (late-morning) | §2 verifier-limits expanded with temporal-coherence gap (LLM produced backwards-in-time delta narrative; values + dates verified individually; narrative direction not checked). Flagged as week-2 fix. | AgentForge build |
| 2026-04-30 (mid-day) | §2 verifier-limits expanded with token-level-vs-semantic-pairing gap (digit groups matched individually; non-ISO date formats decompose to bare digits; (value, date) not validated as a tuple from a single record). Week-2 fix path documented. | AgentForge build |
| 2026-04-30 (afternoon) | Closed the token-level / date-normalization gap in `verifier.py` — date-format normalization (ISO / `MM/DD/YYYY` / `MM-DD-YYYY` → canonical ISO) and proximity-based `(value, date)` tuple pairing against single-record fields. Sentence-splitter bugfix found via test failure. 4 new unit tests; all 16 pass. §2 limits list reduced from 3 to 1 (temporal-coherence remains as week-2 work). | AgentForge build |
| 2026-05-01 (Fri) | §4a expansion — PHI redaction implementation plan (response to MVP grader feedback gap #2). 18 HIPAA Safe Harbor identifiers tagged IN-SCOPE/NOT-RELEVANT for our I/O surface; per-category redaction strategy (substitute / hash with rotated salt / strip / preserve clinical event dates with documented HIPAA tension); code seam at `agent/agent.py`'s `update_current_span(input=...)` calls; two-layer test plan (per-identifier unit + seeded-marker integration); honest scoping of what's not solved (free-text leakage in SOAP notes; chart-side de-id is a separate concern). Implementation deferred to week-3 hardening alongside the BAA-region Langfuse migration. | AgentForge build |
| 2026-05-01 (Fri) | New companion doc [`RULE_CORPUS.md`](./RULE_CORPUS.md) (response to MVP grader feedback gap #3). Three selection filters (clinical-action threshold, evidence quality preferred-order USPSTF > society guideline > UpToDate, false-positive cost tier); initial 7-rule corpus (warfarin+NSAID, ACEi+K-sparing+K≥5.0, PCN+beta-lactam, A1c +1.0 trajectory, eGFR -25%, statin gap on diabetic, SBP +20 on treated HTN); per-rule "adjacent rule considered + why this won" answering the grader's specific critique; "what's NOT in the corpus" table for the explicitly-rejected rules; implementation status (corpus designed; engine is week-2 work). Cross-linked from §2 "Rule corpus boundary" above. | AgentForge build |
| 2026-05-01 (Fri evening) | Explicit prompt-caching shipped in `agent/agent.py:run_chat`. `system` is now a 2-block list with `cache_control: {"type": "ephemeral"}` on the patient-context block (combined cacheable prefix ~7K tokens, well above Anthropic's 1024-token minimum). Verified live against Synthea-Guadalupe: call 1 created a 10,057-token cache entry, call 2 read 100% of it (~90% input cost savings on the cached portion). Static block + patient context together became the cache unit because static alone (~520 tokens) is below the 1024 minimum. Cache benefit pattern: best for UC3 multi-turn same-patient (every follow-up turn hits cache); none for UC1/UC2 single-turn or cross-patient. COST_ANALYSIS.md §3 updated to reflect "shipped + measured" rather than "planned + aspirational." Verification script committed at `agent/tests/verify_cache.py`. | AgentForge build |
| 2026-05-02 (Sat morning) | Three Langfuse observability improvements shipped together. (1) **Sessions** — added `session_id` to the `/chat` request schema; `chat-panel.js` generates a UUID per panel-open; `CoPilotController.php` forwards it. session_id is intentionally NOT in the HMAC payload (observability metadata only — tampering can corrupt Langfuse trace grouping but cannot bypass auth or alter agent behavior). (2) **Users** — promoted `user_id` (and session_id when present) from metadata to top-level Langfuse trace attributes via OTel `user.id` / `session.id` span attributes. The Users + Sessions dashboards now work for per-user spend rollups and multi-turn UC3 conversation grouping. (3) **PHI mask first cut** — see §4a callout above. Year-month bucketing on day-precision dates via Langfuse's `mask=` callback; 12 unit tests; verified end-to-end with live trace round-trip (input + output both bucketed; observation-level dates also bucketed). Forced singleton mask attachment in `_langfuse()` because Langfuse v4's `LangfuseResourceManager` is per-public-key and silently ignores `mask=` on subsequent constructions. Pre-commit hook still passes (37/37). | AgentForge build |
| 2026-05-01 (Fri night) | COST_ANALYSIS.md substantially revised after walking the post-ship math honestly. Headline per-PCP/mo corrected upward from $6.60 to $9.50 (LLM-only) / $9.74–$10.83 (total) across all four tiers — the prior $6.60 assumed a "50% automatic prefix caching" effect that doesn't actually exist in Anthropic's pricing. New §3.1 documents a measurement-driven kill switch (disable explicit caching if pilot cache-hit-ratio < 15%) reframed around "same-patient-same-model repeat call rate" rather than "UC3 share specifically." New §3.2 documents the cached-prefix multiplier — adding new UCs (UC4 orders, UC5 differential dx, etc.) on the same patient runs ~50% cheaper than the first UC because the prefix is already cached, making per-PCP/mo grow sub-linearly with UC count ($9.50 at 3 UCs → ~$11 at 6 UCs → ~$12.50 at 9 UCs vs. $19+ at cold-call math). New §11 documents roadmap implications: bundle UCs into the same encounter window, prefer Haiku for added UCs, pilot data justifies expansion not contraction. ARCHITECTURE.md §2.3, §2.4, §2.5 updated with inline `> Updated` callouts for the same nuances; this §6 same. The "~80% drop on multi-turn" claim is now correctly scoped to within-Sonnet-conversation, not blended. | AgentForge build |

When updating, add a row to this table and date-stamp any modified appendix entries inline.
