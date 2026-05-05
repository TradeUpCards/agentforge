---
chunk_id: rule-corpus-egfr-decline-chunk-6
section: S2.R05
source_url: https://github.com/openemr/AgentForge/blob/master/RULE_CORPUS.md
source_attribution: >
  Paraphrased from: Week-1 internal rule corpus, paraphrased into structural form.
  Source document: RULE_CORPUS.md rule R-05, AgentForge Clinical Co-Pilot repository.
  Evidence base: KDIGO 2024 Clinical Practice Guideline for the Evaluation and Management
  of Chronic Kidney Disease; 25% eGFR decline threshold for acute-on-chronic workup.
  Last verified: 2026-05-05.
---

# R-05: eGFR Decrease ≥ 25% from Established Baseline

**Rule ID:** R-05. **Clinical domain:** nephrology, acute-on-chronic kidney injury detection.

**Trigger condition.** The rule fires when the most recent estimated glomerular filtration rate (eGFR) is at least 25% lower than the patient's established baseline. The baseline is calculated as the mean of all eGFR values recorded in the prior 12 months, excluding the most recent value. Mathematically: the rule fires when `(baseline_eGFR − most_recent_eGFR) / baseline_eGFR ≥ 0.25`.

**Evidence basis.** The KDIGO 2024 Clinical Practice Guideline for the Evaluation and Management of Chronic Kidney Disease specifies a 25% decline from established baseline as the alert threshold warranting acute-on-chronic kidney injury workup. This threshold is explicitly endorsed in the KDIGO framework as the level at which a systematic medication review and confirmatory testing are indicated.

**Clinical impact.** When the rule fires, the clinician considers: (1) review recently started or dose-adjusted medications that affect renal perfusion or tubular function, including NSAIDs, ACE inhibitors, ARBs, nephrotoxic antibiotics, and contrast agents; (2) order a confirmatory serum creatinine and urinalysis; or (3) escalate to nephrology if the decline persists on repeat testing. The rule message cites the baseline window so the clinician can assess whether the baseline itself shifted due to a known cause.

**Why trajectory beats absolute CKD threshold.** The alternative "absolute eGFR below 60 mL/min/1.73 m²" rule — a standard CKD diagnosis threshold — was evaluated and rejected. That rule fires on every stable CKD patient at every visit, providing informational context rather than an action signal. The trajectory rule fires only when the eGFR has acutely changed, which is when clinical action is warranted.

**Why eGFR beats creatinine percentage.** A "serum creatinine increase ≥ 30% from baseline" alternative was also considered. eGFR was chosen because it is the clinician-facing metric for kidney function decision-making. Expressing the same calculation in terms of creatinine percentage adds a conversion step without adding clinical meaning.

**False-positive scenarios and mitigations.** A patient whose baseline recently shifted due to a new medication may have a "new normal" that the 12-month lookback does not fully reflect. The rule message references the baseline window explicitly, allowing the clinician to adjust their interpretation. Single-measurement eGFR noise is mitigated by the rule message framing: "consider confirmatory recheck" rather than asserting that AKI is occurring.

**Implementation status.** Designed; not yet implemented. Patient fixture E in the Phase 12 test set exercises this rule with an eGFR drop from 70 to 45 mL/min/1.73 m².
