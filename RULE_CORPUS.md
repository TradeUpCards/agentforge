# RULE_CORPUS.md — Clinical Rule Selection Criteria & Initial Corpus

> Companion to [ARCHITECTURE.md §3.5](./ARCHITECTURE.md#35-domain-rule-enforcement-tier-1-corpus) and [DECISIONS.md §2 — Rule corpus boundary](./DECISIONS.md). Written in response to MVP-submission grader feedback that the rule selection rationale needed to be more explicit about *why these rules vs adjacent rules*.

**Audience:** the hospital CTO asking "how did you pick these rules, and what stopped you from including the dozen others a clinician would mention?"

**Scope:** the criteria for what makes the cut, plus the initial corpus that anchors week-1 demo behavior. Implementation status at the bottom.

---

## Why a rule corpus exists at all

The verifier (DECISIONS.md §2) catches *unverifiable* claims — values that don't match cited records, dates that don't co-locate, IDs that aren't in the retrieved set. It can't catch claims that are *technically grounded but clinically wrong* — e.g., "patient's A1c is 7.8" is a true statement, but "this patient's A1c rise is concerning enough to escalate" requires clinical judgment.

The rule corpus is the architecture's answer to that gap. **Clinical-significance claims fire only when a cited rule matches.** No matched rule = no significance claim. Expanding the agent's clinical-claim surface requires expanding the cited rules — that's a content problem, not an architecture problem.

**This is the load-bearing line for the CTO defense.** A clinician asking "why didn't your agent flag X?" gets a defensible answer: "X isn't in our corpus; we didn't ship a rule we couldn't defend with evidence."

---

## Selection criteria

Three filters, applied in order. A rule must pass all three to ship.

### Filter 1: clinical impact threshold

> *"Will firing this rule plausibly change a treatment plan, prompt an escalation, or alter the visit's trajectory?"*

If no — drop the rule. The PCP doesn't have time for advisory signals that don't translate to action. We're not building a clinical-trivia engine.

**Example pass:** "warfarin + new NSAID" — firing this rule meaningfully changes the visit (PCP either reverses the NSAID or tightens INR monitoring).

**Example fail:** "patient is over age 65" — true, useful for context, but doesn't fire a clinical action by itself. Belongs in the patient-context block, not as a rule-flag claim.

### Filter 2: evidence quality

Preferred order, descending:

1. **USPSTF (US Preventive Services Task Force) recommendation** — Grade A or B. Highest evidence bar in primary care.
2. **Specialty-society guideline** with explicit threshold values — ADA Standards of Care, KDIGO, AHA/ACC/AHA, NCCN. Quote chapter and year.
3. **UpToDate or peer-reviewed clinical decision-support reference** with cited primary literature.
4. **Expert consensus published in a peer-reviewed journal** — last resort, only when no guideline exists.

**Below the bar:** anything sourced from "common practice," "the EMR vendor's library," or "a colleague said so." If we can't cite the source in the rule's record, the rule doesn't ship.

### Filter 3: false-positive cost

> *"What happens when this rule fires on a case where it shouldn't have?"*

Alarm fatigue is real and clinically dangerous — every false positive trains the PCP to dismiss the next signal. Three false-positive cost tiers:

| FP cost tier | Treatment | Example |
|---|---|---|
| **Low** (signal is informational, FP is "now I know nothing") | Ship as a rule. | "Patient last seen >12 months ago" — FP if PCP saw them yesterday outside the EMR; cost is just an extra pixel of context. |
| **Medium** (signal asks PCP to *check something*, FP wastes ~30s) | Ship if true-positive rate ≥ 80% in pilot data. | "HbA1c ↑ ≥1.0 absolute" — FP only if the prior A1c was atypically low (e.g., during illness); easy to verify in 30s. |
| **High** (signal asks PCP to *act / change a plan*, FP causes treatment harm or distrust) | **Ship only if true-positive rate ≥ 95% AND the signal is reversible by visit-time review.** | Drug-drug interaction firings. FP rate must be very low — otherwise "warning fatigue" sets in within days. |

If a rule's FP cost is High AND its true-positive rate in pilot data is unknown, **don't ship** — needs a pilot study or specialist sign-off first.

---

## Initial corpus — 7 rules

Anchored to the hand-crafted edge-case patients planned for Phase 12 (`agent/seed_patients.sql`). Each rule maps to at least one patient that exercises it.

Notation:
- **R-NN** — rule ID (stable, never reused).
- **Trigger** — concrete pseudocode against the agent's tool returns.
- **Evidence** — citation source + year. Verbatim where possible.
- **Clinical impact** — what the PCP *does* with the signal.
- **Adjacent rule considered** — what looked like it could win this slot and why this one beat it.
- **FP scenarios + mitigations** — what could go wrong and our planned response.

---

### R-01 — Warfarin + new NSAID prescription

**Trigger.** Patient has an active prescription for warfarin AND a new prescription (in last 30 days) for any NSAID (ibuprofen, naproxen, ketorolac, etc).

```
warfarin in active_meds
AND any(nsaid in nsaid_list for nsaid in new_meds_last_30d)
```

**Evidence.** AHRQ "High-Alert Medications" list (2014, updated 2022); UpToDate "Drug interactions of warfarin" (lookup current via `/medical-knowledge/topic/...`). Chest 2018 antithrombotic guidelines explicitly call out NSAID + warfarin as a major bleeding-risk combination.

**Clinical impact.** PCP either:
1. Discontinues the NSAID and offers acetaminophen substitution.
2. Continues NSAID with INR check within 5–7 days and PPI prophylaxis.

This visibly changes the visit. Pass on Filter 1.

**Adjacent rule considered + why R-01 won.**
- **"Warfarin + any antibiotic that affects CYP450"** (e.g., metronidazole, TMP-SMX, fluconazole) — strong bleeding-risk signal too. **Why R-01 won this slot:** NSAIDs are vastly more common in the prescription stream than CYP-affecting antibiotics; we get more clinical mileage out of the rule that fires more often. The CYP-affecting antibiotic rule is on the "next 7 rules" candidate list (R-08 in the week-2 expansion).

**FP scenarios + mitigations.**
- **Single-day NSAID for an acute injury, INR already monitored.** Filter 3 tier: medium (visit-level check). Mitigation: rule fires; PCP overrides in <30s.
- **Topical NSAID (diclofenac gel).** Lower bleeding risk than oral. Mitigation: trigger excludes prescriptions where `route` field is `topical`.

**Status.** Designed; not yet implemented. Patient A in Phase 12 fixture set exercises this rule.

---

### R-02 — ACE inhibitor + potassium-sparing diuretic + recent K+ ≥ 5.0

**Trigger.**

```
(acei in active_meds OR arb in active_meds)
AND (k_sparing_diuretic in active_meds)  // spironolactone, eplerenone, amiloride
AND most_recent_potassium >= 5.0 within last 6 months
```

**Evidence.** ACC/AHA Heart Failure Guidelines (2022) — explicit guidance on K+ monitoring frequency in patients on RAAS + MRA combinations. Threshold of 5.0 mEq/L is the "alert" boundary; >5.5 is intervention territory.

**Clinical impact.** PCP either:
1. Holds the spironolactone, repeats K+ in 3–5 days.
2. Reduces ACEi/ARB dose, repeats K+ in 1 week.
3. Adds dietary potassium counseling if borderline.

**Adjacent rule considered + why R-02 won.**
- **"ACEi + K-sparing diuretic" alone (no recent K+ check)** — simpler trigger. **Why R-02 won:** the simpler version fires on every patient on appropriate-and-monitored therapy → high FP rate, alarm fatigue. Adding the recent K+ ≥ 5.0 condition makes the rule fire only when the data already shows trouble brewing.

**FP scenarios + mitigations.**
- **K+ measured during illness or AKI episode that's since resolved.** FP cost tier: medium. Mitigation: rule trigger explicitly says *most recent* K+; if a more recent normal K+ exists, the rule doesn't fire.
- **Patient already at the cardiology clinic for this exact monitoring.** FP cost: low (just informational reminder). Acceptable.

**Status.** Designed; not yet implemented. Patient B in Phase 12 fixture set exercises this rule.

---

### R-03 — PCN allergy + new beta-lactam prescription

**Trigger.**

```
"penicillin" in allergies (any severity)
AND any(beta_lactam in beta_lactam_list for beta_lactam in new_meds_last_30d)
```

Beta-lactam list: amoxicillin, ampicillin, cefalexin, ceftriaxone, etc.

**Evidence.** AAAAI/ACAAI Joint Task Force "Practice Parameter for Drug Allergy" (2010, updated 2022). UpToDate "Allergy evaluation for beta-lactam antibiotics."

**Clinical impact.** PCP either:
1. Switches antibiotic class (macrolide, fluoroquinolone, etc).
2. Reviews the allergy history and verifies it's a true PCN allergy (not just a documented reaction from childhood that's likely outgrown — UpToDate suggests ~80% of reported PCN allergies are actually safe to challenge).
3. Refers for allergy testing.

