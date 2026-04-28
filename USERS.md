# USERS.md — Target User and Use Cases

**Project:** AgentForge Clinical Co-Pilot
**Source of truth note:** every agent capability built in `ARCHITECTURE.md` and the implementation must trace back to one of the use cases defined here. If a capability does not serve a use case below, it is out of scope.

---

## Target User

**Primary care physician (PCP) running an outpatient clinic, 18–24 patients/day, ~90 seconds between rooms.**

This is not "a doctor." It's a specific shape of clinical workflow that the rest of the project depends on:

- **Mixed acute and chronic patients.** A typical PCP day: a diabetic medication-adjustment follow-up, a well-child visit, an upper respiratory complaint, an annual physical, an anxiety follow-up, a Medicare wellness visit. Each patient is a different problem with a different mental model.
- **Time-constrained transitions.** Patients are scheduled in 15–20-minute slots. The PCP rarely sees the same patient twice in a row. Between rooms they walk down a hall, glance at the next patient's chart on a workstation, and try to recall who they're about to see and why.
- **Established panel.** Most patients are returning, not new. The PCP has seen ~80% of today's roster before — sometimes recently, sometimes years ago.
- **No clinical AI experience required.** The user is a clinician, not an AI engineer. The interface must be conversational and responsive, not a configuration screen. They will not "tune prompts" or "review LLM outputs"; they will ask questions and read answers.

**Why this persona over alternatives.** A plastic-surgery post-op co-pilot was considered (narrower domain, stronger verification story) but rejected: OpenEMR's demo data and schema map naturally to PCP workflows, and the developer lacks the clinical background needed to write defensible verification rules outside the most general primary-care domain. An ED resident or hospitalist persona would be defensible but produces a very different latency/data shape than the brief's canonical "90 seconds between rooms" scenario. PCP is the closest match to the brief's example and the cleanest fit for the data we have.

**What this persona excludes.** Specialists requiring deep domain rules (cardiology, oncology, psych) — out of scope. Inpatient or ED workflows with continuous monitoring data — out of scope. Patient-facing portal users — out of scope. The agent is an internal clinical tool, not a patient-facing product.

---

## The Workflow — A 90-Second Window

To ground the design, walk through a single concrete moment:

> **8:52 AM.** PCP closes the door behind a 67-year-old patient she just finished with — diabetic foot exam, BP recheck, A1c result discussed. She has 90 seconds before her next patient is roomed.
>
> **8:52:05.** She walks to the workstation in the hall. Her schedule shows the next patient: *"Maria Hernandez, 54F, follow-up — diabetes management."*
>
> **8:52:15.** She thinks: *"Hernandez. I saw her three months ago, started her on metformin. Is she back for the A1c recheck? Did she tolerate the metformin? Anything else going on?"*
>
> **8:52:25.** Today, she opens the chart, scrolls through the problem list, clicks into the last visit's note, scans for the plan, then clicks back to find the recent labs. By 8:53:30 she's reconstructed enough to walk in. The patient has been roomed for two minutes.
>
> **With the agent.** She clicks "Pre-visit brief" or "What changed." A 60-second structured summary loads in under three seconds. As she reads it, a question forms: *"What was her last A1c?"* She types it. Two seconds later she has the answer with a citation. She walks in at 8:53:00 with thirty seconds of breathing room.

The agent's value is **time**. Not impressive answers — answers fast enough that the PCP stays in flow.

---

## Use Cases

Three use cases. Each names the moment it serves, the output, and the explicit reason a *conversational agent* (not a dashboard, sorted list, or chart redesign) is the right shape.

### UC1 — Pre-visit brief

**When.** ~90 seconds before the PCP walks into a room. Triggered from the patient chart's tab menu (`PatientMenuEvent::MENU_UPDATE`).

**Output.** A structured 60-second summary:

- Reason for today's visit (from appointment record)
- Active problem list (with onset dates)
- Current medications (with doses)
- Recent labs (most recent values, with abnormal flags)
- Last visit's plan (the assessment-and-plan section of the most recent encounter note)

Every fact is cited to a specific OpenEMR record ID.

**Used by:** patients the PCP doesn't remember well — new to the practice, handed off from another physician, or not seen in 18+ months.

**Why an agent (not a dashboard).** A static summary is the *opening* of the PCP's reasoning, not the destination. Reading "last A1c was 7.2" immediately raises *"and on what regimen, and trending which way?"* A dashboard forces the PCP back into the chart to follow up; an agent stays loaded with the same patient context and answers the next question without context-switching. The 90-second window doesn't have room for the chart-flipping a dashboard requires.

### UC2 — What changed since last visit

**When.** ~90 seconds before walking in, OR when the encounter form loads (`LoadEncounterFormFilterEvent`). Triggered from the same chart entry point as UC1.

**Output.** A delta-focused report:

