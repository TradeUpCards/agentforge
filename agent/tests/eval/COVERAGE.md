# Eval Suite Coverage

**Generated:** 2026-05-02 (after `SYNTHETIC_DATA_PLAN.md` step 7)
**Total cases:** 26 across 26 unique failure modes
**How to regenerate:** rerun the loader / matrix introspection script under `agent/tests/eval/runner.py:EvalCase.load_all()` after adding cases. The latest auto-generated per-run report (per-tier, per-difficulty, failure-mode-distribution tables) is at `agent/tests/eval/results/<timestamp>.md`.

---

## Snapshot

| Dimension | Value |
|---|---|
| Total cases | 26 |
| Unique failure modes | 26 (one case per mode — no duplicates, no typos) |
| Synthetic-patient cases | 12 (using sentinel range 999100-999999) |
| Cases with `source_incident_id` provenance | 11 |
| Smoke tier (pre-commit) | 6 cases, all green in fixture mode |
| Full tier (CI default) | 3 cases |
| Nightly tier (live LLM/DB) | 17 cases |
| Adversarial cases | 6 (auth=1, prompt-injection=5, leakage=1) |

## Category × Difficulty

| Category | basic | intermediate | advanced | total |
|---|---:|---:|---:|---:|
| `happy_path` | 4 | 5 | 7 | 16 |
| `prompt_injection` | 0 | 0 | 5 | 5 |
| `edge_case` | 0 | 2 | 0 | 2 |
| `auth_boundary` | 1 | 0 | 0 | 1 |
| `ambiguous` | 0 | 1 | 0 | 1 |
| `leakage_attempt` | 0 | 0 | 1 | 1 |
| **total** | **5** | **8** | **13** | **26** |

The `prompt_injection` and `leakage_attempt` categories are pure-advanced — these are adversarial cases by design. `happy_path` covers all three difficulties because it includes baseline UC1 briefs (basic), focused queries and trend recognition (intermediate), and analytical synthesis on real-shaped polypharmacy data (advanced).

## Category × Tier

| Category | smoke | full | nightly | total |
|---|---:|---:|---:|---:|
| `happy_path` | 5 | 2 | 9 | 16 |
| `prompt_injection` | 0 | 0 | 5 | 5 |
| `edge_case` | 0 | 0 | 2 | 2 |
| `auth_boundary` | 1 | 0 | 0 | 1 |
| `ambiguous` | 0 | 1 | 0 | 1 |
| `leakage_attempt` | 0 | 0 | 1 | 1 |
| **total** | **6** | **3** | **17** | **26** |

Smoke tier is `auth_boundary_bad_hmac` plus 5 Maria-fixture happy-path cases — all run deterministically in fixture mode in ~3.8s (under the pre-commit budget). Nightly is dominated by adversarial cases and Synthea live-data cases that require a real LLM.

## Tool-Mix Coverage

How many cases declare each tool in `tool_mix:`. The tool-mix field is a *declarative* signal of which tools a case is meant to exercise; the runner cross-checks against `expect_tools_called` for consistency.

| Tool | Cases declaring it |
|---|---:|
| `get_problem_list` | 15 |
| `get_recent_labs` | 10 |
| `get_active_medications` | 9 |
| `get_recent_encounters` | 7 |
| `get_allergies` | 4 |

`get_allergies` is thinnest — only 4 of 26 cases explicitly exercise it, despite allergies being clinically high-stakes context (a missed PCN allergy that the agent doesn't surface is exactly the failure mode the brief calls out). Worth backfilling 1-2 more allergy-focused cases in week 2.

## Cases Without `tool_mix` Declared

4 cases pre-date the tool_mix field and weren't backfilled (the validator's superset rule short-circuits when `tool_mix` is empty, so this is allowed). Worth backfilling for cleaner coverage reports:

- `auth_boundary_bad_hmac` — no tool calls expected (HMAC fail-closed before tools fire); leaving empty is correct
- `empty_records_absence_claim` — invokes all 5 baseline tools; declare `tool_mix` to match
- `ambiguous_query` — invokes baseline tools; declare to match
- `prompt_injection_in_note` — invokes `get_problem_list`; declare to match

## Synthetic Patient Inventory

12 sentinel patients in the 999000-999999 range. Each fixture is a single JSON file under `agent/fixtures/patients/<id>_<slug>.json`, loaded by `_json_fixture_dispatch()` in `agent/tools.py`.

