# RQ5 Results — Web Interface Usability

Maps to Section 6.6. **Not run.** This requires real human participants — I cannot
fabricate SUS scores, task success rates, or think-aloud quotes, and the eval plan
itself says so explicitly (§7 of `EVAL_RQ5_usability_user_study.md`: "Do not fabricate
SUS numbers, success rates, or quotes").

Two honest paths remain, both defensible at master's standard per the eval plan's own
guidance:

1. **Pilot**: recruit 1-3 participants now (historians, genealogists, or graduate
   students), run the five tasks listed in the eval plan (find a person, explore a
   townland, read census demographics, inspect landscape features, get estate
   context), administer the SUS instrument, and report it explicitly as a small-N
   pilot with formative results.
2. **Designed but not run**: present the full study design (tasks, protocol, consent
   form, SUS instrument) in an appendix, and state plainly in §6.6 that execution is
   future work.

`eval/manual_scoring_sheet.csv` already exists in the repo and appears to be a
pre-built scoring instrument for exactly this purpose (Correctness/Faithfulness/
Historical Appropriateness rubric, per `evaluation_pack.md`'s D11 section) — worth
checking whether it's already populated with a task list you can reuse for either path.

If you want, I can:
- Write `sus_score.py` (raw ten-item responses → per-participant and mean SUS) now,
  ready for whenever real responses exist — this is pure computation, no fabrication
  risk.
- Draft the task wording and consent form text for a pilot session.

Neither of those requires a decision from you about whether the study will actually
run — say if you want either prepared in advance.