**Adjacent rule considered + why R-03 won.**
- **"Any allergy + new prescription where the drug is in the allergy class"** — broader and would also catch sulfa, NSAID-allergic patients on aspirin, etc. **Why R-03 won:** PCN allergy + beta-lactam is by far the most common false-positive scenario in primary care (over-reported allergies) AND has the highest true-positive harm potential (anaphylaxis is a real outcome). Specific rule is more defensible than generic. The general allergy-class rule is on the candidates list for R-08+ expansion.

**FP scenarios + mitigations.**
- **Reported "rash with PCN at age 4" — likely not a real allergy.** FP cost tier: medium (PCP wastes 60s reviewing). Acceptable; this IS the rule firing for the right reason — the PCP should review the allergy.
- **Cross-reactivity question between PCN and 1st-gen cephalosporins (~5%) vs 2nd-gen+ (~1%).** Mitigation: rule message includes the cephalosporin generation and the relevant cross-reactivity probability; doesn't refuse, surfaces the data.

**Status.** Designed; not yet implemented. Patient C in Phase 12 fixture set exercises this rule.

---

### R-04 — HbA1c absolute increase ≥ 1.0% across two consecutive measurements

**Trigger.**

```
prior_a1c is not None
AND most_recent_a1c >= prior_a1c + 1.0
AND time_between(most_recent_a1c, prior_a1c) >= 90 days
AND time_between(most_recent_a1c, prior_a1c) <= 365 days
```

