# Evaluation Spec — RQ5: Web Interface Usability (User Study)

Maps to: Section 6.6. Shared metric definitions in `EVAL_00_master_plan.md`.

## 1. What this RQ claims
Non-technical users (historians, genealogists) can complete realistic tasks with
the interface, reported through standard usability measures.

## 2. Metrics

| Metric | Formula | Target band | Ground truth |
|--------|---------|-------------|--------------|
| SUS score | odd items (v-1), even items (5-v), sum ×2.5, mean over N | >= 68 average; see bands | participant responses |
| Task success rate (per task) | successes / attempts | >= ~78% is a common bar | observed completion |
| Time-on-task (per task) | median seconds, with range | context-dependent | timing |
| Error rate (per task) | errors / attempts | lower is better | observation |
| Single Ease Question (optional) | mean 1-7 per task | higher is easier | per-task rating |

SUS adjective bands (Bangor et al.): below 51 poor, 51 to 68 acceptable, 68 to
80.3 good, above 80.3 excellent. Report the band alongside the number.

## 3. Study design required
- **Ethics**: approval reference and date; consent form in the appendix.
- **Participants**: N, recruitment method, background. State N honestly; a small
  study is fine if reported as such.
- **Tasks**: find a person, explore a townland, read census demographics,
  inspect landscape features, get estate context. Fix the wording so every
  participant attempts the same tasks.
- **Protocol**: think-aloud during tasks, SUS at the end, timing per task,
  observer notes for errors and themes.

## 4. Procedure
1. Brief the participant, obtain consent, start the think-aloud.
2. Present each task; record success or failure, time, and errors.
3. Administer the ten-item SUS at the end.
4. Run `sus_score.py` on the raw responses to compute per-participant and mean
   SUS.
5. Code the think-aloud notes into themes; select paraphrased quotes per theme.

## 5. Results tables to fill

SUS:

| Participant | SUS score |
|-------------|-----------|
| P1 | |
| ... | |
| Mean (N=__) | |

Band: __.

Task performance:

| Task | Success rate % | Median time (s) | Errors |
|------|----------------|-----------------|--------|
| Find a person | | | |
| Explore a townland | | | |
| Read census demographics | | | |
| Inspect landscape features | | | |
| Get estate context | | | |

Themes: list each coded theme with a paraphrased supporting quote and how many
participants raised it.

## 6. Rating and interpretation
- A SUS mean with N and a band is the headline. With a small N, report it as
  formative and do not over-claim.
- Pair task success with time. High success but long times signals a usability
  cost worth naming in the discussion.
- Report what the feedback changed or would change, so the study connects to the
  design rather than sitting as a standalone score.

## 7. Honest-reporting notes (read first if the study is not run)
Do not fabricate SUS numbers, success rates, or quotes. Two honest paths:
- **Pilot**: run 1 to 3 participants now, report as a pilot with N stated,
  treat results as formative, and note the small sample as a limitation.
- **Designed but not run**: present the full design and instruments in the
  appendix and move execution to future work, stating plainly it was not run.

Either path is defensible at master's standard. An invented score is not.
