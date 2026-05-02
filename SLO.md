# SLO.md — Service-Level Objectives + Alerting Plan

> **Related docs:** [`ARCHITECTURE.md`](./ARCHITECTURE.md) §5 (observability) · [`DECISIONS.md`](./DECISIONS.md) §8 (custom metrics, alert thresholds) · [`PERFORMANCE.md`](./PERFORMANCE.md) (latency floors that anchor the latency SLOs) · [`COST_ANALYSIS.md`](./COST_ANALYSIS.md) §3.1 (cache-rate kill-switch threshold) · [`EVAL_SUITE.md`](./EVAL_SUITE.md) (correctness gates orthogonal to runtime SLOs)

**Audience:** the hospital CTO asking *"when does this page someone, and when is it just a ticket?"* and the on-call engineer asking *"what numbers actually mean the agent is failing?"*

**Status:** SLO definitions + thresholds documented (this file). Alert wiring (Langfuse → PagerDuty / Opsgenie / email) is operational follow-up — see §4. Until that's wired, monitoring is human-driven from the Langfuse dashboard.

---

## 1. Why SLOs

The 2026-05-02 system-architecture-review named this as a production blocker:

> *"Custom metrics (`verifier_verdict`, `citation_match_rate`, `prompt_cache_hit_rate`) already exist; thresholds + paging do not. Practical risk: when verifier pass-rate drops mid-shift, no one knows until a clinician complains."*

The metrics are already instrumented in `agent/agent.py` (per-request span metadata) + `agent/llm_client.py` (LLM-call telemetry). The gap is **named thresholds + alert routing**. This doc closes the threshold gap and names the alert routing for whoever wires it.

We frame these as SLOs (objectives) rather than SLAs (contracts) — week-1 has no signed customer agreement. The thresholds are calibrated to the demo droplet's single-VPS deployment; tighter targets land at Tier 2+ (per [COST_ANALYSIS.md §5](./COST_ANALYSIS.md#5-tier-2--1k-pcps-multi-clinic-network)).

---

## 2. The five SLOs

Each SLO has: definition, target, error budget, page-vs-ticket trigger, signal source.

### SLO-1 — Agent Availability

**Definition:** Fraction of `/chat` requests returning HTTP 200 with a parseable JSON body (status `ok` OR status `refused`). Both are "the agent did its job and reported back"; only HTTP 5xx + body-parse failures are the failure mode.

| Field | Value |
|---|---|
| **Target** | 99.5% over rolling 30 days |
| **Error budget** | 0.5% = ~3.6 hours of allowed unavailability per month |
| **Page** | Burn rate >14× (consuming 30-day budget in 24h) — implies a hard outage |
| **Ticket** | Burn rate 1×–14× (consuming budget faster than nominal but not catastrophic) |
| **Signal source** | FastAPI access log + Langfuse trace status field |

**Note on the 99.5% target.** This is intentionally loose for week-1 single-droplet deployment — one host reboot consumes a meaningful chunk of the budget. Tier 2+ deployments behind a load balancer with multiple agent instances should target 99.9% (~43m/month).

### SLO-2 — Verifier Pass Rate

**Definition:** Fraction of `/chat` requests where the verifier verdict is `pass` or `partial_strip` (i.e., the agent produced a usable response, even if some claims were stripped). Verdict `refused` is the failure mode.

| Field | Value |
|---|---|
| **Target** | ≥95% over rolling 7 days |
| **Error budget** | 5% refusal rate is the headroom |
| **Page** | Verifier pass-rate <85% over any rolling 1-hour window with ≥20 traces — implies LLM regression or data corruption |
| **Ticket** | Verifier pass-rate 85-95% over rolling 7 days — investigate within 1 business day |
| **Signal source** | Langfuse span metadata `verifier_verdict` (already emitted per `agent/agent.py:run_chat`) |

**Why this matters:** refusals aren't bad in isolation — the verifier is *supposed* to refuse when claims can't be grounded. But a sustained high refusal rate signals one of: the LLM model changed under us, the rule corpus expanded too aggressively, or a data-quality regression in the underlying chart records.

### SLO-3 — Citation Match Rate

**Definition:** Fraction of LLM-emitted claims that pass verifier matching (per request). Reported as `citation_match_rate` in span metadata.

| Field | Value |
|---|---|
| **Target** | ≥85% rolling 7-day average across all requests |
| **Error budget** | 15% claim-strip rate is the headroom (verifier can still produce a partial-strip response under the 30% atomic-strip threshold) |
| **Page** | Rolling 24h average <70% — likely an LLM regression worth waking someone for |
| **Ticket** | Rolling 7-day average 70-85% — may signal prompt drift, schema changes, or LLM version change |
| **Signal source** | Langfuse span metadata `citation_match_rate` |