- New labs since the prior encounter, with **trend annotations** for the five chronic-disease-monitoring labs in the rule corpus (HbA1c, BP, weight, eGFR, LDL — each cited to a published clinical guideline)
- Medication changes (started, stopped, dose-adjusted)
- New diagnoses
- New encounter notes
- Abnormal flags surfaced from OpenEMR's existing data

**Used by:** the daily case — established patients on a known regimen, where the PCP already has a mental model and needs only the diff.

**Why an agent (not a dashboard).** "What's relevant" depends on the patient's whole context — a new abnormal TSH means different things for a stable diabetic vs. a new patient. A static diff view can't prioritize. An agent, with the patient's problem list and history loaded, can rank what's worth surfacing first. Same follow-up dynamic as UC1: the delta produces the next question, and the agent answers it without breaking flow.

**Anticipated defense question — "Isn't UC2 just a degenerate UC1?"** No. The triggers and audiences are different. UC1 serves patients the PCP doesn't remember well — full snapshot needed. UC2 serves the daily case — the PCP already has a mental model, doesn't need a refresher on what they already know, and needs only the delta. Producing a full snapshot for every visit would force them to re-read content they remember, which is exactly the chart-flipping problem the brief is asking us to solve.

### UC3 — Source-backed follow-up Q&A

**When.** During the visit, or in the seconds before walking in. The PCP types or asks a question via a conversational interface (custom REST endpoint, triggered from the agent UI inside the encounter view).

**Output.** A short, factual answer with citations to specific OpenEMR records.

**Examples:**
- *"What was their last A1c?"* → "7.8 on 2026-03-15 (procedure_result:5421), up from 6.8 (procedure_result:4892, 2025-12-10). The 2026 value crossed the ADA-flagged 1.0-point delta threshold."
- *"Why are they on metformin?"* → "Metformin 1000mg BID, started 2025-10-04 (prescriptions:2231) for type 2 diabetes mellitus (lists:1408, ICD-10 E11.9 from 2024-08-12)."
- *"Any allergies to penicillin?"* → "Yes — patient has penicillin allergy on file (lists:1399, allergy type, severity: moderate, reaction: rash)."

**Why an agent.** This is the canonical agent case — multi-turn by definition (PCP asks, gets answer, asks follow-up), conversational, requires tool invocation against live patient data, and *requires the verification layer the brief mandates.* A search bar can match keywords; an agent can answer questions. The difference is whether the response is "here are 12 records that match `metformin`" (search) vs. "she's been on metformin since 2025-10-04 for the diabetes diagnosis from 2024" (answer with synthesis and citation).

The conversational shape also matches how PCPs already think under time pressure — they ask one question, get a piece of information, and that piece raises the next question. This is exactly what multi-turn chat is for.

---

## What This Persona Does NOT Need (and Why)

These are real PCP needs that other products solve. Including them in week 1 dilutes the project's verification story. Listed explicitly so they're not perceived as gaps.

- **Inbox triage / message management.** A real workflow problem, but it's a workflow tool, not an agent — the brief grades agent-shape capabilities, not workflow management.
- **Care-gap sweeps across the panel** ("which of my patients are overdue for a colonoscopy?"). Different mode of interaction (list, not conversation). Possible week-3 add.
- **Note drafting / charting assistance.** Out of scope by architectural rule (`ARCHITECTURE.md` C-1: agent never auto-acts, never writes to the chart). Adds large HIPAA / liability surface for week 1.
- **Order entry / prescription writing.** Same architectural rule — advisory only.
- **Clinical decision support beyond the cited rule corpus.** The agent will not invent rules. A diagnosis suggestion, dose calculation, or differential the rule corpus doesn't cover is something the agent surfaces data for and lets the PCP decide.
- **Voice / ambient capture.** Different category of product. Not an agent in the brief's sense.

---

## Source-of-Truth Commitments

Per the brief (Week 1 doc, p.6): *"Every agent capability you build in Stage 5 must point to a use case here."*

Concretely:

| Use case | Triggers in `ARCHITECTURE.md` | Tools used |
|---|---|---|
| UC1 | `PatientMenuEvent::MENU_UPDATE` event subscriber → calls Python agent service | `get_pre_visit_brief(patient_id)` composite tool, which internally invokes data tools (`get_patient_demographics`, `get_problem_list`, `get_active_medications`, `get_recent_labs`, `get_encounter_notes`, `get_appointments`) |
| UC2 | `LoadEncounterFormFilterEvent` subscriber, OR same chart-menu trigger as UC1 | `get_changes_since_last_visit(patient_id)` composite tool, plus rule-corpus lookups for the 5 chronic-disease delta thresholds |
| UC3 | Custom REST endpoint at `/apis/routes/`, called from a chat UI inside the encounter view | Fine-grained data tools, composed by the LLM at reasoning time |

If any tool, prompt, or interface element in the implementation cannot be traced to one of the three use cases above, it should be deferred or cut.
