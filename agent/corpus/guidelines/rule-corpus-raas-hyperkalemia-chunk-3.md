---
chunk_id: rule-corpus-raas-hyperkalemia-chunk-3
section: S2.R02
source_url: https://github.com/openemr/AgentForge/blob/master/RULE_CORPUS.md
source_attribution: >
  Paraphrased from: Week-1 internal rule corpus, paraphrased into structural form.
  Source document: RULE_CORPUS.md rule R-02, AgentForge Clinical Co-Pilot repository.
  Evidence base: ACC/AHA Heart Failure Guidelines (2022), K+ monitoring guidance for
  RAAS plus mineralocorticoid receptor antagonist combinations.
  Last verified: 2026-05-05.
---

# R-02: ACE Inhibitor or ARB + Potassium-Sparing Diuretic + Recent K+ ≥ 5.0 mEq/L

**Rule ID:** R-02. **Clinical domain:** heart failure pharmacology, hyperkalemia risk.

**Trigger condition.** The rule fires when ALL of the following are true: (1) an ACE inhibitor (ACEi) or angiotensin receptor blocker (ARB) is in the active medication list; (2) a potassium-sparing diuretic — spironolactone, eplerenone, or amiloride — is also in the active medication list; and (3) the most recent serum potassium value is 5.0 mEq/L or higher within the last 6 months.

**Evidence basis.** The ACC/AHA Heart Failure Guidelines (2022) provide explicit guidance on potassium monitoring frequency in patients receiving a RAAS inhibitor (ACEi or ARB) combined with a mineralocorticoid receptor antagonist (spironolactone or eplerenone). The 5.0 mEq/L threshold is the guideline-identified alert boundary; values above 5.5 mEq/L are in the intervention-required range. This combination is a standard evidence-based heart failure treatment but carries a meaningful hyperkalemia risk that requires active surveillance.

**Clinical impact.** When the rule fires, the clinician considers: (1) hold the potassium-sparing diuretic and recheck potassium in 3 to 5 days; (2) reduce the ACEi or ARB dose and recheck potassium in 1 week; or (3) provide dietary potassium counseling if the elevation is borderline. The signal is action-shaping — it changes medication management at the visit.

**Why the trigger requires a recent potassium value.** An earlier candidate trigger — "ACEi or ARB plus potassium-sparing diuretic alone, with no potassium check" — was evaluated and rejected. That version fires on every patient on appropriate and actively monitored combination therapy, producing high alarm fatigue with no marginal information. Adding the K+ threshold makes the rule fire only when the monitoring data already shows a problem developing.

**False-positive scenarios and mitigations.** A potassium value measured during an acute illness or acute kidney injury episode that has since resolved represents the primary false-positive scenario. The trigger uses only the most recent potassium value; if a subsequent normal K+ was measured after the elevated reading, the rule does not fire. A false positive where the patient is already under cardiology surveillance for this exact combination is categorized as low cost — it is an informational reminder, not a redundant action directive.

**Implementation status.** Designed; not yet implemented. Patient fixture B in the Phase 12 test set exercises this rule.