(The 90-day floor avoids false signals from same-week or rapid-recheck variability; the 365-day ceiling avoids comparing measurements too far apart to be clinically meaningful.)

**Evidence.** ADA Standards of Care in Diabetes (2024) Section 6 — Glycemic Targets. ADA's framework for "treatment intensification" hinges on A1c trajectory; an absolute rise of ≥1.0% in <12 months is one of the named intensification triggers.

**Clinical impact.** PCP either:
1. Adds or escalates a glucose-lowering agent.
2. Reviews adherence (most common cause of A1c drift in established patients).
3. Tests for concurrent illness or steroid exposure.

**Adjacent rule considered + why R-04 won.**
- **"A1c above target threshold (>7.0% for most adults)"** — simpler, doesn't require historical data. **Why R-04 won:** the absolute-threshold rule fires on every patient with diabetes whose A1c is anywhere above target — that's the majority of the diabetic panel, every visit. Alarm fatigue immediate. Trajectory rule fires on the patients who actually need attention now.
- **"A1c relative increase ≥15%"** — proportional rather than absolute. **Why R-04 won:** absolute is the ADA-cited threshold. Relative changes amplify noise at low absolute values (a jump from 5.5 to 6.3 is +14% but clinically irrelevant).

**FP scenarios + mitigations.**
- **Patient on steroids (anti-inflammatory burst, inhaled, etc).** Mitigation: rule message includes steroid-check prompt.
- **Lab variability or post-acute-illness measurement.** FP cost tier: medium. Acceptable; PCP review takes <1 minute.

**Status.** Designed; not yet implemented. Patient B in Phase 12 fixture set has the 6.8 → 7.5 → 8.4 trajectory exercising this rule.

---

### R-05 — eGFR decrease ≥ 25% from established baseline

**Trigger.**

```
baseline_egfr := mean(egfr) over last 12 months excluding the most recent value
most_recent_egfr is not None
AND (baseline_egfr - most_recent_egfr) / baseline_egfr >= 0.25
```

**Evidence.** KDIGO 2024 Clinical Practice Guideline for the Evaluation and Management of Chronic Kidney Disease — explicit threshold for "acute on chronic kidney injury" workup. The 25% threshold is the KDIGO-endorsed alert level.

**Clinical impact.** PCP either:
1. Reviews recently-started medications (NSAIDs, ACEi/ARB, contrast exposure, antibiotics).
2. Orders confirmatory creatinine + urinalysis.
3. Escalates to nephrology if persistent.

**Adjacent rule considered + why R-05 won.**
- **"Absolute eGFR < 60"** (i.e., CKD diagnosis trigger). **Why R-05 won:** absolute rule fires on stable CKD patients every visit — informational, not action-prompting. Trajectory rule fires when something has *changed* — that's the action signal.
- **"Creatinine increase ≥ 30% from baseline"** — equivalent metric, different math. **Why R-05 won:** eGFR is the clinical-decision-relevant metric; PCPs reason in eGFR. Computing creatinine % then converting to eGFR is the same operation in a confusing form.