| Patient ID | Scenario | Cases |
|---|---|---|
| `999100` | sparse-data (1 problem only) | `sparse_data_absence_claim` |
| `999101` | polypharmacy + warfarin/ibuprofen DDI | `polypharmacy_anticoagulant_completeness` |
| `999102` | free-text-heavy chart | `free_text_clinical_detail_extraction` |
| `999103` | contradictory progression notes | `progression_recognition_diet_to_pharmacotherapy` |
| `999104` | pediatric T1DM | `pediatric_context_awareness` |
| `999110` | injection in lab `name` field | `injection_via_lab_field_name` |
| `999111` | injection in allergy `reaction` | `injection_via_allergy_reaction` |
| `999112` | injection in encounter narrative (free-text strength) | `injection_via_encounter_narrative` |
| `999113` | unicode-obfuscated injection | `injection_unicode_obfuscated` |
| `999114` | cross-patient leakage lure | `cross_patient_leakage_resistance` |
| `999998` | prompt injection in problem-list title (legacy hardcoded) | `prompt_injection_in_note` |
| `999999` | empty-records sentinel (legacy hardcoded) | `empty_records_absence_claim` |

The legacy hardcoded sentinels (999998, 999999) live in `_fixture_prompt_injection_problems()` and `_EMPTY_PATIENT_SENTINEL` branches in `agent/tools.py`. New synthetic sentinels (999100-999114) load from JSON via the unified dispatch added in `SYNTHETIC_DATA_PLAN.md` step 2.

## Failure Modes (alphabetical)

Every case is tagged with a `failure_mode` string. The runner reports unique values to surface typos. All 26 modes below are unique — no duplicates in the suite.

| Failure mode | Case |
|---|---|
| `absence_claim_honesty` | `empty_records_absence_claim` |
| `allergy_omission` | `uc1_allergy_surfaced` |
| `allergy_omission_real_data` | `synthea_allergy_surfaced` |
| `ambiguous_input_grounding` | `ambiguous_query` |
| `analytical_synthesis_polypharmacy` | `synthea_focused_diabetes_status` |
| `baseline_uc1_brief` | `uc1_happy_path` |
| `cross_patient_leakage_resistance` | `cross_patient_leakage_resistance` |
| `drug_class_completeness` | `maria_drug_class_completeness` |
| `focused_lab_query_grounding` | `maria_focused_lab_query` |
| `focused_medication_query` | `synthea_followup_medications` |
| `free_text_clinical_detail_extraction` | `free_text_clinical_detail_extraction` |
| `hmac_rejection` | `auth_boundary_bad_hmac` |
| `injection_marker_compliance` | `prompt_injection_in_note` |
| `injection_unicode_obfuscated` | `injection_unicode_obfuscated` |
| `injection_via_allergy_reaction` | `injection_via_allergy_reaction` |
| `injection_via_encounter_narrative` | `injection_via_encounter_narrative` |
| `injection_via_lab_field_name` | `injection_via_lab_field_name` |
| `lab_trend_completeness` | `maria_uc1_lab_trend` |
| `lab_value_citation` | `uc1_a1c_value_cited` |
| `pediatric_context_awareness` | `pediatric_context_awareness` |
| `polypharmacy_anticoagulant_completeness` | `polypharmacy_anticoagulant_completeness` |
| `polypharmacy_completeness` | `synthea_polypharmacy_brief` |
| `problem_omission` | `uc1_diagnosis_surfaced` |
| `progression_recognition_treatment_escalation` | `progression_recognition_diet_to_pharmacotherapy` |
| `sparse_data_absence_claim` | `sparse_data_absence_claim` |
| `uc2_delta_grounding` | `maria_uc2_delta_query` |

## Source-Incident Provenance

Cases with a `source_incident_id` field — the trace-to-fixture provenance link the LLM-observability review flagged as missing. 11 of 26 cases (42%) have this; the others are infrastructure cases (cases 01-05, 07, 09-11) authored before the field existed.

| Case | Source |
|---|---|
| `empty_records_absence_claim` | `DECISIONS.md#2026-04-30-empty-records-ux` |
| `sparse_data_absence_claim` | `SYNTHETIC_DATA_PLAN.md#step-2-reference-fixture` |
| `polypharmacy_anticoagulant_completeness` | `SYNTHETIC_DATA_PLAN.md#step-4-polypharmacy-fixture` |
| `free_text_clinical_detail_extraction` | `SYNTHETIC_DATA_PLAN.md#step-4-free-text-fixture` |
| `progression_recognition_diet_to_pharmacotherapy` | `SYNTHETIC_DATA_PLAN.md#step-4-contradictory-fixture` |
| `pediatric_context_awareness` | `SYNTHETIC_DATA_PLAN.md#step-4-pediatric-fixture` |
| `injection_via_lab_field_name` | `SYNTHETIC_DATA_PLAN.md#step-5-injection-variants` |
| `injection_via_allergy_reaction` | `SYNTHETIC_DATA_PLAN.md#step-5-injection-variants` |
| `injection_via_encounter_narrative` | `SYNTHETIC_DATA_PLAN.md#step-5-injection-variants` |
| `injection_unicode_obfuscated` | `SYNTHETIC_DATA_PLAN.md#step-5-injection-variants` |
| `cross_patient_leakage_resistance` | `SYNTHETIC_DATA_PLAN.md#step-5-cross-patient-leakage` |

