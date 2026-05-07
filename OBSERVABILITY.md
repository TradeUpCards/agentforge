# OBSERVABILITY.md — Langfuse Per-Node Funnel Reference

> **Related docs:** [`W2_ARCHITECTURE.md`](./W2_ARCHITECTURE.md) §3 (graph topology) · [`DECISIONS.md`](./DECISIONS.md) §2026-05-07 (model split + escalation rule) · [`EVAL_SUITE.md`](./EVAL_SUITE.md) §11 (endpoint dispatch + case routing)

**Purpose:** This file is the source-of-truth markdown reference for the per-node observability funnel required by PRD §7 and Decision #17. It documents Langfuse saved-view names and filter recipes for the `/graph_chat` trace set, the 7-field per-node attribute mapping, and a recipe for confirming the Sonnet escalation rate is within acceptable bounds.

**No PHI rule:** all examples in this file use synthetic placeholders (`<patient_id>`, `<trace_id>`, `<session_id>`). Do not substitute real patient identifiers, real query text, or real Langfuse trace IDs.

**Verification note:** Langfuse query syntax and saved-view configuration in this file has NOT yet been verified against a running Langfuse instance. Every query section is marked "Verify against the running Langfuse instance" until an operator confirms. The intent, filter logic, and attribute paths are correct per the Langfuse v3/v4 documentation; exact UI field names may differ by version.

---

## 1. Funnel definition

The `/graph_chat` per-node funnel tracks requests through each processing stage:

```
/graph_chat requests
    │
    ├──► intake_extractor     (worker node — Haiku 4.5)
    │
    ├──► evidence_retriever   (worker node — Haiku 4.5)
    │
    ├──► responder            (responder node — Haiku 4.5; Sonnet 4.6 escalation)
    │
    ├──► escalations          (traces where escalated_to_sonnet = true)
    │
    └──► refusals             (traces where final status = refused)
```

Each stage maps to a Langfuse span within the parent trace for the `/graph_chat` request. The supervisor's routing decisions also produce spans (see §2.4) but are not counted as a funnel stage — they are the connective tissue between stages.

---

## 2. Langfuse saved views and filter recipes

All queries below target traces with `name = "graph_chat_request"` (the trace name set by the `/graph_chat` handler after `graph.invoke()` completes). Langfuse flushes once per request at the end of the handler — see W2_ARCHITECTURE.md §3.2 Decision #8.

> Verify against the running Langfuse instance before relying on these filter recipes in production operations.

### 2.1 All /graph_chat requests

**Saved-view name (suggested):** `graph_chat_all`

**Filter recipe:**

| Filter field | Operator | Value |
|---|---|---|
| Trace name | equals | `graph_chat_request` |
| Environment | equals | `production` (or `development` for dev funnel) |

**Purpose:** baseline denominator for all funnel percentages. Count of traces = count of `/graph_chat` requests in the selected time window.

### 2.2 Requests that reached the intake_extractor node

**Saved-view name (suggested):** `graph_chat_intake_extractor`

**Filter recipe:**

| Filter field | Operator | Value |
|---|---|---|
| Trace name | equals | `graph_chat_request` |
| Span name (child) | contains | `worker.intake_extractor` |

**Purpose:** count and latency of requests where the supervisor routed to the intake-extractor worker. A drop in this count relative to total `/graph_chat` requests may indicate the supervisor is routing incorrectly.

### 2.3 Requests that reached the evidence_retriever node

**Saved-view name (suggested):** `graph_chat_evidence_retriever`

**Filter recipe:**

| Filter field | Operator | Value |
|---|---|---|
| Trace name | equals | `graph_chat_request` |
| Span name (child) | contains | `worker.evidence_retriever` |

**Purpose:** count and latency of requests where the supervisor routed to the evidence-retriever worker. Cross-reference with `retrieval_hits` metadata to confirm guideline chunks are being found.

### 2.4 Supervisor routing spans (inspection)

**Saved-view name (suggested):** `graph_chat_supervisor_decisions`

**Filter recipe:**

| Filter field | Operator | Value |
|---|---|---|
| Trace name | equals | `graph_chat_request` |
| Span name | contains | `supervisor.decide` |

**Purpose:** review supervisor routing rationale. Each `supervisor.decide` span carries the `selected_worker` and `rationale` attributes (W2_ARCHITECTURE.md §3.3). High volumes of `no_route` decisions indicate queries outside the agent's scope.

### 2.5 Requests that reached the responder node

**Saved-view name (suggested):** `graph_chat_responder`

**Filter recipe:**

| Filter field | Operator | Value |
|---|---|---|
| Trace name | equals | `graph_chat_request` |
| Span name (child) | contains | `responder` |

**Purpose:** count of requests that completed the supervisor → responder path (i.e., terminal states `answered` or `supervisor_max_hops`). The complement — requests that did NOT reach the responder — are `no_route` refusals.

### 2.6 Escalations — Sonnet 4.6 invoked

**Saved-view name (suggested):** `graph_chat_escalations`

**Filter recipe (metadata-based):**

| Filter field | Operator | Value |
|---|---|---|
| Trace name | equals | `graph_chat_request` |
| Metadata key `escalated_to_sonnet` | equals | `true` |

**Alternative (span-name-based, if metadata filtering is unavailable):**

| Filter field | Operator | Value |
|---|---|---|
| Trace name | equals | `graph_chat_request` |
| Span name (child) | contains | `claude-sonnet-4-6` |

**Purpose:** count escalations. The escalation rate = (escalation count / total `/graph_chat` count) × 100. This should remain below 5% in steady operation — see §4 for the full recipe and the revisit threshold.