### SLO-4 — End-to-End Latency (P95)

**Definition:** 95th percentile time from `/chat` request received to response sent. Excludes browser-side render time (which is captured separately as `visual_render_latency_ms` via the score endpoint).

| Field | Value |
|---|---|
| **Target P50** | <4 seconds |
| **Target P95** | <8 seconds |
| **Target P99** | <15 seconds |
| **Error budget** | The 90-second clinical window from [USERS.md](./USERS.md) is the absolute ceiling; P95 <8s leaves ~80s of headroom for browser render + clinician parse time |
| **Page** | P95 >15s sustained for 10 minutes — implies the agent is unusable in the clinical workflow |
| **Ticket** | P95 8-15s for any rolling hour — investigate; may be Anthropic API latency, DB pressure, or cache-miss spike |
| **Signal source** | Langfuse trace `latency` field (auto-computed) |

The latency targets are anchored in [PERFORMANCE.md](./PERFORMANCE.md)'s tool-fetch latency floors (~37ms cold for the deepest patient context) plus typical Anthropic Sonnet generation latency (~3-6s for a 600-token response). Anything above 8s P95 is dominated by Anthropic-side variance, not our code.

### SLO-5 — Tool Failure Rate

**Definition:** Fraction of tool invocations (`get_problem_list`, `get_active_medications`, etc.) that return `success: false`. Reported in `tools_called[].success` per request.

| Field | Value |
|---|---|
| **Target** | <1% over rolling 7 days |
| **Error budget** | 1% failure rate accommodates intermittent DB connection timeouts |
| **Page** | >5% rolling 1-hour window OR any single tool at 100% failure for 5+ consecutive requests — implies DB outage or schema regression |
| **Ticket** | 1-5% over rolling 24h — investigate persistent slow queries or connection pool pressure |
| **Signal source** | Langfuse span metadata; `tools_called[]` array per request |

---

## 2a. Empirical baseline (2026-05-02, n=50 live calls)

The targets above are aspirational; this section records the **actual measured behavior** at week-1 close so future drift is detectable. Data: 50 live Anthropic Haiku 4.5 calls against the 5 LLM-invoking smoke-tier eval cases (10 iterations each). Spend: $0.26.

| SLO | Target | n=50 baseline | Headroom |
|---|---|---|---|
| **SLO-1 Availability** | ≥99.5% / 30d | 100% (50/50 returned `outcome='success'`) | n too small for availability — rebaseline after ≥1 week of production traffic |
| **SLO-2 Verifier pass rate** | ≥95% / 7d | 100% (0/50 hit the 30% atomic-strip threshold) | substantial — full headroom against target |
| **SLO-3 Citation match rate** | ≥85% / 7d avg | 100% (every claim passed verification on every call) | substantial — full headroom |
| **SLO-4 End-to-end p95** | <8s | **5413ms** | ~32% headroom; comfortably under |
| **SLO-4 End-to-end p99** | <15s | 5727ms | ~62% headroom |
| **SLO-4 End-to-end median** | <4s | 4990ms | **slightly OVER target by ~1s** ⚠️ |
| **SLO-5 Tool failure rate** | <1% / 7d | 0% (all 250 tool calls succeeded — 5 tools × 50 requests) | substantial |

**Sub-metric breakdown for SLO-4** (LLM call dominates):

| Stage | avg | p95 | % of total |
|---|---:|---:|---:|
| Anthropic LLM call (`llm_calls[0].latency_ms`) | 4820ms | 5254ms | ~97% |
| Tool fetch + verifier + audit-write | 143ms | ~250ms | ~3% |
| **Total (`total_latency_ms`)** | **4963ms** | **5413ms** | 100% |

The LLM call is essentially the entire latency. Tool fetch + verifier + audit-write together are noise. This concentrates the optimization lever on Anthropic-side performance: prompt caching, model selection, output token cap.

**One concerning observation:** the median at 4990ms is **just over** the documented <4s P50 target. n=50 may not be enough to draw a strong conclusion (smoke-tier shape is one of the lighter shapes the system handles), but this is the closest existing numbers come to *missing* a target. Worth re-baselining once the cache anomaly in §3 is resolved — explicit caching alone could drop median latency 30-50% according to Anthropic's documented prompt-cache savings.

