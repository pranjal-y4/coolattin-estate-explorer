#!/usr/bin/env python3
"""
scripts/er_eval.py

Score every labelled pair in eval/er_gold.csv with the live entity-resolution
scorer (backend.services.entity_resolution.scoring.score_candidate) and report
a confusion matrix + precision/recall/F1 against the hand-labelled gold_label.

Usage: python3 scripts/er_eval.py
"""
from __future__ import annotations

import csv
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.services.entity_resolution.normalise import (
    normalise_person_fields,
    normalise_place_name,
)
from backend.services.entity_resolution.scoring import score_candidate

GOLD_PATH = os.path.join(os.path.dirname(__file__), "..", "eval", "er_gold.csv")

# Gold labels considered "should be linked" vs "should not be linked".
# UNCERTAIN is excluded from the strict positive/negative confusion matrix and
# reported separately — forcing an ambiguous human label into TP/FP would be
# dishonest, not a methodology choice.
_POSITIVE_GOLD = {"TRUE_MATCH", "POSSIBLE"}
_NEGATIVE_GOLD = {"FALSE_MATCH"}
_EXCLUDED_GOLD = {"UNCERTAIN"}

# System labels that result in an actual promoted link in workhouse_unified_links
# (CONFIRMED_MATCH + POSSIBLE_MATCH — see workhouse_entity_resolution.py).
_POSITIVE_SYSTEM = {"CONFIRMED_MATCH", "POSSIBLE_MATCH"}


def _int_or_none(v: str) -> int | None:
    v = (v or "").strip()
    return int(v) if v else None


def build_mention(row: dict) -> dict:
    fields = normalise_person_fields(row["wh_raw_name"], surname_first=True)
    return {
        **fields,
        "normalised_place": normalise_place_name(row["wh_place_ed"]),
        "inferred_birth_year": _int_or_none(row["wh_birth_year"]),
        "event_year": _int_or_none(row["wh_year"]),
        "age": _int_or_none(row["wh_age"]),
        "gender": None,  # not present in eval/er_gold.csv
    }


def build_candidate(row: dict) -> dict:
    fields = normalise_person_fields(row["u_canonical_name"], surname_first=False)
    return {
        **fields,
        "normalised_place": normalise_place_name(row["u_townland"]),
        "inferred_birth_year": _int_or_none(row["u_birth_year"]),
        "event_year": _int_or_none(row["u_year"]),
        "age": _int_or_none(row["u_age"]),
        "gender": None,  # not present in eval/er_gold.csv
    }


def main() -> int:
    rows = []
    with open(GOLD_PATH, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    tp = fp = fn = tn = 0
    excluded = 0
    print(f"{'id':4} {'gold_label':11} {'sys_label':16} {'score':>6}  wh_raw_name -> u_canonical_name")
    print("-" * 100)
    for row in rows:
        mention = build_mention(row)
        candidate = build_candidate(row)
        result = score_candidate(mention, candidate)
        gold = row["gold_label"].strip()
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

        print(
            f"{row['gold_id']:4} {gold:11} {result.label:16} {result.score:6.3f}  "
            f"{row['wh_raw_name']} -> {row['u_canonical_name']}  [{verdict}]"
        )

    print("-" * 100)
    print(f"n={len(rows)}  excluded(UNCERTAIN)={excluded}  scored={len(rows) - excluded}")
    print(f"TP={tp} FP={fp} FN={fn} TN={tn}")
    precision = tp / (tp + fp) if (tp + fp) else float("nan")
    recall = tp / (tp + fn) if (tp + fn) else float("nan")
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else float("nan")
    print(f"precision={precision:.3f} recall={recall:.3f} f1={f1:.3f}")
    print()
    print("NOTE: 'gender' is not present in eval/er_gold.csv, so it is passed as None")
    print("to score_candidate() for both sides — the scorer treats missing gender as")
    print("neutral (+5, no conflict). Several FALSE_MATCH rationales in the gold set")
    print("cite a gender mismatch inferred from forename; without a gender column the")
    print("scorer can only catch these via the forename-mismatch signal, not gender.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
