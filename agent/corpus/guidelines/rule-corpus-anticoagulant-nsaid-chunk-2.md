---
chunk_id: rule-corpus-anticoagulant-nsaid-chunk-2
section: S2.R01
source_url: https://github.com/openemr/AgentForge/blob/master/RULE_CORPUS.md
source_attribution: >
  Paraphrased from: Week-1 internal rule corpus, paraphrased into structural form.
  Source document: RULE_CORPUS.md rule R-01, AgentForge Clinical Co-Pilot repository.
  Evidence base: AHRQ High-Alert Medications list (2014, updated 2022); Chest 2018
  antithrombotic guidelines; UpToDate "Drug interactions of warfarin."
  Last verified: 2026-05-05.
---

# R-01: Warfarin + New NSAID Prescription — Bleeding Risk Interaction

**Rule ID:** R-01. **Clinical domain:** anticoagulation safety.

**Trigger condition.** The rule fires when a patient has an active prescription for warfarin AND a new prescription (within the last 30 days) for any NSAID, including ibuprofen, naproxen, or ketorolac. Topical NSAID formulations (e.g., diclofenac gel) are excluded from the trigger because transdermal absorption is substantially lower than oral administration.

**Evidence basis.** The AHRQ High-Alert Medications list (2014, updated 2022) designates warfarin as a high-alert medication requiring additional safety monitoring. The Chest 2018 antithrombotic guidelines explicitly identify the warfarin-plus-NSAID combination as a major bleeding-risk pairing. UpToDate drug-interaction guidance corroborates the recommendation to avoid concurrent use or to intensify INR monitoring when concurrent use is unavoidable.

**Clinical impact.** When the rule fires, the clinician considers two paths: (1) discontinue the NSAID and offer acetaminophen as a safer analgesic substitute; or (2) continue the NSAID with an INR check within 5 to 7 days and add PPI prophylaxis to reduce gastrointestinal bleeding risk. This is a Filter 1 pass — the visit trajectory changes in a concrete, observable way.

**False-positive scenarios and mitigations.** A single-day NSAID prescribed for an acute injury where INR is already monitored represents a medium false-positive cost scenario: the rule fires but the clinician can override in under 30 seconds after reviewing the monitoring history. Topical NSAIDs are excluded from the trigger to avoid this scenario. This rule is categorized as High false-positive cost per the selection criteria; its true-positive rate in primary-care prescription streams is considered sufficient because the warfarin-NSAID combination is a well-validated major drug interaction.

**Adjacent rule considered and deferred.** "Warfarin + any CYP450-affecting antibiotic" (e.g., metronidazole, TMP-SMX, fluconazole) was considered for this slot. NSAIDs were chosen because they are far more common in the outpatient prescription stream, yielding greater clinical mileage per firing. The CYP-affecting antibiotic rule is a candidate for corpus expansion.

**Implementation status.** Designed; not yet implemented as of corpus authoring date. The trigger logic references `active_meds` and `new_meds_last_30d` fields from the structured medication record. Patient fixture A in the Phase 12 test set is designed to exercise this rule.
