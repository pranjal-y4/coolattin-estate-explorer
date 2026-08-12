from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from backend.services.ask_eval import GOLDEN_CASES, EvalCase  # noqa: E402


_FALLBACK_CASES = [c for c in GOLDEN_CASES if c.expected_route == "llm"]
_INSCOPE   = [c for c in _FALLBACK_CASES if not c.is_out_of_scope]
_OUTSCOPE  = [c for c in _FALLBACK_CASES if c.is_out_of_scope]


def _run_oracle(case: EvalCase, db_path: Path) -> tuple[bool, Any]:
    if not case.ground_truth_sql or case.ground_truth_type is None:
        return None, None
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(case.ground_truth_sql).fetchall()
        conn.close()
        if not rows:
            return True, None
        row0 = dict(rows[0])
        if case.ground_truth_key and case.ground_truth_key in row0:
            return True, row0[case.ground_truth_key]
        return True, list(row0.values())[0]
    except Exception as exc:
        return False, str(exc)[:80]


def _ask_live(
    question: str,
    base_url: str,
    timeout_s: int = 300,
    chunk_timeout_s: int = 90,
) -> dict[str, Any]:
    url = f"{base_url}/api/ask/query"
    payload = {"question": question}

    out: dict[str, Any] = {
        "status": "error",
        "final_sql": None,
        "result_rows": [],
        "answer_text": "",
        "llm_synthesized_text": "",
        "verifier_verdict": None,
        "verifier_unsupported": [],
        "gate_outcome": "not_applied",
        "gate_blocked_synthesis": "",
        "gate_violations": [],
        "query_strategy": "",
        "elapsed_s": 0.0,
        "error": None,
    }

    t0 = time.perf_counter()

    def _do_request() -> None:
        try:
            resp = requests.post(
                url,
                json=payload,
                stream=True,
                timeout=(10, chunk_timeout_s),
                headers={"Accept": "text/event-stream"},
            )
            resp.raise_for_status()

            buffer = ""
            for raw_line in resp.iter_lines(decode_unicode=True):
                if deadline_event.is_set():
                    break
                if raw_line.startswith("data:"):
                    buffer = raw_line[5:].strip()
                elif raw_line == "" and buffer:
                    try:
                        event = json.loads(buffer)
                        _process_sse_event(event, out)
                    except json.JSONDecodeError:
                        pass
                    buffer = ""

            if not deadline_event.is_set():
                out["status"] = "ok"

        except requests.Timeout:
            out["status"] = "timeout"
            out["error"] = "chunk read timeout (90s without new data)"
        except Exception as exc:
            out["status"] = "error"
            out["error"] = str(exc)[:120]

    deadline_event = threading.Event()
    thread = threading.Thread(target=_do_request, daemon=True)
    thread.start()
    thread.join(timeout=timeout_s)

    if thread.is_alive():
        deadline_event.set()
        out["status"] = "timeout"
        out["error"] = f"wall-clock deadline reached ({timeout_s}s)"

    out["elapsed_s"] = round(time.perf_counter() - t0, 2)
    return out


def _process_sse_event(event: dict, out: dict) -> None:
    etype = event.get("type")
    if etype != "result":
        return

    structured_output = event.get("structured_output", {})
    queries = structured_output.get("queries", {})
    out["final_sql"] = (
        queries.get("local_sqlite_query")
        or queries.get("vrti_postgresql_query")
    )

    db_table = structured_output.get("processed_tables", {}).get("local_database", {})
    out["result_rows"] = db_table.get("rows", [])

    out["answer_text"] = (
        event.get("llm_rephrased_answer")
        or event.get("answer")
        or event.get("actual_answer")
        or ""
    )
    out["llm_synthesized_text"] = event.get("llm_rephrased_answer") or ""

    prov = event.get("query_provenance", {})
    verifier = prov.get("verifier", {})
    out["verifier_verdict"] = verifier.get("verdict")
    out["verifier_unsupported"] = verifier.get("unsupported_claims", [])

    out["gate_outcome"] = prov.get("numeric_gate_outcome", "not_applied")
    out["gate_blocked_synthesis"] = prov.get("gate_blocked_synthesis", "")
    out["gate_violations"] = prov.get("gate_violations", [])
    out["query_strategy"] = prov.get("strategy", "")