Goal for week 2+: every case promoted from a real production trace gets a `source_trace_id` field (Langfuse trace UUID) when the trace-to-case promotion CLI is built (per `llm-observability-review` finding [P2]).

---

## Identified Gaps For Week-2 Expansion

These are the explicit holes in the current suite, ordered by signal value:

1. **Verifier-targeted negative cases live as unit tests, not eval cases.** `agent/tests/unit/test_verifier.py` has `test_value_date_pair_must_come_from_same_record` and `test_value_date_pair_passes_when_co_located_in_same_record`. Plan step 5's "Frankenstein-lab eval cases" were de-scoped because of this overlap. Strong choice — eval cases are the wrong shape for value/date pair regression testing. Worth documenting elsewhere too.

2. **`get_allergies` thinly covered (4 cases).** Allergies are clinically high-stakes and the brief explicitly calls out missed-allergy as the worst failure mode. Add 1-2 more allergy-focused cases in week 2: severe-allergy + contradictory chart, allergy with multiple cross-reactivities (e.g., PCN → cephalosporin), or allergy-buried-in-narrative-only.

3. **No DDI rule corpus tests yet.** Fixture 999101 deliberately embeds a warfarin + ibuprofen DDI; a follow-up case can assert "must_mention 'bleeding risk' or 'NSAID interaction'" once `RULE_CORPUS.md`'s anticoagulant-NSAID rule ships.

4. **No cost-spiral / token-exhaustion cases.** The plan's generation strategy listed "50KB user message history" as an attack — not implemented. Pending the rate-limit + cost-budget guardrails from `ai-security-review` blockers; once those land, write `expected_to_fail: true` regression tests for them.

5. **No HMAC variant coverage beyond case 05.** Plan listed: empty body, replayed body, wrong key, wrong header layout. Currently we have one `bad_hmac: true` case. If the HMAC replay-protection from `ai-security-review` blocker #3 lands, all four variants are easy to add.

6. **4 cases lack declared `tool_mix`** (see "Cases Without tool_mix Declared" above). Backfill is ~30 minutes of mechanical work and would put the consistency-check rule at 100% coverage.

7. **Coverage matrix is per-(category × difficulty) and (category × tier) only.** A 3-way matrix (category × difficulty × tool_mix) would surface combination gaps like "we have 0 advanced cases that exercise only `get_allergies`." Worth adding to the runner's auto-generated report when the suite grows past ~50 cases.

8. **No live-LLM-mode pre-commit gate.** Smoke tier is fixture-only by design; nightly tier requires manual invocation. A weekly cron that runs `python -m agent.tests.eval.runner --tier=nightly` against live Anthropic + live Synthea DB would catch live-mode regressions earlier.

---

## Maturity Self-Assessment

Per the `llm-observability-review` rubric's eval setup taxonomy:

| Stage | Status | Evidence |
|---|---|---|
| Stage 1: Golden Sets | ✅ complete | 26 cases, versioned in git, deterministic checks, pytest gate, review habit (PR-required) |
| Stage 2: Labeled Scenarios | ✅ in flight | Every case has `category`, `difficulty`, `tool_mix`, `failure_mode`, `tier`, `source_incident_id` (where known); coverage matrix surfaces thin areas |
| Stage 3: Replay Harnesses | 🟡 partial | Fixture-mode replay works (canned LLM + sentinel-patient JSON dispatch); full-session recording for live-LLM cases (e.g., 09-11 Synthea) is the missing piece |

To reach Stage 3 fully: capture one reference run per nightly case as a `[input, tool_calls, retrieved_context, llm_output, verdicts]` JSON fixture; add a `replay --case=<name> --reference=<session.json>` mode to the runner; rescore the verifier against the recorded LLM output after code changes. That's the natural week-3 work once nightly cases have stabilized.

---

*Generated 2026-05-02 by Claude (model: claude-opus-4-7) running the `synthetic-data-plan` skill (step 7 close-out) from Adam Foosaner's skill bundle.*
