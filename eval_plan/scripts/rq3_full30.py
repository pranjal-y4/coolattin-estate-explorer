#!/usr/bin/env python3
"""
eval_plan/scripts/rq3_full30.py

RQ3 Part A — scaled-up run (N=30) through current main's default pipeline
(ASK_USE_NEW_PIPELINE=true, direct LLM-SQL). Follows the 14-question pilot in
rq3_pilot.py; same harness, same stratified round-robin selection logic,
widened to n=30 for broader per-category coverage. Single pass, no repeats.

Run from repo root: venv/bin/python3 eval_plan/scripts/rq3_full30.py
"""
from __future__ import annotations

import csv
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
GOLD_PATH = os.path.join(REPO_ROOT, "eval", "gold.csv")


def pick_rows(rows: list[dict], n: int) -> list[dict]:
    from collections import defaultdict
    by_cat = defaultdict(list)
    for r in rows:
        by_cat[r["category"]].append(r)
    picked = []
    cats = sorted(by_cat.keys())
    i = 0
    while len(picked) < n and any(by_cat.values()):
        cat = cats[i % len(cats)]
        if by_cat[cat]:
            picked.append(by_cat[cat].pop(0))
        i += 1
        if i > 2000:
            break
    return picked[:n]


def score(row: dict, entry: dict) -> str:
    """Best-effort scalar scoring: does the gold numeric value appear in the answer text?"""
    gold = str(row.get("gold_answer") or "").strip()
    if row.get("gold_answer_type") != "scalar":
        return "not_scalar"
    if not gold or gold.startswith("N/A"):
        return "n/a_gold"
    blob = f"{entry.get('actual_answer') or ''} {entry.get('llm_rephrased_answer') or ''}"
    # normalise thousands separators for comparison
    gold_norm = gold.replace(",", "")
    blob_norm = blob.replace(",", "")
    return "correct" if gold_norm and gold_norm in blob_norm else "wrong"


def main() -> int:
    from create_app import create_app

    with open(GOLD_PATH, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    picked = pick_rows(rows, n=30)

    app = create_app()
    with app.app_context():
        from backend.services.ask_service import answer_question_stream

        results = []
        for idx, row in enumerate(picked, 1):
            q = row["question"]
            print(f"\n{'='*100}\n[{idx}/{len(picked)}] {row['id']} [{row['category']}]: {q}")
            print(f"  gold_answer={row['gold_answer']!r} type={row['gold_answer_type']} out_of_scope={row['is_out_of_scope']}")

            t0 = time.time()
            final_result = None
            error = None
            try:
                for chunk in answer_question_stream(q, townland_hint=None, include_sql=True, force_llm=False):
                    if not chunk.startswith("data: "):
                        continue
                    payload = json.loads(chunk[len("data: "):])
                    if payload.get("type") == "result":
                        final_result = payload
                    elif payload.get("type") == "error":
                        error = payload.get("message")
            except Exception as exc:
                error = f"EXCEPTION: {exc}"
            elapsed_ms = int((time.time() - t0) * 1000)

            entry = {
                "id": row["id"], "category": row["category"], "question": q,
                "gold_answer": row["gold_answer"], "gold_answer_type": row["gold_answer_type"],
                "is_out_of_scope": row["is_out_of_scope"],
                "elapsed_ms": elapsed_ms, "error": error,
            }
            if final_result:
                entry["sql"] = final_result.get("sql")
                entry["actual_answer"] = final_result.get("actual_answer")
                entry["llm_rephrased_answer"] = final_result.get("llm_rephrased_answer")
                entry["warnings"] = final_result.get("warnings")
                prov = final_result.get("query_provenance") or {}
                entry["strategy"] = prov.get("strategy")
                print(f"  strategy={entry['strategy']}  elapsed_ms={elapsed_ms}")
                print(f"  sql={entry['sql']!r}")
                print(f"  actual_answer={entry['actual_answer']!r}")
            else:
                print(f"  NO RESULT — error={error}  elapsed_ms={elapsed_ms}")

            entry["verdict"] = score(row, entry)
            print(f"  verdict={entry['verdict']}")
            results.append(entry)

        evidence_path = os.path.join(REPO_ROOT, "eval_plan", "evidence", "RQ3_full30_raw_output.json")
        with open(evidence_path, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, default=str)

        n_errors = sum(1 for r in results if r.get("error"))
        n_correct = sum(1 for r in results if r["verdict"] == "correct")
        n_wrong = sum(1 for r in results if r["verdict"] == "wrong")
        n_scalar = sum(1 for r in results if r["verdict"] in ("correct", "wrong"))
        print(f"\n\n{'='*100}\nSUMMARY: n={len(results)}  errors={n_errors}  "
              f"scalar_scorable={n_scalar}  correct={n_correct}  wrong={n_wrong}  "
              f"accuracy_on_scalar={n_correct/n_scalar*100:.1f}%" if n_scalar else "")
        print(f"Wrote {len(results)} results to {evidence_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
