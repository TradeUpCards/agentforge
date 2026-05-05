# W2_ARCHITECTURE.md — Multimodal Evidence Agent

> **Related docs:** [`ARCHITECTURE.md`](./ARCHITECTURE.md) (Week 1 baseline) · [`AUDIT.md`](./AUDIT.md) (security findings the architecture responds to) · [`DECISIONS.md`](./DECISIONS.md) · [`EVAL_SUITE.md`](./EVAL_SUITE.md) · [`COST_ANALYSIS.md`](./COST_ANALYSIS.md)

**Project:** AgentForge Clinical Co-Pilot — Week 2
**Scope:** Add document-vision ingestion + small multi-agent graph + hybrid-RAG-grounded answers, gated by a 50-case PR-blocking eval suite.

---

## Executive Summary

Week 1 shipped a stateless agent that reads structured OpenEMR data, attributes every claim to a record citation, and refuses rather than hallucinate. Week 2 adds **two capabilities** without losing that grounding discipline:

1. **Vision ingestion.** The agent can ingest a scanned lab PDF or an intake form, extract structured facts with **per-field bounding-box citations** that round-trip to a click-to-source overlay in the UI, and persist derived facts as FHIR resources in OpenEMR.
2. **Multi-agent graph.** A supervisor routes work to two workers — `intake-extractor` (vision + schema extraction) and `evidence-retriever` (hybrid RAG over a clinical-guideline corpus) — with explicit, logged handoffs.

**The hardest problem isn't building either capability — it's keeping the grounding contract intact across both.** Vision models hallucinate field labels. Knowledge graphs hallucinate edges. Multi-agent supervisors hallucinate routing decisions. The architecture's load-bearing constraint is the same as Week 1 — *verifiable trust* — extended to two new failure surfaces: extracted facts (must trace to a bounding box) and retrieved evidence (must trace to a guideline chunk).

**Five integration commitments forced by the PRD:**

1. **Vision is two-stage, never single-stage.** Layout engine (Docling) produces text + bounding boxes; LLM (Haiku 4.5) produces schema-validated JSON keyed to those boxes. We never ask a VLM to invent pixel coordinates — they hallucinate them.
2. **Patient-record facts and guideline evidence are separate citation types.** A medication claim cites `prescriptions:<id>` (Week 1 contract). A guideline claim cites `{source_type: guideline, source_id, page, chunk_id, quote}` (new, Week 2). The answer model is instructed to never blur the two.
3. **Supervisor decisions are inspectable.** LangGraph state transitions emit Langfuse spans with the routing rationale; no black-box "supervisor.decide()" calls. Every handoff is a traceable artifact.
4. **The eval gate is the deliverable.** 50 cases with boolean rubrics (`schema_valid`, `citation_present`, `factually_consistent`, `safe_refusal`, `no_phi_in_logs`); CI fails on >5% regression in any category. Graders will introduce a regression and confirm we catch it.
5. **HIPAA-aware vendor routing.** Document OCR runs locally (Docling, no PHI leaves the boundary); the LLM extraction pass routes through Anthropic where we already have a BAA path. No new SaaS observability tools that could see raw PHI.

**Stack additions to Week 1:**
- **Docling** (IBM, open-source, self-hosted) — layout engine producing per-element bounding boxes
- **LangGraph** — supervisor + worker orchestration (Langfuse-instrumented out of the box)
- **Qdrant** — vector DB with native sparse+dense hybrid retrieval (chosen over Chroma/pgvector/Weaviate/Pinecone — see §6 for rationale)
- **Cohere Rerank** — top-K rerank on hybrid candidates. Chosen because (1) PRD names it, (2) best-in-class accuracy on cross-encoder rerank benchmarks, (3) free tier + cheap pricing for our 50-case eval. **Fallback: BAAI/bge-reranker-v2** (open-source, same interface) if Cohere rate limits or pricing surprise.
- **RxNav** (NIH/NLM public API) — drug-name normalization to RxCUI for the entity-keyword retrieval boost. Chosen because (1) free, (2) no auth required, (3) NIH-maintained = authoritative for US clinical data, (4) real-time API. Considered: UMLS Metathesaurus (rejected — license overhead, broader than week-2 needs); local RxNorm DB dump (rejected — staleness risk).
- **Model role split** (inherited from Week 1 multi-model tiering): **Sonnet 4.5** for the supervisor (route decisions need reasoning capacity); **Haiku 4.5** for workers (extraction + synthesis are well-bounded; 3× cheaper). Same tiering shipped + cached in Week 1.
- All other Week 1 stack stays: Anthropic Claude, Langfuse, Pydantic, FastAPI, MariaDB.

**Tradeoff acknowledged.** Adding LangGraph + Docling + a vector DB is real new surface area. The defense for choosing them: each has a load-bearing reason (LangGraph for inspectable supervisor, Docling for real bounding boxes, vector DB for hybrid retrieval), and each has a documented exit ramp (workers as plain functions, Docling output as a JSON contract, vector DB behind a tiny `Retriever` interface).

---

## 1. The Scenario (from the PRD)

A primary care physician prepping for a follow-up visit. The chart has structured OpenEMR data (Week 1 surface), but the important recent information is in a **scanned lab PDF** and an **intake form** uploaded by the front desk. The physician asks: *"What changed, what should I pay attention to, and what evidence supports the recommendation?"*