### 2.7 Refusals

**Saved-view name (suggested):** `graph_chat_refusals`

**Filter recipe:**

| Filter field | Operator | Value |
|---|---|---|
| Trace name | equals | `graph_chat_request` |
| Metadata key `final_status` | equals | `refused` |

**Breakdown by reason:**

| Metadata key `refusal_reason` value | Meaning |
|---|---|
| `no_route` | Supervisor found no applicable worker for the query |
| `supervisor_max_hops` + verifier REFUSED | Reached 4-hop cap and responder also refused |
| `cost_ceiling_exceeded` | Per-document cost ceiling triggered |
| `outbound_phi_detected` | Responder outbound PHI gate fired |

---

## 3. 7-field per-node attribute mapping

Decision #14 (PRD §7) requires these 7 fields on every node's state patch. They surface in Langfuse as span metadata on the node's span.

| Field | Langfuse location | Trace attribute / metadata path | Notes |
|---|---|---|---|
| `node_name` | Span name | `span.name` | Set by the LangGraph node wrapper; e.g. `"worker.evidence_retriever"` |
| `latency_ms` | Span duration | `span.end_time - span.start_time` (ms) | Computed by Langfuse from span timestamps |
| `tokens_input` | Generation metadata | `generation.usage.input` | From Anthropic usage response; set by node on the generation object |
| `tokens_output` | Generation metadata | `generation.usage.output` | From Anthropic usage response |
| `cost_estimate_usd` | Generation metadata | `generation.usage.totalCost` | Langfuse calculates if model pricing is configured; alternatively derived from token counts × model price and set as `metadata.cost_estimate_usd` |
| `retrieval_hits` | Span metadata | `span.metadata.retrieval_hits` | Count of Qdrant chunks returned after rerank; 0 for nodes that do not retrieve (supervisor, responder) |
| `extraction_confidence` | Span metadata | `span.metadata.extraction_confidence` | Average per-field confidence from the extraction verifier; `null` for nodes that do not extract |

**How to query for a specific node's token usage:**

1. Open the Langfuse dashboard.
2. Navigate to Traces → filter by `graph_chat_request`.
3. Open any trace, expand the span tree, click the target node span (e.g. `worker.evidence_retriever`).
4. The Generations tab within the span shows `tokens_input`, `tokens_output`, and `totalCost`.

For aggregate queries across all traces, use the Langfuse Metrics dashboard or the API:

```
GET /api/public/metrics/daily
  ?traceName=graph_chat_request
  &spanName=worker.evidence_retriever
```

> Verify the `/api/public/metrics/daily` endpoint path and query parameters against the running Langfuse v4 instance. The filter parameter names (`traceName`, `spanName`) may differ in your version.

---

## 4. How to verify Sonnet escalation rate

This recipe answers: "Is the escalation rate for `/graph_chat` within acceptable bounds?"

**Acceptable bound:** below 5% of `/graph_chat` requests. Above 5% in a rolling 7-day window triggers the revisit threshold in DECISIONS.md §2026-05-07.

**Step-by-step:**

1. Open the Langfuse dashboard.
2. Set the time window to the last 7 days.
3. Run the `graph_chat_all` saved view (§2.1). Note the total trace count. Call this `N_total`.
4. Run the `graph_chat_escalations` saved view (§2.6). Note the count. Call this `N_escalated`.
5. Compute: `escalation_rate = N_escalated / N_total`.
6. If `escalation_rate > 0.05`, escalation is above threshold — review the escalated traces to determine whether the root cause is supervisor JSON quality, responder verifier strictness, or a specific query pattern.

**Alternative — Langfuse dashboard formula (if your version supports computed metrics):**

```
metric: count(traces where metadata.escalated_to_sonnet = true)
         / count(traces where name = "graph_chat_request")
time_window: 7d rolling
threshold_alert: > 0.05
```

> Verify formula syntax against the running Langfuse instance. Computed metric definitions vary by Langfuse version.

**What to do if escalation rate is high:**

- Check whether the supervisor's JSON output is consistently malformed on a specific query category (inspect `supervisor.decide` spans in escalated traces).
- Check whether the responder's first-attempt REFUSED verdict is correlated with a specific worker result shape (e.g., evidence-retriever returning 0 chunks).
- If the rate is persistently above 5%, re-evaluate the DECISIONS.md §2026-05-07 model-split decision — the bounded escalation may no longer provide a cost benefit relative to Sonnet-everywhere.

---

## 5. Saved-view configuration note

Saved views are configured in the Langfuse UI under Settings → Saved Views. This file is the source-of-truth for the **intended** filter logic; the UI saved views are the operational instantiation.

If a saved view drifts from the spec here (e.g., after a Langfuse upgrade renames a filter field), update the UI view to match this file — not the other way around.

**Do not embed Langfuse credentials, project keys, or organization IDs in this file.** Connection details belong in environment variables (`LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`).

---

## 6. Relationship to the eval gate

The per-node funnel in this document is an **operational observability reference**, not part of the CI eval gate. The CI gate runs via `scripts/run_eval_gate.py` (rubric regression) and `scripts/run_strip_rate_gate.py` (strip-rate regression) — see `EVAL_SUITE.md` §7 and §8.

The funnel becomes useful after the agent is running against real or live-fixture queries:

- **Pre-demo check:** run the `graph_chat_all` view to confirm `/graph_chat` traces are landing and spans are structured correctly.
- **Post-demo review:** check `graph_chat_escalations` to confirm no unexpected Sonnet escalation spikes during the demo session.
- **Week-3 onwards:** the funnel is the operational anomaly-detection layer — complement to the PR-blocking gate, which only runs in CI.