**Where the baseline lives:**
- Raw rows: `agent_log` table, `outcome='success' AND created_at BETWEEN '2026-05-02 21:26' AND '21:32'`
- Per-LLM-call latency: `JSON_EXTRACT(llm_calls, '$[0].latency_ms')` — populated for every request going forward (added 2026-05-02 commit `b52f701a4`)
- Per-eval-case latency: surfaces in `agent/tests/eval/results/<timestamp>.md` `## Latency` section on every run

---

## 3. The cost-side guardrail (operational metric, not a hard SLO)

### Prompt cache hit rate

Not an SLO in the traditional sense — caching is a cost optimization, not a correctness or availability concern. But it's the single biggest cost lever (per [COST_ANALYSIS.md §3.1](./COST_ANALYSIS.md#31-measurement-driven-kill-switch-pilot-decision-rule)) and has a documented kill-switch threshold worth wiring an alert on.

| Field | Value |
|---|---|
| **Tracked metric** | `cache_read_input_tokens / (cache_read_input_tokens + cache_creation_input_tokens)` per LLM generation, rolling 24h |
| **Healthy** | ≥25% — explicit caching is a net cost win |
| **Watch** | 15-25% — near break-even; monitor weekly |
| **Kill-switch trigger** | <15% sustained for 7 days — disable explicit caching per [DECISIONS.md §4a callout](./DECISIONS.md) decision rule |
| **Signal source** | Langfuse generation `usage` field (auto-emitted from Anthropic SDK) |

This isn't a paging condition — it's a weekly review item. A persistent <15% over a week triggers a one-line config change (drop the `cache_control` key in `agent/agent.py:run_chat`). Documented in DECISIONS.md as a designed-in kill switch, not a regression.

**⚠ Observed 2026-05-02 (n=50): cache hit rate is 0%.** Every one of 50 live calls came back with `cache_read_input_tokens=0`. The 50 calls were: 5 different smoke-tier cases × 10 iterations, all using identical patient context (Maria fixture, patient_id=1) and identical "Generate a pre-visit brief" message, with iteration-to-iteration spacing well under the 5-minute cache TTL. Under the documented design (`cache_control: ephemeral` on the patient-context system block per `agent/agent.py:run_chat`), iterations 2-10 of any case should have hit the cache. None did.

This means the kill-switch threshold is **already triggered** under the current cache configuration — but it's not yet a kill-switch decision because the root cause hasn't been investigated. Three plausible explanations, each pointing to a different fix:

1. **Min-cacheable-prefix threshold not met.** Anthropic Haiku 4.5 requires ≥1024 tokens of prefix before caching activates. The static system block alone may be under that threshold; if so, the breakpoint placement in `agent/agent.py` needs to move *down* the prompt to capture more prefix.
2. **Per-request variation in the prefix.** Some field (timestamp, request_id, session_id) might be ending up in the cached prefix when it shouldn't. The fix is identifying the variant field and excluding it.
3. **Anthropic-side fingerprinting.** Less likely but possible — some property of how the SDK constructs the request (e.g., `cache_control` being applied with the wrong `type` value) silently disables caching.

**Investigation cost:** ~30 minutes to inspect one Langfuse trace and verify which of the three is the actual cause. Worth doing **before** considering the kill switch, because the cost-economics in [COST_ANALYSIS.md](./COST_ANALYSIS.md) assume cache savings are real. If the cache *can* work and we just have a misconfiguration, fixing it is much higher leverage than killing it.