The Week 2 agent must:
1. Ingest both documents, extract structured facts with citations
2. Retrieve relevant guideline evidence
3. Return a grounded answer that's useful even when the scan is imperfect, the chart is incomplete, or the user asks a follow-up

This maps to **three named user-visible behaviors** the eval suite will gate:
- **Document round-trip:** upload → extract → display fact + click-to-source bounding box overlay
- **Evidence-grounded answer:** chart facts + guideline evidence appear separately in the response with distinguishable citations
- **Safe refusal:** when extraction confidence is low or evidence is absent, the agent refuses with a named reason rather than fabricating

---

## 2. Document Ingestion Architecture

### 2.1 Upload UX — OpenEMR-native, two-actor workflow

**Important:** the PRD scenario explicitly names the front-desk staff as the uploader, not the PCP. The architecture supports a **two-actor workflow:**

| Actor | Role | Touches |
|---|---|---|
| **Front-desk staff** | Uploads documents (lab PDFs, intake forms) | OpenEMR's Documents tab |
| **PCP (week-1 user)** | Consumes extracted data when prepping for the visit | Co-Pilot drawer |

The PCP **does not manage uploads** — by the time they open the chart, extraction has already run and the structured facts are available in the drawer with citations. This matches the PRD's clinic-realistic scenario ("important recent information is buried in a scanned lab PDF and a patient intake form uploaded by the front desk").

**Decision: leverage OpenEMR's existing patient documents UI as the upload surface; trigger extraction via a category-tag event subscriber.** No custom upload UI in our module.

**Flow:**

```
[Front-desk staff]  opens patient chart → Documents tab
                  → drag-drops file, picks category
                    "Lab Result (auto-extract)" / "Intake Form (auto-extract)"
                  → OpenEMR persists file to documents table + storage
                  → DocumentSavedEvent fires
                                    │
                                    ▼
[PHP module event subscriber]  creates FHIR DocumentReference
                              → POSTs attach_and_extract to agent
                                    │
                                    ▼
[Agent service]  Docling layout → Haiku extraction → verifier
              → persists Observation / AllergyIntolerance / etc.
                with derivedFrom → DocumentReference
                                    │
                                    ▼
[PCP, later]  opens chart → Co-Pilot drawer surfaces extracted facts
            → clicks citation badge → bbox overlay on the PDF
              in OpenEMR's existing document viewer
```

**ACL implications:** the front-desk role in OpenEMR has document-upload privileges but typically not full chart access. The DocumentSavedEvent subscriber fires for any user with upload privileges; the agent's `attach_and_extract` runs on behalf of that user (their `user_id` lands in `agent_log`). The PCP later consumes extracted data through the Week-1 chat surface using their own `user_id` — separate audit trail per actor.

**Alternatives considered and rejected:**

| Option | Verdict | Reason |
|---|---|---|
| Custom upload UI in `oe-module-clinical-copilot/public` | ❌ | Duplicates OpenEMR's existing UX; front-desk staff would learn a second upload flow; bypasses OpenEMR's existing audit logging |
| Standalone agent-side `/upload` endpoint | ❌ | Bypasses OpenEMR integrity — file lives outside `documents` table; breaks FHIR roundtrip requirement; introduces a parallel auth/CSRF surface |
| ✅ **OpenEMR-native upload + category-triggered extraction** | Selected | Reuses existing UX/audit/storage; FHIR roundtrip is naturally OpenEMR-mediated; front-desk and PCP workflows both unchanged |