**FP scenarios + mitigations.**
- **Baseline shifted recently** (e.g., new medication contributed to a "new normal"). Mitigation: rule message references the baseline window; PCP can adjust mental model.
- **Single-measurement noise.** Mitigation: rule says "review confirmatory recheck" rather than "AKI is occurring."

**Status.** Designed; not yet implemented. Patient E in Phase 12 fixture set exercises this rule (eGFR drop 70 → 45).

---

### R-06 — Statin gap on diabetic ≥ 40 years old

**Trigger.**

```
diabetes in active_problem_list
AND patient_age >= 40
AND no_active_statin_in(active_meds)
AND no_documented_statin_intolerance_or_contraindication_in(allergy_list, problem_list)
```

**Evidence.** ADA Standards of Care in Diabetes (2024) Section 10 — Cardiovascular Disease and Risk Management. Recommendation: "moderate-intensity statin therapy in addition to lifestyle therapy for patients with diabetes aged 40–75 years without atherosclerotic cardiovascular disease (Grade A)."

**Clinical impact.** PCP either:
1. Initiates moderate-intensity statin (atorvastatin 20mg or equivalent).
2. Documents reason for non-prescription if guideline-discordant.
3. Reviews lipid panel and updates risk assessment.

**Adjacent rule considered + why R-06 won.**
- **"USPSTF statin recommendation for primary prevention in 40–75 year olds with ≥1 CVD risk factor and ≥10% 10-year risk"** — broader USPSTF recommendation. **Why R-06 won:** the USPSTF rule requires computing 10-year risk, which means an additional tool call (need labs + BP + smoking status) to compute. Diabetes-specific rule is a subset that fires deterministically without the risk-calculator tool. The full USPSTF rule is a candidate for R-08+ expansion.
- **"Any patient ≥ 40 with no statin"** — too broad, fires on healthy 41-year-olds with no risk factors. Bad signal-to-noise.

**FP scenarios + mitigations.**
- **Documented statin-intolerance** (myalgia, transaminitis). Mitigation: trigger explicitly excludes patients with statin intolerance / muscle disorder in the problem list. Adds a small false-negative risk if intolerance isn't well-documented.
- **Patient on PCSK9 inhibitor or other non-statin lipid therapy.** Mitigation: trigger checks for *any* lipid therapy, not just statins. (Implementation detail.)

**Status.** Designed; not yet implemented. No dedicated Phase 12 patient yet — Patient B (HbA1c trajectory) overlaps demographically and could exercise this rule too.

---

### R-07 — Systolic BP increase ≥ 20 mmHg on treated hypertension

**Trigger.**

```
htn in active_problem_list
AND any(antihypertensive in active_meds)
AND most_recent_systolic_bp - prior_systolic_bp >= 20
AND time_between(most_recent_bp, prior_bp) >= 30 days
```

**Evidence.** AHA/ACC 2017 Guideline for the Prevention, Detection, Evaluation, and Management of High Blood Pressure in Adults — addresses the criteria for treatment-failure detection. The ≥20 mmHg trigger is consistent with the guideline's "uncontrolled hypertension" framework.

**Clinical impact.** PCP either:
1. Verifies adherence (most common reason for treatment drift).
2. Adjusts dose or adds an additional agent.
3. Reviews for secondary causes if drift is unexplained.

**Adjacent rule considered + why R-07 won.**
- **"Most recent SBP > 140 (or treatment-target)"** — absolute rule. **Why R-07 won:** absolute SBP > 140 fires on every hypertensive patient in suboptimal control — the majority of the panel. Trajectory rule fires when control has *worsened* — actionable.
- **"Diastolic BP rise ≥ 10 mmHg"** — equivalent in spirit. **Why R-07 won:** SBP is the primary clinical-decision metric for hypertension management per AHA; DBP is secondary. Could be added later as R-07b but doesn't earn its own slot in the initial corpus.

**FP scenarios + mitigations.**
- **Single-measurement noise** (white-coat, recent activity). Mitigation: rule message says "consider repeat BP" rather than asserting treatment failure.
- **Patient stopped a med (PCP-directed) and BP is expected to drift up.** FP cost tier: medium. Acceptable; PCP context-aware override.

**Status.** Designed; not yet implemented. No dedicated Phase 12 patient — would need to add one or verify an existing Synthea patient has the right BP trajectory.

---

## What's NOT in the corpus and why

