---
chunk_id: rule-corpus-a1c-trajectory-chunk-5
section: S2.R04
source_url: https://github.com/openemr/AgentForge/blob/master/RULE_CORPUS.md
source_attribution: >
  Paraphrased from: Week-1 internal rule corpus, paraphrased into structural form.
  Source document: RULE_CORPUS.md rule R-04, AgentForge Clinical Co-Pilot repository.
  Evidence base: ADA Standards of Care in Diabetes (2024) Section 6 — Glycemic Targets,
  treatment intensification framework.
  Last verified: 2026-05-05.
---

# R-04: HbA1c Absolute Increase ≥ 1.0% Across Two Consecutive Measurements

**Rule ID:** R-04. **Clinical domain:** diabetes management, glycemic trajectory monitoring.

**Trigger condition.** The rule fires when ALL of the following are true: (1) a prior HbA1c value exists in the record; (2) the most recent HbA1c is at least 1.0 percentage point higher than the prior value; (3) the interval between the two measurements is at least 90 days; and (4) the interval between the two measurements is no more than 365 days. The 90-day minimum floor prevents false signals from same-week rapid-recheck variability. The 365-day ceiling avoids comparing measurements too far apart to represent a meaningful clinical trajectory.

**Evidence basis.** The ADA Standards of Care in Diabetes (2024), Section 6 on Glycemic Targets, establishes a framework for treatment intensification. An absolute rise in HbA1c of 1.0 percentage point or more within 12 months is one of the named intensification trigger criteria. This is an absolute threshold, not a relative one — the ADA framework uses absolute values because relative percentage changes amplify noise at low baseline values.

**Clinical impact.** When the rule fires, the clinician considers: (1) add or escalate a glucose-lowering agent; (2) review medication adherence, which is the most common cause of HbA1c drift in patients with established diabetes; or (3) investigate for concurrent illness, new corticosteroid exposure, or other secondary causes of glycemic worsening. The trajectory signal targets patients who need attention now, rather than the entire diabetic panel.

**Why trajectory beats absolute threshold.** The alternative "HbA1c above the target of 7.0% for most adults" rule was evaluated and rejected. That version fires on every patient with diabetes whose HbA1c is anywhere above the target — the majority of a typical diabetic primary-care panel, every visit. Immediate alarm fatigue results. The trajectory rule fires only when something has changed for the worse, which is the clinically actionable signal.

**Why absolute beats relative change.** A "relative HbA1c increase of 15%" alternative was also evaluated. The ADA names an absolute threshold, not relative. Relative changes amplify noise: a jump from 5.5% to 6.3% is a 15% relative increase but has no clinical significance.

**False-positive scenarios and mitigations.** A patient on corticosteroids (oral burst, inhaled high-dose, or parenteral) may show a transient HbA1c rise. The rule message includes a steroid-use prompt so the clinician can assess whether the trajectory reflects medication effect. Post-acute-illness measurement elevation is another scenario; false-positive cost is medium and clinician review takes under one minute.

**Implementation status.** Designed; not yet implemented. Patient fixture B in the Phase 12 test set has a 6.8% to 7.5% to 8.4% trajectory designed to exercise this rule.
