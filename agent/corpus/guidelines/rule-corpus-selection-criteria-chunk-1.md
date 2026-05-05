---
chunk_id: rule-corpus-selection-criteria-chunk-1
section: S1
source_url: https://github.com/openemr/AgentForge/blob/master/RULE_CORPUS.md
source_attribution: >
  Paraphrased from: Week-1 internal rule corpus, paraphrased into structural form.
  Source document: RULE_CORPUS.md, AgentForge Clinical Co-Pilot repository.
  Describes the three-filter selection criteria that govern which clinical rules
  are included in the corpus. Last verified: 2026-05-05.
---

# Clinical Rule Corpus: Selection Criteria Framework

The AgentForge rule corpus applies three sequential filters before any clinical rule is included. A rule must pass all three filters to be included in the corpus.

**Filter 1: Clinical Impact Threshold.** The rule must plausibly change a treatment plan, prompt an escalation, or alter the visit's trajectory. Purely contextual signals (e.g., "patient is over age 65") belong in the patient-context block, not as rule-flag claims. Passing example: "warfarin + new NSAID" — this finding changes the visit because the clinician must either discontinue the NSAID or tighten INR monitoring. Failing example: a simple age-based flag that does not trigger a specific action.

**Filter 2: Evidence Quality.** Rules are accepted in descending order of source authority: (1) USPSTF Grade A or B recommendation; (2) specialty-society guideline with explicit threshold values (ADA, KDIGO, AHA/ACC, NCCN) citing chapter and year; (3) UpToDate or peer-reviewed clinical decision-support reference with cited primary literature; (4) expert consensus in a peer-reviewed journal as a last resort. Rules sourced from "common practice," vendor libraries, or informal clinical advice are not accepted. A rule without a citable source does not ship.

**Filter 3: False-Positive Cost.** Alarm fatigue is a real clinical harm — every false positive trains the clinician to dismiss the next signal. Three tiers govern treatment: Low FP cost (informational signal, false positive provides no useful data) — ship the rule. Medium FP cost (signal asks the clinician to verify something, false positive costs approximately 30 seconds) — ship if true-positive rate is at least 80% in pilot data. High FP cost (signal asks the clinician to act or change a plan, false positive may cause treatment harm or erode trust) — ship only if true-positive rate is at least 95% AND the signal is reversible by visit-time review. Drug-drug interaction rules fall in the High tier; an unknown true-positive rate means the rule does not ship pending a pilot study or specialist sign-off.

These filters are the corpus's load-bearing quality constraint. Clinical-significance claims fire only when a matched rule exists. Expanding the agent's clinical-claim surface requires adding rules through this review process — it is a content problem, not an architectural problem. The corpus is intentionally narrow: extension follows quarterly clinical-subject-matter-expert review, with each new rule requiring retrospective false-positive verification on at least 100 cases before deployment.
