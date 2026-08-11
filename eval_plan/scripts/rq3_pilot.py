#!/usr/bin/env python3
"""
eval_plan/scripts/rq3_pilot.py

RQ3 Part A PILOT — per EVAL_RQ3_ask_pipeline_and_comparison.md, scoped down
per user decision: a small (~12-15 question), single-pass run through current
main's default pipeline (ASK_USE_NEW_PIPELINE=true — direct LLM-SQL, no fast
lanes) to validate the harness and get a rough read on behaviour/cost before
deciding whether to scale to the full 83-question eval/gold.csv set, and
before building out C1 (legacy pipeline)/C3 (RAG) arms for the full Part B
three-way comparison.

This makes REAL LLM API calls (SQL generation is unconditional on this
pipeline, per ask_service._generate_sql — confirmed no fast lane exists on
main). Cost/time scales with N; keep N small until reviewed.

Run from repo root: venv/bin/python3 eval_plan/scripts/rq3_pilot.py
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

def pick_pilot_rows(rows: list[dict], n: int = 14) -> list[dict]:
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
        if i > 1000:
            break
    return picked[:n]


def main() -> int:
    from create_app import create_app

    with open(GOLD_PATH, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    pilot = pick_pilot_rows(rows, n=14)

    app = create_app()
    with app.app_context():
        from backend.services.ask_service import answer_question_stream

        results = []
        for row in pilot:
            q = row["question"]
            print(f"\n{'='*100}\n{row['id']} [{row['category']}]: {q}")
            print(f"  gold_answer={row['gold_answer']!r} type={row['gold_answer_type']} out_of_scope={row['is_out_of_scope']}")

            t0 = time.time()
            final_result = None
            error = None
            sql_seen = None
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
                "gold_answer": row["gold_answer"], "is_out_of_scope": row["is_out_of_scope"],
                "elapsed_ms": elapsed_ms, "error": error,
            }
            if final_result:
                entry["sql"] = final_result.get("sql")
                entry["actual_answer"] = final_result.get("actual_answer")
                entry["llm_rephrased_answer"] = final_result.get("llm_rephrased_answer")
                entry["warnings"] = final_result.get("warnings")
                prov = final_result.get("query_provenance") or {}
                entry["strategy"] = prov.get("strategy")
                entry["llm_meta_provider"] = (final_result.get("llm_meta") or {}).get("provider")
                print(f"  strategy={entry['strategy']}  elapsed_ms={elapsed_ms}")
                print(f"  sql={entry['sql']!r}")
                print(f"  actual_answer={entry['actual_answer']!r}")
                print(f"  llm_rephrased_answer={(entry['llm_rephrased_answer'] or '')[:200]!r}")
                gold_str = str(row["gold_answer"])
                answer_blob = f"{entry['actual_answer']} {entry['llm_rephrased_answer']}"
                entry["gold_number_found"] = gold_str.split("=")[0].strip() in answer_blob if gold_str else None
            else:
                print(f"  NO RESULT — error={error}  elapsed_ms={elapsed_ms}")

            results.append(entry)

        evidence_path = os.path.join(REPO_ROOT, "eval_plan", "evidence", "RQ3_pilot_raw_output.json")
        with open(evidence_path, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, default=str)
        print(f"\n\nWrote {len(results)} results to {evidence_path}")

        n_errors = sum(1 for r in results if r.get("error"))
        n_with_result = sum(1 for r in results if "sql" in r)
        print(f"Errors: {n_errors}/{len(results)}  With result: {n_with_result}/{len(results)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
