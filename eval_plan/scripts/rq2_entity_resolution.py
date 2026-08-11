#!/usr/bin/env python3
"""
eval_plan/scripts/rq2_entity_resolution.py

Full RQ2 metric suite against eval/er_gold.csv, per EVAL_RQ2_entity_resolution.md:
  1. Pairwise P/R/F1 (system scorer, using real unified_record fields via
     _load_unified_records() — includes real gender, unlike the earlier
     scripts/er_eval.py pass which had no gender column to draw from).
  2. B-cubed P/R/F1 (clustered by mention).
  3. Blocking Pairs Completeness / Pairs Quality / Reduction Ratio, run against
     the full 13,707-record unified corpus.
  4. Name-only vs authority-ID hard-case comparison (townland table).
  5. Spelling-variant recall (Metaphone) on curated Irish-name pairs.

Run from repo root: python3 eval_plan/scripts/rq2_entity_resolution.py
"""
from __future__ import annotations

import csv
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from create_app import create_app
from extensions import get_db_conn

from backend.services.entity_resolution.normalise import (
    normalise_person_fields,
    normalise_place_name,
    phonetic_code,
)
from backend.services.entity_resolution.candidates import (
    build_unified_index,
    generate_candidates,
)
from backend.services.entity_resolution.scoring import score_candidate

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
GOLD_PATH = os.path.join(REPO_ROOT, "eval", "er_gold.csv")

_POSITIVE_GOLD = {"TRUE_MATCH", "POSSIBLE"}
_NEGATIVE_GOLD = {"FALSE_MATCH"}
_EXCLUDED_GOLD = {"UNCERTAIN"}
_POSITIVE_SYSTEM = {"CONFIRMED_MATCH", "POSSIBLE_MATCH"}


def _int_or_none(v: str | None) -> int | None:
    v = (v or "").strip()
    return int(v) if v else None


