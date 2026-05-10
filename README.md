# AgentForge Clinical Co-Pilot

> A trustworthy AI agent embedded in OpenEMR that gives a primary care physician the patient context they need in the 90 seconds between rooms — verified against the chart, not hallucinated. **Week 2** extends this with multimodal evidence: physicians upload lab PDFs and intake forms; the agent extracts structured facts with click-to-source bbox citations, then writes them back to OpenEMR's clinical tables behind a clinician approval gate.

Built for the **GauntletAI Austin Admission Track, Weeks 1 + 2**. Forked from [openemr/openemr](https://github.com/openemr/openemr); the agent and integration module are net-new.

---

## What this is

A physician walks toward an exam room with ~90 seconds to recall who they're seeing, what's changed since the last visit, and what actually matters today. The Co-Pilot sits inside the OpenEMR chart UI and answers that question with a structured pre-visit brief — every claim cited to a specific record (`lists:1408`, `procedure_result:5421`, etc.). A separate **verifier** strips any claim that can't be matched back to the chart before the response reaches the user.

**Three use cases on the v1 surface:**
- **UC1 — Pre-visit brief.** "What do I need to know about this patient before walking in?"
- **UC2 — Delta narrative.** "What's changed since the last visit?"
- **UC3 — Multi-turn Q&A.** "What's their A1c trajectory?" → follow-ups stay grounded in the same patient context.

**The hard problem isn't generating text — it's *not* generating text that isn't in the data.** The verifier is what makes the surface defensible in front of a hospital CTO; everything else is plumbing.

### What's new in Week 2 (multimodal evidence agent)

- **Document upload + structured extraction.** PDFs (`lab_pdf`, `intake_form`) ingested through Docling layout + Anthropic Haiku schema extraction; results round-trip into OpenEMR's clinical tables (procedures, lists, prescriptions) with full FHIR-link traceability via `co_pilot_fhir_links`.
- **Substring-grounded extraction verifier.** Every extracted field's `value` must substring-match the cited Docling block's text. Fields that fail are atomically stripped; >30% strip rate triggers a refusal with `extraction_low_grounding`.
- **HITL review-and-approve gate.** Extraction lands in `pending_review`. Clinician opens the modal, sees per-field bbox-overlay citations on the original PDF, edits OCR errors, asserts from blocks for missed fields, then explicitly approves before any clinical-table write fires. Reopen-for-review path supports post-approval correction.
- **LangGraph supervisor + 2 workers + responder.** Inspectable DAG (`supervisor → {evidence_retriever | intake_extractor} → responder`). Per-node 7-field Langfuse observability (latency, tokens-in/out, cost, retrieval hits, extraction confidence, eval outcome).
- **Hybrid RAG with reranking.** BM25 (sparse) + Qdrant (dense, BAAI bge-small-en) + Cohere/BAAI cross-encoder rerank over a 26-chunk clinical-guideline corpus.
- **Visual PDF bounding-box overlay.** Click any citation badge → SVG overlay highlights the source region on the original PDF (or PNG/JPG). Resolver endpoint `GET /resolve_citation.php?source_type=...&source_id=...` does pure-SQL traversal; auth via OpenEMR session + ACL.
- **65-case eval suite + PR-blocking CI gate.** 5 boolean rubrics, >5pp regression threshold, 80% absolute floor, strip-rate gate as second axis. Smoke tier (14 cases) on every pre-commit; full + nightly tiers in CI.
- **Modern React patient dashboard.** React 19 + Vite 6 + TypeScript strict + TanStack Query + react-oidc-context (OAuth2 + PKCE). Six clinical cards over OpenEMR FHIR API; zero PHP changes; full parity with the legacy patient summary screen.

**Key architectural choices** (full reasoning in [DECISIONS.md](./DECISIONS.md)):
- **Atomic claim verification + 30%-strip rule** — every fact the LLM emits is paired with `source_record_ids`; the verifier strips claims that fail token/date/citation match. >30% strip rate → refuse rather than ship.
- **Multi-model tiering** — Sonnet for reasoning, Haiku for synthesis (~3× cheaper for ~70% of calls).
- **Explicit Anthropic prompt caching** — `cache_control` breakpoint on the per-patient context block; UC3 follow-ups read from the same cache entry within the 5-min TTL.
- **Stateless agent service** — full conversation history sent every turn; OpenEMR module owns auth, ACL, CSRF, HMAC, session.

### LLM-callable tools (5 baseline, all read-only, all patient-scoped)

Every tool takes `patient_id` as the *only* identifier parameter — derived from the OpenEMR session, never from LLM output (per AUDIT.md S-2). Schemas live in `agent/agent.py`'s `_TOOL_DEFS` block; implementations in `agent/tools.py`.

| Tool | Schema (Anthropic tool-call shape) | Returns | Cite via |
|---|---|---|---|
| `get_problem_list` | `{patient_id: int}` | Active problems with ICD-10 codes + onset dates | `lists:<id>` |
| `get_active_medications` | `{patient_id: int}` | Currently-active prescriptions: drug, dose, frequency, start date | `prescriptions:<id>` |
| `get_recent_labs` | `{patient_id: int, since_date?: str, lab_codes?: list[str]}` | Lab results within window, optionally LOINC-filtered | `procedure_result:<id>` |
| `get_allergies` | `{patient_id: int}` | Documented allergies with reaction + severity | `lists:<id>` |
| `get_recent_encounters` | `{patient_id: int, limit?: int}` | Recent visits with assessment_plan + reason | `form_encounter:<id>` |

**Deliberately not a tool:** demographics, vitals (queried via `get_recent_encounters`), medication history (queried via `get_active_medications` with active=false filter — also not exposed today), appointments. ARCHITECTURE.md §2.6 deviation appendix explains why the original 9-tool design collapsed to 5; tighter prompt + clearer model reasoning surface.

---

## Try it

| | |
|---|---|
| **Deployed agent** | https://142-93-242-40.nip.io (credentials provided via the GauntletAI submission portal — synthetic demo data only, no real PHI) |
| **Eval results (latest run)** | **[`EVAL_RESULTS.md`](./EVAL_RESULTS.md)** — 67-case golden set; **60 pass / 7 fail = 89.6%** in the merged 3-mode view (live + fixture + hybrid). **Fixture mode (PR-blocking gate): 33/0 fail = 100%.** All 7 remaining failures are nightly-tier `live_llm_required` cases documented in [`EVAL_SUITE.md`](./EVAL_SUITE.md) §6 with W3 fix scopes. Per-rubric regression-coverage matrix at [`EVAL_SUITE.md`](./EVAL_SUITE.md) §8.6 documents empirical proof that each of the 5 PRD rubrics fires under deliberate regression injection. |
| **Demo video (final submission)** | https://youtu.be/kwIbpru4ci4 |
| **Demo video (early submission, MVP gate)** | See `.gauntlet/week1/early-submission-video-script.md` (private notes; the video itself was submitted via the GauntletAI portal) |
| **GitHub mirror** | https://github.com/TradeUpCards/agentforge |
| **GitLab (primary)** | https://labs.gauntletai.com/coryvandenberg/agentforge |

Pre-loaded with **200 Synthea-imported demo patients** + 1 hand-crafted edge-case patient. No real PHI.

---

## Documentation map (Week 1 + Week 2 brief deliverables)

### Week 1 brief — 8 required deliverables

| # | Brief deliverable | Location | What's in it |
|---|---|---|---|
| 1 | **GitHub Repository** | This repo + [`SETUP.md`](./SETUP.md) (setup guide) | Forked from OpenEMR; this README + the docs below + `agent/` + `interface/modules/custom_modules/oe-module-clinical-copilot/` + `patient-dashboard/` are net-new. **Setup guide:** [`SETUP.md`](./SETUP.md) covers Docker stack bring-up, demo data loading (Synthea), `agent_ro` DB user creation, env-var configuration, pre-commit hook install, and verification. |
| 2 | **Audit Document** | [`AUDIT.md`](./AUDIT.md) | Security / performance / architecture / data-quality / compliance audit of OpenEMR. Begins with a 1-page summary of the most impactful findings. |
| 3 | **User Doc** | [`USERS.md`](./USERS.md) | Target user (primary care physician), three concrete use cases UC1/UC2/UC3, and *why an agent* is the right shape for each. |
| 4 | **Agent Architecture Doc** | [`ARCHITECTURE.md`](./ARCHITECTURE.md) | The integration plan + verification strategy + tradeoffs. Begins with a 1-page summary; subsequent sections cover model selection, tool design, verifier placement, observability, deployment. |
| 5 | **Demo Video (3–5 min)** | *See "Try it" above* | Final-submission video links here on commit. |
| 6 | **Eval Dataset** | **[`EVAL_RESULTS.md`](./EVAL_RESULTS.md)** + [`EVAL_SUITE.md`](./EVAL_SUITE.md) (suite design) + [`agent/tests/eval/cases/`](./agent/tests/eval/cases/) (65 case YAMLs) + [`agent/tests/eval/COVERAGE.md`](./agent/tests/eval/COVERAGE.md) (coverage matrix) | 65 cases across 10 categories; merged report shows live + fixture + hybrid mode results per case; 12 synthetic patient fixtures. |
| 7 | **AI Cost Analysis** | [`COST_ANALYSIS.md`](./COST_ANALYSIS.md) | Actual W1 + W2 dev burn + per-PCP/mo projections at 100 / 1K / 10K / 100K with the architectural changes named at each tier; W2 ingestion-pipeline economics + bottleneck analysis. |
| 8 | **Deployed Application** | https://142-93-242-40.nip.io | Single DigitalOcean droplet running the full stack (OpenEMR + agent service + MariaDB + Qdrant + Caddy). |
| 9 | **Social Post** *(final only)* | https://x.com/CardsTradeup/status/2053452810885177646 | |

### Week 2 brief — additional deliverables

| # | Brief deliverable | Location | What's in it |
|---|---|---|---|
| W2.1 | **Architecture Doc (W2 multimodal)** | [`W2_ARCHITECTURE.md`](./W2_ARCHITECTURE.md) | Document ingestion pipeline (Docling + Haiku), LangGraph supervisor + 2 workers + responder, hybrid RAG, citation contract + bbox overlay, eval gate, risks + tradeoffs. |
| W2.2 | **Schemas (Pydantic strict, citation fields)** | [`agent/document_schemas.py`](./agent/document_schemas.py) (`LabReport`, `IntakeForm`, `DoclingDoc`, `BBox`) + [`agent/schemas.py`](./agent/schemas.py) (`Citation` discriminated union) + [`agent/tests/unit/test_document_schemas.py`](./agent/tests/unit/test_document_schemas.py) (validation tests) | All extracted entities carry `source_block_id` → traceable to a Docling block. Citation contract: `{source_type, source_id, page_or_section, field_or_chunk_id, quote_or_value, bbox?}`. |
| W2.3 | **Eval Dataset (≥50 cases, boolean rubrics, judge config, results)** | Same as #6 above + [`EVAL_RESULTS.md`](./EVAL_RESULTS.md) (results) + [`agent/tests/eval/baseline.json`](./agent/tests/eval/baseline.json) (judge config) | 65 cases, 5 boolean rubrics (`schema_valid`, `citation_present`, `factually_consistent`, `safe_refusal`, `no_phi_in_logs`), gate fails on >5pp regression OR <80% absolute floor; strip-rate gate as second axis. |
| W2.4 | **CI Evidence (PR-blocking gate)** | [`scripts/git-hooks/pre-commit`](./scripts/git-hooks/pre-commit) (smoke tier) + [`.github/workflows/agent-eval.yml`](./.github/workflows/agent-eval.yml) (full pipeline) + [`.gitlab-ci.yml`](./.gitlab-ci.yml) (mirror) + [`scripts/run_eval_gate.py`](./scripts/run_eval_gate.py) + [`scripts/run_strip_rate_gate.py`](./scripts/run_strip_rate_gate.py) + [`EVAL_SUITE.md`](./EVAL_SUITE.md) §8.6 (per-rubric regression-coverage matrix — empirical proof from one verified regression per rubric, validated 2026-05-10 immediately before final submission) | "Pipelines must succeed" toggle ON in GitLab; rubric meta-tests + 6 adversarial regression-class cases self-test the gate machinery. The §8.6 matrix is the defense surface for the PRD §6 hard-gate question "graders introduce a regression and confirm your CI gate fails." |
| W2.5 | **Surprise Challenge — React Patient Dashboard** | [`patient-dashboard/`](./patient-dashboard/) + [`PATIENT_DASHBOARD_MIGRATION.md`](./PATIENT_DASHBOARD_MIGRATION.md) (framework defense) | React 19 + Vite 6 + TypeScript strict + TanStack Query + react-oidc-context; 6 clinical cards (Allergies, Problems, Medications, Prescriptions, Care Team, Encounters); OAuth2 + PKCE against local OpenEMR; zero PHP changes. |
| W2.6 | **Cost & Latency Report** | [`COST_ANALYSIS.md`](./COST_ANALYSIS.md) (W2 section: actual dev burn, projected production cost, per-tier ladder) + [`PERFORMANCE.md`](./PERFORMANCE.md) (p50/p95 per tool, bottleneck analysis) + [`SLO.md`](./SLO.md) (SLO targets) | All four PRD-required pieces: actual dev spend, projected production cost, p50/p95 latency, bottleneck analysis. |

---

## Companion docs (deeper context the brief doesn't require but a CTO would read)

| Doc | Purpose |
|---|---|
| [`DECISIONS.md`](./DECISIONS.md) | Architectural decisions log — every load-bearing choice with the *why*, the *tradeoff*, and an inline `> Updated <date>` callout when newer information changes the answer. |
| [`W2_ARCHITECTURE.md`](./W2_ARCHITECTURE.md) | **Week 2 architecture** — multimodal evidence agent. Two-stage document extraction (Docling + Haiku), LangGraph supervisor + 2 workers, Qdrant hybrid RAG, 50-case PR-blocking CI gate. Companion to `ARCHITECTURE.md` (which is the Week-1 baseline). |
| [`PERFORMANCE.md`](./PERFORMANCE.md) | EXPLAIN analysis on the 5 tool queries; one load-bearing fix shipped (full-scan on `forms` table → 4-row indexed lookup). |
| [`SLO.md`](./SLO.md) | Service-level objectives + alerting plan — 5 SLOs (availability, verifier pass rate, citation match rate, latency P95, tool failure rate) with named thresholds + page-vs-ticket triage. |
| [`RUNBOOK.md`](./RUNBOOK.md) | Backup, restore, and on-call runbook — what gets backed up at what cadence, three restore scenarios (table / DB / host loss) with RTO targets, monthly restore-drill cadence. |
| [`RULE_CORPUS.md`](./RULE_CORPUS.md) | The 7-rule clinical corpus the verifier flags against, with selection filters and "adjacent rule considered + why this won." |
| [`SYNTHETIC_DATA_PLAN.md`](./SYNTHETIC_DATA_PLAN.md) | How the 10 synthetic edge-case patients were designed — sentinel ID range, no-real-PHI validator, per-patient failure mode coverage. |
| [`SETUP.md`](./SETUP.md) | Local-dev setup: clone, `docker compose up`, agent venv, env vars, pre-commit hook install. |
| [`WORKFLOW.md`](./WORKFLOW.md) | Git workflow + dual-mirror sync rules. **Read before committing** — explains how to push so both GitLab and GitHub stay at the same SHA (avoid the dual-merge divergence trap). |

---

## Repo structure — what's ours vs upstream

```
AgentForge/
├── agent/                                   ← Python agent service (NET-NEW)
│   ├── agent.py, _synthesis.py, _phi_scrubber.py
│   ├── llm_client.py, verifier.py, tools.py, schemas.py, document_schemas.py, main.py
│   ├── extractors/                         Document extraction pipeline (Week 2)
│   │   ├── __init__.py                     attach_and_extract() entrypoint + Langfuse span wiring
│   │   ├── haiku_extraction.py             Stage-2 Haiku/Sonnet field extractor + grounding verifier
│   │   ├── template_id.py                  Filename → template_id resolver
│   │   └── cost.py                         Model pricing constants + compute_cost_usd()
│   ├── graph/                              LangGraph supervisor + workers (Week 2)
│   │   ├── builder.py                      build_supervisor_graph() — DAG construction
│   │   ├── supervisor.py                   Routing decisions + max-hop bound
│   │   ├── state.py                        SupervisorState TypedDict
│   │   └── workers/
│   │       ├── evidence_retriever.py       W1 patient-data tools + W2 search_guidelines
│   │       ├── intake_extractor.py         Document-upload flow (currently fires from /attach_and_extract)
│   │       └── responder.py                Synthesis with verifier + outbound PHI gate + Sonnet escalation
│   ├── retrieval/                          Hybrid RAG (Week 2)
│   │   ├── bm25.py                         Sparse retrieval (rank_bm25)
│   │   ├── qdrant_client.py                Dense vector retrieval (BAAI bge-small-en, 384-dim)
│   │   ├── hybrid.py                       BM25 + dense top-K merge
│   │   └── reranker.py                     Cohere Rerank primary, BAAI cross-encoder fallback
│   ├── corpus/                             Clinical-guideline corpus (Week 2)
│   │   ├── ingest.py                       One-time ingestion script
│   │   └── guidelines/                     26 chunks across afib, ckd, heart_failure, metformin, warfarin, etc.
│   ├── fixtures/patients/                  Synthetic edge-case patients (sentinel IDs 999100-999114)
│   └── tests/
│       ├── unit/                           Verifier + PHI mask + graph + responder + document-schema unit tests
│       ├── integration/                    test_graph_e2e + test_attach_endpoint + test_haiku_live
│       └── eval/                           65 YAML eval cases + runner + 5 boolean rubrics + per-run markdown reports
│
├── interface/modules/custom_modules/
│   └── oe-module-clinical-copilot/         ← OpenEMR integration module (NET-NEW)
│       ├── openemr.bootstrap.php           Module entry — registers PSR-4 namespace, wires event subscribers
│       ├── src/                            Controllers, EventSubscribers (PageHeading, ScriptFilter, DocumentSaved), RoundtripService, PersonaMap
│       ├── public/                         Browser-facing chat panel + HITL review modal + bbox-overlay sidecar + upload-progress UX
│       │   ├── chat-panel.js, chat.css     Chat UI with citation popovers
│       │   ├── hitl-review.js              HITL review modal with bbox SVG overlay (W2)
│       │   ├── hitl-banner.js              Banner state machine (pending_review / approved / rejected)
│       │   ├── upload-progress.js          4-phase progress UX during 30-90s extractions (W2)
│       │   ├── resolve_citation.php        Citation → {document_id, page, bbox, snippet} resolver (W2)
│       │   ├── extraction_for_doc.php      Modal data-loader endpoint
│       │   └── edit_extracted_field.php    Pre-approval edit endpoint (W2 P4)
│       └── sql/install.sql                 Schema for co_pilot_extractions + co_pilot_extracted_fields + co_pilot_fhir_links
│
├── patient-dashboard/                       ← React patient dashboard (NET-NEW, Week 2 surprise challenge)
│   ├── src/auth/                           OAuth2 + PKCE via react-oidc-context
│   ├── src/api/                            FHIR client + per-resource modules
│   ├── src/components/cards/               6 clinical cards (Allergies, Problems, Medications, Prescriptions, Care Team, Encounters)
│   └── src/pages/                          Login + PatientSelect + Dashboard
│
├── .deploy/bootstrap.sh                     ← Droplet bootstrap script (NET-NEW)
├── scripts/                                 ← Build / eval / dev tooling (NET-NEW)
│   ├── git-hooks/pre-commit                Verifier + smoke-tier eval runner
│   ├── run_eval_gate.py                    PR-blocking rubric regression gate
│   ├── run_strip_rate_gate.py              Strip-rate regression gate (second axis)
│   ├── render_kickoff.py                   Lead-rotation tooling (multi-agent dev workflow)
│   └── lead-launchers.{sh,ps1}             Worktree + branch + junction setup per lead
│
├── README.md ARCHITECTURE.md W2_ARCHITECTURE.md AUDIT.md       ← Submission docs (NET-NEW)
├── COST_ANALYSIS.md DECISIONS.md OBSERVABILITY.md              ← Companion docs (NET-NEW)
├── EVAL_SUITE.md EVAL_RESULTS.md PERFORMANCE.md SLO.md
├── RULE_CORPUS.md SYNTHETIC_DATA_PLAN.md RUNBOOK.md
├── PATIENT_DASHBOARD_MIGRATION.md          ← React dashboard framework defense + parity log
├── USERS.md SETUP.md WORKFLOW.md
│
├── OPENEMR_README.md                        ← Upstream OpenEMR README, preserved
└── (everything else is upstream OpenEMR)    ← src/, library/, sql/, tests/, etc.
```

If you're reviewing this repo, the work to look at lives in `agent/`, `interface/modules/custom_modules/oe-module-clinical-copilot/`, `.deploy/`, and the root-level `*.md` docs listed above. The rest is OpenEMR upstream — referenced for context (e.g., `src/Common/Acl/AclMain.php` is what our PHP controller calls into) but not modified.

---

## Status

### Working in production (Week 1 baseline)
- UC1 / UC2 / UC3 end-to-end against real Synthea-imported MariaDB
- Verifier with date normalization (ISO/MM-DD-YYYY/MM/DD/YYYY) + value-date tuple matching
- Explicit Anthropic prompt caching (verified live: 100% cache READ on identical follow-up)
- Langfuse observability — traces + sessions + users + per-LLM-call latency + PHI date-bucketing mask + outbound PHI scrubber (cross-patient ID, SSN, phone, email, MRN)
- Auth depth: HMAC + CSRF + ACL on every backend endpoint; JS/CSS auth gate; **HMAC replay protection** via signed timestamp; **per-user rate limiting + hourly token budget**
- HIPAA `agent_log` audit table — every PHI read recorded (closes AUDIT.md C-1)
- Operational docs: [`SLO.md`](./SLO.md) (5 SLOs with thresholds) + [`RUNBOOK.md`](./RUNBOOK.md) (backup/restore procedures with named RTO targets)

### Working in production (Week 2 multimodal additions)
- **Document upload + extraction** via `/attach_and_extract` — Docling layout + Anthropic Haiku schema extraction; substring-grounded verifier with atomic field-strip + >30%-strip refusal
- **HITL review-and-approve flow** — extraction lands as `pending_review`; clinician opens modal with bbox-overlay click-to-source on the original PDF, edits OCR errors, asserts from blocks for missed fields, then explicitly approves before clinical-table writes fire
- **FHIR round-trip with idempotency** — `RoundtripService` writes derived facts into `procedure_result`, `lists`, `prescriptions`; `co_pilot_fhir_links` table with `UNIQUE(extraction_id, target_table, source_block_id)` makes re-extraction safe
- **LangGraph supervisor + 2 workers + responder** via `/graph_chat` — supervisor decides routing (`evidence_retriever` for clinical Q&A, `intake_extractor` for upload context), responder synthesizes with verifier-bounded answers; per-node 7-field Langfuse spans (latency, tokens-in/out, cost, retrieval hits, extraction confidence, eval outcome)
- **Hybrid RAG** — BM25 + Qdrant dense (BAAI bge-small-en, 384-dim) + Cohere/BAAI cross-encoder rerank over a 26-chunk clinical-guideline corpus
- **Visual PDF bounding-box overlay** — citation badges in chat or HITL modal click through to a SVG overlay on the original PDF; `GET /resolve_citation.php` does pure-SQL traversal (no LLM round-trip needed for the resolution)
- **PHI sentinel boundary** — `PersonaMap` translates real OpenEMR pids to sentinels in the 999100-999199 range before any agent call; agent traces / Langfuse / `co_pilot_extractions` only ever see sentinel IDs
- **65-case eval suite + PR-blocking CI gate** — `.github/workflows/agent-eval.yml` + GitLab CI mirror; gate fails on >5pp regression OR <80% absolute floor; strip-rate gate as second axis; 14-case smoke tier on every pre-commit; rubric meta-tests + 6 adversarial regression-class cases self-test the gate
- **Modern React patient dashboard** ([`patient-dashboard/`](./patient-dashboard/)) — React 19 + Vite 6 + TypeScript strict + TanStack Query + react-oidc-context; 6 clinical cards with full feature parity to the legacy patient summary screen; OAuth2 + PKCE against local OpenEMR; zero PHP changes
- **Coordination tooling** — multi-agent dev workflow via `.gauntlet/week2/coordination/` (cross-lead negotiation threads with row-maintained index) + `.gauntlet/week2/in-flight.md` (per-lead workstream + file-lock tracker)

### Deferred to Week 3+ (with documented rationale in DECISIONS.md / AUDIT.md)
- Cross-patient paraphrased leakage check (`AUDIT.md` C-7 [HIGH]) — `check_citation_patient_boundary()` operating on Citation provenance, not regex; deferred as a pre-clinical-pilot gate; not in graders' fixture-mode CI surface
- Same-patient drawer-open pre-warm cache (cross-patient pre-fetch explicitly *rejected* — see DECISIONS.md)
- Verifier temporal-coherence check (delta-narrative direction validation)
- Outbound PHI scrubber **name detection** — Tier 2 shipped (cross-patient IDs + SSN + phone + email + MRN) but cross-patient name detection still deferred (see DECISIONS.md §4a)
- Full HIPAA Safe Harbor 18-identifier sweep on inbound retrieved-records context (DECISIONS.md §4a)
- LLM-as-judge eval layer
- Replay harness against captured production traces
- Backup automation cron + first restore drill (RUNBOOK.md §6 — procedure documented, automation deferred)
- Alert routing wiring (SLO.md §4 — thresholds documented, page/ticket routing deferred)
- Eval-against-deployed nightly job (eval suite runs in CI against the Python module; nightly run against the deployed droplet not yet wired)
- `intake_extractor` worker activation on the `/graph_chat` runtime path (currently fires only on document upload via `/attach_and_extract`; graph-routing the upload context into chat queries is W3 scope)
- Corpus expansion (HF / CKD / AFib coverage beyond the current 26 chunks) — LLM correctly admits gaps with prose-only inline citations under claim-emission discipline; expansion is content-engineering, not a system gap

---

## Local development

```bash
git clone https://github.com/TradeUpCards/agentforge.git
cd agentforge
# Full setup, env vars, pre-commit hook install:
cat SETUP.md
```

Tests:
```bash
# Pre-commit hook runs this on every commit (~5-10s, no LLM cost)
agent/venv/Scripts/python.exe -m pytest agent/tests/unit/ agent/tests/eval/ -q

# Live-LLM + live-DB sweep (requires API keys + running stack)
USE_FIXTURE_LLM=false USE_FIXTURE_DATA=false python -m agent.tests.eval.runner
```

---

## About this fork

Forked from [openemr/openemr](https://github.com/openemr/openemr) on 2026-04-27. OpenEMR's own README is preserved at [`OPENEMR_README.md`](./OPENEMR_README.md). Upstream contributing guide, code-of-conduct, and CI configuration retained verbatim — this fork inherits OpenEMR's GPL 3 license.

The Co-Pilot module follows OpenEMR's existing custom-module + EventSubscriber patterns (modeled after `oe-module-prior-authorizations` and `oe-module-dorn`); the Python agent service is a separate FastAPI process, not embedded in the PHP runtime.