**MVP vs Extension scope for the click-to-source UI:**
- **MVP (Core req #5 — "visual PDF bounding-box overlay required"):** bbox highlight on the PDF when citation clicked
- **Extension (PRD page 5):** rich snippet preview popover with extracted-fact context, multi-page navigation, side-by-side diff for follow-up questions

### 2.2 Pipeline

```
┌────────────────────────────────────────────────────────────────────┐
│ User uploads PDF via OpenEMR's document attach UI                  │
└────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌────────────────────────────────────────────────────────────────────┐
│ OpenEMR stores the file as a FHIR DocumentReference                │
│   (Week-1 deferral — FHIR R4 path now lit up)                      │
└────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌────────────────────────────────────────────────────────────────────┐
│ attach_and_extract(patient_id, doc_ref_id, doc_type) tool fires    │
└────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌──────────────────────┐                    ┌─────────────────────────┐
│ STAGE 1: Docling     │                    │ Why two stages          │
│ Layout engine        │                    │ ─────────────────       │
│ ─────────────────    │                    │ A VLM can READ a doc    │
│ Input:  PDF bytes    │                    │ but its bbox            │
│ Output: DoclingDoc   │                    │ coordinates are         │
│   { blocks: [        │                    │ hallucinated. Bbox      │
│       { text, bbox,  │                    │ MUST come from a real   │
│         page,        │                    │ layout engine.          │
│         block_type   │                    │                         │
│       }, … ]         │                    │ Docling is open-source, │
│   }                  │                    │ self-hosted, runs on    │
│ Self-hosted, free,   │                    │ CPU. PHI never leaves   │
│ CPU-viable.          │                    │ our boundary at this    │
└──────────────────────┘                    │ stage.                  │
           │                                └─────────────────────────┘
           ▼
┌──────────────────────────────────────────────────────────────────────┐
│ STAGE 2: Haiku 4.5 schema extraction                                 │
│ ──────────────────────────────────────────                           │
│ Input:  DoclingDoc blocks + Pydantic schema (LabReport / IntakeForm) │
│ Prompt: "For each schema field, find the source block in this        │
│          document. Return {field_name: {value, source_block_id,      │
│          confidence}}. If a field is not present, return null with   │
│          source_block_id = null."                                    │
│ Output: validated Pydantic model with per-field citation             │
│         pointing back to a Docling block_id (which carries the bbox) │
└──────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌────────────────────────────────────────────────────────────────────┐
│ FHIR resource creation                                             │
│  - Lab fields  → DiagnosticReport + N×Observation                  │
│  - Intake      → Patient + AllergyIntolerance + MedicationStatement│
│ Each resource carries DocumentReference link back to source PDF    │
└────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌────────────────────────────────────────────────────────────────────┐
│ Audit log row written to agent_log (Week-1 table extends naturally)│
│   - tool: attach_and_extract                                       │
│   - inputs: patient_id, doc_ref_id, doc_type                       │
│   - outputs: count of resources created, extraction_confidence avg │
│   - phi: none in log (only IDs + counts)                           │
└────────────────────────────────────────────────────────────────────┘
```

### 2.3 Why Docling specifically (vs alternatives)

| Candidate | Bbox source | HIPAA posture | Why not |
|---|---|---|---|
| **Claude Sonnet 4.5 vision** | Hallucinated coords | BAA available | Cannot ground bounding boxes; pixel coords drift on small text — exactly the failure mode we can't ship |
| **GPT-4o vision** | Hallucinated coords | BAA via Azure OpenAI | Same hallucination problem; new vendor relationship adds friction |
| **Mistral OCR API** | Native bbox | EU vendor; weaker BAA posture | Strong fallback if Docling install pain bites; commit to migrate before real PHI |
| **Marker (open-source)** | Block-level bbox | Self-hosted | Weaker on noisy clinical scans than Docling |
| **MinerU** | Block bbox | Self-hosted | Heavier dependency footprint; less validated on US clinical forms |
| ✅ **Docling (IBM)** | Per-element bbox + page + table cells | Self-hosted | Built for scientific/clinical PDFs; native Pydantic response models; CPU-viable |

**Decision: Docling for OCR + layout, Haiku 4.5 for schema extraction.** Two stages, separate concerns, real bbox grounding.

### 2.4 Extraction verifier (Week-2 analog of Week-1 claim verifier)

The Week-1 verifier checks that every claim in the LLM's response is grounded in tool-retrieved records. Week-2 adds a **document-side analog**: after Haiku returns `{field: value, source_block_id}`, a deterministic check confirms `value` appears (with normalization) as a substring within the source block's text.

```python
def verify_extracted_field(field: ExtractedField, doc: DoclingDoc) -> bool:
    block = doc.block_by_id(field.source_block_id)
    if block is None:
        return False  # null/missing block_id → reject
    return normalize(field.value) in normalize(block.text)
```

Fields failing this check are **stripped or refused** the same way Week-1 claims are. If >30% of fields fail, the extraction itself is refused with `extraction_low_grounding`. Same atomic-strip discipline; new surface.

This satisfies the PRD's "schema, source links, **and verification strategy** must make unsupported extracted facts visible" requirement explicitly.

### 2.5 Tool signature note

PRD specifies `attach_and_extract(patient_id, file_path, doc_type)` "or equivalent." We use `attach_and_extract(patient_id, doc_ref_id, doc_type)` — the file is uploaded to OpenEMR FIRST and stored as a `DocumentReference`, then this tool is called with the reference ID. Reasons:

1. **Idempotency.** A `doc_ref_id` is content-stable across re-runs; a `file_path` is not (temp paths change). This makes the §2.5 idempotency key trivially correct.
2. **Audit trail.** The DocumentReference lifecycle (upload, who, when) is OpenEMR-native. Re-creating that audit chain inside the agent would be redundant.
3. **No file-handling in the agent.** The agent doesn't need to know about temp dirs, MIME validation, or upload size limits — those are OpenEMR's existing responsibilities.

The tool surface shape (3-arg signature returning strict-schema JSON) matches PRD intent.

### 2.6 FHIR `derivedFrom` contract

Every FHIR resource created from extraction carries a `derivedFrom` reference to the source `DocumentReference`. **Untraceable records are an architectural impossibility** because the schema requires the link:

```python
class ExtractedObservation(BaseModel):
    # ... lab fields ...
    derived_from: DocumentReferenceId  # NOT optional
    source_block_id: str               # bbox carrier
```

A reviewer can always answer "where did this fact come from?" by following `Observation.derivedFrom → DocumentReference → original PDF`. Plus the bbox via `source_block_id`.

### 2.7 Failure modes the architecture catches

| Failure | How caught |
|---|---|
| VLM hallucinates a field that's not in the document | Schema extractor must return `source_block_id` for every field. Haiku instructed to return `null` when no block matches; extraction verifier (§2.4) confirms `value` appears in the named block |
| Bounding box drifts off the actual text | Docling produces real coordinates; we never compute coordinates in the LLM pass |
| Document round-trip creates duplicate FHIR records | Idempotency key on `attach_and_extract` (hash of `doc_ref_id` + `doc_type`); re-running on same input returns existing resource IDs |
| Derived facts lose source link (untraceable) | `derivedFrom` is non-optional in the FHIR resource schema (§2.4); cannot persist a derived record without it |
| PHI leaks into Langfuse traces | Existing Week-1 PHI mask + outbound scrubber extends to extracted-fact payloads; document images never sent to Langfuse (only IDs + counts) |

---

## 3. Multi-Agent Graph

### 3.1 The graph

```
                          ┌──────────────────┐
                          │   SUPERVISOR     │
                          │ (Sonnet 4.5)     │
                          └────────┬─────────┘
                                   │ routes based on user intent + state
                ┌──────────────────┼──────────────────┐
                ▼                                     ▼
      ┌────────────────────┐                ┌─────────────────────┐
      │  intake-extractor  │                │ evidence-retriever  │
      │  (Haiku 4.5 +      │                │ (Haiku 4.5 +        │
      │   Docling layer)   │                │  hybrid RAG)        │
      ├────────────────────┤                ├─────────────────────┤
      │ Input:             │                │ Input:              │
      │  patient_id,       │                │  query, patient_ctx │
      │  doc_ref_id,       │                │ Output:             │
      │  doc_type          │                │  {chunks: [{        │
      │ Output:            │                │    chunk_id,        │
      │  validated Pydantic│                │    text, source,    │
      │  model + bbox      │                │    page, score      │
      │  citations         │                │  }]}                │
      └────────────────────┘                └─────────────────────┘
                                   │
                                   ▼
                          ┌──────────────────┐
                          │   SUPERVISOR     │
                          │ synthesizes →    │
                          │ verifier (Wk-1)  │
                          │ → response       │
                          └──────────────────┘
```

### 3.2 Supervisor responsibilities

The supervisor is a Sonnet-driven LangGraph node that:
1. Reads incoming user intent + current state
2. Decides which worker to call (or whether to return final answer)
3. Emits a routing decision span to Langfuse with:
   - Selected worker
   - Rationale (one-sentence justification)
   - Confidence score
4. Calls the worker
5. On worker return, decides: call another worker, retry, or synthesize final response
6. **Hard stop after 4 worker hops** — calculated as **2 workers × 2 round-trips before declaring stuck**. Anything more is loop-flavored, not progress. Named refusal reason `supervisor_max_hops` so operators can spot supervisor-loop patterns in `agent_log` aggregations.

### 3.3 Inspectability — what a Langfuse trace looks like

A reviewer can pull any `/chat` request's trace and reconstruct the full decision tree. Sample shape:

```
trace: chat_request_{request_id}
├── span: supervisor.decide  (rationale="user mentioned uploaded lab; need extraction first")
│   └── attribute: selected_worker = "intake-extractor"
├── span: worker.intake_extractor
│   ├── span: docling.layout  (n_blocks=42, n_pages=2)
│   ├── span: haiku.extract  (input_tokens=1834, output_tokens=512)
│   └── span: extraction_verifier  (n_fields=9, n_failed=0)
├── span: supervisor.decide  (rationale="extraction complete + answer requires guideline; route to evidence")
│   └── attribute: selected_worker = "evidence-retriever"
├── span: worker.evidence_retriever
│   ├── span: qdrant.hybrid_search  (n_candidates=50)
│   └── span: cohere.rerank  (top_k=5)
├── span: supervisor.decide  (rationale="have extraction + evidence; synthesize")
│   └── attribute: selected_worker = "FINAL"
└── span: synthesizer  (verifier_verdict=PASS, citations=[3 patient_record, 2 guideline, 1 extracted_document])
```

Every supervisor decision is a span with rationale text — black-box routing is structurally impossible.

### 3.4 Worker isolation

Each worker has a **strict input/output contract** (Pydantic models). Workers cannot call each other directly — only the supervisor routes. This makes every handoff a traceable artifact.

| Worker | Input schema | Output schema | Tool surface |
|---|---|---|---|
| `intake-extractor` | `ExtractRequest{patient_id, doc_ref_id, doc_type}` | `ExtractedDoc{fields, citations[], confidence}` | Docling layout + Haiku schema extraction + extraction verifier (§2.4) |
| `evidence-retriever` | `EvidenceRequest{query, patient_context}` | `EvidenceBundle{chunks[], total_score}` | Hybrid RAG (Qdrant native sparse+dense + Cohere rerank) |

### 3.5 Why LangGraph (vs alternatives)

| Framework | Verdict | Reason |
|---|---|---|
| ✅ **LangGraph** | Selected | PRD names it; Langfuse OOTB; supervisor patterns documented; explicit graph state |
| OpenAI Agents SDK | Rejected | Vendor lock to OpenAI; we use Anthropic; would need adapters |
| Pydantic AI | Rejected | Tempting given our Pydantic discipline, but smaller community; gambling on maturity |
| CrewAI | Rejected | Less inspectable; harder to trace handoffs; doesn't meet PRD's "explainable supervisor" requirement |
| Custom (extend Week 1) | Rejected | Faster to implement but loses the "framework" defense; reinventing supervisor patterns |

---

## 4. Hybrid RAG Design

> **Note:** Vector DB choice + final hybrid-vs-GraphRAG tradeoff documented in §6 (still under review at architecture-defense time; will be locked before MVP).

### 4.1 Retrieval pipeline

```
Query (from supervisor) → BM25 (keyword) ─┐
                       └──→ Dense vector ─┴──→ Top-50 candidates → Cohere Rerank → Top-5 → answer model
```

**BM25** catches exact-term matches (e.g., "metformin" or "GFR<60") that dense retrieval can dilute.
**Dense** catches semantic paraphrases (e.g., "blood sugar control" matching "glycemic management").
**Cohere Rerank** (or equivalent) re-orders the top-50 candidates by query-doc relevance — the single biggest quality lift per the PRD's recommendation.

### 4.2 Corpus

Small clinical-guideline corpus aligned to our existing UC1/UC2/UC3 use cases:
- USPSTF preventive-care recommendations (subset)
- ADA Standards of Care (diabetes — A1c targets, screening intervals)
- JNC8 / 2017 ACC-AHA hypertension guidelines
- Drug-interaction rules from `RULE_CORPUS.md` (Week-1 work, repurposed)

Target: 50–200 chunks at week-2 close. Indexed with a stable chunk_id so citations roundtrip.

### 4.3 Citation contract

Per PRD requirement, every retrieved chunk surfaces with:
```json
{
  "source_type": "guideline",
  "source_id": "ada-2024-standards",
  "page_or_section": "S2.3",
  "field_or_chunk_id": "ada-2024-s2-3-chunk-7",
  "quote_or_value": "The A1C target for most nonpregnant adults is <7%."
}
```

This shape extends Week 1's `lists:<id>` pattern — same idea, more fields, distinguishable from patient-record citations by `source_type`.

---

## 5. Eval Gate (the HARD GATE)

### 5.1 Suite shape

50 cases, organized by what they exercise. **Sub-shapes within each category are deliberately chosen to cover the PRD's named scenario challenges**: imperfect scan, incomplete patient record, follow-up question, FHIR roundtrip without duplication.

| Category | Cases | Hand-authored | Public benchmark | Sub-shapes covered |
|---|---|---|---|---|
| **Lab extraction** | 10 | 10 | 0 | 8 standard scans + **2 imperfect scans** (degraded OCR, partial extraction → tests low-confidence handling) + **1 idempotency case** (re-run `attach_and_extract` on same doc; assert no duplicate FHIR resources) |
| **Intake extraction** | 8 | 8 | 0 | 8 standard intake forms covering allergies / meds / family history per PRD schema |
| **Evidence retrieval** | 10 | 4 | 6 | 4 hand-authored RxNav-normalized drug+condition queries + **4 MedQA** (treatment-recommendation lookups) + **2 MedicationQA** (drug-info retrieval) |
| **End-to-end answer** | 10 | 5 | 5 | **2 incomplete-record cases** (sparse chart 999100 + intake form supplements the gap) + **2 follow-up question cases** (multi-turn against extracted doc) + 1 grounded-synthesis case + **5 MedQA full vignettes** (case → recommendation with citation) |
| **Safe refusal** | 6 | 6 | 0 | Out-of-scope query / unreadable scan / extraction confidence below threshold / no relevant evidence found / cross-patient lure / supervisor max-hops — each refusal returns a *named reason*, not a generic 'I cannot' |
| **No PHI in logs** | 6 | 6 | 0 | Adversarial fixtures with embedded SSN/phone/email/cross-patient identifiers; assert via regex on Langfuse trace export |
| **Total** | **50** | **39** | **11** | ~22% public-benchmark, 78% scenario-specific |

**Coverage of the PRD's three named challenges:**
- *"document scan is imperfect"* → 2 cases in Lab extraction + 1 in Safe refusal (extraction-confidence-below-threshold)
- *"patient record is incomplete"* → 2 cases in End-to-end answer (sparse chart + intake form)
- *"user asks a follow-up question"* → 2 cases in End-to-end answer (UC3 multi-turn against extracted doc)
- *"round-trip without untraceable records"* → 1 case in Lab extraction (idempotency) + the §2.4 `derivedFrom` contract is asserted on every end-to-end case

**Why MultiMedQA (MedQA + MedicationQA), not other components:**
- ✅ **MedQA** (USMLE vignettes): closest public proxy for "case → recommendation" — fits Evidence retrieval + End-to-end answer
- ✅ **MedicationQA**: drug retrieval — fits Evidence retrieval
- ❌ **PubMedQA**: research abstracts (different shape — literature, not chart)
- ❌ **HealthSearchQA / LiveQA / ConsumerQA**: consumer-shaped queries (consumer ≠ PCP)
- ❌ **MMLU clinical**: free-form medical knowledge MCQs (not patient-contextual reasoning)

Force-fitting the rejected components would erode rubric signal; we keep the public-benchmark slot tight and well-fit.

### 5.2 Boolean rubrics

Each case asserts on five boolean dimensions (per PRD):

| Rubric | Definition |
|---|---|
| `schema_valid` | Output passes Pydantic validation |
| `citation_present` | Every clinical claim has a non-null citation object |
| `factually_consistent` | Extracted/retrieved facts match expected values |
| `safe_refusal` | When refusal is the expected behavior, the agent refuses with the right named reason |
| `no_phi_in_logs` | Langfuse trace for this case has no SSN / phone / email / cross-patient identifiers |

### 5.3 CI gate

- Pre-commit hook runs the smoke subset (~10 cases, fixture mode, ~30s)
- Full 50-case suite runs in CI on every PR (live LLM, ~5 min, ~$0.30)
- Fails the build if any rubric category regresses by >5% or drops below pass threshold

**The grader will introduce a regression and confirm CI catches it.** The architecture is built around the assumption that boolean rubrics + structured-output judge are deterministic enough to make this gate trustworthy.

### 5.5 Hard Gate confidence — how we know the gate catches regressions

The PRD's grading explicitly tests the gate by introducing a regression. "We have a CI gate" is not enough; we need to defend that the gate is *sensitive* to the failure modes the rubrics name. Three confidence sources:

**1. Per-rubric meta-tests.** For each of the 5 rubric categories, we keep a small set of **deliberately-broken fixtures** that should always fail their respective rubric. If a meta-test passes, the rubric isn't actually checking what it claims to:

| Rubric | Meta-test fixture | Should always fail |
|---|---|---|
| `schema_valid` | Extraction output missing required field | ✓ |
| `citation_present` | Response with un-cited clinical claim | ✓ |
| `factually_consistent` | Cited record but wrong quoted value | ✓ |
| `safe_refusal` | Out-of-scope query that gets a confident answer | ✓ |
| `no_phi_in_logs` | Trace export containing SSN-shaped string | ✓ |

The meta-tests run *separately* from the 50 cases — they're a tripwire on the eval engine itself.

**2. Threshold sensitivity.** PRD requires fail on >5% category regression. We keep a baseline JSON committed to the repo (`agent/tests/eval/baseline.json`) recording per-category pass rates. CI compares each run against the baseline; a single category dropping from 100% to 92% (1 case fail in a category of 12) trips the threshold.

**3. Adversarial regression cases.** 6 of the 50 cases are deliberately designed to detect specific regression types — a prompt-cache misconfiguration, a verifier-strictness reduction, a worker-isolation breach (worker calling another worker directly), an extraction-verifier bypass, a citation-stripping bug, and a PHI-mask leak. Each case maps to a known regression class so we can defend "if the grader injects regression type X, this case fires."

**Defensible interview answer:** *"We test the gate with three layers — per-rubric meta-tests that confirm each rubric actually fires when broken, a baseline-comparison threshold that catches >5% regression in any category, and 6 adversarial cases targeting specific regression classes. The gate is itself part of the test surface, not just a check."*

### 5.6 Why boolean (not 1–10 LLM-as-judge)

A 1–10 score is a synthetic ranking that's hard to act on (is 7 a regression from 8?). Boolean rubrics force a clear yes/no per dimension, with the regression threshold (>5%) measurable against a baseline. Failures are immediately actionable: a `citation_present=false` case names a specific bug.

---

## 6. Vector DB & Retrieval Pattern

### 6.1 Vector DB choice

| Candidate | Hybrid (BM25+dense) | Filter expressivity | Ops complexity | Why not / why yes |
|---|---|---|---|---|
| Chroma local | DIY BM25 (rank_bm25) | Decent metadata filters | Very low | Loses a talking point — we'd hand-roll the PRD-named hybrid retrieval |
| pgvector | Native (PG full-text + vector, fused in SQL) | Best (SQL) | Adds Postgres sidecar (we run MariaDB) | New service purely to host vectors — hard to defend |
| Weaviate | Native hybrid (alpha-tunable) | GraphQL-shaped | Medium (more concepts) | Heavier than needed |
| Pinecone | Native hybrid | Good | Managed, external network | Vendor lock-in for a small corpus |
| LanceDB | Native hybrid (FTS + vector) | Good | Very low (embedded) | Newer; smaller community |
| FAISS | None — DIY everything | DIY | High for features | Not a DB |
| ✅ **Qdrant** | **Native sparse+dense + RRF in one query** | Strong typed payload filters | Low (single container) | Smallest service that natively implements the PRD's named retrieval pattern |

**Decision: Qdrant.** Native sparse+dense fusion in a single query means we don't hand-roll BM25; first-class payload filters cleanly express "limit to ADA-2024" scoping; one Docker container alongside the existing FastAPI/MariaDB stack; horizontal scaling path if the corpus 100×s.

### 6.2 Hybrid RAG vs GraphRAG (and why we rejected GraphRAG)

| Dimension | Hybrid RAG | GraphRAG |
|---|---|---|
| Best query shape | Lexical + semantic lookup over guideline prose ("what does ADA say about A1c in CKD") | Multi-hop synthesis across entities ("summarize themes across all CKD guidance") |
| Infra needed | Vector DB + reranker | Vector DB + LLM extraction pipeline + graph store + community detection |
| Build cost at 50–200 chunks | ~1 day, well-trodden | 3–5+ days; entity extraction quality unstable at small N |
| Citation contract roundtrip | **Maps cleanly** — chunk is the atomic unit, rerank preserves chunk IDs | **Breaks** — answers synthesized from community summaries lose direct chunk provenance |
| Eval rubric fit (`citation_present`) | Straightforward — every claim points to a chunk | Ambiguous — answers span graph summaries |
| Worth it when | Default for guideline/policy corpora <10k chunks | Corpus is large (1k+ docs), queries are global/thematic, relationships matter more than passages |

**Decision: Hybrid RAG (BM25 + dense + Cohere Rerank), GraphRAG rejected.**

We evaluated GraphRAG and rejected it for this corpus for three specific reasons:
1. **Citation contract violation.** Our PRD requires chunk-level citation roundtrip (`{source_id, page, chunk_id, quote}`). GraphRAG synthesizes from community summaries, which actively erode chunk-level provenance.
2. **Entity extraction error compounds at small N.** With 50–200 chunks, there's no statistical washout for misclassified entities; small extraction errors become large semantic errors in retrieval.
3. **Query distribution is local lookup, not global synthesis.** Physicians ask "what does the guideline say about X" — chunk-level retrieval is the right shape. GraphRAG shines when humans can't hold all docs in their head; not our regime.

**Revisit threshold:** ~5,000+ chunks OR query telemetry showing users asking cross-guideline thematic questions ("compare ADA and ACC-AHA on…").

### 6.3 Retrieval pipeline (final)

```
Query → Qdrant native hybrid (sparse + dense, RRF fusion) → top-50 candidates
       → Cohere Rerank (or BAAI/bge-reranker-v2 fallback)  → top-5
       → answer model with chunks + chart facts (separated citation types)
```

**One defensible upgrade beyond pure hybrid:** small entity-keyword index over drug names + condition names (ICD-10 / RxNorm) to boost recall on multi-condition queries. Cheap to add, materially helps queries like "metformin and lisinopril in patients with eGFR<60." Implementation: extra metadata field in the Qdrant payload, scored as a tiebreaker after rerank.

---

## 7. Schemas

### 7.1 LabReport

```python
class LabResult(BaseModel):
    test_name: str                   # e.g., "Hemoglobin A1c"
    value: float
    unit: str                        # e.g., "%"
    reference_range: str | None      # e.g., "<5.7"
    abnormal: bool | None
    collection_date: date | None
    source_block_id: str             # Docling block_id; carries bbox
    confidence: float                # extractor confidence 0-1

class LabReport(BaseModel):
    patient_id: int
    document_reference_id: str       # FHIR DocumentReference link
    results: list[LabResult]
    extraction_metadata: ExtractMeta # Docling + Haiku version, page count
```

### 7.2 IntakeForm

```python
class IntakeForm(BaseModel):
    patient_id: int
    document_reference_id: str
    demographics: Demographics       # name (allowlisted), dob, sex
    chief_concern: str | None
    current_medications: list[Medication]
    allergies: list[Allergy]
    family_history: list[FamilyHistoryItem]
    source_citations: dict[str, str] # field_name → source_block_id
    extraction_metadata: ExtractMeta
```

### 7.3 Citation (extends Week 1)

```python
class Citation(BaseModel):
    source_type: Literal["patient_record", "guideline", "extracted_document"]
    source_id: str                   # OpenEMR table:id, guideline chunk_id, or document_reference_id
    page_or_section: str | None      # for guidelines / multi-page docs
    field_or_chunk_id: str
    quote_or_value: str
    bbox: BBox | None                # populated for extracted_document citations
```

**The discriminator (`source_type`) is what keeps patient-record facts and guideline evidence separable** in the response. The answer-model prompt is structured to render each citation flavor distinctly:

> *"For each clinical claim, attach a citation object. Use `source_type='patient_record'` for facts from the chart (e.g., 'patient is on metformin 1000mg' → cites `prescriptions:573`). Use `source_type='guideline'` for clinical-guideline-derived recommendations (e.g., 'A1c target <7%' → cites `ada-2024-s2-3-chunk-7`). Use `source_type='extracted_document'` for facts derived from an uploaded lab/intake doc this session (e.g., 'today's eGFR is 47' → cites the `DocumentReference` + bbox). Never blur the three; if a fact draws on multiple sources, attach multiple citations."*

Eval rubric `citation_present` enforces; rubric `factually_consistent` cross-checks each cited record/chunk/extracted-field actually contains the quoted value.

---

## 8. Security & HIPAA Posture

### 8.1 Inheritance from Week 1

All Week-1 controls remain in force:
- HMAC + timestamp + CSRF for OpenEMR-to-agent boundary
- `agent_log` audit table with INSERT-only DB user
- Per-user rate limit + token budget
- Outbound PHI scrubber (cross-patient ID/SSN/phone/email/MRN)
- Patient ID from session, never from request body

### 8.2 Week 2 additions

| Surface | Risk | Control |
|---|---|---|
| **Document image → OCR** | PHI on the page goes to a vendor | Docling self-hosted; PHI never leaves the agent container at OCR stage |
| **Extracted fields → Anthropic** | PHI in the LLM extraction prompt | Existing Anthropic BAA path (same as Week 1 chart-context calls); document image sent only as text + bbox metadata, not raw pixels |
| **Document → Langfuse trace** | PHI in observability traces | Existing PHI mask + outbound scrubber extends to extracted payloads; document images never traced (only IDs + counts) |
| **Click-to-source overlay** | Rendered overlay shows PHI visually in browser (counts as PHI artifact) | Overlay rendering is **client-side only** — the browser draws the bbox highlight on the PDF locally; the server never produces a screenshot artifact, never sends a rendered overlay anywhere, and Langfuse only logs that the user clicked a citation (not the rendered result) |
| **Vector DB index** | PHI in chunk text | Corpus is public clinical-guideline content (USPSTF/ADA/JNC); no patient data in the corpus by design |
| **Supervisor routing decision** | Routing decision payload could include PHI | Routing decisions logged with patient_id reference only, not patient content |
| **Synthetic-data discipline** | Real patient data accidentally used in eval cases | All Week-2 document fixtures use the 999100-999199 sentinel range (extends Week-1 sentinel pattern). **Pre-commit hook greps eval YAMLs for `patient_id` values <999100 and fails the commit.** Synthetic-only is a tested property, not a documented intention |

### 8.3 The "no PHI in logs" rubric

The 6 eval cases dedicated to `no_phi_in_logs` use synthetic patients with embedded test identifiers (SSN-shaped strings, phone-shaped strings) and assert via regex against the Langfuse trace export that none of those identifiers appear in any span payload. This makes the PHI-scrubbing contract a *tested* property, not just a documented intention.

---

## 9. Tradeoffs & Risks

### 9.1 Decisions made under uncertainty

| Decision | What we picked | What we sacrificed | Why |
|---|---|---|---|
| Docling vs Mistral OCR vs VLM | Docling | Slightly higher install complexity | Self-hosted = best HIPAA posture; bbox quality on clinical docs; VLMs hallucinate coordinates |
| LangGraph vs custom vs OpenAI Agents SDK | LangGraph | New framework dependency | PRD names it; inspectable supervisor + Langfuse OOTB; Anthropic-friendly |
| Qdrant vs Chroma vs pgvector | Qdrant | Single new container | Native sparse+dense hybrid in one query; PRD's named retrieval pattern; Chroma would force hand-rolled BM25 |
| Hybrid RAG vs GraphRAG | Hybrid | Cross-document thematic synthesis | Citation contract requires chunk-level roundtrip (GraphRAG community summaries break it); entity extraction error compounds at small N; query distribution is local lookup |
| Boolean rubrics vs 1-10 LLM-as-judge | Boolean | Coarser-grained quality signal | Actionable failures; regression detection well-defined; PRD requires |
| Two-stage extraction (Docling+Haiku) | Two-stage | Higher latency than single-stage VLM | Real bbox grounding, hallucination-resistant by construction |

### 9.2 Top risks at architecture-defense time

| Risk | Likelihood | Mitigation |
|---|---|---|
| Docling install / GPU dependency bites the 2-hour MVP window | Medium | Mistral OCR API as documented fallback (1-day swap; same shape) |
| Cohere Rerank rate limits or pricing surprises | Low | BAAI/bge-reranker-v2 as open-source fallback; same interface |
| LangGraph state-tracking complexity exceeds week-2 budget | Medium | Start with 2 nodes (supervisor + 1 worker), add second worker after first round-trips |
| 50-case eval suite takes too long in CI | Low | Smoke subset (10 cases, fixture mode) for pre-commit; full 50 on PR only |
| Multi-agent failure modes not surfaced by current observability | Medium | Each handoff is a Langfuse span; supervisor decisions logged with rationale |

### 9.3 Week-1 carryover sequencing

Per the PRD's "compound, not pile up" principle: Week-1 carryover is tackled **only after Week-2 requirements are met**, with one exception (FHIR R4 read path is required for Week-2 ingestion roundtrip and gets done as part of Stage 1).

| Week-1 carryover item | Week-2 priority | Reason |
|---|---|---|
| FHIR R4 read path | **Required** | DocumentReference + Observation persistence is core to W2 ingestion |
| Replay Harness | Defer | Not on the critical path for W2 deliverables |
| Temporal-coherence verifier rule | Defer | Week-2 evals don't exercise it |
| Calendar-driven trigger | Defer | UX win; not load-bearing for defense |
| Cross-patient name detection | Defer | Already deferred per `DECISIONS.md §4a` |

---

## 10. Open Questions Going Into Defense

1. **Critic agent (extension, not core)** — included in design space but not built in MVP scope. Pattern: critic agent reviews the supervisor's final response and refuses if any clinical claim lacks citation OR proposes any unsafe action. Exists in §3 design but not on the critical path.
2. **Demo-data vs synthetic-data split** — week-1 used Synthea-imported patients (pid 1-200) + sentinel synthetic fixtures (999100-999999). Week-2 needs synthetic lab PDFs and intake forms; sentinel range extends naturally.
3. **Reranker provider** — Cohere Rerank as default; BAAI/bge-reranker-v2 as open-source fallback if rate limits or pricing surprise. Same interface either way.

---

*Document drafted as the architecture-defense backing reference. Slides at `.gauntlet/week2/architecture-defense-slides.md` cover the headline arc; this doc carries the depth for Q&A.*