def _extract_numbers(text: str) -> set[str]:
    stripped = re.sub(r"(?m)^\s*\d+\.\s+", " ", text or "")
    cleaned = re.sub(r'(?<=\d),(?=\d{3}(?:[^\d]|$))', '', stripped)
    return {m for m in re.findall(r"-?\d+(?:\.\d+)?", cleaned)}


def _allowed_numbers(result_rows: list[dict], question: str = "") -> set[str]:
    base = _extract_numbers(json.dumps(result_rows, default=str))
    base |= _extract_numbers(question)
    expanded: set[str] = set(base)
    for tok in set(base):
        if tok.lstrip("-").isdigit() and len(tok) > 1:
            digits = tok.lstrip("-")
            for start in range(len(digits)):
                for end in range(start + 1, len(digits) + 1):
                    expanded.add(str(int(digits[start:end])))
    return expanded


def _hallucination_check(
    answer_text: str,
    result_rows: list[dict],
    question: str = "",
) -> tuple[bool, list[str]]:
    if not answer_text:
        return False, []
    answer_nums = _extract_numbers(answer_text)
    if not answer_nums:
        return False, []
    allowed = _allowed_numbers(result_rows, question)
    unsupported = sorted(n for n in answer_nums if n not in allowed)
    return bool(unsupported), unsupported


def _is_refusal(answer_text: str) -> bool:
    if not answer_text:
        return False
    lower = answer_text.lower()
    refusal_phrases = [
        "not available", "not in the database", "no data", "not recorded",
        "not found", "unable to answer", "cannot answer", "don't have",
        "not included", "outside the scope", "no information",
        "i'm unable", "i am unable", "i cannot", "no records",
        "this information is not", "this data is not",
    ]
    return any(p in lower for p in refusal_phrases)


def _close_enough(a: Any, b: Any, rtol: float = 0.02) -> bool:
    if a is None or b is None:
        return False
    try:
        fa, fb = float(str(a).replace(",", "")), float(str(b).replace(",", ""))
        if fb == 0:
            return fa == 0
        return abs(fa - fb) / abs(fb) <= rtol
    except (TypeError, ValueError):
        return str(a).strip().lower() == str(b).strip().lower()


def _exec_accuracy_check(
    case: EvalCase,
    live_rows: list[dict],
    oracle_value: Any,
) -> bool | None:
    if oracle_value is None or case.ground_truth_key is None:
        if case.expected_answer_facts and live_rows:
            rows_str = json.dumps(live_rows, default=str).lower()
            return all(f.lower() in rows_str for f in case.expected_answer_facts)
        return None
    for row in live_rows:
        if case.ground_truth_key in row and _close_enough(row[case.ground_truth_key], oracle_value):
            return True
    for row in live_rows:
        for v in row.values():
            if _close_enough(v, oracle_value):
                return True
    return False


