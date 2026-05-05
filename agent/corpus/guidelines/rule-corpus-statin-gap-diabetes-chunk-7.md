---
chunk_id: rule-corpus-statin-gap-diabetes-chunk-7
section: S2.R06
source_url: https://github.com/openemr/AgentForge/blob/master/RULE_CORPUS.md
source_attribution: >
  Paraphrased from: Week-1 internal rule corpus, paraphrased into structural form.
  Source document: RULE_CORPUS.md rule R-06, AgentForge Clinical Co-Pilot repository.
  Evidence base: ADA Standards of Care in Diabetes (2024) Section 10 — Cardiovascular
  Disease and Risk Management; Grade A recommendation for moderate-intensity statin
  therapy in diabetic patients aged 40–75 years.
  Last verified: 2026-05-05.
---

# R-06: Statin Therapy Gap in Patients with Diabetes Aged 40 or Older

**Rule ID:** R-06. **Clinical domain:** cardiovascular risk reduction, diabetes management.

**Trigger condition.** The rule fires when ALL of the following are true: (1) diabetes mellitus appears in the active problem list; (2) the patient's age is 40 years or older; (3) no active statin or other lipid-lowering agent (including PCSK9 inhibitors and non-statin alternatives) appears in the active medication list; and (4) no documented statin intolerance, myopathy, rhabdomyolysis history, or other statin contraindication appears in the allergy list or problem list.

**Evidence basis.** The ADA Standards of Care in Diabetes (2024), Section 10 on Cardiovascular Disease and Risk Management, carries a Grade A recommendation: "moderate-intensity statin therapy in addition to lifestyle therapy for patients with diabetes aged 40 to 75 years without atherosclerotic cardiovascular disease." This is among the highest-evidence recommendations in the ADA guidelines. Moderate-intensity statin therapy (e.g., atorvastatin 20 mg or equivalent) is the default starting point; high-intensity therapy is recommended when ASCVD is already present.

**Clinical impact.** When the rule fires, the clinician considers: (1) initiate moderate-intensity statin therapy (atorvastatin 20 mg or equivalent) if no contraindication exists; (2) document the clinical reason for non-prescription if the decision is intentionally guideline-discordant; or (3) review a recent lipid panel and updated cardiovascular risk assessment before initiating.

**Why diabetes-specific beats the broader USPSTF statin rule.** The USPSTF recommendation for primary prevention of cardiovascular disease in adults aged 40 to 75 years with one or more CVD risk factors and a 10-year cardiovascular risk of 10% or higher was considered and deferred. That rule requires computing the 10-year ASCVD risk score, which demands additional data: blood pressure, LDL, smoking status, and sometimes additional lab values. The diabetes-specific rule fires deterministically from data already present in the problem list and medication list, without the risk-calculator dependency. The full USPSTF rule is a candidate for corpus expansion.

**False-positive scenarios and mitigations.** Documented statin intolerance — myalgia, transaminase elevation, or muscle disorder — is the primary false-positive mitigation. The trigger explicitly excludes patients with statin intolerance documented in the problem list or allergy field. The small false-negative risk arises when intolerance is poorly documented; clinicians should ensure intolerance is captured in a structured field rather than free text.

**Implementation status.** Designed; not yet implemented. Patient fixture B overlaps demographically with this rule and may exercise it in conjunction with the HbA1c trajectory rule.
