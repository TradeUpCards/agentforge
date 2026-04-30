# DECISIONS.md — Architectural Choices and Their Defenses

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
- **Rule corpus boundary.** Clinical-significance claims (e.g. "this lab change is concerning") only fire when a cited rule matches. The agent will not invent clinical interpretation outside the rule corpus. Expanding the agent's clinical-claim surface requires expanding cited rules — content problem, not architecture. ([ARCHITECTURE.md §3.5](./ARCHITECTURE.md))

**Limits we acknowledge.** The verifier does NOT catch *omissions* — if the agent fails to mention the active diabetes diagnosis, no rule fires. Eval suite has partial mitigation via "did you surface X?" cases, but true omission detection is a hard problem deferred. ([ARCHITECTURE.md §3.9](./ARCHITECTURE.md))

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
  - **PHI redaction in observability traces** — Langfuse traces include patient context in dev mode for the demo; production hardens this with redaction at log-write time. ([ARCHITECTURE.md §7](./ARCHITECTURE.md))
  - **Anomalous-access alerting** — the structured `agent_log` makes this trivial to add later, but it's not in MVP. ([ARCHITECTURE.md §10 #4](./ARCHITECTURE.md))

**Tradeoff accepted.** This is a v0.5 with a defined v1 path. A CTO would treat it as deployable for pilot/sandbox use today; full production deployment requires the week-3 hardening list to land.

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

**Concrete projections** (week 1 dev burn, in dollars):
- With multi-model + caching: **$15–40**
- Sonnet-only, no caching: ~$60–150 — roughly 4× higher

**Scaling phases** ([ARCHITECTURE.md §8.4](./ARCHITECTURE.md)):
| Phase | Concurrent users | Architectural changes |
|---|---|---|
| 1 — Single VPS | 5–50 | Vertical scaling |
| 2 — Compute / data split | 50–150 | Managed DB, agent + OpenEMR on separate hosts behind LB, Redis for shared state |
| 3 — Horizontal at scale | 300+ | Multiple OpenEMR instances behind LB, agent horizontally scaled (ECS / Cloud Run / GKE), read replicas, patient-context cache, self-hosted Langfuse |

**Honest framing for the CTO.** At 300 concurrent users, the bottleneck is the LLM provider, not the host count. Anthropic API rate limits, token economics, and prompt-cache hit rate are larger levers than any infrastructure decision. Multi-model tiering and caching are already in place — they get *more* valuable at scale.

A full cost-analysis document at 100 / 1K / 10K / 100K users with per-tier architectural transitions is a separate deliverable (`COST_ANALYSIS.md` — final-submission scope per the brief).

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

When updating, add a row to this table and date-stamp any modified appendix entries inline.