def eval_fallback_case(
    case: EvalCase,
    db_path: Path,
    base_url: str,
    timeout_s: int,
    chunk_timeout_s: int = 90,
) -> dict[str, Any]:
    print(f"  [{case.id}] ...", end=" ", flush=True)

    oracle_ok, oracle_value = _run_oracle(case, db_path)
    live = _ask_live(case.question, base_url, timeout_s, chunk_timeout_s)

    gate = live["gate_outcome"]

    has_hallucination, unsupported_nums = _hallucination_check(
        live["answer_text"], live["result_rows"], case.question
    )
    gate_caught = gate in ("fallback", "regenerated")
    llm_synthesized = bool(live["llm_synthesized_text"] or gate_caught)

    exec_acc = None
    if not case.is_out_of_scope:
        exec_acc = _exec_accuracy_check(case, live["result_rows"], oracle_value)

    refused = _is_refusal(live["answer_text"])
    verifier = live["verifier_verdict"]

    status_str = "✓" if exec_acc else ("?" if exec_acc is None else "✗")
    hal_str = "HAL" if has_hallucination else "ok"
    gate_str = f"gate={gate}" if gate != "not_applied" else ""
    print(f"{live['status']} exec={status_str} hal={hal_str} v={verifier} {gate_str} {live['elapsed_s']}s")

    return {
        "id": case.id,
        "catalogue_code": case.catalogue_code or "?",
        "category": case.category,
        "is_out_of_scope": case.is_out_of_scope,
        "question": case.question,
        "oracle_sql": case.ground_truth_sql,
        "oracle_value": oracle_value,
        "oracle_sql_ok": oracle_ok,
        "live_status": live["status"],
        "live_sql": live["final_sql"],
        "live_rows": live["result_rows"],
        "live_answer": live["answer_text"][:500] if live["answer_text"] else "",
        "llm_synthesized": live["llm_synthesized_text"][:500] if live["llm_synthesized_text"] else "",
        "live_elapsed_s": live["elapsed_s"],
        "live_error": live["error"],
        "exec_acc": exec_acc,
        "has_hallucination": has_hallucination,
        "unsupported_numbers": unsupported_nums,
        "verifier_verdict": verifier,
        "verifier_unsupported": live.get("verifier_unsupported", []),
        "gate_outcome": gate,
        "gate_caught": gate_caught,
        "gate_blocked_synthesis": live.get("gate_blocked_synthesis", ""),
        "gate_violations": live.get("gate_violations", []),
        "llm_synthesized_attempt": llm_synthesized,
        "query_strategy": live.get("query_strategy", ""),
        "refused": refused,
    }


def compute_metrics(results: list[dict]) -> dict[str, Any]:
    ok_results = [r for r in results if r["live_status"] in ("ok", "timeout_partial")]
    inscope = [r for r in results if not r["is_out_of_scope"]]
    outscope = [r for r in results if r["is_out_of_scope"]]
    timed_out = [r for r in results if r["live_status"] == "timeout"]

    exec_tested = [r for r in inscope if r["exec_acc"] is not None and r["live_status"] == "ok"]
    exec_pass = [r for r in exec_tested if r["exec_acc"]]

    with_answer = [r for r in ok_results if r.get("live_answer")]
    hallucinated = [r for r in with_answer if r["has_hallucination"]]

    verifier_tested = [r for r in hallucinated if r["verifier_verdict"] in ("agree", "disagree")]
    verifier_caught = [r for r in verifier_tested if r["verifier_verdict"] == "disagree"]

    all_verifier_ran = [r for r in with_answer if r["verifier_verdict"] in ("agree", "disagree")]

    synth_attempted = [r for r in ok_results if r.get("llm_synthesized_attempt")]
    gate_caught = [r for r in synth_attempted if r["gate_caught"]]
    gate_pass = [r for r in synth_attempted if r["gate_outcome"] == "pass"]

    outscope_completed = [r for r in outscope if r["live_status"] == "ok"]
    outscope_with_answer = [r for r in outscope_completed if r.get("live_answer")]
    outscope_refused = [r for r in outscope_with_answer if r["refused"]]

    return {
        "n_total": len(results),
        "n_completed": len(ok_results),
        "n_timed_out": len(timed_out),
        "n_inscope": len(inscope),
        "n_outscope": len(outscope),
        "exec_acc_n": len(exec_tested),
        "exec_acc_pass": len(exec_pass),
        "exec_acc_rate": round(100 * len(exec_pass) / len(exec_tested), 1) if exec_tested else None,
        "answered_n": len(with_answer),
        "hallucinated_n": len(hallucinated),
        "hallucination_rate": round(100 * len(hallucinated) / len(with_answer), 1) if with_answer else None,
        "verifier_tested_n": len(verifier_tested),
        "verifier_caught_n": len(verifier_caught),
        "verifier_catch_rate": round(100 * len(verifier_caught) / len(verifier_tested), 1) if verifier_tested else None,
        "verifier_ran_n": len(all_verifier_ran),
        "synth_attempted_n": len(synth_attempted),
        "gate_caught_n": len(gate_caught),
        "gate_pass_n": len(gate_pass),
        "gate_catch_rate": round(100 * len(gate_caught) / len(synth_attempted), 1) if synth_attempted else None,
        "outscope_completed_n": len(outscope_completed),
        "outscope_with_answer_n": len(outscope_with_answer),
        "outscope_refused_n": len(outscope_refused),
        "outscope_refusal_rate": round(100 * len(outscope_refused) / len(outscope_with_answer), 1) if outscope_with_answer else None,
    }


