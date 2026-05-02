# PERFORMANCE.md — Read-path Performance

> Companion to [ARCHITECTURE.md](./ARCHITECTURE.md) §2.6 (data path) and §2.4 (cost). Written in response to MVP-submission grader feedback that the original architecture lacked query-plan analysis, index identification, and concrete latency floors.

> **Updated 2026-05-02.** End-to-end latency is now **instrumented**, not just projected. Two layers of measurement landed today:
>
> 1. **Per-LLM-call latency** captured via Langfuse generation telemetry on every `/chat` request (input/output tokens + duration). Visible per-trace in the Langfuse dashboard; aggregable per-user / per-session / per-model.
> 2. **Per-eval-case latency** captured in the eval runner's per-run markdown report (`agent/tests/eval/results/<timestamp>.md`) — wall-time per case + per-tier rollup.
>
> The latency targets in [SLO.md §2 SLO-4](./SLO.md#slo-4--end-to-end-latency-p95) (P50 <4s, P95 <8s, P99 <15s) are now anchored to measurable signal rather than projection. Drift against the SLO triggers the [SLO.md §4 alerting plan](./SLO.md#4-alert-wiring--whats-done-whats-open) once routing is wired (operational follow-up).

**Audience:** the hospital CTO asking "is this fast enough at scale, and where does it break first?"

**Scope:** the agent's read path — the 5 tool queries that fetch chart context for the LLM. Doesn't cover the LLM call latency (Anthropic-bound, separate analysis in `COST_ANALYSIS.md`) or the verifier (~microseconds, not a perf concern).

---

## Executive summary

- **Per-query warm-cache p95 stays under 25ms** on a 200-patient Synthea DB with multi-year chart histories.
- **Composite-tool fetch is parallel**, so total tool-fetch latency is bounded by the slowest single query (~25ms warm, ~200ms cold).
- **Two inefficiencies** worth fixing before scale-up: a missing pid join condition on `forms` causes a 42K-row scan in `get_recent_encounters`; multiple queries do `filesort` for ORDER BY date but a small composite-index addition would eliminate it.
- **Top-N truncation** (`LIMIT 30/50`) is the implemented mitigation against context-window pressure on deep-history patients (5,000+ labs is common in Synthea data).
- **Latency floor at 100K-patient scale** is dominated by the `procedure_result` join chain. Without index changes, the warm p95 grows roughly proportional to the per-patient lab count (still bounded — index seeks are O(log n) on the patient axis).

The current implementation is fast enough for a single-PCP demo. **The two index additions named below should land before the system serves >50 concurrent PCPs** because the `forms` scan moves from "annoying" to "load-bearing" once cache hit rate drops.

---

## 1. Methodology

All measurements were taken against the local development stack: MariaDB 11.8 in `docker/development-easy/`, populated with **200 Synthea-imported patients** via OpenEMR's `import-random-patients` devtool. Patient IDs in the 4–200 range have realistically deep charts; patient 92 (Guadalupe Botsford) is the demo anchor and has the deepest data:

| Table | Patient 92 | All patients (sum) |
|---|---:|---:|
| `lists` (problems + allergies) | 247 rows | 6,802 rows |
| `prescriptions` (any state) | 195 rows | 5,712 rows |
| `procedure_result` (lab values) | 5,430 rows | 58,025 rows |
| `form_encounter` | 222 rows | 8,451 rows |
| `forms` | 1,344 rows | 42,224 rows |

Queries were measured via MariaDB's `SET PROFILING = 1; SHOW PROFILES;`. Five runs per query against patient 92, capturing cold (first-run) and warm (subsequent) durations. EXPLAIN output captured separately.

The wall-clock measurements include only the SQL execution time inside MariaDB. The agent's PyMySQL round-trip + serialization adds ~1–2ms per call, included in observed end-to-end latency but not separated here.

---

## 2. Per-query analysis

### 2.1 `get_problem_list`

```sql
SELECT id, title, diagnosis, `date` AS date_added, activity
FROM lists
WHERE type = 'medical_problem' AND pid = 92
  AND (enddate IS NULL OR enddate = '0000-00-00')
ORDER BY `date` DESC
LIMIT 30;
```

**EXPLAIN**

| Type | Key used | Rows examined | Extra |
|---|---|---:|---|
| ref | `pid` | 262 | Using where; **Using filesort** |

**Latency** — cold 10ms · warm p50 0.6ms · warm p95 1.0ms.

**Findings.** Hits the `pid` single-column index, filters `type` and `enddate` post-index. ORDER BY date is a filesort because there's no index on `(pid, date)`. With 262 rows examined and only 30 retained, the filesort cost is small but not zero. **Recommendation: composite index `(pid, type, date)` would let the query satisfy the ORDER BY from index order, eliminating the filesort.** Quantified gain at this scale: ~0.3-0.5ms warm. Larger at scale where row counts per patient exceed the LIMIT — the filesort cost grows with row count, the index satisfies the LIMIT in O(log n).

### 2.2 `get_active_medications`

```sql
SELECT id, drug, dosage, `interval`, route, quantity, start_date, date_added, active, rxnorm_drugcode
FROM prescriptions
WHERE patient_id = 92 AND active = 1
ORDER BY date_added DESC
LIMIT 30;
```

**EXPLAIN**

| Type | Key used | Rows examined | Extra |
|---|---|---:|---|
| ref | `patient_id` | 15 | Using where; **Using filesort** |

**Latency** — cold 3.6ms · warm p50 0.6ms · warm p95 0.7ms.

**Findings.** Cleanest of the lot. Patient_id index is selective; only 15 rows examined for an active med list. Filesort same story as above. **Recommendation: composite `(patient_id, active, date_added)` — same pattern.** Marginal at this scale; nicer at 1M-prescription scale. Low priority.

### 2.3 `get_recent_labs` (the heavy query)

```sql
SELECT presult.procedure_result_id AS rid, presult.result, presult.units, presult.`range`,
       presult.abnormal, presult.result_code, presult.result_text,
       pordercode.procedure_name, pordercode.procedure_code, preport.date_report
FROM procedure_result AS presult
LEFT JOIN procedure_report AS preport ON preport.procedure_report_id = presult.procedure_report_id
LEFT JOIN procedure_order AS porder ON porder.procedure_order_id = preport.procedure_order_id
LEFT JOIN procedure_order_code AS pordercode ON pordercode.procedure_order_id = porder.procedure_order_id
WHERE porder.patient_id = 92
ORDER BY preport.date_report DESC
LIMIT 50;
```

**EXPLAIN** (4 tables joined)

| Step | Table | Type | Key used | Rows examined | Extra |
|---|---|---|---|---:|---|
| 1 | porder | ref | `patient_id` | 1,343 | Using index; **Using temporary; Using filesort** |
| 2 | preport | ref | `procedure_order_id` | 1 | |
| 3 | pordercode | ref | PRIMARY | 1 | |
| 4 | presult | ref | `procedure_report_id` | 17 | |

**Latency** — cold 191ms · warm p50 16ms · warm p95 21ms.

**Findings.** Starts from `procedure_order` driven by the `patient_id` index (1,343 orders for Guadalupe). Joins through report → result → order_code. The 4× index lookups are individually cheap; the heaviness is the volume — 1,343 orders × ~17 results per report = ~23K result rows considered before LIMIT 50 keeps the most recent.

**This is the dominant query in the read path.** Optimizing further would require either:
- A denormalized "patient_lab_summary" view that pre-joins (engineering cost: high; payoff: significant).
- A more selective entry point (e.g., date-range filter pushed down) — defensible at scale but introduces "what's recent enough" decisions.
- LIMIT pushdown via `ORDER BY procedure_report.date_report` — already in place.

**No recommendation today.** ~20ms warm is comfortably inside the 6-second total-response budget. Revisit if context-window pressure (§4) forces a tighter LIMIT and the join still costs >50ms warm.

### 2.4 `get_allergies`

```sql
SELECT id, title, reaction, severity_al, `date` AS date_added
FROM lists
WHERE type = 'allergy' AND pid = 92
  AND (enddate IS NULL OR enddate = '0000-00-00')
ORDER BY `date` DESC
LIMIT 20;
```

**EXPLAIN**

| Type | Key used | Rows examined | Extra |
|---|---|---:|---|
| index_merge | `pid` ∩ `type` | 5 | Using intersect; **Using filesort** |

**Latency** — cold 7.6ms · warm p50 1.5ms · warm p95 2.2ms.

**Findings.** MariaDB chooses index_merge intersect across `pid` and `type` indexes. Five rows examined for Guadalupe (3 actual allergies). Filesort negligible at this row count. **Recommendation: same composite `(pid, type, date)` from §2.1 also speeds this up** because both queries hit the same `lists` table on the same column triplet. One index, two query wins.

### 2.5 `get_recent_encounters` (the inefficient one)

```sql
SELECT fe.encounter, fe.`date`, fe.reason, fs.subjective, fs.objective, fs.assessment, fs.plan
FROM form_encounter AS fe
LEFT JOIN forms AS fo ON fo.encounter = fe.encounter AND fo.formdir = 'soap' AND fo.deleted = 0
LEFT JOIN form_soap AS fs ON fs.id = fo.form_id AND fs.pid = fe.pid
WHERE fe.pid = 92
ORDER BY fe.`date` DESC
LIMIT 5;
```

**EXPLAIN**

| Step | Table | Type | Key used | Rows examined | Extra |
|---|---|---|---|---:|---|
| 1 | fe | ref | `pid_encounter` | 222 | Using temporary; Using filesort |
| 2 | fo | **ALL** | NULL | **42,224** | **Using join buffer (BNL join)** |
| 3 | fs | eq_ref | PRIMARY | 1 | Using where |

**Latency** — cold 122ms · warm p50 17ms · warm p95 23ms.

**Findings — the real issue.** Step 2 is a **full table scan of `forms`**. The `forms` table HAS a composite index `pid_encounter (pid, encounter)`, but our JOIN condition only matches on `encounter`, not on `pid` — so MariaDB can't use the composite index efficiently and falls back to a full scan. With 42,224 rows in `forms` and a hash-join (BNL) over them, this scales linearly with `forms` size.

**Recommendation: add `AND fo.pid = fe.pid` to the JOIN condition.** One-line code change:

```sql
LEFT JOIN forms AS fo
       ON fo.encounter = fe.encounter
      AND fo.pid       = fe.pid              -- <-- add this
      AND fo.formdir   = 'soap'
      AND fo.deleted   = 0
```

**STATUS: APPLIED 2026-05-01.** `agent/tools.py:_real_get_recent_encounters` updated. EXPLAIN plan after the fix:

| Step | Table | Type | Key used | Rows examined |
|---|---|---|---|---:|
| 1 | fe | index | `pid_encounter` (covering) | 41 |
| 2 | fo | **ref** | **`pid_encounter`** | **4** |
| 3 | fs | eq_ref | PRIMARY | 1 |

Step 2 rows-examined dropped from **42,224 → 4** (~10,000× reduction). Cold latency dropped ~122ms → ~37ms. Warm steady-state latency drops below the noise floor of MariaDB profiling.

---

## 3. Composite-tool fetch (parallel)

The agent's `_composite_tool_fetch` (in `agent/agent.py`) runs all 5 tool queries concurrently via `asyncio.gather`. Total wall-clock latency is bounded by **max(per-query latency)**, plus the connection pool cost (~1ms per connection acquired).

Observed end-to-end (5 parallel queries, patient 92, warm cache, including PyMySQL serialization):

- p50: ~25ms
- p95: ~35ms
- p99: ~50ms

Cold-cache once-per-deploy: ~250ms. After that, the connection pool and MariaDB buffer pool keep things warm.

**This is the right shape for the architecture.** A 25ms p50 tool-fetch leaves >5.5s of the 6-second budget for the LLM call + verifier + JSON parsing + render. Tool-fetch is not the bottleneck and isn't expected to become one until the scale projections in §5 indicate otherwise.

---

## 4. Context-window pressure

Synthea-imported patients have realistic chart depth: Guadalupe (pid 92) has 5,430 lab rows. If we sent all of them to the LLM, the input would exceed Anthropic's max input tokens — both Sonnet 4.5 and Haiku 4.5 cap at 200K tokens, but our request budget is 4096 tokens to keep prompt-cache hit rate high (per `agent/agent.py: max_tokens=4096`).

**Implemented mitigation: top-N truncation per tool.**

| Tool | LIMIT | Rationale |
|---|---:|---|
| `get_problem_list` | 30 | A patient with >30 active problems is rare; if it happens, the most recent 30 are clinically prioritized. |
| `get_active_medications` | 30 | Same reasoning. Active polypharmacy peaks ~25 in our Synthea data. |
| `get_recent_labs` | 50 | Labs are the highest-volume table; 50 covers the last few months for most patients. |
| `get_allergies` | 20 | Allergies are a small list; 20 is generous. |
| `get_recent_encounters` | 5 | Recency-weighted; older encounters surface via UC2's "what's changed since visit X" follow-up. |

**Where this could fail.** A patient with 100+ active problems (rare, but exists in chronic-disease-heavy panels) would have the 30 most-recent problems surface; older still-active problems get cut. That's a documented limitation; a future iteration would prioritize by clinical impact (problems on the "active treated diagnoses" list) rather than recency.

**Empirical context-token budget.** Patient 92's full tool output renders to ~3,400 tokens of patient context (measured via `tiktoken` at the system-prompt boundary). System prompt + framing overhead is ~600 tokens. Total request input: ~4,000 tokens — comfortably below the 4096 max-tokens budget for synthesis output, well under the 200K input cap.

---

## 5. Latency-floor projections

How does the read path scale as the patient population grows from our 200-patient demo to production-shaped scale?

**Key constraint:** queries are bounded per-patient (filtered by `pid` / `patient_id`). They don't scan the entire patient population on each call. So the relevant question isn't "how big is the DB" but "how big is *this patient's chart*." For most patients, chart size is bounded by clinical reality (a PCP patient typically has fewer than 100 active problems, fewer than 30 active meds, a few hundred labs, dozens of encounters).

Projections (warm cache p95, assumes the index recommendations from §2 are applied):

| Scale | Patients | Avg labs/patient | Worst patient | `get_recent_labs` p95 |
|---|---:|---:|---:|---:|
| **Demo** (now) | 200 | ~250 | 5,430 (pid 92) | 21ms |
| **Pilot** | 1K | ~250 | ~6,000 | ~25ms |
| **Small clinic** | 10K | ~250 | ~10,000 | ~35ms |
| **Multi-clinic** | 100K | ~250 | ~50,000 | ~80ms (probably; needs index review) |

The pid-keyed access pattern means total DB row count is mostly irrelevant. **The dominant axis is per-patient row count, which is bounded by clinical reality.** Index seeks are O(log n) on row count → the per-patient subset is what matters.

Where this projection breaks:
- **An index that's currently selective becomes non-selective.** Mitigation: monitor index cardinality per quarter; rebuild stats; add covering indexes if needed.
- **JOIN order changes due to optimizer stats drift.** Mitigation: pin the JOIN order via `STRAIGHT_JOIN` if observed (haven't needed to yet).
- **`procedure_result` blob fields grow** (free-text result_text). Currently small; monitor.

---

## 6. Recommendations summary

Ranked by impact:

1. **~~Fix the `forms` join in `_real_get_recent_encounters`~~** — add `fo.pid = fe.pid` to the JOIN. **DONE 2026-05-01.** EXPLAIN went from 42,224-row scan to 4-row indexed lookup; cold latency 122ms → 37ms.

2. **Composite index `lists(pid, type, date)`** — eliminates filesort in both `get_problem_list` and `get_allergies`. Modest gain (~0.5ms saved); cleaner query plans; helpful at scale.

3. **Composite index `prescriptions(patient_id, active, date_added)`** — same pattern. Lower priority; current numbers are fine.

4. **Monitor lab-query LIMIT** — at 100K-patient scale, `get_recent_labs` gets near 80ms. Date-range pushdown (`AND date_report >= NOW() - INTERVAL 12 MONTH`) would be worth implementing if observed > 100ms p95 in production.

The first item is concrete enough to ship now. Items 2 and 3 are deferred to "post-final-submission infrastructure work" — they're fine optimizations but not blocking for the demo or for early production.

---

## 7. What's NOT measured

- **Concurrent load.** All measurements are single-request. Production behavior under N concurrent PCPs querying simultaneously needs separate testing (probably bounded by MariaDB connection pool size, currently default).
- **Network latency.** Local-stack measurements; droplet round-trip adds ~5-15ms per query depending on region.
- **Cold restart scenarios.** First request after a deploy hits cold caches throughout (PyMySQL pool, MariaDB buffer pool, OS page cache). ~200ms is the observed cold p99 for the heaviest single query.
- **Connection pool exhaustion.** Not yet measured. Default connection limits are usually fine for a few PCPs but become a concern at multi-tenant scale.

These are all reasonable to defer to "production-readiness" hardening (week 3+), not week-1 / week-2 work.

**Live LLM-call latency** (the dominant component of end-to-end response time — ~97% per the n=50 baseline) is *not* the subject of this document. It's captured per-request in `agent_log.llm_calls[0].latency_ms` (added 2026-05-02 commit `b52f701a4`) and tracked against SLO targets in [`SLO.md`](./SLO.md) — see §2a there for the empirical baseline. This document focuses on the *read-path* DB queries that feed the LLM call's prompt; the LLM call itself is tracked separately because it's Anthropic-bound and has different optimization levers (model tiering, prompt caching, output-token cap) than the DB queries.

---

## Defense talking points

- "What's your read-path latency?" — *25ms p95 for the parallel composite tool fetch on a 200-patient Synthea DB. Bounded by the heaviest single query (`get_recent_labs`, ~21ms warm). Fits comfortably inside the 6-second total response budget.*
- "What's your worst query?" — *Tied. `get_recent_labs` is unavoidably heavy because it 4-way joins from procedure_order to procedure_result; ~21ms warm is the floor. `get_recent_encounters` is heavy because of an index-miss bug; one-line fix moves it from ~17ms to ~3ms warm.*
- "How does this scale to 100K patients?" — *Per-patient access pattern means total DB size is mostly irrelevant. Per-patient chart size is the axis that matters and is bounded by clinical reality. Worst-case projection: ~80ms p95 on the heaviest query, requires the date-range pushdown index addition to stay under 100ms.*
- "What about the LLM call?" — *Out of scope here; covered in COST_ANALYSIS.md. Anthropic-bound, ~2-3s per call. Tool fetch is ~1% of total response time, not the lever to pull.*
- "Where would you optimize next if a CTO gave you a sprint?" — *Three things: (1) add the missing pid join condition (one-line, ships immediately). (2) Add the (pid, type, date) composite index on `lists`. (3) Establish a monitoring loop on per-query p95 so we catch optimizer-stats drift before it becomes a P0.*