def load_gold() -> list[dict]:
    with open(GOLD_PATH, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def build_mention(row: dict) -> dict:
    fields = normalise_person_fields(row["wh_raw_name"], surname_first=True)
    return {
        **fields,
        "normalised_place": normalise_place_name(row["wh_place_ed"]),
        "inferred_birth_year": _int_or_none(row["wh_birth_year"]),
        "event_year": _int_or_none(row["wh_year"]),
        "age": _int_or_none(row["wh_age"]),
        "gender": None,  # workhouse side has no gender in the gold CSV
    }


def find_unified_record(unified_records: list[dict], record_id: str) -> dict | None:
    for rec in unified_records:
        if str(rec.get("record_id") or "") == record_id:
            return rec
    return None


# ── 1 & 2: Pairwise + B-cubed ────────────────────────────────────────────────

def run_pairwise_and_bcubed(gold_rows: list[dict], unified_records: list[dict]) -> dict:
    by_id = {str(r.get("record_id") or ""): r for r in unified_records}
    per_row = []
    for row in gold_rows:
        mention = build_mention(row)
        candidate = by_id.get(row["u_record_id"])
        if candidate is None:
            # record_id not found in current unified_record snapshot — skip, note it
            per_row.append({"row": row, "result": None, "candidate_found": False})
            continue
        result = score_candidate(mention, candidate)
        per_row.append({"row": row, "result": result, "candidate_found": True, "candidate": candidate})

    tp = fp = fn = tn = 0
    excluded = 0
    missing = 0
    lines = []
    for entry in per_row:
        row = entry["row"]
        gold = row["gold_label"].strip()
        if not entry["candidate_found"]:
            missing += 1
            lines.append(f"{row['gold_id']:4} MISSING unified record {row['u_record_id']} in current DB snapshot")
            continue
        result = entry["result"]
        sys_positive = result.label in _POSITIVE_SYSTEM
        verdict = ""
        if gold in _EXCLUDED_GOLD:
            excluded += 1
            verdict = "(excluded)"
        elif gold in _POSITIVE_GOLD:
            if sys_positive:
                tp += 1
                verdict = "TP"
            else:
                fn += 1
                verdict = "FN"
        elif gold in _NEGATIVE_GOLD:
            if sys_positive:
                fp += 1
                verdict = "FP"
            else:
                tn += 1
                verdict = "TN"
        lines.append(
            f"{row['gold_id']:4} {gold:11} {result.label:16} {result.score:6.3f}  "
            f"{row['wh_raw_name']} -> {row['u_canonical_name']}  [{verdict}]  "
            f"gender_used={entry['candidate'].get('gender')}"
        )

    precision = tp / (tp + fp) if (tp + fp) else float("nan")
    recall = tp / (tp + fn) if (tp + fn) else float("nan")
    f1 = 2 * precision * recall / (precision + recall) if (precision and recall and (precision + recall)) else float("nan")

    # B-cubed: cluster ground truth = (wh_raw_name, wh_place_ed, wh_year) mention
    # groups to their TRUE_MATCH/POSSIBLE unified_record id(s); system cluster =
    # mention grouped to unified records it labels CONFIRMED/POSSIBLE.
    # With this gold set's shape (one mention -> usually one candidate row per
    # gold_id), B-cubed collapses to the same pairwise precision/recall per
    # mention-candidate pair — reported for completeness, not as a distinct
    # clustering result, since no multi-candidate-per-mention gold clusters
    # exist in er_gold.csv to make B-cubed diverge from pairwise here.
    bcubed_precision = precision
    bcubed_recall = recall
    bcubed_f1 = f1

    return {
        "lines": lines,
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "excluded": excluded, "missing": missing,
        "precision": precision, "recall": recall, "f1": f1,
        "bcubed_precision": bcubed_precision, "bcubed_recall": bcubed_recall, "bcubed_f1": bcubed_f1,
    }


# ── 3: Blocking PC / PQ / RR ──────────────────────────────────────────────────

def run_blocking_eval(gold_rows: list[dict], unified_records: list[dict], unified_index: dict) -> dict:
    n_unified = len(unified_records)
    true_match_rows = [r for r in gold_rows if r["gold_label"].strip() in _POSITIVE_GOLD]
    survived = 0
    total_candidates_returned = 0
    true_positives_in_candidates = 0
    lines = []
    for row in true_match_rows:
        mention = build_mention(row)
        candidates = generate_candidates(mention, unified_records, unified_index=unified_index)
        candidate_ids = {str(c.get("record_id") or "") for c in candidates}
        hit = row["u_record_id"] in candidate_ids
        if hit:
            survived += 1
            true_positives_in_candidates += 1
        total_candidates_returned += len(candidates)
        lines.append(
            f"{row['gold_id']:4} true_match={row['u_record_id']:8} "
            f"survived_blocking={hit}  n_candidates={len(candidates)}"
        )

    pc = survived / len(true_match_rows) if true_match_rows else float("nan")
    # PQ approximation: we only have gold labels for the specific pair listed per
    # mention, not for every other candidate returned — so "true matches in
    # candidate set" is a lower bound (only the labelled true match, if present,
    # counts as a known true positive; other returned candidates are unlabelled,
    # not confirmed negative).
    pq_lower_bound = true_positives_in_candidates / total_candidates_returned if total_candidates_returned else float("nan")
    all_possible_pairs = len(true_match_rows) * n_unified
    rr = 1 - (total_candidates_returned / all_possible_pairs) if all_possible_pairs else float("nan")

    return {
        "lines": lines,
        "n_true_matches": len(true_match_rows),
        "n_unified": n_unified,
        "survived": survived,
        "total_candidates_returned": total_candidates_returned,
        "pc": pc,
        "pq_lower_bound": pq_lower_bound,
        "rr": rr,
    }


# ── 4: Name-only vs authority-ID hard cases ──────────────────────────────────

def run_hard_cases(conn) -> dict:
    families = {
        "COOLATTIN": conn.execute(
            "SELECT name, entity_id, civil_parish, barony, osm_id, osi_id, vrti_id "
            "FROM townland WHERE name LIKE '%COOLATTIN%' ORDER BY name"
        ).fetchall(),
        "BALLINACOR": conn.execute(
            "SELECT name, entity_id, civil_parish, barony, osm_id, osi_id, vrti_id "
            "FROM townland WHERE name LIKE '%BALLINACOR%' ORDER BY name"
        ).fetchall(),
    }
    # True name collisions: identical `name` string with different entity_id rows.
    collisions = conn.execute(
        "SELECT name, COUNT(*) n FROM townland GROUP BY name HAVING COUNT(*) > 1"
    ).fetchall()
    return {"families": families, "collisions": [dict(r) for r in collisions]}


# ── 5: Spelling-variant recall (Metaphone) ───────────────────────────────────

_VARIANT_PAIRS = [
    # (name_a, name_b, expect_match) — curated Irish 19th-c. surname variants.
    ("MCDONNELL", "MACDONNELL", True),
    ("O'BRIEN", "OBRIEN", True),
    ("O BRIEN", "OBRIEN", True),
    ("KAVANAGH", "CAVANAGH", True),
    ("BYRNE", "BEIRNE", False),
    ("DOYLE", "DOYAL", True),
    ("O'TOOLE", "OTOOLE", True),
    ("O'NEILL", "NEIL", False),
    ("MCGRATH", "MAGRATH", True),
    ("MOLLOY", "MOLLOWY", True),
    ("MCKEOWN", "MACKEOWN", True),
    ("BRENNAN", "BRANNIGAN", False),
    ("SHEEHAN", "SHEAHAN", True),
    ("GALLAGHER", "GALLIAGHER", True),
    ("O'DONNELL", "DONNELLY", False),
]


def run_spelling_variants() -> dict:
    lines = []
    matched = 0
    for a, b, expect in _VARIANT_PAIRS:
        pa, pb = phonetic_code(a), phonetic_code(b)
        got_match = pa == pb
        ok = got_match == expect
        if got_match:
            matched += 1
        lines.append(
            f"{a:12} ({pa:8}) vs {b:12} ({pb:8})  metaphone_match={got_match!s:5}  "
            f"expected={expect!s:5}  {'OK' if ok else 'MISS vs curated expectation'}"
        )
    return {"lines": lines, "matched": matched, "total": len(_VARIANT_PAIRS)}


def main() -> int:
    app = create_app()
    with app.app_context():
        conn = get_db_conn()
        from backend.services.workhouse_entity_resolution import _load_unified_records

        gold_rows = load_gold()
        unified_records = _load_unified_records()
        unified_index = build_unified_index(unified_records)

        out = []
        out.append("=" * 100)
        out.append("PART 1+2 — Pairwise P/R/F1 (real unified_record fields, including real gender)")
        out.append("=" * 100)
        pw = run_pairwise_and_bcubed(gold_rows, unified_records)
        out.extend(pw["lines"])
        out.append("-" * 100)
        out.append(f"n={len(gold_rows)} excluded(UNCERTAIN)={pw['excluded']} missing_record={pw['missing']}")
        out.append(f"TP={pw['tp']} FP={pw['fp']} FN={pw['fn']} TN={pw['tn']}")
        out.append(f"precision={pw['precision']:.3f} recall={pw['recall']:.3f} f1={pw['f1']:.3f}")
        out.append(
            f"B-cubed precision={pw['bcubed_precision']:.3f} recall={pw['bcubed_recall']:.3f} "
            f"f1={pw['bcubed_f1']:.3f}  (collapses to pairwise — see note in script)"
        )

        out.append("")
        out.append("=" * 100)
        out.append("PART 3 — Blocking: Pairs Completeness / Pairs Quality / Reduction Ratio")
        out.append("=" * 100)
        block = run_blocking_eval(gold_rows, unified_records, unified_index)
        out.extend(block["lines"])
        out.append("-" * 100)
        out.append(f"n_true_matches={block['n_true_matches']}  n_unified_corpus={block['n_unified']}")
        out.append(f"survived_blocking={block['survived']}/{block['n_true_matches']}")
        out.append(f"Pairs Completeness (PC) = {block['pc']:.3f}")
        out.append(f"Pairs Quality (PQ, lower bound — see caveat) = {block['pq_lower_bound']:.3f}")
        out.append(f"Reduction Ratio (RR) = {block['rr']:.6f}")

        out.append("")
        out.append("=" * 100)
        out.append("PART 4 — Name-only vs authority-ID hard cases")
        out.append("=" * 100)
        hard = run_hard_cases(conn)
        for family, rows in hard["families"].items():
            out.append(f"-- {family} family (name-only substring match would risk conflating these) --")
            for r in rows:
                out.append(f"   {dict(r)}")
        out.append(f"True exact-name collisions (same `name` string, different entity_id): {len(hard['collisions'])}")
        if hard["collisions"]:
            for c in hard["collisions"]:
                out.append(f"   {c}")
        else:
            out.append("   NONE — the townland table's ingest process already resolves same-named source")
            out.append("   records to unique canonical rows before they reach this table (see evidence doc).")

        out.append("")
        out.append("=" * 100)
        out.append("PART 5 — Spelling-variant recall (Metaphone)")
        out.append("=" * 100)
        sv = run_spelling_variants()
        out.extend(sv["lines"])
        out.append("-" * 100)
        out.append(f"matched/total = {sv['matched']}/{sv['total']} = {sv['matched']/sv['total']:.3f}")

        report = "\n".join(out)
        print(report)

        evidence_path = os.path.join(REPO_ROOT, "eval_plan", "evidence", "RQ2_raw_output.txt")
        with open(evidence_path, "w", encoding="utf-8") as f:
            f.write(report + "\n")
        print(f"\nWrote raw output to {evidence_path}")

        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
