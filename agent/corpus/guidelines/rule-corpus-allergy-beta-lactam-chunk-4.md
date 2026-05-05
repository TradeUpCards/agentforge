---
chunk_id: rule-corpus-allergy-beta-lactam-chunk-4
section: S2.R03
source_url: https://github.com/openemr/AgentForge/blob/master/RULE_CORPUS.md
source_attribution: >
  Paraphrased from: Week-1 internal rule corpus, paraphrased into structural form.
  Source document: RULE_CORPUS.md rule R-03, AgentForge Clinical Co-Pilot repository.
  Evidence base: AAAAI/ACAAI Joint Task Force "Practice Parameter for Drug Allergy"
  (2010, updated 2022); UpToDate "Allergy evaluation for beta-lactam antibiotics."
  Last verified: 2026-05-05.
---

# R-03: Penicillin Allergy + New Beta-Lactam Prescription — Cross-Reactivity Alert

**Rule ID:** R-03. **Clinical domain:** drug allergy, antibiotic safety.

**Trigger condition.** The rule fires when penicillin appears in the patient's allergy list (any documented severity) AND a new prescription for any beta-lactam antibiotic was issued within the last 30 days. The beta-lactam class includes amoxicillin, ampicillin, cefalexin, cefuroxime, ceftriaxone, and related agents.

**Evidence basis.** The AAAAI/ACAAI Joint Task Force "Practice Parameter for Drug Allergy" (2010, updated 2022) provides the definitive guidance on penicillin allergy evaluation and beta-lactam cross-reactivity. UpToDate corroborates that approximately 80% of patients with a documented penicillin allergy can safely tolerate penicillin on formal challenge — the allergy is frequently over-reported or based on childhood reactions that are unlikely to represent true IgE-mediated hypersensitivity. Cross-reactivity rates between penicillin and cephalosporins vary by generation: approximately 5% for first-generation cephalosporins (sharing the R1 side chain) and approximately 1% for second-generation and later cephalosporins.

**Clinical impact.** When the rule fires, the clinician considers: (1) switch to a non-beta-lactam antibiotic class such as a macrolide or fluoroquinolone; (2) review the allergy history in detail to determine whether it represents a true IgE-mediated reaction or a low-risk documented reaction that may be safely challenged; or (3) refer the patient for formal allergy testing and drug challenge. The rule message includes the cephalosporin generation and the relevant cross-reactivity probability — it surfaces data rather than refusing prescribing outright.

**Why penicillin-specific rather than a generic allergy-class rule.** A broader "any allergy plus any prescription in the same drug class" rule was evaluated. The penicillin-plus-beta-lactam specific rule was chosen because: (1) PCN allergy is the most commonly over-reported drug allergy in primary care, creating a disproportionate clinical-decision surface; and (2) the harm potential for a missed true allergy is anaphylaxis, placing this in the High false-positive cost tier. Specificity makes the rule more defensible than a generic version that would fire on weaker signals such as aspirin in an NSAID-intolerant patient.

**False-positive scenarios and mitigations.** A documented "rash with penicillin at age 4" is categorized as a medium false-positive cost: the rule fires, the clinician spends approximately 60 seconds reviewing the allergy note, and the outcome is a more careful allergy documentation — which is itself a clinical benefit. This is acceptable.

**Implementation status.** Designed; not yet implemented. Patient fixture C in the Phase 12 test set exercises this rule.
