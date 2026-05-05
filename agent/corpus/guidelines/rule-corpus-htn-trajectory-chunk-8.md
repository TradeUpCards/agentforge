---
chunk_id: rule-corpus-htn-trajectory-chunk-8
section: S2.R07
source_url: https://github.com/openemr/AgentForge/blob/master/RULE_CORPUS.md
source_attribution: >
  Paraphrased from: Week-1 internal rule corpus, paraphrased into structural form.
  Source document: RULE_CORPUS.md rule R-07, AgentForge Clinical Co-Pilot repository.
  Evidence base: AHA/ACC 2017 Guideline for the Prevention, Detection, Evaluation,
  and Management of High Blood Pressure in Adults; uncontrolled hypertension framework.
  Last verified: 2026-05-05.
---

# R-07: Systolic BP Increase ≥ 20 mmHg on Treated Hypertension

**Rule ID:** R-07. **Clinical domain:** hypertension management, treatment failure detection.

**Trigger condition.** The rule fires when ALL of the following are true: (1) hypertension appears in the active problem list; (2) at least one antihypertensive agent appears in the active medication list; (3) the most recent systolic blood pressure is at least 20 mmHg higher than the prior systolic blood pressure reading; and (4) the interval between the two blood pressure measurements is at least 30 days. The 30-day minimum prevents noise from same-visit repeat measurements or short-interval rechecks.

**Evidence basis.** The AHA/ACC 2017 Guideline for the Prevention, Detection, Evaluation, and Management of High Blood Pressure in Adults addresses treatment-failure detection. A systolic BP rise of 20 mmHg or more in a patient on antihypertensive therapy is consistent with the guideline's "uncontrolled hypertension" framework — specifically, the criterion for evaluating whether current therapy is adequate or requires adjustment. Systolic blood pressure is the primary decision metric per AHA guidelines; diastolic BP is a secondary metric.

**Clinical impact.** When the rule fires, the clinician considers: (1) verify medication adherence, which is the most common explanation for BP drift in treated patients; (2) adjust the antihypertensive dose or add an agent from a different drug class; or (3) evaluate for secondary hypertension causes if the drift is otherwise unexplained. The rule message frames the signal as "consider repeat BP confirmation" rather than asserting treatment failure, acknowledging single-measurement variability.

**Why trajectory beats absolute BP threshold.** The alternative "most recent SBP above 140 mmHg" rule was evaluated and rejected. An absolute SBP above 140 fires on every hypertensive patient in suboptimal control — a large fraction of the primary-care panel, every visit. Immediate alarm fatigue. The trajectory rule fires when BP control has actively worsened, which is the actionable signal.

**Why systolic beats diastolic trajectory.** A "diastolic BP rise of 10 mmHg from prior value" alternative was considered. The AHA guideline uses systolic BP as the primary clinical-decision metric. The diastolic trajectory rule could be added later as a companion rule but does not earn an independent corpus slot in the initial corpus.

**False-positive scenarios and mitigations.** White-coat hypertension and measurement immediately after physical exertion are known single-measurement sources of falsely elevated BP. The rule message recommends confirmatory repeat measurement rather than directing immediate medication change. A clinician-directed medication hold (e.g., stopping a beta-blocker before surgery) causing expected BP drift is a medium false-positive cost scenario — the clinician context overrides in under 30 seconds.

**Implementation status.** Designed; not yet implemented. No dedicated Phase 12 patient fixture for this rule yet; a fixture with the appropriate BP trajectory needs to be added or confirmed from existing Synthea-imported data.
