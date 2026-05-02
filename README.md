# AgentForge Clinical Co-Pilot

> A trustworthy AI agent embedded in OpenEMR that gives a primary care physician the patient context they need in the 90 seconds between rooms — verified against the chart, not hallucinated.

Built for the **GauntletAI Austin Admission Track, Week 1**. Forked from [openemr/openemr](https://github.com/openemr/openemr); the agent and integration module are net-new.

---

## What this is

A physician walks toward an exam room with ~90 seconds to recall who they're seeing, what's changed since the last visit, and what actually matters today. The Co-Pilot sits inside the OpenEMR chart UI and answers that question with a structured pre-visit brief — every claim cited to a specific record (`lists:1408`, `procedure_result:5421`, etc.). A separate **verifier** strips any claim that can't be matched back to the chart before the response reaches the user.

**Three use cases on the v1 surface:**
- **UC1 — Pre-visit brief.** "What do I need to know about this patient before walking in?"
- **UC2 — Delta narrative.** "What's changed since the last visit?"
- **UC3 — Multi-turn Q&A.** "What's their A1c trajectory?" → follow-ups stay grounded in the same patient context.

**The hard problem isn't generating text — it's *not* generating text that isn't in the data.** The verifier is what makes the surface defensible in front of a hospital CTO; everything else is plumbing.

**Key architectural choices** (full reasoning in [DECISIONS.md](./DECISIONS.md)):
- **Atomic claim verification + 30%-strip rule** — every fact the LLM emits is paired with `source_record_ids`; the verifier strips claims that fail token/date/citation match. >30% strip rate → refuse rather than ship.
- **Multi-model tiering** — Sonnet for reasoning, Haiku for synthesis (~3× cheaper for ~70% of calls).
- **Explicit Anthropic prompt caching** — `cache_control` breakpoint on the per-patient context block; UC3 follow-ups read from the same cache entry within the 5-min TTL.
- **Stateless agent service** — full conversation history sent every turn; OpenEMR module owns auth, ACL, CSRF, HMAC, session.

---

## Try it

| | |
|---|---|
| **Deployed agent** | https://142-93-242-40.nip.io (credentials provided via the GauntletAI submission portal — synthetic demo data only, no real PHI) |
| **Demo video (final submission)** | *Recording today; link added on final commit.* |
| **Demo video (early submission, MVP gate)** | See `.gauntlet/week1/early-submission-video-script.md` (private notes; the video itself was submitted via the GauntletAI portal) |
| **GitHub mirror** | https://github.com/TradeUpCards/agentforge |
| **GitLab (primary)** | https://labs.gauntletai.com/coryvandenberg/agentforge |

Pre-loaded with **200 Synthea-imported demo patients** + 1 hand-crafted edge-case patient. No real PHI.

---

## Documentation map (the brief's required deliverables)

The week-1 brief lists 8 required deliverables. Each maps to a file in this repo:

| # | Brief deliverable | Location | What's in it |
|---|---|---|---|
| 1 | **GitHub Repository** | This repo | Forked from OpenEMR; this README + the docs below + `agent/` + `interface/modules/custom_modules/oe-module-clinical-copilot/` are net-new. |
| 2 | **Audit Document** | [`AUDIT.md`](./AUDIT.md) | Security / performance / architecture / data-quality / compliance audit of OpenEMR. Begins with a 1-page summary of the most impactful findings. |
| 3 | **User Doc** | [`USERS.md`](./USERS.md) | Target user (primary care physician), three concrete use cases UC1/UC2/UC3, and *why an agent* is the right shape for each. |
| 4 | **Agent Architecture Doc** | [`ARCHITECTURE.md`](./ARCHITECTURE.md) | The integration plan + verification strategy + tradeoffs. Begins with a 1-page summary; subsequent sections cover model selection, tool design, verifier placement, observability, deployment. |
| 5 | **Demo Video (3–5 min)** | *See "Try it" above* | Final-submission re-record links here on commit. |
| 6 | **Eval Dataset** | [`EVAL_SUITE.md`](./EVAL_SUITE.md) + [`agent/tests/eval/cases/`](./agent/tests/eval/cases/) + [`agent/tests/eval/results/`](./agent/tests/eval/results/) | 26 cases across 5 categories (happy_path, auth_boundary, edge_case, ambiguous, prompt_injection); per-tier markdown reports. |
| 7 | **AI Cost Analysis** | [`COST_ANALYSIS.md`](./COST_ANALYSIS.md) | Actual dev burn + per-PCP/mo projections at 100 / 1K / 10K / 100K with the architectural changes named at each tier. |
| 8 | **Deployed Application** | https://142-93-242-40.nip.io | Single DigitalOcean droplet running the full stack (OpenEMR + agent service + MariaDB + Caddy). |
| 9 | **Social Post** *(final only)* | *Posted on X; link added on final commit.* | |

---

## Companion docs (deeper context the brief doesn't require but a CTO would read)

| Doc | Purpose |
|---|---|
| [`DECISIONS.md`](./DECISIONS.md) | Architectural decisions log — every load-bearing choice with the *why*, the *tradeoff*, and an inline `> Updated <date>` callout when newer information changes the answer. |
| [`PERFORMANCE.md`](./PERFORMANCE.md) | EXPLAIN analysis on the 5 tool queries; one load-bearing fix shipped (full-scan on `forms` table → 4-row indexed lookup). |
| [`RULE_CORPUS.md`](./RULE_CORPUS.md) | The 7-rule clinical corpus the verifier flags against, with selection filters and "adjacent rule considered + why this won." |
| [`SYNTHETIC_DATA_PLAN.md`](./SYNTHETIC_DATA_PLAN.md) | How the 10 synthetic edge-case patients were designed — sentinel ID range, no-real-PHI validator, per-patient failure mode coverage. |
| [`SETUP.md`](./SETUP.md) | Local-dev setup: clone, `docker compose up`, agent venv, env vars, pre-commit hook install. |

---

## Repo structure — what's ours vs upstream

```
AgentForge/
├── agent/                                   ← Python agent service (NET-NEW)
│   ├── agent.py, llm_client.py, verifier.py, tools.py, schemas.py, main.py
│   ├── fixtures/patients/                  Synthetic edge-case patients (sentinel IDs 999100-999114)
│   └── tests/
│       ├── unit/                           Verifier + PHI mask unit tests
│       └── eval/                           26 YAML eval cases + runner + per-run markdown reports
│
├── interface/modules/custom_modules/
│   └── oe-module-clinical-copilot/         ← OpenEMR integration module (NET-NEW)
│       ├── openemr.bootstrap.php           Module entry — registers PSR-4 namespace, wires event subscribers
│       ├── src/                            Controllers, EventSubscribers (PageHeading, ScriptFilter)
│       └── public/                         Browser-facing chat panel + chart bootstrap JS/CSS
│
├── .deploy/bootstrap.sh                     ← Droplet bootstrap script (NET-NEW)
├── scripts/git-hooks/pre-commit             ← Verifier + smoke-tier eval runner (NET-NEW)
│
├── README.md ARCHITECTURE.md AUDIT.md       ← Submission docs (NET-NEW)
├── COST_ANALYSIS.md DECISIONS.md            ← Companion docs (NET-NEW)
├── EVAL_SUITE.md PERFORMANCE.md
├── RULE_CORPUS.md SYNTHETIC_DATA_PLAN.md
├── USERS.md SETUP.md
│
├── OPENEMR_README.md                        ← Upstream OpenEMR README, preserved
└── (everything else is upstream OpenEMR)    ← src/, library/, sql/, tests/, etc.
```

If you're reviewing this repo, the work to look at lives in `agent/`, `interface/modules/custom_modules/oe-module-clinical-copilot/`, `.deploy/`, and the root-level `*.md` docs listed above. The rest is OpenEMR upstream — referenced for context (e.g., `src/Common/Acl/AclMain.php` is what our PHP controller calls into) but not modified.

---

## Status

**Working in production:**
- UC1 / UC2 / UC3 end-to-end against real Synthea-imported MariaDB
- Verifier with date normalization (ISO/MM-DD-YYYY/MM/DD/YYYY) + value-date tuple matching
- Explicit Anthropic prompt caching (verified live: 100% cache READ on identical follow-up)
- Langfuse observability — traces, sessions, users, PHI date-bucketing mask
- HMAC + CSRF + ACL on every backend endpoint; auth gate on JS/CSS injection so the login page doesn't leak module existence
- 26 eval cases / 5 categories / two-mode runner / pre-commit smoke tier

**Deferred (week-2+ with documented rationale in DECISIONS.md):**
- Same-patient drawer-open pre-warm cache (cross-patient pre-fetch explicitly *rejected* — see `.gauntlet/week2/candidates.md`)
- Verifier temporal-coherence check (delta-narrative direction validation)
- Full HIPAA Safe Harbor PHI scrubbing in observability — first cut shipped (year-month date bucketing); names/DOBs/MRNs/free-text scrubbing remain (DECISIONS.md §4a)
- LLM-as-judge eval layer
- Replay harness against captured production traces
- Rate limiting

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