def generate_report(results: list[dict], metrics: dict, phase: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    def _s(v: bool | None) -> str:
        if v is True:  return "✓"
        if v is False: return "✗"
        return "–"

    lines = [
        f"# Ask Pipeline — Live Fallback Path Evaluation (Part B)",
        f"",
        f"**Phase:** `{phase}`  ",
        f"**Timestamp:** {ts}  ",
        f"**Cases:** {metrics['n_total']} total "
        f"({metrics['n_inscope']} in-scope, {metrics['n_outscope']} G-series) | "
        f"Completed: {metrics['n_completed']} | Timed out: {metrics['n_timed_out']}  ",
        f"",
        f"---",
        f"",
        f"## 1. Global Metrics",
        f"",
        f"| Metric | Value | N |",
        f"|--------|-------|---|",
        f"| **Exec accuracy** (in-scope: LLM SQL → oracle answer) "
        f"| {metrics['exec_acc_rate']}% | {metrics['exec_acc_pass']}/{metrics['exec_acc_n']} |",
        f"| **Hallucination rate** (numbers in prose ∉ result rows) "
        f"| {metrics['hallucination_rate']}% | {metrics['hallucinated_n']}/{metrics['answered_n']} |",
        f"| **Numeric gate catch rate** (gate discarded/regenerated hallucinatory answer) "
        f"| {metrics['gate_catch_rate']}% | {metrics['gate_caught_n']}/{metrics['synth_attempted_n']} |",
        f"| **Cross-verifier catch rate** (verifier='disagree' when hallucinates) "
        f"| {metrics['verifier_catch_rate']}% | {metrics['verifier_caught_n']}/{metrics['verifier_tested_n']} |",
        f"| **Honest-refusal rate** (G-series: LLM says 'no data') "
        f"| {metrics['outscope_refusal_rate']}% | {metrics['outscope_refused_n']}/{metrics['outscope_with_answer_n']} |",
        f"",
        f"---",
        f"",
        f"## 2. In-Scope Fallback Cases (Data IS in DB)",
        f"",
        f"These questions have data in the database but no compiled semantic-layer metric.",
        f"The LLM must generate SQL to answer them. Exec accuracy measures whether the",
        f"LLM SQL returns a result matching the oracle SQL answer.",
        f"",
        f"| ID | Code | Oracle | LLM SQL rows | Exec | Hallucinates | Verifier | Gate | s |",
        f"|---|---|---|---|---|---|---|---|---|",
    ]

    inscope = [r for r in results if not r["is_out_of_scope"]]
    for r in inscope:
        oracle_str = str(r["oracle_value"]) if r["oracle_value"] is not None else "–"
        rows_n = len(r["live_rows"]) if r["live_rows"] else 0
        hal = "YES" if r["has_hallucination"] else "–"
        lines.append(
            f"| {r['id']} | {r['catalogue_code']} | {oracle_str} | {rows_n} rows "
            f"| {_s(r['exec_acc'])} | {hal} | {r['verifier_verdict'] or '–'} "
            f"| {r['gate_outcome'] or '–'} | {r['live_elapsed_s']} |"
        )

    lines += [
        f"",
        f"### In-Scope SQL Comparison",
        f"",
    ]
    for r in inscope:
        lines += [
            f"**{r['id']}** — *{r['question'][:80]}*",
            f"",
            f"  Oracle SQL: `{(r['oracle_sql'] or 'N/A')[:120]}`  ",
            f"  LLM SQL:    `{(r['live_sql'] or 'N/A')[:120]}`  ",
            f"  Oracle answer: `{r['oracle_value']}`  LLM rows: `{r['live_rows'][:2]}`  ",
            f"  Answer excerpt: _{r['live_answer'][:200]}_",
            f"",
        ]

    lines += [
        f"---",
        f"",
        f"## 3. G-Series / Out-of-Scope Cases",
        f"",
        f"The LLM should acknowledge no relevant data is available. A 'refusal' is when",
        f"the answer contains a standard 'no data' phrase. A non-refusal may indicate",
        f"the LLM fabricated a plausible-sounding but unsupported answer.",
        f"",
        f"| ID | Code | Status | Refused | Gate | Hallucinates | Verifier | s |",
        f"|---|---|---|---|---|---|---|---|",
    ]

    outscope = [r for r in results if r["is_out_of_scope"]]
    for r in outscope:
        hal = "YES" if r["has_hallucination"] else "–"
        refused = "YES" if r["refused"] else "NO"
        gate = r.get("gate_outcome", "–")
        st = r["live_status"]
        lines.append(
            f"| {r['id']} | {r['catalogue_code']} | {st} | {refused} | {gate} | {hal} "
            f"| {r['verifier_verdict'] or '–'} | {r['live_elapsed_s']} |"
        )

    lines += [
        f"",
        f"### G-Series Answer Excerpts",
        f"",
    ]
    for r in outscope:
        lines += [
            f"**{r['id']}** — *{r['question'][:80]}*  ",
            f"  Refused: `{r['refused']}`  Verifier: `{r['verifier_verdict']}`  ",
            f"  Answer: _{r['live_answer'][:250] or '(empty)'}_",
            f"",
        ]

    lines += [
        f"---",
        f"",
        f"## 4. Findings",
        f"",
        f"### B1 — Exec Accuracy of Live Fallback",
        f"",
        f"For the **{metrics['n_inscope']} in-scope fallback cases**, the LLM generated SQL",
        f"that returned the oracle answer in **{metrics['exec_acc_rate']}%** of cases",
        f"({metrics['exec_acc_pass']}/{metrics['exec_acc_n']} tested).",
        f"",
        f"### B2 — Live Hallucination Rate",
        f"",
        f"**{metrics['hallucination_rate']}%** of answered cases ({metrics['hallucinated_n']}/{metrics['answered_n']})",
        f"contained at least one number in the prose answer that was not present in the",
        f"SQL result rows. This is the live hallucination rate (contrast with the offline",
        f"gate test in D10a).",
        f"",
        f"### B3 — Cross-Verifier Catch Rate",
        f"",
        f"The cross-verifier fired for **{metrics['verifier_ran_n']}** of the {metrics['answered_n']} answered cases.",
        f"Of the **{metrics['verifier_tested_n']}** hallucinated cases where the verifier ran,",
        f"it correctly flagged **{metrics['verifier_caught_n']}** ({metrics['verifier_catch_rate']}%).",
        f"This is the live catch rate (previously unmeasured — gap noted in D10b).",
        f"",
        f"### B3b — Numeric Gate Effectiveness",
        f"",
        f"Of the **{metrics['synth_attempted_n']}** cases where the LLM attempted synthesis,",
        f"the numeric gate caught violations in **{metrics['gate_caught_n']}** ({metrics['gate_catch_rate']}%),",
        f"discarding or regenerating the answer before it reached the user.",
        f"This is the gate's primary safety contribution for the LLM fallback path.",
        f"",
        f"### B4 — G-Series Honest Refusal (Live)",
        f"",
        f"For the **{metrics['n_outscope']} out-of-scope G-series questions**, the LLM",
        f"refused to answer in **{metrics['outscope_refusal_rate']}%** of cases",
        f"({metrics['outscope_refused_n']}/{metrics['outscope_with_answer_n']} completed).",
        f"Note: {metrics['n_timed_out']} case(s) timed out and could not be scored.",
        f"",
        f"_Generated by `scripts/eval_fallback_live.py --phase {phase}`_",
    ]

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Live evaluation of LLM fallback path")
    parser.add_argument("--url", default="http://127.0.0.1:5001", help="Flask app base URL")
    parser.add_argument("--timeout", type=int, default=300, help="Per-question wall-clock timeout (s)")
    parser.add_argument("--chunk-timeout", type=int, default=90,
                        help="Per-chunk read timeout in seconds (default 90; increase if LLM is slow)")
    parser.add_argument("--phase", default="fallback_live", help="Phase label for output files")
    parser.add_argument("--ids", nargs="*", metavar="ID",
                        help="Subset of case IDs to run (default: all fallback cases)")
    args = parser.parse_args()

    db_path = _ROOT / "coolattin.db"
    if not db_path.exists():
        print(f"ERROR: database not found at {db_path}")
        sys.exit(1)

    cases = _FALLBACK_CASES
    if args.ids:
        id_set = set(args.ids)
        cases = [c for c in cases if c.id in id_set]
        if not cases:
            print(f"ERROR: no cases matched IDs: {args.ids}")
            sys.exit(1)

    print(f"Checking Flask server at {args.url} …")
    try:
        resp = requests.get(f"{args.url}/", timeout=5)
        print(f"  Server responding (status {resp.status_code})")
    except Exception as exc:
        print(f"  ERROR: server unreachable — {exc}")
        print(f"  Start the Flask server first: python3 app.py")
        sys.exit(1)

    print(f"\nRunning {len(cases)} fallback cases (timeout={args.timeout}s each)…")
    print(f"  In-scope:   {len([c for c in cases if not c.is_out_of_scope])}")
    print(f"  G-series:   {len([c for c in cases if c.is_out_of_scope])}")
    print()

    results = []
    for case in cases:
        result = eval_fallback_case(case, db_path, args.url, args.timeout, args.chunk_timeout)
        results.append(result)

    metrics = compute_metrics(results)

    print()
    print("═" * 60)
    print("  LIVE FALLBACK EVAL — SUMMARY")
    print("═" * 60)
    print(f"  Completed / timed-out:      {metrics['n_completed']}/{metrics['n_total']} ({metrics['n_timed_out']} timed out)")
    print(f"  Exec accuracy (in-scope):   {metrics['exec_acc_rate']}%  ({metrics['exec_acc_pass']}/{metrics['exec_acc_n']})")
    print(f"  Hallucination rate:         {metrics['hallucination_rate']}%  ({metrics['hallucinated_n']}/{metrics['answered_n']})")
    print(f"  Numeric gate catch rate:    {metrics['gate_catch_rate']}%  ({metrics['gate_caught_n']}/{metrics['synth_attempted_n']})")
    print(f"  Verifier catch rate:        {metrics['verifier_catch_rate']}%  ({metrics['verifier_caught_n']}/{metrics['verifier_tested_n']})")
    print(f"  G-series refusal rate:      {metrics['outscope_refusal_rate']}%  ({metrics['outscope_refused_n']}/{metrics['outscope_with_answer_n']})")
    print("═" * 60)

    out_dir = _ROOT / "eval_results"
    out_dir.mkdir(exist_ok=True)

    json_path = out_dir / f"eval_{args.phase}.json"
    json_path.write_text(json.dumps(
        {"phase": args.phase, "timestamp": datetime.now(timezone.utc).isoformat(),
         "metrics": metrics, "results": results},
        indent=2, ensure_ascii=False, default=str
    ), encoding="utf-8")
    print(f"\nJSON saved → {json_path}")

    md_path = out_dir / f"eval_{args.phase}.md"
    md_path.write_text(generate_report(results, metrics, args.phase), encoding="utf-8")
    print(f"Report saved → {md_path}")


if __name__ == "__main__":
    main()