Filed as a P2 finding from the n=50 baseline measurement. Not blocking SLO targets above (SLO-4's p95 is well under target without cache), but it's load-bearing for the cost projections.

---

## 4. Alert wiring — what's done, what's open

### What's done (the threshold layer)

- All 5 SLOs are instrumented — every metric named above is already emitted to Langfuse on every `/chat` request without further code changes
- Thresholds + page-vs-ticket logic documented (this file)
- Eval-suite gates correctness in CI via the [agent-eval workflow](./.github/workflows/agent-eval.yml) — that's the *pre-deployment* gate; SLOs are the *post-deployment* runtime gate

### What's open (the routing layer)

The path from "metric crosses threshold" to "engineer's phone vibrates" is operational follow-up, not architecture. Two viable wirings, both ~half-day to land:

1. **Langfuse → PagerDuty webhook** — Langfuse Custom Dashboards support threshold-based alerting; route the page-tier alerts to a PagerDuty service (or Opsgenie / VictorOps), the ticket-tier alerts to email. Costs: PagerDuty Free tier covers 1 user; sufficient for the demo droplet on-call-of-one.

2. **Self-hosted alerting (Prometheus + Alertmanager)** — exposes Langfuse metrics via the `/metrics` endpoint already documented in Langfuse self-hosting; pipe to Alertmanager → email/Slack. Heavier setup, no third-party dependency.

For week 1, neither is wired. The `.gauntlet/week2/candidates.md` sweep should add this as a named candidate. Until wired:

- **Manual cadence**: weekly review of the Langfuse "Custom Dashboards" view filtered to the 5 SLO metrics
- **Reactive**: trace-by-trace inspection on PCP-reported issues

### What's NOT in scope (explicit non-goals)

- **Burn-rate alerting** (modern SRE pattern) — fires alerts based on *how fast* error budget is being consumed rather than instantaneous threshold crosses. Useful at scale; overkill at week-1 traffic levels (a few hundred dev traces).
- **Multi-window multi-burn-rate alerts** (Google SRE Workbook ch. 5) — the 1h + 6h + 30d window combo for high-confidence pages without flapping. Defer until production traffic exists to calibrate against.
- **Synthetic monitoring** (canary `/chat` requests on a cron) — meaningful when the agent is in real use; for the demo droplet it would just inflate dev-burn LLM cost.
- **APM / distributed tracing beyond Langfuse** — Langfuse already produces OpenTelemetry-format traces (it's OTel-based under the hood per Langfuse v4 SDK); no need for a separate APM layer.

---

## 5. Verification — how to know the SLOs are being honored

Once the alert routing lands, the SLOs are honored when:

1. **Langfuse Custom Dashboard** has a panel per SLO showing rolling target vs actual + remaining error budget
2. **PagerDuty / Alertmanager** has 5 alert rules (one per SLO) wired to the right pages/tickets per the table above
3. **Weekly SLO review** is on a calendar — owner checks budget burn, files tickets for at-risk SLOs
4. **Quarterly threshold review** — compare actuals against targets; tighten targets where consistently over-served, loosen where consistently breached and the breach is acceptable

For week 1: items 1-4 are all "operational follow-up." This doc is the precondition (named thresholds, named routing); operations is the implementation.

---

## 6. Defense talking points (interview)

- **"What's an SLO and why does this app need one?"** — *An SLO is a measurable target for runtime behavior — "verifier passes 95% of requests over a rolling week." It's the post-deployment counterpart to the pre-deployment eval suite. Without SLOs you only catch correctness regressions in CI; you don't catch runtime regressions (LLM version drift, DB pressure, latency spikes) until a clinician complains. For a clinical tool that's an unacceptable failure mode.*
- **"Walk me through one of the SLOs."** — *Verifier pass rate. Target ≥95% over rolling 7 days. Already instrumented — every request stamps `verifier_verdict` to Langfuse. Page when rolling 1-hour pass rate drops below 85% with at least 20 traces in the window — that's a confidence threshold; pages are expensive, false pages are worse than missed pages, so we wait for enough samples to be statistically meaningful before waking someone. Below 95% over 7 days is a ticket — investigate within a business day; that's likely LLM-version drift, prompt drift, or expanded rule corpus, not an outage.*
- **"What's NOT yet wired?"** — *The routing — Langfuse → PagerDuty webhook or Prometheus → Alertmanager. Half-day of operational follow-up. The thresholds + page-vs-ticket logic is documented (this file); the metrics are already emitted. The gap is the last-mile of "metric crosses threshold → engineer's phone."*
- **"Why a 99.5% availability target instead of 99.9%?"** — *Single-droplet deployment for week 1 — one host reboot is ~5-10 minutes. 99.9% (~43m/month) leaves no budget for routine maintenance. 99.5% (~3.6h/month) is realistic for a single-VPS topology. Tier 2+ behind a load balancer with multiple agent instances tightens to 99.9%; that's an architecture-tier change, not a target negotiation.*
- **"How do you avoid alert fatigue?"** — *Three things: (1) page-vs-ticket separation — only true production-impacting events page; weekly-review-tier observations are tickets. (2) Sample-size gating — verifier-pass-rate page requires ≥20 traces in the window so a noisy 5-request hour can't trigger. (3) Burn-rate framing for future SLOs (operational follow-up) — alerts fire on *how fast* the budget is being consumed, not instantaneous spikes.*
- **"Do you have a runbook?"** — *Per-SLO triage steps live in DECISIONS.md §8 and the 2026-05-02 system-architecture-review notes. A consolidated RUNBOOK.md per the [`Backup/restore + on-call runbook`](./.gauntlet/week2/candidates.md) week-2+ candidate is named scope, not week-1.*