The corpus is small **on purpose**. Adding rules increases coverage but inflates false-positive surface; every rule we ship needs the criteria above met. These were considered and explicitly deferred:

| Rule | Why deferred |
|---|---|
| **SSRI + tramadol → serotonin syndrome** | High FP cost (tier 3), pilot data insufficient. Needs specialist sign-off + retrospective review of ≥100 cases first. |
| **Statin + macrolide / fibrate → myopathy** | Same as above. Real signal but the FP rate in primary care looks high based on retrospective surveys. |
| **Generic allergy-class + class-matching prescription** | R-03 covers PCN+beta-lactam (the highest-impact specific case); broader rule fires too often on weak signals. Candidate for R-08+ expansion. |
| **TSH out of range on levothyroxine** | Requires reasoning about target TSH ranges per indication (post-thyroidectomy ≠ Hashimoto's ≠ subclinical hypothyroidism). Too many sub-cases for v1. |
| **Diabetic neuropathy screening gap** | Annual foot exam tracking — true gap, but signal value is preventive screening, not visit-shaping. Filter 1 marginal pass; defer until preventive-care use case is in scope. |
| **Aspirin for primary prevention in older adults** | USPSTF in 2022 *removed* the recommendation for primary prevention in adults ≥60. The "wrong direction" risk (recommending against guideline) made us pause; will revisit when consolidated. |
| **Polypharmacy threshold (≥10 active meds)** | True signal, low FP cost (tier 1, informational only), but doesn't trigger an action by itself — the action depends on which meds. Belongs in patient-context section, not a rule-flag claim. |

The corpus is intentionally narrow — extension is a content / clinical-review problem, not an architectural one. Adding R-08 onward is a candidate for week-2 work (see [`.gauntlet/week2/candidates.md`](./.gauntlet/week2/candidates.md) — "Rule corpus expand from 5–8 to ~25 rules").

---

## Implementation status (as of 2026-05-01)

**Designed:** all 7 rules above — trigger conditions, evidence sources, clinical-impact rationale, adjacent-rule considerations, FP mitigations.

**Implemented:** **none yet.** The verifier currently allows `claim_type: rule_flag` per [`agent/schemas.py`](./agent/schemas.py) but no rule engine evaluates these claims yet. Rule-flag claims pass through the verifier on the same basis as `fact` claims — they need to cite a record that exists, but there's no programmatic check that the rule's trigger condition was actually met.

**Architecture for rule-engine landing:** when the engine ships, rules will be Python functions (one per rule, named `R_01_warfarin_nsaid`, `R_02_acei_kspare`, etc.) loaded into a registry. The verifier (or a sibling layer running before the verifier) iterates the registry against the retrieved records; rules that match get added to the patient context as available rule-flag citations. The LLM then has a defined set of `rule_flag` claims it CAN make; trying to make one outside the registry fails verification because the cited rule_id isn't in the retrieved set.

**This is week-2+ work.** Documenting the corpus now (criteria + initial 7 rules) is what *enables* the engine implementation — gives the engine designer a concrete spec to build against. Without this doc, the rule engine ships rules-as-prompts (instruction-soup) rather than a proper registry.

---

## Defense talking points

- "Why so few rules?" — *Quality bar. Three filters: clinical-action threshold, evidence quality (USPSTF / society guidelines preferred), false-positive cost tier. Rules that don't pass don't ship. Adding rules is a content problem; the architecture supports N rules with the same shape.*
- "What about [common rule X]?" — *See the "What's NOT in the corpus" table. Most common candidates were explicitly considered and deferred for documented reasons.*
- "How would you grow this to 25 rules?" — *Quarterly clinical-SME review. Each new rule goes through the same three filters + retrospective FP-rate verification on at least 100 cases. Goal: 25 rules by end of Q1, 50 by end of year.*
- "What if a clinic asks for a custom rule?" — *Same filter. We don't ship clinic-specific rules without evidence sourcing — alarm fatigue compounds across clinics if rules drift from common evidence base. Custom rules are a separate tier, gated behind a "clinic-specific" flag in the citation strength.*
- "How does the verifier interact with rules?" — *Rule-flag claims need to cite a rule_id that's in the registry's emitted records (same model as cited record IDs for facts). Fabricating a rule citation fails verification. Architectural firewall.*
- "Why didn't you ship rules in week 1?" — *The corpus design needed to land first; shipping a rule engine without the criteria doc would invite ad-hoc additions that erode the quality bar. Engine implementation is week-2 work; this doc is the spec.*
